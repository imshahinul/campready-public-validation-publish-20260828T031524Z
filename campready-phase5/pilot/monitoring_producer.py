"""Bounded prospective monitoring producer for the frozen Phase 5 pilot cohort.

This module reuses the Phase 4 source transport and parser/classifier primitives,
but deliberately does not call the Phase 4 runner/capture orchestration.  It has
no pilot intake, review, observation, notification, delivery, or scheduling
responsibility.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

PHASE5 = Path(__file__).resolve().parents[1]
REPO = PHASE5.parent
PHASE4B = REPO / "campready-phase4b"
DEFAULT_OUTPUT_ROOT = Path("/Users/smshahinulislam/Desktop/campReady/audit/phase5_5_pilot/monitoring")
SCHEMA = "campready.phase5.normalized_run_evidence.v1"
COHORT_IDS = (
    "andrews-cove", "low-gap", "nc-van-hook-glade", "wv-big-rock", "or-spinreel",
    "or-cold-springs", "ncsp-carolina-beach", "ncsp-stone-mountain", "nps-elkmont",
    "nps-big-meadows",
)
ALLOWED_ROLES = {"ACTIVE_ALERT", "CAMPGROUND_PAGE", "ALERT_DETAIL"}
ALLOWED_FAMILIES = {"NWS_API", "USFS_HTML", "NC_STATE_PARKS", "NPS_API"}
AUTHORITY_NAMES = {
    "NWS": "National Weather Service", "USFS": "USDA Forest Service",
    "NC_STATE_PARKS": "North Carolina State Parks", "NPS": "National Park Service",
}
TRANSPORT = {
    "enabled": True, "usfs_max_physical_attempts_per_logical_request": 2,
    "retry_delay_seconds": 5,
}


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC_AWARE_CLOCK_REQUIRED")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return parsed


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _bindings() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    phase5 = json.loads((PHASE5 / "config.json").read_text(encoding="utf-8"))
    protocol = json.loads((Path(__file__).with_name("protocol.json")).read_text(encoding="utf-8"))
    configured = tuple(row["site_id"] for row in phase5["cohort"])
    frozen = tuple(row["site_id"] for row in protocol["cohort"]["sites"])
    if configured != COHORT_IDS or frozen != COHORT_IDS:
        raise ValueError("FROZEN_COHORT_DRIFT")
    mapping_doc = json.loads((REPO / phase5["canonical_mapping"]).read_text(encoding="utf-8"))
    mapping = {row["canonical_key"]: row for row in mapping_doc["rows"] if row["canonical_key"] in COHORT_IDS}
    adapters_doc = json.loads((REPO / "campready-authority-adapters/authority_sites.json").read_text(encoding="utf-8"))
    adapters = {row["campground_key"]: row for row in adapters_doc["sites"] if row["campground_key"] in COHORT_IDS}
    if set(mapping) != set(COHORT_IDS) or set(adapters) != set(COHORT_IDS[6:]):
        raise ValueError("FROZEN_SOURCE_BINDING_INCOMPLETE")
    return mapping, adapters, phase5


def _result_status(result: Mapping[str, Any]) -> str:
    first = result["first_attempt"]["succeeded"]
    if result["ok"] and not first:
        return "FAILED_FIRST_ATTEMPT_RESCUED"
    return "SUCCESS" if result["ok"] else "FINAL_FAILED"


def _retry_summary(result: Mapping[str, Any]) -> dict[str, Any] | None:
    attempts = result["physical_attempts"]
    if len(attempts) == 1 and attempts[0]["succeeded"]:
        return None
    def status(attempt: Mapping[str, Any]) -> str:
        return f"HTTP_{attempt['http_status']}" if attempt.get("http_status") is not None else str(attempt.get("failure_type") or "UNKNOWN")
    return {
        "attempted": len(attempts), "max_attempts": 2 if len(attempts) > 1 else 1,
        "rescued": bool(result["ok"] and not attempts[0]["succeeded"]),
        "final_failed": not bool(result["ok"]), "first_attempt_status": status(attempts[0]),
        "eventual_status": status(attempts[-1]),
    }


def _record_base(site: Mapping[str, Any], *, authority: str, family: str, role: str,
                 url: str, run_id: str, detected_at: str, retrieved_at: str | None,
                 result: Mapping[str, Any], evidence: str | None = None,
                 title: str | None = None) -> dict[str, Any]:
    value = {
        "site_id": site["canonical_key"], "campground_name": site["canonical_name"],
        "authority_id": authority, "authority_name": AUTHORITY_NAMES[authority],
        "source_family": family, "source_role": role, "official_source_url": url,
        "source_evidence_digest": "sha256:" + hashlib.sha256(result.get("body", b"")).hexdigest() if result.get("body") else None,
        "provenance_run_id": run_id, "detected_at": detected_at, "retrieved_at": retrieved_at,
        "retrieval_status": _result_status(result), "retry_telemetry_summary": _retry_summary(result),
    }
    if title:
        value["source_title"] = title[:500]
    else:
        value["source_title_unavailable_reason"] = "RETRIEVAL_FINAL_FAILED" if not result["ok"] else "NOT_PERSISTED"
    if evidence:
        value["source_evidence_verbatim"] = evidence[:4000]
    else:
        value["source_evidence_unavailable_reason"] = "RETRIEVAL_FINAL_FAILED" if not result["ok"] else "NOT_PERSISTED"
    return value


def _quality(base: Mapping[str, Any], logical_id: str, reason: str, parser_status: str,
             relevance: str = "NOT_EVALUATED") -> dict[str, Any]:
    return {**base, "item_kind": "quality", "parser_status": parser_status,
            "relevance_status": relevance, "logical_request_id": logical_id,
            "data_quality_reason_code": reason,
            "unknown_reason_code": reason if relevance == "UNKNOWN" else None}


def _semantic(base: Mapping[str, Any], event_id: str, category: str, fingerprint: str,
              evidence: str, title: str) -> dict[str, Any]:
    value = {**base, "item_kind": "semantic", "parser_status": "SUCCESS",
             "relevance_status": "RELEVANT", "upstream_event_id": event_id,
             "event_category": category, "_fingerprint": fingerprint,
             "source_evidence_verbatim": evidence[:4000], "source_title": title[:500]}
    value.pop("source_evidence_unavailable_reason", None)
    value.pop("source_title_unavailable_reason", None)
    return value


def _collect(fetcher: Callable[..., dict[str, Any]], now: datetime,
             sleep: Callable[[float], None]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    mapping, adapter_sites, _ = _bindings()
    capture = _load(PHASE4B / "capture.py", "phase5_monitoring_phase4_capture")
    phase4_config = json.loads((PHASE4B / "config.json").read_text(encoding="utf-8"))
    legacy = capture.load_module(REPO / phase4_config["paths"]["legacy_capture"], "phase5_monitoring_legacy")
    parser = legacy.load_prior_script(REPO / phase4_config["paths"]["usfs_parser"])
    classifier = legacy.load_prior_script(REPO / phase4_config["paths"]["usfs_classifier"])
    adapters = capture.load_module(REPO / phase4_config["paths"]["authority_adapters"], "phase5_monitoring_adapters")
    cache = capture.FetchOnce(fetcher, TRANSPORT, sleep)
    detected = _utc(now)
    quality: list[dict[str, Any]] = []
    events: dict[str, dict[str, Any]] = {}
    requests: list[dict[str, Any]] = []

    def fetched(site: Mapping[str, Any], authority: str, family: str, role: str, url: str,
                accept: str = "text/html,*/*;q=0.8") -> tuple[dict[str, Any], dict[str, Any]]:
        result = cache.get(url, accept, authority=authority, family=family, role=role)
        requests.append({"site_id": site["canonical_key"], "authority_id": authority,
                         "source_family": family, "source_role": role, "official_source_url": url,
                         "retrieval_status": _result_status(result), "elapsed_ms": result.get("elapsed_ms")})
        base = _record_base(site, authority=authority, family=family, role=role, url=url,
                            run_id="", detected_at=detected, retrieved_at=detected if result["ok"] else None,
                            result=result)
        return result, base

    # Frozen point-specific NWS active-alert binding for exactly the ten sites.
    for site_id in COHORT_IDS:
        site = mapping[site_id]
        url = site["nws"]["active_alerts_endpoint"].format(latitude=site["latitude"], longitude=site["longitude"])
        result, base = fetched(site, "NWS", "NWS_API", "ACTIVE_ALERT", url, "application/geo+json")
        features = None
        if result["ok"]:
            try:
                candidate = json.loads(result["body"].decode("utf-8"))["features"]
                features = candidate if isinstance(candidate, list) else None
            except Exception:
                features = None
        logical = "active-alert:" + site_id
        if not result["ok"]:
            quality.append(_quality(base, logical, "FINAL_RETRIEVAL_FAILURE", "NOT_RUN"))
        elif features is None:
            quality.append(_quality(base, logical, "PARSER_FAILURE", "FAILED"))
        else:
            if _result_status(result) == "FAILED_FIRST_ATTEMPT_RESCUED":
                quality.append(_quality(base, logical, "FIRST_ATTEMPT_RESCUED", "SUCCESS", "UNKNOWN"))
            for feature in features:
                event_id, semantic = legacy.nws_semantic(feature)
                if not event_id:
                    continue
                props = feature.get("properties", {})
                event_url = feature.get("id") if str(feature.get("id", "")).startswith("https://") else url
                event_base = dict(base, official_source_url=event_url)
                key = f"{site_id}|NWS_API|{event_id}"
                events[key] = _semantic(event_base, f"NWS_API:{event_id}", "WEATHER_ALERT",
                                        capture.canonical_hash(semantic), json.dumps(semantic, sort_keys=True),
                                        str(props.get("headline") or props.get("event") or "NWS active alert"))

    usfs = [mapping[key] for key in COHORT_IDS if mapping[key]["authority_family"] == "USFS"]
    for site in usfs:
        url = site["campground_status_page_url"]
        result, base = fetched(site, "USFS", "USFS_HTML", "CAMPGROUND_PAGE", url)
        parsed = legacy.parse_recreation_article(result["body"]) if result["ok"] else None
        logical = "campground-page:" + site["canonical_key"]
        if not result["ok"]:
            quality.append(_quality(base, logical, "FINAL_RETRIEVAL_FAILURE", "NOT_RUN"))
        elif not parsed or not parsed.get("article_found") or not parsed.get("article_text"):
            quality.append(_quality(base, logical, "PARSER_FAILURE", "FAILED"))
        else:
            if _result_status(result) == "FAILED_FIRST_ATTEMPT_RESCUED":
                quality.append(_quality(base, logical, "FIRST_ATTEMPT_RESCUED", "SUCCESS", "UNKNOWN"))
            text = parsed["article_text"]
            key = f"{site['canonical_key']}|USFS_HTML|CAMPGROUND_PAGE"
            events[key] = _semantic(base, "USFS_CAMPGROUND_PAGE:" + site["canonical_key"],
                                    "CAMPGROUND_CONDITION", capture.canonical_hash({"text": text, "signals": parsed["signals"]}),
                                    text, site["canonical_name"])

    # Alert indexes are discovery transport only. Only authorized ALERT_DETAIL records are emitted.
    for index_url in sorted({site["alerts_conditions_page_url"] for site in usfs}):
        scoped = capture.scoped_usfs_by_alert_url(usfs, index_url)
        index = cache.get(index_url, authority="USFS", family="USFS_HTML", role="ALERT_INDEX")
        for site_id in scoped:
            requests.append({"site_id": site_id, "authority_id": "USFS", "source_family": "USFS_HTML",
                             "source_role": "ALERT_INDEX_INTERNAL", "official_source_url": index_url,
                             "retrieval_status": _result_status(index), "elapsed_ms": index.get("elapsed_ms")})
        # The frozen parser is parameterized through module globals that its
        # original study script initialized to one forest. Rebind those globals
        # to this canonical index before invoking the unchanged parser logic.
        parser["ALERT_INDEX"] = index_url
        parser["ALERT_PREFIX"] = urllib.parse.urlsplit(index_url).path.rstrip("/") + "/"
        links = parser["extract_alert_links"](index["body"])[:100] if index["ok"] else []
        for url in sorted(set(links)):
            result = cache.get(url, authority="USFS", family="USFS_HTML", role="ALERT_DETAIL")
            parsed = parser["parse_content"](result["body"]) if result["ok"] else None
            for site_id, site in scoped.items():
                requests.append({"site_id": site_id, "authority_id": "USFS", "source_family": "USFS_HTML",
                                 "source_role": "ALERT_DETAIL", "official_source_url": url,
                                 "retrieval_status": _result_status(result), "elapsed_ms": result.get("elapsed_ms")})
                base = _record_base(site, authority="USFS", family="USFS_HTML", role="ALERT_DETAIL", url=url,
                                    run_id="", detected_at=detected, retrieved_at=detected if result["ok"] else None,
                                    result=result, evidence=parsed.get("text") if parsed else None,
                                    title=parsed.get("title") if parsed else None)
                logical = "alert-detail:" + hashlib.sha256(url.encode()).hexdigest()[:24] + ":" + site_id
                if not result["ok"]:
                    quality.append(_quality(base, logical, "FINAL_RETRIEVAL_FAILURE", "NOT_RUN"))
                    continue
                if not parsed or not parsed.get("content_block_found"):
                    quality.append(_quality(base, logical, "PARSER_FAILURE", "FAILED"))
                    continue
                captured = {"source_url": url, "title": parsed["title"],
                            "start_date": parser["extract_date_field"](parsed["text"], "Alert Start Date"),
                            "end_date": parser["extract_date_field"](parsed["text"], "Alert End Date"),
                            "rec_sites_affected": parser["extract_line_field"](parsed["text"], "Rec Sites Affected"),
                            "text_excerpt": parsed["text"]}
                relation = classifier["classify_event"](captured, scoped, now.date()).get(site_id, {})
                if _result_status(result) == "FAILED_FIRST_ATTEMPT_RESCUED":
                    quality.append(_quality(base, logical, "FIRST_ATTEMPT_RESCUED", "SUCCESS", "UNKNOWN"))
                if relation.get("relevance") == "INCLUDE":
                    semantic = {"canonical_url": url, "title": parsed["title"], "bounded_official_text": parsed["text"]}
                    key = f"{site_id}|USFS_HTML|ALERT_DETAIL|{url}"
                    events[key] = _semantic(base, "USFS_ALERT_DETAIL:" + url, "CAMPGROUND_CONDITION",
                                            capture.canonical_hash(semantic), parsed["text"], parsed["title"])

    for site_id in COHORT_IDS[6:]:
        site, binding = mapping[site_id], adapter_sites[site_id]
        authority = site["authority_family"]
        family = "NC_STATE_PARKS" if authority == "NC_STATE_PARKS" else "NPS_API"
        url = binding["campground_url"]
        result, base = fetched(site, authority, family, "CAMPGROUND_PAGE", url)
        retrieval = adapters.Retrieval(url, result["ok"], result["http_status"], result["final_url"], result["body"],
                                       detected, "HTTP_ACCESS_FAILURE" if not result["ok"] else None, result["failure"])
        parsed = adapters.adapter_for(authority).parse(binding, retrieval, "CAMPGROUND_PAGE")
        logical = "campground-page:" + site_id
        if not result["ok"]:
            quality.append(_quality(base, logical, "FINAL_RETRIEVAL_FAILURE", "NOT_RUN"))
        elif parsed.status == "DATA_QUALITY_FAILURE":
            quality.append(_quality(base, logical, "PARSER_FAILURE", "FAILED"))
        else:
            if _result_status(result) == "FAILED_FIRST_ATTEMPT_RESCUED":
                quality.append(_quality(base, logical, "FIRST_ATTEMPT_RESCUED", "SUCCESS", "UNKNOWN"))
            for observation in parsed.observations:
                if not observation.relevant:
                    continue
                key = f"{site_id}|{family}|{observation.source_event_id}"
                events[key] = _semantic(base, f"{family}:{observation.source_event_id}", "CAMPGROUND_CONDITION",
                                        observation.fingerprint, observation.normalized_text, observation.title)
    return quality, events, requests


def _previous_state(root: Path) -> dict[str, Any]:
    completed = sorted(path for path in root.glob("*.json") if not path.name.startswith(".")) if root.exists() else []
    if not completed:
        return {}
    document = json.loads(completed[-1].read_text(encoding="utf-8"))
    validate_artifact(completed[-1], require_production=False)
    state = document.get("accepted_state")
    if not isinstance(state, dict):
        raise ValueError("PRIOR_ACCEPTED_STATE_INVALID")
    return state


def _durable_create(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("DUPLICATE_RUN_ID_PATH")
    temporary = path.parent / ("." + path.name + ".tmp")
    if temporary.exists():
        raise FileExistsError("PARTIAL_RUN_EXISTS")
    data = _canonical(document) + b"\n"
    try:
        with temporary.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        # Hard-link publication is atomic and, unlike replace(), can never
        # overwrite a completed artifact if another process won the run-id race.
        os.link(temporary, path)
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def produce(output_root: str | Path, *, fetcher: Callable[..., dict[str, Any]],
            clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            sleep: Callable[[float], None] = __import__("time").sleep,
            dry_run: bool = False, test_fixture: bool = False) -> dict[str, Any]:
    """Retrieve the frozen sources and durably create exactly one normalized artifact."""
    root = Path(output_root).resolve()
    started = clock()
    run_id = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{run_id}.json"
    if path.exists():
        raise FileExistsError("DUPLICATE_RUN_ID_PATH")
    prior = _previous_state(root)
    quality, current, requests = _collect(fetcher, clock(), sleep)
    # Reproduce Phase 4 Repair C without its runner: retain attributable prior
    # state only when its exact source (or ALERT_DETAIL discovery index) failed.
    # Successful source silence creates no review record and no disappearance or
    # resolution claim, but it also does not masquerade as current accepted state.
    for key, old in prior.items():
        if key in current:
            continue
        exact = [row for row in requests if row["site_id"] == old.get("site_id")
                 and row["source_family"] == old.get("source_family")
                 and row["source_role"] == old.get("source_role")
                 and (old.get("source_role") != "ALERT_DETAIL" or
                      row["official_source_url"] == old.get("official_source_url"))]
        retain = bool(exact) and all(row["retrieval_status"] == "FINAL_FAILED" for row in exact)
        if not exact and old.get("source_role") == "ALERT_DETAIL":
            indexes = [row for row in requests if row["site_id"] == old.get("site_id")
                       and row["source_role"] == "ALERT_INDEX_INTERNAL"]
            retain = bool(indexes) and all(row["retrieval_status"] == "FINAL_FAILED" for row in indexes)
        if retain:
            current[key] = old
    prior_ref = "sha256:" + _sha(prior) if prior else None
    current_ref = "sha256:" + _sha(current)
    records: list[dict[str, Any]] = []
    for record in quality:
        record["provenance_run_id"] = run_id
        if record["source_role"] == "ALERT_DETAIL" and record["retrieval_status"] == "FINAL_FAILED":
            record["prior_accepted_state_ref"] = prior_ref
        records.append(record)
    for key, record in sorted(current.items()):
        # The first successful prospective capture establishes state; it does not
        # claim that already-present source content appeared at capture time.
        if not prior:
            continue
        old = prior.get(key)
        if old is not None and old.get("_fingerprint") == record["_fingerprint"]:
            continue
        value = dict(record)
        value["provenance_run_id"] = run_id
        value["lifecycle_classification"] = "APPEARANCE" if old is None else "MATERIAL_UPDATE"
        value["prior_accepted_state_ref"] = prior_ref
        value["current_accepted_state_ref"] = current_ref
        records.append(value)
    generated = _utc(clock())
    document: dict[str, Any] = {
        "schema_version": SCHEMA, "run_id": run_id, "generated_at": generated,
        "source_evidence_ref": str(path), "dry_run": dry_run, "test_fixture": test_fixture,
        "frozen_cohort": list(COHORT_IDS), "accepted_state": current, "records": records,
        "retrieval_summary": {
            "records": len(records),
            "successes": sum(row["retrieval_status"] != "FINAL_FAILED" for row in requests if row["source_role"] in ALLOWED_ROLES),
            "failures": sum(row["retrieval_status"] == "FINAL_FAILED" for row in requests if row["source_role"] in ALLOWED_ROLES),
            "parser_failures": sum(row.get("data_quality_reason_code") == "PARSER_FAILURE" for row in quality),
            "source_role_counts": {role: sum(row["source_role"] == role for row in requests) for role in sorted(ALLOWED_ROLES)},
        },
    }
    for index, record in enumerate(records):
        record.pop("_fingerprint", None)
        record["provenance_record_ref"] = f"{path}#records/{index}"
    validate_document(document, require_production=not (dry_run or test_fixture))
    _durable_create(path, document)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"run_id": run_id, "generated_at": generated, "artifact_path": str(path),
            "artifact_sha256": digest, **document["retrieval_summary"]}


def validate_document(document: Mapping[str, Any], *, require_production: bool = True) -> dict[str, Any]:
    if document.get("schema_version") != SCHEMA:
        raise ValueError("SCHEMA_INVALID")
    try:
        run_time = datetime.strptime(str(document.get("run_id")), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("RUN_ID_INVALID") from exc
    generated = _parse_utc(document.get("generated_at"))
    if generated < run_time:
        raise ValueError("RUN_TIMING_INVALID")
    if require_production and (document.get("dry_run") is not False or document.get("test_fixture") is not False):
        raise ValueError("PRODUCTION_FLAGS_INVALID")
    if document.get("frozen_cohort") != list(COHORT_IDS):
        raise ValueError("FROZEN_COHORT_INVALID")
    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("RECORDS_INVALID")
    mapping, _, _ = _bindings()
    seen: set[tuple[Any, ...]] = set()
    sys.path[:0] = [str(PHASE5), str(Path(__file__).parent)]
    from read_model import derive_review_items_for_cohort, validate_review_item_contract
    for record in records:
        if not isinstance(record, dict) or record.get("site_id") not in COHORT_IDS:
            raise ValueError("RECORD_SITE_INVALID")
        role, family, authority = record.get("source_role"), record.get("source_family"), record.get("authority_id")
        if role not in ALLOWED_ROLES or family not in ALLOWED_FAMILIES:
            raise ValueError("RECORD_SOURCE_INVALID")
        site_authority = mapping[record["site_id"]]["authority_family"]
        allowed = ((role == "ACTIVE_ALERT" and authority == "NWS" and family == "NWS_API") or
                   (role == "CAMPGROUND_PAGE" and authority == site_authority and
                    family == {"USFS":"USFS_HTML", "NC_STATE_PARKS":"NC_STATE_PARKS", "NPS":"NPS_API"}[site_authority]) or
                   (role == "ALERT_DETAIL" and site_authority == authority == "USFS" and family == "USFS_HTML"))
        if not allowed or record.get("provenance_run_id") != document.get("run_id"):
            raise ValueError("RECORD_BINDING_INVALID")
        detected = _parse_utc(record.get("detected_at"))
        retrieved = _parse_utc(record["retrieved_at"]) if record.get("retrieved_at") is not None else None
        if detected < run_time or detected > generated or (retrieved is not None and (retrieved < run_time or retrieved > generated)):
            raise ValueError("RECORD_TIMING_INVALID")
        if not str(record.get("provenance_record_ref", "")).split("#", 1)[0].endswith(f"/{document['run_id']}.json"):
            raise ValueError("RECORD_PROVENANCE_INVALID")
        identity = ((record.get("item_kind"), record.get("upstream_event_id"), record.get("site_id"), record.get("lifecycle_classification"))
                    if record.get("item_kind") == "semantic" else
                    (record.get("item_kind"), record.get("logical_request_id"), role, record.get("official_source_url"), record.get("data_quality_reason_code")))
        if identity in seen:
            raise ValueError("DUPLICATE_RECORD_IDENTITY")
        seen.add(identity)
    items = derive_review_items_for_cohort(records)
    if len(items) != len(records):
        raise ValueError("DOWNSTREAM_DERIVATION_MISMATCH")
    for item in items:
        validate_review_item_contract(item)
    return {"result": "VALID", "record_count": len(records), "review_item_count": len(items)}


def validate_artifact(path: str | Path, *, require_production: bool = True) -> dict[str, Any]:
    artifact = Path(path)
    if artifact.name.startswith(".") or artifact.suffix != ".json":
        raise ValueError("COMPLETED_ARTIFACT_PATH_REQUIRED")
    try:
        document = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ARTIFACT_UNREADABLE") from exc
    if artifact.name != f"{document.get('run_id')}.json" or document.get("source_evidence_ref") != str(artifact.resolve()):
        raise ValueError("ARTIFACT_IDENTITY_INVALID")
    return validate_document(document, require_production=require_production)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen Phase 5 prospective monitoring producer")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    validate = sub.add_parser("validate")
    validate.add_argument("artifact", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        print(json.dumps(validate_artifact(args.artifact), indent=2, sort_keys=True)); return 0
    capture = _load(PHASE4B / "capture.py", "phase5_monitoring_cli_capture")
    print(json.dumps(produce(args.output_root, fetcher=capture.default_fetch), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
