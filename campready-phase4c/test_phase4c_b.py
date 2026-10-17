from __future__ import annotations
import importlib.util, json, sys, unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path); value=importlib.util.module_from_spec(spec); sys.modules[name]=value; spec.loader.exec_module(value); return value
capture_mod=module(ROOT/"campready-phase4b/capture.py","phase4c_capture_test")
sys.modules["capture"]=capture_mod
run_mod=module(ROOT/"campready-phase4b/run.py","phase4c_run_test")

def result(ok, status=None, failure=None):
    return {"ok":ok,"http_status":status,"final_url":"https://example.test/final" if ok else None,"content_type":"text/html","body":b"ok" if ok else b"","failure":failure,"elapsed_ms":1.0}

class Phase4CBRetryTests(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads((ROOT/"campready-phase4c/phase4c_b_transport_experiment.json").read_text()); self.policy["enabled"]=True
    def cache(self, outcomes):
        fetch=Mock(side_effect=outcomes); sleep=Mock(); return capture_mod.FetchOnce(fetch,self.policy,sleep),fetch,sleep
    def get(self, cache, authority="USFS", role="CAMPGROUND_PAGE"):
        return cache.get("https://example.test/source",authority=authority,family="USFS_WEBSITE_RECREATION",role=role)
    def test_a_usfs_first_success_one_attempt_no_retry(self):
        cache,fetch,sleep=self.cache([result(True,200)]); value=self.get(cache); self.assertEqual(value["physical_attempt_count"],1); self.assertFalse(value["retry"]["attempted"]); sleep.assert_not_called()
    def test_b_usfs_403_then_success_is_logical_rescue(self):
        cache,fetch,sleep=self.cache([result(False,403,"HTTPError: forbidden"),result(True,200)]); value=self.get(cache); self.assertTrue(value["ok"]); self.assertFalse(value["first_attempt"]["succeeded"]); self.assertTrue(value["retry"]["succeeded"]); self.assertEqual(value["physical_attempt_count"],2)
    def test_c_usfs_403_then_403_is_final_failure(self):
        cache,fetch,sleep=self.cache([result(False,403,"HTTPError: forbidden"),result(False,403,"HTTPError: forbidden")]); value=self.get(cache); self.assertFalse(value["ok"]); self.assertEqual(value["physical_attempt_count"],2)
        url="https://example.test/source"; event={"quality":"OK","source_family":"USFS_WEBSITE_RECREATION","source_url":url,"fingerprint":"prior"}
        prior={"sites":{"site":{"authority_family":"USFS","events":{"USFS_WEBSITE_RECREATION:"+url:event}}}}
        request={"source_family":"USFS_WEBSITE_RECREATION","source_url":url,"source_role":"CAMPGROUND_PAGE","retrieval_succeeded":value["ok"]}
        observation={"source_requests":[request],"supported_campgrounds":[{"canonical_key":"site","authority_family":"USFS","semantic_events":[],"unknown_results":[]}]}
        compared=run_mod.compare(prior,observation); self.assertEqual(compared["accepted_state"]["site"]["events"],prior["sites"]["site"]["events"])
    def test_d_timeout_then_success(self):
        cache,_,_=self.cache([result(False,None,"TimeoutError: timed out"),result(True,200)]); self.assertTrue(self.get(cache)["ok"])
    def test_e_500_then_success(self):
        cache,_,_=self.cache([result(False,500,"HTTPError: server"),result(True,200)]); self.assertTrue(self.get(cache)["ok"])
    def test_f_non_usfs_failure_has_no_retry(self):
        cache,fetch,sleep=self.cache([result(False,500,"HTTPError: server")]); value=self.get(cache,"NPS"); self.assertEqual(value["physical_attempt_count"],1); sleep.assert_not_called()
    def test_g_fetch_once_duplicate_is_one_logical_max_two_physical(self):
        cache,fetch,_=self.cache([result(False,403,"HTTPError: forbidden"),result(True,200)]); first=self.get(cache); second=self.get(cache); self.assertIs(first,second); self.assertEqual(fetch.call_count,2); self.assertEqual(len(cache.cache),1)
    def test_h_i_j_deterministic_jitter_reproducible_bounded_and_first_stays_failed(self):
        values=[capture_mod.deterministic_retry_jitter("stable") for _ in range(2)]; self.assertEqual(values[0],values[1]); self.assertGreaterEqual(values[0],0); self.assertLessEqual(values[0],1)
        cache,_,sleep=self.cache([result(False,403,"HTTPError: forbidden"),result(True,200)]); value=self.get(cache); self.assertFalse(value["first_attempt"]["succeeded"]); self.assertGreaterEqual(sleep.call_args.args[0],5); self.assertLessEqual(sleep.call_args.args[0],6)
    def test_k_eventual_reliability_counts_rescue_while_first_attempt_does_not(self):
        cache,_,_=self.cache([result(False,403,"HTTPError: forbidden"),result(True,200)]); value=self.get(cache)
        self.assertEqual(sum([value["first_attempt"]["succeeded"]]),0); self.assertEqual(sum([value["ok"]]),1)
    def test_policy_is_frozen_safe_and_inactive_before_activation(self):
        committed=json.loads((ROOT/"campready-phase4c/phase4c_b_transport_experiment.json").read_text()); self.assertFalse(committed["enabled"]); self.assertFalse(committed["send_real_notifications"]); self.assertTrue(committed["review_only"]); self.assertEqual(committed["usfs_max_physical_attempts_per_logical_request"],2)

if __name__=="__main__": unittest.main()
