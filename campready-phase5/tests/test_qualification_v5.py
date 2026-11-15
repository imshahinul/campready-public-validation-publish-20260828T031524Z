import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "qualification-v5"


def load(name):
    return json.loads((V5 / name).read_text(encoding="utf-8"))


spec = importlib.util.spec_from_file_location("qualification_v5_scorer", V5 / "score_qualification.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


class QualificationV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instrument = load("instrument.json")
        cls.expected = load("expected_results.json")
        cls.schema = load("response.schema.json")
        cls.html = (V5 / "reviewer_form.html").read_text(encoding="utf-8")
        cls.instructions = (V5 / "reviewer_instructions.txt").read_text(encoding="utf-8")

    def passing_response(self, reviewer="R-I"):
        response = scorer.blank_response(reviewer)
        response.update(started_at="2026-11-15T09:00:00Z", submitted_at="2026-11-15T09:25:00Z", independence_attestation=True, glossary_read_attestation=True)
        universal = self.expected["universal"]
        expected = {row["case_id"]: row for row in self.expected["cases"]}
        for row in response["cases"]:
            exp = expected[row["case_id"]]
            action = exp["acceptable_actions"][0]
            pair = exp["allowed_followup_pairs"][0] if action == "NEEDS_FOLLOWUP" else ["NONE", "NONE"]
            note = "Short neutral rationale." if action in scorer.NOTE_REQUIRED_ACTIONS else None
            row.update(
                source_fact_identified=True, system_classification_identified=True,
                reviewer_annotation_boundary_understood=True, official_provenance_identified=True,
                selected_reviewer_action=action, data_quality_or_semantic_change=exp["distinction"],
                guard_required=exp["guard"], quality_defect_basis=exp["allowed_quality_defect_basis"][0],
                followup_operation=pair[0], followup_target=pair[1],
                safe_to_infer_appearance=exp["appearance"],
                safe_to_infer_disappearance=universal["safe_to_infer_disappearance"],
                safe_to_infer_resolution=universal["safe_to_infer_resolution"],
                safe_to_infer_trip_safety=universal["safe_to_infer_trip_safety"],
                safe_to_infer_road_passability=universal["safe_to_infer_road_passability"],
                safe_to_treat_source_silence_as_safe=universal["safe_to_treat_source_silence_as_safe"],
                shared_event_proves_duplicate_relationships=universal["shared_event_proves_duplicate_relationships"],
                reviewer_note=note,
            )
        expected_aw = {row["scenario_id"]: row["answers"] for row in self.expected["acknowledgement_workflow"]}
        for row in response["acknowledgement_workflow"]:
            row.update({key: value[0] if isinstance(value, list) else value for key, value in expected_aw[row["scenario_id"]].items()})
        return response

    @staticmethod
    def case(response, case_id):
        return next(row for row in response["cases"] if row["case_id"] == case_id)

    def choose(self, response, case_id, action, operation="NONE", target="NONE", note=None):
        row = self.case(response, case_id)
        row.update(selected_reviewer_action=action, followup_operation=operation, followup_target=target, reviewer_note=note)
        return row

    def test_identity_fresh_cases_and_concept_coverage(self):
        self.assertEqual(self.instrument["qualification_version"], "phase5-4-v5")
        self.assertEqual(self.schema["$id"], "campready.phase5.qualification_response.v5")
        self.assertEqual(self.instrument["case_order"], [f"Y5{i:02d}" for i in range(1, 13)])
        self.assertEqual(self.instrument["acknowledgement_workflow_order"], ["AW1", "AW2", "AW3"])
        text = json.dumps(self.instrument)
        for concept in ("SEMANTIC_APPEARANCE", "MATERIAL_UPDATE", "GUARDED_FINAL_FAILURE_WITH_PRIOR", "GUARDED_FINAL_FAILURE_WITHOUT_PRIOR", "RESCUED_RETRY_RELEVANCE_UNKNOWN", "PARSER_DATA_QUALITY", "EXPLICIT_UNKNOWN", "UNAVAILABLE_EVIDENCE_INTEGRITY", "SHARED_UPSTREAM_FANOUT", "GUARDED_NEEDS_FOLLOWUP", "SAFE_REVIEWED_NOT_ACTIONABLE", "ACKNOWLEDGEMENT_PROVENANCE_RECONSTRUCTION"):
            self.assertIn(concept, text)
        self.assertNotRegex(text, r"X4\d\d|v4-run|derived/v4|Juniper Basin|Otter Creek")

    def test_schema_is_strict_and_only_fresh_reviewers_are_eligible(self):
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), scorer.TOP_FIELDS)
        self.assertEqual(self.schema["properties"]["reviewer_id"]["enum"], ["R-I", "R-J"])
        properties = self.schema["$defs"]["case"]["properties"]
        for field in ("quality_defect_basis", "followup_operation", "followup_target"):
            self.assertIn(field, properties)
        self.assertNotIn("acknowledgement_required", properties)
        response = self.passing_response()
        scorer.validate_response(response)
        for reviewer in tuple(f"R-{x}" for x in "ABCDEFGH"):
            changed = copy.deepcopy(response); changed["reviewer_id"] = reviewer
            with self.assertRaisesRegex(ValueError, "REVIEWER_ID_INVALID"):
                scorer.validate_response(changed)
        self.assertTrue(scorer.evaluate_two([self.passing_response("R-I"), self.passing_response("R-J")])["contract_pass"])

    def test_quality_basis_contract_and_unknown_preservation(self):
        cases = {row["case_id"]: row for row in self.instrument["cases"]}
        exp = {row["case_id"]: row for row in self.expected["cases"]}
        wanted = {"Y503":"FINAL_RETRIEVAL_FAILURE", "Y504":"FINAL_RETRIEVAL_FAILURE", "Y505":"NONE", "Y506":"PARSER_FAILURE", "Y507":"NONE", "Y508":"EVIDENCE_INTEGRITY_FAILURE", "Y510":"FINAL_RETRIEVAL_FAILURE", "Y512":"FINAL_RETRIEVAL_FAILURE"}
        for case_id, basis in wanted.items():
            self.assertEqual(exp[case_id]["allowed_quality_defect_basis"], [basis])
        for case_id in ("Y505", "Y507"):
            self.assertEqual(cases[case_id]["source_fact"]["retrieval_status"], "SUCCESS")
            self.assertEqual(cases[case_id]["source_fact"]["parser_status"], "SUCCESS")
            self.assertEqual(cases[case_id]["source_fact"]["relevance_status"], "UNKNOWN")
            self.assertEqual(cases[case_id]["system_classification"]["data_quality_state"], "NONE")
            self.assertFalse(cases[case_id]["system_classification"]["alert_detail_guard_required"])
            self.assertEqual(exp[case_id]["acceptable_actions"], ["REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE", "NEEDS_FOLLOWUP"])

    def test_equivalent_and_short_notes_never_change_structured_result(self):
        notes = ["does not assert", "does not claim", "relevance remains undetermined", "Short neutral rationale.", "Different wording entirely."]
        for case_id in ("Y505", "Y507"):
            outcomes = []
            for note in notes:
                response = self.passing_response()
                self.choose(response, case_id, "REVIEWED_VALID", note=note)
                outcomes.append(scorer.score_response(response))
            self.assertTrue(all(result["contract_pass"] for result in outcomes))
            self.assertEqual([r["mandatory_violations"] for r in outcomes], [outcomes[0]["mandatory_violations"]] * len(outcomes))

    def test_historical_false_negative_concepts_are_repaired_without_rescoring(self):
        # R-G X405/X407-style: REVIEWED_VALID UNKNOWN is structured, not synonym-scored.
        for case_id in ("Y505", "Y507"):
            for note in ("does not assert", "does not claim"):
                response = self.passing_response(); self.choose(response, case_id, "REVIEWED_VALID", note=note)
                self.assertTrue(scorer.score_response(response)["contract_pass"])
        # R-H X404-style: terse quality prose cannot invalidate a matching structured basis.
        response = self.passing_response(); self.case(response, "Y504")["reviewer_note"] = "Four attempts ended without evidence."
        self.assertTrue(scorer.score_response(response)["contract_pass"])
        # R-G X410-style: concrete structured scope works without lexical shibboleths.
        response = self.passing_response(); self.choose(response, "Y510", "NEEDS_FOLLOWUP", "VERIFY", "PERSISTED_EVIDENCE", "Ordinary review rationale.")
        self.assertTrue(scorer.score_response(response)["contract_pass"])

    def test_genuine_unknown_hold_defect_cannot_be_repaired_by_tokens(self):
        for note in ("retrieval failure evidence unavailable", "parser provenance integrity acquisition"):
            response = self.passing_response()
            row = self.choose(response, "Y507", "DATA_QUALITY_HOLD", note=note)
            row["quality_defect_basis"] = "NONE"
            result = scorer.score_response(response)
            self.assertIn("Y507:contractually_invalid_action", result["mandatory_violations"]["DISPOSITION_FIDELITY"])
            self.assertIn("Y507:quality_defect_basis", result["mandatory_violations"]["DISPOSITION_FIDELITY"])
            assessment = next(x for x in result["disposition_assessments"] if x["case_id"] == "Y507")
            self.assertEqual(assessment["action_classification"], "CONTRACTUALLY_INVALID_ACTION")
            self.assertEqual(assessment["structured_basis_classification"], "STRUCTURED_DISPOSITION_BASIS_INVALID")

    def test_followup_scope_is_structured_and_prose_cannot_repair_it(self):
        for operation, target in (("NONE", "PERSISTED_EVIDENCE"), ("VERIFY", "NONE"), ("NONE", "NONE")):
            response = self.passing_response()
            self.choose(response, "Y510", "NEEDS_FOLLOWUP", operation, target, "bounded investigate evidence verify inspect obtain compare contact check")
            self.assertIn("Y510:followup_scope", scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])
        response = self.passing_response()
        self.choose(response, "Y510", "NEEDS_FOLLOWUP", "INSPECT", "PROVENANCE_RECORD", "Look at the record.")
        self.assertTrue(scorer.score_response(response)["contract_pass"])

    def test_quality_basis_adversarial_vectors(self):
        response = self.passing_response(); self.case(response, "Y505")["quality_defect_basis"] = "FINAL_RETRIEVAL_FAILURE"
        self.case(response, "Y505")["reviewer_note"] = "retrieval final failure unavailable"
        self.assertIn("Y505:quality_defect_basis", scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])
        response = self.passing_response(); self.assertTrue(scorer.score_response(response)["contract_pass"])
        response = self.passing_response(); self.case(response, "Y506")["quality_defect_basis"] = "NONE"
        self.case(response, "Y506")["reviewer_note"] = "parser failure"
        self.assertIn("Y506:quality_defect_basis", scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])

    def test_note_presence_only_contract(self):
        for case_id, action in (("Y511", "REVIEWED_NOT_ACTIONABLE"), ("Y503", "DATA_QUALITY_HOLD"), ("Y510", "NEEDS_FOLLOWUP")):
            response = self.passing_response(); row = self.case(response, case_id); row["reviewer_note"] = "  "
            with self.assertRaisesRegex(ValueError, "WORKFLOW_NOTE_REQUIRED"):
                scorer.validate_response(response)
        response = self.passing_response(); self.case(response, "Y505")["reviewer_note"] = None
        self.assertTrue(scorer.score_response(response)["contract_pass"])

    def test_no_free_text_keyword_or_synonym_scorer_exists(self):
        source = (V5 / "score_qualification.py").read_text(encoding="utf-8").lower()
        for forbidden in ("unknown_valid_classification_note", "unknown_not_actionable_note", "actual_quality_defect_note", "concrete_investigation_note", "quality_terms", "investigation_terms", "_note_fidelity"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("reviewer_note_unsafe_inference", source)
        self.assertFalse(self.expected["contract"]["free_text_semantics_scored"])

    def test_review_boundary_remains_separate_from_disposition(self):
        response = self.passing_response(); self.choose(response, "Y507", "DATA_QUALITY_HOLD", note="tokens do not matter")
        result = scorer.score_response(response)
        self.assertFalse(result["mandatory_violations"]["REVIEW_BOUNDARY"])
        self.assertTrue(result["mandatory_violations"]["DISPOSITION_FIDELITY"])
        self.case(response, "Y507")["source_fact_identified"] = False
        self.assertTrue(scorer.score_response(response)["mandatory_violations"]["REVIEW_BOUNDARY"])

    def test_anti_pass_manufacturing_every_category(self):
        mutations = [
            ("GUARD", "Y503", "guard_required", "NO"),
            ("DATA_QUALITY_UNKNOWN", "Y507", "data_quality_or_semantic_change", "DATA_QUALITY"),
            ("PROVENANCE", "Y501", "official_provenance_identified", False),
            ("REVIEW_BOUNDARY", "Y501", "system_classification_identified", False),
            ("FANOUT", "Y509", "shared_event_proves_duplicate_relationships", "YES"),
            ("UNSAFE_INFERENCE", "Y507", "safe_to_infer_trip_safety", "YES"),
            ("UNSAFE_INFERENCE", "Y507", "safe_to_infer_road_passability", "YES"),
            ("UNSAFE_INFERENCE", "Y507", "safe_to_treat_source_silence_as_safe", "YES"),
        ]
        for category, case_id, field, value in mutations:
            response = self.passing_response(); self.case(response, case_id)[field] = value
            self.assertTrue(scorer.score_response(response)["mandatory_violations"][category], (category, field))
        response = self.passing_response(); self.choose(response, "Y511", "REVIEWED_VALID")
        self.assertTrue(scorer.score_response(response)["mandatory_violations"]["DISPOSITION_FIDELITY"])

    def test_acknowledgement_workflow_is_preserved_and_zero_tolerance(self):
        response = self.passing_response(); self.assertTrue(scorer.score_response(response)["contract_pass"])
        proofs = {("AW1", "eventual_manual_acknowledgement_required", "YES"), ("AW1", "direct_new_to_acknowledged_permitted", "NO"), ("AW2", "later_acknowledged_transition_available", "YES"), ("AW2", "eventual_acknowledgement_required", "NO"), ("AW2", "data_quality_alone_creates_acknowledgement_obligation", "NO"), ("AW3", "eventual_acknowledgement_required", "YES"), ("AW3", "unresolved_followup_note_required", "YES"), ("AW3", "acknowledgement_erases_unresolved_condition", "NO")}
        truth = {(row["scenario_id"], key, value) for row in self.expected["acknowledgement_workflow"] for key, value in row["answers"].items() if not isinstance(value, list)}
        self.assertTrue(proofs <= truth)
        for scenario_id, field, wanted in proofs:
            changed = copy.deepcopy(response); row = next(x for x in changed["acknowledgement_workflow"] if x["scenario_id"] == scenario_id); row[field] = "NO" if wanted == "YES" else "YES"
            self.assertTrue(scorer.score_response(changed)["mandatory_violations"]["ACKNOWLEDGEMENT_WORKFLOW"])

    def test_categories_no_percentage_and_disposition_taxonomy(self):
        categories = {"GUARD", "DATA_QUALITY_UNKNOWN", "PROVENANCE", "REVIEW_BOUNDARY", "DISPOSITION_FIDELITY", "FANOUT", "UNSAFE_INFERENCE", "ACKNOWLEDGEMENT_WORKFLOW"}
        self.assertEqual(set(scorer.CATEGORIES), categories)
        self.assertEqual(set(self.expected["contract"]["zero_tolerance_categories"]), categories)
        self.assertIsNone(self.expected["contract"]["percentage_threshold"])
        result = scorer.score_response(self.passing_response())
        classes = {x["action_classification"] for x in result["disposition_assessments"]}
        self.assertEqual(classes, {"CONTRACTUALLY_REQUIRED_ACTION", "ALLOWED_DISCRETIONARY_ACTION"})

    def test_glossary_and_instructions_define_structured_note_boundary(self):
        glossary = self.instrument["glossary"]
        for term in ("QUALITY DEFECT BASIS", "FOLLOW-UP OPERATION", "FOLLOW-UP TARGET", "reviewer_note"):
            self.assertIn(term, glossary)
        combined = json.dumps(glossary) + self.instructions
        for phrase in ("does not replace", "cannot override structured responses", "trip-safety", "road-passability", "notification", "unrepresented lifecycle"):
            self.assertIn(phrase, combined)
        self.assertNotRegex(json.dumps(glossary), r"\bY5\d\d\b|correct answer|answer key")

    def test_form_is_offline_readable_leak_free_and_bound_to_digest(self):
        lower = self.html.lower()
        self.assertNotRegex(lower, r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?')
        for forbidden in ("localstorage", "sessionstorage", "indexeddb", "fetch(", "xmlhttprequest", "websocket", "sendbeacon", "score_qualification", "expected_results", "acceptable_actions", "contract_pass", "hold_phase5_4", "pass_phase5_4", "r-g x", "r-h x"):
            self.assertNotIn(forbidden, lower)
        for heading in ("SOURCE FACT", "SYSTEM CLASSIFICATION", "YOUR REVIEW", "ACKNOWLEDGEMENT WORKFLOW"):
            self.assertIn(heading, self.html)
        for field in ("Quality defect basis", "Follow-up operation", "Follow-up target", "Reviewer note (audit rationale)"):
            self.assertIn(field, self.html)
        self.assertEqual(hashlib.sha256((V5 / "instrument.json").read_bytes()).hexdigest(), re.search(r'DIGEST="sha256:([0-9a-f]{64})"', self.html).group(1))
        self.assertNotRegex(self.html, r'<option>R-[A-H]</option>')
        self.assertIn("R-I", self.html); self.assertIn("R-J", self.html)

    def test_manual_readability_cases_and_workflows_are_rendered(self):
        for case_id in ("Y503", "Y505", "Y506", "Y507", "Y510"):
            self.assertIn(case_id, self.html)
        for scenario_id in ("AW1", "AW2", "AW3"):
            self.assertIn(scenario_id, self.html)
        for concept in ("quality_defect_basis", "followup_operation", "followup_target", "reviewer_note"):
            self.assertIn(concept, self.html)

    def test_historical_v1_v2_v3_v4_are_byte_identical_to_starting_commit(self):
        repo = ROOT.parent
        historical = ["campready-phase5/qualification", "campready-phase5/qualification-v2", "campready-phase5/qualification-v3", "campready-phase5/qualification-v4"]
        for directory in historical:
            tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "7830232a797f20d748f40dcbe8bffc9cb1bda690", "--", directory], cwd=repo, text=True).splitlines()
            current_tracked = subprocess.check_output(["git", "ls-files", directory], cwd=repo, text=True).splitlines()
            self.assertEqual(current_tracked, tracked, directory)
            for relative in tracked:
                frozen = subprocess.check_output(["git", "show", f"7830232a797f20d748f40dcbe8bffc9cb1bda690:{relative}"], cwd=repo)
                self.assertEqual((repo / relative).read_bytes(), frozen, relative)


if __name__ == "__main__":
    unittest.main()
