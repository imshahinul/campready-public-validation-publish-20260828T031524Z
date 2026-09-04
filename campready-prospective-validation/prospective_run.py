from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time

from datetime import (
    datetime,
    timezone,
)
from pathlib import Path


ROOT = Path(
    __file__
).resolve().parent

CONFIG_PATH = (
    ROOT / "config.json"
)


def sha(path):
    return hashlib.sha256(
        Path(path).read_bytes()
    ).hexdigest()


def load_json(path):
    return json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )


def append_jsonl(
    path,
    value,
):
    with Path(path).open(
        "a",
        encoding="utf-8",
    ) as fh:
        fh.write(
            json.dumps(
                value,
                ensure_ascii=False,
            )
            + "\n"
        )


def main():
    started_clock = (
        time.monotonic()
    )

    now = datetime.now(
        timezone.utc
    )

    run_id = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    config = load_json(
        CONFIG_PATH
    )

    end_at = (
        datetime.fromisoformat(
            config[
                "validation_window"
            ][
                "end_at_utc"
            ]
        )
    )

    if now > end_at:
        print(
            "VALIDATION_WINDOW_COMPLETE"
        )

        return 3

    paths = config[
        "paths"
    ]

    frozen_hashes = config[
        "frozen_hashes"
    ]

    frozen_bindings = {
        "mapping": (
            sha(
                paths[
                    "mapping"
                ]
            )
            == frozen_hashes[
                "mapping"
            ]
        ),
        "parser": (
            sha(
                paths[
                    "parser"
                ]
            )
            == frozen_hashes[
                "parser"
            ]
        ),
        "classifier": (
            sha(
                paths[
                    "classifier"
                ]
            )
            == frozen_hashes[
                "classifier"
            ]
        ),
        "capture_harness": (
            sha(
                paths[
                    "capture_harness"
                ]
            )
            == frozen_hashes[
                "capture_harness"
            ]
        ),
        "comparator": (
            sha(
                paths[
                    "comparator"
                ]
            )
            == frozen_hashes[
                "comparator"
            ]
        ),
    }

    if not all(
        frozen_bindings.values()
    ):
        summary = {
            "run_id": run_id,
            "started_at_utc": (
                now.isoformat()
            ),
            "status": (
                "HOLD_FROZEN_BINDING_DRIFT"
            ),
            "frozen_bindings": (
                frozen_bindings
            ),
            "state_advanced": False,
            "real_notifications_sent": 0,
        }

        append_jsonl(
            paths[
                "history_jsonl"
            ],
            summary,
        )

        print(
            json.dumps(
                summary,
                indent=2,
            )
        )

        return 4

    run_dir = (
        Path(
            paths[
                "runs_dir"
            ]
        )
        / run_id
    )

    run_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    current_state = Path(
        paths[
            "current_state"
        ]
    )

    current_report = Path(
        paths[
            "current_report"
        ]
    )

    prior_state_sha = sha(
        current_state
    )

    observation = (
        run_dir
        / "observation.json"
    )

    capture_report = (
        run_dir
        / "capture-report.json"
    )

    comparison = (
        run_dir
        / "comparison.json"
    )

    capture_stdout = (
        run_dir
        / "capture.stdout.txt"
    )

    capture_stderr = (
        run_dir
        / "capture.stderr.txt"
    )

    compare_stdout = (
        run_dir
        / "compare.stdout.txt"
    )

    compare_stderr = (
        run_dir
        / "compare.stderr.txt"
    )

    with capture_stdout.open(
        "wb"
    ) as out, capture_stderr.open(
        "wb"
    ) as err:
        capture = subprocess.run(
            [
                sys.executable,
                paths[
                    "capture_harness"
                ],
                paths[
                    "mapping"
                ],
                paths[
                    "parser"
                ],
                paths[
                    "classifier"
                ],
                str(
                    observation
                ),
                str(
                    capture_report
                ),
                frozen_hashes[
                    "classifier"
                ],
            ],
            stdout=out,
            stderr=err,
            check=False,
        )

    compare_returncode = None

    if (
        capture.returncode == 0
        and observation.is_file()
        and capture_report.is_file()
    ):
        with compare_stdout.open(
            "wb"
        ) as out, compare_stderr.open(
            "wb"
        ) as err:
            compare = subprocess.run(
                [
                    sys.executable,
                    paths[
                        "comparator"
                    ],
                    str(
                        current_state
                    ),
                    str(
                        observation
                    ),
                    str(
                        current_report
                    ),
                    str(
                        capture_report
                    ),
                    str(
                        comparison
                    ),
                ],
                stdout=out,
                stderr=err,
                check=False,
            )

        compare_returncode = (
            compare.returncode
        )

    comparison_data = {}

    if comparison.is_file():
        comparison_data = (
            load_json(
                comparison
            )
        )

    decision = (
        comparison_data.get(
            "decision"
        )
    )

    comparison_pass = (
        compare_returncode == 0
        and decision
        == "PASS_CONTROLLED_SECOND_OBSERVATION"
    )

    summary_data = (
        comparison_data.get(
            "summary",
            {}
        )
    )

    candidate_count = int(
        summary_data.get(
            "notification_candidate_count",
            0,
        )
        or 0
    )

    delta_count = int(
        summary_data.get(
            "source_delta_count",
            0,
        )
        or 0
    )

    review_required = bool(
        summary_data.get(
            "review_required",
            False,
        )
    )

    unsafe_count = int(
        summary_data.get(
            "unsafe_candidate_count",
            0,
        )
        or 0
    )

    state_advanced = False

    if comparison_pass:
        shutil.copy2(
            observation,
            current_state,
        )

        shutil.copy2(
            capture_report,
            current_report,
        )

        state_advanced = True

    new_state_sha = (
        sha(
            current_state
        )
    )

    if (
        comparison_pass
        and (
            review_required
            or candidate_count > 0
            or delta_count > 0
        )
    ):
        review_payload = {
            "run_id": run_id,
            "created_at_utc": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            "source_delta_count": (
                delta_count
            ),
            "notification_candidate_count": (
                candidate_count
            ),
            "unsafe_candidate_count": (
                unsafe_count
            ),
            "deltas": (
                comparison_data.get(
                    "deltas",
                    []
                )
            ),
            "notification_candidates": (
                comparison_data.get(
                    "notification_candidates",
                    []
                )
            ),
            "comparison_path": str(
                comparison
            ),
        }

        review_dir = Path(
            paths[
                "review_dir"
            ]
        )

        review_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        (
            review_dir
            / (
                run_id
                + ".json"
            )
        ).write_text(
            json.dumps(
                review_payload,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    finished = datetime.now(
        timezone.utc
    )

    runtime_seconds = round(
        time.monotonic()
        - started_clock,
        3,
    )

    if comparison_pass:
        status = "PASS"

    elif capture.returncode != 0:
        status = (
            "HOLD_CAPTURE_FAILURE"
        )

    else:
        status = (
            "HOLD_COMPARISON_FAILURE"
        )

    run_summary = {
        "run_id": run_id,
        "started_at_utc": (
            now.isoformat()
        ),
        "finished_at_utc": (
            finished.isoformat()
        ),
        "runtime_seconds": (
            runtime_seconds
        ),
        "status": status,
        "capture_exit_code": (
            capture.returncode
        ),
        "comparison_exit_code": (
            compare_returncode
        ),
        "comparison_decision": (
            decision
        ),
        "source_delta_count": (
            delta_count
        ),
        "delta_counts": (
            summary_data.get(
                "delta_counts",
                {}
            )
        ),
        "notification_candidate_count": (
            candidate_count
        ),
        "unsafe_candidate_count": (
            unsafe_count
        ),
        "review_required": (
            review_required
        ),
        "state_advanced": (
            state_advanced
        ),
        "prior_state_sha256": (
            prior_state_sha
        ),
        "current_state_sha256": (
            new_state_sha
        ),
        "real_notifications_sent": 0,
        "frozen_bindings": (
            frozen_bindings
        ),
        "run_directory": str(
            run_dir
        ),
    }

    append_jsonl(
        paths[
            "history_jsonl"
        ],
        run_summary,
    )

    (
        run_dir
        / "run-summary.json"
    ).write_text(
        json.dumps(
            run_summary,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            run_summary,
            indent=2,
        )
    )

    return (
        0
        if comparison_pass
        else 2
    )


raise SystemExit(
    main()
)
