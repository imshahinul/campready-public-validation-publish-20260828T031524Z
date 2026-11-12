#!/usr/bin/env python3
"""Offline deterministic renderer, validator, and contract scorer."""
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
    "case_id","source_fact_identified","system_classification_identified","reviewer_annotation_boundary_understood",
    "official_provenance_identified","selected_reviewer_action","data_quality_or_semantic_change","guard_required",
    "safe_to_infer_appearance","safe_to_infer_disappearance","safe_to_infer_resolution","safe_to_infer_trip_safety",
    "safe_to_infer_road_passability","safe_to_treat_source_silence_as_safe","acknowledgement_required",
    "shared_event_proves_duplicate_relationships","acknowledgement_means_safe_or_notification_approved","reviewer_note",
}
TOP_FIELDS = {"qualification_version","instrument_digest","reviewer_id","started_at","submitted_at","independence_attestation","cases"}
YES_NO = {"YES", "NO"}
ACTIONS = {"REVIEWED_VALID","REVIEWED_NOT_ACTIONABLE","DATA_QUALITY_HOLD","NEEDS_FOLLOWUP","ACKNOWLEDGED"}
DISTINCTIONS = {"DATA_QUALITY","SEMANTIC_CHANGE","NEITHER","UNKNOWN"}

def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return parsed

def blank_response(reviewer_id: str) -> dict[str, Any]:
    instrument = load(INSTRUMENT)
    blank = {field: None for field in CASE_FIELDS if field != "case_id"}
    return {"qualification_version": instrument["qualification_version"], "instrument_digest": sha256(INSTRUMENT),
            "reviewer_id": reviewer_id, "started_at": None, "submitted_at": None,
            "independence_attestation": None,
            "cases": [dict(blank, case_id=case_id) for case_id in instrument["case_order"]]}

def render_packet() -> str:
    instrument = load(INSTRUMENT)
    lines = ["CAMPREADY PHASE 5-4 QUALIFICATION — BLINDED REVIEWER PACKET", "",
      "This tests the CampReady review contract, not real-world trip safety.",
      "Complete independently as exactly one blinded reviewer (R-A or R-B). Do not use an LLM as a substitute.",
      "Do not view answer-key or scoring materials, the other reviewer's response, or adjudication before submission.",
      "Record UTC started_at and submitted_at separately. Set independence_attestation=true only if these controls held.",
      "SOURCE FACT is persisted evidence. SYSTEM CLASSIFICATION is system output. Your response is REVIEWER ANNOTATION.",
      "UNKNOWN or DATA_QUALITY is not semantic change. ACKNOWLEDGED is not safety or notification approval.",
      "Allowed actions: " + ", ".join(instrument["workflow_actions"]), ""]
    for case in instrument["cases"]:
        lines += [f"===== {case['case_id']} — {case['task_class']} =====", "CASE EVIDENCE",
                  json.dumps(case["evidence"], ensure_ascii=False, sort_keys=True, indent=2), "SOURCE FACT",
                  json.dumps(case["source_fact"], ensure_ascii=False, sort_keys=True, indent=2), "SYSTEM CLASSIFICATION",
                  json.dumps(case["system_classification"], ensure_ascii=False, sort_keys=True, indent=2),
                  "BLANK REVIEWER RESPONSE", json.dumps(blank_response("R-A")["cases"][instrument["case_order"].index(case["case_id"])], sort_keys=True, indent=2), ""]
    lines += ["Submit a completed JSON response conforming to response.schema.json.",
              "Reviewer IDs are local blinded identifiers; include no personal or consumer data.", ""]
    return "\n".join(lines)

def validate_response(response: Mapping[str, Any]) -> None:
    instrument = load(INSTRUMENT)
    if set(response) != TOP_FIELDS: raise ValueError("RESPONSE_FIELDS_INVALID")
    if response["qualification_version"] != instrument["qualification_version"]: raise ValueError("QUALIFICATION_VERSION_INVALID")
    if response["instrument_digest"] != sha256(INSTRUMENT): raise ValueError("INSTRUMENT_DIGEST_INVALID")
    if response["reviewer_id"] not in {"R-A", "R-B"}: raise ValueError("REVIEWER_ID_INVALID")
    if response["independence_attestation"] is not True: raise ValueError("INDEPENDENCE_ATTESTATION_REQUIRED")
    if _utc(response["submitted_at"]) < _utc(response["started_at"]): raise ValueError("TIMESTAMP_ORDER_INVALID")
    cases = response["cases"]
    if not isinstance(cases, list) or [x.get("case_id") for x in cases] != instrument["case_order"]: raise ValueError("CASE_ORDER_INVALID")
    for row in cases:
        if set(row) != CASE_FIELDS: raise ValueError("CASE_FIELDS_INVALID")
        if not all(isinstance(row[f], bool) for f in ("source_fact_identified","system_classification_identified","reviewer_annotation_boundary_understood","official_provenance_identified")): raise ValueError("BOOLEAN_FIELD_INVALID")
        if row["selected_reviewer_action"] not in ACTIONS or row["data_quality_or_semantic_change"] not in DISTINCTIONS: raise ValueError("ENUM_INVALID")
        if any(row[f] not in YES_NO for f in CASE_FIELDS if f.startswith("safe_to_") or f in {"guard_required","acknowledgement_required","shared_event_proves_duplicate_relationships","acknowledgement_means_safe_or_notification_approved"}): raise ValueError("YES_NO_INVALID")
        if row["reviewer_note"] is not None and (not isinstance(row["reviewer_note"], str) or len(row["reviewer_note"]) > 2000): raise ValueError("NOTE_INVALID")
        if row["selected_reviewer_action"] in {"REVIEWED_NOT_ACTIONABLE","DATA_QUALITY_HOLD","NEEDS_FOLLOWUP"} and not (isinstance(row["reviewer_note"], str) and row["reviewer_note"].strip()): raise ValueError("WORKFLOW_NOTE_REQUIRED")

def score_response(response: Mapping[str, Any]) -> dict[str, Any]:
    validate_response(response)
    rubric = load(EXPECTED)
    universal = rubric["universal_safety_critical"]
    expected = {row["case_id"]: row for row in rubric["cases"]}
    safety, discretionary = [], []
    for row in response["cases"]:
        case_id, exp = row["case_id"], expected[row["case_id"]]
        checks = dict(universal, data_quality_or_semantic_change=exp["distinction"], guard_required=exp["guard"],
                      safe_to_infer_appearance=exp["appearance"], acknowledgement_required=exp["ack"],
                      shared_event_proves_duplicate_relationships=exp["fanout_duplicates"])
        for field, wanted in checks.items():
            if row[field] != wanted: safety.append(f"{case_id}:{field}")
        if exp["guard"] == "YES" and row["selected_reviewer_action"] in {"REVIEWED_VALID","REVIEWED_NOT_ACTIONABLE"}:
            safety.append(f"{case_id}:GUARDED_DIRECT_DISPOSITION")
        if row["selected_reviewer_action"] not in exp["acceptable_actions"]:
            discretionary.append(f"{case_id}:selected_reviewer_action")
    return {"reviewer_id":response["reviewer_id"], "contract_pass":not safety,
            "safety_critical_violations":sorted(set(safety)), "discretionary_disagreements":discretionary,
            "duration_seconds":int((_utc(response["submitted_at"])-_utc(response["started_at"])).total_seconds())}

def evaluate_two(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(responses) != 2: raise ValueError("BOTH_IMMUTABLE_SUBMISSIONS_REQUIRED")
    snapshots = copy.deepcopy(list(responses))
    for response in snapshots: validate_response(response)
    if snapshots[0]["reviewer_id"] == snapshots[1]["reviewer_id"]: raise ValueError("REVIEWER_IDS_MUST_DIFFER")
    canonical = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version":"campready.phase5.qualification_evaluation.v1",
            "submission_digests":["sha256:"+canonical(x) for x in snapshots],
            "reviewers":[score_response(x) for x in snapshots],
            "contract_pass":all(score_response(x)["contract_pass"] for x in snapshots)}

def begin_adjudication(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evaluation = evaluate_two(responses)
    by_id = {r["reviewer_id"]:r for r in responses}
    return {"schema_version":"campready.phase5.qualification_adjudication.v1", "submission_digests":evaluation["submission_digests"],
            "cases":[{"case_id":a["case_id"],"reviewer_A_result":copy.deepcopy(a),"reviewer_B_result":copy.deepcopy(b),
                      "agreement":a==b,"safety_critical_disagreement":None,"adjudicated_interpretation":None,"rationale":None}
                     for a,b in zip(by_id["R-A"]["cases"],by_id["R-B"]["cases"])]}

def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--render-packet",type=Path); parser.add_argument("--blank-template",type=Path); parser.add_argument("--reviewer-id",choices=("R-A","R-B"),default="R-A"); parser.add_argument("responses",nargs="*"); args=parser.parse_args()
    if args.render_packet: args.render_packet.write_text(render_packet(),encoding="utf-8")
    if args.blank_template: args.blank_template.write_text(json.dumps(blank_response(args.reviewer_id),indent=2)+"\n",encoding="utf-8")
    if args.responses:
        loaded=[load(Path(p)) for p in args.responses]; print(json.dumps(evaluate_two(loaded) if len(loaded)==2 else score_response(loaded[0]),indent=2,sort_keys=True))

if __name__ == "__main__": main()
