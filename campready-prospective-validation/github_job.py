from __future__ import annotations

import json
import subprocess
import sys

from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(
    __file__
).resolve().parent

REPO_ROOT = (
    ROOT.parent
)

CONFIG_PATH = (
    ROOT / "config.json"
)

RUNNER = (
    ROOT / "prospective_run.py"
)

COMPLETE_SENTINEL = (
    ROOT
    / "validation-complete.json"
)


def main():
    config = json.loads(
        CONFIG_PATH.read_text(
            encoding="utf-8"
        )
    )

    policy = config[
        "policy"
    ]

    if (
        policy.get(
            "send_real_notifications"
        )
        is not False
    ):
        print(
            "HOLD_REAL_NOTIFICATIONS_NOT_DISABLED"
        )

        return 20

    end_at = datetime.fromisoformat(
        config[
            "validation_window"
        ][
            "end_at_utc"
        ]
    )

    now = datetime.now(
        timezone.utc
    )

    if now > end_at:
        if not COMPLETE_SENTINEL.exists():
            COMPLETE_SENTINEL.write_text(
                json.dumps(
                    {
                        "status": (
                            "VALIDATION_WINDOW_COMPLETE"
                        ),
                        "completed_at_utc": (
                            now.isoformat()
                        ),
                        "validation_end_at_utc": (
                            end_at.isoformat()
                        ),
                        "network_request_started": False,
                        "real_notifications_sent": 0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        print(
            "VALIDATION_WINDOW_COMPLETE"
        )

        return 0

    result = subprocess.run(
        [
            sys.executable,
            str(
                RUNNER
            ),
        ],
        cwd=str(
            REPO_ROOT
        ),
        check=False,
    )

    return result.returncode


raise SystemExit(
    main()
)
