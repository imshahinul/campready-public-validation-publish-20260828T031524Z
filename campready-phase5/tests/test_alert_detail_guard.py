import copy, importlib, json, pathlib, socket, sys, unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]; PHASE5=ROOT/"campready-phase5"; sys.path.insert(0,str(PHASE5))
rm=importlib.import_module("read_model"); guard=importlib.import_module("alert_detail_guard")

class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows=json.loads((PHASE5/"fixtures/review_model_cases.json").read_text())["records"]; cls.f={r["fixture_id"]:r for r in rows}
    def setUp(self): self.old=socket.socket; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    def tearDown(self): socket.socket=self.old
    def test_g1_rescued_not_guarded(self): self.assertFalse(guard.validate_alert_detail_guard(rm.derive_quality_review_item(self.f["F5"])))
    def test_g2_g3_g4_g5_guard_shapes(self):
        prior=rm.derive_quality_review_item(self.f["F4"]); self.assertTrue(guard.validate_alert_detail_guard(prior)); self.assertEqual(prior["prior_accepted_state_ref"],prior["current_accepted_state_ref"])
        no_prior=rm.derive_quality_review_item(dict(self.f["F4"],logical_request_id="other",prior_accepted_state_ref=None)); self.assertTrue(guard.validate_alert_detail_guard(no_prior)); self.assertIsNone(no_prior["current_accepted_state_ref"])
        self.assertEqual(prior["source_evidence"]["representation"],"UNAVAILABLE"); self.assertEqual(prior["source_title"]["status"],"UNKNOWN")
        self.assertEqual(prior["lifecycle_classification"],"NONE"); self.assertIsNone(prior["event_category"])
    def test_guard_prohibits_direct_valid_and_not_actionable(self):
        item=rm.derive_quality_review_item(self.f["F4"])
        for target in ("REVIEWED_VALID","REVIEWED_NOT_ACTIONABLE"):
            with self.assertRaisesRegex(ValueError,"GUARD_TRANSITION"): guard.validate_guarded_transition(item,"NEW",target)
        for target in ("DATA_QUALITY_HOLD","NEEDS_FOLLOWUP"): guard.validate_guarded_transition(item,"NEW",target)
    def test_g10_g11_g12_contract_tampering(self):
        base=rm.derive_quality_review_item(self.f["F4"])
        variants=[]
        variants.append(dict(base,notification_eligible=True))
        x=copy.deepcopy(base); x["interpretation_prohibitions"].remove("NO_SAFETY_INFERENCE"); variants.append(x)
        variants.append(dict(base,provenance_record_ref=""))
        x=copy.deepcopy(base); x["unknown_state"]["unknown"]=False; variants.append(x)
        x=copy.deepcopy(base); x["lifecycle_classification"]="RESOLUTION"; variants.append(x)
        for item in variants:
            with self.assertRaisesRegex(guard.GuardContractError,"GUARD_CONTRACT_INVALID"): guard.validate_alert_detail_guard(item)

if __name__ == "__main__": unittest.main()
