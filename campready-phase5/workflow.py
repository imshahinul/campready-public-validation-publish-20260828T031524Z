"""Reviewer-only append workflow and deterministic current projection."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from alert_detail_guard import validate_alert_detail_guard, validate_guarded_transition
from annotation_store import SCHEMA_VERSION, AnnotationStore, annotation_id, idempotency_key, validate_annotation

ALLOWED_TRANSITIONS = frozenset({
    ("NEW", "REVIEWED_VALID"), ("NEW", "REVIEWED_NOT_ACTIONABLE"),
    ("NEW", "DATA_QUALITY_HOLD"), ("NEW", "NEEDS_FOLLOWUP"),
    ("NEEDS_FOLLOWUP", "REVIEWED_VALID"), ("NEEDS_FOLLOWUP", "REVIEWED_NOT_ACTIONABLE"),
    ("NEEDS_FOLLOWUP", "DATA_QUALITY_HOLD"), ("DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"),
    ("REVIEWED_VALID", "ACKNOWLEDGED"), ("REVIEWED_NOT_ACTIONABLE", "ACKNOWLEDGED"),
    ("DATA_QUALITY_HOLD", "ACKNOWLEDGED"), ("NEEDS_FOLLOWUP", "ACKNOWLEDGED"),
})


def reconstruct_projection(review_item: Mapping[str, Any], annotations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Deep-copy source/system fields and overlay only the reviewer projection."""
    result = copy.deepcopy(dict(review_item))
    expected = 1
    previous_ref = None
    for annotation in annotations:
        validate_annotation(annotation)
        if annotation["review_item_id"] != result["review_item_id"] or annotation["annotation_version"] != expected or annotation["supersedes_ref"] != previous_ref:
            raise ValueError("ANNOTATION_HISTORY_INVALID")
        expected += 1
        previous_ref = annotation["annotation_id"]
        result["reviewer_status"] = annotation["to_status"]
        result["reviewer_disposition"] = annotation["disposition"]
        result["reviewer_note"] = annotation["note"]
        result["annotation_ref"] = annotation["annotation_id"]
        result["reviewer_acknowledgement"] = {
            "acknowledged": annotation["acknowledged"],
            "acknowledged_at": annotation["acknowledged_at"],
            "reviewer_id": annotation["reviewer_id"] if annotation["acknowledged"] else None,
            "annotation_version": annotation["annotation_version"] if annotation["acknowledged"] else None,
            "acknowledged_status": annotation["acknowledged_status"],
            "acknowledged_disposition": annotation["acknowledged_disposition"],
        }
    if result.get("notification_eligible") is not False:
        raise ValueError("NOTIFICATION_INVARIANT_INVALID")
    return result


def _required_text(value: Any, reason: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(reason)


def _validate_requirements(from_status: str, to_status: str, reason_code: Any, note: Any) -> None:
    if (from_status, to_status) == ("NEW", "REVIEWED_NOT_ACTIONABLE"):
        _required_text(note, "NOTE_REQUIRED")
    if (from_status, to_status) == ("NEW", "DATA_QUALITY_HOLD"):
        _required_text(reason_code, "REASON_REQUIRED"); _required_text(note, "NOTE_REQUIRED")
    if (from_status, to_status) == ("NEW", "NEEDS_FOLLOWUP"):
        _required_text(note, "NOTE_REQUIRED")
    if from_status == "NEEDS_FOLLOWUP" and to_status != "ACKNOWLEDGED":
        _required_text(note, "RESOLUTION_NOTE_REQUIRED")
    if (from_status, to_status) == ("DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"):
        _required_text(reason_code, "EVIDENCE_REFERENCE_REQUIRED"); _required_text(note, "EVIDENCE_REFERENCE_REQUIRED")
    if (from_status, to_status) == ("NEEDS_FOLLOWUP", "ACKNOWLEDGED"):
        _required_text(note, "UNRESOLVED_FOLLOWUP_ACKNOWLEDGEMENT_NOTE_REQUIRED")


def build_annotation(review_item: Mapping[str, Any], history: Sequence[Mapping[str, Any]], action: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a reviewer request and build one immutable annotation."""
    if not isinstance(review_item.get("official_source_url"), str) or not review_item["official_source_url"].startswith("https://") or not review_item.get("provenance_run_id") or not review_item.get("provenance_record_ref"):
        raise ValueError("OFFICIAL_PROVENANCE_REQUIRED")
    if review_item.get("notification_eligible") is not False:
        if review_item.get("alert_detail_guard_required"):
            validate_alert_detail_guard(review_item)
        raise ValueError("NOTIFICATION_INVARIANT_INVALID")
    guarded = validate_alert_detail_guard(review_item)
    current = reconstruct_projection(review_item, history)
    current_status = current["reviewer_status"]
    correction = current_status == "ACKNOWLEDGED"
    if correction:
        if action.get("supersedes_ref") != current["annotation_ref"]:
            raise ValueError("ACKNOWLEDGED_IMMUTABLE")
        from_status = current["reviewer_acknowledgement"]["acknowledged_status"]
        if action.get("from_status", from_status) != from_status:
            raise ValueError("CORRECTION_FROM_STATUS_INVALID")
    else:
        from_status = current_status
        if action.get("supersedes_ref") not in (None, current.get("annotation_ref")):
            raise ValueError("SUPERSEDES_REF_INVALID")
    to_status = action.get("to_status")
    correction_targets = {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"}
    if ((from_status, to_status) not in ALLOWED_TRANSITIONS and not (correction and to_status in correction_targets)) or (correction and to_status == "ACKNOWLEDGED"):
        raise ValueError("TRANSITION_INVALID")
    if guarded:
        validate_guarded_transition(review_item, from_status, to_status)
    reason_code, note = action.get("reason_code"), action.get("note")
    _validate_requirements(from_status, to_status, reason_code, note)
    version = len(history) + 1
    supersedes = None if version == 1 else history[-1]["annotation_id"]
    acknowledged = to_status == "ACKNOWLEDGED"
    disposition = action.get("disposition")
    annotation: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "annotation_id": "", "review_item_id": review_item["review_item_id"],
        "annotation_version": version, "supersedes_ref": supersedes, "reviewer_id": action.get("reviewer_id"),
        "timestamp": action.get("timestamp"), "from_status": from_status, "to_status": to_status,
        "disposition": disposition, "reason_code": reason_code, "note": note,
        "acknowledged": acknowledged, "acknowledged_at": action.get("timestamp") if acknowledged else None,
        "acknowledged_status": from_status if acknowledged else None,
        "acknowledged_disposition": current.get("reviewer_disposition") if acknowledged else None,
        "idempotency_key": "",
    }
    annotation["annotation_id"] = annotation_id(annotation)
    annotation["idempotency_key"] = idempotency_key(annotation)
    validate_annotation(annotation)
    return annotation


def apply_reviewer_action(store: AnnotationStore, review_item: Mapping[str, Any], action: Mapping[str, Any]) -> dict[str, Any]:
    """Append only after every contract and transition check succeeds."""
    if not isinstance(review_item.get("official_source_url"), str) or not review_item["official_source_url"].startswith("https://") or not review_item.get("provenance_run_id") or not review_item.get("provenance_record_ref"):
        raise ValueError("OFFICIAL_PROVENANCE_REQUIRED")
    if review_item.get("notification_eligible") is not False:
        if review_item.get("alert_detail_guard_required"):
            validate_alert_detail_guard(review_item)
        raise ValueError("NOTIFICATION_INVARIANT_INVALID")
    validate_alert_detail_guard(review_item)
    history = store.for_item(review_item["review_item_id"])
    # A byte/logically identical action retry returns its immutable prior row.
    # A later human action must carry a new timestamp and becomes a new version.
    for existing in reversed(history):
        if all(existing[key] == action.get(key) for key in ("to_status", "reviewer_id", "timestamp", "disposition", "reason_code", "note")):
            return copy.deepcopy(existing)
    annotation = build_annotation(review_item, history, action)
    return store.append(annotation)
