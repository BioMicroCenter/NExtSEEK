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

TWO CLASSES OF ARM ARE EXCLUDED RATHER THAN SCORED, and for one reason: in
neither case was the engine ever asked the question, so a `runtime_success=false`
row would teach the posterior that the engine failed at something it never
attempted.

* a provider OUTAGE -- every provider in the fallback chain returned 503, so the
  turn carries an error message where an answer should be and no product code
  ran (see `nessie_tests.outage`). Ten of the eighteen reds in the 2026-08-03
  seed-6 run were one such outage.
* a SKIPPED arm -- at full tier the only route to `skipped` is an unset
  `requires_env` (runner.py:128-133), which returns before `http_driver.drive`.

Excluded arms go to `excluded.csv` with their reason. They are never dropped in
silence: an unexplained gap between 127 pairs and the row count is the same
defect in a quieter form.

WHAT `artifacts_dir` IS. `collect.collect(manifest, out_dir, ...)` writes
`<out_dir>/artifacts/<variant>/<arm>/`, so `artifacts_dir` is that `artifacts`
directory -- not the collector's `out_dir`.
"""
from __future__ import annotations

import csv
import json
import os
import pathlib

from nessie_tests import corpus, outage

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

EXCLUDED_COLUMNS: tuple[str, ...] = ("query_id", "arm", "status", "reason")

ARMS = ("ns", "cc")

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

    `timed_out` is read off `reason` because no status encodes it: `run_case`
    records a timeout as `error` with the exception's text, and a timeout and a
    dead endpoint are different failures for the model.
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
#   CC  cc_assistant/translate.py:158 -- one per `tool_use` block in the stream.
#
# So the column counts ENGINE OPERATIONS, and reads the same way on both arms.
# Emitting 0 for NS instead would be false: NS really does issue API and graph
# calls, and a structural 0 on one arm is not a measurement, it is a claim the
# CC arm did all the work.
#
# `thinking` is excluded. translate.py:172-177 renders every CC narration block
# as a `search_started`/`search_complete` pair with `source: "thinking"`; it is
# not an operation, has no NS counterpart, and counting it would inflate one arm
# by a quantity the other cannot produce.
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


def artifact_count(artifacts_dir, variant_id: str, arm: str) -> int:
    """CC: the published artifact list. NS: FILES under run_root/files/."""
    base = _arm_dir(artifacts_dir, variant_id, arm)
    if base is None:
        return 0
    if arm == "cc":
        path = base / "artifacts.json"
        if not path.is_file():
            return 0
        return len(json.loads(path.read_text(encoding="utf-8")).get("artifacts") or [])
    files = base / "run_root" / "files"
    # `is_file()`, because `rglob("*")` yields directories too and one file in one
    # subdirectory would otherwise count as two artifacts.
    return sum(1 for p in files.rglob("*") if p.is_file()) if files.is_dir() else 0


def artifact_path(artifacts_dir, variant_id: str, arm: str) -> pathlib.Path | None:
    """Where an arm's deliverables actually are.

    CC: `cc_artifacts/`, NOT `cc_scratch/`. The collector writes both, and
    `cc_scratch/` is ALWAYS EMPTY -- `<scratch_mnt>/<run_id>` is created per turn
    but is not where the agent works: `working_dir` is `/home/user`
    (cc_engine.py:137), the whole per-USER scratch is mounted at `/data/scratch`
    (:128) and the plugin defaults there (_nextseek_runner.py:106), and
    `_publish_artifacts` diffs the per-user root (:595). A turn's published
    deliverable FILES exist only at `output/artifacts/<turn_id>` (:931), which is
    what the collector copies to `cc_artifacts/<task_id>/`. Pointing at
    `cc_scratch/` reads Missing (or, once the empty copy lands, a healthy-looking
    Indeterminate that means nothing) for every CC arm in the run.

    NS: `run_root/files/`, the per-session output dir `_ensure_query_log_dir`
    creates (orchestrator.py:337).
    """
    base = _arm_dir(artifacts_dir, variant_id, arm)
    if base is None:
        return None
    return base / "cc_artifacts" if arm == "cc" else base / "run_root" / "files"


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


def _exclusion(entry) -> str | None:
    """Why this arm must not be scored, or None if it must be."""
    if getattr(entry, "outage", False):
        return outage.OUTAGE_REASON
    if entry.status == "skipped":
        return _SKIPPED_REASON
    return None


def export(manifest, out_dir, artifacts_dir=None, corpus_path=None) -> dict:
    """The 14-column runtime rows, one file per arm, plus `excluded.csv`.

    `corpus_path` is accepted and unused: `hibayes_subtype` is recorded on the
    PAIR at run time, so this file needs no corpus lookup and cannot fail on a
    corpus that has since moved. Kept in the signature because the plan publishes
    it, and because `export_stage_b` alongside really does need it.

    Returned keys are counts: `ns`, `cc`, `excluded`, and `engine_ops_unobserved`
    -- how many emitted rows carry a `tool_calls_total` of 0 that means "not
    collected" rather than "no operations". The column cannot hold null, so that
    0 is unavoidable; leaving the reader to infer how many rows it applies to is
    not.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_arm: dict[str, list[dict]] = {"ns": [], "cc": []}
    excluded: list[dict] = []
    ops_unobserved = 0

    for pair in manifest.pairs:
        for arm in ARMS:
            entry = getattr(pair, arm)
            if entry is None:
                # A half-written pair: pairs are persisted as they complete so a
                # paid run can resume. An arm that has not run is not a failed one.
                continue
            reason = _exclusion(entry)
            if reason is not None:
                excluded.append({"query_id": pair.id, "arm": arm,
                                 "status": entry.status, "reason": reason})
                continue
            rows = _turn_rows(artifacts_dir, pair.id, arm)
            if not rows:
                ops_unobserved += 1
            by_arm[arm].append(runtime_row(
                entry, arm=arm, family=pair.family, subtype=pair.hibayes_subtype,
                artifact_count=artifact_count(artifacts_dir, pair.id, arm),
                engine_ops=engine_ops(rows)))

    for arm, rows in by_arm.items():
        _write(out_dir / f"hibayes_eval_rows_{arm}.csv", HIBAYES_CSV_COLUMNS, rows)
    _write(out_dir / "excluded.csv", EXCLUDED_COLUMNS, excluded)
    return {"ns": len(by_arm["ns"]), "cc": len(by_arm["cc"]),
            "excluded": len(excluded), "engine_ops_unobserved": ops_unobserved}


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
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for pair in manifest.pairs:
        meta = corpus.hibayes_meta(pair.id, corpus_path)
        for arm in ARMS:
            entry = getattr(pair, arm)
            # The SAME exclusions as the runtime export, through the same
            # predicate: a row Stage C grades but the model never sees is wasted
            # grading, and the reverse is a row the model scores ungraded.
            if entry is None or _exclusion(entry) is not None:
                continue
            answer_provided, is_error, timed_out = runtime_flags(entry)
            expected = bool(meta["artifact_expected"])
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
                "artifact_status": artifact_status(
                    expected=expected,
                    path=artifact_path(artifacts_dir, pair.id, arm)),
                "artifact_kind": meta["artifact_kind"],
                "declared_artifact_count": artifact_count(artifacts_dir, pair.id, arm),
                "expected_behavior": meta["expected_behavior"],
            })
    _write(out_dir / "hibayes_functional_eval_inputs.csv", CSV_HEADER_12, rows)
    return len(rows)
