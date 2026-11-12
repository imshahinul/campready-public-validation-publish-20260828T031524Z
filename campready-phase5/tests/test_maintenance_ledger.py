import copy
import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from maintenance_ledger import MaintenanceLedger, maintenance_id, maintenance_report, validate_record


def record(**changes):
    value = {"schema_version":"campready.phase5.maintenance.v1", "maintenance_id":"",
             "timestamp":"2026-11-10T12:00:00Z", "work_class":"OPERATIONAL_MAINTENANCE",
             "category":"SCHEDULER_INFRASTRUCTURE", "reason":"Controlled scheduler test",
             "human_minutes":15, "intervention_required":True, "source_authority":"INTERNAL",
             "outcome":"RESOLVED", "actor_id":"operator-1", "evidence_ref":"DERIVED_MEASUREMENT_FIXTURE"}
    value.update(changes)
    value["maintenance_id"] = maintenance_id(value)
    return value


class MaintenanceLedgerTests(unittest.TestCase):
    def setUp(self):
        deny = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network denied"))
        self.patches = [patch.object(socket, "socket", deny), patch.object(socket, "create_connection", deny), patch.object(urllib.request, "urlopen", deny)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()

    def test_golden_vector_schema_and_explicit_zero(self):
        value = record()
        self.assertEqual(value["maintenance_id"], "cr5mnt1-acefcb5ddf8f5cbd07d54e30bf8b283a2444a15096e6e6495a0cb19a47fe9663")
        validate_record(value)
        validate_record(record(human_minutes=0))
        for minutes in (-1, 1.5, True, None):
            with self.assertRaises(ValueError): validate_record(record(human_minutes=minutes))
        bad = record(); bad["extra"] = 1
        with self.assertRaises(ValueError): validate_record(bad)
        with self.assertRaises(KeyError): maintenance_id({k:v for k,v in value.items() if k != "human_minutes"})

    def test_append_idempotency_immutability_reopen_and_malformed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "maintenance.jsonl"
            ledger = MaintenanceLedger(path); first = record(); second = record(timestamp="2026-11-11T12:00:00Z", reason="Review operations")
            ledger.append(first); before = path.read_bytes(); ledger.append(first)
            self.assertEqual(path.read_bytes(), before)
            ledger.append(second); self.assertTrue(path.read_bytes().startswith(before))
            self.assertEqual(MaintenanceLedger(path).read_all(), [first, second])
            path.write_text("{bad}\n", encoding="utf-8")
            with self.assertRaises(ValueError): ledger.read_all()
            with self.assertRaises(ValueError): ledger.append(first)

    def test_reporting_separation_weekly_unknown_and_partial(self):
        start, end = "2026-11-09T00:00:00Z", "2026-11-16T00:00:00Z"
        self.assertEqual(maintenance_report([], start, end)["measurement_status"], "UNKNOWN")
        rows = [record(human_minutes=20), record(timestamp="2026-11-11T12:00:00Z", work_class="DEVELOPMENT_WORK", category="PARSER", reason="Develop parser", human_minutes=40)]
        report = maintenance_report(rows, start, end)
        self.assertEqual((report["operational_minutes"], report["development_minutes"]), (20, 40))
        self.assertEqual(report["measurement_status"], "PARTIAL")
        self.assertEqual(maintenance_report(rows, start, end, True)["measurement_status"], "COMPLETE")
        self.assertEqual(maintenance_report(rows, "2026-11-16T00:00:00Z", "2026-11-23T00:00:00Z")["record_count"], 0)

    def test_enums_and_identity_enforced(self):
        for field, value in (("work_class", "OPS"), ("category", "UNKNOWN"), ("outcome", "DONE")):
            with self.assertRaises(ValueError): validate_record(record(**{field:value}))
        bad = record(); bad["reason"] = "changed"
        with self.assertRaises(ValueError): validate_record(bad)


if __name__ == "__main__": unittest.main()
