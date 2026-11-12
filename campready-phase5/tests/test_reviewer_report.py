import importlib, json, pathlib, socket, sys, unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]; PHASE5=ROOT/"campready-phase5"; sys.path.insert(0,str(PHASE5))
rm=importlib.import_module("read_model"); wf=importlib.import_module("workflow"); report=importlib.import_module("reviewer_report")

class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows=json.loads((PHASE5/"fixtures/review_model_cases.json").read_text())["records"]; f={r["fixture_id"]:r for r in rows}
        cls.semantic=rm.derive_semantic_review_item(f["F1"]); cls.guarded=rm.derive_quality_review_item(f["F4"]); cls.summary=rm.derive_quality_review_item(f["F7"])
    def setUp(self): self.old=socket.socket; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    def tearDown(self): socket.socket=self.old
    def test_three_blocks_guard_labels_and_unavailable(self):
        text=report.render_reviewer_report([self.guarded],{})
        self.assertLess(text.index("SOURCE FACT"),text.index("SYSTEM CLASSIFICATION")); self.assertLess(text.index("SYSTEM CLASSIFICATION"),text.index("REVIEWER ANNOTATION"))
        for label in ("DATA_QUALITY","UNKNOWN","ALERT_DETAIL FINAL RETRIEVAL FAILURE","MANUAL ACKNOWLEDGEMENT REQUIRED","NO SEMANTIC / SAFETY INFERENCE","Evidence label: UNAVAILABLE","Title unavailable reason: RETRIEVAL_FINAL_FAILED"):
            self.assertIn(label,text)
    def test_summary_not_verbatim_and_reviewer_authorship(self):
        text=report.render_reviewer_report([self.summary],{})
        self.assertIn("Evidence label: SUMMARY",text); self.assertNotIn("Evidence label: VERBATIM",text); self.assertIn("Reviewer-authored note:",text)
    def test_no_unsafe_claims_or_delivery_implication(self):
        text=report.render_reviewer_report([self.semantic,self.guarded],{}).lower()
        for phrase in ("site is safe","road is passable","no alert means no hazard","condition changed because","acknowledged means safe","reviewed_valid means safe","notification sent","delivered to"):
            self.assertNotIn(phrase,text)
    def test_grouped_relationships_individually_reviewable(self):
        second=dict(self.semantic,review_item_id="cr5ri1-"+"2"*64,affected_relationship_id="relationship-2")
        text=report.render_reviewer_report([self.semantic,second],{})
        self.assertEqual(text.count("REVIEW ITEM:"),2); self.assertIn(self.semantic["affected_relationship_id"],text); self.assertIn("relationship-2",text)
    def test_guard_ack_remains_prominent(self):
        hold=wf.build_annotation(self.guarded,[],{"to_status":"DATA_QUALITY_HOLD","reviewer_id":"r1","timestamp":"2026-11-12T05:00:00Z","disposition":"HOLD","reason_code":"FINAL_FAILURE","note":"reviewed failure"})
        ack=wf.build_annotation(self.guarded,[hold],{"to_status":"ACKNOWLEDGED","reviewer_id":"r1","timestamp":"2026-11-12T05:01:00Z","disposition":"HOLD"})
        text=report.render_reviewer_report([self.guarded],{self.guarded["review_item_id"]:[hold,ack]})
        self.assertIn("Reviewer status: ACKNOWLEDGED",text); self.assertIn("MANUAL ACKNOWLEDGEMENT REQUIRED",text); self.assertIn("Reviewer acknowledgement recorded: True",text)

if __name__ == "__main__": unittest.main()
