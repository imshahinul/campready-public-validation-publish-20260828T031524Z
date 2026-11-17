import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PHASE5 = Path(__file__).resolve().parents[1]
PILOT = PHASE5 / "pilot"
sys.path.insert(0, str(PILOT)); sys.path.insert(0, str(PHASE5))
from pilot_control import initial_state
from pilot_operator import PilotOperator
from pilot_store import (canonical_bytes, initialize_evidence_root, live_counts,
                         start_pilot, validate_observation)


class Phase55IPilotOperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        initialize_evidence_root(self.root, allow_test_root=True)
        (self.root/"state"/"pilot_state.json").write_bytes(canonical_bytes(initial_state())+b"\n")
        start_pilot(self.root, owner_authorized=True, verified_role_binding_digest="a"*64,
                    allow_test_root=True, clock=lambda:datetime(2026,11,17,5,10,tzinfo=timezone.utc))
        self.operator = PilotOperator(self.root, allow_test_root=True)
        self.run = self.root/"normalized.json"; self.write_run()

    def tearDown(self): self.temp.cleanup()

    def record(self, **changes):
        value = {"item_kind":"semantic", "site_id":"andrews-cove", "campground_name":"Andrews Cove Campground",
            "authority_id":"USFS", "authority_name":"USDA Forest Service", "source_family":"USFS_HTML",
            "source_role":"CAMPGROUND_PAGE", "official_source_url":"https://www.fs.usda.gov/example",
            "source_title":"Prospective source", "source_evidence_summary":"Synthetic isolated prospective evidence.",
            "source_evidence_digest":None, "provenance_run_id":"20261117T051100Z",
            "provenance_record_ref":"campready-phase4b/runs/20261117T051100Z/capture.json#semantic",
            "detected_at":"2026-11-17T05:11:00Z", "retrieved_at":"2026-11-17T05:11:00Z",
            "retrieval_status":"SUCCESS", "parser_status":"SUCCESS", "relevance_status":"RELEVANT",
            "retry_telemetry_summary":None, "upstream_event_id":"synthetic-prospective-event",
            "event_category":"CAMPGROUND_CONDITION", "lifecycle_classification":"APPEARANCE",
            "prior_accepted_state_ref":None, "current_accepted_state_ref":"sha256:"+"b"*64}
        value.update(changes); return value

    def write_run(self, records=None, **changes):
        value={"schema_version":"campready.phase5.normalized_run_evidence.v1", "run_id":"20261117T051100Z",
               "generated_at":"2026-11-17T05:11:01Z", "source_evidence_ref":"synthetic/normalized-run",
               "dry_run":False, "test_fixture":False, "records":records if records is not None else [self.record()]}
        value.update(changes); self.run.write_text(json.dumps(value)); return value

    def prepare_accept(self):
        prepared=self.operator.prepare_review_items(self.run)
        item=prepared["review_items"][0]
        intake=self.operator.accept_review_item(prepared, accepted_at="2026-11-17T05:12:00Z")
        return prepared,item,intake

    def action(self, **changes):
        value={"reviewer_id":"R-AL", "to_status":"REVIEWED_VALID", "disposition":"REVIEWED_VALID",
               "reason_code":None, "note":None, "timestamp":"2026-11-17T05:13:00Z"}
        value.update(changes); return value

    def test_synthetic_end_to_end_and_retries_are_idempotent(self):
        prepared,item,intake=self.prepare_accept()
        self.assertEqual(intake["intake_sequence"],1)
        packet=self.operator.primary_review_packet(intake,item)
        self.assertEqual(packet["required_reviewer"],"R-AL"); self.assertIsNone(packet["reviewer_disposition"])
        annotation=self.operator.record_primary_review(intake,item,self.action())
        self.assertEqual(annotation,self.operator.record_primary_review(intake,item,self.action()))
        observation=self.operator.assemble_observation(intake,item,annotation); validate_observation(observation)
        with patch("pilot_operator.evaluate_stop_hold", wraps=__import__("pilot_control").evaluate_stop_hold) as stop, \
             patch("pilot_operator.evaluate_window", wraps=__import__("pilot_control").evaluate_window) as window:
            result=self.operator.append_observation(observation,evaluated_at="2026-11-17T05:14:00Z")
            self.assertTrue(stop.called); self.assertTrue(window.called)
        retry=self.operator.append_observation(observation,evaluated_at="2026-11-17T05:14:00Z")
        self.assertEqual(result["stop_hold"]["result"],"CONTINUE"); self.assertEqual(result["window"]["result"],"CONTINUE")
        self.assertEqual(retry["result"],"OBSERVATION_ALREADY_APPENDED")
        self.assertEqual(live_counts(self.root,allow_test_root=True)["valid_observation_count"],1)
        self.assertEqual(self.operator.status(evaluated_at="2026-11-17T05:14:00Z")["next_intake_sequence"],2)

    def test_zero_derived_creates_nothing(self):
        self.write_run([self.record(site_id="outside-cohort")])
        # Structural validation rejects non-cohort rather than silently importing it.
        with self.assertRaisesRegex(ValueError,"NON_PRODUCTION_SOURCE_REJECTED"): self.operator.prepare_review_items(self.run)
        self.write_run([])
        self.assertEqual(self.operator.prepare_review_items(self.run)["result"],"NO_REVIEW_ITEMS_FOR_RUN")
        self.assertEqual(set(live_counts(self.root,allow_test_root=True).values()),{0})

    def test_historical_fixture_and_duplicate_source_rejected(self):
        historical=self.root/"campready-phase4b/runs/20261104T024017Z/run-report.json"
        historical.parent.mkdir(parents=True); historical.write_text("{}")
        with self.assertRaisesRegex(ValueError,"HISTORICAL_SOURCE_REJECTED"): self.operator.prepare_review_items(historical)
        self.write_run(generated_at="2026-11-17T05:00:00Z")
        with self.assertRaisesRegex(ValueError,"HISTORICAL_SOURCE_REJECTED"): self.operator.prepare_review_items(self.run)
        self.write_run(); prepared,item,intake=self.prepare_accept()
        with self.assertRaisesRegex(ValueError,"DUPLICATE_SOURCE_IMPORT_REJECTED"): self.operator.prepare_review_items(self.run)
        with self.assertRaisesRegex(ValueError,"DUPLICATE_ID_REJECTED"):
            self.operator.intakes.accept({key:value for key,value in intake.items()
                                          if key not in {"intake_sequence","control_sample_selected"}})

    def test_intake_before_review_and_review_before_append_resume(self):
        prepared,item,intake=self.prepare_accept()
        resumed=self.operator.accept_review_item(prepared,accepted_at="2026-11-17T05:12:00Z")
        self.assertEqual(resumed,intake)
        status=self.operator.status(evaluated_at="2026-11-17T05:12:30Z")
        self.assertEqual(status["pending_accepted_intake_awaiting_primary_review"],[intake["intake_id"]])
        annotation=self.operator.record_primary_review(intake,item,self.action())
        status=self.operator.status(evaluated_at="2026-11-17T05:13:30Z")
        self.assertEqual(status["pending_completed_primary_review_awaiting_observation_append"],[intake["intake_id"]])
        self.assertEqual(self.operator.record_primary_review(intake,item,self.action()),annotation)

    def test_human_action_required_and_wrong_reviewer_rejected(self):
        _,item,intake=self.prepare_accept()
        with self.assertRaisesRegex(ValueError,"CALLER_SUPPLIED_HUMAN_FIELDS_REQUIRED"):
            self.operator.record_primary_review(intake,item,{})
        with self.assertRaisesRegex(ValueError,"PRIMARY_REVIEWER_REQUIRED"):
            self.operator.record_primary_review(intake,item,self.action(reviewer_id="R-BL"))
        with self.assertRaisesRegex(ValueError,"CALLER_SUPPLIED_HUMAN_FIELDS_REQUIRED"):
            self.operator.record_primary_review(intake,item,self.action(disposition=None))

    def test_multiple_items_preserve_order_and_require_selection(self):
        second=self.record(upstream_event_id="second",provenance_record_ref="campready-phase4b/runs/20261117T051100Z/capture.json#second")
        self.write_run([self.record(),second])
        prepared=self.operator.prepare_review_items(self.run)
        self.assertTrue(prepared["selection_required"])
        with self.assertRaisesRegex(ValueError,"EXPLICIT_ITEM_SELECTION_REQUIRED"):
            self.operator.accept_review_item(prepared,accepted_at="2026-11-17T05:12:00Z")
        wanted=prepared["review_items"][1]["review_item_id"]
        self.assertEqual(self.operator.accept_review_item(prepared,wanted,accepted_at="2026-11-17T05:12:00Z")["review_item_id"],wanted)

    def test_sequence_five_secondary_obligation(self):
        fifth=None; fifth_item=None
        for n in range(5):
            run=self.root/f"run-{n}.json"; self.run=run
            rid=f"20261117T05110{n}Z"
            rec=self.record(upstream_event_id=f"event-{n}",provenance_run_id=rid,
                            provenance_record_ref=f"campready-phase4b/runs/{rid}/capture.json#semantic")
            self.write_run([rec],run_id=rid,source_evidence_ref=f"synthetic/run-{n}")
            prepared=self.operator.prepare_review_items(run); fifth_item=prepared["review_items"][0]
            fifth=self.operator.accept_review_item(prepared,accepted_at="2026-11-17T05:12:00Z")
        self.assertEqual(fifth["intake_sequence"],5); self.assertTrue(fifth["control_sample_selected"])
        packet=self.operator.primary_review_packet(fifth,fifth_item); self.assertTrue(packet["secondary_review_required"])
        annotation=self.operator.record_primary_review(fifth,fifth_item,self.action(timestamp="2026-11-17T05:13:05Z"))
        with self.assertRaisesRegex(ValueError,"SECONDARY_REVIEW_OUTSTANDING"):
            self.operator.assemble_observation(fifth,fifth_item,annotation)
        self.assertIn(fifth["intake_id"],self.operator.status(evaluated_at="2026-11-17T05:14:00Z")["pending_secondary_review_obligations"])

    def test_y507_structured_derivation(self):
        self.write_run([self.record(relevance_status="UNKNOWN")])
        prepared,item,intake=self.prepare_accept()
        annotation=self.operator.record_primary_review(intake,item,self.action(to_status="DATA_QUALITY_HOLD",disposition="DATA_QUALITY_HOLD",reason_code="HUMAN_REASON",note="Human supplied hold."))
        observation=self.operator.assemble_observation(intake,item,annotation)
        self.assertTrue(observation["unknown_none_dq_hold_conflict"])


if __name__ == "__main__": unittest.main()
