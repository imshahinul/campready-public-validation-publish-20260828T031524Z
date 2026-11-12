import copy
import hashlib
import importlib.util
import json
import pathlib
import socket
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE5 = ROOT / "campready-phase5"
QUAL = PHASE5 / "qualification"
SPEC = importlib.util.spec_from_file_location("score_qualification", QUAL / "score_qualification.py")
score = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = score
SPEC.loader.exec_module(score)


def valid_response(reviewer_id="R-A"):
    rubric = json.loads((QUAL / "expected_results.json").read_text())
    universal = rubric["universal_safety_critical"]
    cases = []
    for expected in rubric["cases"]:
        action = expected["acceptable_actions"][0]
        row = {
            "case_id": expected["case_id"],
            "source_fact_identified": True,
            "system_classification_identified": True,
            "reviewer_annotation_boundary_understood": True,
            "official_provenance_identified": True,
            "selected_reviewer_action": action,
            "data_quality_or_semantic_change": expected["distinction"],
            "guard_required": expected["guard"],
            "safe_to_infer_appearance": expected["appearance"],
            "safe_to_infer_disappearance": universal["safe_to_infer_disappearance"],
            "safe_to_infer_resolution": universal["safe_to_infer_resolution"],
            "safe_to_infer_trip_safety": universal["safe_to_infer_trip_safety"],
            "safe_to_infer_road_passability": universal["safe_to_infer_road_passability"],
            "safe_to_treat_source_silence_as_safe": universal["safe_to_treat_source_silence_as_safe"],
            "acknowledgement_required": expected["ack"],
            "shared_event_proves_duplicate_relationships": expected["fanout_duplicates"],
            "acknowledgement_means_safe_or_notification_approved": universal["acknowledgement_means_safe_or_notification_approved"],
            "reviewer_note": "Bounded workflow note" if action in {"REVIEWED_NOT_ACTIONABLE", "DATA_QUALITY_HOLD", "NEEDS_FOLLOWUP"} else None,
        }
        cases.append(row)
    return {
        "qualification_version": "phase5-4a-v1",
        "instrument_digest": score.sha256(QUAL / "instrument.json"),
        "reviewer_id": reviewer_id,
        "started_at": "2026-11-12T07:00:00Z",
        "submitted_at": "2026-11-12T07:30:00Z",
        "independence_attestation": True,
        "cases": cases,
    }


class QualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_socket = socket.socket
        cls.old_connection = socket.create_connection
        cls.old_urlopen = urllib.request.urlopen
        deny = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))
        socket.socket = deny
        socket.create_connection = deny
        urllib.request.urlopen = deny

    @classmethod
    def tearDownClass(cls):
        socket.socket = cls.old_socket
        socket.create_connection = cls.old_connection
        urllib.request.urlopen = cls.old_urlopen

    def test_deterministic_packet_fixed_order_and_required_classes(self):
        instrument = json.loads((QUAL / "instrument.json").read_text())
        self.assertEqual(instrument["case_order"], [f"T{i}" for i in range(1, 13)])
        self.assertEqual([x["case_id"] for x in instrument["cases"]], instrument["case_order"])
        classes = {x["task_class"] for x in instrument["cases"]}
        required = {"NWS_SEMANTIC_APPEARANCE","USFS_CAMPGROUND_PAGE_MATERIAL_UPDATE","ALERT_DETAIL_FINAL_FAILURE_RETAINED_PRIOR","ALERT_DETAIL_FINAL_FAILURE_NO_PRIOR","ALERT_DETAIL_RETRY_RESCUE","PARSER_DATA_QUALITY_NO_SEMANTIC_CHANGE","EXPLICIT_UNKNOWN_NON_SEMANTIC","SOURCE_TITLE_EVIDENCE_UNAVAILABLE","SHARED_UPSTREAM_FANOUT","NEEDS_FOLLOWUP_WORKFLOW","VALID_REVIEW_ONLY_NOT_ACTIONABLE","ACKNOWLEDGEMENT_PROVENANCE_RECONSTRUCTION"}
        self.assertEqual(classes, required)
        self.assertEqual((QUAL / "reviewer_packet.txt").read_text(), score.render_packet())

    def test_packet_is_blinded_and_contains_required_blocks(self):
        packet = (QUAL / "reviewer_packet.txt").read_text()
        lower = packet.lower()
        for leaked in ("expected_results", "acceptable_actions", "fixture_origin", "derived_contract_fixture", "observed\""):
            self.assertNotIn(leaked, lower)
        self.assertEqual(packet.count("SOURCE FACT\n"), 12)
        self.assertEqual(packet.count("SYSTEM CLASSIFICATION\n"), 12)
        self.assertEqual(packet.count("BLANK REVIEWER RESPONSE\n"), 12)
        self.assertNotIn("adjudicated_interpretation", packet)

    def test_response_schema_identity_and_strict_validation(self):
        schema = json.loads((QUAL / "response.schema.json").read_text())
        self.assertEqual(schema["$id"], "campready.phase5.qualification_response.v1")
        response = valid_response()
        score.validate_response(response)
        for mutation, reason in [
            (("independence_attestation", False), "INDEPENDENCE"),
            (("reviewer_id", "R-X"), "REVIEWER_ID"),
            (("instrument_digest", "sha256:" + "0" * 64), "INSTRUMENT_DIGEST"),
        ]:
            bad = copy.deepcopy(response); bad[mutation[0]] = mutation[1]
            with self.assertRaisesRegex(ValueError, reason): score.validate_response(bad)

    def test_every_safety_critical_field_is_detected(self):
        rubric = json.loads((QUAL / "expected_results.json").read_text())
        base = valid_response()
        for field in rubric["safety_critical_fields"]:
            bad = copy.deepcopy(base)
            row = bad["cases"][0]
            if isinstance(row[field], bool): row[field] = not row[field]
            elif field == "data_quality_or_semantic_change": row[field] = "UNKNOWN" if row[field] != "UNKNOWN" else "NEITHER"
            else: row[field] = "NO" if row[field] == "YES" else "YES"
            self.assertIn(f"T1:{field}", score.score_response(bad)["safety_critical_violations"], field)

    def test_guarded_final_failures_reject_direct_valid_and_enforce_quality_unknown(self):
        for index in (2, 3):
            for action in ("REVIEWED_VALID", "REVIEWED_NOT_ACTIONABLE"):
                bad = valid_response(); bad["cases"][index]["selected_reviewer_action"] = action
                bad["cases"][index]["reviewer_note"] = "Required note" if action == "REVIEWED_NOT_ACTIONABLE" else None
                result = score.score_response(bad)
                self.assertIn(f"T{index + 1}:GUARDED_DIRECT_DISPOSITION", result["safety_critical_violations"])
            bad = valid_response(); bad["cases"][index]["data_quality_or_semantic_change"] = "SEMANTIC_CHANGE"
            self.assertFalse(score.score_response(bad)["contract_pass"])

    def test_provenance_source_silence_safety_and_acknowledgement_enforced(self):
        mutations = [(0, "official_provenance_identified", False), (2, "safe_to_treat_source_silence_as_safe", "YES"), (2, "acknowledgement_required", "NO"), (2, "safe_to_infer_road_passability", "YES"), (11, "acknowledgement_means_safe_or_notification_approved", "YES")]
        for index, field, value in mutations:
            bad = valid_response(); bad["cases"][index][field] = value
            self.assertIn(f"T{index + 1}:{field}", score.score_response(bad)["safety_critical_violations"])

    def test_fanout_does_not_mean_duplicate_relationships(self):
        bad = valid_response(); bad["cases"][8]["shared_event_proves_duplicate_relationships"] = "YES"
        self.assertIn("T9:shared_event_proves_duplicate_relationships", score.score_response(bad)["safety_critical_violations"])

    def test_discretionary_action_disagreement_is_not_safety_failure(self):
        response = valid_response(); response["cases"][0]["selected_reviewer_action"] = "REVIEWED_NOT_ACTIONABLE"; response["cases"][0]["reviewer_note"] = "Bounded discretionary choice"
        result = score.score_response(response)
        self.assertTrue(result["contract_pass"])
        self.assertEqual(result["discretionary_disagreements"], ["T1:selected_reviewer_action"])

    def test_two_humans_differ_and_adjudication_waits_for_both_immutable_submissions(self):
        a, b = valid_response("R-A"), valid_response("R-B")
        originals = copy.deepcopy([a, b])
        with self.assertRaisesRegex(ValueError, "BOTH_IMMUTABLE"): score.begin_adjudication([a])
        with self.assertRaisesRegex(ValueError, "MUST_DIFFER"): score.evaluate_two([a, copy.deepcopy(a)])
        adjudication = score.begin_adjudication([a, b])
        self.assertEqual([a, b], originals)
        self.assertEqual(len(adjudication["submission_digests"]), 2)
        self.assertTrue(all(row["adjudicated_interpretation"] is None for row in adjudication["cases"]))

    def test_no_network_delivery_or_canonical_write(self):
        tracked = []
        for top in ROOT.glob("campready-phase4*"):
            if top.is_dir(): tracked.extend(p for p in top.rglob("*") if p.is_file())
        tracked.extend(p for p in PHASE5.rglob("*") if p.is_file() and QUAL not in p.parents and p.name != "test_qualification.py" and "__pycache__" not in p.parts)
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        before = {p: digest(p) for p in tracked}
        score.render_packet(); score.score_response(valid_response())
        after = {p: digest(p) for p in tracked}
        self.assertEqual(before, after)
        source = (QUAL / "score_qualification.py").read_text().lower()
        for forbidden in ("requests", "httpx", "aiohttp", "smtplib", "twilio", "sendgrid", "boto3", "firebase"):
            self.assertNotIn("import " + forbidden, source)


if __name__ == "__main__":
    unittest.main()
