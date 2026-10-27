"""Turn an accepted paid run into the committed replay fixtures.

  python -m NessieAI.tests.pipeline_scenarios.record <run dir> [--ids a,b]

Run on the host from the worktree root, on the commit the paid run was made from (its
fingerprint must equal the current one, or nothing is written). A story the runner failed on
(an `error` on the row, `tape: null`) is skipped. Outside the story (which is the committed story
itself), every URL that is not public archive or nf-core test data is rewritten to "<url>", as the
tape's already were; a fixture that still holds an email or an absolute path anywhere in its text
is refused.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from NessieAI.tests.pipeline_scenarios import fingerprint
from NessieAI.tests.pipeline_scenarios.scrub import for_tape, refusals_in

HERE = Path(__file__).resolve().parent
RECORDED = HERE / "recorded"
STORY_FIELDS = ("id", "family", "route", "acceptable", "must_not", "expect", "launch", "cohort", "question",
                "history", "replies")


def fixture_from_row(row: dict, fp: str) -> dict:
    """The committed form of one run row: the story fields the replay checks, and what was seen,
    with its URLs rewritten as a tape's are (the story is left as committed, so the replay can compare it)."""
    seen = for_tape({"decision": row["decision"], "outcome": row["outcome"], "score": row["score"],
                     "tape": row["tape"]})
    return {"story": {k: row["story"][k] for k in STORY_FIELDS}, "fingerprint": fp, **seen,
            "agent_model": row.get("agent_model"), "laya_mode": row.get("laya_mode")}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", type=Path)
    p.add_argument("--ids", default="")
    p.add_argument("--fingerprint", default=None, help="the run's fingerprint, if the run dir lacks one")
    a = p.parse_args(argv)
    now = fingerprint.current()
    summary = json.loads((a.run_dir / "summary.json").read_text())
    # The flag only vouches for an older run dir whose summary has no fingerprint of its own.
    run_fp = summary["fingerprint"] if "fingerprint" in summary else a.fingerprint
    if run_fp != now:
        print(f"refusing: the run was made under fingerprint {run_fp}, the tree is at {now}", file=sys.stderr)
        return 1
    if summary.get("posterior_routing") is not False:
        # The posterior selector reads the whole corpus file's sha, which the fingerprint (a projection
        # of it) does not cover; a run that used it, or did not say, cannot be replayed faithfully.
        print(f"refusing: posterior routing was {summary.get('posterior_routing')!r} for this run "
              "(it must be recorded False)", file=sys.stderr)
        return 1
    wanted = set(filter(None, a.ids.split(",")))
    fixtures: dict[str, dict] = {}
    for path in sorted((a.run_dir / "stories").glob("*.json")):
        row = json.loads(path.read_text())
        story_id = row["story"]["id"]
        if wanted and story_id not in wanted:
            continue
        if row["outcome"].get("error") or row.get("tape") is None:
            print(f"skip {story_id}: runner error")
            continue
        fixtures[story_id] = fixture_from_row(row, now)
    # Scrub every fixture before writing any, so a refusal leaves RECORDED as it was.
    for story_id, fixture in fixtures.items():
        found = refusals_in(fixture)
        if found:
            print(f"refusing {story_id}: {found[:3]}", file=sys.stderr)
            return 1
    for story_id, fixture in fixtures.items():
        RECORDED.mkdir(parents=True, exist_ok=True)
        (RECORDED / f"{story_id}.json").write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
        print(f"recorded {story_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
