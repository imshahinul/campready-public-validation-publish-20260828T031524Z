"""Pure deterministic Phase 5 pilot derivations and metric arithmetic."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def unknown_none_dq_hold_conflict(relevance_status: Any, quality_defect_basis: Any,
                                  reviewer_disposition: Any) -> bool:
    return relevance_status == "UNKNOWN" and quality_defect_basis == "NONE" and reviewer_disposition == "DATA_QUALITY_HOLD"


def comprehension_metrics(observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    unique: dict[str, Mapping[str, Any]] = {}
    for row in observations:
        identity = row.get("observation_id")
        if identity is None:
            raise ValueError("OBSERVATION_ID_REQUIRED")
        if identity in unique:
            raise ValueError("DUPLICATE_OBSERVATION_ID")
        unique[identity] = row
    denominator = sum(row.get("reviewer_code") == "PRIMARY_REVIEWER" and row.get("reviewer_disposition") is not None for row in unique.values())
    numerator = sum(row.get("confirmed_structured_disposition_incompatibility") is True for row in unique.values())
    return {
        "confirmed_comprehension_error_count": numerator,
        "primary_disposition_count": denominator,
        "disposition_comprehension_error_rate": "NOT_MEASURABLE" if denominator == 0 else numerator / denominator,
        "unknown_none_dq_hold_conflict_count": sum(row.get("unknown_none_dq_hold_conflict") is True for row in unique.values()),
        "corrections_required": sum(row.get("correction_required") is True for row in unique.values()),
    }


def disagreement_metrics(observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(observations)
    reviewed = sum(row.get("secondary_reviewer_code") == "SECOND_REVIEWER" and row.get("secondary_disposition") is not None for row in rows)
    disagreements = sum(row.get("disagreement_type") is not None for row in rows)
    return {
        "disagreement_count": disagreements,
        "independently_double_reviewed_count": reviewed,
        "disagreement_rate": "NOT_MEASURABLE" if reviewed == 0 else disagreements / reviewed,
        "allowed_discretionary_difference_count": sum(row.get("disagreement_type") == "ALLOWED_DISCRETIONARY_DIFFERENCE" for row in rows),
    }


def pilot_maintenance_rows(records: Iterable[Mapping[str, Any]], period_start: str, period_end: str) -> list[Mapping[str, Any]]:
    from maintenance_ledger import _parse_utc, validate_record
    start, end = _parse_utc(period_start), _parse_utc(period_end)
    selected = []
    for row in records:
        validate_record(row)
        if row["work_class"] == "OPERATIONAL_MAINTENANCE" and start <= _parse_utc(row["timestamp"]) < end:
            selected.append(row)
    return selected


def separated_workload(observations: Iterable[Mapping[str, Any]], maintenance_rows: Iterable[Mapping[str, Any]], development_minutes: float = 0) -> dict[str, float]:
    rows = list(observations)
    return {
        "review_minutes": sum(float(row.get("review_minutes", 0)) for row in rows),
        "adjudication_minutes": sum(float(row.get("adjudication_minutes", 0)) for row in rows),
        "correction_minutes": sum(float(row.get("correction_minutes", 0)) for row in rows),
        "operational_maintenance_minutes": sum(float(row.get("human_minutes", 0)) for row in maintenance_rows if row.get("work_class") == "OPERATIONAL_MAINTENANCE"),
        "development_debugging_minutes": float(development_minutes),
    }
