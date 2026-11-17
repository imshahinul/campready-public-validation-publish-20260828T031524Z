"""Append-only custody primitives for the bounded Phase 5 pilot.

This module has no product-runtime, network, notification, or delivery imports.
Live writes are restricted to the owner-controlled external evidence root.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


EVIDENCE_ROOT = Path("/Users/smshahinulislam/Desktop/campReady/audit/phase5_5_pilot")
LIVE_CLASSES = (
    "state", "observations", "secondary_reviews", "adjudications",
    "corrections", "maintenance", "metrics", "stop_events",
)
ALL_CLASSES = LIVE_CLASSES + ("dry_run",)
OBSERVATION_ID_RE = re.compile(r"^cr5pilotobs1-[0-9a-f]{64}$")
REVIEW_ITEM_ID_RE = re.compile(r"^cr5ri1-[0-9a-f]{64}$")
UTC_RE = re.compile(r"(?:Z|[+]00:00)$")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not UTC_RE.search(value):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("UTC_TIMESTAMP_REQUIRED") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return parsed


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


class AppendOnlyJsonl:
    """Fail-closed, process-locked, durable JSONL with unique identities."""

    def __init__(self, path: Path, identity_field: str, validator: Callable[[Mapping[str, Any]], None]):
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self.identity_field = identity_field
        self.validator = validator

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.endswith("\n") or not line.strip():
                        raise ValueError("MALFORMED_APPEND_ONLY_STORE")
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("MALFORMED_APPEND_ONLY_STORE")
                    self.validator(row)
                    identity = row[self.identity_field]
                    if identity in seen:
                        raise ValueError("MALFORMED_APPEND_ONLY_STORE")
                    seen.add(identity)
                    result.append(row)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("MALFORMED_APPEND_ONLY_STORE") from exc
        return result

    def read_all(self) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return copy.deepcopy(self._read_unlocked())

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        candidate = copy.deepcopy(dict(record))
        self.validator(candidate)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self._read_unlocked()
            if any(row[self.identity_field] == candidate[self.identity_field] for row in records):
                raise ValueError("DUPLICATE_ID_REJECTED")
            payload = canonical_bytes(candidate) + b"\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                if os.write(fd, payload) != len(payload):
                    raise OSError("short append")
                os.fsync(fd)
            finally:
                os.close(fd)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return copy.deepcopy(candidate)


def validate_evidence_root(root: str | Path, *, allow_test_root: bool = False) -> Path:
    resolved = Path(root).resolve()
    if not allow_test_root and resolved != EVIDENCE_ROOT.resolve():
        raise ValueError("UNAUTHORIZED_EVIDENCE_ROOT")
    return resolved


def initialize_evidence_root(root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False) -> Path:
    resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
    for name in ALL_CLASSES:
        (resolved / name).mkdir(parents=True, exist_ok=True)
    return resolved


INTAKE_FIELDS = {
    "schema_version", "intake_id", "observation_id", "review_item_id",
    "source_evidence_ref", "accepted_at", "intake_sequence",
    "control_sample_selected", "dry_run", "pilot_observation",
}


def validate_intake(record: Mapping[str, Any]) -> None:
    if set(record) != INTAKE_FIELDS or record.get("schema_version") != "campready.phase5.pilot_intake.v1":
        raise ValueError("INTAKE_SCHEMA_INVALID")
    if not re.fullmatch(r"cr5intake1-[0-9a-f]{64}", str(record.get("intake_id", ""))):
        raise ValueError("INTAKE_SCHEMA_INVALID")
    if not OBSERVATION_ID_RE.fullmatch(str(record.get("observation_id", ""))) or not REVIEW_ITEM_ID_RE.fullmatch(str(record.get("review_item_id", ""))):
        raise ValueError("INTAKE_SCHEMA_INVALID")
    if not _nonempty(record.get("source_evidence_ref")):
        raise ValueError("INTAKE_SCHEMA_INVALID")
    parse_utc(record.get("accepted_at"))
    sequence = record.get("intake_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or not 1 <= sequence <= 100:
        raise ValueError("INTAKE_SCHEMA_INVALID")
    if record.get("control_sample_selected") != (sequence % 5 == 0):
        raise ValueError("CONTROL_SAMPLE_INVALID")
    if record.get("dry_run") is not False or record.get("pilot_observation") is not True:
        raise ValueError("LIVE_CUSTODY_INVALID")


class IntakeStore(AppendOnlyJsonl):
    """Assigns the next sequence only after structural validation, under one lock."""

    def __init__(self, root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False):
        resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
        super().__init__(resolved / "observations" / "intake.jsonl", "intake_id", validate_intake)

    def accept(self, unsequenced: Mapping[str, Any]) -> dict[str, Any]:
        allowed = INTAKE_FIELDS - {"intake_sequence", "control_sample_selected"}
        if set(unsequenced) != allowed:
            raise ValueError("INTAKE_SCHEMA_INVALID")
        candidate = copy.deepcopy(dict(unsequenced))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self._read_unlocked()
            sequence = len(records) + 1
            candidate["intake_sequence"] = sequence
            candidate["control_sample_selected"] = sequence % 5 == 0
            validate_intake(candidate)
            if any(row["intake_id"] == candidate["intake_id"] or row["observation_id"] == candidate["observation_id"] for row in records):
                raise ValueError("DUPLICATE_ID_REJECTED")
            payload = canonical_bytes(candidate) + b"\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                if os.write(fd, payload) != len(payload):
                    raise OSError("short append")
                os.fsync(fd)
            finally:
                os.close(fd)
        return copy.deepcopy(candidate)


OBS_REQUIRED = {
    "schema_version","observation_id","intake_sequence","review_item_id","site_id","campground_name","affected_relationship_id","source_role","source_authority","source_evidence_ref","event_category","system_classification","relevance_status","quality_defect_basis","data_quality_state","guard_required","retrieval_first_attempt_outcome","retrieval_eventual_outcome","parser_status","rescued_retry","reviewer_disposition","reviewer_code","review_started_at","review_completed_at","acknowledgement_required","acknowledgement_completed","correction_required","correction_reason","correction_before_downstream_transition","corrected_item_unresolved","adjudication_required","adjudication_completed","confirmed_structured_disposition_incompatibility","comprehension_error_classes","control_sample_selected","secondary_reviewer_code","secondary_disposition","disagreement_type","maintenance_minutes_attributable","review_minutes","adjudication_minutes","correction_minutes","notes_or_rationale","unsafe_inference","protocol_integrity_status","evidence_refs","unknown_none_dq_hold_conflict",
}
DISPOSITIONS = {"REVIEWED_VALID","REVIEWED_NOT_ACTIONABLE","DATA_QUALITY_HOLD","NEEDS_FOLLOWUP","ACKNOWLEDGED"}
ERROR_CLASSES = {"UNKNOWN_DATA_QUALITY_CONFUSION","GUARD_COMPREHENSION_ERROR","DISPOSITION_ACTION_CONFLICT","ACKNOWLEDGEMENT_WORKFLOW_ERROR","PROVENANCE_COMPREHENSION_ERROR","SOURCE_SYSTEM_REVIEW_BOUNDARY_ERROR","UNSAFE_INFERENCE","OTHER_REVIEWER_WORKFLOW_ERROR"}
DISAGREEMENT_CLASSES = {"ALLOWED_DISCRETIONARY_DIFFERENCE","CONTRACT_CRITICAL_DISAGREEMENT","SAFETY_CRITICAL_DISAGREEMENT"}


def validate_observation(record: Mapping[str, Any]) -> None:
    if set(record) != OBS_REQUIRED or record.get("schema_version") != "campready.phase5.pilot_observation.v1":
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if not OBSERVATION_ID_RE.fullmatch(str(record.get("observation_id", ""))) or not REVIEW_ITEM_ID_RE.fullmatch(str(record.get("review_item_id", ""))):
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    seq = record.get("intake_sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or not 1 <= seq <= 100:
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    enums = {
        "site_id":{"andrews-cove","low-gap","nc-van-hook-glade","wv-big-rock","or-spinreel","or-cold-springs","ncsp-carolina-beach","ncsp-stone-mountain","nps-elkmont","nps-big-meadows"},
        "source_role":{"ACTIVE_ALERT","CAMPGROUND_PAGE","ALERT_DETAIL"},
        "event_category":{"WEATHER_ALERT","CAMPGROUND_CONDITION",None},
        "relevance_status":{"RELEVANT","NOT_RELEVANT","UNKNOWN","NOT_EVALUATED"},
        "quality_defect_basis":{"NONE","RETRIEVAL","PARSER","PROVENANCE","EVIDENCE_INTEGRITY","OTHER_STRUCTURED_DEFECT"},
        "data_quality_state":{"NONE","DATA_QUALITY"},
        "retrieval_first_attempt_outcome":{"SUCCESS","FAILED","NOT_APPLICABLE"},
        "retrieval_eventual_outcome":{"SUCCESS","FINAL_FAILED","NOT_APPLICABLE"},
        "parser_status":{"SUCCESS","FAILED","NOT_RUN","NOT_APPLICABLE"},
        "reviewer_disposition":DISPOSITIONS,
        "protocol_integrity_status":{"VALID","MATERIALLY_CORRUPTED"},
    }
    if any(record.get(field) not in values for field, values in enums.items()):
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    for field in ("campground_name","affected_relationship_id","source_authority","source_evidence_ref","system_classification"):
        if not _nonempty(record.get(field)):
            raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if record.get("reviewer_code") != "PRIMARY_REVIEWER":
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    parse_utc(record.get("review_started_at")); parse_utc(record.get("review_completed_at"))
    bool_fields = ("guard_required","rescued_retry","acknowledgement_required","acknowledgement_completed","correction_required","adjudication_required","adjudication_completed","confirmed_structured_disposition_incompatibility","control_sample_selected","unsafe_inference","unknown_none_dq_hold_conflict")
    if any(not isinstance(record.get(field), bool) for field in bool_fields):
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    expected_y507 = record["relevance_status"] == "UNKNOWN" and record["quality_defect_basis"] == "NONE" and record["reviewer_disposition"] == "DATA_QUALITY_HOLD"
    if record["unknown_none_dq_hold_conflict"] != expected_y507 or record["control_sample_selected"] != (seq % 5 == 0):
        raise ValueError("OBSERVATION_DERIVATION_INVALID")
    classes = record.get("comprehension_error_classes")
    if not isinstance(classes, list) or len(classes) != len(set(classes)) or not set(classes) <= ERROR_CLASSES:
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if record["confirmed_structured_disposition_incompatibility"] and (not classes or not record["adjudication_required"] or not record["adjudication_completed"]):
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if record["unsafe_inference"] and "UNSAFE_INFERENCE" not in classes:
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    selected = record["control_sample_selected"]
    if selected:
        if record.get("secondary_reviewer_code") != "SECOND_REVIEWER" or record.get("secondary_disposition") not in DISPOSITIONS or not record["adjudication_required"]:
            raise ValueError("OBSERVATION_SCHEMA_INVALID")
    elif record.get("secondary_reviewer_code") is not None or record.get("secondary_disposition") is not None or record.get("disagreement_type") is not None:
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if record.get("disagreement_type") not in DISAGREEMENT_CLASSES | {None}:
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if not isinstance(record.get("evidence_refs"), list) or not record["evidence_refs"] or not all(_nonempty(x) for x in record["evidence_refs"]):
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    for field in ("maintenance_minutes_attributable","review_minutes","adjudication_minutes","correction_minutes"):
        value = record.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if record["correction_required"]:
        if not _nonempty(record.get("correction_reason")) or not isinstance(record.get("correction_before_downstream_transition"), bool) or not isinstance(record.get("corrected_item_unresolved"), bool):
            raise ValueError("OBSERVATION_SCHEMA_INVALID")


def _validate_secondary(record: Mapping[str, Any]) -> None:
    fields = {"schema_version","secondary_review_id","observation_id","review_item_id","reviewer_code","reviewer_disposition","reviewed_at","rationale","source_evidence_refs"}
    if set(record) != fields or record.get("schema_version") != "campready.phase5.pilot_secondary_review.v1" or record.get("reviewer_code") != "SECOND_REVIEWER":
        raise ValueError("SECONDARY_REVIEW_SCHEMA_INVALID")
    if not re.fullmatch(r"cr5secondary1-[0-9a-f]{64}", str(record.get("secondary_review_id", ""))) or not OBSERVATION_ID_RE.fullmatch(str(record.get("observation_id", ""))) or not REVIEW_ITEM_ID_RE.fullmatch(str(record.get("review_item_id", ""))):
        raise ValueError("SECONDARY_REVIEW_SCHEMA_INVALID")
    if record.get("reviewer_disposition") not in DISPOSITIONS or not _nonempty(record.get("rationale")) or not isinstance(record.get("source_evidence_refs"), list) or not record["source_evidence_refs"]:
        raise ValueError("SECONDARY_REVIEW_SCHEMA_INVALID")
    parse_utc(record.get("reviewed_at"))


def _validate_adjudication(record: Mapping[str, Any]) -> None:
    fields = {"schema_version","adjudication_id","observation_id","primary_original_response","secondary_original_response","difference_classification","confirmed_error_status","error_class","correction_requirement","adjudicator_code","adjudicated_at","rationale"}
    if set(record) != fields or record.get("schema_version") != "campready.phase5.pilot_adjudication.v1":
        raise ValueError("ADJUDICATION_SCHEMA_INVALID")
    if not re.fullmatch(r"cr5adjudication1-[0-9a-f]{64}", str(record.get("adjudication_id", ""))) or not OBSERVATION_ID_RE.fullmatch(str(record.get("observation_id", ""))):
        raise ValueError("ADJUDICATION_SCHEMA_INVALID")
    if record.get("difference_classification") not in DISAGREEMENT_CLASSES or not isinstance(record.get("confirmed_error_status"), bool) or not isinstance(record.get("correction_requirement"), bool):
        raise ValueError("ADJUDICATION_SCHEMA_INVALID")
    if record.get("error_class") not in ERROR_CLASSES | {None} or not _nonempty(record.get("adjudicator_code")) or not _nonempty(record.get("rationale")):
        raise ValueError("ADJUDICATION_SCHEMA_INVALID")
    if not isinstance(record.get("primary_original_response"), dict) or not isinstance(record.get("secondary_original_response"), dict):
        raise ValueError("ADJUDICATION_SCHEMA_INVALID")
    parse_utc(record.get("adjudicated_at"))


def _validate_correction(record: Mapping[str, Any]) -> None:
    fields = {"schema_version","correction_id","original_observation_id","correction_required","reason","corrected_disposition","corrected_by","corrected_at","before_downstream_transition","item_remains_unresolved"}
    if set(record) != fields or record.get("schema_version") != "campready.phase5.pilot_correction.v1":
        raise ValueError("CORRECTION_SCHEMA_INVALID")
    if not re.fullmatch(r"cr5correction1-[0-9a-f]{64}", str(record.get("correction_id", ""))) or not OBSERVATION_ID_RE.fullmatch(str(record.get("original_observation_id", ""))):
        raise ValueError("CORRECTION_SCHEMA_INVALID")
    if not isinstance(record.get("correction_required"), bool) or not _nonempty(record.get("reason")) or record.get("corrected_disposition") not in DISPOSITIONS | {None}:
        raise ValueError("CORRECTION_SCHEMA_INVALID")
    if not _nonempty(record.get("corrected_by")) or not isinstance(record.get("before_downstream_transition"), bool) or not isinstance(record.get("item_remains_unresolved"), bool):
        raise ValueError("CORRECTION_SCHEMA_INVALID")
    parse_utc(record.get("corrected_at"))


class PilotStores:
    def __init__(self, root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False):
        resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
        self.observations = AppendOnlyJsonl(resolved / "observations" / "observations.jsonl", "observation_id", validate_observation)
        self.secondary_reviews = AppendOnlyJsonl(resolved / "secondary_reviews" / "secondary_reviews.jsonl", "secondary_review_id", _validate_secondary)
        self.adjudications = AppendOnlyJsonl(resolved / "adjudications" / "adjudications.jsonl", "adjudication_id", _validate_adjudication)
        self.corrections = AppendOnlyJsonl(resolved / "corrections" / "corrections.jsonl", "correction_id", _validate_correction)


def live_counts(root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False) -> dict[str, int]:
    resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
    files = {
        "valid_observation_count": resolved / "observations" / "observations.jsonl",
        "secondary_review_count": resolved / "secondary_reviews" / "secondary_reviews.jsonl",
        "adjudication_count": resolved / "adjudications" / "adjudications.jsonl",
        "correction_count": resolved / "corrections" / "corrections.jsonl",
        "stop_event_count": resolved / "stop_events" / "stop_events.jsonl",
    }
    result: dict[str, int] = {}
    for key, path in files.items():
        if not path.exists():
            result[key] = 0
        else:
            with path.open("rb") as stream:
                result[key] = sum(1 for line in stream if line.strip())
    return result
