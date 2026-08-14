#!/usr/bin/env python3
"""Plan 018 V4-3 DONE verifier CLI — raw-attempt replay without provider spend."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import orjson

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.v4_3_verifier import V13A_ZIP, run_verifier  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan 018 V4-3 judgment replay verifier")
    parser.add_argument("--zip", type=Path, default=V13A_ZIP, help="V13-A testquestions.zip path")
    parser.add_argument(
        "--sidecar",
        type=Path,
        default=REPO / "evidence" / "plan018-v4-3-verifier.sidecar.json",
        help="Write evidence sidecar JSON here",
    )
    args = parser.parse_args()

    report = run_verifier(zip_path=args.zip)
    sidecar = {
        "schema": "plan018-v4-3-verifier/v1",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": "PASS" if report.passed else "FAIL",
        "zip_path": str(args.zip),
        "route_execution": False,
        "set3_rerun": False,
        "paid_or_live_resources_used": False,
        "checks_passed": sum(1 for c in report.checks if c.get("pass")),
        "checks_total": len(report.checks),
        "errors": report.errors,
        "checks": report.checks,
        "v4_8_note": "Live three-call provider judgment requires separate V4-8 authorization",
    }
    args.sidecar.parent.mkdir(parents=True, exist_ok=True)
    args.sidecar.write_bytes(orjson.dumps(sidecar, option=orjson.OPT_INDENT_2))

    if report.passed:
        print(f"V4-3 verifier PASS ({sidecar['checks_passed']} checks)")
        return 0
    for err in report.errors:
        print(f"FAIL: {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
