from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


NWS_UA = (
    "CampReady-Phase3B3-Baseline/0.1 "
    "(prospective validation baseline)"
)

EXPECTED_KEYS = {
    "andrews-cove",
    "low-gap",
    "upper-chattahoochee",
    "sarahs-creek",
    "tate-branch",
    "lake-russell",
    "lake-rabun",
    "willis-knob",
    "wildcat-1",
    "wildcat-2",
}


def sha256_bytes(value):
    return hashlib.sha256(
        value
    ).hexdigest()


def sha256_file(path):
    return sha256_bytes(
        Path(path).read_bytes()
    )


def normalize_text(value):
    value = html.unescape(
        str(value or "")
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def canonical_semantic(value):
    if isinstance(value, dict):
        return {
            key: canonical_semantic(
                value[key]
            )
            for key in sorted(value)
        }

    if isinstance(value, list):
        return [
            canonical_semantic(item)
            for item in value
        ]

    if isinstance(value, str):
        return normalize_text(
            value
        )

    return value


def semantic_fingerprint(payload):
    encoded = json.dumps(
        canonical_semantic(
            payload
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return sha256_bytes(
        encoded
    )


def load_prior_script(path):
    text = Path(path).read_text(
        encoding="utf-8"
    )

    marker = "raise SystemExit("

    if marker not in text:
        raise RuntimeError(
            f"final SystemExit marker not found in {path}"
        )

    prefix = text.rsplit(
        marker,
        1,
    )[0]

    namespace = {
        "__name__": (
            "campready_loaded_"
            + hashlib.sha256(
                str(path).encode(
                    "utf-8"
                )
            ).hexdigest()[:12]
        ),
        "__file__": str(path),
    }

    exec(
        compile(
            prefix,
            str(path),
            "exec",
        ),
        namespace,
    )

    return namespace


class RecreationArticleParser(HTMLParser):
    BLOCKS = {
        "article",
        "section",
        "div",
        "p",
        "li",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
        "span",
    }

    SKIP = {
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "aside",
    }

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.capture = False
        self.article_depth = 0
        self.skip_depth = 0
        self.parts = []
        self.found = False

    def handle_starttag(
        self,
        tag,
        attrs,
    ):
        tag = tag.lower()
        attrs_dict = dict(attrs)

        if (
            tag == "article"
            and not self.capture
        ):
            classes = (
                attrs_dict.get(
                    "class",
                    ""
                )
                .split()
            )

            if "wfs-rec__full" in classes:
                self.capture = True
                self.article_depth = 1
                self.found = True
                return

        if not self.capture:
            return

        if tag == "article":
            self.article_depth += 1

        if tag in self.SKIP:
            self.skip_depth += 1
            return

        if (
            self.skip_depth == 0
            and tag in self.BLOCKS
        ):
            self.parts.append(
                "\n"
            )

    def handle_endtag(
        self,
        tag,
    ):
        tag = tag.lower()

        if not self.capture:
            return

        if (
            tag in self.SKIP
            and self.skip_depth > 0
        ):
            self.skip_depth -= 1
            return

        if (
            self.skip_depth == 0
            and tag in self.BLOCKS
        ):
            self.parts.append(
                "\n"
            )

        if tag == "article":
            self.article_depth -= 1

            if self.article_depth <= 0:
                self.capture = False

    def handle_data(
        self,
        data,
    ):
        if (
            self.capture
            and self.skip_depth == 0
            and data.strip()
        ):
            self.parts.append(
                data
            )


def parse_recreation_article(body):
    parser = RecreationArticleParser()

    parser.feed(
        body.decode(
            "utf-8",
            errors="replace",
        )
    )

    text = "\n".join(
        line.strip()
        for line
        in "".join(
            parser.parts
        ).splitlines()
        if line.strip()
    )

    text = re.sub(
        r"\n{2,}",
        "\n",
        text,
    ).strip()

    normalized = normalize_text(
        text
    ).casefold()

    signals = {
        "site_open_present": (
            "site open"
            in normalized
        ),
        "site_closed_present": (
            "site closed"
            in normalized
        ),
        "temporarily_closed_present": (
            "temporarily closed"
            in normalized
        ),
        "reopened_present": (
            "reopened"
            in normalized
            or
            "now open"
            in normalized
        ),
    }

    return {
        "article_found": (
            parser.found
        ),
        "article_text": text,
        "signals": signals,
    }


def nws_fetch_json(url):
    last_error = None

    for attempt in range(3):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": NWS_UA,
                "Accept": (
                    "application/geo+json"
                ),
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                body = response.read()

                payload = json.loads(
                    body.decode(
                        "utf-8"
                    )
                )

                return {
                    "status": (
                        response.status
                    ),
                    "payload": payload,
                    "bytes": len(body),
                    "error": None,
                }

        except Exception as exc:
            last_error = repr(exc)

            if attempt < 2:
                time.sleep(
                    1.0
                    * (attempt + 1)
                )

    return {
        "status": None,
        "payload": None,
        "bytes": 0,
        "error": last_error,
    }


def nws_semantic(feature):
    props = feature.get(
        "properties",
        {}
    )

    event_id = (
        feature.get("id")
        or props.get("@id")
        or props.get("id")
    )

    semantic = {
        "event": props.get(
            "event"
        ),
        "severity": props.get(
            "severity"
        ),
        "certainty": props.get(
            "certainty"
        ),
        "urgency": props.get(
            "urgency"
        ),
        "effective": props.get(
            "effective"
        ),
        "onset": props.get(
            "onset"
        ),
        "expires": props.get(
            "expires"
        ),
        "ends": props.get(
            "ends"
        ),
        "headline": props.get(
            "headline"
        ),
    }

    return (
        event_id,
        semantic,
    )


def main():
    mapping_path = Path(
        sys.argv[1]
    )

    parser_path = Path(
        sys.argv[2]
    )

    classifier_path = Path(
        sys.argv[3]
    )

    baseline_path = Path(
        sys.argv[4]
    )

    report_path = Path(
        sys.argv[5]
    )

    expected_classifier_sha = (
        sys.argv[6]
    )

    mapping_raw = (
        mapping_path.read_bytes()
    )

    mapping = json.loads(
        mapping_raw.decode(
            "utf-8"
        )
    )

    rows = mapping.get(
        "rows",
        [],
    )

    sites = {
        row["canonical_key"]: row
        for row in rows
    }

    mapping_gate = (
        mapping.get(
            "decision"
        )
        == (
            "PASS_CANONICAL_10_SITE_"
            "RELEVANCE_MAPPING"
        )
        and len(rows) == 10
        and set(
            sites.keys()
        )
        == EXPECTED_KEYS
    )

    parser_sha = sha256_file(
        parser_path
    )

    classifier_sha = sha256_file(
        classifier_path
    )

    classifier_binding_gate = (
        expected_classifier_sha
        not in {
            "",
            "MISSING",
            "NOT_FOUND",
        }
        and classifier_sha
        == expected_classifier_sha
    )

    parser_ns = load_prior_script(
        parser_path
    )

    classifier_ns = load_prior_script(
        classifier_path
    )

    required_parser_symbols = {
        "fetch",
        "extract_alert_links",
        "parse_content",
        "extract_date_field",
        "extract_line_field",
    }

    parser_symbol_gate = all(
        symbol in parser_ns
        for symbol
        in required_parser_symbols
    )

    classifier_symbol_gate = (
        "classify_event"
        in classifier_ns
    )

    created_at = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    reference_date = (
        datetime.now(
            timezone.utc
        ).date()
    )

    alert_index = parser_ns[
        "ALERT_INDEX"
    ]

    index_result = parser_ns[
        "fetch"
    ](
        alert_index
    )

    alert_links = []

    if (
        index_result[
            "status"
        ]
        == 200
    ):
        alert_links = parser_ns[
            "extract_alert_links"
        ](
            index_result[
                "body"
            ]
        )

    alert_links = (
        alert_links[:100]
    )

    usfs_alert_events = []
    usfs_relationships = []

    alert_fetch_success = 0
    alert_parse_success = 0

    for url in alert_links:
        response = parser_ns[
            "fetch"
        ](
            url
        )

        if (
            response[
                "status"
            ]
            == 200
        ):
            alert_fetch_success += 1

        parsed = {
            "content_block_found": (
                False
            ),
            "title": "",
            "text": "",
        }

        if response[
            "body"
        ]:
            parsed = parser_ns[
                "parse_content"
            ](
                response[
                    "body"
                ]
            )

        if (
            response[
                "status"
            ]
            == 200
            and parsed[
                "content_block_found"
            ]
        ):
            alert_parse_success += 1

        title = parsed[
            "title"
        ]

        if not title:
            title = (
                urllib.parse
                .urlsplit(url)
                .path
                .rstrip("/")
                .split("/")[-1]
                .replace("-", " ")
            )

        text = parsed[
            "text"
        ]

        start_date = parser_ns[
            "extract_date_field"
        ](
            text,
            "Alert Start Date",
        )

        end_date = parser_ns[
            "extract_date_field"
        ](
            text,
            "Alert End Date",
        )

        rec_sites = parser_ns[
            "extract_line_field"
        ](
            text,
            "Rec Sites Affected",
        )

        order_number = parser_ns[
            "extract_line_field"
        ](
            text,
            "Order Number",
        )

        last_updated = parser_ns[
            "extract_line_field"
        ](
            text,
            "Last updated",
        )

        event_id = (
            "usfs:"
            + hashlib.sha256(
                url.encode(
                    "utf-8"
                )
            ).hexdigest()[:20]
        )

        captured = {
            "event_id": event_id,
            "source_url": url,
            "fetch_status": (
                response[
                    "status"
                ]
            ),
            "content_block_found": (
                parsed[
                    "content_block_found"
                ]
            ),
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "rec_sites_affected": (
                rec_sites
            ),
            "order_number": (
                order_number
            ),
            "last_updated": (
                last_updated
            ),
            "text_excerpt": text,
        }

        semantic = {
            "canonical_url": url,
            "title": title,
            "alert_start_date": (
                start_date
            ),
            "alert_end_date": (
                end_date
            ),
            "order_number": (
                order_number
            ),
            "bounded_official_text": (
                text
            ),
        }

        if (
            response[
                "status"
            ]
            == 200
            and parsed[
                "content_block_found"
            ]
        ):
            relations = (
                classifier_ns[
                    "classify_event"
                ](
                    captured,
                    sites,
                    reference_date,
                )
            )

            parsed_sites = (
                classifier_ns[
                    "parse_rec_sites_section"
                ](
                    captured
                )
            )

            operational_date = (
                classifier_ns[
                    "operational_effective_date"
                ](
                    captured
                )
            )

            semantic[
                "rec_sites_affected"
            ] = parsed_sites

            semantic[
                "operational_effective_date"
            ] = operational_date

            for site_key in sorted(
                relations
            ):
                relation = relations[
                    site_key
                ]

                usfs_relationships.append({
                    "source_event_id": (
                        event_id
                    ),
                    "campground": (
                        site_key
                    ),
                    "relevance": (
                        relation.get(
                            "relevance"
                        )
                    ),
                    "reason": (
                        relation.get(
                            "reason"
                        )
                    ),
                    "lifecycle": (
                        relation.get(
                            "lifecycle"
                        )
                    ),
                    "baseline_notification_eligible": (
                        False
                    ),
                    "resolution": (
                        bool(
                            relation.get(
                                "resolution"
                            )
                        )
                    ),
                })

        else:
            semantic[
                "data_quality_failure"
            ] = True

        usfs_alert_events.append({
            "source_event_id": (
                event_id
            ),
            "source_family": (
                "USFS_ALERT_INDEX"
            ),
            "source_url": url,
            "quality": (
                "OK"
                if (
                    response[
                        "status"
                    ]
                    == 200
                    and parsed[
                        "content_block_found"
                    ]
                )
                else
                "DATA_QUALITY_FAILURE"
            ),
            "semantic": semantic,
            "semantic_fingerprint": (
                semantic_fingerprint(
                    semantic
                )
            ),
            "first_observed": (
                created_at
            ),
            "last_observed": (
                created_at
            ),
        })

        time.sleep(
            0.05
        )

    recreation_pages = []

    recreation_fetch_success = 0
    recreation_parse_success = 0

    for site_key in sorted(
        sites
    ):
        site = sites[
            site_key
        ]

        url = site[
            "recreation_page_url"
        ]

        response = parser_ns[
            "fetch"
        ](
            url
        )

        if (
            response[
                "status"
            ]
            == 200
        ):
            recreation_fetch_success += 1

        parsed = (
            parse_recreation_article(
                response[
                    "body"
                ]
            )
            if response[
                "body"
            ]
            else {
                "article_found": False,
                "article_text": "",
                "signals": {},
            }
        )

        if (
            response[
                "status"
            ]
            == 200
            and parsed[
                "article_found"
            ]
            and parsed[
                "article_text"
            ]
        ):
            recreation_parse_success += 1

        semantic = {
            "canonical_key": (
                site_key
            ),
            "canonical_url": url,
            "bounded_article_text": (
                parsed[
                    "article_text"
                ]
            ),
            "signals": (
                parsed[
                    "signals"
                ]
            ),
        }

        recreation_pages.append({
            "source_event_id": (
                "campground:"
                + site_key
            ),
            "source_family": (
                "USFS_RECREATION_PAGE"
            ),
            "campground": (
                site_key
            ),
            "source_url": url,
            "http_status": (
                response[
                    "status"
                ]
            ),
            "article_found": (
                parsed[
                    "article_found"
                ]
            ),
            "quality": (
                "OK"
                if (
                    response[
                        "status"
                    ]
                    == 200
                    and parsed[
                        "article_found"
                    ]
                    and parsed[
                        "article_text"
                    ]
                )
                else
                "DATA_QUALITY_FAILURE"
            ),
            "semantic": semantic,
            "semantic_fingerprint": (
                semantic_fingerprint(
                    semantic
                )
            ),
            "first_observed": (
                created_at
            ),
            "last_observed": (
                created_at
            ),
            "baseline_notification_eligible": (
                False
            ),
        })

        time.sleep(
            0.05
        )

    nws_site_queries = []
    nws_events_by_id = {}
    nws_relationships = []

    nws_query_success = 0
    nws_payload_success = 0
    nws_event_conflicts = []

    for site_key in sorted(
        sites
    ):
        site = sites[
            site_key
        ]

        lat = site[
            "latitude"
        ]

        lon = site[
            "longitude"
        ]

        url = (
            "https://api.weather.gov/"
            "alerts/active?point="
            + str(lat)
            + ","
            + str(lon)
        )

        response = nws_fetch_json(
            url
        )

        if (
            response[
                "status"
            ]
            == 200
        ):
            nws_query_success += 1

        payload = response[
            "payload"
        ]

        features = None

        if isinstance(
            payload,
            dict,
        ):
            candidate = payload.get(
                "features"
            )

            if isinstance(
                candidate,
                list,
            ):
                features = candidate
                nws_payload_success += 1

        ids_for_site = []

        if features is not None:
            for feature in features:
                event_id, semantic = (
                    nws_semantic(
                        feature
                    )
                )

                if not event_id:
                    continue

                source_event_id = (
                    "nws:"
                    + str(
                        event_id
                    )
                )

                fp = (
                    semantic_fingerprint(
                        semantic
                    )
                )

                ids_for_site.append(
                    source_event_id
                )

                existing = (
                    nws_events_by_id.get(
                        source_event_id
                    )
                )

                if existing is None:
                    nws_events_by_id[
                        source_event_id
                    ] = {
                        "source_event_id": (
                            source_event_id
                        ),
                        "official_alert_id": (
                            event_id
                        ),
                        "source_family": (
                            "NWS_ACTIVE_POINT_ALERT"
                        ),
                        "semantic": semantic,
                        "semantic_fingerprint": (
                            fp
                        ),
                        "quality": "OK",
                        "first_observed": (
                            created_at
                        ),
                        "last_observed": (
                            created_at
                        ),
                    }

                elif (
                    existing[
                        "semantic_fingerprint"
                    ]
                    != fp
                ):
                    nws_event_conflicts.append({
                        "source_event_id": (
                            source_event_id
                        ),
                        "campground": (
                            site_key
                        ),
                    })

                nws_relationships.append({
                    "source_event_id": (
                        source_event_id
                    ),
                    "campground": (
                        site_key
                    ),
                    "relevance": (
                        "INCLUDE"
                    ),
                    "reason": (
                        "NWS_POINT_ALERT"
                    ),
                    "baseline_notification_eligible": (
                        False
                    ),
                })

        nws_site_queries.append({
            "campground": (
                site_key
            ),
            "url": url,
            "http_status": (
                response[
                    "status"
                ]
            ),
            "valid_features_list": (
                features
                is not None
            ),
            "active_alert_ids": (
                sorted(
                    ids_for_site
                )
            ),
            "error": (
                response[
                    "error"
                ]
            ),
        })

        time.sleep(
            0.1
        )

    nws_events = sorted(
        nws_events_by_id.values(),
        key=lambda item: item[
            "source_event_id"
        ],
    )

    all_source_ids = (
        [
            item[
                "source_event_id"
            ]
            for item
            in usfs_alert_events
        ]
        + [
            item[
                "source_event_id"
            ]
            for item
            in recreation_pages
        ]
        + [
            item[
                "source_event_id"
            ]
            for item
            in nws_events
        ]
    )

    source_identity_unique = (
        len(
            all_source_ids
        )
        == len(
            set(
                all_source_ids
            )
        )
    )

    all_relationship_keys = (
        [
            (
                item[
                    "source_event_id"
                ],
                item[
                    "campground"
                ],
            )
            for item
            in usfs_relationships
        ]
        + [
            (
                item[
                    "source_event_id"
                ],
                item[
                    "campground"
                ],
            )
            for item
            in nws_relationships
        ]
    )

    relationship_unique = (
        len(
            all_relationship_keys
        )
        == len(
            set(
                all_relationship_keys
            )
        )
    )

    reason_counts = Counter(
        item[
            "reason"
        ]
        for item
        in usfs_relationships
    )

    relevance_counts = Counter(
        item[
            "relevance"
        ]
        for item
        in usfs_relationships
    )

    baseline = {
        "schema_version": (
            "campready-live-baseline-v1"
        ),
        "baseline_id": (
            "campready:"
            + created_at
        ),
        "created_at_utc": (
            created_at
        ),
        "reference_date_utc": (
            reference_date.isoformat()
        ),
        "frozen_inputs": {
            "mapping_sha256": (
                sha256_bytes(
                    mapping_raw
                )
            ),
            "parser_sha256": (
                parser_sha
            ),
            "classifier_sha256": (
                classifier_sha
            ),
        },
        "policy": {
            "baseline_notifications_allowed": (
                False
            ),
            "unknown_notifications_allowed": (
                False
            ),
            "expired_notifications_allowed": (
                False
            ),
            "scheduled_notifications_allowed": (
                False
            ),
            "removal_as_resolution_allowed": (
                False
            ),
        },
        "usfs_alert_index": {
            "url": alert_index,
            "http_status": (
                index_result[
                    "status"
                ]
            ),
            "source_events": (
                usfs_alert_events
            ),
            "relationships": (
                usfs_relationships
            ),
        },
        "usfs_recreation_pages": (
            recreation_pages
        ),
        "nws": {
            "site_queries": (
                nws_site_queries
            ),
            "source_events": (
                nws_events
            ),
            "relationships": (
                nws_relationships
            ),
        },
        "notification_ledger": [],
        "baseline_notifications": [],
    }

    baseline_path.write_text(
        json.dumps(
            baseline,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    persisted_raw = (
        baseline_path.read_bytes()
    )

    persisted = json.loads(
        persisted_raw.decode(
            "utf-8"
        )
    )

    persistence_gate = (
        persisted.get(
            "schema_version"
        )
        == "campready-live-baseline-v1"
        and persisted.get(
            "baseline_notifications"
        )
        == []
        and persisted.get(
            "notification_ledger"
        )
        == []
        and len(
            persisted.get(
                "usfs_recreation_pages",
                [],
            )
        )
        == 10
    )

    alert_count = len(
        alert_links
    )

    required_alert_success = (
        math.ceil(
            alert_count
            * 0.95
        )
        if alert_count
        else 0
    )

    usfs_index_gate = (
        index_result[
            "status"
        ]
        == 200
        and alert_count >= 1
    )

    usfs_alert_gate = (
        alert_fetch_success
        >= required_alert_success
        and alert_parse_success
        >= required_alert_success
    )

    recreation_gate = (
        recreation_fetch_success
        == 10
        and recreation_parse_success
        == 10
    )

    nws_gate = (
        nws_query_success
        == 10
        and nws_payload_success
        == 10
        and not nws_event_conflicts
    )

    baseline_notification_gate = (
        persisted[
            "baseline_notifications"
        ]
        == []
        and persisted[
            "notification_ledger"
        ]
        == []
    )

    provenance_gate = (
        mapping_gate
        and parser_symbol_gate
        and classifier_symbol_gate
        and classifier_binding_gate
    )

    if (
        provenance_gate
        and usfs_index_gate
        and usfs_alert_gate
        and recreation_gate
        and nws_gate
        and baseline_notification_gate
        and persistence_gate
        and source_identity_unique
        and relationship_unique
    ):
        decision = (
            "PASS_FIRST_LIVE_BASELINE_CAPTURE"
        )
        rc = 0

    else:
        decision = (
            "HOLD_FIRST_LIVE_BASELINE_CAPTURE"
        )
        rc = 2

    report = {
        "validated_at_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "inputs": {
            "mapping_path": str(
                mapping_path
            ),
            "mapping_sha256": (
                sha256_bytes(
                    mapping_raw
                )
            ),
            "parser_path": str(
                parser_path
            ),
            "parser_sha256": (
                parser_sha
            ),
            "classifier_path": str(
                classifier_path
            ),
            "classifier_sha256": (
                classifier_sha
            ),
            "expected_classifier_sha256": (
                expected_classifier_sha
            ),
        },
        "gates": {
            "mapping_gate": (
                mapping_gate
            ),
            "parser_symbol_gate": (
                parser_symbol_gate
            ),
            "classifier_symbol_gate": (
                classifier_symbol_gate
            ),
            "classifier_binding_gate": (
                classifier_binding_gate
            ),
            "usfs_index_gate": (
                usfs_index_gate
            ),
            "usfs_alert_gate": (
                usfs_alert_gate
            ),
            "recreation_gate": (
                recreation_gate
            ),
            "nws_gate": (
                nws_gate
            ),
            "baseline_notification_gate": (
                baseline_notification_gate
            ),
            "persistence_gate": (
                persistence_gate
            ),
            "source_identity_unique": (
                source_identity_unique
            ),
            "relationship_unique": (
                relationship_unique
            ),
        },
        "summary": {
            "usfs_alert_index_http": (
                index_result[
                    "status"
                ]
            ),
            "usfs_live_alert_count": (
                alert_count
            ),
            "usfs_alert_fetch_success": (
                f"{alert_fetch_success}/"
                f"{alert_count}"
            ),
            "usfs_alert_parse_success": (
                f"{alert_parse_success}/"
                f"{alert_count}"
            ),
            "usfs_required_95pct_count": (
                required_alert_success
            ),
            "usfs_relationship_count": (
                len(
                    usfs_relationships
                )
            ),
            "usfs_relevance_counts": (
                dict(
                    relevance_counts
                )
            ),
            "usfs_reason_counts": (
                dict(
                    reason_counts
                )
            ),
            "recreation_fetch_success": (
                f"{recreation_fetch_success}/10"
            ),
            "recreation_parse_success": (
                f"{recreation_parse_success}/10"
            ),
            "nws_query_success": (
                f"{nws_query_success}/10"
            ),
            "nws_payload_success": (
                f"{nws_payload_success}/10"
            ),
            "nws_unique_active_source_events": (
                len(
                    nws_events
                )
            ),
            "nws_relationship_count": (
                len(
                    nws_relationships
                )
            ),
            "nws_event_conflict_count": (
                len(
                    nws_event_conflicts
                )
            ),
            "total_persisted_source_events": (
                len(
                    all_source_ids
                )
            ),
            "total_persisted_relationships": (
                len(
                    all_relationship_keys
                )
            ),
            "baseline_notification_count": (
                len(
                    persisted[
                        "baseline_notifications"
                    ]
                )
            ),
            "notification_ledger_count": (
                len(
                    persisted[
                        "notification_ledger"
                    ]
                )
            ),
            "baseline_bytes": (
                len(
                    persisted_raw
                )
            ),
            "baseline_sha256": (
                sha256_bytes(
                    persisted_raw
                )
            ),
        },
        "nws_site_queries": (
            nws_site_queries
        ),
        "recreation_pages": [
            {
                "campground": (
                    item[
                        "campground"
                    ]
                ),
                "http_status": (
                    item[
                        "http_status"
                    ]
                ),
                "article_found": (
                    item[
                        "article_found"
                    ]
                ),
                "quality": (
                    item[
                        "quality"
                    ]
                ),
                "semantic_fingerprint": (
                    item[
                        "semantic_fingerprint"
                    ]
                ),
            }
            for item
            in recreation_pages
        ],
        "nws_event_conflicts": (
            nws_event_conflicts
        ),
        "decision": decision,
        "next_scope_if_pass": (
            "PHASE 3B-4 — perform one immediate controlled "
            "second live observation against this persisted "
            "baseline and prove unchanged/repeated evidence "
            "does not produce duplicate notifications. "
            "The unattended multi-day cycle remains unauthorized "
            "until that controlled second-observation gate passes."
        ),
    }

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        "CampReady Phase 3B-3:",
        decision,
    )

    print(
        "Mapping gate:",
        mapping_gate,
    )

    print(
        "Classifier binding:",
        classifier_binding_gate,
    )

    print(
        "USFS alert index:",
        index_result[
            "status"
        ],
    )

    print(
        "USFS alerts:",
        alert_count,
    )

    print(
        "USFS detail fetch:",
        f"{alert_fetch_success}/"
        f"{alert_count}",
    )

    print(
        "USFS detail parse:",
        f"{alert_parse_success}/"
        f"{alert_count}",
    )

    print(
        "USFS relevance:",
        dict(
            relevance_counts
        ),
    )

    print(
        "Recreation fetch:",
        f"{recreation_fetch_success}/10",
    )

    print(
        "Recreation parse:",
        f"{recreation_parse_success}/10",
    )

    print(
        "NWS query:",
        f"{nws_query_success}/10",
    )

    print(
        "NWS valid payload:",
        f"{nws_payload_success}/10",
    )

    print(
        "NWS active source events:",
        len(
            nws_events
        ),
    )

    print(
        "Baseline notifications:",
        len(
            persisted[
                "baseline_notifications"
            ]
        ),
    )

    print(
        "Notification ledger:",
        len(
            persisted[
                "notification_ledger"
            ]
        ),
    )

    print(
        "Baseline SHA256:",
        sha256_bytes(
            persisted_raw
        ),
    )

    return rc


raise SystemExit(
    main()
)
