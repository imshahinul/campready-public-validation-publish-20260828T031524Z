import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilot"


class Phase55PilotProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((PILOT / "protocol.json").read_text(encoding="utf-8"))
        cls.schema = json.loads((PILOT / "observation.schema.json").read_text(encoding="utf-8"))
        cls.closeout = json.loads((PILOT / "closeout_rules.json").read_text(encoding="utf-8"))
        cls.markdown = (PILOT / "protocol.md").read_text(encoding="utf-8")

    def test_versioned_frozen_and_no_post_hoc_mutation(self):
        self.assertEqual(self.protocol["schema_version"], "campready.phase5.pilot_protocol.v1")
        self.assertTrue(self.protocol["frozen"])
        self.assertFalse(self.protocol["immutability"]["post_hoc_threshold_mutation_permitted"])
        self.assertFalse(self.closeout["percentage_alone_determines_decision"])

    def test_owner_override_and_phase54_hold_are_exact(self):
        governance = self.protocol["governance"]
        self.assertEqual(governance["phase5_4_result"], "CLOSE_PHASE5_4_HOLD_FINAL_HUMAN_QUALIFICATION_GATE_NOT_MET")
        self.assertEqual(governance["phase5_5a_result"], "PASS_PHASE5_5A_OWNER_OVERRIDE_BOUNDED_PILOT_AUTHORIZED")
        self.assertEqual(governance["classification"], "OWNER_OVERRIDE_AUTHORIZE_PHASE5_5_BOUNDED_INTERNAL_PILOT")

    def test_exact_cohort_is_frozen(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(self.protocol["cohort"]["sites"], config["cohort"])
        self.assertFalse(self.protocol["cohort"]["expansion_permitted"])
        self.assertEqual(self.protocol["cohort"]["event_categories"], ["WEATHER_ALERT", "CAMPGROUND_CONDITION"])
        self.assertEqual(self.protocol["cohort"]["source_roles"], ["ACTIVE_ALERT", "CAMPGROUND_PAGE", "ALERT_DETAIL"])

    def test_y507_derivation_is_deterministic(self):
        derivation = self.protocol["y507_control"]["derivation"]
        self.assertIn("if and only if", derivation)
        derive = lambda relevance, basis, disposition: relevance == "UNKNOWN" and basis == "NONE" and disposition == "DATA_QUALITY_HOLD"
        values = ("UNKNOWN", "NONE", "DATA_QUALITY_HOLD")
        self.assertTrue(derive(*values))
        for i, replacement in enumerate(("RELEVANT", "PARSER", "NEEDS_FOLLOWUP")):
            changed = list(values); changed[i] = replacement
            self.assertFalse(derive(*changed))
        condition = self.schema["allOf"][0]
        self.assertEqual(condition["then"]["properties"]["unknown_none_dq_hold_conflict"]["const"], True)
        self.assertEqual(condition["else"]["properties"]["unknown_none_dq_hold_conflict"]["const"], False)

    def test_comprehension_rate_is_unambiguous_and_excludes_system_defects(self):
        metric = self.protocol["metrics"]["disposition_comprehension_error_rate"]
        self.assertIn("confirmed_structured_disposition_incompatibility == true", metric["numerator"])
        self.assertIn("reviewer_disposition is non-null", metric["denominator"])
        self.assertIn("NOT_MEASURABLE", metric["rate"])
        self.assertIn("transport defects", metric["exclusions"])

    def test_allowed_discretion_excluded_and_corrections_counted(self):
        self.assertFalse(self.protocol["metrics"]["disagreement"]["allowed_discretion_is_comprehension_error"])
        self.assertIn("remains counted", self.protocol["metrics"]["correction_rule"])

    def test_immediate_stops_and_unsafe_inference_zero_tolerance(self):
        stops = " ".join(self.protocol["immediate_stop_conditions"])
        for phrase in ("notification or delivery", "canonical semantic write", "guard bypass", "trip-safety", "road-passability", "source silence", "false lifecycle", "materially corrupted", "UNSAFE_INFERENCE"):
            self.assertIn(phrase, stops)
        self.assertEqual(self.protocol["immediate_stop_result"], "PILOT_STOP_SAFETY_BOUNDARY")

    def test_repeated_unknown_dq_and_other_triggers_are_absolute(self):
        triggers = self.protocol["repeated_error_triggers"]
        self.assertEqual([item["absolute_count"] for item in triggers], [2, 2, 2, 2])
        self.assertEqual(triggers[0]["class"], "UNKNOWN + basis NONE -> DATA_QUALITY_HOLD conflict")
        self.assertIn("Do not STOP on the first isolated", self.protocol["isolated_y507_rule"])

    def test_window_is_finite_and_no_backfill(self):
        window = self.protocol["observation_window"]
        self.assertEqual(window["count_boundary"], 100)
        self.assertIn("336 hours", window["calendar_boundary"])
        self.assertFalse(window["open_ended"])
        self.assertFalse(window["pre_protocol_backfill_permitted"])

    def test_control_sample_is_prospective_and_not_cherry_picked(self):
        sample = self.protocol["control_sample"]
        self.assertIn("divisible by 5", sample["method"])
        self.assertTrue(sample["outcome_blinded"])
        self.assertFalse(sample["post_outcome_cherry_picking_permitted"])
        self.assertFalse(sample["pass_threshold_created"])

    def test_required_observation_fields_and_error_taxonomy(self):
        required = set(self.schema["required"])
        for field in ("observation_id", "review_item_id", "affected_relationship_id", "source_role", "source_authority", "system_classification", "data_quality_state", "guard_required", "reviewer_disposition", "reviewer_code", "review_started_at", "review_completed_at", "acknowledgement_required", "acknowledgement_completed", "correction_required", "adjudication_required", "disagreement_type", "maintenance_minutes_attributable", "notes_or_rationale"):
            self.assertIn(field, required)
        self.assertEqual(set(self.schema["properties"]["comprehension_error_classes"]["items"]["enum"]), set(self.protocol["metrics"]["error_classes"]))

    def test_no_notification_delivery_public_or_safety_authority(self):
        self.assertFalse(self.protocol["execution_authorized"])
        prohibited = set(self.protocol["authorization_boundary"]["prohibited"])
        for item in ("pilot execution", "consumer notification", "automatic delivery", "public launch", "public users", "trip-safety claims", "road-passability claims", "national expansion", "new event categories"):
            self.assertIn(item, prohibited)

    def test_historical_transparency_is_mandatory_everywhere(self):
        statements = self.protocol["governance"]["required_history_statements"]
        self.assertEqual(statements, self.closeout["historical_transparency"][:2])
        for statement in statements:
            self.assertIn(statement, self.markdown)
        self.assertIn("R-I final PASS", self.closeout["historical_transparency"])
        self.assertIn("R-J Y507 genuine individual comprehension error", self.closeout["historical_transparency"])

    def test_closeout_classes_eligibility_and_success_boundary(self):
        expected = {"PILOT_CONTINUE_TO_CLOSEOUT", "PILOT_HOLD_REVIEWER_COMPREHENSION", "PILOT_HOLD_OPERATIONAL_BURDEN", "PILOT_HOLD_SOURCE_RELIABILITY", "PILOT_STOP_SAFETY_BOUNDARY", "PILOT_INVALID_INSUFFICIENT_EVIDENCE"}
        self.assertEqual(set(self.closeout["classes"]), expected)
        self.assertGreaterEqual(len(self.closeout["normal_closeout_eligibility"]), 8)
        self.assertFalse(self.closeout["final_decision_mapping"]["execution_started_by_this_checkpoint"])


if __name__ == "__main__":
    unittest.main()
