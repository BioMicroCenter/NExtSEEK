#!/usr/bin/env python3
"""Ask one question about one cohort, live, and get the pipeline selection.

The batch runners answer a fixed list. This is the same machinery with the
scoring removed and a prompt loop added, for demonstrating the selection
judgement to a person who is typing.

## Why the digest is cached

Building a cohort's digest is the slow part: sample metadata over the API,
then every referenced protocol document downloaded and text-extracted. That is
several seconds, and it does not depend on the question. So it is built once
per cohort and cached to disk; the second and later questions about the same
samples return in about the time of one model call.

Delete .digest-cache/ to force a rebuild after the samples change.

## Why the cheap payload is the default

The full payload attaches ~100k tokens of nf-core documentation. Measured over
30 questions it buys no accuracy (24/30 without it, 23/30 with), and it is
roughly three times slower to send. `--full` restores it.

    # one shot
    docker compose exec -T nextseek uv run python \\
        /app/chat_nextseek/evals/ask_pipeline.py \\
        --cohort 240910LAU::A.GEX -q "Run a splicing analysis on these mice"

    # interactive: type questions, blank line to change cohort, Ctrl-D to quit
    docker compose exec -it nextseek uv run python \\
        /app/chat_nextseek/evals/ask_pipeline.py --cohort 240910LAU::A.GEX
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.sample_digest import DigestError
from chat_nextseek.pipeline.selection_context import SECTION_NAMES, build_selection_context

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR))
from demo_cohort_real import build_real_digest  # noqa: E402
from run_question_cases import SYSTEM_PROMPT, USER_TEMPLATE, _ask  # noqa: E402

GROUNDTRUTH = EVALS_DIR / "groundtruth_cohorts.json"
#: The team's five questions are about production samples that resolve on no
#: local instance, so their digests are pre-built on the host by
#: build_prod_digests.py and carried in. `--team` reads those rather than
#: trying to fetch samples that are not here.
TEAM_QUESTIONS = EVALS_DIR / "team_questions.json"
PROD_DIGESTS = EVALS_DIR / "prod_digests.json"
CACHE_DIR = EVALS_DIR / ".digest-cache"
SAMPLE_CAP = 10
CHEAP = ["atlas", "digest", "schemas"]


def cohort_uids(name: str) -> list[str]:
    """Accept `STUDY::ANALYSIS`, a bare study code, or a comma-separated UID list."""
    if "D.SEQ-" in name or "A." in name and "::" not in name and "," in name:
        return [u.strip() for u in name.split(",") if u.strip()]
    cohorts = json.loads(GROUNDTRUTH.read_text())
    if "::" in name:
        key = name
    else:
        matches = [c for c in cohorts if c["study"] == name]
        if not matches:
            raise SystemExit(f"no cohort for study {name!r}")
        key = f"{matches[0]['study']}::{matches[0]['analysis']}"
    for c in cohorts:
        if f"{c['study']}::{c['analysis']}" == key:
            return sorted(c["uids"])[:SAMPLE_CAP]
    raise SystemExit(f"no cohort {key!r}. Try --list.")


def load_team(qid: str, digests_path: Path) -> tuple[dict[str, Any], str, int]:
    """Return (digest, question text, n UIDs) for one team question."""
    entries = {e["id"]: e for e in json.loads(digests_path.read_text())}
    entry = entries.get(qid)
    if not entry or not entry.get("digest"):
        raise SystemExit(
            f"no pre-built digest for {qid!r} in {digests_path.name}. "
            f"Available: {', '.join(sorted(entries))}")
    questions = {q["id"]: q for q in json.loads(TEAM_QUESTIONS.read_text())}
    return entry["digest"], questions[qid]["question"], entry.get("n_uids_supplied", 0)


def list_team() -> None:
    for q in json.loads(TEAM_QUESTIONS.read_text()):
        print(f"  {q['id']:<34} {q['question'][:78]}")


def list_cohorts() -> None:
    for c in json.loads(GROUNDTRUTH.read_text()):
        print(f"  {c['study']}::{c['analysis']:<16} {c['seqtype']:<28} {len(c['uids'])} samples")


def load_digest(config, uids: list[str], refresh: bool) -> dict[str, Any]:
    CACHE_DIR.mkdir(exist_ok=True)
    key = hashlib.sha256("|".join(uids).encode()).hexdigest()[:16]
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    # The reporter and API layers print request/response debug to stdout. That
    # is useful in a batch run and unwatchable in a live demo, so it is captured
    # and only surfaced if the build fails.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            digest = build_real_digest(config, uids=uids)
    except Exception:
        sys.stderr.write(buf.getvalue())
        raise
    path.write_text(json.dumps(digest))
    return digest


def answer(client, model, budget, payload: str, question: str) -> None:
    started = time.time()
    out = _ask(client, model, budget, payload, question)
    elapsed = time.time() - started
    if out["parse_error"]:
        print(f"\n  could not parse the reply: {out['parse_error']}\n")
        return
    chosen = out["chosen"]
    bold, dim, off = ("\033[1m", "\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "", "")
    print()
    print(f"  {bold}{' + '.join(chosen) if chosen else 'no pipeline fits'}{off}")
    print(f"  {out['reason']}")
    print(f"  {dim}({elapsed:.1f}s){off}\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cohort", help="STUDY::ANALYSIS, a study code, or comma-separated UIDs")
    p.add_argument("-q", "--question", help="Ask one question and exit. Omit for a prompt loop.")
    p.add_argument("--full", action="store_true",
                   help="Send the nf-core documentation too (~3x the tokens, no measured "
                        "accuracy gain over 30 questions).")
    p.add_argument("--refresh", action="store_true", help="Rebuild the cached digest.")
    p.add_argument("--team", metavar="ID",
                   help="Ask about one of the team's five questions, using its pre-built "
                        "production digest. Without -q, the team's own wording is used.")
    p.add_argument("--digests", type=Path, default=PROD_DIGESTS,
                   help="Pre-built digests file for --team.")
    p.add_argument("--list", action="store_true", help="List available cohorts and exit.")
    p.add_argument("--list-team", action="store_true", help="List the team questions and exit.")
    p.add_argument("--agent", default="pipeline_agent")
    args = p.parse_args()

    if args.list:
        list_cohorts()
        return 0
    if args.list_team:
        list_team()
        return 0
    if not (args.cohort or args.team):
        p.error("give --cohort or --team (--list / --list-team to see them)")

    config = ChatConfig()
    client, model, budget = config.get_agent_model(args.agent)
    sections = None if args.full else CHEAP

    started = time.time()
    if args.team:
        digest, team_question, n_uids = load_team(args.team, args.digests)
        uids = []
        print(f"team question {args.team} — {n_uids} samples on production, model {model}")
        print("loading the pre-built digest…", end=" ", flush=True)
        if not args.question:
            args.question = team_question
    else:
        uids = cohort_uids(args.cohort)
        print(f"cohort {args.cohort} — {len(uids)} samples, model {model}")
        print("building the sample digest…", end=" ", flush=True)
        try:
            digest = load_digest(config, uids, args.refresh)
        except DigestError as exc:
            print(f"\nfailed: {exc}")
            return 1
    ctx = build_selection_context(config=None, uids=uids, digest=digest, max_tokens=10**12)
    payload = ctx.to_prompt_text(sections)
    size = ctx.size_report(sections)
    print(f"{time.time() - started:.1f}s — payload {size['est_tokens']:,} tokens "
          f"({'everything' if args.full else 'no documentation'})")

    if args.question:
        answer(client, model, budget, payload, args.question)
        return 0

    print("\nType a question. Blank line to quit.\n")
    while True:
        try:
            q = input("  ? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
        answer(client, model, budget, payload, q)


if __name__ == "__main__":
    raise SystemExit(main())
