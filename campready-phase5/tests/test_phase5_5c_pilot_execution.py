import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilot"
sys.path.insert(0, str(PILOT)); sys.path.insert(0, str(ROOT))
from pilot_control import (BLINDED_EXCLUSIONS, HISTORY, blinded_secondary_packet,
                           control_sample_selected, evaluate_stop_hold, evaluate_window,
                           initial_state, prospective_start_record, validate_prelaunch_state,
                           validate_role_bindings)
from pilot_metrics import (comprehension_metrics, disagreement_metrics,
                           separated_workload, unknown_none_dq_hold_conflict)
from pilot_store import (AppendOnlyJsonl, IntakeStore, PilotStores, canonical_bytes,
                         COHORT_DIGEST, CONTROL_RULE, PROTOCOL_DIGEST,
                         STOP_HOLD_RULE, initialize_evidence_root, live_counts,
                         recover_start, start_pilot, validate_active_start,
                         validate_observation)


def digest(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()


def observation(seq=1, **changes):
    selected = seq % 5 == 0
    value = {
        "schema_version":"campready.phase5.pilot_observation.v1", "observation_id":digest("cr5pilotobs1-", f"obs-{seq}"),
        "intake_sequence":seq, "review_item_id":digest("cr5ri1-", f"ri-{seq}"), "site_id":"andrews-cove",
        "campground_name":"Synthetic Camp", "affected_relationship_id":"synthetic-rel", "source_role":"CAMPGROUND_PAGE",
        "source_authority":"SYNTHETIC", "source_evidence_ref":"synthetic/source", "event_category":"CAMPGROUND_CONDITION",
        "system_classification":"SEMANTIC_CHANGE", "relevance_status":"RELEVANT", "quality_defect_basis":"NONE",
        "data_quality_state":"NONE", "guard_required":False, "retrieval_first_attempt_outcome":"SUCCESS",
        "retrieval_eventual_outcome":"SUCCESS", "parser_status":"SUCCESS", "rescued_retry":False,
        "reviewer_disposition":"REVIEWED_VALID", "reviewer_code":"PRIMARY_REVIEWER", "review_started_at":"2026-11-17T00:00:00Z",
        "review_completed_at":"2026-11-17T00:01:00Z", "acknowledgement_required":False, "acknowledgement_completed":False,
        "correction_required":False, "correction_reason":None, "correction_before_downstream_transition":None,
        "corrected_item_unresolved":None, "adjudication_required":selected, "adjudication_completed":selected,
        "confirmed_structured_disposition_incompatibility":False, "comprehension_error_classes":[],
        "control_sample_selected":selected, "secondary_reviewer_code":"SECOND_REVIEWER" if selected else None,
        "secondary_disposition":"REVIEWED_VALID" if selected else None, "disagreement_type":None,
        "maintenance_minutes_attributable":0, "review_minutes":1, "adjudication_minutes":0, "correction_minutes":0,
        "notes_or_rationale":"synthetic", "unsafe_inference":False, "protocol_integrity_status":"VALID",
        "evidence_refs":["synthetic/evidence"], "unknown_none_dq_hold_conflict":False,
    }
    value.update(changes)
    return value


def intake(n):
    return {"schema_version":"campready.phase5.pilot_intake.v1", "intake_id":digest("cr5intake1-", f"intake-{n}"),
            "observation_id":digest("cr5pilotobs1-", f"obs-{n}"), "review_item_id":digest("cr5ri1-", f"ri-{n}"),
            "source_evidence_ref":"synthetic/source", "accepted_at":"2026-11-17T00:00:00Z", "dry_run":False, "pilot_observation":True}


class PilotExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        initialize_evidence_root(self.root, allow_test_root=True)

    def tearDown(self): self.temp.cleanup()

    def prepare_start_root(self, root=None):
        root = Path(root or self.root)
        initialize_evidence_root(root, allow_test_root=True)
        (root/"state"/"pilot_state.json").write_bytes(canonical_bytes(initial_state()) + b"\n")
        return root

    def test_append_only_duplicate_and_earlier_bytes(self):
        store = PilotStores(self.root, allow_test_root=True, bypass_start_guard=True).observations
        first = observation(); store.append(first); before = store.path.read_bytes()
        store.append(observation(2)); self.assertTrue(store.path.read_bytes().startswith(before))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID_REJECTED"): store.append(first)

    def test_schema_rejection_and_derived_field(self):
        bad = observation(); del bad["site_id"]
        with self.assertRaisesRegex(ValueError, "OBSERVATION_SCHEMA_INVALID"): validate_observation(bad)
        conflict = observation(relevance_status="UNKNOWN", quality_defect_basis="NONE", reviewer_disposition="DATA_QUALITY_HOLD")
        with self.assertRaisesRegex(ValueError, "OBSERVATION_DERIVATION_INVALID"): validate_observation(conflict)

    def test_sequence_monotonicity_and_failed_intake_no_gap(self):
        store = IntakeStore(self.root, allow_test_root=True, bypass_start_guard=True)
        self.assertEqual(store.accept(intake(1))["intake_sequence"], 1)
        malformed = intake(2); malformed["extra"] = True
        with self.assertRaises(ValueError): store.accept(malformed)
        self.assertEqual(store.accept(intake(2))["intake_sequence"], 2)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID_REJECTED"): store.accept(intake(2))

    def test_control_sampling_exact(self):
        for value in (5,10,15,20): self.assertTrue(control_sample_selected(value))
        for value in (1,4,6,19,99): self.assertFalse(control_sample_selected(value))
        self.assertEqual(sum(control_sample_selected(x) for x in range(1,101)), 20)

    def test_secondary_packet_is_blinded(self):
        packet = blinded_secondary_packet(observation(5))
        self.assertFalse(set(packet) & BLINDED_EXCLUSIONS)
        self.assertNotIn("reviewer_disposition", packet); self.assertIn("source_evidence_ref", packet)

    def test_adjudication_and_correction_preserve_originals(self):
        stores = PilotStores(self.root, allow_test_root=True, bypass_start_guard=True)
        adj = {"schema_version":"campready.phase5.pilot_adjudication.v1", "adjudication_id":digest("cr5adjudication1-","a"),
               "observation_id":observation()["observation_id"], "primary_original_response":{"disposition":"DATA_QUALITY_HOLD"},
               "secondary_original_response":{"disposition":"REVIEWED_VALID"}, "difference_classification":"CONTRACT_CRITICAL_DISAGREEMENT",
               "confirmed_error_status":True, "error_class":"UNKNOWN_DATA_QUALITY_CONFUSION", "correction_requirement":True,
               "adjudicator_code":"OWNER_ADJUDICATOR", "adjudicated_at":"2026-11-17T00:02:00Z", "rationale":"structured conflict"}
        correction = {"schema_version":"campready.phase5.pilot_correction.v1", "correction_id":digest("cr5correction1-","c"),
                      "original_observation_id":observation()["observation_id"], "correction_required":True, "reason":"contract correction",
                      "corrected_disposition":"REVIEWED_VALID", "corrected_by":"PRIMARY_REVIEWER", "corrected_at":"2026-11-17T00:03:00Z",
                      "before_downstream_transition":True, "item_remains_unresolved":False}
        stores.adjudications.append(adj); stores.corrections.append(correction)
        self.assertEqual(stores.adjudications.read_all()[0]["primary_original_response"]["disposition"], "DATA_QUALITY_HOLD")
        self.assertEqual(stores.corrections.read_all()[0]["original_observation_id"], observation()["observation_id"])

    def test_y507_positive_and_negative_vectors(self):
        self.assertTrue(unknown_none_dq_hold_conflict("UNKNOWN","NONE","DATA_QUALITY_HOLD"))
        self.assertFalse(unknown_none_dq_hold_conflict("RELEVANT","NONE","DATA_QUALITY_HOLD"))
        self.assertFalse(unknown_none_dq_hold_conflict("UNKNOWN","PARSER","DATA_QUALITY_HOLD"))
        self.assertFalse(unknown_none_dq_hold_conflict("UNKNOWN","NONE","REVIEWED_VALID"))

    def test_metrics_zero_arithmetic_allowed_discretion_and_correction(self):
        self.assertEqual(comprehension_metrics([])["disposition_comprehension_error_rate"], "NOT_MEASURABLE")
        self.assertEqual(disagreement_metrics([])["disagreement_rate"], "NOT_MEASURABLE")
        row = observation(5, disagreement_type="ALLOWED_DISCRETIONARY_DIFFERENCE", correction_required=True,
                          correction_reason="corrected", correction_before_downstream_transition=True, corrected_item_unresolved=False)
        self.assertEqual(comprehension_metrics([row])["confirmed_comprehension_error_count"], 0)
        self.assertEqual(comprehension_metrics([row])["corrections_required"], 1)
        self.assertEqual(disagreement_metrics([row])["disagreement_rate"], 1)

    def test_immediate_stop_conditions(self):
        for flag in ("notification_or_delivery_enabled_or_invoked","unexpected_canonical_semantic_write","guard_bypass_permitting_prohibited_action","trip_safety_endorsement","road_passability_endorsement","source_silence_treated_as_safety_or_resolution","uncontained_false_lifecycle_event_accepted_as_truth","material_protocol_or_observation_corruption","unsafe_inference"):
            self.assertEqual(evaluate_stop_hold([{"observation_id":"x", flag:True}])["result"], "PILOT_STOP_SAFETY_BOUNDARY")

    def test_two_error_hold_distinct_and_first_continues(self):
        first = {"observation_id":"a","review_item_id":"a","unknown_none_dq_hold_conflict":True,"confirmed_structured_disposition_incompatibility":True}
        self.assertEqual(evaluate_stop_hold([first])["result"], "CONTINUE")
        self.assertEqual(evaluate_stop_hold([first, dict(first)])["result"], "CONTINUE")
        second = dict(first, observation_id="b", review_item_id="b")
        self.assertEqual(evaluate_stop_hold([first,second])["result"], "PILOT_HOLD_REVIEWER_COMPREHENSION")
        for name in ("GUARD_COMPREHENSION_ERROR","ACKNOWLEDGEMENT_WORKFLOW_ERROR","SOURCE_SYSTEM_REVIEW_BOUNDARY_ERROR"):
            rows=[{"review_item_id":"a","error_classes":[name]},{"review_item_id":"b","error_classes":[name]}]
            self.assertEqual(evaluate_stop_hold(rows)["result"], "PILOT_HOLD_REVIEWER_COMPREHENSION")

    def test_stop_precedence_and_window_boundaries(self):
        start = "2026-11-01T00:00:00Z"
        self.assertEqual(evaluate_window(99,start,"2026-11-14T23:59:00Z")["result"], "CONTINUE")
        self.assertEqual(evaluate_window(100,start,"2026-11-01T00:01:00Z")["reason"], "COUNT_100")
        self.assertEqual(evaluate_window(1,start,"2026-11-15T00:00:00Z")["reason"], "HOURS_336")
        stop={"observation_id":"x","unsafe_inference":True}
        self.assertEqual(evaluate_window(100,start,"2026-11-15T00:00:00Z",[stop])["result"], "PILOT_STOP_SAFETY_BOUNDARY")

    def test_not_started_invariants_and_start_requires_owner(self):
        state=initial_state(); validate_prelaunch_state(state)
        self.assertEqual(state["state"],"NOT_STARTED"); self.assertIsNone(state["pilot_started_at"]); self.assertEqual(state["valid_observation_count"],0)
        with self.assertRaisesRegex(ValueError,"EXPLICIT_OWNER_AUTHORIZATION_REQUIRED"):
            prospective_start_record(state,owner_authorized=False,pilot_started_at="2026-11-17T00:00:00Z",protocol_digest="a"*64,cohort_digest="b"*64,role_binding_digest="c"*64)

    def test_role_binding_validation_and_privacy(self):
        path=self.root/"role_bindings.json"
        value={"schema_version":"campready.phase5.pilot_role_bindings.v1","bindings":{"PRIMARY_REVIEWER":"P-001","SECOND_REVIEWER":"P-002","OWNER_ADJUDICATOR":"P-003"},"three_distinct_humans_attested":True,"roles_frozen_before_pilot_started_at":True,"owner_attested_at":"2026-11-17T00:00:00Z"}
        path.write_text(json.dumps(value)); result=validate_role_bindings(path)
        self.assertTrue(result["binding_present"]); self.assertEqual(len(set(result["stable_blinded_codes"].values())),3)

    def test_role_binding_placeholders_are_incomplete(self):
        path=self.root/"role_bindings.json"
        value={"schema_version":"campready.phase5.pilot_role_bindings.v1","bindings":{"PRIMARY_REVIEWER":"REPLACE_WITH_YOUR_PRIMARY_CODE","SECOND_REVIEWER":"REPLACE_WITH_YOUR_SECONDARY_CODE","OWNER_ADJUDICATOR":"REPLACE_WITH_YOUR_OWNER_CODE"},"three_distinct_humans_attested":True,"roles_frozen_before_pilot_started_at":True,"owner_attested_at":"2026-11-17T00:00:00Z"}
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"ROLE_BINDING_INCOMPLETE"):
            validate_role_bindings(path)

    def test_dry_run_live_separation_and_empty_counts(self):
        (self.root/"dry_run"/"fixture.json").write_text(json.dumps({"dry_run":True,"pilot_observation":False,"observation_id":"cr5dryrunobs1-synthetic"}))
        self.assertEqual(set(live_counts(self.root,allow_test_root=True).values()),{0})

    def test_no_notification_network_or_canonical_write_dependencies(self):
        sources="\n".join((PILOT/name).read_text() for name in ("pilot_store.py","pilot_control.py","pilot_metrics.py"))
        for forbidden in ("smtplib","requests","urllib.request","http.client","subprocess","socket"):
            self.assertNotIn("import "+forbidden,sources)
        with self.assertRaisesRegex(ValueError,"UNAUTHORIZED_EVIDENCE_ROOT"):
            PilotStores(self.root)

    def test_historical_transparency_and_walkthrough(self):
        readiness=(PILOT/"execution_readiness.txt").read_text()
        for statement in HISTORY: self.assertIn(statement,readiness)
        for step in range(1,14): self.assertIn(f"{step}.",readiness)

    def test_workload_categories_remain_separate(self):
        result=separated_workload([observation(review_minutes=2,adjudication_minutes=3,correction_minutes=4)], [{"work_class":"OPERATIONAL_MAINTENANCE","human_minutes":5}], 6)
        self.assertEqual(result,{"review_minutes":2.0,"adjudication_minutes":3.0,"correction_minutes":4.0,"operational_maintenance_minutes":5.0,"development_debugging_minutes":6.0})

    def test_atomic_start_contract_repeated_rejection_and_observation_gate(self):
        self.prepare_start_root()
        self.assertEqual(validate_active_start(self.root,allow_test_root=True),"VALID_NOT_STARTED")
        with self.assertRaisesRegex(ValueError,"VALID_ACTIVE_START_REQUIRED"):
            PilotStores(self.root,allow_test_root=True).observations.append(observation())
        fixed=lambda: datetime(2026,11,17,4,30,tzinfo=timezone.utc)
        active=start_pilot(self.root,owner_authorized=True,verified_role_binding_digest="c"*64,
                           allow_test_root=True,clock=fixed)
        self.assertEqual(validate_active_start(self.root,allow_test_root=True),"VALID_ACTIVE_START")
        self.assertEqual(active["state"],"ACTIVE"); self.assertEqual(active["valid_observation_count"],0)
        self.assertEqual(active["pilot_started_at"],"2026-11-17T04:30:00.000000Z")
        self.assertEqual(active["protocol_digest"],PROTOCOL_DIGEST)
        self.assertEqual(active["cohort_digest"],COHORT_DIGEST)
        self.assertEqual(active["role_binding_digest"],"c"*64)
        self.assertEqual(active["control_sampling_rule_id"],CONTROL_RULE)
        self.assertEqual(active["stop_rule_id"],STOP_HOLD_RULE)
        events=(self.root/"state"/"start_events.jsonl").read_text().splitlines()
        self.assertEqual(len(events),1); event=json.loads(events[0])
        self.assertEqual(event["start_transaction_id"],active["start_transaction_id"])
        self.assertEqual(event["pilot_started_at"],active["pilot_started_at"])
        self.assertEqual(event["history"],list(HISTORY))
        before=(active["pilot_started_at"],active["start_transaction_id"],events[0])
        with self.assertRaisesRegex(ValueError,"PILOT_ALREADY_STARTED"):
            start_pilot(self.root,owner_authorized=True,verified_role_binding_digest="d"*64,
                        allow_test_root=True,clock=fixed)
        persisted=json.loads((self.root/"state"/"pilot_state.json").read_text())
        after_events=(self.root/"state"/"start_events.jsonl").read_text().splitlines()
        self.assertEqual(before,(persisted["pilot_started_at"],persisted["start_transaction_id"],after_events[0]))
        self.assertEqual(len(after_events),1); self.assertEqual(set(live_counts(self.root,allow_test_root=True).values()),{0})
        PilotStores(self.root,allow_test_root=True).observations.append(observation())
        self.assertEqual(live_counts(self.root,allow_test_root=True)["valid_observation_count"],1)

    def test_start_rejects_wrong_malformed_and_non_null_prelaunch_state(self):
        for index, bad in enumerate((
            dict(initial_state(),state="HOLD"),
            dict(initial_state(),pilot_started_at="2026-11-17T00:00:00Z"),
            {"state":"NOT_STARTED"},
        )):
            root=self.root/f"bad-{index}"; self.prepare_start_root(root)
            (root/"state"/"pilot_state.json").write_bytes(canonical_bytes(bad)+b"\n")
            self.assertEqual(validate_active_start(root,allow_test_root=True),"INVALID_START_STATE")
            with self.assertRaisesRegex(ValueError,"START_STATE_INVALID"):
                start_pilot(root,owner_authorized=True,verified_role_binding_digest="a"*64,allow_test_root=True)

    def test_start_interruption_classification_recovery_and_fail_closed_admission(self):
        before_commit={"before_prepare","after_prepare","after_state_temp","before_record"}
        after_commit={"after_record","before_activation","after_activation"}
        for point in sorted(before_commit|after_commit):
            root=self.root/point; self.prepare_start_root(root)
            with self.assertRaisesRegex(RuntimeError,"SIMULATED_INTERRUPTION"):
                start_pilot(root,owner_authorized=True,verified_role_binding_digest="b"*64,
                            allow_test_root=True,clock=lambda:datetime(2026,11,17,tzinfo=timezone.utc),
                            _failure_point=point)
            classification=validate_active_start(root,allow_test_root=True)
            expected="VALID_NOT_STARTED" if point=="before_prepare" else "INCOMPLETE_START_RECOVERABLE"
            self.assertEqual(classification,expected)
            with self.assertRaisesRegex(ValueError,"VALID_ACTIVE_START_REQUIRED"):
                PilotStores(root,allow_test_root=True).observations.append(observation())
            recovered=recover_start(root,allow_test_root=True)
            self.assertEqual(recovered,"VALID_NOT_STARTED" if point in before_commit else "VALID_ACTIVE_START")
            records=(root/"state"/"start_events.jsonl")
            self.assertEqual(len(records.read_text().splitlines()) if records.exists() else 0,
                             0 if point in before_commit else 1)

    def test_concurrent_start_has_exactly_one_winner(self):
        self.prepare_start_root()
        def invoke(_):
            try:
                return start_pilot(self.root,owner_authorized=True,verified_role_binding_digest="e"*64,
                                   allow_test_root=True,clock=lambda:datetime(2026,11,17,tzinfo=timezone.utc))["start_transaction_id"]
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(invoke,range(8)))
        winners=[value for value in results if value.startswith("cr5start1-")]
        self.assertEqual(len(winners),1); self.assertEqual(results.count("PILOT_ALREADY_STARTED"),7)
        state=json.loads((self.root/"state"/"pilot_state.json").read_text())
        event=json.loads((self.root/"state"/"start_events.jsonl").read_text())
        self.assertEqual(state["start_transaction_id"],winners[0]); self.assertEqual(event["start_transaction_id"],winners[0])

    def test_tampered_start_record_fails_closed_in_isolated_root(self):
        self.prepare_start_root(); start_pilot(self.root,owner_authorized=True,verified_role_binding_digest="f"*64,allow_test_root=True)
        path=self.root/"state"/"start_events.jsonl"; event=json.loads(path.read_text()); event["cohort_digest"]="0"*64
        path.write_bytes(canonical_bytes(event)+b"\n")
        self.assertEqual(validate_active_start(self.root,allow_test_root=True),"INVALID_START_STATE")
        with self.assertRaisesRegex(ValueError,"VALID_ACTIVE_START_REQUIRED"):
            PilotStores(self.root,allow_test_root=True).observations.append(observation())


if __name__ == "__main__": unittest.main()
