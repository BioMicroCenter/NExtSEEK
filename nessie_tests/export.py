"""Paired manifest -> the locked HiBayes CSVs, one per arm.

COLUMN TUPLES ARE COPIED, NOT IMPORTED. They are locked in
dmac-assistant/tools/hibayes/exporter.py (HIBAYES_CSV_COLUMNS) and
tools/hibayes/functional_inputs.py (CSV_HEADER_12). `test_export.py` pins our
header against the copy below, which catches OUR drift and cannot catch
upstream's, because the repos are separate and nothing here reads dmac-assistant.
That limit is real; do not write a docstring that implies otherwise.

One file per arm because upstream's `_validate_consistency` check #6 requires
`is_opus` uniform within a file, and an NS turn is not Opus at all. The two
concatenate for the model. `query_id` is the SAME on both sides of a pair and
`image` is the discriminator -- that is what makes the design paired.

THREE CLASSES OF ARM ARE EXCLUDED RATHER THAN SCORED, because in none of them
did the engine produce an answer to the question it was asked, and a scored row
would put a verdict on something the run did not observe.

* `provider_outage` -- every provider in the fallback chain returned 503, so the
  turn carries an error message where an answer should be and no product code
  ran (see `nessie_tests.outage`). Ten of the eighteen reds in the 2026-08-03
  seed-6 run were one such outage.
* `never_executed` -- at full tier the only route to `skipped` is an unset
  `requires_env` (runner.py:128-133), which returns before `http_driver.drive`.
  The engine was never asked at all.
* `deadline_abort` -- the turn blew `full_timeout_s` and the collected row is
  still non-terminal. See `_deadline_aborted`; this is the one that would
  otherwise have exported as a SUCCESS.

Each carries its own `cause` and its own `reason`, and they are never folded into
one bucket: an outage means the provider chain died before the product ran, while
a deadline abort means the product ran and did not finish. Those have different
remedies and a triage that cannot tell them apart is worthless. `export` returns
a per-cause count and `excluded.csv` carries the `cause` column, so a run's
exclusions can be counted by kind from disk rather than inferred from a length.

Excluded arms are never dropped in silence: an unexplained gap between 127 pairs
and the row count is the same defect in a quieter form.

NOR IS AN UNOBSERVED CELL. `tool_calls_total` and `artifact_count` are
non-nullable ints, so an arm whose evidence was never collected emits a 0 that is
indistinguishable from a measured one. Those cells are listed by row in
`unobserved.csv` -- `excluded.csv` exists precisely because a scalar in a return
dict identifies nothing.

WHAT `artifacts_dir` IS. `collect.collect(manifest, out_dir, ...)` writes
`<out_dir>/artifacts/<variant>/<arm>/`, so `artifacts_dir` is that `artifacts`
directory -- not the collector's `out_dir`. It is DERIVED, by
`collect.artifacts_dir(run)`, and never spelled out here or in the report
builder: the two must read the same tree or they disagree about which arms are
gradable, and `merge_grades` then tells the operator to grade a row the page
gives them no controls for. `main` below passes it for exactly that reason, and
warns when the tree is absent rather than exporting a quietly under-excluded run.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys

from nessie_tests import bayes_manifest, collect, corpus, outage

# Imported, never restated. `collect` owns the task-row status vocabulary and
# `manifest` owns the statuses that mean the harness never issued a request; a
# second copy of either here is the duplication the outage rule forbids.
from nessie_tests.collect import _TERMINAL
from nessie_tests.manifest import _NEVER_EXECUTED

HIBAYES_CSV_COLUMNS: tuple[str, ...] = (
    "query_id", "task_family", "task_subtype", "image", "answer_provided",
    "is_error", "timed_out", "runtime_success", "failure_mode",
    "latency_seconds", "cost_usd", "tool_calls_total", "artifact_count", "is_opus",
)

CSV_HEADER_12: tuple[str, ...] = (
    "query_id", "task_family", "query_text", "final_answer", "answer_provided",
    "runtime_success", "failure_mode", "artifact_expected", "artifact_status",
    "artifact_kind", "declared_artifact_count", "expected_behavior",
)

# `cause` is a stable token, so exclusions are countable by KIND from disk.
# `reason` is the prose that explains one to a human; never group by it.
EXCLUDED_COLUMNS: tuple[str, ...] = ("query_id", "arm", "cause", "status", "reason")

# One row per CELL, not per arm: an arm can have a measured artifact_count and an
# unobserved tool_calls_total. `emitted` is the value that actually reached the
# CSV, so a reader can find it without recomputing anything.
UNOBSERVED_COLUMNS: tuple[str, ...] = ("query_id", "arm", "column", "emitted", "reason")

ARMS = ("ns", "cc")

CAUSE_OUTAGE = "provider_outage"
CAUSE_NEVER_EXECUTED = "never_executed"
CAUSE_DEADLINE = "deadline_abort"

# `image` carries the ARM. It is the discriminator the model conditions on.
ARM_IMAGE = {"ns": "nextseek_query", "cc": "container_cc"}
ARM_IS_OPUS = {"ns": 0, "cc": 1}

# Statuses in which the engine produced a reply. `skipped` is absent because it
# is excluded upstream of this, and `error` because it is the negative case.
# `no_assertions` IS here: the engine answered and the CORPUS had nothing
# observable to assert about it, which is a fact about the corpus rather than
# about the runtime. Whether the answer was any good is Stage C's judgement, not
# this column's.
_ANSWERED = ("passed", "failed", "xpass", "no_assertions")

# The `reason` an arm the harness never asked is recorded under. The phrase
# "never issued" is asserted by the test: it is the whole distinction from a
# scored failure.
_SKIPPED_REASON = (
    "skipped: the harness never issued a request for this arm (an unset "
    "requires_env returns before http_driver.drive), so nothing about the "
    "engine was observed"
)

# Deliberately shares no vocabulary with OUTAGE_REASON. The product RAN here.
_DEADLINE_REASON = (
    "deadline abort: the turn blew full_timeout_s and its collected task row is "
    "still non-terminal, so the engine never produced an answer. Unlike a "
    "provider outage the product did run -- it did not finish in time"
)


def failure_mode(*, answer_provided: bool, is_error: bool, timed_out: bool) -> str:
    """Priority: timeout > error > no_answer > none. Mirrors upstream DD-05."""
    if timed_out:
        return "timeout"
    if is_error:
        return "error"
    if not answer_provided:
        return "no_answer"
    return "none"


def runtime_flags(entry) -> tuple[bool, bool, bool]:
    """`(answer_provided, is_error, timed_out)` for one arm.

    ONE derivation, because `answer_provided`, `runtime_success` and
    `failure_mode` all appear in BOTH locked tuples and a downstream join puts
    the two files side by side. Two definitions of one column name is exactly the
    drift the rest of this harness refuses.

    `timed_out` is read off `reason` because no status encodes it. That catches
    exactly ONE kind of timeout: a socket-level `TimeoutError` out of `urlopen`,
    which `run_case` records as `error` with the exception's text.

    It does NOT catch the harness's own `full_timeout_s` deadline, and nothing
    here needs it to. `http_driver.drive` BREAKS on the deadline
    (http_driver.py:107-108) rather than raising, and `run_case` never reads
    `DriveResult.status`/`aborted_early`, so a turn still `pending` at 600s
    carries no exception, no "timeout" anywhere in `reason`, and lands `failed` --
    a status inside `_ANSWERED`. That arm is EXCLUDED before it reaches this
    function (see `_deadline_aborted`), which is why this function can go on
    treating `failed` as an answer.
    """
    return (entry.status in _ANSWERED,
            entry.status == "error",
            "timeout" in (entry.reason or "").lower())


def runtime_row(entry, *, arm, family, subtype, artifact_count, engine_ops=0) -> dict:
    answer_provided, is_error, timed_out = runtime_flags(entry)
    return {
        "query_id": entry.id,
        "task_family": family,
        "task_subtype": subtype,
        "image": ARM_IMAGE[arm],
        "answer_provided": answer_provided,
        "is_error": is_error,
        "timed_out": timed_out,
        "runtime_success": answer_provided and not is_error and not timed_out,
        "failure_mode": failure_mode(answer_provided=answer_provided,
                                     is_error=is_error, timed_out=timed_out),
        "latency_seconds": entry.elapsed_s,
        # `None` is NOT zero. Only container_cc emits total_cost_usd.
        "cost_usd": entry.cost,
        # int, not nullable. See `engine_ops`.
        "tool_calls_total": engine_ops,
        "artifact_count": artifact_count,
        "is_opus": ARM_IS_OPUS[arm],
    }


# --- the Stage B join key -----------------------------------------------------
# Stage B is ONE file covering both arms, so its rows need a key the arms cannot
# collide on -- unlike the 14-column files, which are split by arm and therefore
# keep the bare variant id. Task 4 joins Stage C's grades back onto the runtime
# rows through this pair of functions rather than re-deriving the separator.
STAGE_B_ID_SEP = "::"


def stage_b_query_id(pair_id: str, arm: str) -> str:
    return f"{pair_id}{STAGE_B_ID_SEP}{arm}"


def split_stage_b_query_id(query_id: str) -> tuple[str, str]:
    """`rsplit`, not `split`: nothing forbids a variant id containing the
    separator, and the ARM is the part we know the shape of."""
    pair_id, _sep, arm = query_id.rpartition(STAGE_B_ID_SEP)
    return pair_id, arm


# --- tool_calls_total ---------------------------------------------------------
# `search_started` is the one progress event BOTH engines emit per external
# operation, which is what lets a single column compare them:
#
#   NS  orchestrator.py:427 (`source: "neo4j"`, one per Cypher execution), :1176
#       (`source: "api"`, one per REST endpoint call) and :880 (`reporter`) --
#       all three on the LIVE `run_query` path, not only in legacy plan mode,
#       which adds agents/planner/execution.py:224,389 on top.
#   CC  cc_assistant/translate.py:156-165 -- one per `tool_use` block.
#
# So the column counts ENGINE OPERATIONS, and reads the same way on both arms.
# Emitting 0 for NS instead would be false: NS really does issue API and graph
# calls, and a structural 0 on one arm is not a measurement, it is a claim the
# CC arm did all the work.
#
# `thinking` is excluded. translate.py:168-177 renders every CC narration block
# as a `search_started`/`search_complete` pair with `source: "thinking"`; it is
# not an operation, has no NS counterpart, and counting it would inflate one arm
# by a quantity the other cannot produce.
#
# THE TWO ARMS SHARE A UNIT BUT NOT A POPULATION: one event is one external
# operation on both sides, but CC emits one for EVERY tool -- `Read`, `Bash`,
# `TodoWrite`, `Grep` -- while NS emits one only per Cypher, REST or reporter
# call. The counts are therefore comparable, not equivalent, and a ratio between
# them is a ratio of two different mixes of work rather than of the same work
# done more or less efficiently. Anyone deriving one needs to know that.
_NOT_AN_OPERATION = frozenset({"thinking"})


def engine_ops(rows) -> int:
    """Engine operations across an arm's turns. See the block comment above."""
    total = 0
    for row in rows:
        for event in (row.get("progress") or []):
            if event.get("event") != "search_started":
                continue
            if ((event.get("data") or {}).get("source")) in _NOT_AN_OPERATION:
                continue
            total += 1
    return total


def _arm_dir(artifacts_dir, variant_id: str, arm: str) -> pathlib.Path | None:
    if not artifacts_dir:
        return None
    return pathlib.Path(artifacts_dir) / variant_id / arm


def _turn_rows(artifacts_dir, variant_id: str, arm: str) -> list[dict]:
    """EVERY turn's task row, from the collected tree. `[]` when uncollected.

    `turns.json` is written only for a multi-turn arm and `task.json` holds the
    LAST turn alone, so reading `task.json` on its own silently drops turns 1..n-1
    of the 30 multi-turn variants -- 25 of them `refine_and_recall`, the family
    whose whole subject is the follow-up.
    """
    base = _arm_dir(artifacts_dir, variant_id, arm)
    if base is None:
        return []
    turns = base / "turns.json"
    if turns.is_file():
        return [t.get("row") or {} for t in json.loads(turns.read_text(encoding="utf-8"))]
    task = base / "task.json"
    if task.is_file():
        return [json.loads(task.read_text(encoding="utf-8"))]
    return []


def _deadline_aborted(rows) -> bool:
    """True when any of an arm's collected turn rows is EXPLICITLY non-terminal.

    This is the only visible trace of the harness's own `full_timeout_s` deadline.
    `http_driver.drive`'s poll loop has four exits and three of them are loud: a
    terminal status, `max_consecutive_poll_errors` (which RAISES, so `run_case`
    records `error`), and `route_decided` at route tier (a `--bayesian` run is
    full tier and the route_gate variants were dropped from the selection). The
    fourth, `if clock() >= deadline: break` (http_driver.py:107-108), returns the
    last non-terminal payload with no exception at all.

    Reading the COLLECTED row is sound in the direction that matters: a task
    status only moves forward, so a row still non-terminal at collection time was
    certainly non-terminal when the harness gave up on it.

    ONE turn is enough to condemn the arm. A case is up to three turns, and a
    follow-up asked after a turn that produced nothing is not the engine's answer
    to the question the corpus posed.

    POSITIVE EVIDENCE ONLY -- an absent `status` key is not a claim that the turn
    is unfinished, and excluding on it would silently discard paid arms whenever a
    `Sources` returned a partial row.

    The RESIDUAL, stated because it is not covered: a turn the harness abandoned
    at 600s that then finished at 620s has a terminal row by collection time and
    is indistinguishable from a prompt success here. The row carries no timestamp
    the `Sources` contract guarantees, so there is nothing to compare against.
    Its `latency_seconds` is the harness's elapsed time and therefore a floor.
    """
    return any(r.get("status") is not None and r.get("status") not in _TERMINAL
               for r in rows)


def _ns_roots(base: pathlib.Path) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """`(own run_root* directories, run_root*.shared.json pointers)`.

    `collect.py:415-431` names an arm's FIRST root `run_root/` and any further
    one `run_root_2/`, `run_root_3/`... -- and when a root is already owned by
    another arm it writes NO directory at all, only a `<name>.shared.json`
    pointer. Reading `run_root/` alone therefore exports `artifact_count=0` and
    `Missing` for an arm that genuinely produced files.
    """
    if not base.is_dir():
        return [], []
    dirs = sorted(p for p in base.glob("run_root*") if p.is_dir())
    shared = sorted(base.glob("run_root*.shared.json"))
    return dirs, shared


def artifact_evidence(artifacts_dir, variant_id: str, arm: str) -> dict:
    """What the collected tree says about this arm's deliverables.

    `{"count": int, "paths": [Path], "observed": bool, "reason": str}` --
    `observed` False means the `count` is not a measurement and belongs in
    `unobserved.csv` rather than being passed off as a zero.

    CC reads `cc_artifacts/`, NOT `cc_scratch/`. The collector writes both, and
    `cc_scratch/` is ALWAYS EMPTY -- `<scratch_mnt>/<run_id>` is created per turn
    but is not where the agent works: `working_dir` is `/home/user`
    (cc_engine.py:137), the whole per-USER scratch is mounted at `/data/scratch`
    (:128) and the plugin defaults there (_nextseek_runner.py:106), and
    `_publish_artifacts` diffs the per-user root (:595). A turn's published
    deliverable FILES exist only at `output/artifacts/<turn_id>` (:931), which is
    what the collector copies to `cc_artifacts/<task_id>/`. Pointing at
    `cc_scratch/` reads Missing (or, once the empty copy lands, a healthy-looking
    Indeterminate that means nothing) for every CC arm in the run.

    NS reads every `run_root*/files/` -- the per-session output dir
    `_ensure_query_log_dir` creates (orchestrator.py:337), across every root the
    collector wrote.
    """
    base = _arm_dir(artifacts_dir, variant_id, arm)
    if base is None:
        return {"count": 0, "paths": [], "shared": 0, "observed": False,
                "reason": "no artifacts_dir was given, so nothing was collected"}
    if arm == "cc":
        # TWO sources, deliberately not conflated. The COUNT is the published
        # artifact list off `query_complete`; the STATUS is the deliverable FILES
        # the collector copied. Either can be present without the other -- the
        # list is written whenever a task row joined (collect.py:445), the tree
        # whenever the copy landed (:492) -- and reading the count's absence as
        # the files' absence reports `Missing` over a directory full of them.
        paths = [base / "cc_artifacts"]
        listing = base / "artifacts.json"
        if not listing.is_file():
            return {"count": 0, "paths": paths, "shared": 0, "observed": False,
                    "reason": "no artifacts.json for this arm: the collector "
                              "joined no task row to it, so the published-artifact "
                              "list was never written"}
        published = json.loads(listing.read_text(encoding="utf-8")).get("artifacts") or []
        return {"count": len(published), "paths": paths,
                "shared": 0, "observed": True, "reason": ""}

    dirs, shared = _ns_roots(base)
    paths = [d / "files" for d in dirs]
    # `is_file()`, because `rglob("*")` yields directories too and one file in one
    # subdirectory would otherwise count as two artifacts.
    count = sum(1 for p in paths if p.is_dir()
                for q in p.rglob("*") if q.is_file())
    if shared:
        # The collector records WHICH arm owns the copy (`copied_under`) but not
        # which of that arm's `run_root*` slots holds it, so this arm's share is
        # not recoverable from disk. Recorded as unobserved rather than guessed
        # at -- and rather than reported as a confident partial count.
        return {"count": count, "paths": paths, "shared": len(shared),
                "observed": False,
                "reason": f"{len(shared)} run_root(s) are shared with another arm "
                          f"and copied under it; the collector records the owning "
                          f"arm but not which of its run_root* slots holds the "
                          f"tree, so this count is a floor"}
    if not dirs:
        return {"count": 0, "paths": [], "shared": 0, "observed": False,
                "reason": "no run_root* directory for this arm: no ns_run_root "
                          "event was collected, or the copy did not land"}
    return {"count": count, "paths": paths, "shared": 0, "observed": True,
            "reason": ""}


def artifact_count(artifacts_dir, variant_id: str, arm: str) -> int:
    """CC: the published artifact list. NS: FILES under every run_root*/files/."""
    return artifact_evidence(artifacts_dir, variant_id, arm)["count"]


# Presence and readability ONLY. Schema validation (the GEO xlsx template parity
# dmac-assistant's artifact_validator.py does) is deferred, and anything it would
# have judged is emitted Indeterminate rather than guessed at.
def artifact_status(*, expected: bool, path: pathlib.Path | None) -> str:
    """`NotExpected` | `Missing` | `Unreadable` | `Indeterminate`.

    An EMPTY directory is `Missing`, not `Indeterminate`. Both arms create their
    directory unconditionally -- `_ensure_query_log_dir` mkdirs `<run_root>/files`
    on every NS turn whether or not anything is written into it, and the collector
    copies an empty `cc_scratch` just as happily as a full one. Reading the
    presence of such a directory as the presence of an artifact produces a
    healthy-looking count that means nothing, which is worse than a Missing.
    """
    if not expected:
        return "NotExpected"
    if path is None or not path.exists():
        return "Missing"
    try:
        if path.is_file():
            with path.open("rb") as fh:
                empty = not fh.read(1)
        else:
            empty = not _holds_a_file(path)
    except OSError:
        return "Unreadable"
    return "Missing" if empty else "Indeterminate"


def _holds_a_file(root: pathlib.Path) -> bool:
    """True if any non-empty file exists under `root`. Propagates OSError.

    `os.scandir` and NOT `Path.rglob`: rglob swallows the OSError it hits while
    walking, so an unreadable directory yields nothing and reads as EMPTY -- the
    difference between "the engine produced no artifact" and "we were not allowed
    to look", reported as the first. Caught by
    `test_an_unreadable_artifact_is_Unreadable_not_Missing`.

    Symlinks are followed for neither test, so a dangling one is neither a
    directory to descend nor a file to stat and simply does not count.
    """
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(pathlib.Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and entry.stat().st_size:
                    return True
    return False


def artifact_status_across(*, expected: bool, evidence: dict) -> str:
    """`artifact_status` reduced over every root the collector wrote for an arm.

    Any one path holding a file makes the arm's artifact present, so
    `Indeterminate` wins; `Unreadable` beats `Missing`, because "we could not
    look" must not be reported as "nothing was produced".

    A SHARED root with no own files is `Indeterminate`, not `Missing`. The tree
    exists -- it is filed under the arm that copied it first (`collect.py:430`) --
    and this function does not judge contents anyway, so `Missing` would be a
    false negative about an artifact that is sitting on disk.
    """
    if not expected:
        return "NotExpected"
    seen = {artifact_status(expected=True, path=p) for p in evidence["paths"]}
    if "Indeterminate" in seen:
        return "Indeterminate"
    if "Unreadable" in seen:
        return "Unreadable"
    # A shared root is evidence that EXISTS, just not under this arm.
    return "Indeterminate" if evidence["shared"] else "Missing"


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write(path, columns, rows) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns))
        w.writeheader()
        for r in rows:
            w.writerow({c: _fmt(r.get(c)) for c in columns})


def _exclusion(entry, rows) -> tuple[str, str] | None:
    """`(cause, reason)` this arm must not be scored under, or None to score it.

    ONE predicate, called by both exports, so a row the model never scores can
    never reach the grader and vice versa.

    Order is by CAUSE, not by symptom. An outage that then sat pending is both an
    outage and a deadline abort, and the outage is why: reporting the symptom
    would hide a provider incident behind a latency story.
    """
    if getattr(entry, "outage", False):
        return CAUSE_OUTAGE, outage.OUTAGE_REASON
    if entry.status in _NEVER_EXECUTED:
        return CAUSE_NEVER_EXECUTED, _SKIPPED_REASON
    if _deadline_aborted(rows):
        return CAUSE_DEADLINE, _DEADLINE_REASON
    return None


def _unobserved(pair_id, arm, rows, evidence, emitted) -> list[dict]:
    """The cells in this arm's row whose value is an absence, not a measurement."""
    out = []
    if not rows:
        out.append({"query_id": pair_id, "arm": arm, "column": "tool_calls_total",
                    "emitted": emitted["tool_calls_total"],
                    "reason": "no collected task row for this arm, so no progress "
                              "stream to count engine operations from"})
    if not evidence["observed"]:
        out.append({"query_id": pair_id, "arm": arm, "column": "artifact_count",
                    "emitted": emitted["artifact_count"], "reason": evidence["reason"]})
    return out


def export(manifest, out_dir, artifacts_dir=None, corpus_path=None) -> dict:
    """The 14-column runtime rows, one file per arm, plus `excluded.csv`.

    `corpus_path` is accepted and unused: `hibayes_subtype` is recorded on the
    PAIR at run time, so this file needs no corpus lookup and cannot fail on a
    corpus that has since moved. Kept in the signature because the plan publishes
    it, and because `export_stage_b` alongside really does need it.

    Returned counts: `ns`, `cc`, `excluded`, one `excluded_<cause>` per cause,
    `engine_ops_unobserved` and `unobserved_cells`. The per-cause counts are not
    decoration -- if this project's history repeats, `excluded_deadline` IS the
    run's headline result rather than a footnote, and it must not have to be
    grepped out of a reason column to be seen.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_arm: dict[str, list[dict]] = {"ns": [], "cc": []}
    excluded: list[dict] = []
    unobserved: list[dict] = []
    ops_unobserved = 0

    for pair in manifest.pairs:
        for arm in ARMS:
            entry = getattr(pair, arm)
            if entry is None:
                # A half-written pair: pairs are persisted as they complete so a
                # paid run can resume. An arm that has not run is not a failed one.
                continue
            # Read BEFORE the exclusion test: the collected rows are what a
            # deadline abort is visible in.
            rows = _turn_rows(artifacts_dir, pair.id, arm)
            verdict = _exclusion(entry, rows)
            if verdict is not None:
                cause, reason = verdict
                excluded.append({"query_id": pair.id, "arm": arm, "cause": cause,
                                 "status": entry.status, "reason": reason})
                continue
            if not rows:
                ops_unobserved += 1
            evidence = artifact_evidence(artifacts_dir, pair.id, arm)
            row = runtime_row(
                entry, arm=arm, family=pair.family, subtype=pair.hibayes_subtype,
                artifact_count=evidence["count"], engine_ops=engine_ops(rows))
            by_arm[arm].append(row)
            unobserved += _unobserved(pair.id, arm, rows, evidence, row)

    for arm, rows in by_arm.items():
        _write(out_dir / f"hibayes_eval_rows_{arm}.csv", HIBAYES_CSV_COLUMNS, rows)
    _write(out_dir / "excluded.csv", EXCLUDED_COLUMNS, excluded)
    _write(out_dir / "unobserved.csv", UNOBSERVED_COLUMNS, unobserved)
    summary = {"ns": len(by_arm["ns"]), "cc": len(by_arm["cc"]),
               "excluded": len(excluded),
               "engine_ops_unobserved": ops_unobserved,
               "unobserved_cells": len(unobserved)}
    for cause, key in ((CAUSE_OUTAGE, "excluded_outage"),
                       (CAUSE_NEVER_EXECUTED, "excluded_never_executed"),
                       (CAUSE_DEADLINE, "excluded_deadline")):
        summary[key] = sum(1 for e in excluded if e["cause"] == cause)
    return summary


def export_stage_b(manifest, out_dir, artifacts_dir=None, corpus_path=None) -> int:
    """The 12-column Stage C input. One file covering BOTH arms: Stage C grades an
    answer, and which engine produced it is not part of that judgement -- naming
    the engine in the grader's input is an invitation to grade the engine.

    `query_text` and `final_answer` are filled from the collected `task.json` in
    Task 4's report builder, which is the only place that has both the corpus and
    the collection in hand. Leaving them empty here is a staged build, not a
    placeholder; `test_stage_b_leaves_the_two_text_columns_for_task_4` pins that
    and is the test to replace once the wiring exists.

    A variant in the manifest but not in the corpus RAISES. That is a
    disagreement between the run's record and the corpus, which makes the
    metadata suspect for every row rather than for one, and the alternative
    (defaulting `artifact_expected` to false) would emit `NotExpected` -- a
    positive claim about an artifact nobody looked for.

    No `unobserved.csv` of its own: `declared_artifact_count` reads the same
    evidence as the runtime export's `artifact_count`, so the file `export`
    writes already names every row whose count here is an absence.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for pair in manifest.pairs:
        meta = corpus.hibayes_meta(pair.id, corpus_path)
        for arm in ARMS:
            entry = getattr(pair, arm)
            if entry is None:
                continue
            # The SAME exclusions as the runtime export, through the same
            # predicate and the same evidence: a row Stage C grades but the model
            # never sees is wasted grading, and the reverse is a row the model
            # scores ungraded.
            turn_rows = _turn_rows(artifacts_dir, pair.id, arm)
            if _exclusion(entry, turn_rows) is not None:
                continue
            answer_provided, is_error, timed_out = runtime_flags(entry)
            expected = bool(meta["artifact_expected"])
            evidence = artifact_evidence(artifacts_dir, pair.id, arm)
            rows.append({
                "query_id": stage_b_query_id(pair.id, arm),
                "task_family": pair.family,
                "query_text": "",
                "final_answer": "",
                "answer_provided": answer_provided,
                "runtime_success": answer_provided and not is_error and not timed_out,
                "failure_mode": failure_mode(answer_provided=answer_provided,
                                             is_error=is_error, timed_out=timed_out),
                "artifact_expected": expected,
                "artifact_status": artifact_status_across(
                    expected=expected, evidence=evidence),
                "artifact_kind": meta["artifact_kind"],
                "declared_artifact_count": evidence["count"],
                "expected_behavior": meta["expected_behavior"],
            })
    _write(out_dir / "hibayes_functional_eval_inputs.csv", CSV_HEADER_12, rows)
    return len(rows)


# --- the command SKILL.md names ----------------------------------------------
# This module was documented as `python -m nessie_tests.export --run <dir>` and had
# no entry point at all, so the documented step exited 0, printed nothing and
# wrote nothing -- and the failure surfaced four steps later, after the grading
# pass, as a FileNotFoundError out of `merge_grades`.

_NO_ARTIFACTS_WARNING = (
    "WARNING: no {art} -- the collected artifact tree is absent, so this export "
    "is DEGRADED:\n"
    "  * no arm can be excluded as a {cause}: the only evidence is a collected, "
    "still-non-terminal task row, so a turn that blew the deadline exports as a "
    "SUCCESS.\n"
    "  * every `tool_calls_total` and `artifact_count` is an absence rather than "
    "a measurement, and lands in unobserved.csv.\n"
    "Collection is not runnable yet (see nessie_tests/output-skill-bayesian/"
    "SKILL.md step 1 and `python -m nessie_tests.collect`). Exporting anyway is "
    "supported; reading the result as a measured run is not."
)


def main(argv=None) -> int:
    """Write every CSV for one paired run directory. Returns a process exit code.

    `artifacts_dir` is DERIVED from the run through `collect.artifacts_dir`, the
    same function the report builder uses, so the CSVs and the page can never
    disagree about which arms `_exclusion` dropped.
    """
    ap = argparse.ArgumentParser(
        description="Export a paired (--bayesian) run to the HiBayes CSVs.")
    ap.add_argument("--run", required=True,
                    help=f"the paired run directory: the one holding "
                         f"{bayes_manifest.MANIFEST_NAME} and "
                         f"{collect.ARTIFACTS_DIRNAME}/")
    ap.add_argument("--out", default=None,
                    help="where the CSVs go; default is the run directory itself")
    ap.add_argument("--corpus", default=None,
                    help="default nessie_tests/corpus.json. Read by the Stage B "
                         "export only, which RAISES on a variant the corpus no "
                         "longer holds")
    args = ap.parse_args(argv)

    run = pathlib.Path(args.run)
    m = bayes_manifest.read_bayes_manifest(run)
    if m is None:
        # The same collision the report builder refuses: a normal run's
        # `manifest.json` is a different schema that validates as an EMPTY paired
        # manifest, so reading it here would write four empty CSVs and call the
        # run exported.
        extra = (f"  ({run / 'manifest.json'} exists -- that is a NORMAL run's "
                 f"manifest and a DIFFERENT schema: it validates as an EMPTY "
                 f"paired manifest rather than raising, so reading it here would "
                 f"export a run of nothing.)\n"
                 if (run / "manifest.json").is_file() else "")
        print(f"no {bayes_manifest.MANIFEST_NAME} in {run}\n{extra}"
              f"Run the suite with --bayesian first.", file=sys.stderr)
        return 2
    if not m.pairs:
        print(f"{run / bayes_manifest.MANIFEST_NAME} records no pairs",
              file=sys.stderr)
        return 2

    art = collect.artifacts_dir(run)
    if not art.is_dir():
        print(_NO_ARTIFACTS_WARNING.format(art=art, cause=CAUSE_DEADLINE),
              file=sys.stderr)

    out_dir = pathlib.Path(args.out) if args.out else run
    summary = export(m, out_dir, artifacts_dir=art, corpus_path=args.corpus)
    stage_b = export_stage_b(m, out_dir, artifacts_dir=art, corpus_path=args.corpus)

    print(f"{len(m.pairs)} pair(s) -> {out_dir}")
    print(f"  hibayes_eval_rows_ns.csv   {summary['ns']} row(s)")
    print(f"  hibayes_eval_rows_cc.csv   {summary['cc']} row(s)")
    print(f"  hibayes_functional_eval_inputs.csv  {stage_b} row(s)")
    # Never a bare total. `excluded_deadline` can be a run's headline result, and
    # it must not have to be grepped out of a reason column to be seen.
    print(f"  excluded.csv               {summary['excluded']} arm(s): " +
          ", ".join(f"{k.removeprefix('excluded_')} {summary[k]}"
                    for k in ("excluded_outage", "excluded_never_executed",
                              "excluded_deadline")))
    print(f"  unobserved.csv             {summary['unobserved_cells']} cell(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
