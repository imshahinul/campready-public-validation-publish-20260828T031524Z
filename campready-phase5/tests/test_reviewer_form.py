"""Frozen-surface checks for the local, offline Phase 5-4A reviewer form."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUALIFICATION = ROOT / "campready-phase5" / "qualification"
FORM = QUALIFICATION / "reviewer_form.html"
INSTRUMENT = QUALIFICATION / "instrument.json"
PACKET = QUALIFICATION / "reviewer_packet.txt"
SCHEMA = QUALIFICATION / "response.schema.json"
SCORER = QUALIFICATION / "score_qualification.py"
CASE_ORDER = [f"T{i}" for i in range(1, 13)]
FROZEN_HASHES = {
    "instrument.json": "40cf3375c929603d1415006bd6f02df385dc690c221963ed45833ba39268f188",
    "reviewer_packet.txt": "00fdd4735284f35a52bda08ac3336bba81475bbf1b4d0321b3d1a6d8667e86f2",
    "expected_results.json": "a58522a7761f7cf4db72cbb0ece60d788f7951f3022d9d49a65f5ff69e9acda4",
    "response.schema.json": "94fd83adb2c4c972da258a0182b34640871927e90a333aa53450508a2db9bad6",
    "score_qualification.py": "11f8b92c99e4513cf36ea3175de823232645e0945835997ce5f4dc6f018ac19b",
}


def _html() -> str:
    return FORM.read_text(encoding="utf-8")


def _embedded_cases() -> list[dict]:
    match = re.search(r"const CASES=(\[.*?\]);\nlet startedAt", _html(), re.DOTALL)
    assert match
    return json.loads(match.group(1))


def _scorer_module():
    spec = importlib.util.spec_from_file_location("phase5_qualification_scorer", SCORER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample(reviewer_id: str) -> dict:
    cases = []
    for case_id in CASE_ORDER:
        cases.append({
            "case_id": case_id,
            "source_fact_identified": True,
            "system_classification_identified": True,
            "reviewer_annotation_boundary_understood": True,
            "official_provenance_identified": True,
            "selected_reviewer_action": "REVIEWED_VALID",
            "data_quality_or_semantic_change": "UNKNOWN",
            "guard_required": "NO",
            "safe_to_infer_appearance": "NO",
            "safe_to_infer_disappearance": "NO",
            "safe_to_infer_resolution": "NO",
            "safe_to_infer_trip_safety": "NO",
            "safe_to_infer_road_passability": "NO",
            "safe_to_treat_source_silence_as_safe": "NO",
            "acknowledgement_required": "NO",
            "shared_event_proves_duplicate_relationships": "NO",
            "acknowledgement_means_safe_or_notification_approved": "NO",
            "reviewer_note": None,
        })
    return {
        "qualification_version": "phase5-4a-v1",
        "instrument_digest": "sha256:" + FROZEN_HASHES["instrument.json"],
        "reviewer_id": reviewer_id,
        "started_at": "2026-11-12T07:00:00.000Z",
        "submitted_at": "2026-11-12T07:30:00.000Z",
        "independence_attestation": True,
        "cases": cases,
    }


class ReviewerFormTests(unittest.TestCase):
    def test_cases_are_exact_frozen_content_and_order(self) -> None:
        instrument = json.loads(INSTRUMENT.read_text(encoding="utf-8"))
        embedded = _embedded_cases()
        self.assertEqual([case["case_id"] for case in embedded], CASE_ORDER)
        self.assertEqual(CASE_ORDER, instrument["case_order"])
        self.assertEqual(embedded, instrument["cases"])
        packet = PACKET.read_text(encoding="utf-8")
        for case in embedded:
            self.assertIn(f"===== {case['case_id']} — {case['task_class']} =====", packet)
            for section in ("evidence", "source_fact", "system_classification"):
                rendered = json.dumps(case[section], ensure_ascii=False, sort_keys=True, indent=2)
                self.assertIn(rendered, packet)

    def test_no_answer_key_designations_or_scoring_logic_leak(self) -> None:
        html = _html()
        forbidden = (
            "DERIVED_CONTRACT_FIXTURE", "universal_safety_critical", "acceptable_actions",
            "fanout_duplicates", "safety_critical_violations", "score_response",
            "expected_results.json", "contract_pass",
        )
        for token in forbidden:
            self.assertNotIn(token, html)
        # Ordinary frozen title text uses "Observed"; the prohibited designation is all-caps.
        self.assertIsNone(re.search(r"\bOBSERVED\b", html))

    def test_form_is_fully_offline_and_has_no_persistence_or_analytics(self) -> None:
        html = _html()
        forbidden = (
            "fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "localStorage",
            "sessionStorage", "document.cookie", "navigator.sendBeacon", "analytics",
            "googletag", "gtag(", "cdn.",
        )
        for token in forbidden:
            self.assertNotIn(token, html)
        self.assertIsNone(re.search(r"<(?:script|link)[^>]+(?:src|href)\s*=", html, re.IGNORECASE))
        self.assertIsNone(re.search(r"<style[^>]+src\s*=", html, re.IGNORECASE))
        self.assertNotIn('http-equiv="refresh"', html.lower())

    def test_identity_attestation_controls_and_digest_are_frozen(self) -> None:
        html = _html()
        self.assertEqual(html.count('name="reviewer_id" value="R-A"'), 1)
        self.assertEqual(html.count('name="reviewer_id" value="R-B"'), 1)
        self.assertIn("const REVIEWER_IDS=['R-A','R-B'];", html)
        self.assertIn("independence_attestation:document.getElementById('attestation').checked", html)
        self.assertIn("Independence attestation is required.", html)
        self.assertIn("sha256:" + FROZEN_HASHES["instrument.json"], html)
        self.assertIn("startedAt=new Date().toISOString()", html)

    def test_all_frozen_response_fields_and_workflow_note_rule_are_represented(self) -> None:
        html = _html()
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        required = schema["$defs"]["case"]["required"]
        for field in required:
            self.assertIsNotNone(re.search(rf"\b{re.escape(field)}\b", html))
        for action in schema["$defs"]["case"]["properties"]["selected_reviewer_action"]["enum"]:
            self.assertIn(action, html)
        self.assertIn("reviewer note is required for the selected workflow action", html)
        self.assertIn("After submission, do not edit the JSON response.", html)
        self.assertIn("PRINT / SAVE HUMAN COPY", html)
        self.assertIn("window.print()", html)

    def test_generated_samples_validate_against_schema_and_existing_scorer_input(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        scorer = _scorer_module()
        top_required = schema["required"]
        case_schema = schema["$defs"]["case"]
        for reviewer_id in ("R-A", "R-B"):
            response = _sample(reviewer_id)
            # Deterministic validation directly against every frozen schema constraint used by the form.
            self.assertEqual(set(response), set(top_required))
            self.assertEqual(response["qualification_version"], schema["properties"]["qualification_version"]["const"])
            self.assertIn(response["reviewer_id"], schema["properties"]["reviewer_id"]["enum"])
            self.assertIs(response["independence_attestation"], True)
            self.assertRegex(response["instrument_digest"], schema["properties"]["instrument_digest"]["pattern"])
            self.assertEqual(len(response["cases"]), schema["properties"]["cases"]["minItems"])
            for row in response["cases"]:
                self.assertEqual(set(row), set(case_schema["required"]))
                self.assertRegex(row["case_id"], case_schema["properties"]["case_id"]["pattern"])
                self.assertIn(row["selected_reviewer_action"], case_schema["properties"]["selected_reviewer_action"]["enum"])
                self.assertIn(row["data_quality_or_semantic_change"], case_schema["properties"]["data_quality_or_semantic_change"]["enum"])
            self.assertIsNone(scorer.validate_response(response))

    def test_browser_only_exports_contract_json_and_never_runs_or_displays_result(self) -> None:
        html = _html()
        self.assertIn("JSON.stringify(response,null,2)", html)
        self.assertIn("reviewer_A_response.json", html)
        self.assertIn("reviewer_B_response.json", html)
        self.assertIn("new Blob", html)
        self.assertIn("URL.createObjectURL", html)
        for token in ("score_response", "evaluate_two", "contract_pass", "discretionary_disagreements"):
            self.assertNotIn(token, html)

    def test_frozen_artifacts_unchanged_and_git_diff_is_authorized(self) -> None:
        for name, expected in FROZEN_HASHES.items():
            actual = hashlib.sha256((QUALIFICATION / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected)
        lines = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, check=True,
            text=True, capture_output=True,
        ).stdout.splitlines()
        changed = {line[3:] for line in lines if line.strip()}
        allowed = {
            "campready-phase5/qualification/reviewer_form.html",
            "campready-phase5/tests/test_reviewer_form.py",
        }
        self.assertLessEqual(changed, allowed)


if __name__ == "__main__":
    unittest.main()
