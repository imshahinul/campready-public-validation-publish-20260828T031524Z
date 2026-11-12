# CampReady Phase 5-1 review-item read model

This internal, review-only implementation deterministically projects persisted CampReady evidence into `campready.phase5.review_item.v1`. It does not retrieve, parse, classify, compare, schedule, notify, or write reviewer annotations. `notification_eligible` is always `false`.

## Frozen cohort

The exact canonical keys are `andrews-cove`, `low-gap`, `nc-van-hook-glade`, `wv-big-rock`, `or-spinreel`, `or-cold-springs`, `ncsp-carolina-beach`, `ncsp-stone-mountain`, `nps-elkmont`, and `nps-big-meadows`. No USACE or additional site is included.

## Read-only boundary

`read_model.py` is a library with no import-time I/O. It reads explicit JSON paths and the local Phase 5 config only when called. Canonical Phase 4 state, history, runs, review queue, mapping, parser, classifier, workflow, and retry evidence are immutable inputs. Network access and canonical writes are prohibited.

## Exact identities

Serialization is UTF-8 JSON with `ensure_ascii=false`, separators `(",", ":")`, no whitespace, and tuple order represented by a JSON array.

Semantic ID is `"cr5ri1-" + sha256(JSON(["campready.phase5.review_item.v1","semantic",upstream_event_id,affected_relationship_id,prior_accepted_state_ref or "",current_accepted_state_ref or "",lifecycle_classification])).hexdigest()`.

Quality ID is `"cr5ri1-" + sha256(JSON(["campready.phase5.review_item.v1","quality",provenance_run_id,logical_request_id,source_role,official_source_url,data_quality_reason_code])).hexdigest()`.

Golden vectors: semantic inputs `event-α`, `rel-1`, null prior, `sha256:abc` current, `APPEARANCE` produce `cr5ri1-56b244c4926514e8687ce7cd28881e48c315cc5b3f2991d199303ecdcb28e4cd`. Quality inputs `run-1`, `request-1`, `ALERT_DETAIL`, `https://example.gov/a`, `FINAL_RETRIEVAL_FAILURE` produce `cr5ri1-cd3f1b40aff1a50431894805f42f80e4fcbcd335f7e9bca6565b158f0dfc0bb8`.

The semantic relationship fallback is `"cr5rel1-" + sha256(JSON([canonical_site_id,upstream_event_id])).hexdigest()`. The quality relationship fallback is `"cr5relq1-" + sha256(JSON([canonical_site_id,logical_request_id,source_role,official_source_url])).hexdigest()`.

## Projection rules

Persisted source evidence is explicitly labeled `VERBATIM`, `SUMMARY`, or `UNAVAILABLE`; missing titles are `UNKNOWN` with a reason and are never generated from URLs. Quality-only items use lifecycle `NONE`, make no semantic event claim, expose `DATA_QUALITY` and `unknown=true` with stable reasons, and retain an attributable prior state on final failure. State fields are references, never copied state blobs.

A USFS `ALERT_DETAIL` item with eventual/final failure has `alert_detail_guard_required=true` and all frozen no-inference prohibitions. A first-attempt failure rescued by retry is not guarded as a final failure, while retry telemetry remains visible.

## Explicit exclusions

There is no reviewer workflow, annotation write, acknowledgement transition, UI, dashboard, maintenance/cost ledger, pilot runtime, delivery path, live network retrieval, scheduler change, parser/relevance/lifecycle change, or canonical evidence mutation in Phase 5-1.
