import copy
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "campready-phase5" / "qualification"
V2 = ROOT / "campready-phase5" / "qualification-v2"
AUDIT = ROOT.parent / "audit"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module():
    spec = importlib.util.spec_from_file_location("qualification_v2_scorer", V2 / "score_qualification.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualificationV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instrument = json.loads((V2 / "instrument.json").read_text())
        cls.expected = json.loads((V2 / "expected_results.json").read_text())
        cls.schema = json.loads((V2 / "response.schema.json").read_text())
        cls.instructions = (V2 / "reviewer_instructions.txt").read_text()
        cls.form = (V2 / "reviewer_form.html").read_text()
        cls.root_cause = (V2 / "root_cause_review.txt").read_text()
        cls.scorer = load_module()

    def passing_response(self, reviewer_id="R-C"):
        response = self.scorer.blank_response(reviewer_id)
        response.update(started_at="2026-11-14T10:00:00Z", submitted_at="2026-11-14T10:20:00Z",
                        independence_attestation=True, glossary_read_attestation=True)
        universal = self.expected["universal"]
        by_id = {row["case_id"]: row for row in self.expected["cases"]}
        for row in response["cases"]:
            expected = by_id[row["case_id"]]
            row.update(universal)
            row.update(selected_reviewer_action=expected["acceptable_actions"][0],
                       data_quality_or_semantic_change=expected["distinction"],
                       guard_required=expected["guard"],
                       safe_to_infer_appearance=expected["appearance"],
                       acknowledgement_required=expected["ack"],
                       reviewer_note="Bounded reviewer note" if expected["acceptable_actions"][0] in {
                           "REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"} else None)
        return response

    def test_v1_files_are_byte_frozen(self):
        frozen = {
            "instrument.json": "40cf3375c929603d1415006bd6f02df385dc690c221963ed45833ba39268f188",
            "reviewer_packet.txt": "00fdd4735284f35a52bda08ac3336bba81475bbf1b4d0321b3d1a6d8667e86f2",
            "expected_results.json": "a58522a7761f7cf4db72cbb0ece60d788f7951f3022d9d49a65f5ff69e9acda4",
            "response.schema.json": "94fd83adb2c4c972da258a0182b34640871927e90a333aa53450508a2db9bad6",
            "score_qualification.py": "11f8b92c99e4513cf36ea3175de823232645e0945835997ce5f4dc6f018ac19b",
            "reviewer_form.html": "77c855165d77aeec37906bd8fe837f4b67fbf528c36d5d03dd6d18d11fecaec1",
        }
        self.assertEqual({name: digest(V1 / name) for name in frozen}, frozen)

    def test_v1_responses_and_report_are_untouched(self):
        frozen = {
            AUDIT / "phase5_4_reviews" / "reviewer_A_response.json": "343441cd53d3557f2b1a26e7be18c8d06caf259389e2f79f3e5a92bdf14d19ba",
            AUDIT / "phase5_4_reviews" / "reviewer_B_response.json": "6e13a44700309845689c13b65d4d4b51dfc2c182425bb1185d90ff3fea61630a",
            AUDIT / "campready_phase5_4b_two_human_qualification_20261114T065920Z.txt": "abc231d57e9f092a5422dd37402a3c4ae0f56b3a661107916760bd8c11aceaa2",
        }
        self.assertEqual({path: digest(path) for path in frozen}, frozen)

    def test_general_glossary_disambiguates_without_case_hints(self):
        for text in (self.instructions, self.form):
            self.assertIn("ultimately record an explicit acknowledgement", text)
            self.assertRegex(text, r"(?is)does (?:not|<em>not</em>) mean ACKNOWLEDGED must be the immediate next")
            self.assertIn("retrieval, parser, provenance, or evidence-integrity failure", text)
            self.assertIn("does not support a determinate semantic conclusion", text)
            self.assertIn("does not mean safe", text)
            self.assertIn("successfully rescued does not by itself", text)
        glossary = self.instructions.split("WORKFLOW")[0]
        self.assertNotRegex(glossary, r"\bQ(?:0[1-9]|1[0-2])\b")
        self.assertNotIn("Choose UNKNOWN here", glossary)
        self.assertNotIn("correct answer", glossary.lower())

    def test_new_parallel_cases_cover_every_required_concept(self):
        classes = {case["task_class"] for case in self.instrument["cases"]}
        required = {
            "ORDINARY_SEMANTIC_APPEARANCE", "MATERIAL_SEMANTIC_UPDATE",
            "GUARDED_FINAL_FAILURE_RETAINED_PRIOR", "GUARDED_FINAL_FAILURE_NO_PRIOR",
            "RESCUED_RETRY_UNKNOWN_RELEVANCE", "PARSER_EVIDENCE_FAILURE",
            "EXPLICIT_UNKNOWN_NON_SEMANTIC", "UNAVAILABLE_EVIDENCE_INTEGRITY",
            "SHARED_EVENT_FANOUT", "GUARDED_OPEN_FOLLOWUP",
            "SAFE_REVIEW_ONLY_NOT_ACTIONABLE", "ACKNOWLEDGEMENT_PROVENANCE_RECONSTRUCTION",
        }
        self.assertEqual(classes, required)
        self.assertEqual(self.instrument["qualification_version"], "phase5-4-v2")
        self.assertEqual(len(self.instrument["cases"]), 12)
        self.assertEqual([case["case_id"] for case in self.instrument["cases"]], self.instrument["case_order"])
        self.assertTrue(all(case["evidence"]["provenance_run_id"] and case["evidence"]["provenance_record_ref"] for case in self.instrument["cases"]))

    def test_rescued_retry_and_unknown_truth_are_not_changed(self):
        by_class = {case["task_class"]: case for case in self.instrument["cases"]}
        expected = {row["case_id"]: row for row in self.expected["cases"]}
        rescued = by_class["RESCUED_RETRY_UNKNOWN_RELEVANCE"]
        explicit = by_class["EXPLICIT_UNKNOWN_NON_SEMANTIC"]
        parser = by_class["PARSER_EVIDENCE_FAILURE"]
        self.assertTrue(rescued["source_fact"]["retry_rescued"])
        self.assertEqual(rescued["source_fact"]["parser_status"], "SUCCESS")
        self.assertEqual(expected[rescued["case_id"]]["distinction"], "UNKNOWN")
        self.assertEqual(expected[explicit["case_id"]]["distinction"], "UNKNOWN")
        self.assertEqual(expected[parser["case_id"]]["distinction"], "DATA_QUALITY")

    def test_acknowledgement_truth_and_immediate_actions_remain_separate(self):
        expected = {row["case_id"]: row for row in self.expected["cases"]}
        for case_id in ("Q03", "Q04", "Q10"):
            self.assertEqual(expected[case_id]["ack"], "YES")
            self.assertNotIn("ACKNOWLEDGED", expected[case_id]["acceptable_actions"])
        self.assertEqual(expected["Q12"]["acceptable_actions"], ["ACKNOWLEDGED"])

    def test_schema_is_strict_and_fresh_reviewers_only(self):
        self.assertEqual(self.schema["$id"], "campready.phase5.qualification_response.v2")
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(self.schema["properties"]["reviewer_id"]["enum"], ["R-C", "R-D"])
        self.assertIn("glossary_read_attestation", self.schema["required"])
        self.assertFalse(self.schema["$defs"]["case"]["additionalProperties"])
        response = self.passing_response()
        self.scorer.validate_response(response)
        for mutation in (lambda r: r.update(extra=True), lambda r: r.update(reviewer_id="R-A"),
                         lambda r: r.update(glossary_read_attestation=False)):
            invalid = copy.deepcopy(response)
            mutation(invalid)
            with self.assertRaises(ValueError):
                self.scorer.validate_response(invalid)

    def test_scorer_is_deterministic_and_has_no_percentage_threshold(self):
        response = self.passing_response()
        first = self.scorer.score_response(response)
        second = self.scorer.score_response(copy.deepcopy(response))
        self.assertEqual(first, second)
        self.assertTrue(first["contract_pass"])
        self.assertNotIn("percentage", json.dumps(first).lower())
        bad = copy.deepcopy(response)
        bad["cases"][2]["acknowledgement_required"] = "NO"
        result = self.scorer.score_response(bad)
        self.assertFalse(result["contract_pass"])
        self.assertIn("Q03:acknowledgement_required", result["safety_critical_violations"])

    def test_both_fresh_reviewers_must_independently_pass(self):
        rc, rd = self.passing_response("R-C"), self.passing_response("R-D")
        rd["submitted_at"] = "2026-11-14T10:25:00Z"
        self.assertTrue(self.scorer.evaluate_two([rc, rd])["contract_pass"])
        rd["cases"][4]["data_quality_or_semantic_change"] = "DATA_QUALITY"
        self.assertFalse(self.scorer.evaluate_two([rc, rd])["contract_pass"])
        with self.assertRaisesRegex(ValueError, "FRESH_REVIEWERS"):
            self.scorer.evaluate_two([rc, copy.deepcopy(rc)])

    def test_form_is_offline_self_contained_and_exports_exact_identity(self):
        self.assertIn("file://", self.form)
        self.assertIn("glossary_read_attestation", self.form)
        self.assertIn("phase5-4-v2", self.form)
        self.assertIn("sha256:" + digest(V2 / "instrument.json"), self.form)
        self.assertNotRegex(self.form, r"<(?:script|link)[^>]+(?:src|href)=[\"']https?://")
        self.assertNotRegex(self.form, r"\b(fetch|XMLHttpRequest|WebSocket|localStorage|sessionStorage)\s*\(")
        self.assertNotIn("expected_results.json", self.form)
        self.assertNotIn("score_qualification", self.form)
        self.assertNotIn("contract_pass", self.form)
        self.assertNotIn("R-A", self.form)
        self.assertNotIn("R-B", self.form)
        self.assertEqual(re.findall(r'data-id="(Q\d{2})"', self.form), self.instrument["case_order"])

    def test_no_answer_or_historical_response_leakage(self):
        forbidden = ["reviewer_A_response", "reviewer_B_response", "343441cd", "6e13a447",
                     "T3:acknowledgement_required", "T5:data_quality_or_semantic_change"]
        reviewer_surface = self.form + self.instructions
        for token in forbidden:
            self.assertNotIn(token, reviewer_surface)
        self.assertTrue(self.expected["never_reviewer_facing"])

    def test_no_network_delivery_or_canonical_writes(self):
        code = (V2 / "score_qualification.py").read_text()
        for token in ("urllib", "requests", "http.client", "socket", "subprocess", "notification_eligible = True"):
            self.assertNotIn(token, code)
        self.assertNotIn("campready-phase4", code)
        self.assertNotIn("campready-phase5/state", code)
        self.assertNotIn("send", code.lower())

    def test_root_cause_assessments_and_historical_hold_are_explicit(self):
        self.assertIn("HOLD_PHASE5_4_USABILITY_GUARD_COMPREHENSION", self.root_cause)
        self.assertIn("INSUFFICIENT_CONTEXT", self.root_cause)
        self.assertIn("AMBIGUOUS", self.root_cause)
        for concept in ("Manual acknowledgement", "Final retrieval DATA_QUALITY", "Rescued-retry UNKNOWN", "Explicit UNKNOWN", "Workflow action vs eventual acknowledgement"):
            self.assertIn(concept, self.root_cause)


if __name__ == "__main__":
    unittest.main()
