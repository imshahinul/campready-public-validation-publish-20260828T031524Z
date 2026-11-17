"""Bounded, resumable orchestration for the Phase 5 production pilot.

This module composes the frozen read-model, workflow, annotation, custody,
metric, STOP/HOLD, and window primitives.  It performs no source discovery,
network access, notification, delivery, or product-runtime mutation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from annotation_store import AnnotationStore
from pilot_control import evaluate_stop_hold, evaluate_window
from pilot_metrics import comprehension_metrics, disagreement_metrics, unknown_none_dq_hold_conflict
from pilot_store import (EVIDENCE_ROOT, IntakeStore, PilotStores, canonical_bytes,
                         live_counts, parse_utc, require_valid_active_start,
                         validate_active_start, validate_observation)
from read_model import derive_review_items_for_run, load_phase5_config, validate_review_item_contract
from workflow import apply_reviewer_action


PRIMARY_REVIEWER = "R-AL"
NORMALIZED_EVIDENCE_SCHEMA = "campready.phase5.normalized_run_evidence.v1"
OBSERVATION_AUTHORIZED_AT = "2026-11-17T05:09:06Z"
HISTORICAL_REFS = {
    "campready-phase4b/runs/20261104T024017Z/run-report.json",
    "campready-phase4b/review-queue/20261103T224022Z.json",
}
ALLOWED_SOURCE_ROLES = {"ACTIVE_ALERT", "CAMPGROUND_PAGE", "ALERT_DETAIL"}
ALLOWED_SOURCE_FAMILIES = {"NWS_API", "USFS_HTML", "NC_STATE_PARKS", "NC_STATE_PARKS_HTML", "NPS_API"}
HUMAN_FIELDS = {"reviewer_id", "to_status", "disposition", "reason_code", "note", "timestamp"}


def _digest(prefix: str, values: list[Any]) -> str:
    return prefix + hashlib.sha256(canonical_bytes(values)).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("SOURCE_EVIDENCE_INVALID") from exc


class PilotOperator:
    """Small production operator facade over immutable Phase 5 primitives."""

    def __init__(self, root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False,
                 config_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.allow_test_root = allow_test_root
        self.config = load_phase5_config(config_path)
        self.intakes = IntakeStore(self.root, allow_test_root=allow_test_root)
        self.stores = PilotStores(self.root, allow_test_root=allow_test_root)
        # This is the narrowest existing authorized evidence class. AnnotationStore
        # remains append-only and retains every original human action/version.
        self.annotations = AnnotationStore(self.root / "observations" / "primary_review_annotations.jsonl")

    def _require_active(self) -> dict[str, Any]:
        require_valid_active_start(self.root, allow_test_root=self.allow_test_root)
        return _read_json(self.root / "state" / "pilot_state.json")

    def _intakes(self) -> list[dict[str, Any]]:
        return self.intakes.read_all()

    def _source_ref(self, path: Path, document: Mapping[str, Any]) -> str:
        declared = document.get("source_evidence_ref")
        return declared if isinstance(declared, str) and declared.strip() else str(path.resolve())

    def prepare_review_items(self, source_run_path: str | Path) -> dict[str, Any]:
        """Validate one explicitly supplied prospective artifact, then call the frozen derivation."""
        self._require_active()
        path = Path(source_run_path)
        normalized = path.as_posix()
        if any(normalized.endswith(ref) for ref in HISTORICAL_REFS):
            raise ValueError("HISTORICAL_SOURCE_REJECTED")
        document = _read_json(path)
        if not isinstance(document, dict) or document.get("schema_version") != NORMALIZED_EVIDENCE_SCHEMA:
            raise ValueError("MONITORING_EVIDENCE_FAMILY_INVALID")
        if document.get("dry_run") is not False or document.get("test_fixture") is not False:
            raise ValueError("NON_PRODUCTION_SOURCE_REJECTED")
        records = document.get("records")
        if not isinstance(records, list):
            raise ValueError("SOURCE_EVIDENCE_INVALID")
        state = self._require_active()
        try:
            run_generated = datetime.strptime(str(document.get("run_id")), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError("SOURCE_PROVENANCE_INVALID") from exc
        generated = parse_utc(document["generated_at"]) if document.get("generated_at") is not None else run_generated
        if generated < parse_utc(OBSERVATION_AUTHORIZED_AT) or generated < parse_utc(state["pilot_started_at"]):
            raise ValueError("HISTORICAL_SOURCE_REJECTED")
        cohort = {row["site_id"]: row for row in self.config["cohort"]}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("SOURCE_EVIDENCE_INVALID")
            if record.get("fixture_origin") is not None or record.get("site_id") not in cohort:
                raise ValueError("NON_PRODUCTION_SOURCE_REJECTED")
            if record.get("source_role") not in ALLOWED_SOURCE_ROLES or record.get("source_family") not in ALLOWED_SOURCE_FAMILIES:
                raise ValueError("MONITORING_EVIDENCE_FAMILY_INVALID")
            if record.get("provenance_run_id") != document.get("run_id"):
                raise ValueError("SOURCE_PROVENANCE_INVALID")
            detected = parse_utc(record.get("detected_at"))
            if detected < parse_utc(OBSERVATION_AUTHORIZED_AT) or detected < parse_utc(state["pilot_started_at"]) or detected > generated:
                raise ValueError("SOURCE_PROVENANCE_INVALID")
            ref = str(record.get("provenance_record_ref", "")).split("#", 1)[0]
            if ref in HISTORICAL_REFS:
                raise ValueError("HISTORICAL_SOURCE_REJECTED")
        source_ref = self._source_ref(path, document)
        if any(row["source_evidence_ref"] == source_ref for row in self._intakes()):
            raise ValueError("DUPLICATE_SOURCE_IMPORT_REJECTED")
        items = derive_review_items_for_run(path, self.config)
        for item in items:
            validate_review_item_contract(item)
        if not items:
            return {"result": "NO_REVIEW_ITEMS_FOR_RUN", "source_evidence_ref": source_ref, "review_items": []}
        # Existing derivation preserves source order; expose the complete set and
        # require an explicit ID whenever there is more than one item.
        return {"result": "REVIEW_ITEMS_READY", "source_evidence_ref": source_ref,
                "selection_required": len(items) > 1, "review_items": items}

    def accept_review_item(self, prepared: Mapping[str, Any], review_item_id: str | None = None,
                           *, accepted_at: str) -> dict[str, Any]:
        self._require_active(); parse_utc(accepted_at)
        items = prepared.get("review_items")
        if not isinstance(items, list) or not items:
            raise ValueError("NO_REVIEW_ITEM_SELECTED")
        if len(items) > 1 and review_item_id is None:
            raise ValueError("EXPLICIT_ITEM_SELECTION_REQUIRED")
        selected_id = review_item_id or items[0]["review_item_id"]
        matches = [item for item in items if item.get("review_item_id") == selected_id]
        if len(matches) != 1:
            raise ValueError("REVIEW_ITEM_SELECTION_INVALID")
        item = matches[0]; source_ref = prepared.get("source_evidence_ref")
        intake_id = _digest("cr5intake1-", ["phase5-5i", item["review_item_id"], source_ref])
        observation_id = _digest("cr5pilotobs1-", ["phase5-5i", item["review_item_id"], source_ref])
        for existing in self._intakes():
            if existing["intake_id"] == intake_id and existing["observation_id"] == observation_id:
                return existing
        return self.intakes.accept({
            "schema_version":"campready.phase5.pilot_intake.v1", "intake_id":intake_id,
            "observation_id":observation_id, "review_item_id":item["review_item_id"],
            "source_evidence_ref":source_ref, "accepted_at":accepted_at,
            "dry_run":False, "pilot_observation":True,
        })

    def primary_review_packet(self, intake: Mapping[str, Any], review_item: Mapping[str, Any]) -> dict[str, Any]:
        if intake.get("review_item_id") != review_item.get("review_item_id"):
            raise ValueError("INTAKE_REVIEW_ITEM_MISMATCH")
        return {"schema_version":"campready.phase5.primary_review_packet.v1",
                "intake":dict(intake), "review_item":dict(review_item),
                "required_reviewer":PRIMARY_REVIEWER,
                "secondary_review_required":bool(intake["control_sample_selected"]),
                "reviewer_disposition":None}

    def record_primary_review(self, intake: Mapping[str, Any], review_item: Mapping[str, Any],
                              human_action: Mapping[str, Any]) -> dict[str, Any]:
        self._require_active()
        if set(human_action) != HUMAN_FIELDS:
            raise ValueError("CALLER_SUPPLIED_HUMAN_FIELDS_REQUIRED")
        if human_action.get("reviewer_id") != PRIMARY_REVIEWER:
            raise ValueError("PRIMARY_REVIEWER_REQUIRED")
        if human_action.get("to_status") is None or human_action.get("disposition") is None:
            raise ValueError("CALLER_SUPPLIED_HUMAN_FIELDS_REQUIRED")
        if intake.get("review_item_id") != review_item.get("review_item_id"):
            raise ValueError("INTAKE_REVIEW_ITEM_MISMATCH")
        return apply_reviewer_action(self.annotations, review_item, human_action)

    @staticmethod
    def _quality_basis(item: Mapping[str, Any]) -> str:
        if item["data_quality_state"]["classification"] == "NONE":
            return "NONE"
        reason = str(item["data_quality_state"].get("reason_code") or "")
        if "RETRIEVAL" in reason: return "RETRIEVAL"
        if "PARSER" in reason: return "PARSER"
        if "PROVENANCE" in reason: return "PROVENANCE"
        if "EVIDENCE" in reason or "UNAVAILABLE" in reason: return "EVIDENCE_INTEGRITY"
        return "OTHER_STRUCTURED_DEFECT"

    def assemble_observation(self, intake: Mapping[str, Any], item: Mapping[str, Any],
                             annotation: Mapping[str, Any], *, secondary_review: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if annotation.get("review_item_id") != item.get("review_item_id") or intake.get("review_item_id") != item.get("review_item_id"):
            raise ValueError("OBSERVATION_INPUT_MISMATCH")
        selected = intake["control_sample_selected"]
        if selected and secondary_review is None:
            raise ValueError("SECONDARY_REVIEW_OUTSTANDING")
        retrieval = item["retrieval_status"]
        if secondary_review is not None:
            persisted_secondary = self.stores.secondary_reviews.read_all()
            if not any(row == dict(secondary_review) and row.get("observation_id") == intake["observation_id"]
                       and row.get("review_item_id") == item["review_item_id"] for row in persisted_secondary):
                raise ValueError("PERSISTED_SECONDARY_REVIEW_REQUIRED")
        secondary_disposition = None if secondary_review is None else secondary_review.get("reviewer_disposition")
        observation = {
            "schema_version":"campready.phase5.pilot_observation.v1", "observation_id":intake["observation_id"],
            "intake_sequence":intake["intake_sequence"], "review_item_id":item["review_item_id"], "site_id":item["site_id"],
            "campground_name":item["campground_name"], "affected_relationship_id":item["affected_relationship_id"],
            "source_role":item["source_role"], "source_authority":item["authority_name"],
            "source_evidence_ref":intake["source_evidence_ref"], "event_category":item["event_category"],
            "system_classification":item["lifecycle_classification"] if item["lifecycle_classification"] != "NONE" else "DATA_QUALITY",
            "relevance_status":item["relevance_status"], "quality_defect_basis":self._quality_basis(item),
            "data_quality_state":item["data_quality_state"]["classification"], "guard_required":item["alert_detail_guard_required"],
            "retrieval_first_attempt_outcome":"FAILED" if retrieval != "SUCCESS" else "SUCCESS",
            "retrieval_eventual_outcome":"FINAL_FAILED" if retrieval == "FINAL_FAILED" else "SUCCESS",
            "parser_status":item["parser_status"], "rescued_retry":retrieval == "FAILED_FIRST_ATTEMPT_RESCUED",
            "reviewer_disposition":annotation["disposition"], "reviewer_code":"PRIMARY_REVIEWER",
            "review_started_at":intake["accepted_at"], "review_completed_at":annotation["timestamp"],
            "acknowledgement_required":False, "acknowledgement_completed":annotation["acknowledged"],
            "correction_required":False, "correction_reason":None, "correction_before_downstream_transition":None,
            "corrected_item_unresolved":None, "adjudication_required":selected, "adjudication_completed":False,
            "confirmed_structured_disposition_incompatibility":False, "comprehension_error_classes":[],
            "control_sample_selected":selected, "secondary_reviewer_code":"SECOND_REVIEWER" if selected else None,
            "secondary_disposition":secondary_disposition, "disagreement_type":None,
            "maintenance_minutes_attributable":0,
            "review_minutes":max(0.0, (parse_utc(annotation["timestamp"])-parse_utc(intake["accepted_at"])).total_seconds()/60),
            "adjudication_minutes":0, "correction_minutes":0, "notes_or_rationale":annotation["note"],
            "unsafe_inference":False, "protocol_integrity_status":"VALID",
            "evidence_refs":[intake["source_evidence_ref"], item["provenance_record_ref"], annotation["annotation_id"]],
            "unknown_none_dq_hold_conflict":unknown_none_dq_hold_conflict(item["relevance_status"], self._quality_basis(item), annotation["disposition"]),
        }
        validate_observation(observation)
        schema = _read_json(Path(__file__).with_name("observation.schema.json"))
        if schema.get("additionalProperties") is not False or set(observation) != set(schema.get("properties", {})):
            raise ValueError("OBSERVATION_SCHEMA_INVALID")
        if not set(schema.get("required", ())).issubset(observation):
            raise ValueError("OBSERVATION_SCHEMA_INVALID")
        return observation

    def append_observation(self, observation: Mapping[str, Any], *, evaluated_at: str) -> dict[str, Any]:
        self._require_active(); parse_utc(evaluated_at)
        existing = self.stores.observations.read_all()
        matches = [row for row in existing if row["observation_id"] == observation.get("observation_id")]
        if matches:
            if matches[0] != dict(observation):
                raise ValueError("OBSERVATION_RETRY_CONFLICT")
            appended = matches[0]
        else:
            appended = self.stores.observations.append(observation)
        observations = self.stores.observations.read_all()
        state = self._require_active()
        return {"result":"OBSERVATION_APPENDED" if not matches else "OBSERVATION_ALREADY_APPENDED",
                "observation":appended, "stop_hold":evaluate_stop_hold(observations),
                "window":evaluate_window(len(observations), state["pilot_started_at"], evaluated_at, observations),
                "metrics":{"comprehension":comprehension_metrics(observations),
                           "disagreement":disagreement_metrics(observations)}}

    def status(self, *, evaluated_at: str | None = None) -> dict[str, Any]:
        state = self._require_active()
        now = evaluated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        parse_utc(now)
        intakes = self._intakes(); annotations = self.annotations.read_all(); observations = self.stores.observations.read_all()
        annotation_ids = {row["review_item_id"] for row in annotations}
        observed_ids = {row["observation_id"] for row in observations}
        pending_primary = [row["intake_id"] for row in intakes if row["review_item_id"] not in annotation_ids]
        pending_append = [row["intake_id"] for row in intakes if row["review_item_id"] in annotation_ids and row["observation_id"] not in observed_ids]
        secondary = [row["intake_id"] for row in intakes if row["control_sample_selected"] and row["observation_id"] not in observed_ids]
        return {"pilot_classification":validate_active_start(self.root, allow_test_root=self.allow_test_root),
                "window_evaluation":evaluate_window(len(observations), state["pilot_started_at"], now, observations),
                "next_intake_sequence":len(intakes)+1, "valid_observation_count":len(observations),
                "pending_accepted_intake_awaiting_primary_review":pending_primary,
                "pending_completed_primary_review_awaiting_observation_append":pending_append,
                "pending_secondary_review_obligations":secondary,
                "stop_hold_evaluation":evaluate_stop_hold(observations), "live_counts":live_counts(self.root, allow_test_root=self.allow_test_root)}
