"""Human-entered, append-only Phase 5 maintenance evidence ledger."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "campready.phase5.maintenance.v1"
FIELDS = {"schema_version", "maintenance_id", "timestamp", "work_class", "category", "reason", "human_minutes", "intervention_required", "source_authority", "outcome", "actor_id", "evidence_ref"}
WORK_CLASSES = {"OPERATIONAL_MAINTENANCE", "DEVELOPMENT_WORK"}
CATEGORIES = {"SCHEDULER_INFRASTRUCTURE", "AUTH_CREDENTIAL", "SOURCE_TRANSPORT", "PARSER", "SEMANTIC_RULE", "CONFIGURATION", "REVIEW_OPERATIONS", "OTHER"}
OUTCOMES = {"RESOLVED", "MITIGATED", "DEFERRED", "NO_ACTION", "OTHER"}
ID_RE = re.compile(r"^cr5mnt1-[0-9a-f]{64}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("UTC_TIMESTAMP_REQUIRED") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return parsed


def maintenance_id(record: Mapping[str, Any]) -> str:
    values = [SCHEMA_VERSION, record["timestamp"], record["work_class"], record["category"],
              record["reason"], record["human_minutes"], record["intervention_required"],
              record["source_authority"], record["outcome"], record["actor_id"],
              record.get("evidence_ref") or ""]
    return "cr5mnt1-" + hashlib.sha256(_canonical_bytes(values)).hexdigest()


def validate_record(record: Mapping[str, Any]) -> None:
    if set(record) != FIELDS or record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    _parse_utc(record.get("timestamp"))
    if not ID_RE.fullmatch(str(record.get("maintenance_id", ""))):
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    if record.get("work_class") not in WORK_CLASSES or record.get("category") not in CATEGORIES or record.get("outcome") not in OUTCOMES:
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    if not isinstance(record.get("reason"), str) or not record["reason"].strip() or len(record["reason"]) > 2000:
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    minutes = record.get("human_minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 0:
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    if not isinstance(record.get("intervention_required"), bool):
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    if not isinstance(record.get("source_authority"), str) or not record["source_authority"].strip() or len(record["source_authority"]) > 128:
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    if not ACTOR_RE.fullmatch(str(record.get("actor_id", ""))):
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    evidence = record.get("evidence_ref")
    if evidence is not None and (not isinstance(evidence, str) or len(evidence) > 1000):
        raise ValueError("MAINTENANCE_SCHEMA_INVALID")
    if record["maintenance_id"] != maintenance_id(record):
        raise ValueError("MAINTENANCE_IDENTITY_INVALID")


class MaintenanceLedger:
    """Process-locked O_APPEND JSONL; existing bytes are never rewritten."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.endswith("\n") or not line.strip():
                        raise ValueError("MALFORMED_MAINTENANCE_LEDGER")
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("MALFORMED_MAINTENANCE_LEDGER")
                    validate_record(value)
                    if value["maintenance_id"] in seen:
                        raise ValueError("MALFORMED_MAINTENANCE_LEDGER")
                    seen[value["maintenance_id"]] = value
                    records.append(value)
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise ValueError("MALFORMED_MAINTENANCE_LEDGER") from exc
        return records

    def read_all(self) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return copy.deepcopy(self._read_unlocked())

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        candidate = copy.deepcopy(dict(record))
        validate_record(candidate)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self._read_unlocked()
            for existing in records:
                if existing["maintenance_id"] == candidate["maintenance_id"]:
                    if existing == candidate:
                        return copy.deepcopy(existing)
                    raise ValueError("MAINTENANCE_ID_CONFLICT")
            row = _canonical_bytes(candidate) + b"\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                if os.write(fd, row) != len(row):
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


def maintenance_report(records: list[Mapping[str, Any]], period_start: str, period_end: str,
                       evidence_complete: bool = False) -> dict[str, Any]:
    start, end = _parse_utc(period_start), _parse_utc(period_end)
    if end <= start:
        raise ValueError("INVALID_MEASUREMENT_PERIOD")
    selected = []
    for record in records:
        validate_record(record)
        if start <= _parse_utc(record["timestamp"]) < end:
            selected.append(record)
    by_category = {name: 0 for name in sorted(CATEGORIES)}
    for record in selected:
        by_category[record["category"]] += record["human_minutes"]
    status = "UNKNOWN" if not selected else ("COMPLETE" if evidence_complete else "PARTIAL")
    return {"operational_minutes": sum(r["human_minutes"] for r in selected if r["work_class"] == "OPERATIONAL_MAINTENANCE"),
            "development_minutes": sum(r["human_minutes"] for r in selected if r["work_class"] == "DEVELOPMENT_WORK"),
            "record_count": len(selected), "by_category": by_category, "measurement_status": status}
