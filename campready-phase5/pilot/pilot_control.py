"""Deterministic control sampling, blinding, state, and STOP/HOLD rules."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from pilot_store import canonical_bytes, parse_utc


STATES = {"NOT_STARTED", "ACTIVE", "HOLD", "STOPPED", "CLOSED"}
HISTORY = (
    "Phase 5-4 did not pass its original two-human qualification gate.",
    "Phase 5-5 proceeded under explicit owner governance override.",
)
BLINDED_EXCLUSIONS = {
    "reviewer_disposition", "notes_or_rationale", "correction_required",
    "correction_reason", "corrected_disposition", "adjudication_result",
    "confirmed_structured_disposition_incompatibility", "comprehension_error_classes",
    "disagreement_type", "unsafe_inference",
}
IMMEDIATE_FLAGS = {
    "notification_or_delivery_enabled_or_invoked",
    "unexpected_canonical_semantic_write",
    "guard_bypass_permitting_prohibited_action",
    "trip_safety_endorsement",
    "road_passability_endorsement",
    "source_silence_treated_as_safety_or_resolution",
    "uncontained_false_lifecycle_event_accepted_as_truth",
    "material_protocol_or_observation_corruption",
    "unsafe_inference",
}
REPEATED_CLASSES = {
    "UNKNOWN_NONE_DQ_HOLD_CONFLICT",
    "GUARD_COMPREHENSION_ERROR",
    "ACKNOWLEDGEMENT_WORKFLOW_ERROR",
    "SOURCE_SYSTEM_REVIEW_BOUNDARY_ERROR",
}


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": "campready.phase5.pilot_state.v1",
        "state": "NOT_STARTED",
        "pilot_started_at": None,
        "protocol_digest": None,
        "cohort_digest": None,
        "role_binding_digest": None,
        "valid_observation_count": 0,
        "control_sampling_rule_id": "INTAKE_SEQUENCE_MOD_5_MAX_20",
        "stop_rule_id": "PHASE5_5B_FROZEN_STOP_HOLD_V1",
        "history": list(HISTORY),
    }


def validate_prelaunch_state(state: Mapping[str, Any]) -> None:
    expected = initial_state()
    if set(state) != set(expected) or state.get("state") != "NOT_STARTED" or state.get("pilot_started_at") is not None or state.get("valid_observation_count") != 0:
        raise ValueError("PRELAUNCH_STATE_INVALID")
    if state.get("history") != list(HISTORY):
        raise ValueError("HISTORICAL_TRANSPARENCY_MISSING")


def control_sample_selected(intake_sequence: int) -> bool:
    if not isinstance(intake_sequence, int) or isinstance(intake_sequence, bool) or not 1 <= intake_sequence <= 100:
        raise ValueError("INTAKE_SEQUENCE_INVALID")
    return intake_sequence % 5 == 0


def blinded_secondary_packet(observation: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "observation_id", "intake_sequence", "review_item_id", "site_id",
        "campground_name", "affected_relationship_id", "source_role",
        "source_authority", "source_evidence_ref", "event_category",
        "system_classification", "relevance_status", "quality_defect_basis",
        "data_quality_state", "guard_required", "retrieval_first_attempt_outcome",
        "retrieval_eventual_outcome", "parser_status", "rescued_retry", "evidence_refs",
    }
    packet = {key: observation[key] for key in sorted(allowed) if key in observation}
    if set(packet) & BLINDED_EXCLUSIONS:
        raise ValueError("SECONDARY_BLINDING_INVALID")
    return packet


def validate_role_bindings(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = {"schema_version", "bindings", "three_distinct_humans_attested", "roles_frozen_before_pilot_started_at", "owner_attested_at"}
    if set(value) != expected or value.get("schema_version") != "campready.phase5.pilot_role_bindings.v1":
        raise ValueError("ROLE_BINDING_INCOMPLETE")
    bindings = value.get("bindings")
    roles = {"PRIMARY_REVIEWER", "SECOND_REVIEWER", "OWNER_ADJUDICATOR"}
    if not isinstance(bindings, dict) or set(bindings) != roles or not all(isinstance(code, str) and code.strip() for code in bindings.values()):
        raise ValueError("ROLE_BINDING_INCOMPLETE")
    placeholder_markers = ("REPLACE", "PLACEHOLDER", "<", ">")
    if any(any(marker in code.upper() for marker in placeholder_markers) for code in bindings.values()):
        raise ValueError("ROLE_BINDING_INCOMPLETE")
    if len(set(bindings.values())) != 3 or value.get("three_distinct_humans_attested") is not True or value.get("roles_frozen_before_pilot_started_at") is not True:
        raise ValueError("ROLE_BINDING_INCOMPLETE")
    parse_utc(value.get("owner_attested_at"))
    return {"binding_present": True, "three_distinct_humans_attested": True, "stable_blinded_codes": bindings, "digest": hashlib.sha256(canonical_bytes(value)).hexdigest()}


def evaluate_stop_hold(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(events)
    for row in rows:
        if any(row.get(flag) is True for flag in IMMEDIATE_FLAGS) or "UNSAFE_INFERENCE" in row.get("error_classes", []):
            return {"result": "PILOT_STOP_SAFETY_BOUNDARY", "trigger_observation_id": row.get("observation_id")}
    identities: dict[str, set[str]] = {name: set() for name in REPEATED_CLASSES}
    for row in rows:
        identity = row.get("review_item_id") or row.get("observation_id")
        if not identity:
            continue
        if row.get("unknown_none_dq_hold_conflict") is True and row.get("confirmed_structured_disposition_incompatibility") is True:
            identities["UNKNOWN_NONE_DQ_HOLD_CONFLICT"].add(identity)
        for name in REPEATED_CLASSES - {"UNKNOWN_NONE_DQ_HOLD_CONFLICT"}:
            if name in row.get("error_classes", []):
                identities[name].add(identity)
        if "PROVENANCE_COMPREHENSION_ERROR" in row.get("error_classes", []):
            identities["SOURCE_SYSTEM_REVIEW_BOUNDARY_ERROR"].add(identity)
    for name, values in identities.items():
        if len(values) >= 2:
            return {"result": "PILOT_HOLD_REVIEWER_COMPREHENSION", "trigger_class": name, "distinct_item_count": len(values)}
    return {"result": "CONTINUE"}


def evaluate_window(valid_observation_count: int, pilot_started_at: str, evaluated_at: str, events: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    safety = evaluate_stop_hold(events)
    if safety["result"] != "CONTINUE":
        return safety
    if not isinstance(valid_observation_count, int) or isinstance(valid_observation_count, bool) or valid_observation_count < 0:
        raise ValueError("OBSERVATION_COUNT_INVALID")
    start, now = parse_utc(pilot_started_at), parse_utc(evaluated_at)
    if valid_observation_count >= 100:
        return {"result": "NORMAL_COLLECTION_BOUNDARY", "reason": "COUNT_100"}
    if now >= start + timedelta(hours=336):
        return {"result": "NORMAL_COLLECTION_BOUNDARY", "reason": "HOURS_336"}
    return {"result": "CONTINUE"}


def prospective_start_record(state: Mapping[str, Any], *, owner_authorized: bool, pilot_started_at: str,
                             protocol_digest: str, cohort_digest: str, role_binding_digest: str) -> dict[str, Any]:
    """Build start evidence for a later explicit owner action; never writes it."""
    validate_prelaunch_state(state)
    if owner_authorized is not True:
        raise ValueError("EXPLICIT_OWNER_AUTHORIZATION_REQUIRED")
    parse_utc(pilot_started_at)
    digests = (protocol_digest, cohort_digest, role_binding_digest)
    if any(not re_full_digest(value) for value in digests):
        raise ValueError("START_DIGEST_INVALID")
    result = dict(state)
    result.update({"state":"ACTIVE", "pilot_started_at":pilot_started_at, "protocol_digest":protocol_digest,
                   "cohort_digest":cohort_digest, "role_binding_digest":role_binding_digest,
                   "valid_observation_count":0})
    return result


def re_full_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
