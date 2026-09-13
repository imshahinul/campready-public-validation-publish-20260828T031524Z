"""Append explicit human-supplied maintenance time; never infers minutes."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = ("SOURCE_INSPECTION", "PARSER_FIX", "MAPPING_FIX", "AMBIGUITY_REVIEW", "HARNESS_FIX", "WORKFLOW_FIX", "OTHER")
LOG = Path(__file__).with_name("maintenance") / "human-maintenance.jsonl"


def append_entry(minutes: int, category: str, site_or_source: str, reason: str, action: str, notes: str = "", week_number: int | None = None, timestamp: str | None = None) -> dict:
    if not isinstance(minutes, int) or minutes < 0: raise ValueError("minutes must be an explicit nonnegative integer")
    if category not in CATEGORIES: raise ValueError("invalid maintenance category")
    if minutes == 0 and reason != "NO_HUMAN_MAINTENANCE_THIS_WEEK": raise ValueError("zero minutes requires weekly zero-maintenance attestation reason")
    if reason == "NO_HUMAN_MAINTENANCE_THIS_WEEK" and (minutes != 0 or week_number is None): raise ValueError("weekly zero attestation requires minutes=0 and week_number")
    entry = {"timestamp_utc": timestamp or datetime.now(timezone.utc).isoformat(), "minutes": minutes, "category": category, "site_or_source": site_or_source, "reason": reason, "action": action, "notes": notes}
    if week_number is not None: entry["week_number"] = week_number
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as stream: stream.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--minutes", required=True, type=int); parser.add_argument("--category", required=True, choices=CATEGORIES); parser.add_argument("--site-or-source", required=True); parser.add_argument("--reason", required=True); parser.add_argument("--action", required=True); parser.add_argument("--notes", default=""); parser.add_argument("--week-number", type=int)
    print(json.dumps(append_entry(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__": main()
