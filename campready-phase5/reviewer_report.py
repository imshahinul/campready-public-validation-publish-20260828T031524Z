"""Narrow local text report with explicit source/system/reviewer separation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from alert_detail_guard import validate_alert_detail_guard
from workflow import reconstruct_projection


def _value(value: Any) -> str:
    return "UNAVAILABLE" if value is None or value == "" else str(value)


def render_reviewer_report(review_items: Sequence[Mapping[str, Any]], annotations_by_item: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    sections: list[str] = ["CAMPREADY INTERNAL REVIEWER REPORT", "REVIEW ONLY — NO DELIVERY"]
    for item in review_items:
        projection = reconstruct_projection(item, annotations_by_item.get(item["review_item_id"], ()))
        guarded = validate_alert_detail_guard(item)
        sections.extend(["", "=" * 72, f"REVIEW ITEM: {item['review_item_id']}"])
        if guarded:
            sections.extend(["!!! DATA_QUALITY !!!", "!!! UNKNOWN !!!", "!!! ALERT_DETAIL FINAL RETRIEVAL FAILURE !!!",
                             "!!! MANUAL ACKNOWLEDGEMENT REQUIRED !!!", "!!! NO SEMANTIC / SAFETY INFERENCE !!!"])
        title = item["source_title"]
        evidence = item["source_evidence"]
        sections.extend(["", "SOURCE FACT", f"Official source URL: {item['official_source_url']}",
            f"Title: {_value(title.get('value'))}", f"Title unavailable reason: {_value(title.get('reason_code'))}",
            f"Evidence label: {evidence['representation']}", f"Evidence content: {_value(evidence.get('content'))}",
            f"Evidence unavailable reason: {_value(evidence.get('unavailable_reason_code'))}"])
        retry = item.get("retry_telemetry_summary")
        sections.extend(["", "SYSTEM CLASSIFICATION", f"Retrieval state: {item['retrieval_status']}",
            f"Parser state: {item['parser_status']}", f"Relevance state: {item['relevance_status']}",
            f"Retry status: {_value(retry)}", f"Lifecycle: {item['lifecycle_classification']}",
            f"Prior accepted state ref: {_value(item.get('prior_accepted_state_ref'))}",
            f"Current accepted state ref: {_value(item.get('current_accepted_state_ref'))}",
            "Interpretation prohibitions: " + (", ".join(item.get("interpretation_prohibitions", [])) or "NONE")])
        ack = projection["reviewer_acknowledgement"]
        sections.extend(["", "REVIEWER ANNOTATION", f"Reviewer status: {projection['reviewer_status']}",
            f"Reviewer disposition: {_value(projection.get('reviewer_disposition'))}",
            f"Reviewer-authored note: {_value(projection.get('reviewer_note'))}",
            f"Reviewer acknowledgement recorded: {ack['acknowledged']}",
            f"Annotation ref: {_value(projection.get('annotation_ref'))}",
            f"Upstream event ref: {_value(item.get('upstream_event_id'))}",
            f"Affected relationship ref: {item['affected_relationship_id']}"])
    return "\n".join(sections) + "\n"
