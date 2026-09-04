from __future__ import annotations

import hashlib
import json
import sys

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CURRENT = "CURRENT_OR_UNBOUNDED"
INCLUDE = "INCLUDE"


def sha256_bytes(value):
    return hashlib.sha256(
        value
    ).hexdigest()


def source_map(items):
    result = {}

    duplicates = []

    for item in items:
        key = item[
            "source_event_id"
        ]

        if key in result:
            duplicates.append(
                key
            )

        result[key] = item

    return result, duplicates


def relationship_map(items):
    result = {}

    duplicates = []

    for item in items:
        key = (
            item[
                "source_event_id"
            ],
            item[
                "campground"
            ],
        )

        if key in result:
            duplicates.append(
                key
            )

        result[key] = item

    return result, duplicates


def changed_fields(
    before,
    after,
):
    keys = (
        set(before)
        | set(after)
    )

    changed = []

    for key in sorted(keys):
        if (
            before.get(key)
            != after.get(key)
        ):
            changed.append(
                key
            )

    return changed


def parsed_observation_date(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value)
        ).date()
    except ValueError:
        pass

    for fmt in (
        "%B %d, %Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(
                str(value),
                fmt,
            ).date()
        except ValueError:
            continue

    return None


def expected_unknown_road_time_transition(
    event,
    before_relationships,
    after_relationships,
    baseline,
    second,
):
    if (
        not before_relationships
        or set(before_relationships)
        != set(after_relationships)
    ):
        return None

    semantic = event.get(
        "semantic",
        {},
    )

    effective_raw = (
        semantic.get(
            "operational_effective_date"
        )
        or semantic.get(
            "alert_start_date"
        )
    )

    effective = parsed_observation_date(
        effective_raw
    )

    baseline_reference = parsed_observation_date(
        baseline.get(
            "reference_date_utc"
        )
    )

    second_reference = parsed_observation_date(
        second.get(
            "reference_date_utc"
        )
    )

    if (
        effective is None
        or baseline_reference is None
        or second_reference is None
        or not (
            baseline_reference
            < effective
            <= second_reference
        )
    ):
        return None

    transitions = []

    for key in sorted(
        before_relationships
    ):
        before = before_relationships[key]
        after = after_relationships[key]

        if before == after:
            continue

        before_rest = dict(before)
        after_rest = dict(after)

        before_lifecycle = before_rest.pop(
            "lifecycle",
            None,
        )

        after_lifecycle = after_rest.pop(
            "lifecycle",
            None,
        )

        if before_rest != after_rest:
            return None

        if (
            before_lifecycle
            != "SCHEDULED"
            or after_lifecycle
            != CURRENT
        ):
            return None

        if (
            before.get("relevance")
            != "UNKNOWN"
            or after.get("relevance")
            != "UNKNOWN"
            or before.get("reason")
            != "ROAD_RELATION_UNPROVEN"
            or after.get("reason")
            != "ROAD_RELATION_UNPROVEN"
            or before.get("resolution")
            is not False
            or after.get("resolution")
            is not False
            or before.get(
                "baseline_notification_eligible"
            )
            is not False
            or after.get(
                "baseline_notification_eligible"
            )
            is not False
        ):
            return None

        transitions.append({
            "campground": key[1],
            "before_lifecycle": (
                before_lifecycle
            ),
            "after_lifecycle": (
                after_lifecycle
            ),
        })

    if not transitions:
        return None

    return {
        "effective_date": effective_raw,
        "baseline_reference_date": (
            baseline.get(
                "reference_date_utc"
            )
        ),
        "second_reference_date": (
            second.get(
                "reference_date_utc"
            )
        ),
        "relationship_count": len(
            transitions
        ),
        "relationships": transitions,
    }


def event_title(item):
    semantic = item.get(
        "semantic",
        {},
    )

    return (
        semantic.get(
            "title"
        )
        or semantic.get(
            "event"
        )
        or item.get(
            "campground"
        )
        or item.get(
            "source_event_id"
        )
    )


def usfs_candidate_relationships(
    event_id,
    relationships,
):
    candidates = []

    for (
        source_event_id,
        campground
    ), relation in relationships.items():

        if source_event_id != event_id:
            continue

        if (
            relation.get(
                "relevance"
            )
            != INCLUDE
        ):
            continue

        if (
            relation.get(
                "lifecycle"
            )
            != CURRENT
        ):
            continue

        candidates.append({
            "campground": (
                campground
            ),
            "reason": (
                relation.get(
                    "reason"
                )
            ),
            "lifecycle": (
                relation.get(
                    "lifecycle"
                )
            ),
            "resolution": (
                bool(
                    relation.get(
                        "resolution"
                    )
                )
            ),
        })

    return sorted(
        candidates,
        key=lambda item: item[
            "campground"
        ],
    )


def nws_candidate_relationships(
    event_id,
    relationships,
):
    candidates = []

    for (
        source_event_id,
        campground
    ), relation in relationships.items():

        if source_event_id != event_id:
            continue

        if (
            relation.get(
                "relevance"
            )
            != INCLUDE
        ):
            continue

        candidates.append({
            "campground": (
                campground
            ),
            "reason": (
                relation.get(
                    "reason"
                )
            ),
        })

    return sorted(
        candidates,
        key=lambda item: item[
            "campground"
        ],
    )


def main():
    baseline_path = Path(
        sys.argv[1]
    )

    second_path = Path(
        sys.argv[2]
    )

    baseline_report_path = Path(
        sys.argv[3]
    )

    second_report_path = Path(
        sys.argv[4]
    )

    output_path = Path(
        sys.argv[5]
    )

    baseline_raw = (
        baseline_path.read_bytes()
    )

    second_raw = (
        second_path.read_bytes()
    )

    baseline = json.loads(
        baseline_raw.decode(
            "utf-8"
        )
    )

    second = json.loads(
        second_raw.decode(
            "utf-8"
        )
    )

    baseline_report = json.loads(
        baseline_report_path.read_text(
            encoding="utf-8"
        )
    )

    second_report = json.loads(
        second_report_path.read_text(
            encoding="utf-8"
        )
    )

    frozen_inputs_same = (
        baseline.get(
            "frozen_inputs"
        )
        == second.get(
            "frozen_inputs"
        )
    )

    policy_same = (
        baseline.get(
            "policy"
        )
        == second.get(
            "policy"
        )
    )

    baseline_hash_matches_report = (
        sha256_bytes(
            baseline_raw
        )
        == baseline_report.get(
            "summary",
            {}
        ).get(
            "baseline_sha256"
        )
    )

    second_hash_matches_report = (
        sha256_bytes(
            second_raw
        )
        == second_report.get(
            "summary",
            {}
        ).get(
            "baseline_sha256"
        )
    )

    second_capture_pass = (
        second_report.get(
            "decision"
        )
        == "PASS_FIRST_LIVE_BASELINE_CAPTURE"
    )

    second_summary = (
        second_report.get(
            "summary",
            {}
        )
    )

    alert_count = int(
        second_summary.get(
            "usfs_live_alert_count",
            0,
        )
        or 0
    )

    expected_full_score = (
        f"{alert_count}/{alert_count}"
    )

    full_live_health = (
        alert_count >= 1
        and second_summary.get(
            "usfs_alert_fetch_success"
        )
        == expected_full_score
        and second_summary.get(
            "usfs_alert_parse_success"
        )
        == expected_full_score
        and second_summary.get(
            "recreation_fetch_success"
        )
        == "10/10"
        and second_summary.get(
            "recreation_parse_success"
        )
        == "10/10"
        and second_summary.get(
            "nws_query_success"
        )
        == "10/10"
        and second_summary.get(
            "nws_payload_success"
        )
        == "10/10"
    )

    baseline_alerts, dup_ba = (
        source_map(
            baseline.get(
                "usfs_alert_index",
                {},
            ).get(
                "source_events",
                [],
            )
        )
    )

    second_alerts, dup_sa = (
        source_map(
            second.get(
                "usfs_alert_index",
                {},
            ).get(
                "source_events",
                [],
            )
        )
    )

    baseline_rec, dup_br = (
        source_map(
            baseline.get(
                "usfs_recreation_pages",
                [],
            )
        )
    )

    second_rec, dup_sr = (
        source_map(
            second.get(
                "usfs_recreation_pages",
                [],
            )
        )
    )

    baseline_nws, dup_bn = (
        source_map(
            baseline.get(
                "nws",
                {},
            ).get(
                "source_events",
                [],
            )
        )
    )

    second_nws, dup_sn = (
        source_map(
            second.get(
                "nws",
                {},
            ).get(
                "source_events",
                [],
            )
        )
    )

    baseline_usfs_rel, dup_bur = (
        relationship_map(
            baseline.get(
                "usfs_alert_index",
                {},
            ).get(
                "relationships",
                [],
            )
        )
    )

    second_usfs_rel, dup_sur = (
        relationship_map(
            second.get(
                "usfs_alert_index",
                {},
            ).get(
                "relationships",
                [],
            )
        )
    )

    baseline_nws_rel, dup_bnr = (
        relationship_map(
            baseline.get(
                "nws",
                {},
            ).get(
                "relationships",
                [],
            )
        )
    )

    second_nws_rel, dup_snr = (
        relationship_map(
            second.get(
                "nws",
                {},
            ).get(
                "relationships",
                [],
            )
        )
    )

    duplicate_ids = {
        "baseline_usfs": dup_ba,
        "second_usfs": dup_sa,
        "baseline_recreation": dup_br,
        "second_recreation": dup_sr,
        "baseline_nws": dup_bn,
        "second_nws": dup_sn,
        "baseline_usfs_relationships": (
            dup_bur
        ),
        "second_usfs_relationships": (
            dup_sur
        ),
        "baseline_nws_relationships": (
            dup_bnr
        ),
        "second_nws_relationships": (
            dup_snr
        ),
    }

    identity_unique = not any(
        duplicate_ids.values()
    )

    deltas = []

    candidates = []

    unsafe_candidates = []

    # -----------------------------------------------------
    # USFS alert index events
    # -----------------------------------------------------

    alert_ids = (
        set(
            baseline_alerts
        )
        | set(
            second_alerts
        )
    )

    for event_id in sorted(
        alert_ids
    ):
        before = baseline_alerts.get(
            event_id
        )

        after = second_alerts.get(
            event_id
        )

        if before is None:
            delta_type = (
                "NEW_SOURCE_EVENT"
            )

            candidate_rel = (
                usfs_candidate_relationships(
                    event_id,
                    second_usfs_rel,
                )
            )

            for relation in candidate_rel:
                candidates.append({
                    "source_family": (
                        "USFS_ALERT_INDEX"
                    ),
                    "source_event_id": (
                        event_id
                    ),
                    "campground": (
                        relation[
                            "campground"
                        ]
                    ),
                    "change_type": (
                        delta_type
                    ),
                    "reason": (
                        relation[
                            "reason"
                        ]
                    ),
                    "resolution": (
                        relation[
                            "resolution"
                        ]
                    ),
                })

            deltas.append({
                "source_family": (
                    "USFS_ALERT_INDEX"
                ),
                "source_event_id": (
                    event_id
                ),
                "title": (
                    event_title(
                        after
                    )
                ),
                "delta_type": (
                    delta_type
                ),
                "notification_candidate_count": (
                    len(
                        candidate_rel
                    )
                ),
            })

            continue

        if after is None:
            deltas.append({
                "source_family": (
                    "USFS_ALERT_INDEX"
                ),
                "source_event_id": (
                    event_id
                ),
                "title": (
                    event_title(
                        before
                    )
                ),
                "delta_type": (
                    "SOURCE_EVENT_REMOVED_FROM_INDEX"
                ),
                "notification_candidate_count": 0,
                "resolution": False,
                "safety_inference": False,
            })

            continue

        same_fp = (
            before.get(
                "semantic_fingerprint"
            )
            == after.get(
                "semantic_fingerprint"
            )
        )

        if same_fp:
            before_rel = {
                key: value
                for key, value
                in baseline_usfs_rel.items()
                if key[0]
                == event_id
            }
            after_rel = {
                key: value
                for key, value
                in second_usfs_rel.items()
                if key[0]
                == event_id
            }
            if (
                before_rel
                != after_rel
            ):
                time_transition = (
                    expected_unknown_road_time_transition(
                        after,
                        before_rel,
                        after_rel,
                        baseline,
                        second,
                    )
                )

                if (
                    time_transition
                    is not None
                ):
                    deltas.append({
                        "source_family": (
                            "USFS_ALERT_INDEX"
                        ),
                        "source_event_id": (
                            event_id
                        ),
                        "title": (
                            event_title(
                                after
                            )
                        ),
                        "delta_type": (
                            "TIME_DRIVEN_UNKNOWN_ROAD_"
                            "LIFECYCLE_TRANSITION"
                        ),
                        "notification_candidate_count": 0,
                        "time_transition": (
                            time_transition
                        ),
                    })
                else:
                    deltas.append({
                        "source_family": (
                            "USFS_ALERT_INDEX"
                        ),
                        "source_event_id": (
                            event_id
                        ),
                        "title": (
                            event_title(
                                after
                            )
                        ),
                        "delta_type": (
                            "RULE_OR_RELATIONSHIP_DRIFT"
                        ),
                        "notification_candidate_count": 0,
                    })
            continue

        candidate_rel = (
            usfs_candidate_relationships(
                event_id,
                second_usfs_rel,
            )
        )

        resolution = any(
            item[
                "resolution"
            ]
            for item
            in candidate_rel
        )

        delta_type = (
            "EXPLICIT_RESOLUTION"
            if resolution
            else "SOURCE_EVENT_CHANGED"
        )

        for relation in candidate_rel:
            candidates.append({
                "source_family": (
                    "USFS_ALERT_INDEX"
                ),
                "source_event_id": (
                    event_id
                ),
                "campground": (
                    relation[
                        "campground"
                    ]
                ),
                "change_type": (
                    delta_type
                ),
                "reason": (
                    relation[
                        "reason"
                    ]
                ),
                "resolution": (
                    relation[
                        "resolution"
                    ]
                ),
            })

        deltas.append({
            "source_family": (
                "USFS_ALERT_INDEX"
            ),
            "source_event_id": (
                event_id
            ),
            "title": (
                event_title(
                    after
                )
            ),
            "delta_type": (
                delta_type
            ),
            "changed_semantic_fields": (
                changed_fields(
                    before.get(
                        "semantic",
                        {},
                    ),
                    after.get(
                        "semantic",
                        {},
                    ),
                )
            ),
            "notification_candidate_count": (
                len(
                    candidate_rel
                )
            ),
        })

    # -----------------------------------------------------
    # Recreation supporting evidence
    # -----------------------------------------------------

    rec_ids = (
        set(
            baseline_rec
        )
        | set(
            second_rec
        )
    )

    for event_id in sorted(
        rec_ids
    ):
        before = baseline_rec.get(
            event_id
        )

        after = second_rec.get(
            event_id
        )

        if before is None:
            deltas.append({
                "source_family": (
                    "USFS_RECREATION_PAGE"
                ),
                "source_event_id": (
                    event_id
                ),
                "campground": (
                    after.get(
                        "campground"
                    )
                ),
                "delta_type": (
                    "SUPPORTING_EVIDENCE_APPEARED"
                ),
                "notification_candidate_count": 0,
            })

            continue

        if after is None:
            deltas.append({
                "source_family": (
                    "USFS_RECREATION_PAGE"
                ),
                "source_event_id": (
                    event_id
                ),
                "campground": (
                    before.get(
                        "campground"
                    )
                ),
                "delta_type": (
                    "DATA_QUALITY_OR_SOURCE_REMOVAL"
                ),
                "notification_candidate_count": 0,
                "resolution": False,
                "safety_inference": False,
            })

            continue

        if (
            before.get(
                "semantic_fingerprint"
            )
            != after.get(
                "semantic_fingerprint"
            )
        ):
            deltas.append({
                "source_family": (
                    "USFS_RECREATION_PAGE"
                ),
                "source_event_id": (
                    event_id
                ),
                "campground": (
                    after.get(
                        "campground"
                    )
                ),
                "delta_type": (
                    "SUPPORTING_EVIDENCE_CHANGED_REVIEW"
                ),
                "changed_semantic_fields": (
                    changed_fields(
                        before.get(
                            "semantic",
                            {},
                        ),
                        after.get(
                            "semantic",
                            {},
                        ),
                    )
                ),
                "notification_candidate_count": 0,
            })

    # -----------------------------------------------------
    # NWS source events
    # -----------------------------------------------------

    nws_ids = (
        set(
            baseline_nws
        )
        | set(
            second_nws
        )
    )

    for event_id in sorted(
        nws_ids
    ):
        before = baseline_nws.get(
            event_id
        )

        after = second_nws.get(
            event_id
        )

        if before is None:
            delta_type = (
                "NWS_ALERT_APPEARED"
            )

            candidate_rel = (
                nws_candidate_relationships(
                    event_id,
                    second_nws_rel,
                )
            )

            for relation in candidate_rel:
                candidates.append({
                    "source_family": (
                        "NWS_ACTIVE_POINT_ALERT"
                    ),
                    "source_event_id": (
                        event_id
                    ),
                    "campground": (
                        relation[
                            "campground"
                        ]
                    ),
                    "change_type": (
                        delta_type
                    ),
                    "reason": (
                        relation[
                            "reason"
                        ]
                    ),
                    "resolution": False,
                })

            deltas.append({
                "source_family": (
                    "NWS_ACTIVE_POINT_ALERT"
                ),
                "source_event_id": (
                    event_id
                ),
                "title": (
                    event_title(
                        after
                    )
                ),
                "delta_type": (
                    delta_type
                ),
                "notification_candidate_count": (
                    len(
                        candidate_rel
                    )
                ),
            })

            continue

        if after is None:
            deltas.append({
                "source_family": (
                    "NWS_ACTIVE_POINT_ALERT"
                ),
                "source_event_id": (
                    event_id
                ),
                "title": (
                    event_title(
                        before
                    )
                ),
                "delta_type": (
                    "NWS_ALERT_DISAPPEARED"
                ),
                "notification_candidate_count": 0,
                "resolution": False,
                "safety_inference": False,
            })

            continue

        same_fp = (
            before.get(
                "semantic_fingerprint"
            )
            == after.get(
                "semantic_fingerprint"
            )
        )

        if same_fp:
            continue

        candidate_rel = (
            nws_candidate_relationships(
                event_id,
                second_nws_rel,
            )
        )

        for relation in candidate_rel:
            candidates.append({
                "source_family": (
                    "NWS_ACTIVE_POINT_ALERT"
                ),
                "source_event_id": (
                    event_id
                ),
                "campground": (
                    relation[
                        "campground"
                    ]
                ),
                "change_type": (
                    "NWS_ALERT_UPDATED"
                ),
                "reason": (
                    relation[
                        "reason"
                    ]
                ),
                "resolution": False,
            })

        deltas.append({
            "source_family": (
                "NWS_ACTIVE_POINT_ALERT"
            ),
            "source_event_id": (
                event_id
            ),
            "title": (
                event_title(
                    after
                )
            ),
            "delta_type": (
                "NWS_ALERT_UPDATED"
            ),
            "changed_semantic_fields": (
                changed_fields(
                    before.get(
                        "semantic",
                        {},
                    ),
                    after.get(
                        "semantic",
                        {},
                    ),
                )
            ),
            "notification_candidate_count": (
                len(
                    candidate_rel
                )
            ),
        })

    # -----------------------------------------------------
    # Candidate safety and dedup
    # -----------------------------------------------------

    candidate_keys = []

    for item in candidates:
        key = "|".join([
            item[
                "source_family"
            ],
            item[
                "source_event_id"
            ],
            item[
                "campground"
            ],
            item[
                "change_type"
            ],
        ])

        candidate_keys.append(
            key
        )

        if (
            item[
                "change_type"
            ]
            in {
                "SOURCE_EVENT_REMOVED_FROM_INDEX",
                "NWS_ALERT_DISAPPEARED",
                "SUPPORTING_EVIDENCE_CHANGED_REVIEW",
                "DATA_QUALITY_OR_SOURCE_REMOVAL",
            }
        ):
            unsafe_candidates.append(
                item
            )

    candidate_dedup_ok = (
        len(
            candidate_keys
        )
        == len(
            set(
                candidate_keys
            )
        )
    )

    unchanged_candidate_count = 0

    relation_drift = [
        item
        for item in deltas
        if item[
            "delta_type"
        ]
        == "RULE_OR_RELATIONSHIP_DRIFT"
    ]

    removal_safety_ok = all(
        item.get(
            "notification_candidate_count",
            0,
        )
        == 0
        and not item.get(
            "resolution",
            False,
        )
        and not item.get(
            "safety_inference",
            False,
        )
        for item in deltas
        if item[
            "delta_type"
        ]
        in {
            "SOURCE_EVENT_REMOVED_FROM_INDEX",
            "NWS_ALERT_DISAPPEARED",
            "DATA_QUALITY_OR_SOURCE_REMOVAL",
        }
    )

    recreation_auto_notify_ok = all(
        item.get(
            "notification_candidate_count",
            0,
        )
        == 0
        for item in deltas
        if item[
            "source_family"
        ]
        == "USFS_RECREATION_PAGE"
    )

    # The live capture harness is baseline-shaped and
    # must itself still contain zero notification state.
    second_capture_zero_notifications = (
        second.get(
            "baseline_notifications"
        )
        == []
        and second.get(
            "notification_ledger"
        )
        == []
    )

    delta_counts = Counter(
        item[
            "delta_type"
        ]
        for item in deltas
    )

    source_delta_count = len(
        deltas
    )

    notification_candidate_count = (
        len(
            candidates
        )
    )

    review_required = (
        source_delta_count > 0
    )

    invariants = {
        "baseline_hash_matches_report": (
            baseline_hash_matches_report
        ),
        "second_hash_matches_report": (
            second_hash_matches_report
        ),
        "second_capture_pass": (
            second_capture_pass
        ),
        "full_live_health": (
            full_live_health
        ),
        "frozen_inputs_same": (
            frozen_inputs_same
        ),
        "policy_same": (
            policy_same
        ),
        "identity_unique": (
            identity_unique
        ),
        "candidate_dedup_ok": (
            candidate_dedup_ok
        ),
        "unchanged_candidate_count_zero": (
            unchanged_candidate_count
            == 0
        ),
        "relationship_drift_zero": (
            len(
                relation_drift
            )
            == 0
        ),
        "removal_safety_ok": (
            removal_safety_ok
        ),
        "recreation_auto_notify_ok": (
            recreation_auto_notify_ok
        ),
        "unsafe_candidate_count_zero": (
            len(
                unsafe_candidates
            )
            == 0
        ),
        "second_capture_zero_notifications": (
            second_capture_zero_notifications
        ),
    }

    all_invariants = all(
        invariants.values()
    )

    if all_invariants:
        decision = (
            "PASS_CONTROLLED_SECOND_OBSERVATION"
        )
        rc = 0

    else:
        decision = (
            "HOLD_CONTROLLED_SECOND_OBSERVATION"
        )
        rc = 2

    report = {
        "validated_at_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "baseline": {
            "path": str(
                baseline_path
            ),
            "sha256": (
                sha256_bytes(
                    baseline_raw
                )
            ),
            "created_at_utc": (
                baseline.get(
                    "created_at_utc"
                )
            ),
        },
        "second_observation": {
            "path": str(
                second_path
            ),
            "sha256": (
                sha256_bytes(
                    second_raw
                )
            ),
            "created_at_utc": (
                second.get(
                    "created_at_utc"
                )
            ),
        },
        "invariants": (
            invariants
        ),
        "summary": {
            "source_delta_count": (
                source_delta_count
            ),
            "delta_counts": dict(
                delta_counts
            ),
            "notification_candidate_count": (
                notification_candidate_count
            ),
            "review_required": (
                review_required
            ),
            "relationship_drift_count": (
                len(
                    relation_drift
                )
            ),
            "unsafe_candidate_count": (
                len(
                    unsafe_candidates
                )
            ),
            "baseline_usfs_alert_count": (
                len(
                    baseline_alerts
                )
            ),
            "second_usfs_alert_count": (
                len(
                    second_alerts
                )
            ),
            "baseline_recreation_count": (
                len(
                    baseline_rec
                )
            ),
            "second_recreation_count": (
                len(
                    second_rec
                )
            ),
            "baseline_nws_event_count": (
                len(
                    baseline_nws
                )
            ),
            "second_nws_event_count": (
                len(
                    second_nws
                )
            ),
        },
        "deltas": (
            deltas
        ),
        "notification_candidates": (
            candidates
        ),
        "unsafe_candidates": (
            unsafe_candidates
        ),
        "duplicate_identity_evidence": (
            duplicate_ids
        ),
        "decision": (
            decision
        ),
        "next_scope_if_accepted": (
            "If this controlled second observation is "
            "mechanically PASS and any reported live deltas "
            "are human-reviewed as genuine/non-unsafe, "
            "authorize the prospective unattended validation "
            "harness. Do not reinterpret historical baseline "
            "events as new notifications."
        ),
    }

    output_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        "CampReady Phase 3B-4:",
        decision,
    )

    print(
        "Second live capture:",
        second_capture_pass,
    )

    print(
        "Full live health:",
        full_live_health,
    )

    print(
        "Frozen inputs unchanged:",
        frozen_inputs_same,
    )

    print(
        "Source deltas:",
        source_delta_count,
    )

    print(
        "Delta types:",
        dict(
            delta_counts
        ),
    )

    print(
        "Notification candidates:",
        notification_candidate_count,
    )

    print(
        "Relationship drift:",
        len(
            relation_drift
        ),
    )

    print(
        "Unsafe candidates:",
        len(
            unsafe_candidates
        ),
    )

    print(
        "Review required:",
        review_required,
    )

    print()

    for key, value in (
        invariants.items()
    ):
        print(
            "PASS"
            if value
            else "FAIL",
            key,
        )

    print()

    for delta in deltas:
        print(
            "DELTA",
            delta[
                "source_family"
            ],
            delta[
                "delta_type"
            ],
            delta.get(
                "title"
            )
            or delta.get(
                "campground"
            )
            or delta[
                "source_event_id"
            ],
            "candidate_count="
            + str(
                delta.get(
                    "notification_candidate_count",
                    0,
                )
            ),
        )

    return rc


raise SystemExit(
    main()
)
