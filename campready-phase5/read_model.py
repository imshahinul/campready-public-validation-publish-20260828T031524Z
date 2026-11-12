"""Deterministic, side-effect-free Phase 5 review-item projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "campready.phase5.review_item.v1"
GUARD_PROHIBITIONS = (
    "NO_APPEARANCE_INFERENCE", "NO_DISAPPEARANCE_INFERENCE",
    "NO_RESOLUTION_INFERENCE", "NO_SEMANTIC_CHANGE_INFERENCE",
    "NO_SAFETY_INFERENCE", "NO_ROAD_PASSABILITY_INFERENCE",
    "NO_SOURCE_SILENCE_AS_SAFE",
)
ALLOWED_LIFECYCLES = {"APPEARANCE", "DISAPPEARANCE", "RESOLUTION", "MATERIAL_UPDATE", "UNCHANGED", "NONE"}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _digest_id(prefix: str, values: list[Any]) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(values)).hexdigest()


def semantic_review_item_id(upstream_event_id: str, affected_relationship_id: str,
                            prior_accepted_state_ref: str | None,
                            current_accepted_state_ref: str | None,
                            lifecycle_classification: str) -> str:
    return _digest_id("cr5ri1-", [SCHEMA_VERSION, "semantic", upstream_event_id,
        affected_relationship_id, prior_accepted_state_ref or "",
        current_accepted_state_ref or "", lifecycle_classification])


def quality_review_item_id(provenance_run_id: str, logical_request_id: str,
                           source_role: str, official_source_url: str,
                           data_quality_reason_code: str) -> str:
    return _digest_id("cr5ri1-", [SCHEMA_VERSION, "quality", provenance_run_id,
        logical_request_id, source_role, official_source_url,
        data_quality_reason_code])


def semantic_relationship_id(site_id: str, upstream_event_id: str) -> str:
    return _digest_id("cr5rel1-", [site_id, upstream_event_id])


def quality_relationship_id(site_id: str, logical_request_id: str,
                            source_role: str, official_source_url: str) -> str:
    return _digest_id("cr5relq1-", [site_id, logical_request_id, source_role,
                                     official_source_url])


def load_phase5_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else Path(__file__).with_name("config.json")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    keys = [site["site_id"] for site in data["cohort"]]
    if len(keys) != 10 or len(set(keys)) != 10 or any("usace" in key.lower() for key in keys):
        raise ValueError("Phase 5 cohort must contain exactly 10 unique, non-USACE canonical keys")
    return data


def _title(record: Mapping[str, Any]) -> dict[str, Any]:
    value = record.get("source_title")
    return ({"status":"AVAILABLE", "value":value, "reason_code":None} if value else
            {"status":"UNKNOWN", "value":None,
             "reason_code":record.get("source_title_unavailable_reason", "NOT_PERSISTED")})


def _evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    if record.get("source_evidence_verbatim") is not None:
        return {"representation":"VERBATIM", "content":record["source_evidence_verbatim"], "unavailable_reason_code":None}
    if record.get("source_evidence_summary") is not None:
        return {"representation":"SUMMARY", "content":record["source_evidence_summary"], "unavailable_reason_code":None}
    return {"representation":"UNAVAILABLE", "content":None,
            "unavailable_reason_code":record.get("source_evidence_unavailable_reason", "NOT_PERSISTED")}


def _base(record: Mapping[str, Any]) -> dict[str, Any]:
    url = record.get("official_source_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("official persisted HTTPS source URL is required")
    if not record.get("provenance_record_ref") or not record.get("provenance_run_id"):
        raise ValueError("resolvable persisted provenance is required")
    return {
        "schema_version": SCHEMA_VERSION,
        "detected_at": record["detected_at"], "retrieved_at": record.get("retrieved_at"),
        "site_id": record["site_id"], "campground_name": record["campground_name"],
        "authority_id": record["authority_id"], "authority_name": record["authority_name"],
        "source_family": record["source_family"], "source_role": record["source_role"],
        "official_source_url": url, "source_title": _title(record),
        "source_evidence": _evidence(record),
        "source_evidence_digest": record.get("source_evidence_digest"),
        "provenance_run_id": record["provenance_run_id"],
        "provenance_record_ref": record["provenance_record_ref"],
        "retrieval_status": record["retrieval_status"], "parser_status": record["parser_status"],
        "relevance_status": record["relevance_status"],
        "retry_telemetry_summary": record.get("retry_telemetry_summary"),
        "reviewer_status":"NEW", "reviewer_disposition":None,
        "reviewer_acknowledgement":{"acknowledged":False,"acknowledged_at":None,
            "reviewer_id":None,"annotation_version":None,"acknowledged_status":None,
            "acknowledged_disposition":None},
        "reviewer_note":None, "annotation_ref":None, "notification_eligible":False,
    }


def derive_semantic_review_item(record: Mapping[str, Any]) -> dict[str, Any]:
    item = _base(record)
    upstream = record["upstream_event_id"]
    relationship = record.get("affected_relationship_id") or semantic_relationship_id(record["site_id"], upstream)
    lifecycle = record["lifecycle_classification"]
    if lifecycle not in ALLOWED_LIFECYCLES or lifecycle == "NONE":
        raise ValueError("semantic item requires an existing semantic lifecycle")
    prior, current = record.get("prior_accepted_state_ref"), record.get("current_accepted_state_ref")
    item.update({"upstream_event_id":upstream,"affected_relationship_id":relationship,
        "event_category":record["event_category"],"lifecycle_classification":lifecycle,
        "prior_accepted_state_ref":prior,"current_accepted_state_ref":current,
        "data_quality_state":{"classification":"NONE","reason_code":None},
        "unknown_state":{"unknown":False,"reason_code":None},
        "alert_detail_guard_required":False,"interpretation_prohibitions":[]})
    item["review_item_id"] = semantic_review_item_id(upstream, relationship, prior, current, lifecycle)
    validate_review_item_contract(item)
    return item


def derive_quality_review_item(record: Mapping[str, Any]) -> dict[str, Any]:
    item = _base(record)
    reason = record["data_quality_reason_code"]
    logical = record["logical_request_id"]
    relationship = record.get("affected_relationship_id") or quality_relationship_id(
        record["site_id"], logical, record["source_role"], record["official_source_url"])
    prior = record.get("prior_accepted_state_ref")
    final_failed = record["retrieval_status"] == "FINAL_FAILED"
    current = record.get("current_accepted_state_ref", prior if final_failed else None)
    guard = (record["authority_id"] == "USFS" and record["source_role"] == "ALERT_DETAIL" and final_failed)
    prohibitions = list(GUARD_PROHIBITIONS if guard else ("NO_SEMANTIC_CHANGE_INFERENCE",))
    item.update({"upstream_event_id":None,"affected_relationship_id":relationship,
        "event_category":None,"lifecycle_classification":"NONE",
        "prior_accepted_state_ref":prior,"current_accepted_state_ref":current,
        "data_quality_state":{"classification":"DATA_QUALITY","reason_code":reason},
        "unknown_state":{"unknown":True,"reason_code":record.get("unknown_reason_code", reason)},
        "alert_detail_guard_required":guard,"interpretation_prohibitions":prohibitions})
    item["review_item_id"] = quality_review_item_id(item["provenance_run_id"], logical,
        item["source_role"], item["official_source_url"], reason)
    validate_review_item_contract(item)
    return item


def derive_review_items_for_cohort(records: Iterable[Mapping[str, Any]], config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = dict(config) if config is not None else load_phase5_config()
    cohort = {entry["site_id"] for entry in cfg["cohort"]}
    result = []
    for record in records:
        if record.get("site_id") not in cohort:
            continue
        result.append(derive_semantic_review_item(record) if record.get("item_kind") == "semantic" else derive_quality_review_item(record))
    return result


def derive_review_items_for_run(run_evidence_path: str | Path, config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Read a persisted normalized run-evidence JSON document; never writes or retrieves."""
    document = json.loads(Path(run_evidence_path).read_text(encoding="utf-8"))
    records = document["records"] if isinstance(document, dict) else document
    return derive_review_items_for_cohort(records, config)


def validate_review_item_contract(item: Mapping[str, Any]) -> None:
    required = {"schema_version","review_item_id","upstream_event_id","affected_relationship_id",
        "detected_at","retrieved_at","site_id","campground_name","authority_id","authority_name",
        "source_family","source_role","official_source_url","source_title","source_evidence",
        "source_evidence_digest","provenance_run_id","provenance_record_ref","event_category",
        "lifecycle_classification","prior_accepted_state_ref","current_accepted_state_ref",
        "retrieval_status","parser_status","relevance_status","data_quality_state","unknown_state",
        "retry_telemetry_summary","reviewer_status","reviewer_disposition","reviewer_acknowledgement",
        "reviewer_note","annotation_ref","alert_detail_guard_required","notification_eligible",
        "interpretation_prohibitions"}
    if set(item) != required:
        raise ValueError(f"review item fields differ: missing={required-set(item)}, extra={set(item)-required}")
    if item["schema_version"] != SCHEMA_VERSION or item["notification_eligible"] is not False:
        raise ValueError("schema/notification invariant violated")
    if item["reviewer_status"] != "NEW" or item["reviewer_disposition"] is not None:
        raise ValueError("Phase 5-1 reviewer projection must be NEW/unannotated")
    if item["retrieval_status"] not in {"SUCCESS", "FAILED_FIRST_ATTEMPT_RESCUED", "FINAL_FAILED"}:
        raise ValueError("invalid retrieval status")
    if item["parser_status"] not in {"SUCCESS", "FAILED", "NOT_RUN", "NOT_APPLICABLE"}:
        raise ValueError("invalid parser status")
    if item["relevance_status"] not in {"RELEVANT", "NOT_RELEVANT", "UNKNOWN", "NOT_EVALUATED"}:
        raise ValueError("invalid relevance status")
    if item["lifecycle_classification"] not in ALLOWED_LIFECYCLES:
        raise ValueError("invalid lifecycle")
    for field in ("detected_at",):
        if not (str(item[field]).endswith("Z") or str(item[field]).endswith("+00:00")):
            raise ValueError(f"{field} must be UTC")
    if item["retrieved_at"] is not None and not (str(item["retrieved_at"]).endswith("Z") or str(item["retrieved_at"]).endswith("+00:00")):
        raise ValueError("retrieved_at must be UTC")
    if item["alert_detail_guard_required"] and not set(GUARD_PROHIBITIONS).issubset(item["interpretation_prohibitions"]):
        raise ValueError("guarded item lacks mandatory prohibitions")
