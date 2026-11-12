import copy
import hashlib
import importlib.util
import json
import pathlib
import socket
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE5 = ROOT / "campready-phase5"
spec = importlib.util.spec_from_file_location("phase5_read_model", PHASE5 / "read_model.py")
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)


class ReviewModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((PHASE5 / "contracts/review-item.schema.json").read_text())
        cls.document = json.loads((PHASE5 / "fixtures/review_model_cases.json").read_text())
        cls.records = cls.document["records"]
        cls.by_id = {x["fixture_id"]: x for x in cls.records}

    def setUp(self):
        self.old_socket = socket.socket
        socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))

    def tearDown(self):
        socket.socket = self.old_socket

    def test_schema_contract(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["$id"], rm.SCHEMA_VERSION)
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(self.schema["properties"]["notification_eligible"]["const"], False)
        self.assertEqual(self.schema["properties"]["reviewer_status"]["const"], "NEW")
        self.assertIn("required", self.schema)
        self.assertEqual(self.schema["properties"]["reviewer_disposition"]["type"], "null")
        self.assertEqual(set(self.schema["properties"]["parser_status"]["enum"]), {"SUCCESS","FAILED","NOT_RUN","NOT_APPLICABLE"})
        self.assertIn("00:00", self.schema["$defs"]["utcTimestamp"]["pattern"])

    def test_cohort_exactly_ten_unique_non_usace(self):
        cohort = rm.load_phase5_config(PHASE5 / "config.json")["cohort"]
        self.assertEqual(len(cohort), 10)
        self.assertEqual(len({x["site_id"] for x in cohort}), 10)
        self.assertFalse(any("USACE" in x["authority_family"] for x in cohort))

    def test_golden_identity_vectors(self):
        semantic = rm.semantic_review_item_id("event-α", "rel-1", None, "sha256:abc", "APPEARANCE")
        quality = rm.quality_review_item_id("run-1", "request-1", "ALERT_DETAIL", "https://example.gov/a", "FINAL_RETRIEVAL_FAILURE")
        self.assertEqual(semantic, "cr5ri1-56b244c4926514e8687ce7cd28881e48c315cc5b3f2991d199303ecdcb28e4cd")
        self.assertEqual(quality, "cr5ri1-cd3f1b40aff1a50431894805f42f80e4fcbcd335f7e9bca6565b158f0dfc0bb8")
        self.assertNotEqual(semantic, quality)

    def test_identity_determinism_and_volatile_time(self):
        a = rm.derive_semantic_review_item(self.by_id["F1"])
        changed = dict(self.by_id["F1"], detected_at="2099-01-01T00:00:00Z")
        b = rm.derive_semantic_review_item(changed)
        self.assertEqual(a["review_item_id"], b["review_item_id"])
        self.assertEqual(a["review_item_id"], rm.derive_semantic_review_item(dict(reversed(list(self.by_id["F1"].items()))))["review_item_id"])

    def test_identity_changes_with_relationship_and_transition(self):
        base = self.by_id["F1"]
        a = rm.derive_semantic_review_item(base)["review_item_id"]
        self.assertNotEqual(a, rm.derive_semantic_review_item(dict(base, affected_relationship_id="other"))["review_item_id"])
        self.assertNotEqual(a, rm.derive_semantic_review_item(dict(base, lifecycle_classification="MATERIAL_UPDATE"))["review_item_id"])

    def test_duplicate_quality_request_same_id(self):
        self.assertEqual(rm.derive_quality_review_item(self.by_id["F6"])["review_item_id"], rm.derive_quality_review_item(self.by_id["F10"])["review_item_id"])

    def test_projection_and_exclusion(self):
        items = rm.derive_review_items_for_cohort(self.records)
        self.assertEqual(len(items), 9)
        self.assertNotIn("wv-bear-heaven", {x["site_id"] for x in items})
        f1 = rm.derive_semantic_review_item(self.by_id["F1"])
        self.assertEqual(f1["event_category"], self.by_id["F1"]["event_category"])
        self.assertEqual(f1["lifecycle_classification"], "APPEARANCE")

    def test_quality_has_no_semantic_claim_and_retains_prior(self):
        f4 = rm.derive_quality_review_item(self.by_id["F4"])
        self.assertIsNone(f4["event_category"])
        self.assertEqual(f4["lifecycle_classification"], "NONE")
        self.assertEqual(f4["prior_accepted_state_ref"], f4["current_accepted_state_ref"])
        f6 = rm.derive_quality_review_item(self.by_id["F6"])
        self.assertIsNone(f6["prior_accepted_state_ref"])
        self.assertIsNone(f6["current_accepted_state_ref"])

    def test_alert_detail_guard_and_rescue(self):
        guarded = rm.derive_quality_review_item(self.by_id["F4"])
        rescued = rm.derive_quality_review_item(self.by_id["F5"])
        self.assertTrue(guarded["alert_detail_guard_required"])
        self.assertTrue(set(rm.GUARD_PROHIBITIONS).issubset(guarded["interpretation_prohibitions"]))
        self.assertFalse(rescued["alert_detail_guard_required"])
        self.assertTrue(rescued["retry_telemetry_summary"]["rescued"])

    def test_quality_unknown_and_evidence_labels(self):
        f3 = rm.derive_semantic_review_item(self.by_id["F3"])
        f7 = rm.derive_quality_review_item(self.by_id["F7"])
        f8 = rm.derive_quality_review_item(self.by_id["F8"])
        self.assertEqual(f3["source_evidence"]["representation"], "VERBATIM")
        self.assertEqual(f7["source_evidence"]["representation"], "SUMMARY")
        self.assertTrue(f7["unknown_state"]["unknown"])
        self.assertEqual(f8["source_evidence"]["representation"], "UNAVAILABLE")
        self.assertEqual(f8["source_title"]["status"], "UNKNOWN")

    def test_provenance_resolves_and_state_refs_are_persisted_shape(self):
        for record in self.records:
            ref = record["provenance_record_ref"].split("#", 1)[0]
            self.assertTrue((ROOT / ref).exists(), ref)
        f1 = rm.derive_semantic_review_item(self.by_id["F1"])
        queue = json.loads((ROOT / "campready-phase4b/review-queue/20260922T234016Z.json").read_text())
        self.assertEqual(f1["current_accepted_state_ref"], "sha256:" + queue[1]["after"])
        report = json.loads((ROOT / "campready-phase4b/runs/20260929T194019Z/run-report.json").read_text())
        f3 = rm.derive_semantic_review_item(self.by_id["F3"])
        f4 = rm.derive_quality_review_item(self.by_id["F4"])
        self.assertEqual(f3["prior_accepted_state_ref"], "sha256:" + report["prior_state_sha256"])
        self.assertEqual(f3["current_accepted_state_ref"], "sha256:" + report["accepted_state_sha256"])
        self.assertEqual(f4["prior_accepted_state_ref"], "sha256:" + report["prior_state_sha256"])

    def test_defaults_and_validation_fail_closed(self):
        item = rm.derive_semantic_review_item(self.by_id["F1"])
        self.assertEqual(item["reviewer_status"], "NEW")
        self.assertIsNone(item["reviewer_disposition"])
        self.assertFalse(item["reviewer_acknowledgement"]["acknowledged"])
        self.assertFalse(item["notification_eligible"])
        bad = dict(item, notification_eligible=True)
        with self.assertRaises(ValueError): rm.validate_review_item_contract(bad)
        with self.assertRaises(ValueError): rm.validate_review_item_contract(dict(item, unexpected=True))
        with self.assertRaises(ValueError): rm.derive_semantic_review_item(dict(self.by_id["F1"], official_source_url="http://invalid"))
        with self.assertRaises(ValueError): rm.validate_review_item_contract(dict(item, retrieval_status="MAYBE"))

    def test_utc_required(self):
        with self.assertRaises(ValueError):
            rm.derive_semantic_review_item(dict(self.by_id["F1"], detected_at="2026-01-01T00:00:00-05:00"))

    def test_fixture_provenance_labels(self):
        self.assertEqual({x["fixture_origin"] for x in self.records}, {"OBSERVED", "DERIVED_CONTRACT_FIXTURE"})
        observed = [x for x in self.records if x["fixture_origin"] == "OBSERVED"]
        self.assertTrue(all(x.get("provenance_run_id") and x.get("reason_selected") for x in observed))

    def test_import_and_derivation_do_not_write_canonical_inputs(self):
        paths = [ROOT/"campready-phase4b/state/current.json", ROOT/"campready-phase4b/history/history.jsonl",
                 ROOT/"campready-phase4b/config.json"]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        rm.derive_review_items_for_run(PHASE5 / "fixtures/review_model_cases.json")
        after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
