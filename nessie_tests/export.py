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

THE 14 AND THE 12 ARE LOCKED, AND SOME OF WHAT A RUN OBSERVES DOES NOT FIT IN
THEM. `arm_diagnostics.csv` is the third sidecar, on the same principle as the
other two: it NAMES the rows rather than counting them, one row per arm, keyed
`(query_id, arm)` exactly as `excluded.csv` and `unobserved.csv` are, so all
three join to the locked files and to each other without a second key. It carries
two things the locked tuples have no column for and must not grow one:

* `error_text` -- the turn's own error message, verbatim. `advanced.bacteria_mtb`'s
  CC arm was terminated by an Anthropic Usage Policy trigger and the canned
  refusal is the single most diagnostic thing that run produced about it; it sat
  in `result.error` and in the `query_error` event and reached nothing at all.
  Recovering it is not new capture, it is reading what the collector already
  wrote.
* `stop_reason` / `stop_reason_status` -- how the CC transcript's last `assistant`
  record says the model stopped, and whether that is a MEASUREMENT.

EVERY arm gets a diagnostics row, INCLUDING the excluded ones. An excluded arm is
the one whose error text matters most, and whether a policy refusal ends up
excluded or scored is an open question -- so the evidence a future ruling would
be made on must not disappear the moment the ruling is made.

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

# One row per ARM -- every arm the manifest holds, scored or excluded. Two
# columns for the stop reason and ONE for the error, because the two absences are
# shaped differently: a `stop_reason` is a value whose existence is a separate
# question from its content (there may be no transcript to look in at all),
# whereas an error either happened or did not, so its own status IS its class.
# `ERROR_CLASSES` below documents that vocabulary, absences included.
ARM_DIAGNOSTIC_COLUMNS: tuple[str, ...] = (
    "query_id", "arm", "stop_reason", "stop_reason_status", "error_class",
    "error_text")

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


def runtime_flags(entry, rows=()) -> tuple[bool, bool, bool]:
    """`(answer_provided, is_error, timed_out)` for one arm.

    ONE derivation, because `answer_provided`, `runtime_success` and
    `failure_mode` all appear in BOTH locked tuples and a downstream join puts
    the two files side by side. Two definitions of one column name is exactly the
    drift the rest of this harness refuses.

    `rows` is this arm's COLLECTED turn rows (`_turn_rows`). It is what the
    endpoint itself recorded, and it outranks the manifest entry on the two facts
    the entry gets wrong -- see `_answered` and `_row_errored`. It defaults to
    `()` so a caller with no collected tree still gets today's status-only
    answer rather than a TypeError, and `export` passes the rows it has already
    read for `_exclusion`.

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
    treating `failed` as an answer WHEN THE ARM ALSO SAID SOMETHING.
    """
    rows = list(rows)
    return (_answered(entry, rows),
            entry.status == "error" or _row_errored(rows),
            "timeout" in (entry.reason or "").lower())


def _answered(entry, rows) -> bool:
    """Did this arm produce a reply?

    TWO conditions, and the second is the fix. `_ANSWERED` contains `failed`,
    which is the harness saying "the criteria did not hold" and says nothing at
    all about whether the engine spoke -- so an arm that produced NOTHING but
    landed `failed` (the Usage Policy refusal on `advanced.bacteria_mtb`'s CC arm
    is the case) reported `answer_provided=true`, `runtime_success=true`,
    `failure_mode=none`, and then went to a paid LLM judge as an empty string.
    A status in `_ANSWERED` is now necessary and not sufficient: the arm must
    also have said something.

    THE COLLECTED REPLY CAN REFUTE, IT CANNOT ESTABLISH. `not rows` means nothing
    was collected for this arm, which is not evidence that it stayed silent --
    the same rule, and the same reason, as `_is_unfinished`: an absence is not a
    positive fact, and reading it as one would stamp `answer_provided=false` on
    every arm of any export run without a collected tree, silently zeroing the
    study's outcome variable across a paid run. So an uncollected arm falls back
    to the status alone, and `_unobserved` records that it did, by row.

    An arm that WAS collected and whose last turn carried no reply is a
    measurement, and `false` is the honest reading of it.
    """
    if entry.status not in _ANSWERED:
        return False
    return bool(final_answer(rows)) if rows else True


# The task-row status that means the turn itself errored. This is the ENDPOINT's
# vocabulary (`QueryTask.status`), which is why it is checked against a collected
# row and not against a manifest entry; `collect._TERMINAL` is the same
# vocabulary and holds this value as its second member.
_ROW_ERROR = "error"


def _row_errored(rows) -> bool:
    """True when any collected turn row says the turn ERRORED.

    `is_error` used to read `entry.status == "error"` alone, and the manifest
    entry's status is the HARNESS's verdict, not the turn's: `run_case` records
    `error` when the harness itself raised, and evaluates criteria otherwise. A
    turn the endpoint terminated -- a Usage Policy refusal, a provider fault,
    anything that lands `QueryTask.status = "error"` -- comes back with failing
    criteria and is written `failed`. So the row on disk said `status: "error"`
    while the CSV said `is_error=false`.

    OR'd with the entry status rather than replacing it: an arm whose harness
    raised before any row existed (connection refused) has no collected row to
    read and is still an error.

    ANY turn, not the last. Same rule as `_deadline_aborted`, for the same
    reason: a follow-up asked after a turn that errored is not the engine's
    answer to the question the corpus posed.
    """
    return any((r.get("status") or "") == _ROW_ERROR for r in rows)


def runtime_row(entry, *, arm, family, subtype, artifact_count, engine_ops=0,
                rows=()) -> dict:
    answer_provided, is_error, timed_out = runtime_flags(entry, rows)
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


def reply_of(row) -> str:
    """The answer ONE collected turn produced, or `""` when it produced none.

    `result.reply` first because that is the endpoint's own final field; the
    `query_complete` event is the fallback for a row whose result never landed.

    ONE definition, and the report builder imports it rather than keeping a
    second. The human grades what this returns and Stage C grades what this
    returns, so two extractions would put one verdict pair on two different
    answers -- and the disagreement between the two graders IS the study's
    output, so noise introduced here is indistinguishable from the result.
    """
    result = row.get("result")
    if isinstance(result, dict) and result.get("reply"):
        return str(result["reply"])
    for ev in reversed(row.get("progress") or []):
        data = ev.get("data") or {}
        if isinstance(data, dict) and data.get("reply"):
            return str(data["reply"])
    return ""


def final_answer(rows) -> str:
    """The answer under grade: the LAST collected turn's reply.

    THE LAST TURN, not the last turn that happened to produce something. A
    `refine_and_recall` arm whose follow-up errored answered nothing, and
    substituting turn 1's reply presents an answer to a DIFFERENT question under
    the heading the grader is judging -- which biases both graders the same way,
    upward, in the 25-variant family the paired design keeps precisely because it
    is where the two engines differ most.

    RESIDUAL, stated because it is not covered: `collect` writes a row only for a
    turn whose task row joined, so if the FINAL turn's row is the one that failed
    to join, this returns turn n-1's reply against turn n's question. That gap is
    recorded by the collector in `collection.json`'s `missing` list; it is not
    recoverable from the artifact tree alone, because `turns.json` carries the
    task_id of each row it kept and no marker for the ones it did not.
    """
    return reply_of(rows[-1]) if rows else ""


# --- the error text, and what kind of error it was ---------------------------
# `advanced.bacteria_mtb`'s CC arm produced this and nothing else:
#
#   "API Error: Claude Code is unable to respond to this request, which appears
#    to violate our Usage Policy (https://www.anthropic.com/legal/aup). ..."
#
# It was in `result.error` and in the `query_error` event's `data.error`, and the
# exported row said `is_error=false, answer_provided=true, failure_mode=none`.
# Nothing downstream ever saw the message: the 14 columns have nowhere to put it,
# Stage B's `final_answer` is empty (correctly -- the refusal is not an answer and
# must never be graded as one), and the report page renders "No reply was recorded
# for this arm". Recovering it costs one read of a file the collector already
# wrote.

def error_of(row) -> str:
    """The error ONE collected turn recorded, or `""`.

    `result.error` first, for the same reason `reply_of` reads `result.reply`
    first: it is the endpoint's own final field. The `query_error` event is the
    fallback for a row whose result never landed -- and it is a real fallback,
    not a theoretical one, because `translate.py` emits the event and the result
    dict is written separately.
    """
    result = row.get("result")
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])
    for ev in reversed(row.get("progress") or []):
        if ev.get("event") != "query_error":
            continue
        data = ev.get("data") or {}
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    return ""


def final_error(rows) -> str:
    """The LAST error any of this arm's turns recorded, or `""`.

    The last rather than the first, so a multi-turn arm reports the failure that
    ended it. `_row_errored` condemns the arm on ANY errored turn, so an arm with
    a non-empty text here need not have errored on its final turn -- the text
    says which error, the flag says the arm had one.
    """
    for row in reversed(list(rows)):
        text = error_of(row)
        if text:
            return text
    return ""


# The vocabulary of `error_class`, absences INCLUDED, because an absent value
# must never read as a known one.
#
#   provider_outage  every provider in the fallback chain returned 503. Detected
#                    through `outage.is_provider_outage` and NOT by a copy of the
#                    marker -- `tests/test_evaluate.py` asserts `outage.py` is the
#                    only production module in `nessie_tests/*.py` holding it.
#   usage_policy     Claude Code refused the request under the Usage Policy. THIS
#                    IS A CLASSIFICATION AND NOT A RULING: whether such an arm
#                    should be excluded like an outage or scored like a failure is
#                    the operator's call and is deliberately NOT made here or
#                    anywhere else in this module. `_exclusion` is untouched.
#   timeout          the error text itself says the turn timed out.
#   unclassified     an error text this function has no rule for. A real error,
#                    a real message, and no claim about its kind.
#   none             rows WERE collected and none of them carried an error. A
#                    measurement.
#   unobserved       no rows were collected, so there was nothing to look in.
#                    NOT the same as `none` and never folded into it.
ERROR_OUTAGE = "provider_outage"
ERROR_USAGE_POLICY = "usage_policy"
ERROR_TIMEOUT = "timeout"
ERROR_UNCLASSIFIED = "unclassified"
ERROR_NONE = "none"
ERROR_UNOBSERVED = "unobserved"

ERROR_CLASSES: tuple[str, ...] = (
    ERROR_OUTAGE, ERROR_USAGE_POLICY, ERROR_TIMEOUT, ERROR_UNCLASSIFIED,
    ERROR_NONE, ERROR_UNOBSERVED)

# Two markers, either of which is decisive. The prose has been reworded before and
# will be again; the URL is the stable half, and a message carrying either is the
# same refusal. Matched case-insensitively against the ERROR text only, never
# against a reply, so a turn that merely discusses the usage policy is not at risk.
_USAGE_POLICY_MARKERS = ("usage policy", "/legal/aup")

_TIMEOUT_MARKERS = ("timeout", "timed out")


def classify_error(text: str) -> str:
    """One of `ERROR_CLASSES`, for an error text. `""` -> `ERROR_NONE`.

    Order is by CAUSE, the same principle `_exclusion` orders exclusions by. An
    outage is checked first because it is the one class with an independent
    detector the rest of the harness already trusts, and a 503 chain that reports
    itself with the word "timeout" in it is still an outage.
    """
    if not text:
        return ERROR_NONE
    if outage.is_provider_outage(text):
        return ERROR_OUTAGE
    low = text.lower()
    if any(m in low for m in _USAGE_POLICY_MARKERS):
        return ERROR_USAGE_POLICY
    if any(m in low for m in _TIMEOUT_MARKERS):
        return ERROR_TIMEOUT
    return ERROR_UNCLASSIFIED


# --- stop_reason --------------------------------------------------------------
# `collect` writes the CC session transcript to `<arm>/session.jsonl`
# (collect.py:587). Its `assistant` records are Claude's own API messages, so each
# carries `message.stop_reason`: `end_turn`, `tool_use`, `max_tokens`, `refusal`,
# `stop_sequence`. NS has no such thing at all.
#
# THE ARM THIS WHOLE CHANGE IS ABOUT HAS NO TRANSCRIPT. `advanced.bacteria_mtb`'s
# CC arm died before one was written, so it can supply no stop_reason -- and the
# only wrong answer here is one that turns that into a value.
SESSION_TRANSCRIPT_NAME = "session.jsonl"

# The vocabulary of `stop_reason_status`. `stop_reason` is EMPTY unless this says
# `observed`; the two columns exist rather than one because Anthropic owns the
# `stop_reason` vocabulary and may extend it, so a sentinel smuggled into that
# column could one day collide with a real value.
STOP_OBSERVED = "observed"
STOP_NO_TRANSCRIPT = "no_transcript"          # no session.jsonl for this arm
STOP_NOT_RECORDED = "not_recorded"            # a transcript, and no stop_reason in it
STOP_UNREADABLE = "unreadable"                # the file is there and would not open
STOP_NOT_APPLICABLE = "not_applicable"        # an NS arm; there is no transcript to have

STOP_REASON_STATUSES: tuple[str, ...] = (
    STOP_OBSERVED, STOP_NO_TRANSCRIPT, STOP_NOT_RECORDED, STOP_UNREADABLE,
    STOP_NOT_APPLICABLE)


def stop_reason(artifacts_dir, variant_id: str, arm: str) -> tuple[str, str]:
    """`(stop_reason, status)` for one arm. `status` is from `STOP_REASON_STATUSES`.

    THE LAST `assistant` RECORD'S, and read as a floor rather than as the turn's
    final word. `CCSessionTranscript` stores the session file as of each turn and
    the tail of a turn is not necessarily in it -- 11 of the 12 smoke-run CC arms
    end on `tool_use`, which is the transcript stopping short of the closing
    `end_turn`, not the model stopping mid-tool. So this says "the last stop
    Claude recorded in what we collected", and that is all it says.

    Streamed line by line: the store's own cap is 256MB (collect.py:106) and
    reading one of those into a list to take its tail is not worth the memory. A
    line that will not parse is SKIPPED rather than fatal -- a transcript
    truncated mid-line still has every complete record before it, and losing all
    of them to the last one would be the same absence-as-evidence mistake the
    rest of this module refuses.
    """
    if arm != "cc":
        # Not an absence to explain. NS never had a transcript to lose.
        return "", STOP_NOT_APPLICABLE
    base = _arm_dir(artifacts_dir, variant_id, arm)
    path = None if base is None else base / SESSION_TRANSCRIPT_NAME
    if path is None or not path.is_file():
        return "", STOP_NO_TRANSCRIPT
    found = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "assistant":
                    continue
                message = rec.get("message")
                if not isinstance(message, dict):
                    continue
                value = message.get("stop_reason")
                if isinstance(value, str) and value:
                    found = value
    except OSError:
        # "We could not look" is not "there was nothing there", the same
        # distinction `artifact_status` draws between Unreadable and Missing.
        return "", STOP_UNREADABLE
    return (found, STOP_OBSERVED) if found else ("", STOP_NOT_RECORDED)


def arm_diagnostic(artifacts_dir, variant_id: str, arm: str, rows) -> dict:
    """One `arm_diagnostics.csv` row: what this arm's turn recorded about ending.

    Written for EVERY arm, scored or excluded. The excluded ones are the arms
    whose error text matters most, and whether a `usage_policy` refusal should be
    excluded rather than scored is an open question -- the evidence for deciding
    it must not vanish the moment it is decided.
    """
    value, status = stop_reason(artifacts_dir, variant_id, arm)
    text = final_error(rows)
    return {
        "query_id": variant_id,
        "arm": arm,
        "stop_reason": value,
        "stop_reason_status": status,
        # `ERROR_UNOBSERVED`, not `ERROR_NONE`: with no collected row there was
        # nothing to look in, and "this arm had no error" would be a claim.
        "error_class": classify_error(text) if rows else ERROR_UNOBSERVED,
        "error_text": text,
    }


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

    POSITIVE EVIDENCE ONLY -- see `_is_unfinished`. Neither an absent `status`
    key nor an empty one is a claim that the turn is unfinished, and excluding on
    either would silently discard paid arms whenever a `Sources` returned a
    partial row.

    The RESIDUAL, stated because it is not covered: a turn the harness abandoned
    at 600s that then finished at 620s has a terminal row by collection time and
    is indistinguishable from a prompt success here. The row carries no timestamp
    the `Sources` contract guarantees, so there is nothing to compare against.
    Its `latency_seconds` is the harness's elapsed time and therefore a floor.
    """
    return any(_is_unfinished(r.get("status")) for r in rows)


def _is_unfinished(status) -> bool:
    """True only for a status that POSITIVELY says the turn had not finished.

    THE ABSENCE OF A STATUS COMES IN TWO SHAPES AND NEITHER IS EVIDENCE. `None`
    is the key missing from the row; `""` is a `Sources` that mapped a NULL
    status column straight through, which is the obvious MySQL shape and the one
    a real implementation reaches for first. Treating the second as non-terminal
    excluded 8 of 8 arms of a reproduced run: both eval CSVs and Stage B were
    written EMPTY, and every arm was stamped "the turn blew full_timeout_s"
    beside `status=passed`.

    A whitespace-only status is the same absence wearing a different hat, so it
    is read the same way. Anything else that is not terminal is real evidence:
    `pending`, `running`, or a status this harness has never heard of, which is
    a row that at least claims to be in some state.
    """
    if status is None:
        return False
    if isinstance(status, str) and not status.strip():
        return False
    return status not in _TERMINAL


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
        # `answer_provided` is a conjunction of the entry status and the collected
        # reply (see `_answered`), and with no collected row only the first half
        # was evaluated. The emitted value is the status's answer, which is the
        # fail-safe direction -- but it rests on a status that means "the criteria
        # ran", not "the engine spoke", and nobody looked at what was said.
        out.append({"query_id": pair_id, "arm": arm, "column": "answer_provided",
                    "emitted": emitted["answer_provided"],
                    "reason": "no collected task row for this arm, so no reply was "
                              "ever read: this value is the manifest status alone, "
                              "and runtime_success and failure_mode derive from it"})
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
    `engine_ops_unobserved`, `unobserved_cells`, `arm_diagnostics` and one
    `errors_<class>` per non-empty error class. The per-cause counts are not
    decoration -- if this project's history repeats, `excluded_deadline` IS the
    run's headline result rather than a footnote, and it must not have to be
    grepped out of a reason column to be seen. The error classes are there for
    the same reason: a run in which 12 CC arms were refused under the Usage
    Policy is a fact about the study, not a footnote in a text column.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_arm: dict[str, list[dict]] = {"ns": [], "cc": []}
    excluded: list[dict] = []
    unobserved: list[dict] = []
    diagnostics: list[dict] = []
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
            # BEFORE the exclusion `continue`, deliberately: an excluded arm still
            # gets its diagnostics row. See the module docstring.
            diagnostics.append(arm_diagnostic(artifacts_dir, pair.id, arm, rows))
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
                artifact_count=evidence["count"], engine_ops=engine_ops(rows),
                rows=rows)
            by_arm[arm].append(row)
            unobserved += _unobserved(pair.id, arm, rows, evidence, row)

    for arm, rows in by_arm.items():
        _write(out_dir / f"hibayes_eval_rows_{arm}.csv", HIBAYES_CSV_COLUMNS, rows)
    _write(out_dir / "excluded.csv", EXCLUDED_COLUMNS, excluded)
    _write(out_dir / "unobserved.csv", UNOBSERVED_COLUMNS, unobserved)
    _write(out_dir / "arm_diagnostics.csv", ARM_DIAGNOSTIC_COLUMNS, diagnostics)
    summary = {"ns": len(by_arm["ns"]), "cc": len(by_arm["cc"]),
               "excluded": len(excluded),
               "engine_ops_unobserved": ops_unobserved,
               "unobserved_cells": len(unobserved),
               "arm_diagnostics": len(diagnostics)}
    for cause, key in ((CAUSE_OUTAGE, "excluded_outage"),
                       (CAUSE_NEVER_EXECUTED, "excluded_never_executed"),
                       (CAUSE_DEADLINE, "excluded_deadline")):
        summary[key] = sum(1 for e in excluded if e["cause"] == cause)
    # Every class, `none` and `unobserved` included: a reader must be able to see
    # that a run's arms were accounted for rather than infer it from a subtotal.
    for klass in ERROR_CLASSES:
        summary[f"errors_{klass}"] = sum(1 for d in diagnostics
                                         if d["error_class"] == klass)
    return summary


def query_text(variant) -> str:
    """The question this variant asked, as the grader is shown it.

    Single-turn (97 of the 127 selected): the query verbatim. Multi-turn (30, of
    which 25 are `refine_and_recall`): one labelled line per turn, in order.

    The conversation and not just its last line, because that is what the human
    grader reads off the page -- the card renders every declared turn -- and the
    two graders must judge the same answer against the same question. A bare
    "now group those by genotype" handed to Stage C alone has nothing to resolve
    "those" against, and an LLM grader marking it unanswerable while the human
    reads the thread is a disagreement about the CSV, not about the engines.

    A variant with no declared turns yields `""`. That is REACHABLE, not
    impossible: `e2e.catalog.Variant` requires the `turns` KEY, not a non-empty
    list, so `Variant(family=..., id=..., name=..., turns=[])` validates --
    and `corpus.py:258,363` already treat `not v.turns` as a live case. No
    variant in the corpus is one today (the shortest declares 1 turn), but a
    variant that became one would be blank in BOTH text columns and defeat the
    "an empty `final_answer` means the arm produced no reply" guarantee
    `export_stage_b` documents. Left unhandled rather than raised because this
    function does not own corpus validation; recorded so the next reader knows
    which of the two it is.
    """
    turns = list(getattr(variant, "turns", ()) or ())
    if not turns:
        return ""
    if len(turns) == 1:
        return turns[0].query
    return "\n".join(f"turn {i + 1} ({t.label}): {t.query}"
                     for i, t in enumerate(turns))


def export_stage_b(manifest, out_dir, artifacts_dir=None, corpus_path=None,
                   stats=None) -> int:
    """The 12-column Stage C input. One file covering BOTH arms: Stage C grades an
    answer, and which engine produced it is not part of that judgement -- naming
    the engine in the grader's input is an invitation to grade the engine.

    `query_text` and `final_answer` ARE FILLED HERE, from the corpus this
    function already reads and the collected turn rows it already reads. They
    were left empty through Task 4 on the theory that the report builder would
    fill them; it never did, nothing else on the branch ever referenced either
    column, and Stage C therefore graded 254 blank answers. Those verdicts join
    cleanly and mean nothing: `functional_evaluator.py:216` maps `""` to `None`
    and the BAML template renders an empty `final_answer`, at up to 3 calls a row.

    `final_answer` is `final_answer(rows)` -- the LAST collected turn's reply,
    the same string `build_bayes_report` shows a human under "Final answer".
    `query_text` is `query_text(variant)`. Both graders read the same pair or the
    disagreement set measures this file instead of the engines.

    AN EMPTY `final_answer` NOW MEANS EXACTLY ONE THING: this arm's collected row
    carried no reply (or nothing was collected for it at all). It can no longer
    mean "the column was never wired up", and the two are told apart
    structurally rather than by trust -- `query_text` comes from the corpus,
    which RAISES on a variant it does not hold, so no row can be blank in both
    columns. `main` prints the count of reply-less rows, and `stats`, when a dict
    is passed, receives `{"rows": int, "no_reply": int}`; the return value stays
    the row count because that is what the caller and its tests read.

    `answer_provided` AND `final_answer` NOW AGREE on every collected arm, because
    `runtime_flags` is given the same `turn_rows` this function reads the answer
    from. They could not before: an arm that said nothing exported
    `answer_provided=true` beside an empty `final_answer` in one row of one file,
    which is a self-contradiction the grader was fed at up to 3 calls a row. The
    one case where they still differ is an arm with NO collected rows at all --
    there `final_answer` is empty because nothing was read and `answer_provided`
    falls back to the manifest status, which `export`'s `unobserved.csv` names by
    row.

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
    # Read ONCE, through the harness's own loader, and keyed by id the way
    # `hibayes_meta` keys its own lookup -- including retired definitions, because
    # a variant retired since the run was paid for still has an answer on disk and
    # `hibayes_meta` would still resolve its metadata.
    questions = {v.id: query_text(v)
                 for v in corpus.load_all_definitions(corpus_path)}
    rows = []
    no_reply = 0
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
            answer_provided, is_error, timed_out = runtime_flags(entry, turn_rows)
            expected = bool(meta["artifact_expected"])
            evidence = artifact_evidence(artifacts_dir, pair.id, arm)
            answer = final_answer(turn_rows)
            if not answer:
                no_reply += 1
            rows.append({
                "query_id": stage_b_query_id(pair.id, arm),
                "task_family": pair.family,
                # `questions[...]`, not `.get(...)`: a KeyError here is the same
                # manifest/corpus disagreement `hibayes_meta` raises on three
                # lines above, and it must not degrade into a blank question.
                "query_text": questions[pair.id],
                "final_answer": answer,
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
    if stats is not None:
        stats.update({"rows": len(rows), "no_reply": no_reply})
    return len(rows)


# --- the command SKILL.md names ----------------------------------------------
# This module was documented as `python -m nessie_tests.export --run <dir>` and had
# no entry point at all, so the documented step exited 0, printed nothing and
# wrote nothing -- and the failure surfaced four steps later, after the grading
# pass, as a FileNotFoundError out of `merge_grades`.

# ONE voice for the missing tree, shared with the report builder. This module
# warned in detail while the BUILDER -- step 3, immediately before a 254-arm
# human grading pass -- printed "8 arms 8 gradable" and said nothing at all.
# The deadline consequence reads the same wherever it is printed
# from, so it is written once and each caller adds only its own tail.
_DEADLINE_CONSEQUENCE = (
    "no arm can be excluded as a {cause}: the only evidence is a collected, "
    "still-non-terminal task row, so a turn that blew the deadline is "
    "indistinguishable here from one that answered.")

_COLLECTION_GAP = (
    "Run step 1 first: `python -m nessie_tests.collect --run <run>` (see "
    "nessie_tests/output-skill-bayesian/SKILL.md). Proceeding anyway is "
    "supported; reading the result as a measured run is not.")


def no_artifacts_warning(art, *, step: str, consequences=()) -> str:
    """The warning for an absent collected tree, in one voice for both callers."""
    lines = [f"WARNING: no {art} -- the collected artifact tree is absent, so "
             f"this {step} is DEGRADED:",
             "  * " + _DEADLINE_CONSEQUENCE.format(cause=CAUSE_DEADLINE)]
    lines += [f"  * {c}" for c in consequences]
    lines.append(_COLLECTION_GAP)
    return "\n".join(lines)


_EXPORT_CONSEQUENCES = (
    "every `tool_calls_total` and `artifact_count` is an absence rather than a "
    "measurement, and lands in unobserved.csv.",
    "every `final_answer` is empty, so Stage C grades 254 blank answers and "
    "returns verdicts that join cleanly and mean nothing.",
    "every `answer_provided` falls back to the manifest status alone -- the "
    "collected reply is what refutes it, and there is none to read -- so it also "
    "lands in unobserved.csv, and runtime_success and failure_mode derive from it.",
    "arm_diagnostics.csv carries no error text and no stop_reason for any arm: "
    "both are read out of the collected tree.",
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
        print(no_artifacts_warning(art, step="export",
                                   consequences=_EXPORT_CONSEQUENCES),
              file=sys.stderr)

    out_dir = pathlib.Path(args.out) if args.out else run
    summary = export(m, out_dir, artifacts_dir=art, corpus_path=args.corpus)
    stage_b_stats: dict = {}
    stage_b = export_stage_b(m, out_dir, artifacts_dir=art, corpus_path=args.corpus,
                             stats=stage_b_stats)

    print(f"{len(m.pairs)} pair(s) -> {out_dir}")
    print(f"  hibayes_eval_rows_ns.csv   {summary['ns']} row(s)")
    print(f"  hibayes_eval_rows_cc.csv   {summary['cc']} row(s)")
    # The blank count is on the same line as the row count, because a Stage C
    # input whose answers are missing costs the same to grade and is worth
    # nothing, and the operator's next command is the one that pays for it.
    print(f"  hibayes_functional_eval_inputs.csv  {stage_b} row(s), "
          f"{stage_b_stats['no_reply']} with NO answer to grade")
    # Never a bare total. `excluded_deadline` can be a run's headline result, and
    # it must not have to be grepped out of a reason column to be seen.
    print(f"  excluded.csv               {summary['excluded']} arm(s): " +
          ", ".join(f"{k.removeprefix('excluded_')} {summary[k]}"
                    for k in ("excluded_outage", "excluded_never_executed",
                              "excluded_deadline")))
    print(f"  unobserved.csv             {summary['unobserved_cells']} cell(s)")
    # Never a bare total here either. `errors_usage_policy` is a run-shaped fact
    # -- a refusal produced no answer at all -- and `errors_unobserved` says how
    # many arms this file could say nothing about, which is the difference between
    # "no errors" and "we did not look".
    print(f"  arm_diagnostics.csv        {summary['arm_diagnostics']} arm(s): " +
          ", ".join(f"{k} {summary[f'errors_{k}']}" for k in ERROR_CLASSES
                    if summary[f"errors_{k}"]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
