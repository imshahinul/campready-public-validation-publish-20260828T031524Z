"""One-shot bounded live validation; writes only the explicitly requested report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from authority_adapters import adapter_for, fetch, load_sites


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path(__file__).with_name("authority_sites.json")))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = []
    for site in load_sites(args.config):
        sources = []
        for role, url_key in (("CAMPGROUND_PAGE", "campground_url"), ("CONDITIONS_PAGE", "conditions_url")):
            retrieval = fetch(site[url_key])
            parsed = adapter_for(site["authority_family"]).parse(site, retrieval, role)
            sources.append({
                "source_role": role,
                "requested_url": retrieval.requested_url,
                "http_result": retrieval.http_status,
                "final_url": retrieval.final_url,
                "retrieval_failure": retrieval.failure_kind,
                "parser_result": parsed.status,
                "parser_failure": parsed.failure_detail,
                "identity_resolved": parsed.identity_resolved,
                "raw_extracted_candidate_count": parsed.raw_candidate_count,
                "post_scope_filter_event_count": parsed.post_scope_filter_count,
                "pre_dedup_observation_count": parsed.pre_dedup_observation_count,
                "post_dedup_event_count": len(parsed.observations),
                "observations": [item.to_dict() for item in parsed.observations],
                "parser_specific_exception_needed": False
            })
        rows.append({"campground_key": site["campground_key"], "campground_name": site["campground_name"], "authority_family": site["authority_family"], "sources": sources})
    Path(args.output).write_text(json.dumps({"live_validation": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
