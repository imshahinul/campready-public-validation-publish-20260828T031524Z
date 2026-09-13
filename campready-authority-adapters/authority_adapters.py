"""Read-only, configuration-driven NC State Parks and NPS source adapters."""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PARSER_VERSION = "shared-authority-adapter-v1"
USER_AGENT = "CampReady-Phase4A2/1.0 (read-only official-source validation)"
SIGNALS = {"OPEN", "CLOSED", "SCHEDULED", "CURRENT_OR_UNBOUNDED", "EXPIRED", "UNKNOWN"}
EVENT_CLASSES = {"CAMPGROUND_STATUS", "ACCESS_ALERT", "FIRE_RESTRICTION", "OTHER_OPERATIONAL_ALERT", "UNKNOWN"}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_text(value).casefold()).strip()


@dataclass(frozen=True)
class Retrieval:
    requested_url: str
    ok: bool
    http_status: int | None
    final_url: str | None
    body: bytes
    checked_at_utc: str
    failure_kind: str | None = None
    failure_detail: str | None = None


@dataclass(frozen=True)
class Observation:
    authority_family: str
    source_url: str
    source_role: str
    campground_key: str | None
    park_key: str | None
    source_event_id: str
    fingerprint: str
    title: str
    normalized_text: str
    observed_at_utc: str
    published_at: str | None
    effective_start: str | None
    effective_end: str | None
    lifecycle: str
    operational_signal: str
    event_class: str
    relevant: bool
    notifying: bool
    raw_provenance: dict[str, Any]
    parser_version: str
    parse_confidence: str
    unknown_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParseResult:
    status: str
    identity_resolved: bool
    observations: tuple[Observation, ...]
    failure_detail: str | None = None


class CommonPageParser(HTMLParser):
    """Extract common semantic blocks without authority- or site-specific selectors."""

    BLOCKS = {"article", "section", "aside", "li", "div"}
    HEADINGS = {"h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._ignored = 0
        self._blocks: list[dict[str, Any]] = []
        self._stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer", "svg"}:
            self._ignored += 1
        if tag == "title":
            self._in_title = True
        if self._ignored:
            return
        attr_map = dict(attrs)
        if tag in self.BLOCKS or tag in self.HEADINGS:
            node = {"tag": tag, "attrs": attr_map, "parts": []}
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "nav", "footer", "svg"}:
            self._ignored = max(0, self._ignored - 1)
            return
        if self._ignored:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                node = self._stack.pop(index)
                text = normalize_text(" ".join(node["parts"]))
                if text:
                    marker = " ".join(str(value or "") for value in node["attrs"].values()).casefold()
                    self._blocks.append({"tag": tag, "marker": marker, "text": text})
                break

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        if self._in_title:
            self.title += data
        for node in self._stack:
            node["parts"].append(data)

    @property
    def blocks(self) -> list[dict[str, str]]:
        unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for block in sorted(self._blocks, key=lambda item: len(item["text"])):
            key = normalized_key(block["text"])
            if key and key not in seen:
                seen.add(key)
                unique.append(block)
        return unique


def fetch(url: str, timeout: int = 20) -> Retrieval:
    checked = datetime.now(timezone.utc).isoformat()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(3_000_000)
            return Retrieval(url, 200 <= response.status < 300, response.status, response.geturl(), body, checked)
    except urllib.error.HTTPError as exc:
        return Retrieval(url, False, exc.code, exc.geturl(), b"", checked, "HTTP_ACCESS_FAILURE", str(exc))
    except Exception as exc:  # transport failures are data quality, never operational state
        return Retrieval(url, False, None, None, b"", checked, "HTTP_ACCESS_FAILURE", f"{type(exc).__name__}: {exc}")


def _contains(text: str, values: list[str]) -> bool:
    haystack = f" {normalized_key(text)} "
    return any(f" {normalized_key(value)} " in haystack for value in values if normalized_key(value))


def _dates(text: str) -> tuple[str | None, str | None, str | None]:
    values = re.findall(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b", text, re.I)
    parsed: list[str] = []
    for value in values[:3]:
        try:
            parsed.append(datetime.strptime(value.title(), "%B %d, %Y").date().isoformat())
        except ValueError:
            pass
    published = parsed[0] if re.search(r"published|posted|updated", text, re.I) and parsed else None
    effective = parsed[-2:] if len(parsed) > 1 else parsed
    return published, effective[0] if effective else None, effective[1] if len(effective) > 1 else None


def _evidence_snippets(text: str) -> list[str]:
    """Bound evidence to local clauses so unrelated page text cannot imply status."""
    clean = normalize_text(text)
    if len(clean) <= 800:
        return [] if normalized_key(clean) in {"loading", "alerts", "alert"} else [clean]
    sentences = [normalize_text(value) for value in re.split(r"(?<=[.!?])\s+|\s+[|•]\s+", clean)]
    operational = re.compile(r"\b(open|closed|closure|reopen(?:ed|ing)?|restriction|alert|notice|no access|not accessible|prescribed burn)\b", re.I)
    snippets = []
    for sentence in sentences:
        if operational.search(sentence) and 15 <= len(sentence) <= 800:
            snippets.append(sentence)
    return snippets


def _semantics(text: str, observed_at: str) -> tuple[str, str, str, str | None, str | None, str | None, str | None]:
    value = normalized_key(text)
    published, start, end = _dates(text)
    today = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
    start_date = datetime.fromisoformat(start).date() if start else None
    end_date = datetime.fromisoformat(end).date() if end else None
    close = bool(re.search(r"\b(closed|closure|not accessible|no access|suspended)\b", value))
    reopen = bool(re.search(r"\b(reopened|reopening|closure lifted|restrictions lifted)\b", value))
    generic_open = bool(re.search(r"\b(open|operating)\b", value))
    if start_date and start_date > today:
        signal, lifecycle = "SCHEDULED", "SCHEDULED"
    elif end_date and end_date < today:
        signal, lifecycle = "EXPIRED", "EXPIRED"
    elif close:
        signal, lifecycle = "CLOSED", "CURRENT_OR_UNBOUNDED"
    elif reopen or generic_open:
        signal, lifecycle = "OPEN", "CURRENT_OR_UNBOUNDED"
    else:
        signal, lifecycle = "UNKNOWN", "UNKNOWN"
    if re.search(r"\b(campground|camping|campsite)\b", value):
        event_class = "CAMPGROUND_STATUS"
    elif re.search(r"\b(road|bridge|entrance|entrances|access|route)\b", value):
        event_class = "ACCESS_ALERT"
    elif re.search(r"\b(fire ban|fire restriction|prescribed burn|wildfire)\b", value):
        event_class = "FIRE_RESTRICTION"
    elif signal != "UNKNOWN":
        event_class = "OTHER_OPERATIONAL_ALERT"
    else:
        event_class = "UNKNOWN"
    reason = None if signal != "UNKNOWN" else "No safely determinable authorized operational signal"
    return signal, lifecycle, event_class, published, start, end, reason


class SharedAuthorityAdapter:
    authority_family = ""
    alert_markers = ("alert", "closure", "condition", "notice", "advisory", "status", "news")

    def parse(self, site: dict[str, Any], retrieval: Retrieval, source_role: str) -> ParseResult:
        if not retrieval.ok:
            return ParseResult("DATA_QUALITY_FAILURE", False, (), retrieval.failure_detail or retrieval.failure_kind)
        try:
            document = retrieval.body.decode("utf-8", errors="strict")
            parser = CommonPageParser()
            parser.feed(document)
        except Exception as exc:
            return ParseResult("DATA_QUALITY_FAILURE", False, (), f"PARSER_FAILURE: {type(exc).__name__}: {exc}")
        identity = self.identity_resolved(site, parser, retrieval.final_url or retrieval.requested_url)
        if not identity:
            return ParseResult("PARSED_IDENTITY_UNRESOLVED", False, ())
        candidates = [block for block in parser.blocks if any(marker in block["marker"] for marker in self.alert_markers)]
        if source_role == "CAMPGROUND_PAGE":
            candidates.extend(block for block in parser.blocks if _contains(block["text"], site["aliases"]) and re.search(r"\b(open|closed|closure|reopen|restriction|alert|notice)\b", block["text"], re.I))
        snippets = []
        for block in candidates:
            snippets.extend(_evidence_snippets(block["text"]))
        observations = tuple(self._observation(site, retrieval, source_role, snippet) for snippet in snippets)
        observations = self._deduplicate_and_precede(observations)
        return ParseResult("PARSED" if observations else "NO_CURRENT_EVENT", True, observations)

    def identity_resolved(self, site: dict[str, Any], parser: CommonPageParser, final_url: str) -> bool:
        evidence = f"{parser.title} {final_url} " + " ".join(block["text"] for block in parser.blocks[-20:])
        return _contains(evidence, site["aliases"] + site["park_aliases"])

    def relevant(self, site: dict[str, Any], text: str, event_class: str) -> tuple[bool, str | None]:
        if _contains(text, site["aliases"]):
            return True, None
        return False, "Source does not deterministically identify this campground"

    def _observation(self, site: dict[str, Any], retrieval: Retrieval, source_role: str, text: str) -> Observation:
        clean = normalize_text(text)
        signal, lifecycle, event_class, published, effective_start, effective_end, unknown = _semantics(clean, retrieval.checked_at_utc)
        relevant, relevance_reason = self.relevant(site, clean, event_class)
        if not relevant:
            signal, lifecycle = "UNKNOWN", "UNKNOWN"
            unknown = relevance_reason
        stable_payload = "|".join((self.authority_family, site["campground_key"], source_role, normalized_key(clean)))
        event_id = hashlib.sha256(stable_payload.encode()).hexdigest()[:24]
        fingerprint = hashlib.sha256(json.dumps({"text": normalized_key(clean), "signal": signal, "class": event_class, "relevant": relevant}, sort_keys=True).encode()).hexdigest()
        title = clean[:160] or "Untitled official-source observation"
        return Observation(self.authority_family, retrieval.final_url or retrieval.requested_url, source_role, site["campground_key"] if relevant else None, site["park_key"], event_id, fingerprint, title, clean, retrieval.checked_at_utc, published, effective_start, effective_end, lifecycle, signal, event_class, relevant, False, {"requested_url": retrieval.requested_url, "http_status": retrieval.http_status, "final_url": retrieval.final_url, "excerpt": clean[:1000]}, PARSER_VERSION, "HIGH" if signal != "UNKNOWN" and relevant else "LOW", unknown)

    @staticmethod
    def _deduplicate_and_precede(items: tuple[Observation, ...]) -> tuple[Observation, ...]:
        unique = {item.source_event_id: item for item in items}
        values = list(unique.values())
        if any(item.relevant and item.operational_signal == "CLOSED" for item in values):
            values = [item for item in values if not (item.operational_signal == "OPEN" and item.event_class != "CAMPGROUND_STATUS")]
        return tuple(sorted(values, key=lambda item: item.source_event_id))


class NCStateParksAdapter(SharedAuthorityAdapter):
    authority_family = "NC_STATE_PARKS"

    def relevant(self, site: dict[str, Any], text: str, event_class: str) -> tuple[bool, str | None]:
        if _contains(text, site["aliases"]):
            return True, None
        if _contains(text, site["park_aliases"]) and event_class in {"ACCESS_ALERT", "FIRE_RESTRICTION"} and re.search(r"\b(entire park|park closed|all facilities|all access)\b", text, re.I):
            return True, None
        return False, "NC notice lacks deterministic campground or authorized whole-park scope"


class NPSAdapter(SharedAuthorityAdapter):
    authority_family = "NPS"

    def relevant(self, site: dict[str, Any], text: str, event_class: str) -> tuple[bool, str | None]:
        if _contains(text, site["aliases"]):
            return True, None
        whole_park = _contains(text, site["park_aliases"]) and bool(re.search(r"\b(park-wide|entire park|all park areas|all entrances)\b", text, re.I))
        if whole_park and event_class in {"ACCESS_ALERT", "FIRE_RESTRICTION"}:
            return True, None
        return False, "NPS alert does not name the campground or an authorized deterministic whole-park scope"


def load_sites(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sites = data.get("sites")
    if data.get("schema_version") != "campready-authority-sites-v1" or not isinstance(sites, list):
        raise ValueError("invalid authority site configuration")
    return sites


def adapter_for(authority_family: str) -> SharedAuthorityAdapter:
    adapters = {"NC_STATE_PARKS": NCStateParksAdapter, "NPS": NPSAdapter}
    try:
        return adapters[authority_family]()
    except KeyError as exc:
        raise ValueError(f"unsupported authority family: {authority_family}") from exc
