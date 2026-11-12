import copy, importlib, json, pathlib, socket, sys, tempfile, unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]; PHASE5=ROOT/"campready-phase5"; sys.path.insert(0,str(PHASE5))
rm=importlib.import_module("read_model"); sm=importlib.import_module("annotation_store"); wf=importlib.import_module("workflow")

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows=json.loads((PHASE5/"fixtures/review_model_cases.json").read_text())["records"]; f={r["fixture_id"]:r for r in rows}
        cls.normal=rm.derive_semantic_review_item(f["F1"]); cls.guarded=rm.derive_quality_review_item(f["F4"])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=sm.AnnotationStore(pathlib.Path(self.tmp.name)/"a.jsonl"); self.old=socket.socket; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    def tearDown(self): socket.socket=self.old; self.tmp.cleanup()
    def action(self,to,n=0,**kw):
        base={"to_status":to,"reviewer_id":"reviewer-1","timestamp":f"2026-11-12T05:0{n}:00Z","disposition":kw.pop("disposition","VALID"),"reason_code":kw.pop("reason_code",None),"note":kw.pop("note",None)}; base.update(kw); return base
    def test_every_allowed_transition(self):
        for start,target in wf.ALLOWED_TRANSITIONS:
            history=[]
            if start!="NEW":
                seed_target=start; seed=self.action(seed_target,n=0,note="required",reason_code="QUALITY_REF" if seed_target=="DATA_QUALITY_HOLD" else None)
                if seed_target=="ACKNOWLEDGED": continue
                first=wf.build_annotation(self.normal,[],seed); history=[first]
            action=self.action(target,n=1,note="resolution or explicit unresolved note",reason_code="EVIDENCE_REF")
            wf.build_annotation(self.normal,history,action)
    def test_all_other_direct_transitions_rejected(self):
        states={"NEW","REVIEWED_VALID","REVIEWED_NOT_ACTIONABLE","DATA_QUALITY_HOLD","NEEDS_FOLLOWUP","ACKNOWLEDGED"}
        for start in states:
            for target in states:
                if (start,target) in wf.ALLOWED_TRANSITIONS or start=="ACKNOWLEDGED": continue
                history=[]
                if start!="NEW": history=[wf.build_annotation(self.normal,[],self.action(start,note="seed",reason_code="SEED"))]
                with self.assertRaisesRegex(ValueError,"TRANSITION_INVALID"): wf.build_annotation(self.normal,history,self.action(target,1,note="x",reason_code="X"))
    def test_requirements_and_invalid_append_nothing(self):
        cases=[("REVIEWED_NOT_ACTIONABLE",{}),("DATA_QUALITY_HOLD",{}),("NEEDS_FOLLOWUP",{})]
        for target,extra in cases:
            with self.assertRaises(ValueError): wf.apply_reviewer_action(self.store,self.normal,self.action(target,**extra))
        self.assertEqual(self.store.read_all(),[])
    def test_guard_hold_ack_restart_projection_and_source_unchanged(self):
        original=copy.deepcopy(self.guarded)
        with self.assertRaisesRegex(ValueError,"GUARD_TRANSITION"): wf.apply_reviewer_action(self.store,self.guarded,self.action("REVIEWED_VALID"))
        hold=wf.apply_reviewer_action(self.store,self.guarded,self.action("DATA_QUALITY_HOLD",reason_code="FINAL_FAILURE",note="Investigate persisted failure"))
        ack=wf.apply_reviewer_action(self.store,self.guarded,self.action("ACKNOWLEDGED",1,disposition="HOLD",note=None))
        projection=wf.reconstruct_projection(self.guarded,sm.AnnotationStore(self.store.path).for_item(self.guarded["review_item_id"]))
        self.assertEqual(projection["reviewer_status"],"ACKNOWLEDGED"); self.assertTrue(projection["reviewer_acknowledgement"]["acknowledged"])
        self.assertFalse(projection["notification_eligible"]); self.assertEqual(self.guarded,original); self.assertEqual(ack["supersedes_ref"],hold["annotation_id"])
    def test_followup_unresolved_ack_note_required(self):
        first=wf.apply_reviewer_action(self.store,self.normal,self.action("NEEDS_FOLLOWUP",note="Investigate"))
        with self.assertRaisesRegex(ValueError,"UNRESOLVED"): wf.apply_reviewer_action(self.store,self.normal,self.action("ACKNOWLEDGED",1))
        self.assertEqual(len(self.store.read_all()),1); self.assertEqual(first["annotation_version"],1)
    def test_ack_immutable_without_superseding_correction(self):
        first=wf.apply_reviewer_action(self.store,self.normal,self.action("REVIEWED_VALID")); ack=wf.apply_reviewer_action(self.store,self.normal,self.action("ACKNOWLEDGED",1))
        with self.assertRaisesRegex(ValueError,"ACKNOWLEDGED_IMMUTABLE"): wf.apply_reviewer_action(self.store,self.normal,self.action("REVIEWED_NOT_ACTIONABLE",2,note="correction"))
        correction=wf.apply_reviewer_action(self.store,self.normal,self.action("REVIEWED_NOT_ACTIONABLE",2,note="correction",supersedes_ref=ack["annotation_id"],from_status="REVIEWED_VALID"))
        self.assertEqual(correction["annotation_version"],3); self.assertEqual(correction["supersedes_ref"],ack["annotation_id"]); self.assertNotEqual(correction["annotation_id"],first["annotation_id"])
    def test_duplicate_action_no_duplicate_acknowledgement(self):
        first_action=self.action("REVIEWED_VALID"); first=wf.apply_reviewer_action(self.store,self.normal,first_action)
        self.assertEqual(wf.apply_reviewer_action(self.store,self.normal,first_action),first)
        action=self.action("ACKNOWLEDGED",1)
        a=wf.apply_reviewer_action(self.store,self.normal,action)
        self.assertEqual(self.store.append(copy.deepcopy(a)),a); self.assertEqual(len(self.store.read_all()),2)
    def test_guard_invalid_appends_nothing(self):
        bad=dict(self.guarded,notification_eligible=True)
        with self.assertRaisesRegex(ValueError,"GUARD_CONTRACT_INVALID"): wf.apply_reviewer_action(self.store,bad,self.action("DATA_QUALITY_HOLD",reason_code="X",note="x"))
        self.assertEqual(self.store.read_all(),[])
    def test_official_provenance_required(self):
        bad=dict(self.normal,provenance_record_ref="")
        with self.assertRaisesRegex(ValueError,"OFFICIAL_PROVENANCE_REQUIRED"): wf.apply_reviewer_action(self.store,bad,self.action("REVIEWED_VALID"))
        self.assertEqual(self.store.read_all(),[])
    def test_g9_later_recovery_item_does_not_change_guarded_history(self):
        hold=wf.apply_reviewer_action(self.store,self.guarded,self.action("DATA_QUALITY_HOLD",reason_code="FINAL_FAILURE",note="historical failure"))
        recovery=dict(self.normal,review_item_id="cr5ri1-"+"9"*64,authority_id="USFS",source_role="ALERT_DETAIL",retrieval_status="SUCCESS")
        wf.apply_reviewer_action(self.store,recovery,self.action("REVIEWED_VALID",1))
        self.assertEqual(self.store.for_item(self.guarded["review_item_id"]),[hold])

if __name__ == "__main__": unittest.main()
