"""Transactional Phase 4B capture/comparison/state runner."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from capture import capture

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent

EXTERNAL_SCHEDULER_TRIGGER_SOURCE = "external_scheduler"
MANUAL_TRIGGER_SOURCE = "manual"
MIN_PLAUSIBLE_SCHEDULER_EPOCH = 946684800  # 2000-01-01T00:00:00Z
MAX_PLAUSIBLE_SCHEDULER_EPOCH = 4102444799  # 2099-12-31T23:59:59Z


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def semantic_state(observation: dict) -> dict:
    return {site["canonical_key"]: {"authority_family": site["authority_family"], "events": {f"{event.get('source_family')}:{event.get('event_id', event.get('source_url'))}": event for event in site["semantic_events"] if event.get("quality") == "OK"}} for site in observation["supported_campgrounds"]}


def failed_source_scopes(observation: dict) -> tuple[set[str], set[str]]:
    """Return explicitly failed USFS alert detail and index URLs."""
    requests = observation.get("source_requests", [])
    if not isinstance(requests, list):
        return set(), set()
    failed_details, failed_indexes = set(), set()
    successful_details, successful_indexes = set(), set()
    for request in requests:
        if not isinstance(request, dict) or request.get("source_family") != "USFS_WEBSITE_ALERTS":
            continue
        url, role, succeeded = request.get("source_url"), request.get("source_role"), request.get("retrieval_succeeded")
        if not isinstance(url, str) or not url or succeeded not in (True, False):
            continue
        if role == "ALERT_DETAIL":
            (successful_details if succeeded else failed_details).add(url)
        elif role == "ALERT_INDEX":
            (successful_indexes if succeeded else failed_indexes).add(url)
    return failed_details - successful_details, failed_indexes - successful_indexes


def retain_failed_source_state(current: dict, prior: dict, observation: dict) -> dict:
    """Carry attributable prior events into accepted state, never comparator input."""
    failed_details, failed_indexes = failed_source_scopes(observation)
    if not failed_details and not failed_indexes:
        return current
    prior_sites = prior.get("sites", {})
    if not isinstance(prior_sites, dict):
        return current
    for site, value in current.items():
        prior_site = prior_sites.get(site, {})
        prior_events = prior_site.get("events", {}) if isinstance(prior_site, dict) else {}
        if not isinstance(prior_events, dict):
            continue
        for key, event in prior_events.items():
            if key in value["events"] or not isinstance(event, dict):
                continue
            source_url, source_scope_url = event.get("source_url"), event.get("source_scope_url")
            detail_failed = isinstance(source_url, str) and source_url in failed_details
            index_failed = isinstance(source_scope_url, str) and source_scope_url in failed_indexes
            if detail_failed or index_failed:
                value["events"][key] = event
    return current


def compare(prior: dict | None, observation: dict) -> dict:
    current = semantic_state(observation)
    if prior is None:
        return {"baseline": True, "source_deltas": [], "semantic_deltas": [], "relevance_deltas": [], "lifecycle_deltas": [], "review_candidates": [], "would_notify_candidates": [], "unsafe_candidates": [], "consumer_relevant_event_count": 0, "relevance_ambiguity_count": 0, "accepted_state": current}
    deltas, candidates = [], []
    for site, value in current.items():
        before = prior.get("sites", {}).get(site, {}).get("events", {})
        for key, event in value["events"].items():
            old = before.get(key)
            if old and old.get("fingerprint") != event.get("fingerprint"):
                delta = {"canonical_key": site, "event_key": key, "before": old.get("fingerprint"), "after": event.get("fingerprint"), "category": "MATERIAL_UPDATE"}
                deltas.append(delta); candidates.append({**delta, "review_only": True})
            elif not old:
                delta = {"canonical_key": site, "event_key": key, "before": None, "after": event.get("fingerprint"), "category": "APPEARANCE"}
                deltas.append(delta); candidates.append({**delta, "review_only": True})
    ambiguity = sum(len(site["unknown_results"]) for site in observation["supported_campgrounds"])
    accepted = retain_failed_source_state(current, prior, observation)
    return {"baseline": False, "source_deltas": deltas, "semantic_deltas": deltas, "relevance_deltas": [], "lifecycle_deltas": [], "review_candidates": candidates, "would_notify_candidates": candidates, "unsafe_candidates": [], "consumer_relevant_event_count": len(candidates), "relevance_ambiguity_count": ambiguity, "accepted_state": accepted}


def verify_hashes(config: dict) -> dict[str, bool]:
    manifest_path = REPO / config["paths"]["hash_manifest"]
    if not manifest_path.exists(): return {"manifest_present": False}
    manifest = json.loads(manifest_path.read_text())
    return {path: (REPO / path).is_file() and file_sha(REPO / path) == expected for path, expected in manifest["sha256"].items()}


def resolve_dispatch_provenance(
    event_name: str | None,
    trigger_source: str | None,
    scheduler_epoch: str | None,
) -> dict:
    """Validate dispatch-only metadata and return provenance without semantics."""
    event_name = (event_name or "").strip()
    trigger_source = (trigger_source or "").strip()
    scheduler_epoch = (scheduler_epoch or "").strip()

    if event_name != "workflow_dispatch":
        return {
            "trigger": event_name or "manual-local",
            "dispatch_provider": None,
            "scheduler_epoch": None,
            "github_event_name": event_name or None,
        }

    trigger_source = trigger_source or MANUAL_TRIGGER_SOURCE
    if trigger_source == MANUAL_TRIGGER_SOURCE:
        if scheduler_epoch:
            raise ValueError("manual dispatch cannot supply scheduler_epoch")
        return {
            "trigger": "manual",
            "dispatch_provider": None,
            "scheduler_epoch": None,
            "github_event_name": "workflow_dispatch",
        }

    if trigger_source != EXTERNAL_SCHEDULER_TRIGGER_SOURCE:
        raise ValueError("unsupported workflow_dispatch trigger_source")

    parsed_epoch = None
    if scheduler_epoch:
        if not scheduler_epoch.isascii() or not scheduler_epoch.isdigit():
            raise ValueError("scheduler_epoch must be an integer Unix timestamp")
        parsed_epoch = int(scheduler_epoch)
        if not MIN_PLAUSIBLE_SCHEDULER_EPOCH <= parsed_epoch <= MAX_PLAUSIBLE_SCHEDULER_EPOCH:
            raise ValueError("scheduler_epoch is outside the plausible range")

    return {
        "trigger": "external_schedule",
        "dispatch_provider": "external",
        "scheduler_epoch": parsed_epoch,
        "github_event_name": "workflow_dispatch",
    }


def main(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    config_path = ROOT / "config.json"; config = json.loads(config_path.read_text())
    if config["policy"]["send_real_notifications"] is not False: print("HOLD_REAL_NOTIFICATIONS_NOT_DISABLED"); return 20
    activation = config["activation"]
    if activation["validation_end_utc"] and now >= datetime.fromisoformat(activation["validation_end_utc"]):
        print(json.dumps({"status": "VALIDATION_WINDOW_COMPLETE", "network_request_started": False, "state_advanced": False, "real_notifications": 0})); return 0
    bindings = verify_hashes(config)
    if not bindings or not all(bindings.values()): print(json.dumps({"status": "HOLD_SOFTWARE_CONFIG_DRIFT", "frozen_bindings": bindings, "state_advanced": False, "real_notifications": 0})); return 2
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPO / config["paths"]["runs"] / run_id; run_dir.mkdir(parents=True, exist_ok=False)
    state_path = REPO / config["paths"]["state"]
    prior_document = json.loads(state_path.read_text()) if state_path.exists() and state_path.stat().st_size else None
    prior_sha = file_sha(state_path) if prior_document else None
    try:
        provenance = resolve_dispatch_provenance(
            os.getenv("GITHUB_EVENT_NAME"),
            os.getenv("CAMPREADY_TRIGGER_SOURCE"),
            os.getenv("CAMPREADY_SCHEDULER_EPOCH"),
        )
    except ValueError as exc:
        summary = {
            "run_id": run_id,
            "started_at_utc": now.isoformat(),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "HOLD_PROVENANCE_INPUT",
            "failure": f"ValueError: {exc}",
            "github_event_name": os.getenv("GITHUB_EVENT_NAME"),
            "trigger": "invalid_dispatch_provenance",
            "prior_state_sha256": prior_sha,
            "current_state_sha256": prior_sha,
            "accepted_state_sha256": prior_sha,
            "state_advanced": False,
            "unsafe_candidate_count": 0,
            "real_notifications": 0,
            "real_notifications_sent": 0,
            "network_request_started": False,
            "frozen_bindings": bindings,
        }
        atomic_json(run_dir / "run-report.json", summary)
        history_path = REPO / config["paths"]["history"]
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a") as stream:
            stream.write(json.dumps(summary, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2))
        return 2
    try:
        observation = capture(config_path, now=now)
        comparison = compare(prior_document, observation)
        accepted = {"schema_version": "campready-phase4b-state-v1", "accepted_at_utc": now.isoformat(), "run_id": run_id, "sites": comparison.pop("accepted_state")}
        atomic_json(run_dir / "capture.json", observation); atomic_json(run_dir / "comparison.json", comparison)
        accepted_sha = hashlib.sha256((json.dumps(accepted, indent=2, ensure_ascii=False) + "\n").encode()).hexdigest()
        summary = {"run_id": run_id, "started_at_utc": now.isoformat(), "finished_at_utc": datetime.now(timezone.utc).isoformat(), **provenance, "workflow_run_id": os.getenv("GITHUB_RUN_ID"), "validation_start_utc": activation["validation_start_utc"], "validation_end_utc": activation["validation_end_utc"], "status": "PASS", "cohort": observation["cohort"], "metrics": observation["metrics"], "telemetry": observation["telemetry"], "consumer_relevant_event_count": comparison["consumer_relevant_event_count"], "would_notify_candidate_count": len(comparison["would_notify_candidates"]), "review_candidate_count": len(comparison["review_candidates"]), "unsafe_candidate_count": 0, "relevance_ambiguity_count": comparison["relevance_ambiguity_count"], "real_notifications": 0, "prior_state_sha256": prior_sha, "accepted_state_sha256": accepted_sha, "state_advanced": True, "frozen_bindings": bindings}
        atomic_json(run_dir / "run-report.json", summary)
        history_path = REPO / config["paths"]["history"]; history_path.parent.mkdir(parents=True, exist_ok=True)
        old_history = history_path.read_bytes() if history_path.exists() else b""
        old_state = state_path.read_bytes() if state_path.exists() else None
        try:
            atomic_json(state_path, accepted)
            with history_path.open("a") as stream: stream.write(json.dumps(summary, sort_keys=True) + "\n"); stream.flush(); os.fsync(stream.fileno())
        except Exception:
            if old_state is None: state_path.unlink(missing_ok=True)
            else: state_path.write_bytes(old_state)
            history_path.write_bytes(old_history)
            raise
        if comparison["review_candidates"]:
            atomic_json(REPO / config["paths"]["review_queue"] / f"{run_id}.json", comparison["review_candidates"])
        print(json.dumps(summary, indent=2)); return 0
    except Exception as exc:
        summary = {"run_id": run_id, "started_at_utc": now.isoformat(), "finished_at_utc": datetime.now(timezone.utc).isoformat(), **provenance, "status": "HOLD_RUNTIME_FAILURE", "failure": f"{type(exc).__name__}: {exc}", "prior_state_sha256": prior_sha, "accepted_state_sha256": prior_sha, "state_advanced": False, "unsafe_candidate_count": 0, "real_notifications": 0, "frozen_bindings": bindings}
        atomic_json(run_dir / "run-report.json", summary)
        history_path = REPO / config["paths"]["history"]; history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a") as stream: stream.write(json.dumps(summary, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2)); return 2


if __name__ == "__main__": raise SystemExit(main())
