"""The paid in-process runner: real router, real pipeline agent, no submit (spec §4.1).

Inside the nextseek container, with the owner's go for this run:

  docker exec -w /app nextseek uv run --no-sync python -m NessieAI.tests.pipeline_scenarios.run \
      [--ids a,b | --family chromatin] [--per-story-usd 1.50] --yes

Without --yes it prints the stories it would run and the estimated cost, and stops.
Only ruled stories run, and only those whose cohort has UIDs on this stack.
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
from NessieAI.tests.pipeline_scenarios.tape import RecordingClient, Tape, taped

DEFAULT_REPLY = "Yes, use what you recommend and build the run."
MAX_REPLIES = 3
#: Below the agent's 75-leaf resolve cap (agent_tools MAX_RESOLVE_LEAVES), so a cohort is never refused for size.
MAX_STORY_UIDS = 60
OUTPUTS = Path(__file__).resolve().parents[3] / "outputs"
_METADATA_SOURCES = ("fetch_reporter_metadata", "annotate_metadata_with_sampletypes")


class RunConfig:
    """The real config, minus every submit tool, with the selection model's answers taped."""
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
        client, model, budget = self._config.get_agent_model(label)
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
        session["results_history"].append({"id": 1, "user_query": query, "graph_result": result})
        append_turn(session, user_query=query, mode="graph_query", result_payload=result,
                    assistant_reply=f"Found {len(uids)} samples.", bundle_id=1)
    for turn in story.history:
        append_turn(session, user_query=turn["user"], mode="graph_query",
                    assistant_reply=turn.get("assistant") or None)
    return session


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


def _finish(outcome: Outcome, record: dict, session: dict, pa) -> None:
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
    keeps the router's decision and the agent's replies."""
    from chat_nextseek.pipeline import agent as pa
    if decide is None:
        from NessieAI.router.router import decide
    uids = cohort_uids(story.cohort)[:MAX_STORY_UIDS] if story.cohort else []
    decision = decide(story.question, router_history(story, uids))
    record = {"decision": _decision_record(decision), "replies": []}
    outcome = Outcome(route=decision.route, uids=list(uids))
    if decision.route != "nextseek_query" or (story.family == "off_topic" and not story.acceptable):
        return outcome, record
    session = make_session(story, uids)
    try:
        cfg = config if isinstance(config, RunConfig) else RunConfig(config, tape)
        log_dir.mkdir(parents=True, exist_ok=True)
        with (taped(tape, live=live, store=store) if tape else contextlib.nullcontext()):
            res = pa.start(session, cfg, user_query=story.question, log_dir=str(log_dir))
            record["replies"].append(res.get("reply") or "")
            answers = list(story.replies)
            for _ in range(MAX_REPLIES):
                state = session.get(pa.PIPELINE_AGENT_KEY) or {}
                if (state.get("artifacts") or {}).get("launch") or not state.get("active") or story.expect == "ask":
                    break
                res = pa.handle_turn(session, cfg, answers.pop(0) if answers else DEFAULT_REPLY, log_dir=str(log_dir))
                record["replies"].append(res.get("reply") or "")
    except Exception as exc:
        _finish(outcome, record, session, pa)
        exc.outcome, exc.record = outcome, record
        raise
    _finish(outcome, record, session, pa)
    return outcome, record


def main(argv: list[str]) -> int:
    from NessieAI.tests.pipeline_scenarios.report import write_report
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids", default="")
    p.add_argument("--family", default="")
    p.add_argument("--per-story-usd", type=float, default=1.50)
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
    for story in runnable:
        tape = Tape(mode="record")
        try:
            outcome, record = run_story(story, RunConfig(real, tape), tape, out / "logs" / story.id)
        except Exception as exc:  # noqa: BLE001 - one story's crash is that story's FAIL
            # Keep what the paid run produced; only a crash before the router answered has nothing.
            outcome = getattr(exc, "outcome", None) or Outcome(route="?")
            record = getattr(exc, "record", None) or {"decision": {}, "replies": []}
            outcome.error = f"{type(exc).__name__}: {exc}"
        s = score(story, outcome)
        row = {"story": asdict(story), "decision": record["decision"], "outcome": asdict(outcome),
               "score": asdict(s), "tape": None if outcome.error else tape.to_json(), "fingerprint": run_fingerprint,
               "fingerprint_files": run_files, "agent_model": agent_model, **routing}
        (out / "stories" / f"{story.id}.json").write_text(json.dumps(row, indent=2, default=str))
        results.append(row)
        print(f"[{s.grade:<7}] {story.id}: {'; '.join(s.reasons)}")
    summary = {g: sum(1 for r in results if r["score"]["grade"] == g) for g in ("PASS", "PARTIAL", "FAIL")}
    (out / "summary.json").write_text(json.dumps({"summary": summary, "n": len(results), "skipped": skipped,
                                                "fingerprint": run_fingerprint, "fingerprint_files": run_files,
                                                "agent_model": agent_model,
                                                **routing}, indent=2))
    write_report(out, results)
    print(f"{summary} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
