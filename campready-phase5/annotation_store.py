"""Append-only, durable local JSONL reviewer-annotation store."""

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

SCHEMA_VERSION = "campready.phase5.reviewer_annotation.v1"
STATUSES = {"NEW", "REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP", "ACKNOWLEDGED"}
FIELDS = {"schema_version", "annotation_id", "review_item_id", "annotation_version", "supersedes_ref", "reviewer_id", "timestamp", "from_status", "to_status", "disposition", "reason_code", "note", "acknowledged", "acknowledged_at", "acknowledged_status", "acknowledged_disposition", "idempotency_key"}
ID_RE = re.compile(r"^cr5ann1-[0-9a-f]{64}$")
ITEM_RE = re.compile(r"^cr5ri1-[0-9a-f]{64}$")
IDEM_RE = re.compile(r"^cr5idem1-[0-9a-f]{64}$")
REVIEWER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,127}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _digest(prefix: str, value: list[Any]) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def annotation_id(annotation: Mapping[str, Any]) -> str:
    return _digest("cr5ann1-", [SCHEMA_VERSION, annotation["review_item_id"], annotation["annotation_version"],
        annotation["from_status"], annotation["to_status"], annotation.get("disposition") or "",
        annotation.get("reason_code") or "", annotation.get("note") or "", annotation["reviewer_id"], annotation["timestamp"]])


def idempotency_key(annotation: Mapping[str, Any]) -> str:
    return _digest("cr5idem1-", ["campready.phase5.reviewer_annotation.idempotency.v1",
        annotation["review_item_id"], annotation["annotation_version"], annotation["from_status"],
        annotation["to_status"], annotation.get("disposition") or "", annotation["reviewer_id"]])


def _utc(value: Any) -> bool:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def validate_annotation(annotation: Mapping[str, Any]) -> None:
    if set(annotation) != FIELDS or annotation.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if not ID_RE.fullmatch(str(annotation.get("annotation_id", ""))) or not ITEM_RE.fullmatch(str(annotation.get("review_item_id", ""))):
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if not isinstance(annotation.get("annotation_version"), int) or isinstance(annotation.get("annotation_version"), bool) or annotation["annotation_version"] < 1:
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if annotation.get("supersedes_ref") is not None and not ID_RE.fullmatch(str(annotation["supersedes_ref"])):
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if not REVIEWER_RE.fullmatch(str(annotation.get("reviewer_id", ""))) or not _utc(annotation.get("timestamp")):
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if annotation.get("from_status") not in STATUSES or annotation.get("to_status") not in STATUSES:
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    for name in ("disposition", "reason_code", "acknowledged_disposition"):
        value = annotation.get(name)
        if value is not None and not CODE_RE.fullmatch(str(value)):
            raise ValueError("ANNOTATION_SCHEMA_INVALID")
    note = annotation.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip() or len(note) > 2000 or any(ord(c) < 32 and c not in "\n\r\t" for c in note)):
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if not isinstance(annotation.get("acknowledged"), bool) or not IDEM_RE.fullmatch(str(annotation.get("idempotency_key", ""))):
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if annotation["acknowledged"]:
        if not _utc(annotation.get("acknowledged_at")) or annotation.get("acknowledged_status") not in STATUSES:
            raise ValueError("ANNOTATION_SCHEMA_INVALID")
    elif any(annotation.get(k) is not None for k in ("acknowledged_at", "acknowledged_status", "acknowledged_disposition")):
        raise ValueError("ANNOTATION_SCHEMA_INVALID")
    if annotation["annotation_id"] != annotation_id(annotation) or annotation["idempotency_key"] != idempotency_key(annotation):
        raise ValueError("ANNOTATION_IDENTITY_INVALID")


class AnnotationStore:
    """A process-locked O_APPEND JSONL store; existing bytes are never rewritten."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.endswith("\n") or not line.strip():
                        raise ValueError("MALFORMED_ANNOTATION_STORE")
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("MALFORMED_ANNOTATION_STORE")
                    validate_annotation(value)
                    records.append(value)
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise ValueError("MALFORMED_ANNOTATION_STORE") from exc
        self._validate_history(records)
        return records

    @staticmethod
    def _validate_history(records: list[dict[str, Any]]) -> None:
        per_item: dict[str, list[dict[str, Any]]] = {}
        seen_ids: set[str] = set()
        seen_idem: set[str] = set()
        for record in records:
            if record["annotation_id"] in seen_ids or record["idempotency_key"] in seen_idem:
                raise ValueError("MALFORMED_ANNOTATION_STORE")
            seen_ids.add(record["annotation_id"]); seen_idem.add(record["idempotency_key"])
            history = per_item.setdefault(record["review_item_id"], [])
            expected = len(history) + 1
            expected_ref = None if expected == 1 else history[-1]["annotation_id"]
            if record["annotation_version"] != expected or record["supersedes_ref"] != expected_ref:
                raise ValueError("MALFORMED_ANNOTATION_STORE")
            history.append(record)

    def read_all(self) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return copy.deepcopy(self._read_unlocked())

    def for_item(self, review_item_id: str) -> list[dict[str, Any]]:
        return [r for r in self.read_all() if r["review_item_id"] == review_item_id]

    def append(self, annotation: Mapping[str, Any]) -> dict[str, Any]:
        candidate = copy.deepcopy(dict(annotation))
        validate_annotation(candidate)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self._read_unlocked()
            for existing in records:
                if existing["idempotency_key"] == candidate["idempotency_key"]:
                    return copy.deepcopy(existing)
            history = [r for r in records if r["review_item_id"] == candidate["review_item_id"]]
            expected_version = len(history) + 1
            if candidate["annotation_version"] != expected_version:
                raise ValueError("ANNOTATION_VERSION_CONFLICT")
            expected_ref = None if not history else history[-1]["annotation_id"]
            if candidate["supersedes_ref"] != expected_ref:
                raise ValueError("SUPERSEDES_REF_INVALID")
            row = _canonical_bytes(candidate) + b"\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(fd, row)
                if written != len(row):
                    raise OSError("short append")
                os.fsync(fd)
            finally:
                os.close(fd)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return copy.deepcopy(candidate)
