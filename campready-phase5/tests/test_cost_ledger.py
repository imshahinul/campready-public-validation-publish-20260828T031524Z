import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from cost_ledger import CostLedger, cost_id, economics_report, validate_record


def record(**changes):
    value = {"schema_version":"campready.phase5.cost.v1", "cost_id":"",
             "period_start":"2026-11-01T00:00:00Z", "period_end":"2026-12-01T00:00:00Z",
             "cost_category":"SCHEDULER_SERVICE", "provider":"documented-provider",
             "description":"Documented monthly scheduler rate", "quantity":1, "unit":"month",
             "unit_cost":5.0, "amount":5.0, "currency":"USD", "evidence_type":"PUBLISHED_RATE",
             "evidence_ref":"rate-card-v1", "measurement_status":"ESTIMATED_FROM_DOCUMENTED_RATE",
             "infrastructure_cost":True, "projection_basis":"one documented monthly unit",
             "monthly_projected_amount":5.0}
    value.update(changes); value["cost_id"] = cost_id(value); return value


class CostLedgerTests(unittest.TestCase):
    def setUp(self):
        deny = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network denied"))
        self.patches = [patch.object(socket, "socket", deny), patch.object(socket, "create_connection", deny), patch.object(urllib.request, "urlopen", deny)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()

    def test_golden_vector_schema_null_and_ordering(self):
        value = record()
        self.assertEqual(value["cost_id"], "cr5cost1-3e840eec15c90b5cf66075a2562b05ee8c843a602a346d5f4ef1b38cd93e0606")
        validate_record(value)
        unknown = record(quantity=None, unit=None, unit_cost=None, amount=None, currency=None,
                         evidence_type="NOT_AVAILABLE", evidence_ref=None, measurement_status="NOT_MEASURABLE",
                         projection_basis=None, monthly_projected_amount=None)
        validate_record(unknown)
        self.assertIsNone(unknown["amount"])
        with self.assertRaises(ValueError): validate_record(record(period_end="2026-11-01T00:00:00Z"))
        bad = record(); bad["unexpected"] = 1
        with self.assertRaises(ValueError): validate_record(bad)

    def test_append_reopen_idempotency_and_malformed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cost.jsonl"; ledger = CostLedger(path); value = record()
            ledger.append(value); before = path.read_bytes(); ledger.append(value)
            self.assertEqual(path.read_bytes(), before); self.assertEqual(CostLedger(path).read_all(), [value])
            path.write_text("[]\n", encoding="utf-8")
            with self.assertRaises(ValueError): ledger.read_all()
            with self.assertRaises(ValueError): ledger.append(value)

    def test_economics_itemization_projection_and_reviewer_exclusion(self):
        reviewer = record(cost_category="REVIEWER_OPERATIONS", provider="internal", description="Reviewer time",
                          amount=20.0, unit_cost=20.0, evidence_type="INTERNAL_RATE", infrastructure_cost=False,
                          monthly_projected_amount=None, projection_basis=None)
        report = economics_report([record(), reviewer], "2026-11-01T00:00:00Z", "2026-12-01T00:00:00Z")
        self.assertEqual(report["infrastructure_subtotal"], 5.0)
        self.assertEqual(report["reviewer_operations_subtotal"], 20.0)
        self.assertEqual(report["monthly_projected_infrastructure_cost"], 5.0)
        self.assertEqual(report["target_comparison"], "WITHIN_ORIGINAL_TARGET")
        unknown = record(quantity=None, unit=None, unit_cost=None, amount=None, currency=None, evidence_type="NOT_AVAILABLE",
                         evidence_ref=None, measurement_status="NOT_MEASURABLE", projection_basis=None, monthly_projected_amount=None)
        unknown_report = economics_report([unknown], "2026-11-01T00:00:00Z", "2026-12-01T00:00:00Z")
        self.assertIsNone(unknown_report["monthly_projected_infrastructure_cost"])
        self.assertIsNone(unknown_report["infrastructure_subtotal"])
        self.assertEqual(unknown_report["target_comparison"], "UNKNOWN")

    def test_currency_projection_and_reviewer_rules(self):
        with self.assertRaises(ValueError): validate_record(record(currency=None))
        with self.assertRaises(ValueError): validate_record(record(cost_category="REVIEWER_OPERATIONS", infrastructure_cost=True))
        with self.assertRaises(ValueError): validate_record(record(monthly_projected_amount=5.0, projection_basis=None))


if __name__ == "__main__": unittest.main()
