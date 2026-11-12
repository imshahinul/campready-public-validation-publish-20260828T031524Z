"""Evidenced, append-only Phase 5 cost ledger and deterministic reporting."""

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

SCHEMA_VERSION = "campready.phase5.cost.v1"
FIELDS = {"schema_version", "cost_id", "period_start", "period_end", "cost_category", "provider", "description", "quantity", "unit", "unit_cost", "amount", "currency", "evidence_type", "evidence_ref", "measurement_status", "infrastructure_cost", "projection_basis", "monthly_projected_amount"}
CATEGORIES = {"SCHEDULER_SERVICE", "GITHUB_ACTIONS_CI_RUNTIME", "HOSTING_STORAGE", "EXTERNAL_SERVICE", "REVIEWER_OPERATIONS", "OTHER"}
EVIDENCE_TYPES = {"INVOICE", "BILLING_EXPORT", "PUBLISHED_RATE", "INTERNAL_RATE", "NOT_AVAILABLE"}
STATUSES = {"ACTUAL", "ESTIMATED_FROM_DOCUMENTED_RATE", "NOT_MEASURABLE"}
ID_RE = re.compile(r"^cr5cost1-[0-9a-f]{64}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


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


def cost_id(record: Mapping[str, Any]) -> str:
    values = [SCHEMA_VERSION, record["period_start"], record["period_end"], record["cost_category"],
              record["provider"], record["description"], record.get("quantity"), record.get("unit"),
              record.get("unit_cost"), record.get("amount"), record.get("currency"), record["evidence_type"],
              record.get("evidence_ref") or "", record["measurement_status"], record["infrastructure_cost"],
              record.get("projection_basis") or "", record.get("monthly_projected_amount")]
    return "cr5cost1-" + hashlib.sha256(_canonical_bytes(values)).hexdigest()


def validate_record(record: Mapping[str, Any]) -> None:
    if set(record) != FIELDS or record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("COST_SCHEMA_INVALID")
    start, end = _parse_utc(record.get("period_start")), _parse_utc(record.get("period_end"))
    if end <= start or not ID_RE.fullmatch(str(record.get("cost_id", ""))):
        raise ValueError("COST_SCHEMA_INVALID")
    if record.get("cost_category") not in CATEGORIES or record.get("evidence_type") not in EVIDENCE_TYPES or record.get("measurement_status") not in STATUSES:
        raise ValueError("COST_SCHEMA_INVALID")
    for name, limit in (("provider", 128), ("description", 2000)):
        if not isinstance(record.get(name), str) or not record[name].strip() or len(record[name]) > limit:
            raise ValueError("COST_SCHEMA_INVALID")
    for name in ("quantity", "unit_cost", "amount", "monthly_projected_amount"):
        value = record.get(name)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
            raise ValueError("COST_SCHEMA_INVALID")
    for name, limit in (("unit", 64), ("evidence_ref", 1000), ("projection_basis", 1000)):
        value = record.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError("COST_SCHEMA_INVALID")
    currency = record.get("currency")
    if currency is not None and not CURRENCY_RE.fullmatch(str(currency)):
        raise ValueError("COST_SCHEMA_INVALID")
    if (record.get("amount") is not None or record.get("monthly_projected_amount") is not None) and currency is None:
        raise ValueError("COST_SCHEMA_INVALID")
    if not isinstance(record.get("infrastructure_cost"), bool) or (record["cost_category"] == "REVIEWER_OPERATIONS" and record["infrastructure_cost"]):
        raise ValueError("COST_SCHEMA_INVALID")
    if record["measurement_status"] == "NOT_MEASURABLE" and any(record.get(k) is not None for k in ("quantity", "unit_cost", "amount", "monthly_projected_amount")):
        raise ValueError("COST_SCHEMA_INVALID")
    if record["monthly_projected_amount"] is not None and (record["measurement_status"] not in {"ACTUAL", "ESTIMATED_FROM_DOCUMENTED_RATE"} or not record.get("projection_basis")):
        raise ValueError("COST_SCHEMA_INVALID")
    if record["cost_id"] != cost_id(record):
        raise ValueError("COST_IDENTITY_INVALID")


class CostLedger:
    """Process-locked O_APPEND JSONL; existing bytes are never rewritten."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.endswith("\n") or not line.strip():
                        raise ValueError("MALFORMED_COST_LEDGER")
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("MALFORMED_COST_LEDGER")
                    validate_record(value)
                    if value["cost_id"] in seen:
                        raise ValueError("MALFORMED_COST_LEDGER")
                    seen.add(value["cost_id"])
                    records.append(value)
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise ValueError("MALFORMED_COST_LEDGER") from exc
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
                if existing["cost_id"] == candidate["cost_id"]:
                    if existing == candidate:
                        return copy.deepcopy(existing)
                    raise ValueError("COST_ID_CONFLICT")
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


def economics_report(records: list[Mapping[str, Any]], period_start: str, period_end: str) -> dict[str, Any]:
    start, end = _parse_utc(period_start), _parse_utc(period_end)
    if end <= start:
        raise ValueError("INVALID_MEASUREMENT_PERIOD")
    selected = []
    for record in records:
        validate_record(record)
        if _parse_utc(record["period_start"]) < end and _parse_utc(record["period_end"]) > start:
            selected.append(record)
    itemized = {name: [] for name in sorted(CATEGORIES)}
    for record in selected:
        itemized[record["cost_category"]].append(copy.deepcopy(dict(record)))
    measurable_infra = [r for r in selected if r["infrastructure_cost"] and r["amount"] is not None]
    currencies = {r["currency"] for r in measurable_infra}
    subtotal = sum(r["amount"] for r in measurable_infra) if measurable_infra and len(currencies) <= 1 else None
    projections = [r["monthly_projected_amount"] for r in selected if r["infrastructure_cost"] and r["monthly_projected_amount"] is not None]
    projected = sum(projections) if projections and len(currencies) <= 1 else None
    reviewer_amounts = [r["amount"] for r in selected if r["cost_category"] == "REVIEWER_OPERATIONS" and r["amount"] is not None]
    return {"itemized": itemized, "infrastructure_subtotal": subtotal,
            "reviewer_operations_subtotal": sum(reviewer_amounts) if reviewer_amounts else None,
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "monthly_projected_infrastructure_cost": projected,
            "target_comparison": ("WITHIN_ORIGINAL_TARGET" if projected is not None and 2 <= projected <= 25 else
                                  "OUTSIDE_ORIGINAL_TARGET" if projected is not None else "UNKNOWN"),
            "measurement_status": "NOT_MEASURABLE" if not selected or all(r["measurement_status"] == "NOT_MEASURABLE" for r in selected) else "MEASURED"}
