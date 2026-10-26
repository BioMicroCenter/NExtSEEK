"""The pipeline-scenario stories: load them, resolve their cohorts, list format problems.

Standard library only, so the story files can be checked anywhere. A story is a user's
question about a cohort plus the route, the pipelines and the launch the owner ruled
right (the spec: docs/superpowers/specs/2026-10-26-question-route-pipeline-harness-design.md,
gitignored).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORIES_DIR = HERE / "stories"
TEST_COHORTS = HERE / "test_cohorts.json"
GROUNDTRUTH = HERE.parents[1] / "chat_nextseek" / "evals" / "groundtruth_cohorts.json"

FAMILIES = ("bulk_rna", "single_cell", "small_rna", "dna_variants", "hla", "chromatin",
            "methylation", "not_yet", "off_topic")
ROUTES = ("nextseek_query", "container_cc", "unrelated")
EXPECTS = ("not_yet", "ask", "refuse")
PHRASINGS = ("plain", "named", "outcome", "ambiguous", "lookalike", "migrated", "off_topic")
RULING_STATES = ("proposed", "accepted", "changed")
#: Data kinds with no choosable pipeline yet: stories about them expect "not_yet".
NOT_YET_PIPELINES = ("cutandrun", "raredisease")
#: Phrasings that must not name the pipeline they are about.
UNNAMED = ("plain", "outcome", "ambiguous", "lookalike")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Story:
    id: str
    family: str
    question: str
    cohort: str
    route: str
    acceptable: list = field(default_factory=list)
    must_not: list = field(default_factory=list)
    expect: str | None = None
    launch: dict = field(default_factory=lambda: {"built": False, "must_match": {}})
    history: list = field(default_factory=list)
    replies: list = field(default_factory=list)
    phrasing: str = "plain"
    why: dict = field(default_factory=dict)
    ruling: dict = field(default_factory=lambda: {"state": "proposed", "date": "", "note": ""})

    @property
    def ruled(self) -> bool:
        """Only a story the owner accepted or changed runs in any layer."""
        return self.ruling.get("state") in ("accepted", "changed")


def load_stories(directory: Path = STORIES_DIR) -> list[Story]:
    out: list[Story] = []
    for path in sorted(Path(directory).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload["stories"]:
            out.append(Story(**{"family": payload["family"], **raw}))
    return out


def story_by_id(story_id: str) -> Story:
    for story in load_stories():
        if story.id == story_id:
            return story
    raise KeyError(story_id)


def _real_cohorts() -> dict[str, list[str]]:
    rows = json.loads(GROUNDTRUTH.read_text(encoding="utf-8"))
    return {f"{r['study']}::{r['analysis']}": list(r["uids"]) for r in rows}


def _test_cohorts() -> dict[str, dict]:
    return json.loads(TEST_COHORTS.read_text(encoding="utf-8"))["cohorts"]


def known_cohorts() -> set[str]:
    return set(_real_cohorts()) | {f"test::{name}" for name in _test_cohorts()}


def cohort_uids(cohort: str) -> list[str]:
    """The cohort's sample UIDs. A test cohort not yet uploaded has none (an empty list)."""
    if cohort.startswith("test::"):
        return list(_test_cohorts()[cohort.removeprefix("test::")].get("uids") or [])
    return _real_cohorts()[cohort]


def _names_a_pipeline(question: str, names: set[str]) -> str | None:
    lowered = question.lower()
    return next((n for n in sorted(names) if re.search(rf"\b{re.escape(n)}\b", lowered)), None)


def problems(stories: list[Story], atlas_keys: set[str], catalog_keys: set[str]) -> list[str]:
    """Every format problem, one message each; an empty list means the files are good."""
    found: list[str] = []
    cohorts = known_cohorts()
    pipelines = set(atlas_keys) | set(catalog_keys) | set(NOT_YET_PIPELINES)
    seen: set[str] = set()
    for s in stories:
        p = f"{s.id}:"
        if s.id in seen:
            found.append(f"{p} duplicate id")
        seen.add(s.id)
        if s.family not in FAMILIES:
            found.append(f"{p} family {s.family!r} is not one of {FAMILIES}")
        if s.route not in ROUTES:
            found.append(f"{p} route {s.route!r} is not one of {ROUTES}")
        if s.phrasing not in PHRASINGS:
            found.append(f"{p} phrasing {s.phrasing!r} is not one of {PHRASINGS}")
        if s.expect is not None and s.expect not in EXPECTS:
            found.append(f"{p} expect {s.expect!r} is not one of {EXPECTS}")
        if (s.ruling or {}).get("state") not in RULING_STATES or not _DATE.match(str((s.ruling or {}).get("date") or "")):
            found.append(f"{p} ruling needs a state in {RULING_STATES} and a yyyy-mm-dd date")
        if not isinstance(s.why, dict) or not str(s.why.get("route") or "").strip():
            found.append(f"{p} why needs a 'route' reason")
        if not s.question.strip():
            found.append(f"{p} has no question")
        route_only = s.family == "off_topic" and not s.acceptable
        if s.route == "nextseek_query" and not route_only and s.cohort not in cohorts:
            found.append(f"{p} unknown cohort {s.cohort!r}")
        named = {k for answer in s.acceptable for k in answer} | set(s.must_not)
        for key in sorted(named - pipelines):
            found.append(f"{p} {key!r} is not in the atlas or the catalog")
        for key in sorted({k for answer in s.acceptable for k in answer} - set(atlas_keys)):
            found.append(f"{p} acceptable {key!r} is not in the atlas")
        both = {k for answer in s.acceptable for k in answer} & set(s.must_not)
        if both:
            found.append(f"{p} {sorted(both)} in both acceptable and must_not")
        if s.route == "nextseek_query" and not route_only:
            if s.expect in ("not_yet", "refuse"):
                if s.acceptable:
                    found.append(f"{p} an expect={s.expect!r} story must have no acceptable")
                if s.launch.get("built"):
                    found.append(f"{p} an expect={s.expect!r} story cannot expect a launch")
            elif s.expect != "ask" and (not s.acceptable or not all(s.acceptable)):
                found.append(f"{p} needs acceptable: a non-empty list of non-empty pipeline lists")
            if s.expect != "ask" and s.acceptable and not str(s.why.get("pipelines") or "").strip():
                found.append(f"{p} why needs a 'pipelines' reason")
        if s.launch.get("built") and not any(k in catalog_keys for answer in s.acceptable for k in answer):
            found.append(f"{p} expects a launch but no acceptable pipeline is launchable")
        if not isinstance(s.launch.get("must_match", {}), dict):
            found.append(f"{p} launch.must_match must be an object")
        if s.phrasing in UNNAMED:
            hit = _names_a_pipeline(s.question, set(atlas_keys) | set(catalog_keys))
            if hit:
                found.append(f"{p} phrasing {s.phrasing!r} names a pipeline ({hit})")
    return found
