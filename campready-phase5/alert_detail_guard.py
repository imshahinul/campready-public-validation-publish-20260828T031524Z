"""Fail-closed validation for the USFS ALERT_DETAIL final-failure guard."""

from __future__ import annotations

from typing import Any, Mapping

GUARD_CONTRACT_INVALID = "GUARD_CONTRACT_INVALID"
GUARD_PROHIBITIONS = frozenset({
    "NO_APPEARANCE_INFERENCE", "NO_DISAPPEARANCE_INFERENCE",
    "NO_RESOLUTION_INFERENCE", "NO_SEMANTIC_CHANGE_INFERENCE",
    "NO_SAFETY_INFERENCE", "NO_ROAD_PASSABILITY_INFERENCE",
    "NO_SOURCE_SILENCE_AS_SAFE",
})
GUARDED_INITIAL_TARGETS = frozenset({"DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"})


class GuardContractError(ValueError):
    """Stable fail-closed guard-contract failure."""

    reason = GUARD_CONTRACT_INVALID

    def __init__(self) -> None:
        super().__init__(self.reason)


def _invalid() -> None:
    raise GuardContractError()


def validate_alert_detail_guard(item: Mapping[str, Any]) -> bool:
    """Validate guarded evidence without repairing or reinterpreting the item."""
    if not item.get("alert_detail_guard_required"):
        return False
    if (item.get("authority_id"), item.get("source_role"), item.get("retrieval_status")) != (
            "USFS", "ALERT_DETAIL", "FINAL_FAILED"):
        _invalid()
    if item.get("notification_eligible") is not False:
        _invalid()
    quality = item.get("data_quality_state")
    unknown = item.get("unknown_state")
    if not isinstance(quality, Mapping) or quality.get("classification") != "DATA_QUALITY" or not quality.get("reason_code"):
        _invalid()
    if not isinstance(unknown, Mapping) or unknown.get("unknown") is not True or not unknown.get("reason_code"):
        _invalid()
    if item.get("lifecycle_classification") != "NONE" or item.get("event_category") is not None:
        _invalid()
    if item.get("prior_accepted_state_ref") is not None and item.get("current_accepted_state_ref") != item.get("prior_accepted_state_ref"):
        _invalid()
    if not isinstance(item.get("official_source_url"), str) or not item["official_source_url"].startswith("https://"):
        _invalid()
    if not item.get("provenance_run_id") or not item.get("provenance_record_ref"):
        _invalid()
    prohibitions = item.get("interpretation_prohibitions")
    if not isinstance(prohibitions, list) or not GUARD_PROHIBITIONS.issubset(prohibitions):
        _invalid()
    retry = item.get("retry_telemetry_summary")
    if not isinstance(retry, Mapping) or retry.get("final_failed") is not True or retry.get("rescued") is not False:
        _invalid()
    return True


def validate_guarded_transition(item: Mapping[str, Any], from_status: str, to_status: str) -> None:
    """Apply the additional guarded-initial-transition restriction."""
    if validate_alert_detail_guard(item) and from_status == "NEW" and to_status not in GUARDED_INITIAL_TARGETS:
        raise ValueError("ALERT_DETAIL_GUARD_TRANSITION_PROHIBITED")
