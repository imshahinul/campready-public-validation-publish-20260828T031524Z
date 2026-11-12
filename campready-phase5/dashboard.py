"""Deterministic, offline Phase 5 operational dashboard read model.

All inputs are already-persisted evidence supplied by the caller.  This module
does not retrieve, schedule, notify, append, or mutate source evidence.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from cost_ledger import economics_report
from maintenance_ledger import maintenance_report

SCHEMA_VERSION = "campready.phase5.dashboard.v1"
SECTIONS = ("SCHEDULER", "RETRIEVAL", "SEMANTIC", "QUALITY", "SAFETY", "REVIEW", "MAINTENANCE", "ECONOMICS")
REVIEW_STATUSES = ("NEW", "REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP", "ACKNOWLEDGED")
USFS_ROLES = ("CAMPGROUND_PAGE", "ALERT_INDEX", "ALERT_DETAIL")


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("UTC_TIMESTAMP_REQUIRED") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return parsed


def _context(period_start: str, period_end: str, freshness: str) -> dict[str, str]:
    return {"period_start": period_start, "period_end": period_end, "freshness_timestamp": freshness}


def _rate(successes: int, attempts: int, context: Mapping[str, str]) -> dict[str, Any]:
    return {"successes": successes, "attempts": attempts, "failures": attempts - successes,
            "reliability": successes / attempts if attempts else None,
            "measurement_status": "MEASURED" if attempts else "NOT_MEASURABLE", **context}


def _scheduler(start: datetime, end: datetime, cadence_seconds: int,
               observations: Iterable[Mapping[str, Any]], context: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(cadence_seconds, int) or isinstance(cadence_seconds, bool) or cadence_seconds <= 0:
        raise ValueError("INVALID_CADENCE")
    expected: list[datetime] = []
    slot = start
    while slot < end:
        expected.append(slot)
        slot += timedelta(seconds=cadence_seconds)
    expected_set = set(expected)
    counts: Counter[datetime] = Counter()
    ambiguous: set[datetime] = set()
    for observation in observations:
        observed_slot = _utc(observation["slot"])
        if observed_slot < start or observed_slot >= end or observed_slot not in expected_set:
            raise ValueError("OBSERVATION_OUTSIDE_EXPECTED_SLOT")
        counts[observed_slot] += 1
        if observation.get("ambiguous") is True:
            ambiguous.add(observed_slot)
    missing = [candidate for candidate in expected if counts[candidate] == 0]
    longest = current = 0
    for candidate in expected:
        current = current + 1 if counts[candidate] == 0 else 0
        longest = max(longest, current)
    observed_count = len(counts)
    return {"expected_slots": len(expected), "observed_slots": observed_count,
            "missing_slots": len(missing), "longest_missing_streak": longest,
            "duplicate_slots": sum(value - 1 for value in counts.values() if value > 1),
            "ambiguous_slots": len(ambiguous),
            "coverage_ratio": observed_count / len(expected) if expected else None,
            "coverage_numerator": observed_count, "coverage_denominator": len(expected),
            "measurement_status": "MEASURED" if expected else "NOT_MEASURABLE", **context}


def _retrieval(records: Iterable[Mapping[str, Any]], context: Mapping[str, str]) -> dict[str, Any]:
    rows = list(records)

    def rates(selected: list[Mapping[str, Any]]) -> dict[str, Any]:
        return {"aggregate_first_attempt": _rate(sum(r.get("first_attempt_success") is True for r in selected), len(selected), context),
                "aggregate_eventual": _rate(sum(r.get("eventual_success") is True for r in selected), len(selected), context)}

    by_authority = {authority: rates([r for r in rows if r.get("authority_id") == authority])
                    for authority in sorted({str(r.get("authority_id")) for r in rows})}
    by_role = {role: rates([r for r in rows if r.get("authority_id") == "USFS" and r.get("source_role") == role])
               for role in USFS_ROLES}
    eligible = sum(r.get("retry_eligible") is True for r in rows)
    attempted = sum(r.get("retry_attempted") is True for r in rows)
    rescued = sum(r.get("retry_rescued") is True for r in rows)
    final_failed = sum(r.get("retry_final_failed") is True for r in rows)
    return {**rates(rows), "by_authority": by_authority, "usfs_by_source_role": by_role,
            "retry": {"retry_eligible_count": eligible, "retry_attempted_count": attempted,
                      "retry_rescued_count": rescued, "retry_final_failed_count": final_failed,
                      "retry_rescue_rate": rescued / attempted if attempted else None,
                      "retry_rescue_numerator": rescued, "retry_rescue_denominator": attempted,
                      "measurement_status": "MEASURED" if attempted else "NOT_MEASURABLE", **context},
            "measurement_status": "MEASURED" if rows else "UNKNOWN", **context}


def _semantic(records: Iterable[Mapping[str, Any]], context: Mapping[str, str]) -> dict[str, Any]:
    rows = list(records)
    reasons = Counter(str(r.get("unresolved_reason")) for r in rows if r.get("unresolved") is True)
    return {"candidate_count": sum(r.get("candidate") is True for r in rows),
            "legitimate_count": sum(r.get("legitimate") is True for r in rows),
            "retrieval_recovery_churn_count": sum(r.get("retrieval_recovery_churn") is True for r in rows),
            "cross_scope_fanout_count": sum(int(r.get("cross_scope_fanout_count", 0)) for r in rows),
            "unresolved_count": sum(reasons.values()), "unresolved_by_reason": dict(sorted(reasons.items())),
            "measurement_status": "MEASURED" if rows else "UNKNOWN", **context}


def _effective_annotations(annotations: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    effective: dict[str, Mapping[str, Any]] = {}
    for annotation in annotations:
        item = str(annotation["review_item_id"])
        version = annotation.get("annotation_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("MALFORMED_ANNOTATION_EVIDENCE")
        prior = effective.get(item)
        if prior is not None and version <= prior["annotation_version"]:
            raise ValueError("MALFORMED_ANNOTATION_EVIDENCE")
        effective[item] = annotation
    return effective


def _status(item: Mapping[str, Any], effective: Mapping[str, Mapping[str, Any]]) -> str:
    annotation = effective.get(str(item["review_item_id"]))
    return str(annotation["to_status"]) if annotation else str(item.get("reviewer_status", "NEW"))


def _quality(items: list[Mapping[str, Any]], effective: Mapping[str, Mapping[str, Any]], context: Mapping[str, str]) -> dict[str, Any]:
    parser_denominator = sum(i.get("parser_status") in {"SUCCESS", "FAILED"} for i in items)
    parser_failures = sum(i.get("parser_status") == "FAILED" for i in items)
    guarded = [i for i in items if i.get("alert_detail_guard_required") is True and i.get("retrieval_status") == "FINAL_FAILED"]
    statuses = Counter(_status(i, effective) for i in guarded)
    return {"parser_failure_count": parser_failures, "parser_denominator": parser_denominator,
            "parser_failure_rate": parser_failures / parser_denominator if parser_denominator else None,
            "parser_measurement_status": "MEASURED" if parser_denominator else "NOT_MEASURABLE",
            "data_quality_item_count": sum(i.get("data_quality_state", {}).get("classification") == "DATA_QUALITY" for i in items),
            "unknown_item_count": sum(i.get("unknown_state", {}).get("unknown") is True for i in items),
            "guarded_alert_detail": {"total": len(guarded), "outstanding": sum(statuses[s] for s in REVIEW_STATUSES if s != "ACKNOWLEDGED"),
                                     "acknowledged": statuses["ACKNOWLEDGED"], "needs_followup": statuses["NEEDS_FOLLOWUP"],
                                     "data_quality_hold": statuses["DATA_QUALITY_HOLD"]},
            "measurement_status": "MEASURED" if items else "UNKNOWN", **context}


def _percentiles(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if len(ordered) < 2:
        return {"count": len(ordered), "median_seconds": None, "p90_seconds": None,
                "maximum_seconds": ordered[-1] if ordered else None,
                "percentile_method": "nearest-rank; unavailable when n<2", "measurement_status": "NOT_MEASURABLE"}
    nearest = lambda p: ordered[max(0, math.ceil(p * len(ordered)) - 1)]
    return {"count": len(ordered), "median_seconds": nearest(0.5), "p90_seconds": nearest(0.9),
            "maximum_seconds": ordered[-1], "percentile_method": "nearest-rank", "measurement_status": "MEASURED"}


def _review(items: list[Mapping[str, Any]], effective: Mapping[str, Mapping[str, Any]], context: Mapping[str, str]) -> dict[str, Any]:
    statuses = Counter(_status(item, effective) for item in items)
    latencies: list[float] = []
    detected = {str(item["review_item_id"]): _utc(item["detected_at"]) for item in items}
    for item_id, annotation in effective.items():
        if annotation.get("acknowledged") is True:
            if item_id not in detected:
                raise ValueError("ACKNOWLEDGEMENT_WITHOUT_REVIEW_ITEM")
            latency = (_utc(annotation.get("acknowledged_at")) - detected[item_id]).total_seconds()
            if latency < 0:
                raise ValueError("NEGATIVE_ACKNOWLEDGEMENT_LATENCY")
            latencies.append(latency)
    outstanding = sum(statuses[s] for s in REVIEW_STATUSES if s != "ACKNOWLEDGED")
    latency = _percentiles(latencies)
    latency.update(context)
    return {"new_items": statuses["NEW"], "reviewed_valid": statuses["REVIEWED_VALID"],
            "reviewed_not_actionable": statuses["REVIEWED_NOT_ACTIONABLE"], "data_quality_hold": statuses["DATA_QUALITY_HOLD"],
            "needs_followup": statuses["NEEDS_FOLLOWUP"], "acknowledged_items": statuses["ACKNOWLEDGED"],
            "outstanding_items": outstanding, "unresolved_followup_count": statuses["NEEDS_FOLLOWUP"],
            "acknowledgement_latency": latency, "measurement_status": "MEASURED" if items else "UNKNOWN", **context}


def derive_dashboard(*, period_start: str, period_end: str, generated_at: str, cadence_seconds: int,
                     scheduler_observations: Iterable[Mapping[str, Any]] = (),
                     retrieval_records: Iterable[Mapping[str, Any]] = (), semantic_records: Iterable[Mapping[str, Any]] = (),
                     safety_records: Iterable[Mapping[str, Any]] = (), review_items: Iterable[Mapping[str, Any]] = (),
                     annotations: Iterable[Mapping[str, Any]] = (), maintenance_records: list[Mapping[str, Any]] | None = None,
                     cost_records: list[Mapping[str, Any]] | None = None, maintenance_evidence_complete: bool = False) -> dict[str, Any]:
    """Build a read-only dashboard from explicit persisted evidence and period."""
    start, end, generated = _utc(period_start), _utc(period_end), _utc(generated_at)
    if end <= start:
        raise ValueError("INVALID_MEASUREMENT_PERIOD")
    context = _context(period_start, period_end, generated_at)
    items, annotation_rows = list(review_items), list(annotations)
    effective = _effective_annotations(annotation_rows)
    safety = list(safety_records)
    safety_section = {name: sum(int(r.get(name, 0)) for r in safety) for name in
                      ("unsafe_candidate_count", "real_notification_count", "false_lifecycle_claim_count", "fail_closed_retention_violation_count")}
    safety_section.update({"measurement_status": "MEASURED" if safety else "UNKNOWN", **context})
    dashboard = {"schema_version": SCHEMA_VERSION, "measurement_period": {"period_start": period_start, "period_end": period_end},
                 "generated_at": generated_at, "freshness_timestamp": generated_at,
                 "SCHEDULER": _scheduler(start, end, cadence_seconds, scheduler_observations, context),
                 "RETRIEVAL": _retrieval(retrieval_records, context), "SEMANTIC": _semantic(semantic_records, context),
                 "QUALITY": _quality(items, effective, context), "SAFETY": safety_section,
                 "REVIEW": _review(items, effective, context),
                 "MAINTENANCE": {**maintenance_report(maintenance_records or [], period_start, period_end, maintenance_evidence_complete), **context},
                 "ECONOMICS": {**economics_report(cost_records or [], period_start, period_end), **context}}
    return copy.deepcopy(dashboard)


def render_dashboard(dashboard: Mapping[str, Any]) -> str:
    """Render stable local text without reducing sections to an overall score."""
    lines = [f"CampReady operational dashboard ({dashboard['measurement_period']['period_start']} to {dashboard['measurement_period']['period_end']})",
             f"Generated/freshness: {dashboard['generated_at']}"]
    for section in SECTIONS:
        lines.extend((f"\n[{section}]", json.dumps(dashboard[section], ensure_ascii=False, sort_keys=True, indent=2)))
    return "\n".join(lines) + "\n"
