#!/usr/bin/env python3
"""Strict offline validator and deterministic Phase 5-4 V2 scorer."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
INSTRUMENT = HERE / "instrument.json"
EXPECTED = HERE / "expected_results.json"
CASE_FIELDS = {
    "case_id", "source_fact_identified", "system_classification_identified",
    "reviewer_annotation_boundary_understood", "official_provenance_identified",
    "selected_reviewer_action", "data_quality_or_semantic_change", "guard_required",
    "safe_to_infer_appearance", "safe_to_infer_disappearance", "safe_to_infer_resolution",
    "safe_to_infer_trip_safety", "safe_to_infer_road_passability",
    "safe_to_treat_source_silence_as_safe", "acknowledgement_required",
    "shared_event_proves_duplicate_relationships",
    "acknowledgement_means_safe_or_notification_approved", "reviewer_note",
}
TOP_FIELDS = {
    "qualification_version", "instrument_digest", "reviewer_id", "started_at",
    "submitted_at", "independence_attestation", "glossary_read_attestation", "cases",
}
YES_NO = {"YES", "NO"}
ACTIONS = {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP", "ACKNOWLEDGED"}
DISTINCTIONS = {"DATA_QUALITY", "SEMANTIC_CHANGE", "NEITHER", "UNKNOWN"}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return parsed


def blank_response(reviewer_id: str) -> dict[str, Any]:
    if reviewer_id not in {"R-C", "R-D"}:
        raise ValueError("REVIEWER_ID_INVALID")
    instrument = load(INSTRUMENT)
    blank = {field: None for field in CASE_FIELDS if field != "case_id"}
    return {
        "qualification_version": "phase5-4-v2", "instrument_digest": sha256(INSTRUMENT),
        "reviewer_id": reviewer_id, "started_at": None, "submitted_at": None,
        "independence_attestation": None, "glossary_read_attestation": None,
        "cases": [dict(blank, case_id=case_id) for case_id in instrument["case_order"]],
    }


def validate_response(response: Mapping[str, Any]) -> None:
    instrument = load(INSTRUMENT)
    if set(response) != TOP_FIELDS:
        raise ValueError("RESPONSE_FIELDS_INVALID")
    if response["qualification_version"] != "phase5-4-v2":
        raise ValueError("QUALIFICATION_VERSION_INVALID")
    if response["instrument_digest"] != sha256(INSTRUMENT):
        raise ValueError("INSTRUMENT_DIGEST_INVALID")
    if response["reviewer_id"] not in {"R-C", "R-D"}:
        raise ValueError("REVIEWER_ID_INVALID")
    if response["independence_attestation"] is not True:
        raise ValueError("INDEPENDENCE_ATTESTATION_REQUIRED")
    if response["glossary_read_attestation"] is not True:
        raise ValueError("GLOSSARY_ATTESTATION_REQUIRED")
    if _utc(response["submitted_at"]) < _utc(response["started_at"]):
        raise ValueError("TIMESTAMP_ORDER_INVALID")
    cases = response["cases"]
    if not isinstance(cases, list) or [row.get("case_id") for row in cases] != instrument["case_order"]:
        raise ValueError("CASE_ORDER_INVALID")
    for row in cases:
        if not isinstance(row, Mapping) or set(row) != CASE_FIELDS:
            raise ValueError("CASE_FIELDS_INVALID")
        booleans = ("source_fact_identified", "system_classification_identified", "reviewer_annotation_boundary_understood", "official_provenance_identified")
        if not all(isinstance(row[field], bool) for field in booleans):
            raise ValueError("BOOLEAN_FIELD_INVALID")
        if row["selected_reviewer_action"] not in ACTIONS or row["data_quality_or_semantic_change"] not in DISTINCTIONS:
            raise ValueError("ENUM_INVALID")
        yes_no_fields = [field for field in CASE_FIELDS if field.startswith("safe_to_") or field in {"guard_required", "acknowledgement_required", "shared_event_proves_duplicate_relationships", "acknowledgement_means_safe_or_notification_approved"}]
        if any(row[field] not in YES_NO for field in yes_no_fields):
            raise ValueError("YES_NO_INVALID")
        note = row["reviewer_note"]
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValueError("NOTE_INVALID")
        if row["selected_reviewer_action"] in {"REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"} and not (isinstance(note, str) and note.strip()):
            raise ValueError("WORKFLOW_NOTE_REQUIRED")


def score_response(response: Mapping[str, Any]) -> dict[str, Any]:
    validate_response(response)
    rubric = load(EXPECTED)
    expected = {row["case_id"]: row for row in rubric["cases"]}
    safety: list[str] = []
    discretionary: list[str] = []
    for row in response["cases"]:
        exp = expected[row["case_id"]]
        checks = dict(
            rubric["universal"], data_quality_or_semantic_change=exp["distinction"],
            guard_required=exp["guard"], safe_to_infer_appearance=exp["appearance"],
            acknowledgement_required=exp["ack"],
        )
        for field, wanted in checks.items():
            if row[field] != wanted:
                safety.append(f"{row['case_id']}:{field}")
        if exp["guard"] == "YES" and row["selected_reviewer_action"] in {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE"}:
            safety.append(f"{row['case_id']}:GUARDED_DIRECT_DISPOSITION")
        if row["selected_reviewer_action"] not in exp["acceptable_actions"]:
            discretionary.append(f"{row['case_id']}:selected_reviewer_action")
    return {
        "reviewer_id": response["reviewer_id"], "contract_pass": not safety,
        "safety_critical_violations": sorted(set(safety)),
        "discretionary_disagreements": discretionary,
        "duration_seconds": int((_utc(response["submitted_at"]) - _utc(response["started_at"])).total_seconds()),
    }


def evaluate_two(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(responses) != 2:
        raise ValueError("BOTH_IMMUTABLE_SUBMISSIONS_REQUIRED")
    snapshots = copy.deepcopy(list(responses))
    for response in snapshots:
        validate_response(response)
    if {response["reviewer_id"] for response in snapshots} != {"R-C", "R-D"}:
        raise ValueError("FRESH_REVIEWERS_R_C_R_D_REQUIRED")
    digest = lambda value: "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    scores = [score_response(response) for response in snapshots]
    return {
        "schema_version": "campready.phase5.qualification_evaluation.v2",
        "historical_v1_decision": "HOLD_PHASE5_4_USABILITY_GUARD_COMPREHENSION",
        "submission_digests": [digest(response) for response in snapshots],
        "reviewers": scores, "contract_pass": all(score["contract_pass"] for score in scores),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blank-template", type=Path)
    parser.add_argument("--reviewer-id", choices=("R-C", "R-D"), default="R-C")
    parser.add_argument("responses", nargs="*")
    args = parser.parse_args()
    if args.blank_template:
        args.blank_template.write_text(json.dumps(blank_response(args.reviewer_id), indent=2) + "\n", encoding="utf-8")
    if args.responses:
        loaded = [load(Path(path)) for path in args.responses]
        result = evaluate_two(loaded) if len(loaded) == 2 else score_response(loaded[0])
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
