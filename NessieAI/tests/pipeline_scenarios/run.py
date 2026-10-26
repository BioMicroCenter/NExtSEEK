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

from NessieAI.tests.pipeline_scenarios import scrub
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

    def __init__(self, config, tape: Tape | None):
        self._config, self._tape = config, tape

    def get_agent_model(self, label):
        client, model, budget = self._config.get_agent_model(label)
        return (RecordingClient(client, self._tape) if self._tape else client), model, budget

    def __getattr__(self, name):
        return getattr(self._config, name)


def make_session(story: Story, uids: list[str]) -> dict:
    bundle = {"id": 1, "user_query": f"the samples of {story.cohort}",
              "graph_result": {"ok": True, "data": [{"uuid": u} for u in uids]}}
    log = [{"turn_id": 1, "ts": "t", "mode": "graph_query", "user_query": bundle["user_query"],
            "assistant_reply": f"Found {len(uids)} samples.", "bundle_id": 1, "status": "ok"}]
    for n, turn in enumerate(story.history, 2):
        log.append({"turn_id": n, "ts": "t", "mode": "graph_query", "user_query": turn["user"],
                    "assistant_reply": turn.get("assistant", ""), "status": "ok"})
    return {"results_history": [bundle], "chat_log": log}


def router_history(story: Story, uids: list[str]):
    from NessieAI.router.router_context import HistoryTurn
    turns = [HistoryTurn(position=1, user_message=f"Find the samples of {story.cohort}",
                         assistant_reply=f"Found {len(uids)} samples.", result_count=len(uids),
                         sample_uids=uids[:20])] if uids else []
    for n, turn in enumerate(story.history, len(turns) + 1):
        turns.append(HistoryTurn(position=n, user_message=turn["user"], assistant_reply=turn.get("assistant") or None))
    return turns


def scrubber(source: str, value):
    """Make one taped value safe for a public repository, or refuse it (ValueError)."""
    if source in _METADATA_SOURCES:
        value = scrub.scrub_metadata(value)
    if source == "gather_protocol_text" and isinstance(value, dict):
        value = {**value, "text": scrub.protocol_markers_found(value.get("text") or "")}
    hits = scrub.refusals_in(value)
    if hits:
        raise ValueError(f"{source}: cannot keep {hits[:3]} on a public tape")
    return value


def _decision_record(d) -> dict:
    return {k: getattr(d, k, None) for k in ("route", "source", "reasoning", "router_model", "router_cost_usd")}


def run_story(story: Story, config, tape: Tape | None, log_dir: Path, decide=None) -> tuple[Outcome, dict]:
    """Run one story. A tape that refused or did not match raises from taped()'s exit: the
    caller (main) turns that into the story's FAIL, so it must never be caught here."""
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
    cfg = config if isinstance(config, RunConfig) else RunConfig(config, tape)
    log_dir.mkdir(parents=True, exist_ok=True)
    with (taped(tape, scrubber) if tape else contextlib.nullcontext()):
        res = pa.start(session, cfg, user_query=story.question, log_dir=str(log_dir))
        record["replies"].append(res.get("reply") or "")
        answers = list(story.replies)
        for _ in range(MAX_REPLIES):
            state = session.get(pa.PIPELINE_AGENT_KEY) or {}
            if (state.get("artifacts") or {}).get("launch") or not state.get("active") or story.expect == "ask":
                break
            res = pa.handle_turn(session, cfg, answers.pop(0) if answers else DEFAULT_REPLY, log_dir=str(log_dir))
            record["replies"].append(res.get("reply") or "")
    state = session.get(pa.PIPELINE_AGENT_KEY) or {}
    built = bool((state.get("artifacts") or {}).get("launch"))
    outcome.selection = state.get("selection")
    outcome.pipeline_key = state.get("pipeline_key")
    outcome.launch_params = ((state.get("launch_plan") or {}).get("params") or {}) if built else None
    outcome.replies = record["replies"]
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
    chosen = [s for s in load_stories() if s.ruled
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
    results = []
    for story in runnable:
        tape = Tape(mode="record")
        try:
            outcome, record = run_story(story, RunConfig(real, tape), tape, out / "logs" / story.id)
        except Exception as exc:  # noqa: BLE001 - one story's crash is that story's FAIL
            outcome, record = Outcome(route="?", error=f"{type(exc).__name__}: {exc}"), {"decision": {}, "replies": []}
        s = score(story, outcome)
        row = {"story": asdict(story), "decision": record["decision"], "outcome": asdict(outcome),
               "score": asdict(s), "tape": None if tape.refused else tape.to_json(), "fingerprint": None}
        (out / "stories" / f"{story.id}.json").write_text(json.dumps(row, indent=2, default=str))
        results.append(row)
        print(f"[{s.grade:<7}] {story.id}: {'; '.join(s.reasons)}")
    summary = {g: sum(1 for r in results if r["score"]["grade"] == g) for g in ("PASS", "PARTIAL", "FAIL")}
    (out / "summary.json").write_text(json.dumps({"summary": summary, "n": len(results), "skipped": skipped}, indent=2))
    write_report(out, results)
    print(f"{summary} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
