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
import uuid
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
PROTOCOL_DIGEST = "cd5aa50e11935a12bb60d85fd16ada2649bd01baf4cf8ae7593963e0348b9cd7"
COHORT_DIGEST = "fd4ad72f0bf59baafc7575274849bc522d5c3cef47722bb843907f4d441f3ea2"
CONTROL_RULE = "INTAKE_SEQUENCE_MOD_5_MAX_20"
STOP_HOLD_RULE = "PHASE5_5B_FROZEN_STOP_HOLD_V1"
HISTORY = (
    "Phase 5-4 did not pass its original two-human qualification gate.",
    "Phase 5-5 proceeded under explicit owner governance override.",
)


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

    def __init__(self, path: Path, identity_field: str, validator: Callable[[Mapping[str, Any]], None],
                 before_append: Callable[[], None] | None = None):
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self.identity_field = identity_field
        self.validator = validator
        self.before_append = before_append

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
            if self.before_append is not None:
                self.before_append()
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


def _digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("START_ARTIFACT_MALFORMED") from exc
    if not isinstance(value, dict):
        raise ValueError("START_ARTIFACT_MALFORMED")
    return value


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: Mapping[str, Any], *, temp_name: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(temp_name or (path.name + ".tmp"))
    payload = canonical_bytes(dict(value)) + b"\n"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError("short write")
        os.fsync(fd)
    finally:
        os.close(fd)
    if temp_name is None:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    return temporary


def _prelaunch_state_valid(state: Mapping[str, Any]) -> bool:
    return state == {
        "schema_version": "campready.phase5.pilot_state.v1",
        "state": "NOT_STARTED", "pilot_started_at": None,
        "protocol_digest": None, "cohort_digest": None,
        "role_binding_digest": None, "valid_observation_count": 0,
        "control_sampling_rule_id": CONTROL_RULE, "stop_rule_id": STOP_HOLD_RULE,
        "history": list(HISTORY),
    }


START_RECORD_FIELDS = {
    "schema_version", "start_transaction_id", "pilot_started_at",
    "protocol_digest", "cohort_digest", "role_binding_digest",
    "control_sampling_rule", "stop_hold_rule",
    "initial_valid_observation_count", "history", "created_at",
}


def validate_start_record(record: Mapping[str, Any]) -> None:
    if set(record) != START_RECORD_FIELDS or record.get("schema_version") != "campready.phase5.pilot_start.v1":
        raise ValueError("START_RECORD_INVALID")
    if not re.fullmatch(r"cr5start1-[0-9a-f]{64}", str(record.get("start_transaction_id", ""))):
        raise ValueError("START_RECORD_INVALID")
    parse_utc(record.get("pilot_started_at")); parse_utc(record.get("created_at"))
    if record.get("created_at") != record.get("pilot_started_at"):
        raise ValueError("START_RECORD_INVALID")
    if record.get("protocol_digest") != PROTOCOL_DIGEST or record.get("cohort_digest") != COHORT_DIGEST:
        raise ValueError("START_RECORD_INVALID")
    if not _digest(record.get("role_binding_digest")):
        raise ValueError("START_RECORD_INVALID")
    if record.get("control_sampling_rule") != CONTROL_RULE or record.get("stop_hold_rule") != STOP_HOLD_RULE:
        raise ValueError("START_RECORD_INVALID")
    if record.get("initial_valid_observation_count") != 0 or record.get("history") != list(HISTORY):
        raise ValueError("START_RECORD_INVALID")


def _active_state_matches(state: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    expected = {
        "schema_version": "campready.phase5.pilot_state.v1", "state": "ACTIVE",
        "pilot_started_at": record["pilot_started_at"],
        "protocol_digest": record["protocol_digest"], "cohort_digest": record["cohort_digest"],
        "role_binding_digest": record["role_binding_digest"], "valid_observation_count": 0,
        "control_sampling_rule_id": record["control_sampling_rule"],
        "stop_rule_id": record["stop_hold_rule"], "history": record["history"],
        "start_transaction_id": record["start_transaction_id"],
    }
    return state == expected


def _start_paths(root: Path) -> tuple[Path, Path, Path, Path]:
    state_dir = root / "state"
    return (state_dir / "pilot_state.json", state_dir / "start_events.jsonl",
            state_dir / "start_transaction.json", state_dir / "pilot_start.lock")


def _read_start_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n") or not line.strip():
                    raise ValueError("START_RECORD_INVALID")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("START_RECORD_INVALID")
                validate_start_record(row); rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("START_RECORD_INVALID") from exc
    if len(rows) > 1:
        raise ValueError("START_RECORD_INVALID")
    return rows


def _marker_record(marker: Mapping[str, Any]) -> dict[str, Any]:
    if set(marker) != {"schema_version", "status", "start_transaction_id", "record"}:
        raise ValueError("START_MARKER_INVALID")
    if marker.get("schema_version") != "campready.phase5.pilot_start_transaction.v1" or marker.get("status") not in {"PREPARED", "COMMITTED"}:
        raise ValueError("START_MARKER_INVALID")
    record = marker.get("record")
    if not isinstance(record, dict):
        raise ValueError("START_MARKER_INVALID")
    validate_start_record(record)
    if marker.get("start_transaction_id") != record["start_transaction_id"]:
        raise ValueError("START_MARKER_INVALID")
    return record


def validate_active_start(root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False) -> str:
    """Classify persisted start artifacts without changing them."""
    resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
    state_path, events_path, marker_path, _ = _start_paths(resolved)
    try:
        state = _read_json(state_path)
        records = _read_start_records(events_path)
        marker = _read_json(marker_path) if marker_path.exists() else None
        marker_record = _marker_record(marker) if marker is not None else None
        if marker is None and not records and _prelaunch_state_valid(state):
            return "VALID_NOT_STARTED"
        if marker_record is None:
            return "INVALID_START_STATE"
        matching_record = len(records) == 1 and records[0] == marker_record
        if marker["status"] == "COMMITTED" and matching_record and _active_state_matches(state, marker_record):
            return "VALID_ACTIVE_START"
        recoverable_state = _prelaunch_state_valid(state) or _active_state_matches(state, marker_record)
        if recoverable_state and ((not records and marker["status"] == "PREPARED") or matching_record):
            return "INCOMPLETE_START_RECOVERABLE"
    except (KeyError, TypeError, ValueError):
        pass
    return "INVALID_START_STATE"


def _append_start_record(path: Path, record: Mapping[str, Any]) -> None:
    validate_start_record(record)
    if _read_start_records(path):
        raise ValueError("PILOT_ALREADY_STARTED")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(dict(record)) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError("short append")
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _recover_locked(root: Path) -> str:
    classification = validate_active_start(root, allow_test_root=True)
    if classification != "INCOMPLETE_START_RECOVERABLE":
        return classification
    state_path, events_path, marker_path, _ = _start_paths(root)
    marker = _read_json(marker_path); record = _marker_record(marker)
    records = _read_start_records(events_path)
    prepared_state = state_path.with_name("pilot_state.start.tmp")
    if not records:
        marker_path.unlink()
        if prepared_state.exists(): prepared_state.unlink()
        _fsync_directory(marker_path.parent)
        return "VALID_NOT_STARTED"
    active = {
        "schema_version":"campready.phase5.pilot_state.v1", "state":"ACTIVE",
        "pilot_started_at":record["pilot_started_at"], "protocol_digest":record["protocol_digest"],
        "cohort_digest":record["cohort_digest"], "role_binding_digest":record["role_binding_digest"],
        "valid_observation_count":0, "control_sampling_rule_id":record["control_sampling_rule"],
        "stop_rule_id":record["stop_hold_rule"], "history":record["history"],
        "start_transaction_id":record["start_transaction_id"],
    }
    _atomic_json(state_path, active)
    committed = dict(marker); committed["status"] = "COMMITTED"
    _atomic_json(marker_path, committed)
    if prepared_state.exists(): prepared_state.unlink()
    return validate_active_start(root, allow_test_root=True)


def recover_start(root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False) -> str:
    resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
    _, _, _, lock_path = _start_paths(resolved)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _recover_locked(resolved)


def start_pilot(root: str | Path = EVIDENCE_ROOT, *, owner_authorized: bool,
                verified_role_binding_digest: str, allow_test_root: bool = False,
                clock: Callable[[], datetime] | None = None,
                _failure_point: str | None = None) -> dict[str, Any]:
    """Perform the sole NOT_STARTED -> ACTIVE transaction; it creates no observation."""
    resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
    if owner_authorized is not True:
        raise ValueError("EXPLICIT_OWNER_AUTHORIZATION_REQUIRED")
    if not _digest(verified_role_binding_digest):
        raise ValueError("START_DIGEST_INVALID")
    if clock is not None and not allow_test_root:
        raise ValueError("TEST_CLOCK_FORBIDDEN")
    if _failure_point is not None and not allow_test_root:
        raise ValueError("TEST_FAILURE_INJECTION_FORBIDDEN")
    state_path, events_path, marker_path, lock_path = _start_paths(resolved)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recovered = _recover_locked(resolved)
        if recovered != "VALID_NOT_STARTED":
            raise ValueError("PILOT_ALREADY_STARTED" if recovered == "VALID_ACTIVE_START" else "START_STATE_INVALID")
        state = _read_json(state_path)
        if not _prelaunch_state_valid(state):
            raise ValueError("PRELAUNCH_STATE_INVALID")
        if _failure_point == "before_prepare": raise RuntimeError("SIMULATED_INTERRUPTION")
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        if not isinstance(now, datetime) or now.utcoffset() != timezone.utc.utcoffset(now):
            raise ValueError("UTC_TIMESTAMP_REQUIRED")
        timestamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
        transaction_id = "cr5start1-" + uuid.uuid4().hex + uuid.uuid4().hex
        record = {
            "schema_version":"campready.phase5.pilot_start.v1", "start_transaction_id":transaction_id,
            "pilot_started_at":timestamp, "protocol_digest":PROTOCOL_DIGEST, "cohort_digest":COHORT_DIGEST,
            "role_binding_digest":verified_role_binding_digest, "control_sampling_rule":CONTROL_RULE,
            "stop_hold_rule":STOP_HOLD_RULE, "initial_valid_observation_count":0,
            "history":list(HISTORY), "created_at":timestamp,
        }
        marker = {"schema_version":"campready.phase5.pilot_start_transaction.v1", "status":"PREPARED",
                  "start_transaction_id":transaction_id, "record":record}
        _atomic_json(marker_path, marker)
        if _failure_point == "after_prepare": raise RuntimeError("SIMULATED_INTERRUPTION")
        active = {
            "schema_version":"campready.phase5.pilot_state.v1", "state":"ACTIVE", "pilot_started_at":timestamp,
            "protocol_digest":PROTOCOL_DIGEST, "cohort_digest":COHORT_DIGEST,
            "role_binding_digest":verified_role_binding_digest, "valid_observation_count":0,
            "control_sampling_rule_id":CONTROL_RULE, "stop_rule_id":STOP_HOLD_RULE,
            "history":list(HISTORY), "start_transaction_id":transaction_id,
        }
        temporary = _atomic_json(state_path, active, temp_name="pilot_state.start.tmp")
        if _failure_point in {"after_state_temp", "before_record"}: raise RuntimeError("SIMULATED_INTERRUPTION")
        _append_start_record(events_path, record)
        if _failure_point in {"after_record", "before_activation"}: raise RuntimeError("SIMULATED_INTERRUPTION")
        os.replace(temporary, state_path); _fsync_directory(state_path.parent)
        if _failure_point == "after_activation": raise RuntimeError("SIMULATED_INTERRUPTION")
        marker["status"] = "COMMITTED"; _atomic_json(marker_path, marker)
        if validate_active_start(resolved, allow_test_root=True) != "VALID_ACTIVE_START":
            raise ValueError("START_COMMIT_VALIDATION_FAILED")
        return copy.deepcopy(active)


def require_valid_active_start(root: str | Path, *, allow_test_root: bool = False) -> None:
    if validate_active_start(root, allow_test_root=allow_test_root) != "VALID_ACTIVE_START":
        raise ValueError("VALID_ACTIVE_START_REQUIRED")


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

    def __init__(self, root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False,
                 bypass_start_guard: bool = False):
        resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
        super().__init__(resolved / "observations" / "intake.jsonl", "intake_id", validate_intake)
        self._start_guard = None if (allow_test_root and bypass_start_guard) else lambda: require_valid_active_start(resolved, allow_test_root=allow_test_root)

    def accept(self, unsequenced: Mapping[str, Any]) -> dict[str, Any]:
        allowed = INTAKE_FIELDS - {"intake_sequence", "control_sample_selected"}
        if set(unsequenced) != allowed:
            raise ValueError("INTAKE_SCHEMA_INVALID")
        candidate = copy.deepcopy(dict(unsequenced))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self._start_guard is not None: self._start_guard()
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
    def __init__(self, root: str | Path = EVIDENCE_ROOT, *, allow_test_root: bool = False,
                 bypass_start_guard: bool = False):
        resolved = validate_evidence_root(root, allow_test_root=allow_test_root)
        guard = None if (allow_test_root and bypass_start_guard) else lambda: require_valid_active_start(resolved, allow_test_root=allow_test_root)
        self.observations = AppendOnlyJsonl(resolved / "observations" / "observations.jsonl", "observation_id", validate_observation, guard)
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
