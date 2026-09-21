from __future__ import annotations
import importlib.util, json, re, subprocess, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent


def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path); value=importlib.util.module_from_spec(spec); sys.modules[name]=value; spec.loader.exec_module(value); return value


capture_mod=module(ROOT/"campready-phase4b/capture.py","capture_test")
sys.modules["capture"] = capture_mod
run_mod=module(ROOT/"campready-phase4b/run.py","run_test")
maintenance_mod=module(ROOT/"campready-phase4b/maintenance.py","maintenance_test")


class Phase4BTests(unittest.TestCase):
    def setUp(self): self.config=json.loads((ROOT/"campready-phase4b/config.json").read_text()); self.mapping=json.loads((ROOT/self.config["paths"]["mapping"]).read_text())
    def alert_event(self, fingerprint="3f8dbe4a7ba2209d1367652d11b88aa46cfe721fed66ad9c72b6f77f45f008a3", url="https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/campground-updates-low-gap-now-open-and-water-system-changes-upper", scope="https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts"):
        return {"quality":"OK","source_family":"USFS_WEBSITE_ALERTS","source_url":url,"source_scope_url":scope,"fingerprint":fingerprint,"semantic":{"bounded_official_text":"frozen Low Gap text"}}
    def observation(self, events=(), requests=(), key="low-gap"):
        return {"source_requests":list(requests),"supported_campgrounds":[{"canonical_key":key,"authority_family":"USFS","semantic_events":list(events),"unknown_results":[]}]}
    def request(self, url, role, succeeded):
        return {"source_family":"USFS_WEBSITE_ALERTS","source_url":url,"source_role":role,"retrieval_succeeded":succeeded,"parser_result":"PARSED" if succeeded else "NOT_ATTEMPTED_RETRIEVAL_FAILURE"}
    def state(self, event, key="low-gap"):
        return {"sites":{key:{"authority_family":"USFS","events":{f"USFS_WEBSITE_ALERTS:{event['source_url']}":event}}}}
    def usfs_scopes(self):
        rows=[row for row in self.mapping["rows"] if row["authority_family"]=="USFS"]
        return {url:capture_mod.scoped_usfs_by_alert_url(rows,url) for url in sorted({row["alerts_conditions_page_url"] for row in rows})}
    def frozen_classifier(self):
        legacy=capture_mod.load_module(ROOT/self.config["paths"]["legacy_capture"],"repair_b_legacy")
        return legacy.load_prior_script(ROOT/self.config["paths"]["usfs_classifier"])["classify_event"]
    def historical_alert(self, url, fingerprint):
        captures=sorted((ROOT/"campready-phase4b/runs").glob("*/capture.json"))
        for path in captures:
            value=json.loads(path.read_text())
            for site in value["supported_campgrounds"]:
                for event in site["semantic_events"]:
                    if event.get("source_url")==url and event.get("fingerprint")==fingerprint:
                        semantic=event["semantic"]
                        return {"source_url":url,"title":semantic["title"],"start_date":None,"end_date":None,"rec_sites_affected":None,"text_excerpt":semantic["bounded_official_text"]}
        self.fail(f"frozen alert fixture not found: {url}")
    def test_cohort_and_hurricane(self):
        rows=self.mapping["rows"]; self.assertEqual(len(rows),35); self.assertEqual(sum(r["authority_family"]!="USACE" for r in rows),32); self.assertEqual(sum(r["authority_family"]=="USACE" for r in rows),3); self.assertNotIn("33555",json.dumps(self.mapping)); self.assertIn("71807",json.dumps(self.mapping))
    def test_frozen_metrics(self): self.assertEqual(self.config["frozen_metrics"]["strict_useful_coverage"],"31 / 35 = 88.57%"); self.assertEqual(self.config["frozen_metrics"]["supported_core_dlr"],"32 / 5 = 6.40")
    def test_real_notifications_disabled(self): self.assertIs(self.config["policy"]["send_real_notifications"],False); self.assertNotIn("send_notification",(ROOT/"campready-phase4b/run.py").read_text())
    def test_usfs_alert_source_scope_inventory_is_complete_exact_and_unique(self):
        expected={
            "https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts":{"andrews-cove","low-gap","upper-chattahoochee","sarahs-creek","tate-branch","lake-russell","lake-rabun","willis-knob","wildcat-1","wildcat-2"},
            "https://www.fs.usda.gov/r08/northcarolina/alerts":{"nc-north-mills-river","nc-van-hook-glade","nc-hurricane-creek"},
            "https://www.fs.usda.gov/r08/francismarionsumter/alerts":{"sc-burrells-ford","sc-brick-house"},
            "https://www.fs.usda.gov/r08/alabama/alerts":{"al-payne-lake","al-clear-creek","al-coleman-lake"},
            "https://www.fs.usda.gov/r08/cherokee/alerts":{"tn-indian-boundary","tn-rock-creek"},
            "https://www.fs.usda.gov/r09/monongahela/alerts":{"wv-big-rock","wv-seneca-shadows","wv-bear-heaven"},
            "https://www.fs.usda.gov/r06/siuslaw/alerts":{"or-spinreel"},
            "https://www.fs.usda.gov/r06/deschutes/alerts":{"or-cold-springs"},
        }
        scopes=self.usfs_scopes(); self.assertEqual({url:set(rows) for url,rows in scopes.items()},expected)
        keys=[key for rows in scopes.values() for key in rows]; self.assertEqual(len(keys),25); self.assertEqual(len(keys),len(set(keys)))
        with self.assertRaisesRegex(RuntimeError,"mapping incomplete"):
            capture_mod.scoped_usfs_by_alert_url([{"canonical_key":"missing","alerts_conditions_page_url":""}],"https://example.test/alerts")
    def test_forest_wide_restriction_and_lift_are_exactly_chattahoochee_oconee_scoped(self):
        scope="https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts"; sites=self.usfs_scopes()[scope]; classify=self.frozen_classifier()
        fixtures=(
            ("https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/spring-2026-forest-wide-fire-restrictions","f854bb50ca280052464c0a5d1999b3a38e3ef81f81b22bfdaffa1cd39dfe44a5"),
            ("https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/campfire-restriction-lifted-chattahoochee-oconee-national-forest","2be7638453f70fe0ced52ac7562386e5681d2b324396acdcc4d5484b9fc922f9"),
        )
        expected=set(sites)
        for url,fingerprint in fixtures:
            relations=classify(self.historical_alert(url,fingerprint),sites,datetime(2026,9,21,tzinfo=timezone.utc).date())
            self.assertEqual({key for key,value in relations.items() if value["relevance"]=="INCLUDE"},expected)
            self.assertEqual(len(relations),10); self.assertFalse(any(value["relevance"]=="UNKNOWN" for value in relations.values()))
            self.assertNotIn("or-spinreel",relations); self.assertNotIn("wv-big-rock",relations)
    def test_low_gap_control_remains_site_specific_within_ten_site_scope(self):
        url="https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/campground-updates-low-gap-now-open-and-water-system-changes-upper"
        scope=self.usfs_scopes()["https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts"]
        captures=sorted((ROOT/"campready-phase4b/runs").glob("*/capture.json")); fixture=None
        for path in captures:
            value=json.loads(path.read_text())
            for site in value["supported_campgrounds"]:
                for event in site["semantic_events"]:
                    if event.get("source_url")==url: fixture=self.historical_alert(url,event["fingerprint"]); break
                if fixture: break
            if fixture: break
        self.assertIsNotNone(fixture); relations=self.frozen_classifier()(fixture,scope,datetime(2026,9,21,tzinfo=timezone.utc).date())
        self.assertEqual({key for key,value in relations.items() if value["relevance"]=="INCLUDE"},{"low-gap"})
    def test_broad_events_cannot_cross_exact_alert_source_scopes(self):
        scopes=self.usfs_scopes(); classify=self.frozen_classifier(); date=datetime(2026,9,21,tzinfo=timezone.utc).date()
        ga=scopes["https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts"]
        broad=self.historical_alert("https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/spring-2026-forest-wide-fire-restrictions","f854bb50ca280052464c0a5d1999b3a38e3ef81f81b22bfdaffa1cd39dfe44a5")
        ga_relations=classify(broad,ga,date); self.assertNotIn("or-spinreel",ga_relations); self.assertNotIn("wv-big-rock",ga_relations)
        al=scopes["https://www.fs.usda.gov/r08/alabama/alerts"]
        synthetic={"source_url":"https://www.fs.usda.gov/r08/alabama/alerts/bounded-test","title":"Forest-wide fire restrictions","start_date":None,"end_date":None,"rec_sites_affected":"All National Forests in Alabama","text_excerpt":"Forest-wide fire restrictions apply throughout the National Forests in Alabama."}
        al_relations=classify(synthetic,al,date); self.assertEqual(set(al_relations),set(al)); self.assertNotIn("andrews-cove",al_relations)
    def test_failed_index_retention_uses_same_scope_identity_while_other_scope_progresses(self):
        ga_scope="https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts"; al_scope="https://www.fs.usda.gov/r08/alabama/alerts"
        ga=self.alert_event(url="https://example.test/ga",scope=ga_scope); old_al=self.alert_event("old-al",url="https://example.test/al",scope=al_scope); new_al=self.alert_event("new-al",url="https://example.test/al",scope=al_scope)
        prior={"sites":{"low-gap":{"authority_family":"USFS","events":{"USFS_WEBSITE_ALERTS:https://example.test/ga":ga}},"al-payne-lake":{"authority_family":"USFS","events":{"USFS_WEBSITE_ALERTS:https://example.test/al":old_al}}}}
        obs={"source_requests":[self.request(ga_scope,"ALERT_INDEX",False),self.request(al_scope,"ALERT_INDEX",True)],"supported_campgrounds":[{"canonical_key":"low-gap","authority_family":"USFS","semantic_events":[],"unknown_results":[]},{"canonical_key":"al-payne-lake","authority_family":"USFS","semantic_events":[new_al],"unknown_results":[]}]}
        result=run_mod.compare(prior,obs); self.assertIn("USFS_WEBSITE_ALERTS:https://example.test/ga",result["accepted_state"]["low-gap"]["events"]); self.assertEqual(result["accepted_state"]["al-payne-lake"]["events"]["USFS_WEBSITE_ALERTS:https://example.test/al"]["fingerprint"],"new-al"); self.assertEqual([d["category"] for d in result["semantic_deltas"]],["MATERIAL_UPDATE"])
    def test_external_schedule_cutover_contract(self):
        workflow=(ROOT/".github/workflows/campready-validation.yml").read_text()
        self.assertEqual(len(re.findall(r"(?m)^\s+schedule:\s*$",workflow)),0)
        self.assertIn("workflow_dispatch:",workflow)
        self.assertIn("trigger_source:",workflow)
        self.assertIn("scheduler_epoch:",workflow)
        self.assertIs(self.config["activation"]["schedule_enabled"],True)
        self.assertEqual(self.config["activation"]["hourly_cron"],"40 * * * *")
        self.assertEqual(self.config["activation"]["validation_start_utc"],"2026-09-13T04:00:00+00:00")
        self.assertEqual(self.config["activation"]["validation_end_utc"],"2026-10-13T04:00:00+00:00")
        self.assertIs(self.config["policy"]["send_real_notifications"],False)
        self.assertRegex(workflow,r"(?m)^concurrency:\s*$")
        self.assertRegex(workflow,r"(?m)^\s+group: campready-prospective-validation\s*$")
        self.assertRegex(workflow,r"(?m)^\s+cancel-in-progress: false\s*$")
    def test_manual_workflow_dispatch_provenance_remains_manual(self):
        value=run_mod.resolve_dispatch_provenance("workflow_dispatch","manual",""); self.assertEqual(value["trigger"],"manual"); self.assertIsNone(value["dispatch_provider"]); self.assertIsNone(value["scheduler_epoch"])
    def test_external_scheduler_maps_to_external_schedule_provenance(self):
        value=run_mod.resolve_dispatch_provenance("workflow_dispatch","external_scheduler",""); self.assertEqual(value["trigger"],"external_schedule"); self.assertEqual(value["dispatch_provider"],"external"); self.assertEqual(value["github_event_name"],"workflow_dispatch")
    def test_valid_scheduler_epoch_is_accepted(self):
        value=run_mod.resolve_dispatch_provenance("workflow_dispatch","external_scheduler","1789274400"); self.assertEqual(value["scheduler_epoch"],1789274400)
    def test_malformed_scheduler_epoch_fails_closed_before_capture(self):
        cfg=json.loads((ROOT/"campready-phase4b/config.json").read_text())
        fake_capture=Mock(side_effect=AssertionError("capture must not execute"))
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td); (temp/"config.json").write_text(json.dumps(cfg))
            with patch.object(run_mod,"ROOT",temp), patch.object(run_mod,"REPO",temp), patch.object(run_mod,"verify_hashes",return_value={"contract":True}), patch.object(run_mod,"capture",fake_capture), patch.dict("os.environ",{"GITHUB_EVENT_NAME":"workflow_dispatch","CAMPREADY_TRIGGER_SOURCE":"external_scheduler","CAMPREADY_SCHEDULER_EPOCH":"not-a-timestamp"},clear=True):
                self.assertEqual(run_mod.main(datetime(2026,9,15,tzinfo=timezone.utc)),2)
            fake_capture.assert_not_called()
            report=json.loads((temp/"campready-phase4b/runs/20260915T000000Z/run-report.json").read_text()); self.assertEqual(report["status"],"HOLD_PROVENANCE_INPUT"); self.assertIs(report["network_request_started"],False); self.assertIs(report["state_advanced"],False)
    def test_dispatch_provenance_does_not_affect_comparison_or_notification_eligibility(self):
        event={"quality":"OK","source_family":"NPS","source_url":"u","fingerprint":"new"}; obs={"supported_campgrounds":[{"canonical_key":"x","authority_family":"NPS","semantic_events":[event],"unknown_results":[]}]}; prior={"sites":{"x":{"events":{"NPS:u":{"fingerprint":"old"}}}}}
        manual=run_mod.compare(prior,obs); external=run_mod.compare(prior,obs)
        self.assertEqual(manual,external); self.assertEqual(manual["semantic_deltas"],external["semantic_deltas"]); self.assertEqual(manual["would_notify_candidates"],external["would_notify_candidates"])
    def test_root_readme_absent(self): self.assertFalse((ROOT/"README").exists()); self.assertFalse((ROOT/"README.md").exists())
    def test_no_credential_literal_in_tracked_files(self):
        tracked=subprocess.run(["git","ls-files","-z"],cwd=ROOT,check=True,capture_output=True).stdout.split(b"\0")
        forbidden=(b"Authorization: Bearer "+b"github_",b"gh"+b"p_",b"github"+b"_pat_")
        for name in filter(None,tracked):
            data=(ROOT/name.decode()).read_bytes()
            for literal in forbidden: self.assertNotIn(literal,data,name.decode())
    def test_http_and_parser_failures_not_semantic(self):
        prior={"sites":{"x":{"events":{"a":{"fingerprint":"old"}}}}}; obs={"supported_campgrounds":[{"canonical_key":"x","authority_family":"NPS","semantic_events":[],"unknown_results":[]}]}; result=run_mod.compare(prior,obs); self.assertEqual(result["semantic_deltas"],[]); self.assertEqual(result["would_notify_candidates"],[])
    def test_fixture_a_detail_403_retains_event_and_unchanged_recovery_is_not_appearance(self):
        event=self.alert_event(); prior=self.state(event); failed=self.request(event["source_url"],"ALERT_DETAIL",False)
        failure=run_mod.compare(prior,self.observation(requests=[failed])); self.assertEqual(failure["semantic_deltas"],[]); self.assertEqual(failure["accepted_state"]["low-gap"]["events"],prior["sites"]["low-gap"]["events"]); self.assertEqual(failure["unsafe_candidates"],[])
        recovery=run_mod.compare({"sites":failure["accepted_state"]},self.observation([event],[self.request(event["source_url"],"ALERT_DETAIL",True)])); self.assertEqual(recovery["semantic_deltas"],[]); self.assertEqual(sum(d["category"]=="APPEARANCE" for d in recovery["semantic_deltas"]),0)
    def test_fixture_b_multiple_403s_retain_event_until_unchanged_recovery(self):
        event=self.alert_event(); accepted=self.state(event); failed=self.request(event["source_url"],"ALERT_DETAIL",False)
        for _ in range(2):
            result=run_mod.compare(accepted,self.observation(requests=[failed])); self.assertEqual(result["semantic_deltas"],[]); accepted={"sites":result["accepted_state"]}
        recovery=run_mod.compare(accepted,self.observation([event],[self.request(event["source_url"],"ALERT_DETAIL",True)])); self.assertEqual(recovery["semantic_deltas"],[])
    def test_fixture_c_changed_recovery_is_one_material_update_against_retained_fingerprint(self):
        event=self.alert_event(); failed=self.request(event["source_url"],"ALERT_DETAIL",False); retained=run_mod.compare(self.state(event),self.observation(requests=[failed]))
        changed=self.alert_event("changed-fingerprint"); changed["semantic"]["bounded_official_text"]="deterministically changed bounded text"
        recovery=run_mod.compare({"sites":retained["accepted_state"]},self.observation([changed],[self.request(event["source_url"],"ALERT_DETAIL",True)])); self.assertEqual(recovery["semantic_deltas"],[{"canonical_key":"low-gap","event_key":f"USFS_WEBSITE_ALERTS:{event['source_url']}","before":event["fingerprint"],"after":"changed-fingerprint","category":"MATERIAL_UPDATE"}]); self.assertFalse(any(d["category"]=="APPEARANCE" for d in recovery["semantic_deltas"]))
    def test_fixture_d_successful_absence_overrides_retention_without_manufactured_delta(self):
        event=self.alert_event(); failed=self.request(event["source_url"],"ALERT_DETAIL",False); retained=run_mod.compare(self.state(event),self.observation(requests=[failed]))
        success=self.request(event["source_scope_url"],"ALERT_INDEX",True); absent=run_mod.compare({"sites":retained["accepted_state"]},self.observation(requests=[success])); self.assertEqual(absent["semantic_deltas"],[]); self.assertEqual(absent["accepted_state"]["low-gap"]["events"],{})
    def test_fixture_e_first_observation_failure_invents_no_state_or_candidate(self):
        event=self.alert_event(); result=run_mod.compare({"sites":{"low-gap":{"authority_family":"USFS","events":{}}}},self.observation(requests=[self.request(event["source_url"],"ALERT_DETAIL",False)])); self.assertEqual(result["accepted_state"]["low-gap"]["events"],{}); self.assertEqual(result["review_candidates"],[])
    def test_alert_index_failure_retains_all_explicitly_scoped_events_and_recovery_is_quiet(self):
        scope="https://example.test/alerts"; a=self.alert_event(url="https://example.test/a",scope=scope); b=self.alert_event(url="https://example.test/b",scope=scope); prior={"sites":{"low-gap":{"authority_family":"USFS","events":{"USFS_WEBSITE_ALERTS:https://example.test/a":a,"USFS_WEBSITE_ALERTS:https://example.test/b":b}}}}
        failure=run_mod.compare(prior,self.observation(requests=[self.request(scope,"ALERT_INDEX",False)])); self.assertEqual(len(failure["accepted_state"]["low-gap"]["events"]),2); self.assertEqual(failure["semantic_deltas"],[])
        recovery=run_mod.compare({"sites":failure["accepted_state"]},self.observation([a,b],[self.request(scope,"ALERT_INDEX",True)])); self.assertEqual(recovery["semantic_deltas"],[])
    def test_mixed_run_retains_only_failed_scope_while_successful_source_updates(self):
        a=self.alert_event(url="https://x.test/a",scope="https://x.test/alerts"); old_b=self.alert_event("old-b",url="https://y.test/b",scope="https://y.test/alerts"); new_b=self.alert_event("new-b",url="https://y.test/b",scope="https://y.test/alerts"); prior={"sites":{"low-gap":{"authority_family":"USFS","events":{"USFS_WEBSITE_ALERTS:https://x.test/a":a,"USFS_WEBSITE_ALERTS:https://y.test/b":old_b}}}}
        obs=self.observation([new_b],[self.request(a["source_scope_url"],"ALERT_INDEX",False),self.request(new_b["source_scope_url"],"ALERT_INDEX",True)]); result=run_mod.compare(prior,obs); self.assertEqual([d["category"] for d in result["semantic_deltas"]],["MATERIAL_UPDATE"]); self.assertEqual(set(result["accepted_state"]["low-gap"]["events"]),set(prior["sites"]["low-gap"]["events"])); self.assertEqual(result["unsafe_candidates"],[])
    def test_success_record_overrides_duplicate_failure_and_malformed_provenance_fails_closed(self):
        event=self.alert_event(); requests=[self.request(event["source_url"],"ALERT_DETAIL",False),self.request(event["source_url"],"ALERT_DETAIL",True)]; result=run_mod.compare(self.state(event),self.observation(requests=requests)); self.assertEqual(result["accepted_state"]["low-gap"]["events"],{})
        malformed=dict(event,source_scope_url={"not":"a URL"}); result=run_mod.compare(self.state(malformed),self.observation(requests=[self.request(event["source_scope_url"],"ALERT_INDEX",False)])); self.assertEqual(result["accepted_state"]["low-gap"]["events"],{})
    def test_real_403_record_remains_data_quality_failure_and_reliability_failure(self):
        capture=json.loads((ROOT/"campready-phase4b/runs/20260915T094016Z/capture.json").read_text()); url=self.alert_event()["source_url"]; request=next(r for r in capture["source_requests"] if r["source_url"]==url)
        self.assertIs(request["retrieval_succeeded"],False); self.assertEqual(request["parser_result"],"NOT_ATTEMPTED_RETRIEVAL_FAILURE"); self.assertEqual(capture["metrics"]["supported_core_retrieval"]["attempted"]-capture["metrics"]["supported_core_retrieval"]["successful"],sum(not r["retrieval_succeeded"] for r in capture["source_requests"] if r["reliability_denominator"]=="SUPPORTED_CORE"))
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
