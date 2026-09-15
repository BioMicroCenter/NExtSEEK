"""Prove the endpoint is the one this run needs, before it spends anything.

TWO things are checked on ONE probe turn, and both are unrecoverable after the
fact:

1.  `force_route` is honoured. `_decide_route` (nextseek_api/services/
    cc_assistant.py:245-251) drops a non-admin's `force_route` and falls back to
    the router. Nothing in the response says so. A whole 300-turn run would
    complete, cost real money, and measure the router instead of the engines.

2.  The turn emits `ns_run_root`. That event is a PRODUCT change made on this
    branch, and a running container serves a BAKED image, so an operator who
    pulled the branch and did not `./startup.sh rebuild` gets an endpoint with
    no such event. The run completes, looks healthy, and every one of the 127 NS
    arms loses the only key that joins its task_id to the engine's output
    directory -- discoverable at collection, hours and tens of dollars later,
    and fixable only by paying for the run again.

Both raise before a paired arm is billed. Neither is folded into the other: the
remedies are "run as a staff account" and "rebuild the image", and telling an
operator the wrong one sends them somewhere that cannot help.

A forced-arm run (`runner.run_arms`, the graph_search Nessie POC) adds a third
check, `assert_parser_force_works`: one full forced turn per arm proves the
evaluation switch (`force_parser_mode`) landed. The server drops that field
silently too, and a run whose switch did not land compares the parser with
itself.
"""
from __future__ import annotations

import time

from NessieAI.tests.nessie_tests import collect, http_driver, outage
from NessieAI.tests.nessie_tests import route_observer as ro

# Imported, never restated: `collect` owns the task-row status vocabulary and a
# second copy here is the drift the rest of this harness refuses.
from NessieAI.tests.nessie_tests.collect import _TERMINAL

PROBE_QUERY = "What is the weather in Boston tomorrow?"

# The event Task 1 added to the CC turn (now `NessieAI/cc/turn.py`). Named once;
# `collect._run_roots` reads the same string off the collected rows.
NS_RUN_ROOT_EVENT = "ns_run_root"

# The probe turn is a real NS turn and has to finish before the event can exist,
# so its ceiling is the run's own per-turn ceiling. This is only the DEFAULT, for
# a caller that drives this function directly: `run_paired` passes the operator's
# `--full-timeout` through, because a hardcoded 600s under `--full-timeout 900`
# refuses a healthy run as INCONCLUSIVE and aborts a run that would have
# succeeded. It is only ever waited out when something is already wrong.
NS_RUN_ROOT_TIMEOUT_S = 600.0

# The parser-force probe (spec E2). A retrieval question, so the parser has a
# mode for the switch to force; one full forced turn per arm.
PARSER_FORCE_PROBE_QUERY = "How many tissue samples are in the database?"

# The phrase the NS parser writes into `parser_plan.notes` on every forced
# retrieval plan: "forced to <arm> by the evaluation switch (parser chose <mode>)".
# Owned by NessieAI/chat_nextseek/src/chat_nextseek/agents/parser.py and pinned
# against that file's text by tests/test_preflight.py, because the host lane
# cannot import the engine.
FORCE_NOTE_MARKER = "by the evaluation switch"

# The server process flag the switch needs, named in the remedies. The server
# compares it with exactly "1".
EVAL_PARSER_FORCE_ENV = "NEXTSEEK_EVAL_PARSER_FORCE"

# The parser modes the switch acts on. It writes no note for any other mode.
_FORCEABLE_MODES = frozenset({"new_search", "graph_query"})


class PreflightRefused(RuntimeError):
    """Base for every refusal here, so a caller can map them to one exit code.

    Distinct SUBCLASSES because the remedies differ; one base because the
    consequence does not -- the run is refused, the probe turn was billed, and
    no paired arm was.
    """


class ForceRouteRejected(PreflightRefused):
    """The server ignored `force_route`. The account is almost certainly not staff."""


class NsRunRootMissing(PreflightRefused):
    """The endpoint emitted no `ns_run_root`. The image predates this branch."""


class ParserForceRejected(PreflightRefused):
    """The evaluation switch did not land, or the probe could not show that it did."""


def assert_force_route_works(post_query, get_progress, *, sleep=time.sleep,
                             clock=time.monotonic, poll_interval_s: float = 2.0,
                             ns_run_root_timeout_s: float = NS_RUN_ROOT_TIMEOUT_S
                             ) -> None:
    """One out-of-scope turn forced to `ns`, checked twice. Raises on either.

    Out-of-scope on purpose. A forced decision is ROUTE_NS or ROUTE_CC and never
    ROUTE_UNRELATED, so a question the router WOULD call unrelated gives a clean
    two-valued answer: `nextseek_query` means the force landed, `unrelated` means
    it did not. Cheapest possible discriminator, and it is an NS turn either way.

    A turn that never emits `route_decided` leaves both fields None, which is
    inconclusive rather than refused — it takes the raising path too, because
    proceeding on an unproven force is the exact failure this guard exists for.
    It gets its own message: the two conditions have different remedies, and
    telling an operator whose endpoint is hung to switch accounts sends them
    somewhere that cannot help.

    Driven at `route` tier, not `full`. `route_decided` is emitted before either
    engine runs (cc_assistant.py:403, immediately after `_decide_route`), so the
    discriminator is identical — but the poll loop breaks at the event in ~2s
    and a hung probe hits `route_timeout_s=60` instead of `full_timeout_s=600`.

    THE FORCE IS CHECKED FIRST AND THE TURN IS THEN FOLLOWED TO ITS END. The
    route check is the cheap one and a dropped force makes everything after it
    meaningless, so it fails fast at 60s; only once it passes does
    `_await_ns_run_root` keep polling the SAME task_id for the join key. One
    turn, two facts, no second probe to pay for.
    """
    res = http_driver.drive(PROBE_QUERY, tier="route", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    route, source = res.route_obs.route, res.route_obs.source
    if source != "forced" or route == "unrelated":
        observed = f"route={route!r} source={source!r}"
        if not ro.has_route_decided(res.payload):
            # No `route_decided` event at all: the turn never reported a routing
            # decision, so there is no observation to contradict. Claiming the
            # force was dropped here would be asserting a cause we cannot see —
            # the same refusal `cost_summary` makes when it reports `unmeasured`
            # over $0.00.
            raise ForceRouteRejected(
                f"the probe turn produced NO routing decision: {observed}, "
                f"status={res.status!r}, no `route_decided` event arrived.\n"
                f"This is INCONCLUSIVE, not evidence that force_route was dropped: a "
                f"hung, erroring or unreachable endpoint looks exactly like this, and "
                f"so does a turn that died before it routed. Check the stack is up and "
                f"that one turn completes at all before suspecting the account.\n"
                f"Raising regardless — an UNPROVEN force is as unsafe to spend a "
                f"300-turn run on as a refused one.")
        raise ForceRouteRejected(
            f"force_route was not honoured: {observed}, expected "
            f"route='nextseek_query' source='forced'.\n"
            f"force_route is gated on is_staff/is_superuser and a non-admin's value is "
            f"dropped silently. Run --bayesian as a staff account; the harness default "
            f"'demo' is not one. Without this the whole run measures the router, not "
            f"the engines.")

    found, payload = _await_ns_run_root(
        get_progress, res.task_id, sleep=sleep, clock=clock,
        poll_interval_s=poll_interval_s, timeout_s=ns_run_root_timeout_s)
    if found:
        return
    status = payload.get("status")
    if status not in _TERMINAL:
        # The turn never finished, so the event's absence proves nothing about
        # the image. Same split as the inconclusive force above, and it still
        # raises: an unproven join key is as unsafe to spend a paid run on.
        raise NsRunRootMissing(
            f"the probe turn never finished: status={status!r} after "
            f"{ns_run_root_timeout_s:g}s, so no `{NS_RUN_ROOT_EVENT}` event could "
            f"be observed.\n"
            f"This is INCONCLUSIVE, not evidence that the event is missing from "
            f"the image: a hung or saturated endpoint looks exactly like this. "
            f"Check that one NS turn completes at all before rebuilding.\n"
            f"Raising regardless — a run whose join key is UNPROVEN is as unsafe "
            f"to pay for as one whose join key is known absent.")
    raise NsRunRootMissing(
        f"the forced-NS probe turn reached status={status!r} and emitted NO "
        f"`{NS_RUN_ROOT_EVENT}` event.\n"
        f"That event is PRODUCT code added on this branch "
        f"(NessieAI/cc/turn.py, `_emit_ns_run_root`), and a "
        f"running container serves a BAKED image, so it is absent until the image "
        f"is rebuilt:\n"
        f"    ./startup.sh rebuild\n"
        f"...then run this again. It is the ONLY key joining a task_id to the NS "
        f"engine's output directory: without it `collect` records "
        f"`ns_run_root_event` absent for all 127 NS arms, every NS `artifact_count` "
        f"is unobserved, and the only way to recover is to pay for the whole run "
        f"a second time.")


def _await_ns_run_root(get_progress, task_id, *, sleep, clock, poll_interval_s,
                       timeout_s) -> tuple[bool, dict]:
    """`(the event was seen, the last payload)` for one already-issued turn.

    Polls the SAME task the route probe issued, so this costs no extra turn.

    ONE retry after the row goes terminal, for the reason `collect` documents as
    fact 3: `ns_run_root` necessarily lands AFTER `query_complete`, because
    `run_query` emits `query_complete` from inside itself while the join key is
    emitted from the `finally` around the call, through a separate `task.save()`.
    A reader that stopped at the terminal status would call the event missing in
    the one-save-wide window between them, and send an operator to rebuild an
    image that is already correct. The delay is `collect.RETRY_DELAY_S`, the same
    constant, so the two cannot drift.

    Poll exceptions are NOT swallowed. The route probe has already driven this
    endpoint successfully by the time this runs, so a failure here is a real
    change in the endpoint's health and not a transient to be papered over --
    and `cli` maps the URLError it raises to its own exit code.
    """
    deadline = clock() + timeout_s
    while True:
        payload = get_progress(task_id)
        if _has_event(payload, NS_RUN_ROOT_EVENT):
            return True, payload
        if payload.get("status") in _TERMINAL:
            sleep(collect.RETRY_DELAY_S)
            payload = get_progress(task_id)
            return _has_event(payload, NS_RUN_ROOT_EVENT), payload
        if clock() >= deadline:
            return False, payload
        sleep(poll_interval_s)


def _has_event(payload, name: str) -> bool:
    return any((e or {}).get("event") == name
               for e in (payload.get("progress") or []))


def _last_event_data(payload, name: str) -> dict:
    data: dict = {}
    for e in payload.get("progress") or []:
        if (e or {}).get("event") == name:
            data = e.get("data") or {}
    return data


def assert_parser_force_works(post_query, get_progress, arms, *, sleep=time.sleep,
                              clock=time.monotonic,
                              timeout_s: float = NS_RUN_ROOT_TIMEOUT_S) -> None:
    """One full forced turn per arm proves the evaluation switch landed. Raises on any arm.

    Run after `assert_force_route_works`. Each arm's probe is checked in this
    order, and each failure names its own remedy:

    1. the route force held (`route_decided` from source `forced`, to NS). A drop
       is a `ForceRouteRejected`: the account is the remedy;
    2. the turn finished and was not a provider outage (else INCONCLUSIVE);
    3. `parser_plan.notes` carries FORCE_NOTE_MARKER. The server drops the field
       for anyone but a superuser, on a process without the flag, and on code that
       predates the switch, all without a word, so a missing note names all three;
    4. the plan ends on `graph_query` for the graph arm and off it for the api arm;
    5. on the graph arm, `debug.graph_context` is `catalog` (spec D15). `fallback`
       means the graph agent read the committed JSON, not the live catalog, which
       voids every graph-arm question.

    A full turn, not a route-tier one: the note and the context arrive with
    `query_complete`, long after `route_decided`. `timeout_s` is the run's own
    per-turn ceiling, for the reason `NS_RUN_ROOT_TIMEOUT_S` gives.
    """
    # Lazy: runner imports this module, and the arm presets are runner's.
    from NessieAI.tests.nessie_tests.runner import ARM_PRESETS

    arms = list(arms)
    unknown = [a for a in arms if a not in ARM_PRESETS]
    if unknown or not arms:
        raise ValueError(f"parser-force probe: name one or more arms of "
                         f"{sorted(ARM_PRESETS)}; got {arms!r}")
    for arm in arms:
        preset = ARM_PRESETS[arm]
        res = http_driver.drive(
            PARSER_FORCE_PROBE_QUERY, tier="full", post_query=post_query,
            get_progress=get_progress, force_new=True, force_route=preset["force_route"],
            force_parser_mode=preset["force_parser_mode"], full_timeout_s=timeout_s,
            sleep=sleep, clock=clock)
        _check_parser_force(arm, preset["force_parser_mode"], res, timeout_s)


def _check_parser_force(arm, force_mode, res, timeout_s) -> None:
    where = (f"parser-force probe for arm {arm!r} (force_parser_mode={force_mode!r}, "
             f"task {res.task_id!r})")
    route, source = res.route_obs.route, res.route_obs.source
    if source != "forced" or route != ro.ROUTE_NS:
        if not ro.has_route_decided(res.payload):
            raise ParserForceRejected(
                f"{where}: INCONCLUSIVE, the turn produced no routing decision "
                f"(status={res.status!r}, no `route_decided` event).\n"
                f"A hung, erroring or unreachable endpoint looks exactly like this. Check "
                f"that one turn completes at all (scripts/graph_search/nessie_venue.sh logs) "
                f"before suspecting the switch. Raising regardless: an unproven switch is as "
                f"unsafe to spend a paid run on as a refused one.")
        raise ForceRouteRejected(
            f"{where}: force_route was not honoured: route={route!r} source={source!r}, "
            f"expected route='nextseek_query' source='forced'.\n"
            f"force_route and force_parser_mode are honoured only for a superuser "
            f"(is_superuser; is_staff alone is ignored) and dropped silently for anyone "
            f"else. Run as the superuser account.")

    qc = _last_event_data(res.payload, "query_complete")
    if outage.is_provider_outage(qc.get("reply")):
        raise ParserForceRejected(
            f"{where}: INCONCLUSIVE, a provider outage: the reply is the exhausted-fallback "
            f"message, so no parser ran and there is no plan to read.\n"
            f"Nothing here points at the switch. Rerun the same command once the provider "
            f"recovers; the probes are the only turns spent so far.")
    if res.status != "completed":
        raise ParserForceRejected(
            f"{where}: INCONCLUSIVE, the turn ended with status={res.status!r} (per-turn "
            f"ceiling {timeout_s:g}s), so its plan cannot be read.\n"
            f"A hung, saturated or failing endpoint looks exactly like this. Read the "
            f"venue's log for the task (scripts/graph_search/nessie_venue.sh logs) and check "
            f"that one NS turn completes before suspecting the switch. Raising regardless.")

    debug = qc.get("debug") or {}
    plan = debug.get("parser_plan") or {}
    mode = plan.get("mode")
    notes = plan.get("notes")
    notes_text = notes if isinstance(notes, str) else " | ".join(str(n) for n in notes or [])
    if FORCE_NOTE_MARKER not in notes_text:
        if mode is None:
            raise ParserForceRejected(
                f"{where}: INCONCLUSIVE, the finished turn carries no "
                f"`debug.parser_plan`.\n"
                f"The NS pipeline stopped before its parser ran, or the reply is an error. "
                f"Read the turn's reply and the venue's log for the task.")
        if mode not in _FORCEABLE_MODES:
            raise ParserForceRejected(
                f"{where}: INCONCLUSIVE, the parser read the probe as {mode!r}, a "
                f"non-retrieval mode the switch leaves alone and writes no note for.\n"
                f"This proves nothing either way. The probe asks "
                f"\"{PARSER_FORCE_PROBE_QUERY}\"; a parser that reads it as {mode!r} has "
                f"changed, so look at the turn's reply and plan before any paid run.")
        raise ParserForceRejected(
            f"{where}: the evaluation switch did not land: parser_plan.notes carries no "
            f"{FORCE_NOTE_MARKER!r} note (notes={notes!r}, mode={mode!r}).\n"
            f"The server drops force_parser_mode without a word in three cases; check each:\n"
            f"  1. the server process lacks {EVAL_PARSER_FORCE_ENV}=1 (exactly \"1\"; "
            f"\"true\" or \" 1\" leave it off). scripts/graph_search/nessie_venue.sh check "
            f"compares it;\n"
            f"  2. the account is not a superuser (is_staff alone is ignored);\n"
            f"  3. the venue's snapshot predates the switch: nessie_venue.sh prepare, then "
            f"down and up, to snapshot this branch's HEAD.")

    if force_mode == "graph" and mode != "graph_query":
        raise ParserForceRejected(
            f"{where}: the switch's note is present but the plan ended in mode {mode!r}, "
            f"not 'graph_query'.\n"
            f"The switch turns new_search into graph_query and runs last among the "
            f"parser's guardrails, so either code after it changed the mode or the "
            f"snapshot's switch is not this branch's. Compare the venue's SNAPSHOT with "
            f"this branch's HEAD before any paid run.")
    if force_mode != "graph" and mode == "graph_query":
        raise ParserForceRejected(
            f"{where}: the switch's note is present but the plan stayed on "
            f"'graph_query'.\n"
            f"The api arm must end on a REST endpoint (new_search). Either code after the "
            f"switch sent it back to the graph, or the snapshot's switch is not this "
            f"branch's; compare the venue's SNAPSHOT with this branch's HEAD.")

    if force_mode == "graph":
        context = debug.get("graph_context")
        if context is None:
            raise ParserForceRejected(
                f"{where}: the graph turn carries no `debug.graph_context`.\n"
                f"This branch's graph turn records which schema its agent read (spec D15); "
                f"a turn without the record ran code that predates it. Snapshot this "
                f"branch's HEAD (scripts/graph_search/nessie_venue.sh prepare, then down and "
                f"up) and run the preflight again.")
        if context != "catalog":
            raise ParserForceRejected(
                f"{where}: the graph agent read graph_context={context!r}, not the live "
                f"catalog.\n"
                f"'fallback' means it used the committed neo4j_schema.json because the "
                f"catalog read failed: the graph is unreachable from the venue, or it has no "
                f"GraphMeta with schema_version 1.1. Every graph-arm question would be void "
                f"(spec E6). Run scripts/graph_search/nessie_venue.sh check, which reads the "
                f"catalog the same way.")
