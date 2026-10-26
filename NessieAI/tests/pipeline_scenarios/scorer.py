"""Score one story's outcome: PASS, PARTIAL or FAIL, with the reasons (spec §4.1)."""
from __future__ import annotations

from dataclasses import dataclass, field

from NessieAI.tests.pipeline_scenarios.stories import Story


@dataclass
class Outcome:
    """What the router and the pipeline agent did for one story."""
    route: str
    selection: dict | None = None        # the agent state's "selection": verdict, pipelines, dropped
    pipeline_key: str | None = None      # what the agent built on
    launch_params: dict | None = None    # launch_plan.params once configure_run succeeded, else None
    replies: list = field(default_factory=list)   # the agent's text replies, in order
    uids: list = field(default_factory=list)      # the cohort UIDs the run used (the replay rebuilds from them)
    error: str | None = None             # the runner's own failure, never the agent's


@dataclass
class Score:
    grade: str
    reasons: list


def offered(o: Outcome) -> set[str]:
    out = set()
    if o.selection:
        out |= set(o.selection.get("pipelines") or []) | set(o.selection.get("dropped") or [])
    if o.pipeline_key:
        out.add(o.pipeline_key)
    return out


def _mismatches(params: dict, must_match: dict) -> list[str]:
    out = []
    for key, want in (must_match or {}).items():
        got = params.get(key)
        if isinstance(want, dict) and "includes" in want:
            have = {t.strip() for t in str(got or "").split(",") if t.strip()}
            missing = sorted(set(want["includes"]) - have)
            if missing:
                out.append(f"{key} lacks {missing} (is {got!r})")
        elif got != want:
            out.append(f"{key} is {got!r}, not {want!r}")
    return out


def score(story: Story, o: Outcome) -> Score:
    if o.error:
        return Score("FAIL", [f"runner error: {o.error}"])
    if o.route != story.route:
        return Score("FAIL", [f"routed {o.route}, expected {story.route}"])
    if story.route != "nextseek_query" or (story.family == "off_topic" and not story.acceptable):
        return Score("PASS", ["route"])
    bad = sorted(offered(o) & set(story.must_not))
    if bad:
        return Score("FAIL", [f"offered {bad}, which must never be offered"])
    built = o.launch_params is not None
    if story.expect in ("not_yet", "refuse"):
        if built or o.pipeline_key:
            return Score("FAIL", [f"built on {o.pipeline_key} for a {story.expect} story"])
        return Score("PASS", [story.expect])
    if story.expect == "ask":
        asked = bool(o.replies and "?" in o.replies[0]) or (o.selection or {}).get("verdict") == "fork"
        if asked and not built:
            return Score("PASS", ["asked"])
        return Score("PARTIAL", [f"chose {o.pipeline_key or sorted(offered(o))} without asking"])
    if (o.selection or {}).get("verdict") == "refused" and not built:
        return Score("FAIL", [f"refused: {o.selection.get('reason') or ''}"[:200]])
    if story.launch.get("built") and not built:
        last = (o.replies[-1] if o.replies else "")[:160]
        return Score("FAIL", [f"launch expected but not built; last reply: {last!r}"])
    sets = [set(a) for a in story.acceptable]
    union = set().union(*sets) if sets else set()
    reasons = []
    if built:
        if o.pipeline_key not in union:
            reasons.append(f"built on {o.pipeline_key}, acceptable {story.acceptable}")
        elif (o.selection or {}).get("verdict") == "fork" and all(len(a) == 1 for a in sets) \
                and set(o.selection.get("pipelines") or []) not in sets:
            reasons.append(f"asked between {o.selection.get('pipelines')} when the story expects a choice")
        reasons += _mismatches(o.launch_params, story.launch.get("must_match") or {})
    elif offered(o) not in sets and not (offered(o) & union):
        reasons.append(f"offered {sorted(offered(o))}, acceptable {story.acceptable}")
    return Score("PARTIAL", reasons) if reasons else Score("PASS", ["acceptable"])
