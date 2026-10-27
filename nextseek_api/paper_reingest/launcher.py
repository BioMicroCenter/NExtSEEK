"""Stages 3 and 4 (spec §4 and §5), run by the web container's loop, the one place that reaches cc-spawner.

A run in `curation_pending` is claimed (`curating`), its inputs are staged into the CC users volume, and the agent
runs in the cc-runtime image with the `paper-curation` skill, no turn pass and no NExtSEEK access (user ruling
2026-10-27). Its curation.json is checked (`verifying`); errors go back to it once, with the findings; then
verify.verify makes what still fails safe and the run waits in `building` for piece 3.

The volume layout reuses the spawner's chat contract: project dirname `paper-reingest` and the run id as the user
id give each run its own /data/input and /data/scratch, with no spawner change beyond `max_turns`.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from django.db.models import F

from NessieAI.cc import spawner_client
from NessieAI.ns.paper import verify as stage4
from NessieAI.ns.paper.curation_models import ContextArtifact, CurationArtifact, Finding
from NessieAI.ns.paper.curation_prompt import bounce_prompt, first_prompt
from NessieAI.ns.paper.models import EvidenceArtifact, PaperArtifact
from NessieAI.ns.paper.rulebook import RULEBOOK_PATH, load_rulebook
from NessieAI.ns.paper.store import load_artifact, save_artifact
from nextseek_api.paper_reingest.models_db import PaperReingestRun
from nextseek_api.paper_reingest.service import transition

logger = logging.getLogger(__name__)

PROJECT_DIRNAME = "paper-reingest"
MAX_LAUNCH_ATTEMPTS = 3
_SPAWNER_BUDGET_CAP = 100.0  # the spawner refuses more (docker/cc-spawner/spawner.py _validate_turn)


class LoopStopped(BaseException):
    """The web container is stopping: the run goes back to `curation_pending` for the next container."""


@dataclass(frozen=True)
class LaunchSettings:
    timeout_s: int
    max_turns: int
    budget_usd: float

    @classmethod
    def from_env(cls) -> "LaunchSettings":
        def number(name, default, cast):
            try:
                return cast(os.environ.get(name, "") or default)
            except ValueError:
                logger.warning("%s is not a number; using %s", name, default)
                return cast(default)
        return cls(timeout_s=number("PAPER_REINGEST_CURATION_TIMEOUT_S", 1800, int),
                   max_turns=number("PAPER_REINGEST_CURATION_MAX_TURNS", 200, int),
                   budget_usd=min(_SPAWNER_BUDGET_CAP, number("PAPER_REINGEST_CURATION_BUDGET_USD", 15, float)))


def launch_id(run_id: str, attempt: int) -> str:
    return f"{run_id}-{attempt}"


def _users_root() -> Path:
    return Path(os.environ.get("DMAC_USER_ROOT_MOUNT", "/dmac/users"))


def _model_ids() -> tuple[str | None, str | None]:
    from dmac_assistant.router.models import resolve_cc_fallback_model, resolve_cc_model

    return resolve_cc_model(), resolve_cc_fallback_model()


def _run_dir(root: Path, run_id: str) -> Path:
    return root / PROJECT_DIRNAME / run_id


def _stage(root: Path, run: PaperReingestRun, attempt: int, artifacts: dict, *, previous: str | None,
           findings: list[Finding]) -> Path:
    """Write the agent's inputs (``artifacts``: name -> loaded model); return the launch's scratch folder."""
    base = _run_dir(root, run.run_id)
    inputs, scratch = base / "input", base / "scratch" / launch_id(run.run_id, attempt)
    for folder in (inputs, scratch, root / PROJECT_DIRNAME / "shared"):
        folder.mkdir(parents=True, exist_ok=True)
    os.chmod(scratch, 0o777)  # the agent writes here (cc_engine sets its scratch the same way)
    for name, model in artifacts.items():
        (inputs / f"{name}.json").write_text(model.model_dump_json(), encoding="utf-8")
    (inputs / "rulebook.json").write_bytes(RULEBOOK_PATH.read_bytes())
    if previous is not None:
        (inputs / "previous_curation.json").write_text(previous, encoding="utf-8")
        (inputs / "findings.json").write_text(json.dumps([f.model_dump(mode="json") for f in findings]),
                                              encoding="utf-8")
    return scratch


def _body(run: PaperReingestRun, attempt: int, prompt: str, settings: LaunchSettings) -> dict:
    from NessieAI.cc.cc_engine import build_agent_environment

    model, fallback = _model_ids()
    env = build_agent_environment(api_user=None, turn_pass=None, path_mappings={},
                                  turn_deadline=time.time() + settings.timeout_s)
    envelope = json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}, separators=(",", ":"))
    return {"run_id": launch_id(run.run_id, attempt), "user_id": run.run_id, "project_dirname": PROJECT_DIRNAME,
            "cc_state_key": None, "transcripts_subpath": None, "previous_turns": False, "turn_files": False,
            "model_id": model, "fallback_model_id": fallback, "max_budget_usd": settings.budget_usd,
            "resume_session_id": None, "environment": env, "stdin_envelope": envelope + "\n",
            "max_turns": settings.max_turns}


def _drive(client, body: dict, settings: LaunchSettings) -> float | None:
    """Run the container to its end (or the time limit) and return the result frame's cost, if any."""
    stream = client.start_turn(body, read_timeout=settings.timeout_s + 60)
    watchdog = threading.Timer(settings.timeout_s, lambda: client.stop_turn(body["run_id"]))
    watchdog.daemon = True
    watchdog.start()
    cost = None
    try:
        while (line := stream.read_event_line()) is not None:
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if isinstance(frame, dict) and frame.get("type") == "result":
                cost = frame.get("total_cost_usd")
    finally:
        watchdog.cancel()
        stream.close()
    return cost


def _read(scratch: Path) -> tuple[CurationArtifact | None, str | None, list[Finding]]:
    path = scratch / "curation.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None, [Finding(code="shape", severity="error", detail="the agent wrote no curation.json")]
    try:
        return CurationArtifact.model_validate_json(raw), raw, []
    except Exception as exc:  # pydantic's ValidationError, for bad JSON as for a bad shape
        return None, raw, [Finding(code="shape", severity="error",
                                   detail=f"curation.json does not match the schema: {str(exc)[:800]}")]


def curate(run_id: str, *, client=spawner_client, settings: LaunchSettings | None = None,
           users_root: Path | None = None) -> str:
    run = PaperReingestRun.objects.get(run_id=run_id)
    if not transition(run, ("curation_pending",), "curating", curation_attempts=F("curation_attempts") + 1):
        return f"skipped:{run.state}"
    settings = settings or LaunchSettings.from_env()
    root = users_root or _users_root()
    attempt, costs, staged = 0, [], {}

    def launch(prompt: str, previous: str | None, findings: list[Finding]):
        nonlocal attempt
        attempt += 1
        scratch = _stage(root, run, attempt, staged, previous=previous, findings=findings)
        costs.append(_drive(client, _body(run, attempt, prompt, settings), settings))
        return _read(scratch)

    try:
        evidence = load_artifact(run_id, run.artifacts["evidence"], EvidenceArtifact)
        context = load_artifact(run_id, run.artifacts["context"], ContextArtifact)
        staged.update(paper=load_artifact(run_id, run.artifacts["paper"], PaperArtifact), evidence=evidence,
                      context=context)
        rulebook = load_rulebook()
        curation, raw, findings = launch(first_prompt(), None, [])
        if curation is not None:
            findings = stage4.check(curation, evidence, context, rulebook)
        bounced = curation is None or stage4.needs_bounce(findings)
        if bounced:
            curation, raw, second = launch(bounce_prompt(findings), raw, findings)
            if curation is None:
                return _end(run, "failed", failed_stage="curate",
                            reason=f"the agent's curation.json is unusable twice: {second[0].detail}")
        if not transition(run, ("curating",), "verifying"):
            return f"skipped:{run.state}"
        verified = stage4.verify(curation, evidence, context, rulebook, bounced=bounced)
        artifacts = {**run.artifacts, "curation": save_artifact(run_id, "curation", curation),
                     "verified": save_artifact(run_id, "verified", verified)}
        logger.info("paper reingest %s: curated in %d launch(es), cost %s USD, %d held, %d findings", run_id,
                    attempt, sum(c for c in costs if c) if any(costs) else "unknown", len(verified.held),
                    len(verified.findings))
        return _end(run, "building", from_states=("verifying",), artifacts=artifacts)
    except LoopStopped:
        _stop(client, run_id, attempt)
        transition(run, ("curating", "verifying"), "curation_pending",
                   reason="the web container stopped mid-curation; it runs again on the next one")
        raise
    except stage4.CurationUnusable as exc:
        return _end(run, "failed", from_states=("curating", "verifying"), failed_stage="verify",
                    reason=f"the curation is unusable: {exc}")
    except spawner_client.SpawnerUnavailable as exc:
        run.refresh_from_db()
        if run.curation_attempts >= MAX_LAUNCH_ATTEMPTS:
            return _end(run, "failed", failed_stage="curate", reason=f"cc-spawner unavailable: {exc}")
        return _end(run, "curation_pending", reason=f"waiting for cc-spawner: {exc}")
    except Exception as exc:
        logger.exception("paper reingest %s: curation failed", run_id)
        _stop(client, run_id, attempt)
        return _end(run, "failed", from_states=("curating", "verifying"), failed_stage="curate",
                    reason=f"{type(exc).__name__}: {exc}")


def _stop(client, run_id: str, attempt: int) -> None:
    if attempt:
        try:
            client.stop_turn(launch_id(run_id, attempt))
        except Exception:  # noqa: BLE001 - best effort; the stale sweep stops it on a later tick
            logger.warning("paper reingest %s: could not stop launch %d", run_id, attempt, exc_info=True)


def _end(run: PaperReingestRun, state: str, *, from_states=("curating",), **fields) -> str:
    if not transition(run, from_states, state, **fields):
        logger.info("paper reingest %s: left %s, not %s (changed while it was curated)", run.run_id, run.state, state)
        return f"skipped:{run.state}"
    return state
