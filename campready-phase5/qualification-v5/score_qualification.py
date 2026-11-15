#!/usr/bin/env python3
"""Strict offline validator and deterministic Phase 5-4 V5 scorer."""
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
    "quality_defect_basis", "followup_operation", "followup_target",
    "safe_to_infer_appearance", "safe_to_infer_disappearance", "safe_to_infer_resolution",
    "safe_to_infer_trip_safety", "safe_to_infer_road_passability",
    "safe_to_treat_source_silence_as_safe", "shared_event_proves_duplicate_relationships",
    "reviewer_note",
}
TOP_FIELDS = {
    "qualification_version", "instrument_digest", "reviewer_id", "started_at",
    "submitted_at", "independence_attestation", "glossary_read_attestation",
    "cases", "acknowledgement_workflow",
}
YES_NO = {"YES", "NO"}
ACTIONS = {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP", "ACKNOWLEDGED"}
DISTINCTIONS = {"DATA_QUALITY", "SEMANTIC_CHANGE", "NEITHER", "UNKNOWN"}
QUALITY_BASES = {"NONE", "FINAL_RETRIEVAL_FAILURE", "PARSER_FAILURE", "PROVENANCE_FAILURE", "EVIDENCE_INTEGRITY_FAILURE", "EVIDENCE_UNAVAILABLE"}
FOLLOWUP_OPERATIONS = {"NONE", "VERIFY", "INSPECT", "OBTAIN", "COMPARE", "CONTACT", "CHECK"}
FOLLOWUP_TARGETS = {"NONE", "PERSISTED_EVIDENCE", "OFFICIAL_SOURCE", "PROVENANCE_RECORD", "PRIOR_CURRENT_STATE", "MISSING_EVIDENCE"}
NOTE_REQUIRED_ACTIONS = {"REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"}
AW_FIELDS = {
    "AW1": {"scenario_id", "immediate_allowed_action", "direct_new_to_acknowledged_permitted", "eventual_manual_acknowledgement_required", "acknowledgement_means_safety_approval", "acknowledgement_means_notification_approval", "source_silence_permits_semantic_resolution"},
    "AW2": {"scenario_id", "data_quality_hold_permitted", "later_acknowledged_transition_available", "eventual_acknowledgement_required", "data_quality_alone_creates_acknowledgement_obligation", "acknowledgement_means_safety_or_notification_approval"},
    "AW3": {"scenario_id", "needs_followup_allowed_immediate_action", "eventual_acknowledgement_required", "unresolved_followup_note_required", "acknowledgement_erases_unresolved_condition", "acknowledgement_makes_trip_safe", "acknowledgement_authorizes_notification"},
}
CATEGORIES = ("GUARD", "DATA_QUALITY_UNKNOWN", "PROVENANCE", "REVIEW_BOUNDARY", "DISPOSITION_FIDELITY", "FANOUT", "UNSAFE_INFERENCE", "ACKNOWLEDGEMENT_WORKFLOW")


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
    if reviewer_id not in {"R-I", "R-J"}:
        raise ValueError("REVIEWER_ID_INVALID")
    instrument = load(INSTRUMENT)
    blank_case = {field: None for field in CASE_FIELDS if field != "case_id"}
    workflows = [
        {field: (scenario_id if field == "scenario_id" else None) for field in AW_FIELDS[scenario_id]}
        for scenario_id in instrument["acknowledgement_workflow_order"]
    ]
    return {
        "qualification_version": "phase5-4-v5", "instrument_digest": sha256(INSTRUMENT),
        "reviewer_id": reviewer_id, "started_at": None, "submitted_at": None,
        "independence_attestation": None, "glossary_read_attestation": None,
        "cases": [dict(blank_case, case_id=case_id) for case_id in instrument["case_order"]],
        "acknowledgement_workflow": workflows,
    }


def validate_response(response: Mapping[str, Any]) -> None:
    instrument = load(INSTRUMENT)
    if not isinstance(response, Mapping) or set(response) != TOP_FIELDS:
        raise ValueError("RESPONSE_FIELDS_INVALID")
    if response["qualification_version"] != "phase5-4-v5" or response["instrument_digest"] != sha256(INSTRUMENT):
        raise ValueError("QUALIFICATION_IDENTITY_INVALID")
    if response["reviewer_id"] not in {"R-I", "R-J"}:
        raise ValueError("REVIEWER_ID_INVALID")
    if response["independence_attestation"] is not True:
        raise ValueError("INDEPENDENCE_ATTESTATION_REQUIRED")
    if response["glossary_read_attestation"] is not True:
        raise ValueError("GLOSSARY_ATTESTATION_REQUIRED")
    if _utc(response["submitted_at"]) < _utc(response["started_at"]):
        raise ValueError("TIMESTAMP_ORDER_INVALID")
    cases = response["cases"]
    if not isinstance(cases, list) or [row.get("case_id") for row in cases if isinstance(row, Mapping)] != instrument["case_order"]:
        raise ValueError("CASE_ORDER_INVALID")
    for row in cases:
        if not isinstance(row, Mapping) or set(row) != CASE_FIELDS:
            raise ValueError("CASE_FIELDS_INVALID")
        booleans = ("source_fact_identified", "system_classification_identified", "reviewer_annotation_boundary_understood", "official_provenance_identified")
        if not all(isinstance(row[field], bool) for field in booleans):
            raise ValueError("BOOLEAN_FIELD_INVALID")
        if row["selected_reviewer_action"] not in ACTIONS or row["data_quality_or_semantic_change"] not in DISTINCTIONS:
            raise ValueError("CASE_ENUM_INVALID")
        if row["quality_defect_basis"] not in QUALITY_BASES or row["followup_operation"] not in FOLLOWUP_OPERATIONS or row["followup_target"] not in FOLLOWUP_TARGETS:
            raise ValueError("STRUCTURED_DISPOSITION_ENUM_INVALID")
        yes_no = [field for field in CASE_FIELDS if field.startswith("safe_to_") or field in {"guard_required", "shared_event_proves_duplicate_relationships"}]
        if any(row[field] not in YES_NO for field in yes_no):
            raise ValueError("YES_NO_INVALID")
        note = row["reviewer_note"]
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValueError("NOTE_INVALID")
        if row["selected_reviewer_action"] in NOTE_REQUIRED_ACTIONS and not (isinstance(note, str) and note.strip()):
            raise ValueError("WORKFLOW_NOTE_REQUIRED")
    workflow = response["acknowledgement_workflow"]
    if not isinstance(workflow, list) or [row.get("scenario_id") for row in workflow if isinstance(row, Mapping)] != instrument["acknowledgement_workflow_order"]:
        raise ValueError("ACKNOWLEDGEMENT_WORKFLOW_ORDER_INVALID")
    for row in workflow:
        scenario_id = row["scenario_id"]
        if set(row) != AW_FIELDS[scenario_id]:
            raise ValueError("ACKNOWLEDGEMENT_WORKFLOW_FIELDS_INVALID")
        for field, value in row.items():
            if field not in {"scenario_id", "immediate_allowed_action"} and value not in YES_NO:
                raise ValueError("ACKNOWLEDGEMENT_WORKFLOW_ENUM_INVALID")
        if scenario_id == "AW1" and row["immediate_allowed_action"] not in ACTIONS:
            raise ValueError("ACKNOWLEDGEMENT_WORKFLOW_ENUM_INVALID")


def score_response(response: Mapping[str, Any]) -> dict[str, Any]:
    validate_response(response)
    rubric = load(EXPECTED)
    expected = {row["case_id"]: row for row in rubric["cases"]}
    violations: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    assessments: list[dict[str, str]] = []
    for row in response["cases"]:
        case_id = row["case_id"]
        exp = expected[case_id]
        if row["guard_required"] != exp["guard"]:
            violations["GUARD"].append(f"{case_id}:guard_required")
        if exp["guard"] == "YES" and row["selected_reviewer_action"] in {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE"}:
            violations["GUARD"].append(f"{case_id}:guarded_direct_disposition")
        if row["data_quality_or_semantic_change"] != exp["distinction"]:
            violations["DATA_QUALITY_UNKNOWN"].append(f"{case_id}:data_quality_or_semantic_change")
        if row["official_provenance_identified"] is not True:
            violations["PROVENANCE"].append(f"{case_id}:official_provenance_identified")
        for field in ("source_fact_identified", "system_classification_identified", "reviewer_annotation_boundary_understood"):
            if row[field] is not True:
                violations["REVIEW_BOUNDARY"].append(f"{case_id}:{field}")
        if row["shared_event_proves_duplicate_relationships"] != "NO":
            violations["FANOUT"].append(f"{case_id}:shared_event_proves_duplicate_relationships")
        unsafe = {field: "NO" for field in CASE_FIELDS if field.startswith("safe_to_")}
        unsafe["safe_to_infer_appearance"] = exp["appearance"]
        for field, wanted in unsafe.items():
            if row[field] != wanted:
                violations["UNSAFE_INFERENCE"].append(f"{case_id}:{field}")

        action_valid = row["selected_reviewer_action"] in exp["acceptable_actions"]
        action_class = exp["disposition_classification"] if action_valid else "CONTRACTUALLY_INVALID_ACTION"
        if not action_valid:
            violations["DISPOSITION_FIDELITY"].append(f"{case_id}:contractually_invalid_action")
        basis_valid = row["quality_defect_basis"] in exp["allowed_quality_defect_basis"]
        action = row["selected_reviewer_action"]
        operation, target = row["followup_operation"], row["followup_target"]
        if action == "DATA_QUALITY_HOLD" and row["quality_defect_basis"] == "NONE":
            basis_valid = False
        if action == "NEEDS_FOLLOWUP":
            pair_valid = operation != "NONE" and target != "NONE" and [operation, target] in exp["allowed_followup_pairs"]
        else:
            pair_valid = operation == "NONE" and target == "NONE"
        if not basis_valid:
            violations["DISPOSITION_FIDELITY"].append(f"{case_id}:quality_defect_basis")
        if not pair_valid:
            violations["DISPOSITION_FIDELITY"].append(f"{case_id}:followup_scope")
        assessments.append({
            "case_id": case_id,
            "action_classification": action_class,
            "structured_basis_classification": "STRUCTURED_DISPOSITION_BASIS_VALID" if basis_valid and pair_valid else "STRUCTURED_DISPOSITION_BASIS_INVALID",
        })
    workflow_expected = {row["scenario_id"]: row["answers"] for row in rubric["acknowledgement_workflow"]}
    for row in response["acknowledgement_workflow"]:
        for field, wanted in workflow_expected[row["scenario_id"]].items():
            wrong = row[field] not in wanted if isinstance(wanted, list) else row[field] != wanted
            if wrong:
                violations["ACKNOWLEDGEMENT_WORKFLOW"].append(f"{row['scenario_id']}:{field}")
    return {
        "reviewer_id": response["reviewer_id"],
        "contract_pass": all(not values for values in violations.values()),
        "mandatory_violations": violations,
        "disposition_assessments": assessments,
        "duration_seconds": int((_utc(response["submitted_at"]) - _utc(response["started_at"])).total_seconds()),
    }


def evaluate_two(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(responses) != 2:
        raise ValueError("BOTH_IMMUTABLE_SUBMISSIONS_REQUIRED")
    snapshots = copy.deepcopy(list(responses))
    for response in snapshots:
        validate_response(response)
    if {response["reviewer_id"] for response in snapshots} != {"R-I", "R-J"}:
        raise ValueError("FRESH_REVIEWERS_R_I_R_J_REQUIRED")
    digest = lambda value: "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    scores = [score_response(response) for response in snapshots]
    return {"schema_version": "campready.phase5.qualification_evaluation.v5", "submission_digests": [digest(response) for response in snapshots], "reviewers": scores, "contract_pass": all(score["contract_pass"] for score in scores)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blank-template", type=Path)
    parser.add_argument("--reviewer-id", choices=("R-I", "R-J"), default="R-I")
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
