import copy
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "qualification-v3"


def load(name):
    return json.loads((V3 / name).read_text(encoding="utf-8"))


spec = importlib.util.spec_from_file_location("qualification_v3_scorer", V3 / "score_qualification.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


class QualificationV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instrument = load("instrument.json")
        cls.expected = load("expected_results.json")
        cls.schema = load("response.schema.json")
        cls.html = (V3 / "reviewer_form.html").read_text(encoding="utf-8")

    def passing_response(self, reviewer="R-E"):
        response = scorer.blank_response(reviewer)
        response.update(started_at="2026-11-15T04:00:00Z", submitted_at="2026-11-15T04:20:00Z", independence_attestation=True, glossary_read_attestation=True)
        universal = self.expected["universal"]
        expected_cases = {row["case_id"]: row for row in self.expected["cases"]}
        for row in response["cases"]:
            exp = expected_cases[row["case_id"]]
            row.update(source_fact_identified=True, system_classification_identified=True, reviewer_annotation_boundary_understood=True, official_provenance_identified=True, selected_reviewer_action=exp["acceptable_actions"][0], data_quality_or_semantic_change=exp["distinction"], guard_required=exp["guard"], safe_to_infer_appearance=exp["appearance"], safe_to_infer_disappearance=universal["safe_to_infer_disappearance"], safe_to_infer_resolution=universal["safe_to_infer_resolution"], safe_to_infer_trip_safety=universal["safe_to_infer_trip_safety"], safe_to_infer_road_passability=universal["safe_to_infer_road_passability"], safe_to_treat_source_silence_as_safe=universal["safe_to_treat_source_silence_as_safe"], shared_event_proves_duplicate_relationships=universal["shared_event_proves_duplicate_relationships"], reviewer_note="Independent bounded review.")
        expected_aw = {row["scenario_id"]: row["answers"] for row in self.expected["acknowledgement_workflow"]}
        for row in response["acknowledgement_workflow"]:
            row.update({key: (value[0] if isinstance(value, list) else value) for key, value in expected_aw[row["scenario_id"]].items()})
        return response

    def test_identity_order_freshness_and_concept_coverage(self):
        self.assertEqual(self.instrument["qualification_version"], "phase5-4-v3")
        self.assertEqual(self.schema["$id"], "campready.phase5.qualification_response.v3")
        self.assertEqual(self.instrument["case_order"], [f"V3{i:02d}" for i in range(1, 13)])
        self.assertEqual(self.instrument["acknowledgement_workflow_order"], ["AW1", "AW2", "AW3"])
        text = json.dumps(self.instrument)
        for concept in ("SEMANTIC_APPEARANCE", "MATERIAL_UPDATE", "GUARDED_FINAL_FAILURE_WITH_PRIOR", "GUARDED_FINAL_FAILURE_WITHOUT_PRIOR", "RESCUED_RETRY_RELEVANCE_UNKNOWN", "PARSER_DATA_QUALITY", "EXPLICIT_UNKNOWN", "UNAVAILABLE_EVIDENCE_INTEGRITY", "SHARED_UPSTREAM_FANOUT", "GUARDED_NEEDS_FOLLOWUP", "SAFE_REVIEWED_NOT_ACTIONABLE", "ACKNOWLEDGEMENT_PROVENANCE_RECONSTRUCTION"):
            self.assertIn(concept, text)
        self.assertNotRegex(text, r'"case_id"\s*:\s*"(?:T|Q)\d')
        self.assertNotIn("20261114", text)

    def test_no_derivative_per_case_acknowledgement_scoring(self):
        forbidden = "acknowledgement_required"
        case_def = self.schema["$defs"]["case"]
        self.assertNotIn(forbidden, case_def["required"])
        self.assertNotIn(forbidden, case_def["properties"])
        self.assertNotIn(forbidden, scorer.CASE_FIELDS)
        self.assertNotIn(forbidden, {key for row in self.expected["cases"] for key in row})
        response = self.passing_response()
        self.assertNotIn(forbidden, {key for row in response["cases"] for key in row})
        self.assertTrue(scorer.score_response(response)["contract_pass"])

    def test_data_quality_unknown_guard_and_fanout_truth(self):
        rows = {row["case_id"]: row for row in self.expected["cases"]}
        self.assertEqual(rows["V305"]["distinction"], "UNKNOWN")
        self.assertEqual(rows["V307"]["distinction"], "UNKNOWN")
        self.assertEqual(rows["V306"]["distinction"], "DATA_QUALITY")
        for case_id in ("V303", "V304", "V310", "V312"):
            self.assertEqual((rows[case_id]["distinction"], rows[case_id]["guard"]), ("DATA_QUALITY", "YES"))
        self.assertEqual(self.expected["universal"]["shared_event_proves_duplicate_relationships"], "NO")
        cases = {row["case_id"]: row for row in self.instrument["cases"]}
        self.assertTrue(cases["V305"]["source_fact"]["retry_rescued"])
        self.assertEqual(cases["V306"]["source_fact"]["parser_status"], "FAILED")
        self.assertEqual(cases["V309"]["source_fact"]["upstream_event_count"], 1)

    def test_acknowledgement_workflow_contract_is_zero_tolerance(self):
        response = self.passing_response()
        self.assertTrue(scorer.score_response(response)["contract_pass"])
        required_proofs = {
            ("AW1", "eventual_manual_acknowledgement_required", "YES"),
            ("AW1", "direct_new_to_acknowledged_permitted", "NO"),
            ("AW2", "eventual_acknowledgement_required", "NO"),
            ("AW2", "later_acknowledged_transition_available", "YES"),
            ("AW2", "data_quality_alone_creates_acknowledgement_obligation", "NO"),
            ("AW3", "eventual_acknowledgement_required", "YES"),
            ("AW3", "unresolved_followup_note_required", "YES"),
            ("AW3", "acknowledgement_erases_unresolved_condition", "NO"),
            ("AW3", "acknowledgement_makes_trip_safe", "NO"),
            ("AW3", "acknowledgement_authorizes_notification", "NO"),
        }
        truth = {(row["scenario_id"], key, value) for row in self.expected["acknowledgement_workflow"] for key, value in row["answers"].items() if not isinstance(value, list)}
        self.assertTrue(required_proofs <= truth)
        for scenario_id, field, wanted in required_proofs:
            changed = copy.deepcopy(response)
            row = next(item for item in changed["acknowledgement_workflow"] if item["scenario_id"] == scenario_id)
            row[field] = "NO" if wanted == "YES" else "YES"
            result = scorer.score_response(changed)
            self.assertFalse(result["contract_pass"])
            self.assertIn(f"{scenario_id}:{field}", result["mandatory_violations"]["ACKNOWLEDGEMENT_WORKFLOW"])

    def test_non_guard_followup_permission_does_not_create_obligation(self):
        aw2 = next(row for row in self.expected["acknowledgement_workflow"] if row["scenario_id"] == "AW2")["answers"]
        self.assertEqual(aw2["later_acknowledged_transition_available"], "YES")
        self.assertEqual(aw2["eventual_acknowledgement_required"], "NO")
        glossary = self.instrument["glossary"]
        self.assertIn("allowed transition does not by itself make", glossary["transition required"])
        response = self.passing_response()
        rescued = next(row for row in response["cases"] if row["case_id"] == "V305")
        self.assertEqual(rescued["guard_required"], "NO")
        rescued["selected_reviewer_action"] = "NEEDS_FOLLOWUP"
        self.assertTrue(scorer.score_response(response)["contract_pass"])
        self.assertNotIn("acknowledgement_required", rescued)

    def test_mandatory_categories_and_no_percentage_threshold(self):
        expected = {"GUARD", "DATA_QUALITY_UNKNOWN", "PROVENANCE", "REVIEW_BOUNDARY", "FANOUT", "UNSAFE_INFERENCE", "ACKNOWLEDGEMENT_WORKFLOW"}
        self.assertEqual(set(scorer.CATEGORIES), expected)
        self.assertEqual(set(self.expected["contract"]["zero_tolerance_categories"]), expected)
        self.assertIsNone(self.expected["contract"]["percentage_threshold"])
        response = self.passing_response()
        response["cases"][0]["official_provenance_identified"] = False
        result = scorer.score_response(response)
        self.assertFalse(result["contract_pass"])
        self.assertTrue(result["mandatory_violations"]["PROVENANCE"])
        response = self.passing_response()
        response["cases"][0]["selected_reviewer_action"] = "ACKNOWLEDGED"
        result = scorer.score_response(response)
        self.assertFalse(result["contract_pass"])
        self.assertIn("V301:selected_reviewer_action_not_permitted", result["mandatory_violations"]["REVIEW_BOUNDARY"])

    def test_strict_response_identity_attestations_and_two_reviewers(self):
        response = self.passing_response()
        scorer.validate_response(response)
        for reviewer in ("R-A", "R-B", "R-C", "R-D", "R-G"):
            bad = copy.deepcopy(response); bad["reviewer_id"] = reviewer
            with self.assertRaisesRegex(ValueError, "REVIEWER_ID_INVALID"):
                scorer.validate_response(bad)
        for field, error in (("independence_attestation", "INDEPENDENCE"), ("glossary_read_attestation", "GLOSSARY")):
            bad = copy.deepcopy(response); bad[field] = False
            with self.assertRaisesRegex(ValueError, error):
                scorer.validate_response(bad)
        self.assertTrue(scorer.evaluate_two([self.passing_response("R-E"), self.passing_response("R-F")])["contract_pass"])
        extra = copy.deepcopy(response); extra["personal_name"] = "not allowed"
        with self.assertRaisesRegex(ValueError, "RESPONSE_FIELDS_INVALID"):
            scorer.validate_response(extra)

    def test_form_is_offline_human_readable_and_has_no_scorer_or_key(self):
        lower = self.html.lower()
        self.assertNotRegex(lower, r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?')
        for forbidden in ("localstorage", "sessionstorage", "indexeddb", "fetch(", "xmlhttprequest", "websocket", "sendbeacon", "score_qualification", "expected_results", "acceptable_actions", "contract_pass", "historical_v1_decision"):
            self.assertNotIn(forbidden, lower)
        for heading in ("SOURCE FACT", "SYSTEM CLASSIFICATION", "YOUR REVIEW", "ACKNOWLEDGEMENT WORKFLOW"):
            self.assertIn(heading, self.html)
        self.assertNotIn("<pre", lower)
        self.assertNotIn("<code", lower)
        self.assertIn("file", "file")
        self.assertEqual(self.html.count('data-case="${c.id}"'), 1)
        self.assertNotRegex(self.html, r'HOLD_PHASE5_4|PASS_PHASE5_4|R-[ABCD]')

    def test_form_digest_schema_and_exact_fields(self):
        digest = hashlib.sha256((V3 / "instrument.json").read_bytes()).hexdigest()
        self.assertIn(f"sha256:{digest}", self.html)
        self.assertEqual(self.schema["additionalProperties"], False)
        self.assertEqual(set(self.schema["required"]), scorer.TOP_FIELDS)
        self.assertEqual(self.schema["properties"]["reviewer_id"]["enum"], ["R-E", "R-F"])
        self.assertEqual(self.schema["properties"]["cases"]["minItems"], 12)
        self.assertEqual(self.schema["properties"]["acknowledgement_workflow"]["minItems"], 3)

    def test_no_case_specific_correctness_hints_or_historical_answers(self):
        for token in ("correct answer", "answer key", "rubric", "expected result", "reviewer answer", "V1", "V2"):
            self.assertNotIn(token.lower(), self.html.lower())
        expected_bytes = (V3 / "expected_results.json").read_text(encoding="utf-8")
        self.assertNotIn(expected_bytes, self.html)
        self.assertNotIn('"zero_tolerance_categories"', self.html)

    def test_no_network_delivery_or_canonical_write_implementation(self):
        combined = self.html + (V3 / "score_qualification.py").read_text(encoding="utf-8")
        for forbidden in ("requests.", "urllib.request", "http.client", "smtplib", "subprocess", "socket.", "canonical.write", "notification.send", "delivery.send"):
            self.assertNotIn(forbidden, combined.lower())

    def test_v1_v2_full_frozen_hash_inventory(self):
        frozen = {
            "qualification/__pycache__/score_qualification.cpython-314.pyc":"0d51e64dd288333b63b0b9a716f30b250b996b665bb831ac49a14a7bb87711bc",
            "qualification/expected_results.json":"a58522a7761f7cf4db72cbb0ece60d788f7951f3022d9d49a65f5ff69e9acda4",
            "qualification/instrument.json":"40cf3375c929603d1415006bd6f02df385dc690c221963ed45833ba39268f188",
            "qualification/response.schema.json":"94fd83adb2c4c972da258a0182b34640871927e90a333aa53450508a2db9bad6",
            "qualification/reviewer_form.html":"77c855165d77aeec37906bd8fe837f4b67fbf528c36d5d03dd6d18d11fecaec1",
            "qualification/reviewer_packet.txt":"00fdd4735284f35a52bda08ac3336bba81475bbf1b4d0321b3d1a6d8667e86f2",
            "qualification/score_qualification.py":"11f8b92c99e4513cf36ea3175de823232645e0945835997ce5f4dc6f018ac19b",
            "qualification-v2/__pycache__/score_qualification.cpython-314.pyc":"04f75336ba9e4ee87609a7db53615b163c68abd94688fb65c7c9eb32e886c99f",
            "qualification-v2/expected_results.json":"bfe14e1cb4220e6e6e6acbc93e39328c22fb5fc664191119b45c21c8674e2462",
            "qualification-v2/instrument.json":"ae3811612380e52687aa6a987968d877f59f52ac978b15f8ad7a5e3b4ac74332",
            "qualification-v2/response.schema.json":"0dd3c9ed3117f520a8e0fc3e3062e5a3c43d3f04897bdfe3492463c788661cff",
            "qualification-v2/reviewer_form.html":"d8a2b58f7c5ec6f9dc4429a6ce66dc8ca81153042005749986d45a91cfe09ca0",
            "qualification-v2/reviewer_instructions.txt":"d303800b545c6f79ccef186777b777bdb9c0a7f39b880f19b7c42971ec937e72",
            "qualification-v2/root_cause_review.txt":"529f182f79d63a2bafac10d5283eef84b947d308756c997f47b6a029310a4771",
            "qualification-v2/score_qualification.py":"eb26ceee191bba6617c2eb7559be04d39976323dd9e38715dc5756d87324b42a",
        }
        actual_paths = {str(path.relative_to(ROOT)) for directory in (ROOT / "qualification", ROOT / "qualification-v2") for path in directory.rglob("*") if path.is_file()}
        self.assertEqual(actual_paths, set(frozen))
        for relative, wanted in frozen.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), wanted, relative)

    def test_manual_readability_sample_has_neutral_context_and_questions(self):
        for case_id in ("V301", "V303", "V305", "V306"):
            self.assertIn(case_id, self.html)
        for scenario_id in ("AW1", "AW2", "AW3"):
            self.assertIn(scenario_id, self.html)
        for phrase in ("newly persisted official wind event", "UNAVAILABLE (RETRIEVAL_FINAL_FAILED)", "retry obtained parseable persisted evidence", "parser produced no reliable accepted evidence"):
            self.assertIn(phrase.lower(), self.html.lower())


if __name__ == "__main__":
    unittest.main()
