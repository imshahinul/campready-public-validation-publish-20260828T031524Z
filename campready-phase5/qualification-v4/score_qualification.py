#!/usr/bin/env python3
"""Strict offline validator and deterministic Phase 5-4 V4 scorer."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
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
    "safe_to_treat_source_silence_as_safe", "shared_event_proves_duplicate_relationships",
    "reviewer_note",
}
TOP_FIELDS = {"qualification_version", "instrument_digest", "reviewer_id", "started_at", "submitted_at", "independence_attestation", "glossary_read_attestation", "cases", "acknowledgement_workflow"}
YES_NO = {"YES", "NO"}
ACTIONS = {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP", "ACKNOWLEDGED"}
DISTINCTIONS = {"DATA_QUALITY", "SEMANTIC_CHANGE", "NEITHER", "UNKNOWN"}
AW_FIELDS = {
    "AW1": {"scenario_id", "immediate_allowed_action", "direct_new_to_acknowledged_permitted", "eventual_manual_acknowledgement_required", "acknowledgement_means_safety_approval", "acknowledgement_means_notification_approval", "source_silence_permits_semantic_resolution"},
    "AW2": {"scenario_id", "data_quality_hold_permitted", "later_acknowledged_transition_available", "eventual_acknowledgement_required", "data_quality_alone_creates_acknowledgement_obligation", "acknowledgement_means_safety_or_notification_approval"},
    "AW3": {"scenario_id", "needs_followup_allowed_immediate_action", "eventual_acknowledgement_required", "unresolved_followup_note_required", "acknowledgement_erases_unresolved_condition", "acknowledgement_makes_trip_safe", "acknowledgement_authorizes_notification"},
}
CATEGORIES = ("GUARD", "DATA_QUALITY_UNKNOWN", "PROVENANCE", "REVIEW_BOUNDARY", "DISPOSITION_FIDELITY", "FANOUT", "UNSAFE_INFERENCE", "ACKNOWLEDGEMENT_WORKFLOW")
UNKNOWN_CASES = {"X405", "X407"}
QUALITY_TERMS = ("retrieval", "parser", "provenance", "integrity", "evidence quality", "acquisition", "final failure", "unavailable")
INVESTIGATION_TERMS = ("investigate", "verify", "inspect", "obtain", "compare", "contact", "check")
UNSAFE_NOTE_PATTERNS = (
    r"\btrip\b.{0,24}\bsafe\b", r"\bsafe for (?:travel|camping|a trip)\b",
    r"\broad\b.{0,24}\bpassable\b", r"\bsource silence\b.{0,32}\b(?:safe|safety)\b",
    r"\bno (?:alert|report|notice)\b.{0,24}\b(?:safe|safety)\b",
)


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
    if reviewer_id not in {"R-G", "R-H"}:
        raise ValueError("REVIEWER_ID_INVALID")
    instrument = load(INSTRUMENT)
    blank_case = {field: None for field in CASE_FIELDS if field != "case_id"}
    workflows = []
    for scenario_id in instrument["acknowledgement_workflow_order"]:
        workflows.append({field: (scenario_id if field == "scenario_id" else None) for field in AW_FIELDS[scenario_id]})
    return {"qualification_version":"phase5-4-v4", "instrument_digest":sha256(INSTRUMENT), "reviewer_id":reviewer_id, "started_at":None, "submitted_at":None, "independence_attestation":None, "glossary_read_attestation":None, "cases":[dict(blank_case, case_id=case_id) for case_id in instrument["case_order"]], "acknowledgement_workflow":workflows}


def validate_response(response: Mapping[str, Any]) -> None:
    instrument = load(INSTRUMENT)
    if not isinstance(response, Mapping) or set(response) != TOP_FIELDS:
        raise ValueError("RESPONSE_FIELDS_INVALID")
    if response["qualification_version"] != "phase5-4-v4" or response["instrument_digest"] != sha256(INSTRUMENT):
        raise ValueError("QUALIFICATION_IDENTITY_INVALID")
    if response["reviewer_id"] not in {"R-G", "R-H"}:
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
        if not all(isinstance(row[field], bool) for field in ("source_fact_identified", "system_classification_identified", "reviewer_annotation_boundary_understood", "official_provenance_identified")):
            raise ValueError("BOOLEAN_FIELD_INVALID")
        if row["selected_reviewer_action"] not in ACTIONS or row["data_quality_or_semantic_change"] not in DISTINCTIONS:
            raise ValueError("CASE_ENUM_INVALID")
        yes_no = [field for field in CASE_FIELDS if field.startswith("safe_to_") or field in {"guard_required", "shared_event_proves_duplicate_relationships"}]
        if any(row[field] not in YES_NO for field in yes_no):
            raise ValueError("YES_NO_INVALID")
        note = row["reviewer_note"]
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValueError("NOTE_INVALID")
        if row["selected_reviewer_action"] in {"REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"} and not (isinstance(note, str) and note.strip()):
            raise ValueError("WORKFLOW_NOTE_REQUIRED")
        if row["case_id"] in UNKNOWN_CASES and row["selected_reviewer_action"] == "REVIEWED_VALID" and not (isinstance(note, str) and note.strip()):
            raise ValueError("UNKNOWN_VALIDATION_NOTE_REQUIRED")
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


def _note_fidelity(case_id: str, action: str, note: Any) -> str | None:
    """Return a stable defect label when an action's mandatory note semantics fail."""
    text = note.strip().lower() if isinstance(note, str) else ""
    if action == "REVIEWED_NOT_ACTIONABLE":
        if case_id in UNKNOWN_CASES and not ("unknown" in text and "bounded" in text and "no further" in text):
            return "unknown_not_actionable_note"
        if case_id in UNKNOWN_CASES and any(term in text for term in ("irrelevant", "universally not actionable", "never actionable")):
            return "unknown_not_actionable_overclaim"
    elif action == "REVIEWED_VALID" and case_id in UNKNOWN_CASES:
        if not all(term in text for term in ("unknown", "classification", "evidence")) or not any(term in text for term in ("does not claim", "without claiming")):
            return "unknown_valid_classification_note"
    elif action == "NEEDS_FOLLOWUP":
        if not ("bounded" in text and any(term in text for term in INVESTIGATION_TERMS)):
            return "concrete_investigation_note"
    elif action == "DATA_QUALITY_HOLD":
        if not any(term in text for term in QUALITY_TERMS):
            return "actual_quality_defect_note"
    return None


def score_response(response: Mapping[str, Any]) -> dict[str, Any]:
    validate_response(response)
    rubric = load(EXPECTED)
    expected = {row["case_id"]: row for row in rubric["cases"]}
    violations: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    disposition_assessments: list[dict[str, str]] = []
    for row in response["cases"]:
        exp = expected[row["case_id"]]
        if row["guard_required"] != exp["guard"]:
            violations["GUARD"].append(f"{row['case_id']}:guard_required")
        if exp["guard"] == "YES" and row["selected_reviewer_action"] in {"REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE"}:
            violations["GUARD"].append(f"{row['case_id']}:guarded_direct_disposition")
        if row["data_quality_or_semantic_change"] != exp["distinction"]:
            violations["DATA_QUALITY_UNKNOWN"].append(f"{row['case_id']}:data_quality_or_semantic_change")
        if row["official_provenance_identified"] is not True:
            violations["PROVENANCE"].append(f"{row['case_id']}:official_provenance_identified")
        for field in ("source_fact_identified", "system_classification_identified", "reviewer_annotation_boundary_understood"):
            if row[field] is not True:
                violations["REVIEW_BOUNDARY"].append(f"{row['case_id']}:{field}")
        if row["shared_event_proves_duplicate_relationships"] != "NO":
            violations["FANOUT"].append(f"{row['case_id']}:shared_event_proves_duplicate_relationships")
        unsafe = {field:"NO" for field in CASE_FIELDS if field.startswith("safe_to_")}
        unsafe["safe_to_infer_appearance"] = exp["appearance"]
        for field, wanted in unsafe.items():
            if row[field] != wanted:
                violations["UNSAFE_INFERENCE"].append(f"{row['case_id']}:{field}")
        note = row["reviewer_note"] or ""
        if any(re.search(pattern, note, re.IGNORECASE) for pattern in UNSAFE_NOTE_PATTERNS):
            violations["UNSAFE_INFERENCE"].append(f"{row['case_id']}:reviewer_note_unsafe_inference")
        action = row["selected_reviewer_action"]
        if action not in exp["acceptable_actions"]:
            disposition_assessments.append({"case_id":row["case_id"], "classification":"CONTRACTUALLY_INVALID_ACTION"})
            violations["DISPOSITION_FIDELITY"].append(f"{row['case_id']}:contractually_invalid_action")
        else:
            defect = _note_fidelity(row["case_id"], action, row["reviewer_note"])
            if defect:
                disposition_assessments.append({"case_id":row["case_id"], "classification":"CONTRACTUALLY_INVALID_ACTION", "reason":defect})
                violations["DISPOSITION_FIDELITY"].append(f"{row['case_id']}:{defect}")
            else:
                disposition_assessments.append({"case_id":row["case_id"], "classification":exp["disposition_classification"]})
    workflow_expected = {row["scenario_id"]: row["answers"] for row in rubric["acknowledgement_workflow"]}
    for row in response["acknowledgement_workflow"]:
        for field, wanted in workflow_expected[row["scenario_id"]].items():
            if (row[field] not in wanted) if isinstance(wanted, list) else (row[field] != wanted):
                violations["ACKNOWLEDGEMENT_WORKFLOW"].append(f"{row['scenario_id']}:{field}")
    return {"reviewer_id":response["reviewer_id"], "contract_pass":all(not values for values in violations.values()), "mandatory_violations":violations, "disposition_assessments":disposition_assessments, "duration_seconds":int((_utc(response["submitted_at"])-_utc(response["started_at"])).total_seconds())}


def evaluate_two(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(responses) != 2:
        raise ValueError("BOTH_IMMUTABLE_SUBMISSIONS_REQUIRED")
    snapshots = copy.deepcopy(list(responses))
    for response in snapshots:
        validate_response(response)
    if {response["reviewer_id"] for response in snapshots} != {"R-G", "R-H"}:
        raise ValueError("FRESH_REVIEWERS_R_G_R_H_REQUIRED")
    digest = lambda value: "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    scores = [score_response(response) for response in snapshots]
    return {"schema_version":"campready.phase5.qualification_evaluation.v4", "submission_digests":[digest(response) for response in snapshots], "reviewers":scores, "contract_pass":all(score["contract_pass"] for score in scores)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blank-template", type=Path)
    parser.add_argument("--reviewer-id", choices=("R-G", "R-H"), default="R-G")
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
