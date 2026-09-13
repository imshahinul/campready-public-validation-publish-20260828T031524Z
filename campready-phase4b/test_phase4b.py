from __future__ import annotations
import importlib.util, json, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path); value=importlib.util.module_from_spec(spec); sys.modules[name]=value; spec.loader.exec_module(value); return value


capture_mod=module(ROOT/"campready-phase4b/capture.py","capture_test")
sys.modules["capture"] = capture_mod
run_mod=module(ROOT/"campready-phase4b/run.py","run_test")
maintenance_mod=module(ROOT/"campready-phase4b/maintenance.py","maintenance_test")


class Phase4BTests(unittest.TestCase):
    def setUp(self): self.config=json.loads((ROOT/"campready-phase4b/config.json").read_text()); self.mapping=json.loads((ROOT/self.config["paths"]["mapping"]).read_text())
    def test_cohort_and_hurricane(self):
        rows=self.mapping["rows"]; self.assertEqual(len(rows),35); self.assertEqual(sum(r["authority_family"]!="USACE" for r in rows),32); self.assertEqual(sum(r["authority_family"]=="USACE" for r in rows),3); self.assertNotIn("33555",json.dumps(self.mapping)); self.assertIn("71807",json.dumps(self.mapping))
    def test_frozen_metrics(self): self.assertEqual(self.config["frozen_metrics"]["strict_useful_coverage"],"31 / 35 = 88.57%"); self.assertEqual(self.config["frozen_metrics"]["supported_core_dlr"],"32 / 5 = 6.40")
    def test_real_notifications_disabled(self): self.assertIs(self.config["policy"]["send_real_notifications"],False); self.assertNotIn("send_notification",(ROOT/"campready-phase4b/run.py").read_text())
    def test_schedule_disabled_pre_activation(self): self.assertIs(self.config["activation"]["schedule_enabled"],False); self.assertIsNone(self.config["activation"]["hourly_cron"]); self.assertNotIn("schedule:",(ROOT/".github/workflows/campready-validation.yml").read_text())
    def test_http_and_parser_failures_not_semantic(self):
        prior={"sites":{"x":{"events":{"a":{"fingerprint":"old"}}}}}; obs={"supported_campgrounds":[{"canonical_key":"x","authority_family":"NPS","semantic_events":[],"unknown_results":[]}]}; result=run_mod.compare(prior,obs); self.assertEqual(result["semantic_deltas"],[]); self.assertEqual(result["would_notify_candidates"],[])
    def test_unknown_nonnotifying(self): self.assertTrue(self.config["policy"]["unknown_is_non_notifying"])
    def test_first_observation_zero_notify(self):
        obs={"supported_campgrounds":[{"canonical_key":"x","authority_family":"NPS","semantic_events":[{"quality":"OK","source_family":"NPS","source_url":"u","fingerprint":"f"}],"unknown_results":[]}]}; result=run_mod.compare(None,obs); self.assertTrue(result["baseline"]); self.assertEqual(result["would_notify_candidates"],[])
    def test_identical_second_observation_no_candidate(self):
        event={"quality":"OK","source_family":"NPS","source_url":"u","fingerprint":"f"}; obs={"supported_campgrounds":[{"canonical_key":"x","authority_family":"NPS","semantic_events":[event],"unknown_results":[]}]}; prior={"sites":{"x":{"events":{"NPS:u":event}}}}; self.assertEqual(run_mod.compare(prior,obs)["would_notify_candidates"],[])
    def test_changed_event_review_only(self):
        event={"quality":"OK","source_family":"NPS","source_url":"u","fingerprint":"new"}; obs={"supported_campgrounds":[{"canonical_key":"x","authority_family":"NPS","semantic_events":[event],"unknown_results":[]}]}; prior={"sites":{"x":{"events":{"NPS:u":{"fingerprint":"old"}}}}}; candidate=run_mod.compare(prior,obs)["would_notify_candidates"][0]; self.assertTrue(candidate["review_only"])
    def test_usace_transport_never_semantic(self): self.assertIs(self.config["policy"]["usace_semantic_support"],False); self.assertNotIn("USACE",capture_mod.SUPPORTED)
    def test_denominators_separate(self):
        self.assertEqual(set(self.config["reliability_denominators"]),{"supported_core","parser_failure","usace_transport","workflow_execution"})
    def test_shared_fetch_once(self):
        count=0
        def fake(url,accept="x"): nonlocal count; count+=1; return {"ok":True}
        cache=capture_mod.FetchOnce(fake); cache.get("u"); cache.get("u"); self.assertEqual(count,1)
    def test_definition_loader_does_not_execute_cli_entrypoint_without_arguments(self):
        source = """\
import sys
def reusable():
    return "loaded"
def main():
    return sys.argv[1]
raise SystemExit(main())
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "frozen_dependency.py"
            path.write_text(source)
            with patch.object(sys, "argv", ["capture.py"]):
                loaded = capture_mod.load_module(path, "definition_loader_regression")
            self.assertEqual(loaded.reusable(), "loaded")
    def test_validation_end_guard_zero_network(self):
        cfg=json.loads((ROOT/"campready-phase4b/config.json").read_text()); cfg["activation"]["validation_end_utc"]="2026-01-01T00:00:00+00:00"
        with tempfile.TemporaryDirectory() as td:
            path=ROOT/"campready-phase4b/config.json"
            with patch.object(run_mod,"ROOT",Path(td)), patch.object(run_mod,"REPO",ROOT):
                Path(td,"config.json").write_text(json.dumps(cfg)); self.assertEqual(run_mod.main(datetime(2026,1,1,tzinfo=timezone.utc)),0)
    def test_maintenance_explicit_minutes_and_zero_attestation(self):
        with tempfile.TemporaryDirectory() as td, patch.object(maintenance_mod,"LOG",Path(td)/"log.jsonl"):
            with self.assertRaises(ValueError): maintenance_mod.append_entry(-1,"OTHER","all","x","x")
            with self.assertRaises(ValueError): maintenance_mod.append_entry(0,"OTHER","all","x","x")
            value=maintenance_mod.append_entry(0,"OTHER","all","NO_HUMAN_MAINTENANCE_THIS_WEEK","none",week_number=1); self.assertEqual(value["minutes"],0)
    def test_telemetry_names(self):
        text=(ROOT/"campready-phase4b/capture.py").read_text()
        for name in ("raw_candidate_count","parsed_observation_count","deduplicated_observation_count","relevant_operational_event_count","unknown_nonrelevant_count"): self.assertIn(name,text)
        self.assertNotIn("POST_SCOPE",text); self.assertNotIn("POST_DEDUP",text)
    def test_no_site_specific_capture_branches(self):
        text=(ROOT/"campready-phase4b/capture.py").read_text()
        for row in self.mapping["rows"]: self.assertNotIn(f'== "{row["canonical_key"]}"',text)
    def test_transport_failure_cannot_change_state(self): self.test_http_and_parser_failures_not_semantic()
    def test_noncampground_facility_guard_regression(self):
        authority=module(ROOT/"campready-authority-adapters/authority_adapters.py","authority_again"); self.assertIsNotNone(authority._non_campground_resource_target("The boat ramp is closed."))
    def test_manifest_covers_behavior(self):
        manifest=json.loads((ROOT/"campready-phase4b/frozen-hashes.json").read_text()); self.assertIn("campready-phase4b/capture.py",manifest["sha256"]); self.assertIn(".github/workflows/campready-validation.yml",manifest["sha256"])
    def test_precision_zero_policy(self): self.assertEqual(self.mapping["prospective_gates"]["zero_denominator_precision_reported_as"],"UNDEFINED_NOT_100_PERCENT")
    def test_state_transaction_has_rollback(self):
        text=(ROOT/"campready-phase4b/run.py").read_text(); self.assertIn("old_state",text); self.assertIn("old_history",text); self.assertIn("os.replace",text)
    def test_guarded_hold_does_not_advance(self): self.assertIn('"state_advanced": False',(ROOT/"campready-phase4b/run.py").read_text())


if __name__=="__main__": unittest.main()
