"""The paid in-process runner: real router, real pipeline agent, no submit (spec §4.1).

Inside the nextseek container, with the owner's go for this run:

  docker exec -w /app nextseek uv run --no-sync python -m NessieAI.tests.pipeline_scenarios.run \
      [--ids a,b | --family chromatin] [--per-story-usd 1.50] [--max-usd 40] --yes

Without --yes it prints the stories it would run and the estimated cost, and stops.
Only ruled stories run, and only those whose cohort has UIDs on this stack.
Each story's row and summary.json record what the run spent (story_spend); --max-usd stops the run
before the next story once that total reaches it.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from NessieAI.tests.pipeline_scenarios import fingerprint, scrub
from NessieAI.tests.pipeline_scenarios.scorer import Outcome, score
from NessieAI.tests.pipeline_scenarios.stories import Story, cohort_uids, load_stories
from NessieAI.tests.pipeline_scenarios.tape import RecordingClient, Tape, TapeRefused, taped

DEFAULT_REPLY = "Yes, use what you recommend and build the run."
MAX_REPLIES = 3
#: Below the agent's 75-leaf resolve cap (agent_tools MAX_RESOLVE_LEAVES), so a cohort is never refused for size.
MAX_STORY_UIDS = 60
#: The run folders' root; scrub rewrites paths under it to /RUN on the tape.
OUTPUTS = scrub.OUTPUTS
#: The id make_session gives the cohort's search, which the parser plan names.
SEED_RESULT_ID = 1
_METADATA_SOURCES = ("fetch_reporter_metadata", "annotate_metadata_with_sampletypes")


class _PricedClient:
    """Hands each answer of the model client's chat() to the story's spend collector, as tool_loop.call_tools
    does for the agent loop's own calls (chat_with_tools, passed through here, is counted there). The
    selection model is asked through chat() on a worker thread, where turn_spend's ContextVar is unset,
    so the collector is the one current when the client was handed out."""
    def __init__(self, client, spend):
        self._client, self._spend = client, spend

    def chat(self, **kw):
        entry = {"agent": "selection", "provider": None, "model": kw.get("model"), "attempt": 0, "outcome": "ok"}
        try:
            resp = self._client.chat(**kw)
        except Exception as exc:
            self._spend.record({**entry, "outcome": "error"}, err=exc)
            raise
        self._spend.record(entry, resp=resp)
        return resp

    def __getattr__(self, name):
        return getattr(self._client, name)


class RunConfig:
    """The real config, minus every submit tool, with the selection model's answers taped and priced."""
    LURIA_ENV_COMPLETE = False
    # The loop dispatches any tool name the model emits, and the submit tools read these, so a
    # model that names submit_to_luria or submit_to_tower still finds nothing configured. The
    # empty TOWER_ENV also stops the emitter uploading a Tower dataset during a build.
    LURIA_ENV: dict = {}
    TOWER_ENV: dict = {}
    TOWER_ENV_COMPLETE = False
    SEQERA_AUTO_LAUNCH = False

    def __init__(self, config, tape: Tape | None):
        self._config, self._tape = config, tape

    def get_agent_model(self, label):
        from chat_nextseek import turn_spend
        client, model, budget = self._config.get_agent_model(label)
        spend = turn_spend.current()
        if spend is not None:
            client = _PricedClient(client, spend)
        return (RecordingClient(client, self._tape) if self._tape and self._tape.mode == "record" else client), model, budget

    def __getattr__(self, name):
        return getattr(self._config, name)


def seed_query(uids: list[str]) -> str:
    """The question a user would have typed to get this cohort: its first three UIDs."""
    more = len(uids) - 3
    return "Find these samples: " + ", ".join(uids[:3]) + (f" and {more} more" if more > 0 else "")


def make_session(story: Story, uids: list[str]) -> dict:
    """The chat as production keeps it: the cohort's search turn, then the story's earlier turns,
    each written by chat_memory.append_turn so the log has the shape the router reads."""
    from chat_nextseek.chat_memory import append_turn
    session: dict = {"results_history": [], "chat_log": []}
    if uids:
        query = seed_query(uids)
        result = {"ok": True, "data": [{"uuid": u} for u in uids]}
        session["results_history"].append({"id": SEED_RESULT_ID, "user_query": query, "graph_result": result})
        append_turn(session, user_query=query, mode="graph_query", result_payload=result,
                    assistant_reply=f"Found {len(uids)} samples.", bundle_id=SEED_RESULT_ID)
    for turn in story.history:
        append_turn(session, user_query=turn["user"], mode="graph_query",
                    assistant_reply=turn.get("assistant") or None)
    return session


def parser_plan(story: Story, uids: list[str]):
    """The parser plan the agent starts with, as the orchestrator hands it over on its nf-core report
    branch: the parser names the cohort's search as the result the question is about. start() then
    builds on that search and puts the chat's earlier turns, the story's history among them, into the
    agent's first message. None with no cohort, as there is no search to name."""
    if not uids:
        return None
    from chat_nextseek.schemas.router import ParserPlan
    return ParserPlan(mode="reporter", intent_summary=story.question, report_type="NFCORE",
                      target_result_id=SEED_RESULT_ID)


def router_history(story: Story, uids: list[str]):
    """What the router would be handed for this chat: build_history over the session's chat_log."""
    from NessieAI.router.router_context import build_history
    return build_history(make_session(story, uids)["chat_log"])


def live(source: str, value):
    """What the live run gets back from a taped call: person fields redacted, nothing else
    (no URL changed, no protocol text trimmed), so the run behaves as production does."""
    return scrub.scrub_metadata(value) if source in _METADATA_SOURCES else value


def store(source: str, value):
    """What goes on the tape: live(), then the protocol text cut to its atlas markers, every URL
    that is not public archive or nf-core test data rewritten to "<url>", then refused (ValueError)
    if an email or an absolute path is left. taped() collects the refusal on the tape and never
    raises it into the live run."""
    value = live(source, value)
    if source == "gather_protocol_text" and isinstance(value, dict):
        value = {**value, "text": scrub.protocol_markers_found(value.get("text") or "")}
    value = scrub.for_tape(value)
    hits = scrub.refusals_in(value)
    if hits:
        raise ValueError(f"{source}: cannot keep {hits[:3]} on a public tape")
    return value


def routing_settings() -> dict:
    """What the router's behaviour depends on that no recording can capture: whether posterior routing
    was on (the posterior selector reads the whole corpus file) and the laya mode. A setting that
    cannot be read is written as "error: <Class>", never left out."""
    def posterior():
        from NessieAI.router import posterior_selector
        return posterior_selector.posterior_routing_enabled()

    def laya_mode():
        from NessieAI.router import laya
        return laya.mode()

    out = {}
    for key, read in (("posterior_routing", posterior), ("laya_mode", laya_mode)):
        try:
            out[key] = read()
        except Exception as exc:  # noqa: BLE001 - a setting we cannot read must show, not vanish
            out[key] = f"error: {type(exc).__name__}"
    return out


def _decision_record(d) -> dict:
    return {k: getattr(d, k, None) for k in ("route", "source", "reasoning", "router_model", "router_cost_usd")}


_ROUTER_TOKENS = ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens")
_AGENT_TOKENS = ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")


def story_spend(decision, agent: dict | None) -> dict:
    """What one story cost, from what is already counted: the router's calls as its decision prices them
    (router_cost_usd, router_usage) and the agent's (the loop's calls and the selection model's) as
    turn_spend priced them, both on model_prices.json. `usd` is the sum of what was priced; `partial`
    says some call went unpriced or unseen (its cost is not in `usd`), never that it cost nothing.
    `agent` is turn_spend's turn record, None for a story that never started the agent."""
    usage = getattr(decision, "router_usage", None)
    router_usd = getattr(decision, "router_cost_usd", None)
    router = {"usd": router_usd,
              "partial": router_usd is None or bool(getattr(decision, "router_cost_partial", False)),
              "tokens": {k: int((usage or {}).get(k) or 0) for k in _ROUTER_TOKENS}}
    if agent is None:
        agent_part = {"usd": 0.0, "partial": False, "tokens": {k: 0 for k in _AGENT_TOKENS}}
    else:
        by_model = (agent.get("cost") or {}).get("by_model") or {}
        agent_part = {"usd": agent.get("total_cost_usd"),
                      "partial": agent.get("total_cost_usd") is None or bool(agent.get("cost_partial")),
                      "tokens": {k: sum(int(m.get(k) or 0) for m in by_model.values()) for k in _AGENT_TOKENS},
                      "turn_record": agent}
    known = [part["usd"] for part in (router, agent_part) if part["usd"] is not None]
    return {"usd": round(sum(known), 6), "partial": router["partial"] or agent_part["partial"],
            "router": router, "agent": agent_part}


def _total_spend(rows: list[dict], max_usd: float | None) -> dict:
    spends = [r.get("spend") or {"usd": 0.0, "partial": True} for r in rows]

    def part(name):
        return round(sum((s.get(name) or {}).get("usd") or 0.0 for s in spends), 6)
    return {"usd": round(sum(s["usd"] or 0.0 for s in spends), 6), "router_usd": part("router"),
            "agent_usd": part("agent"), "partial": any(s["partial"] for s in spends), "max_usd": max_usd}


def _finish(outcome: Outcome, record: dict, session: dict, pa, decision=None, spend=None) -> None:
    record["spend"] = story_spend(decision, spend.summary() if spend is not None else None)
    state = session.get(pa.PIPELINE_AGENT_KEY) or {}
    built = bool((state.get("artifacts") or {}).get("launch"))
    outcome.selection = state.get("selection")
    outcome.pipeline_key = state.get("pipeline_key")
    outcome.launch_params = ((state.get("launch_plan") or {}).get("params") or {}) if built else None
    outcome.replies = record["replies"]


def run_story(story: Story, config, tape: Tape | None, log_dir: Path, decide=None) -> tuple[Outcome, dict]:
    """Run one story. A tape that refused or did not match raises from taped()'s exit: the
    caller (main) records that as the story's runner error, so it must never be caught here.
    The exception carries what the paid run produced (`exc.outcome`, `exc.record`) so main
    keeps the router's decision, the agent's replies and what both spent (`record["spend"]`)."""
    from chat_nextseek import turn_spend
    from chat_nextseek.pipeline import agent as pa
    if decide is None:
        from NessieAI.router.router import decide
    uids = cohort_uids(story.cohort)[:MAX_STORY_UIDS] if story.cohort else []
    decision = decide(story.question, router_history(story, uids))
    record = {"decision": _decision_record(decision), "replies": []}
    outcome = Outcome(route=decision.route, uids=list(uids))
    if decision.route != "nextseek_query" or (story.family == "off_topic" and not story.acceptable):
        record["spend"] = story_spend(decision, None)
        return outcome, record
    session: dict = {}
    with turn_spend.collecting() as spend:
        try:
            session = make_session(story, uids)
            cfg = config if isinstance(config, RunConfig) else RunConfig(config, tape)
            log_dir.mkdir(parents=True, exist_ok=True)
            with (taped(tape, live=live, store=store) if tape else contextlib.nullcontext()):
                res = pa.start(session, cfg, user_query=story.question, parser_plan=parser_plan(story, uids),
                               log_dir=str(log_dir))
                record["replies"].append(res.get("reply") or "")
                answers = list(story.replies)
                for _ in range(MAX_REPLIES):
                    state = session.get(pa.PIPELINE_AGENT_KEY) or {}
                    if (state.get("artifacts") or {}).get("launch") or not state.get("active") or story.expect == "ask":
                        break
                    res = pa.handle_turn(session, cfg, answers.pop(0) if answers else DEFAULT_REPLY,
                                         log_dir=str(log_dir))
                    record["replies"].append(res.get("reply") or "")
        except Exception as exc:
            _finish(outcome, record, session, pa, decision, spend)
            exc.outcome, exc.record = outcome, record
            raise
        _finish(outcome, record, session, pa, decision, spend)
    return outcome, record


def main(argv: list[str]) -> int:
    from NessieAI.tests.pipeline_scenarios.report import write_report
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids", default="")
    p.add_argument("--family", default="")
    p.add_argument("--per-story-usd", type=float, default=1.50)
    p.add_argument("--max-usd", type=float, default=None,
                   help="stop before the next story once the recorded spend reaches this (priced calls only)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    every = load_stories()
    if a.ids:
        by_id = {s.id: s for s in every}
        for wanted in a.ids.split(","):
            if wanted not in by_id:
                print(f"skipped {wanted}: no such story")
            elif not by_id[wanted].ruled:
                print(f"skipped {wanted}: not ruled ({by_id[wanted].ruling.get('state')}); only accepted or changed stories run")
    chosen = [s for s in every if s.ruled
              and (not a.ids or s.id in a.ids.split(","))
              and (not a.family or s.family == a.family)]
    runnable = [s for s in chosen if not s.cohort or cohort_uids(s.cohort)]
    skipped = sorted(s.id for s in chosen if s not in runnable)
    print(f"{len(runnable)} stories, ~${len(runnable) * a.per_story_usd:.2f} estimated"
          + (f", stopping once ${a.max_usd:.2f} is spent (--max-usd)" if a.max_usd is not None else "")
          + (f"; skipped (no UIDs on this stack): {skipped}" if skipped else ""))
    if not a.yes:
        print("Not run: pass --yes once the owner has approved this run.")
        return 0
    from chat_nextseek.config import ChatConfig
    out = a.out or OUTPUTS / f"{time.strftime('%Y%m%dT%H%M%S')}_pipeline_scenarios"
    (out / "stories").mkdir(parents=True, exist_ok=True)
    real = ChatConfig()
    run_fingerprint, run_files = fingerprint.current(), fingerprint.files()
    agent_model = real.get_agent_model("pipeline_agent")[1]    # a model swap must be visible on the run
    routing = routing_settings()
    results = []
    not_run: list[str] = []
    for n, story in enumerate(runnable):
        spent = _total_spend(results, a.max_usd)["usd"]
        if a.max_usd is not None and spent >= a.max_usd:
            not_run = [s.id for s in runnable[n:]]
            print(f"Stopped: ${spent:.2f} spent reaches --max-usd ${a.max_usd:.2f}; not run: {not_run}")
            break
        tape = Tape(mode="record")
        unrecorded = None
        try:
            outcome, record = run_story(story, RunConfig(real, tape), tape, out / "logs" / story.id)
        except TapeRefused as exc:
            # Only the tape is lost: the run went as production would, so it is graded on its merits.
            outcome, record = exc.outcome, exc.record
            unrecorded = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - one story's crash is that story's FAIL
            # Keep what the paid run produced; only a crash before the router answered has nothing.
            outcome = getattr(exc, "outcome", None) or Outcome(route="?")
            # A crash before the router answered: whatever it spent was never seen.
            record = getattr(exc, "record", None) or {"decision": {}, "replies": [],
                                                      "spend": {"usd": 0.0, "partial": True}}
            outcome.error = f"{type(exc).__name__}: {exc}"
        s = score(story, outcome)
        row = {"story": asdict(story), "decision": record["decision"], "outcome": asdict(outcome),
               "score": asdict(s), "spend": record["spend"],
               "tape": None if outcome.error or unrecorded else tape.to_json(),
               "fingerprint": run_fingerprint, "fingerprint_files": run_files, "agent_model": agent_model, **routing}
        if unrecorded:
            row["unrecorded"] = unrecorded
        (out / "stories" / f"{story.id}.json").write_text(json.dumps(row, indent=2, default=str))
        results.append(row)
        print(f"[{s.grade:<7}] {story.id}: {'; '.join(s.reasons)}" + (" (unrecorded)" if unrecorded else "")
              + f" ${record['spend']['usd']:.2f}" + ("+" if record["spend"]["partial"] else ""))
    summary = {g: sum(1 for r in results if r["score"]["grade"] == g) for g in ("PASS", "PARTIAL", "FAIL")}
    spend = _total_spend(results, a.max_usd)
    (out / "summary.json").write_text(json.dumps({"summary": summary, "n": len(results), "skipped": skipped,
                                                "not_run": not_run, "spend": spend,
                                                "unrecorded": [r["story"]["id"] for r in results if "unrecorded" in r],
                                                "fingerprint": run_fingerprint, "fingerprint_files": run_files,
                                                "agent_model": agent_model,
                                                **routing}, indent=2))
    write_report(out, results)
    print(f"{summary}, ${spend['usd']:.2f} spent{' (partial)' if spend['partial'] else ''} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
