"""Phase 4B heterogeneous read-only capture; no notification transport exists here."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "CampReady-Phase4B/1.0 (30-day prospective research validation)"
SUPPORTED = {"USFS", "NC_STATE_PARKS", "NPS"}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec:
        raise RuntimeError(f"cannot load {path}")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    if tree.body and isinstance(tree.body[-1], ast.Raise):
        exc = tree.body[-1].exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit":
            tree.body.pop()
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def default_fetch(url: str, accept: str = "text/html,*/*;q=0.8", timeout: int = 30) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(3_000_000)
            return {"ok": 200 <= response.status < 300, "http_status": response.status, "final_url": response.geturl(), "content_type": response.headers.get("Content-Type"), "body": body, "failure": None, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "http_status": exc.code, "final_url": exc.geturl(), "content_type": None, "body": b"", "failure": f"HTTPError: {exc}", "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception as exc:
        return {"ok": False, "http_status": None, "final_url": None, "content_type": None, "body": b"", "failure": f"{type(exc).__name__}: {exc}", "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}


class FetchOnce:
    def __init__(self, fetcher: Callable[..., dict[str, Any]] = default_fetch):
        self.fetcher, self.cache, self.calls = fetcher, {}, []

    def get(self, url: str, accept: str = "text/html,*/*;q=0.8") -> dict[str, Any]:
        key = (url, accept)
        if key not in self.cache:
            self.cache[key] = self.fetcher(url, accept=accept)
            self.calls.append(url)
        return self.cache[key]


def request_record(authority: str, family: str, url: str, role: str, result: dict[str, Any], parser_result: str, denominator: str) -> dict[str, Any]:
    return {
        "authority_family": authority, "source_family": family, "source_url": url, "source_role": role,
        "http_status": result["http_status"], "final_url": result["final_url"], "content_type": result["content_type"],
        "body_size": len(result["body"]), "body_sha256": hashlib.sha256(result["body"]).hexdigest() if result["body"] else None,
        "retrieval_succeeded": result["ok"], "failure": result["failure"], "parser_result": parser_result,
        "elapsed_ms": result["elapsed_ms"], "reliability_denominator": denominator,
    }


def capture(config_path: Path, fetcher: Callable[..., dict[str, Any]] = default_fetch, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    config = json.loads(config_path.read_text())
    root = config_path.parent.parent
    mapping = json.loads((root / config["paths"]["mapping"]).read_text())
    rows = mapping["rows"]
    supported = [row for row in rows if row["authority_family"] in SUPPORTED]
    transport = [row for row in rows if row["authority_family"] == "USACE"]
    if len(rows) != 35 or len(supported) != 32 or len(transport) != 3:
        raise RuntimeError("frozen cohort cardinality gate failed")
    cache = FetchOnce(fetcher)
    requests: list[dict[str, Any]] = []
    sites: dict[str, dict[str, Any]] = {row["canonical_key"]: {"canonical_key": row["canonical_key"], "authority_family": row["authority_family"], "source_observations": [], "semantic_events": [], "relevance_results": [], "unknown_results": [], "nws_status": None} for row in supported}

    # USFS recreation pages and shared authority alert pages. Frozen semantics are used only by the frozen components.
    legacy = load_module(root / config["paths"]["legacy_capture"], "phase4b_legacy_capture")
    parser_ns = legacy.load_prior_script(root / config["paths"]["usfs_parser"])
    classifier_ns = legacy.load_prior_script(root / config["paths"]["usfs_classifier"])
    usfs_rows = [row for row in supported if row["authority_family"] == "USFS"]
    usfs_by_key = {row["canonical_key"]: row for row in usfs_rows}
    for row in usfs_rows:
        url = row["campground_status_page_url"]
        result = cache.get(url)
        parsed = legacy.parse_recreation_article(result["body"]) if result["ok"] else {"article_found": False, "article_text": "", "signals": {}}
        parser_result = "PARSED" if parsed.get("article_found") and parsed.get("article_text") else ("NOT_ATTEMPTED_RETRIEVAL_FAILURE" if not result["ok"] else "PARSER_FAILURE")
        requests.append(request_record("USFS", "USFS_WEBSITE_RECREATION", url, "CAMPGROUND_PAGE", result, parser_result, "SUPPORTED_CORE"))
        event = {"source_family": "USFS_WEBSITE_RECREATION", "source_url": url, "quality": "OK" if parser_result == "PARSED" else "DATA_QUALITY_FAILURE", "semantic": {"bounded_article_text": parsed.get("article_text", ""), "signals": parsed.get("signals", {})}}
        event["fingerprint"] = canonical_hash(event["semantic"])
        sites[row["canonical_key"]]["source_observations"].append(event)
        if parser_result == "PARSED":
            sites[row["canonical_key"]]["semantic_events"].append(event)
    for alert_url in sorted({row["alerts_conditions_page_url"] for row in usfs_rows}):
        index = cache.get(alert_url)
        links = parser_ns["extract_alert_links"](index["body"])[:100] if index["ok"] else []
        parser_result = "PARSED" if index["ok"] else "NOT_ATTEMPTED_RETRIEVAL_FAILURE"
        requests.append(request_record("USFS", "USFS_WEBSITE_ALERTS", alert_url, "ALERT_INDEX", index, parser_result, "SUPPORTED_CORE"))
        for event_url in sorted(set(links)):
            result = cache.get(event_url)
            parsed = parser_ns["parse_content"](result["body"]) if result["ok"] else {"content_block_found": False, "title": "", "text": ""}
            parse_status = "PARSED" if parsed.get("content_block_found") else ("NOT_ATTEMPTED_RETRIEVAL_FAILURE" if not result["ok"] else "PARSER_FAILURE")
            requests.append(request_record("USFS", "USFS_WEBSITE_ALERTS", event_url, "ALERT_DETAIL", result, parse_status, "SUPPORTED_CORE"))
            if parse_status != "PARSED":
                continue
            text = parsed["text"]
            captured = {"source_url": event_url, "title": parsed["title"], "start_date": parser_ns["extract_date_field"](text, "Alert Start Date"), "end_date": parser_ns["extract_date_field"](text, "Alert End Date"), "rec_sites_affected": parser_ns["extract_line_field"](text, "Rec Sites Affected"), "text_excerpt": text}
            relations = classifier_ns["classify_event"](captured, usfs_by_key, now.date())
            semantic = {"canonical_url": event_url, "title": parsed["title"], "bounded_official_text": text}
            event = {"source_family": "USFS_WEBSITE_ALERTS", "source_url": event_url, "quality": "OK", "semantic": semantic, "fingerprint": canonical_hash(semantic)}
            for key, relation in relations.items():
                if key in sites:
                    sites[key]["source_observations"].append(event)
                    sites[key]["relevance_results"].append({"source_url": event_url, **relation})
                    if relation.get("relevance") == "INCLUDE": sites[key]["semantic_events"].append(event)
                    elif relation.get("relevance") == "UNKNOWN": sites[key]["unknown_results"].append({"source_url": event_url, **relation})

    # Accepted shared NC State Parks/NPS adapter; each shared URL is fetched once then deterministically fanned out.
    adapters = load_module(root / config["paths"]["authority_adapters"], "phase4b_authority_adapters")
    authority_sites = {site["campground_key"]: site for site in adapters.load_sites(root / config["paths"]["authority_sites"])}
    for authority in ("NC_STATE_PARKS", "NPS"):
        relevant_rows = [row for row in supported if row["authority_family"] == authority]
        for role, field in (("CAMPGROUND_PAGE", "campground_url"), ("CONDITIONS_PAGE", "conditions_url")):
            for url in sorted({authority_sites[row["canonical_key"]][field] for row in relevant_rows}):
                result = cache.get(url)
                for row in [r for r in relevant_rows if authority_sites[r["canonical_key"]][field] == url]:
                    retrieval = adapters.Retrieval(url, result["ok"], result["http_status"], result["final_url"], result["body"], now.isoformat(), "HTTP_ACCESS_FAILURE" if not result["ok"] else None, result["failure"])
                    parsed = adapters.adapter_for(authority).parse(authority_sites[row["canonical_key"]], retrieval, role)
                    for observation in parsed.observations:
                        value = observation.to_dict()
                        sites[row["canonical_key"]]["source_observations"].append(value)
                        (sites[row["canonical_key"]]["semantic_events"] if observation.relevant else sites[row["canonical_key"]]["unknown_results"]).append(value)
                # One source request record, regardless of fan-out count.
                representative = authority_sites[relevant_rows[0]["canonical_key"]] if relevant_rows else None
                if representative:
                    retrieval = adapters.Retrieval(url, result["ok"], result["http_status"], result["final_url"], result["body"], now.isoformat(), None, result["failure"])
                    probe = adapters.adapter_for(authority).parse(representative, retrieval, role)
                    requests.append(request_record(authority, authority, url, role, result, probe.status, "SUPPORTED_CORE"))

    # NWS point-specific active alerts for all and only the 32 supported-core sites.
    for row in supported:
        url = row["nws"]["active_alerts_endpoint"].format(latitude=row["latitude"], longitude=row["longitude"])
        result = cache.get(url, "application/geo+json")
        features = None
        if result["ok"]:
            try:
                candidate = json.loads(result["body"].decode())["features"]
                if isinstance(candidate, list): features = candidate
            except Exception:
                pass
        parser_result = "PARSED" if features is not None else ("NOT_ATTEMPTED_RETRIEVAL_FAILURE" if not result["ok"] else "PARSER_FAILURE")
        requests.append(request_record("NWS", "NWS_API", url, "POINT_ACTIVE_ALERTS", result, parser_result, "SUPPORTED_CORE"))
        sites[row["canonical_key"]]["nws_status"] = {"retrieval_succeeded": result["ok"], "parser_result": parser_result, "active_alert_count": len(features or [])}
        for feature in features or []:
            event_id, semantic = legacy.nws_semantic(feature)
            if event_id:
                event = {"source_family": "NWS_API", "source_url": url, "event_id": str(event_id), "quality": "OK", "semantic": semantic, "fingerprint": canonical_hash(semantic)}
                sites[row["canonical_key"]]["source_observations"].append(event)
                sites[row["canonical_key"]]["semantic_events"].append(event)

    # USACE is transport evidence only and can never enter semantic state or supported denominators.
    transport_results = []
    for row in transport:
        url = row["alerts_conditions_page_url"]
        result = cache.get(url)
        text = result["body"].decode("utf-8", errors="replace").casefold()
        identity = any(alias.casefold() in text for alias in row["aliases"])
        structure = "operational" in text and "status" in text
        item = request_record("USACE", "USACE_TRANSPORT_STATUS", url, "TRANSPORT_ONLY", result, "NOT_APPLICABLE_TRANSPORT_ONLY", "USACE_TRANSPORT")
        item.update({"canonical_key": row["canonical_key"], "candidate_identity_presence": identity, "expected_status_page_structure": structure, "semantic_event_count": 0})
        requests.append(item); transport_results.append(item)

    supported_requests = [item for item in requests if item["reliability_denominator"] == "SUPPORTED_CORE"]
    parseable = [item for item in supported_requests if item["retrieval_succeeded"]]
    parser_failures = [item for item in parseable if item["parser_result"] == "PARSER_FAILURE"]
    usace_requests = [item for item in requests if item["reliability_denominator"] == "USACE_TRANSPORT"]
    telemetry = {"raw_candidate_count": sum(len(site["source_observations"]) for site in sites.values()), "parsed_observation_count": sum(len(site["source_observations"]) for site in sites.values()), "deduplicated_observation_count": sum(len({canonical_hash(v) for v in site["source_observations"]}) for site in sites.values()), "relevant_operational_event_count": sum(len(site["semantic_events"]) for site in sites.values()), "unknown_nonrelevant_count": sum(len(site["unknown_results"]) for site in sites.values())}
    return {"schema_version": "campready-phase4b-capture-v1", "captured_at_utc": now.isoformat(), "cohort": {"experimental": len(rows), "supported_core": len(supported), "strict_useful": 31, "usace_transport_only": len(transport)}, "source_requests": requests, "supported_campgrounds": [sites[key] for key in sorted(sites)], "usace_transport": transport_results, "telemetry": telemetry, "metrics": {"supported_core_retrieval": {"successful": sum(r["retrieval_succeeded"] for r in supported_requests), "attempted": len(supported_requests)}, "parser_failure": {"failures": len(parser_failures), "successfully_retrieved_parseable": len(parseable)}, "usace_transport_reliability": {"successful": sum(r["retrieval_succeeded"] for r in usace_requests), "attempted": len(usace_requests)}}, "network_fetch_count": len(cache.calls), "unique_network_urls": len(set(cache.calls)), "real_notifications": 0}


if __name__ == "__main__":
    config_path = Path(os.environ.get("CAMPREADY_PHASE4B_CONFIG", Path(__file__).with_name("config.json")))
    print(json.dumps(capture(config_path), indent=2, ensure_ascii=False))
