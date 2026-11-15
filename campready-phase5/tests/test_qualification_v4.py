import copy
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "qualification-v4"


def load(name):
    return json.loads((V4 / name).read_text(encoding="utf-8"))


spec = importlib.util.spec_from_file_location("qualification_v4_scorer", V4 / "score_qualification.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


class QualificationV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instrument = load("instrument.json")
        cls.expected = load("expected_results.json")
        cls.schema = load("response.schema.json")
        cls.html = (V4 / "reviewer_form.html").read_text(encoding="utf-8")

    def note_for(self, action, case_id):
        if action == "REVIEWED_VALID" and case_id in scorer.UNKNOWN_CASES:
            return "Displayed official evidence supports the bounded UNKNOWN classification without claiming relevance, resolution, safety, or actionability."
        if action == "REVIEWED_NOT_ACTIONABLE":
            return "UNKNOWN remains the system classification; no further bounded review action is needed." if case_id in scorer.UNKNOWN_CASES else "No further bounded review action is needed."
        if action == "DATA_QUALITY_HOLD":
            return "The parser or retrieval evidence-quality defect prevents reliable semantic confirmation."
        if action == "NEEDS_FOLLOWUP":
            return "Conduct a bounded investigation to verify the official evidence and provenance."
        return None

    def passing_response(self, reviewer="R-G"):
        response = scorer.blank_response(reviewer)
        response.update(started_at="2026-11-15T06:00:00Z", submitted_at="2026-11-15T06:20:00Z", independence_attestation=True, glossary_read_attestation=True)
        universal = self.expected["universal"]
        expected_cases = {row["case_id"]: row for row in self.expected["cases"]}
        for row in response["cases"]:
            exp = expected_cases[row["case_id"]]
            action = exp["acceptable_actions"][0]
            row.update(source_fact_identified=True, system_classification_identified=True, reviewer_annotation_boundary_understood=True, official_provenance_identified=True, selected_reviewer_action=action, data_quality_or_semantic_change=exp["distinction"], guard_required=exp["guard"], safe_to_infer_appearance=exp["appearance"], safe_to_infer_disappearance=universal["safe_to_infer_disappearance"], safe_to_infer_resolution=universal["safe_to_infer_resolution"], safe_to_infer_trip_safety=universal["safe_to_infer_trip_safety"], safe_to_infer_road_passability=universal["safe_to_infer_road_passability"], safe_to_treat_source_silence_as_safe=universal["safe_to_treat_source_silence_as_safe"], shared_event_proves_duplicate_relationships=universal["shared_event_proves_duplicate_relationships"], reviewer_note=self.note_for(action, row["case_id"]))
        expected_aw = {row["scenario_id"]: row["answers"] for row in self.expected["acknowledgement_workflow"]}
        for row in response["acknowledgement_workflow"]:
            row.update({key: (value[0] if isinstance(value, list) else value) for key, value in expected_aw[row["scenario_id"]].items()})
        return response

    def case(self, response, case_id):
        return next(row for row in response["cases"] if row["case_id"] == case_id)

    def test_identity_freshness_schema_and_concept_coverage(self):
        self.assertEqual(self.instrument["qualification_version"], "phase5-4-v4")
        self.assertEqual(self.schema["$id"], "campready.phase5.qualification_response.v4")
        self.assertEqual(self.instrument["case_order"], [f"X4{i:02d}" for i in range(1, 13)])
        self.assertEqual(self.instrument["acknowledgement_workflow_order"], ["AW1", "AW2", "AW3"])
        text = json.dumps(self.instrument)
        for concept in ("SEMANTIC_APPEARANCE", "MATERIAL_UPDATE", "GUARDED_FINAL_FAILURE_WITH_PRIOR", "GUARDED_FINAL_FAILURE_WITHOUT_PRIOR", "RESCUED_RETRY_RELEVANCE_UNKNOWN", "PARSER_DATA_QUALITY", "EXPLICIT_UNKNOWN", "UNAVAILABLE_EVIDENCE_INTEGRITY", "SHARED_UPSTREAM_FANOUT", "GUARDED_NEEDS_FOLLOWUP", "SAFE_REVIEWED_NOT_ACTIONABLE", "ACKNOWLEDGEMENT_PROVENANCE_RECONSTRUCTION"):
            self.assertIn(concept, text)
        self.assertNotRegex(text, r'Foxglove|Copper Beech|Moonstone|Quartz|Silver Fir|Red Alder|Mica Falls|Blue Ash|Larkspur|Starling|Tamarack|v3-run|derived/v3')

    def test_unknown_semantic_truth_is_unchanged(self):
        cases = {row["case_id"]: row for row in self.instrument["cases"]}
        expected = {row["case_id"]: row for row in self.expected["cases"]}
        rescued = cases["X405"]
        self.assertEqual(rescued["source_fact"]["retrieval_status"], "SUCCESS")
        self.assertEqual(rescued["source_fact"]["first_attempt_status"], "FAILED")
        self.assertTrue(rescued["source_fact"]["retry_rescued"])
        for case_id in ("X405", "X407"):
            self.assertEqual(cases[case_id]["source_fact"]["parser_status"], "SUCCESS")
            self.assertEqual(cases[case_id]["source_fact"]["relevance_status"], "UNKNOWN")
            self.assertEqual(cases[case_id]["system_classification"]["data_quality_state"], "NONE")
            self.assertFalse(cases[case_id]["system_classification"]["alert_detail_guard_required"])
            self.assertEqual((expected[case_id]["distinction"], expected[case_id]["guard"]), ("UNKNOWN", "NO"))

    def test_unknown_three_contractually_valid_dispositions(self):
        vectors = {
            "REVIEWED_NOT_ACTIONABLE": "UNKNOWN remains unchanged; no further bounded review action is needed.",
            "REVIEWED_VALID": "Displayed official evidence supports the bounded UNKNOWN classification without claiming relevance, resolution, safety, or actionability.",
            "NEEDS_FOLLOWUP": "A bounded investigation must verify the official evidence against the campground relationship.",
        }
        for case_id in ("X405", "X407"):
            for action, note in vectors.items():
                with self.subTest(case_id=case_id, action=action):
                    response = self.passing_response()
                    row = self.case(response, case_id); row["selected_reviewer_action"] = action; row["reviewer_note"] = note
                    result = scorer.score_response(response)
                    self.assertFalse(result["mandatory_violations"]["DISPOSITION_FIDELITY"])
                    assessment = next(x for x in result["disposition_assessments"] if x["case_id"] == case_id)
                    self.assertEqual(assessment["classification"], "ALLOWED_DISCRETIONARY_ACTION")

    def test_unknown_data_quality_hold_and_semantic_rewrites_fail(self):
        response = self.passing_response(); row = self.case(response, "X405")
        row["selected_reviewer_action"] = "DATA_QUALITY_HOLD"; row["reviewer_note"] = "UNKNOWN alone warrants this hold."
        result = scorer.score_response(response)
        self.assertIn("X405:contractually_invalid_action", result["mandatory_violations"]["DISPOSITION_FIDELITY"])
        for wrong in ("DATA_QUALITY", "SEMANTIC_CHANGE"):
            changed = self.passing_response(); self.case(changed, "X405")["data_quality_or_semantic_change"] = wrong
            self.assertIn("X405:data_quality_or_semantic_change", scorer.score_response(changed)["mandatory_violations"]["DATA_QUALITY_UNKNOWN"])

    def test_unknown_action_note_semantics_are_mandatory(self):
        invalid = {
            "REVIEWED_NOT_ACTIONABLE": "This is irrelevant and never actionable.",
            "REVIEWED_VALID": "Looks fine.",
            "NEEDS_FOLLOWUP": "Maybe later.",
        }
        for action, note in invalid.items():
            response = self.passing_response(); row = self.case(response, "X407"); row["selected_reviewer_action"] = action; row["reviewer_note"] = note
            self.assertTrue(scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])

    def test_unsafe_note_inferences_fail_independently(self):
        claims = ("The trip is safe.", "The road is passable.", "Source silence means safe.")
        for claim in claims:
            response = self.passing_response(); self.case(response, "X405")["reviewer_note"] = "Displayed evidence supports the bounded UNKNOWN classification without claiming relevance. " + claim
            self.assertTrue(scorer.score_response(response)["mandatory_violations"]["UNSAFE_INFERENCE"])

    def test_data_quality_hold_remains_valid_for_actual_quality_facts(self):
        for case_id in ("X403", "X404", "X406", "X408"):
            response = self.passing_response(); row = self.case(response, case_id); row["selected_reviewer_action"] = "DATA_QUALITY_HOLD"; row["reviewer_note"] = "The retrieval, parser, or evidence integrity defect prevents semantic confirmation."
            self.assertFalse(scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])

    def test_no_pass_manufacturing_guard_parser_and_required_followup(self):
        for action in ("REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE"):
            response = self.passing_response(); row = self.case(response, "X403"); row["selected_reviewer_action"] = action; row["reviewer_note"] = "No further bounded action." if action.endswith("ACTIONABLE") else None
            result = scorer.score_response(response)
            self.assertTrue(result["mandatory_violations"]["GUARD"])
            self.assertTrue(result["mandatory_violations"]["DISPOSITION_FIDELITY"])
        response = self.passing_response(); row = self.case(response, "X406"); row["selected_reviewer_action"] = "REVIEWED_VALID"; row["reviewer_note"] = None
        self.assertTrue(scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])
        response = self.passing_response(); row = self.case(response, "X410"); row["selected_reviewer_action"] = "DATA_QUALITY_HOLD"; row["reviewer_note"] = "Retrieval defect exists."
        self.assertTrue(scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])

    def test_review_boundary_is_separate_from_disposition_fidelity(self):
        response = self.passing_response(); row = self.case(response, "X405"); row["selected_reviewer_action"] = "DATA_QUALITY_HOLD"; row["reviewer_note"] = "No quality defect is present."
        result = scorer.score_response(response)
        self.assertFalse(result["mandatory_violations"]["REVIEW_BOUNDARY"])
        self.assertTrue(result["mandatory_violations"]["DISPOSITION_FIDELITY"])
        row["source_fact_identified"] = False
        self.assertTrue(scorer.score_response(response)["mandatory_violations"]["REVIEW_BOUNDARY"])

    def test_historical_defect_regression_vectors_without_rescoring_history(self):
        for case_id in ("X405", "X407"):
            response = self.passing_response(); row = self.case(response, case_id); row["selected_reviewer_action"] = "REVIEWED_NOT_ACTIONABLE"; row["reviewer_note"] = "UNKNOWN remains unchanged; no further bounded review action is needed."
            result = scorer.score_response(response)
            self.assertFalse(result["mandatory_violations"]["DATA_QUALITY_UNKNOWN"])
            self.assertFalse(result["mandatory_violations"]["REVIEW_BOUNDARY"])
            self.assertFalse(result["mandatory_violations"]["DISPOSITION_FIDELITY"])
        response = self.passing_response(); self.case(response, "X405")["data_quality_or_semantic_change"] = "DATA_QUALITY"
        self.assertTrue(scorer.score_response(response)["mandatory_violations"]["DATA_QUALITY_UNKNOWN"])

    def test_acknowledgement_workflow_preserved_and_zero_tolerance(self):
        response = self.passing_response()
        self.assertTrue(scorer.score_response(response)["contract_pass"])
        proofs = {("AW1", "eventual_manual_acknowledgement_required", "YES"), ("AW1", "direct_new_to_acknowledged_permitted", "NO"), ("AW2", "later_acknowledged_transition_available", "YES"), ("AW2", "eventual_acknowledgement_required", "NO"), ("AW2", "data_quality_alone_creates_acknowledgement_obligation", "NO"), ("AW3", "eventual_acknowledgement_required", "YES"), ("AW3", "unresolved_followup_note_required", "YES"), ("AW3", "acknowledgement_erases_unresolved_condition", "NO")}
        truth = {(row["scenario_id"], key, value) for row in self.expected["acknowledgement_workflow"] for key, value in row["answers"].items() if not isinstance(value, list)}
        self.assertTrue(proofs <= truth)
        for scenario_id, field, wanted in proofs:
            changed = copy.deepcopy(response); row = next(x for x in changed["acknowledgement_workflow"] if x["scenario_id"] == scenario_id); row[field] = "NO" if wanted == "YES" else "YES"
            self.assertTrue(scorer.score_response(changed)["mandatory_violations"]["ACKNOWLEDGEMENT_WORKFLOW"])

    def test_strict_schema_fields_identity_attestations_and_fresh_reviewers(self):
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), scorer.TOP_FIELDS)
        self.assertEqual(self.schema["properties"]["reviewer_id"]["enum"], ["R-G", "R-H"])
        self.assertNotIn("acknowledgement_required", self.schema["$defs"]["case"]["properties"])
        response = self.passing_response(); scorer.validate_response(response)
        for reviewer in ("R-A", "R-B", "R-C", "R-D", "R-E", "R-F"):
            changed = copy.deepcopy(response); changed["reviewer_id"] = reviewer
            with self.assertRaisesRegex(ValueError, "REVIEWER_ID_INVALID"): scorer.validate_response(changed)
        self.assertTrue(scorer.evaluate_two([self.passing_response("R-G"), self.passing_response("R-H")])["contract_pass"])

    def test_categories_no_threshold_and_zero_tolerance(self):
        categories = {"GUARD", "DATA_QUALITY_UNKNOWN", "PROVENANCE", "REVIEW_BOUNDARY", "DISPOSITION_FIDELITY", "FANOUT", "UNSAFE_INFERENCE", "ACKNOWLEDGEMENT_WORKFLOW"}
        self.assertEqual(set(scorer.CATEGORIES), categories)
        self.assertEqual(set(self.expected["contract"]["zero_tolerance_categories"]), categories)
        self.assertIsNone(self.expected["contract"]["percentage_threshold"])

    def test_glossary_preserves_terms_and_neutral_action_meanings(self):
        glossary = self.instrument["glossary"]
        for term in ("SOURCE FACT", "SYSTEM CLASSIFICATION", "REVIEWER ANNOTATION", "DATA_QUALITY", "UNKNOWN", "workflow action", "transition permitted", "transition required", "manual acknowledgement", "ACKNOWLEDGED", "unresolved follow-up", "REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"):
            self.assertIn(term, glossary)
        text = json.dumps(glossary).lower()
        self.assertNotRegex(text, r'\bX4\d\d\b|correct answer|answer key')
        self.assertIn("does not rewrite", text)
        self.assertIn("unknown alone", text)

    def test_action_contracts_preserve_unknown_and_bounded_workflow_meaning(self):
        glossary = self.instrument["glossary"]
        not_actionable = glossary["REVIEWED_NOT_ACTIONABLE"].lower()
        for phrase in ("does not rewrite unknown", "known irrelevance", "universal non-actionability", "safety", "road passability", "source silence", "authorize delivery"):
            self.assertIn(phrase, not_actionable)
        reviewed_valid = glossary["REVIEWED_VALID"].lower()
        for phrase in ("displayed system classification", "official evidence", "does not rewrite unknown", "relevance", "appearance", "disappearance", "resolution", "safety", "future re-evaluation"):
            self.assertIn(phrase, reviewed_valid)
        needs_followup = glossary["NEEDS_FOLLOWUP"].lower()
        self.assertIn("concrete additional bounded investigation", needs_followup)
        self.assertIn("does not itself imply a guard", needs_followup)
        self.assertIn("acknowledgement obligation", needs_followup)
        # Follow-up is valid but not universally mandatory for UNKNOWN.
        for action, note in (("REVIEWED_VALID", "Displayed official evidence supports the bounded UNKNOWN classification without claiming relevance, resolution, safety, or actionability."), ("REVIEWED_NOT_ACTIONABLE", "UNKNOWN remains unchanged; no further bounded review action is needed."), ("NEEDS_FOLLOWUP", "A bounded investigation must verify the official evidence.")):
            response = self.passing_response(); row = self.case(response, "X405"); row["selected_reviewer_action"] = action; row["reviewer_note"] = note
            self.assertFalse(scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])

    def test_form_offline_readable_leak_free_and_bound_to_digest(self):
        lower = self.html.lower()
        self.assertIn("file", "file")
        self.assertNotRegex(lower, r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?')
        for forbidden in ("localstorage", "sessionstorage", "indexeddb", "fetch(", "xmlhttprequest", "websocket", "sendbeacon", "score_qualification", "expected_results", "acceptable_actions", "contract_pass", "hold_phase5_4", "pass_phase5_4"):
            self.assertNotIn(forbidden, lower)
        for heading in ("SOURCE FACT", "SYSTEM CLASSIFICATION", "YOUR REVIEW", "ACKNOWLEDGEMENT WORKFLOW"):
            self.assertIn(heading, self.html)
        self.assertNotIn("<pre", lower); self.assertNotIn("<code", lower)
        self.assertEqual(hashlib.sha256((V4 / "instrument.json").read_bytes()).hexdigest(), re.search(r'DIGEST="sha256:([0-9a-f]{64})"', self.html).group(1))
        self.assertNotRegex(self.html, r'<option>R-[A-F]</option>')
        self.assertIn("R-G", self.html); self.assertIn("R-H", self.html)
        for case_id in ("X401", "X403", "X405", "X406", "X407"):
            self.assertIn(case_id, self.html)
        for scenario_id in ("AW1", "AW2", "AW3"):
            self.assertIn(scenario_id, self.html)

    def test_v1_v2_v3_complete_frozen_hash_inventory(self):
        frozen = {
            "qualification/__pycache__/score_qualification.cpython-314.pyc":"0d51e64dd288333b63b0b9a716f30b250b996b665bb831ac49a14a7bb87711bc", "qualification/expected_results.json":"a58522a7761f7cf4db72cbb0ece60d788f7951f3022d9d49a65f5ff69e9acda4", "qualification/instrument.json":"40cf3375c929603d1415006bd6f02df385dc690c221963ed45833ba39268f188", "qualification/response.schema.json":"94fd83adb2c4c972da258a0182b34640871927e90a333aa53450508a2db9bad6", "qualification/reviewer_form.html":"77c855165d77aeec37906bd8fe837f4b67fbf528c36d5d03dd6d18d11fecaec1", "qualification/reviewer_packet.txt":"00fdd4735284f35a52bda08ac3336bba81475bbf1b4d0321b3d1a6d8667e86f2", "qualification/score_qualification.py":"11f8b92c99e4513cf36ea3175de823232645e0945835997ce5f4dc6f018ac19b",
            "qualification-v2/__pycache__/score_qualification.cpython-314.pyc":"04f75336ba9e4ee87609a7db53615b163c68abd94688fb65c7c9eb32e886c99f", "qualification-v2/expected_results.json":"bfe14e1cb4220e6e6e6acbc93e39328c22fb5fc664191119b45c21c8674e2462", "qualification-v2/instrument.json":"ae3811612380e52687aa6a987968d877f59f52ac978b15f8ad7a5e3b4ac74332", "qualification-v2/response.schema.json":"0dd3c9ed3117f520a8e0fc3e3062e5a3c43d3f04897bdfe3492463c788661cff", "qualification-v2/reviewer_form.html":"d8a2b58f7c5ec6f9dc4429a6ce66dc8ca81153042005749986d45a91cfe09ca0", "qualification-v2/reviewer_instructions.txt":"d303800b545c6f79ccef186777b777bdb9c0a7f39b880f19b7c42971ec937e72", "qualification-v2/root_cause_review.txt":"529f182f79d63a2bafac10d5283eef84b947d308756c997f47b6a029310a4771", "qualification-v2/score_qualification.py":"eb26ceee191bba6617c2eb7559be04d39976323dd9e38715dc5756d87324b42a",
            "qualification-v3/__pycache__/score_qualification.cpython-314.pyc":"6cdc5151b42239c62386516a08e3501740c204ae3c4916516441c2a105cdad44", "qualification-v3/expected_results.json":"5959c0131150771adf97366454bacdebbae877f137aa8a13740cbf66ce5f7b81", "qualification-v3/instrument.json":"b7dd3abfe2815e232355d01f5a6234ee2c349989afa8b3564baeaf318047675a", "qualification-v3/response.schema.json":"975c3a5c8b71874963e97573a3bc30e037bc1a44e4cadc9ae675b7960266f128", "qualification-v3/reviewer_form.html":"ce73037f0a46a554bb1768a395ae4eca577034bf6cda5227e737c619e8dfb0f4", "qualification-v3/reviewer_instructions.txt":"08459c932ae9ce7f1ae8be6d4d9473dc81b500a5cad190ef6773a2a7c8e942bc", "qualification-v3/score_qualification.py":"c4f08d0ff855026cdf669c2bd67e05587de743e092127a8ce406cfbeb32f4f5a",
        }
        actual = {str(path.relative_to(ROOT)) for directory in (ROOT / "qualification", ROOT / "qualification-v2", ROOT / "qualification-v3") for path in directory.rglob("*") if path.is_file()}
        self.assertEqual(actual, set(frozen))
        for relative, wanted in frozen.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), wanted, relative)


if __name__ == "__main__":
    unittest.main()
