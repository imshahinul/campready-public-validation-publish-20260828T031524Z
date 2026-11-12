import copy
import socket
import unittest
import urllib.request
from unittest.mock import patch

from dashboard import derive_dashboard, render_dashboard

START, END, GENERATED = "2026-11-09T00:00:00Z", "2026-11-09T06:00:00Z", "2026-11-09T06:01:00Z"


def item(number, status="NEW", **changes):
    value = {"review_item_id":f"item-{number}", "detected_at":f"2026-11-09T0{number}:00:00Z",
             "reviewer_status":status, "parser_status":"SUCCESS", "retrieval_status":"SUCCESS",
             "data_quality_state":{"classification":"NONE"}, "unknown_state":{"unknown":False},
             "alert_detail_guard_required":False}
    value.update(changes); return value


def annotation(number, status, acknowledged=False, acknowledged_at=None):
    return {"review_item_id":f"item-{number}", "annotation_version":1, "to_status":status,
            "acknowledged":acknowledged, "acknowledged_at":acknowledged_at}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        deny = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network denied"))
        self.patches = [patch.object(socket, "socket", deny), patch.object(socket, "create_connection", deny), patch.object(urllib.request, "urlopen", deny)]
        for entry in self.patches: entry.start()
        self.base = dict(period_start=START, period_end=END, generated_at=GENERATED, cadence_seconds=3600)

    def tearDown(self):
        for entry in reversed(self.patches): entry.stop()

    def test_scheduler_perfect_isolated_consecutive_duplicate_ambiguous(self):
        perfect = [{"slot":f"2026-11-09T0{hour}:00:00Z"} for hour in range(6)]
        self.assertEqual(derive_dashboard(**self.base, scheduler_observations=perfect)["SCHEDULER"]["coverage_ratio"], 1)
        isolated = perfect[:2] + perfect[3:]
        self.assertEqual(derive_dashboard(**self.base, scheduler_observations=isolated)["SCHEDULER"]["longest_missing_streak"], 1)
        consecutive = perfect[:2] + perfect[4:]
        scheduler = derive_dashboard(**self.base, scheduler_observations=consecutive)["SCHEDULER"]
        self.assertEqual((scheduler["expected_slots"], scheduler["observed_slots"], scheduler["missing_slots"], scheduler["longest_missing_streak"]), (6,4,2,2))
        unusual = perfect + [{"slot":"2026-11-09T00:00:00Z"}, {"slot":"2026-11-09T01:00:00Z", "ambiguous":True}]
        scheduler = derive_dashboard(**self.base, scheduler_observations=unusual)["SCHEDULER"]
        self.assertEqual((scheduler["duplicate_slots"], scheduler["ambiguous_slots"]), (2,1))

    def test_alert_detail_visible_with_healthy_aggregate_and_rates_have_raw_counts(self):
        records = [{"authority_id":"USFS", "source_role":"CAMPGROUND_PAGE", "first_attempt_success":True, "eventual_success":True} for _ in range(99)]
        records.append({"authority_id":"USFS", "source_role":"ALERT_DETAIL", "first_attempt_success":False, "eventual_success":False,
                        "retry_eligible":True, "retry_attempted":True, "retry_final_failed":True})
        retrieval = derive_dashboard(**self.base, retrieval_records=records)["RETRIEVAL"]
        self.assertEqual(retrieval["aggregate_eventual"]["reliability"], .99)
        detail = retrieval["usfs_by_source_role"]["ALERT_DETAIL"]["aggregate_eventual"]
        self.assertEqual((detail["successes"], detail["attempts"], detail["failures"], detail["reliability"]), (0,1,1,0))
        self.assertEqual(retrieval["retry"]["retry_rescue_denominator"], 1)

    def test_semantic_quality_safety_exact_and_rescued_detail_not_guarded(self):
        semantic = [{"candidate":True, "legitimate":True, "retrieval_recovery_churn":True, "cross_scope_fanout_count":3,
                     "unresolved":True, "unresolved_reason":"PERSISTED_UNKNOWN"}]
        items = [item(0, parser_status="FAILED", retrieval_status="FINAL_FAILED", alert_detail_guard_required=True,
                      data_quality_state={"classification":"DATA_QUALITY"}, unknown_state={"unknown":True}),
                 item(1, retrieval_status="FAILED_FIRST_ATTEMPT_RESCUED", alert_detail_guard_required=True)]
        safety = [{"unsafe_candidate_count":2, "real_notification_count":1, "false_lifecycle_claim_count":3,
                   "fail_closed_retention_violation_count":4}]
        result = derive_dashboard(**self.base, semantic_records=semantic, review_items=items, safety_records=safety)
        self.assertEqual(result["SEMANTIC"]["retrieval_recovery_churn_count"], 1)
        self.assertEqual(result["SEMANTIC"]["cross_scope_fanout_count"], 3)
        self.assertEqual(result["QUALITY"]["guarded_alert_detail"]["total"], 1)
        self.assertEqual(result["QUALITY"]["data_quality_item_count"], 1)
        self.assertEqual(result["QUALITY"]["unknown_item_count"], 1)
        self.assertEqual(result["SAFETY"]["unsafe_candidate_count"], 2)
        self.assertEqual(result["SAFETY"]["real_notification_count"], 1)

    def test_every_review_state_latency_and_unacknowledged_not_zero(self):
        statuses = ("NEW", "REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP", "ACKNOWLEDGED")
        items = [item(index) for index in range(6)]
        annotations = [annotation(index, status, status == "ACKNOWLEDGED", "2026-11-09T05:30:00Z" if status == "ACKNOWLEDGED" else None)
                       for index, status in enumerate(statuses) if status != "NEW"]
        review = derive_dashboard(**self.base, review_items=items, annotations=annotations)["REVIEW"]
        for field in ("new_items", "reviewed_valid", "reviewed_not_actionable", "data_quality_hold", "needs_followup", "acknowledged_items"):
            self.assertEqual(review[field], 1)
        self.assertEqual(review["acknowledgement_latency"]["count"], 1)
        self.assertIsNone(review["acknowledgement_latency"]["median_seconds"])
        self.assertEqual(review["outstanding_items"], 5)

    def test_nearest_rank_latency_negative_fails_closed(self):
        items = [item(0), item(1)]
        annotations = [annotation(0, "ACKNOWLEDGED", True, "2026-11-09T00:01:00Z"),
                       annotation(1, "ACKNOWLEDGED", True, "2026-11-09T01:10:00Z")]
        latency = derive_dashboard(**self.base, review_items=items, annotations=annotations)["REVIEW"]["acknowledgement_latency"]
        self.assertEqual((latency["median_seconds"], latency["p90_seconds"], latency["maximum_seconds"]), (60,600,600))
        with self.assertRaises(ValueError):
            derive_dashboard(**self.base, review_items=[item(1)], annotations=[annotation(1, "ACKNOWLEDGED", True, "2026-11-09T00:00:00Z")])

    def test_unknown_propagation_period_freshness_no_score_no_mutation_and_render(self):
        source = [item(0)]; before = copy.deepcopy(source)
        result = derive_dashboard(**self.base, review_items=source)
        self.assertEqual(source, before)
        self.assertEqual(result["MAINTENANCE"]["measurement_status"], "UNKNOWN")
        self.assertEqual(result["ECONOMICS"]["measurement_status"], "NOT_MEASURABLE")
        self.assertIsNone(result["ECONOMICS"]["infrastructure_subtotal"])
        self.assertNotIn("health_score", str(result).lower())
        for section in ("SCHEDULER","RETRIEVAL","SEMANTIC","QUALITY","SAFETY","REVIEW","MAINTENANCE","ECONOMICS"):
            self.assertEqual(result[section]["period_start"], START)
            self.assertEqual(result[section]["freshness_timestamp"], GENERATED)
        rendered = render_dashboard(result)
        self.assertIn("[SCHEDULER]", rendered); self.assertIn("[ECONOMICS]", rendered)
        with self.assertRaises(ValueError): derive_dashboard(**{**self.base, "period_end":START})


if __name__ == "__main__": unittest.main()
