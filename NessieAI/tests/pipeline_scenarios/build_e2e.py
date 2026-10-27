"""Generate the chat end-to-end variants (spec §4.3) into nessie_tests' corpus.json.

  python3 -m NessieAI.tests.pipeline_scenarios.build_e2e

Each variant: a setup turn that finds the cohort's samples by UID, the story's question, and,
for a launch story, one reply asking for the build (the story's first scripted reply, else
DEFAULT_REPLY). It stops at the confirm-to-submit question and never answers it.

The build turn asserts only which pipeline was built. Settings (`launch.must_match`) are checked by
the in-process run only, because the chat snapshot carries no launch plan, so the harness skips every
`pipeline_agent.launch_plan.*` criterion. No built-launch artifact is observable either: a pipeline-agent
turn's query_complete carries no files or artifacts, so `api_artifact.samplesheet.csv` cannot resolve.

The variants join the existing `pipeline_launch` family (the live router reads corpus.json
for its family labels, so a new family would change routing). Everything else in the file is
left byte for byte as it was: the rewrite uses the file's own indent and ASCII escaping.
The variants are origin "overlay": they exist only here, and test_catalog_drift requires every
origin "base" variant to still be in the upstream e2e catalog.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from NessieAI.paths import NESSIE_CORPUS
from NessieAI.tests.pipeline_scenarios.run import DEFAULT_REPLY
from NessieAI.tests.pipeline_scenarios.stories import cohort_uids, story_by_id

E2E = Path(__file__).resolve().parent / "e2e.json"
MAX_SETUP_UIDS = 12
#: corpus.json's own layout; a different one would make the rewrite touch every line.
CORPUS_INDENT = 2


def _alternation(keys) -> str:
    return "|".join(re.escape(k) for k in sorted(keys))


def _variant(story) -> dict:
    uids = cohort_uids(story.cohort)[:MAX_SETUP_UIDS]
    setup = {"label": "setup_search", "query": "Find " + ", ".join(uids) + ".",
             "pass_criteria": [{"field": "neo4j_ok", "op": "true", "value": None}]}
    ask = {"label": "question", "query": story.question,
           "pass_criteria": [{"field": "route", "op": "eq", "value": story.route}]}
    turns = [setup, ask]
    if story.route == "nextseek_query" and story.must_not:
        ask["pass_criteria"].append({"field": "pipeline_agent.pipeline_key", "op": "matches_re",
                                     "value": rf"(?s)^(?!({_alternation(story.must_not)})$).*"})
    if story.expect in ("not_yet", "refuse"):
        ask["pass_criteria"].append({"field": "pipeline_agent.selection.verdict", "op": "matches_re",
                                     "value": "^(refused|out_of_scope)$"})
    elif story.launch.get("built"):
        union = {k for a in story.acceptable for k in a}
        turns.append({"label": "build", "query": story.replies[0] if story.replies else DEFAULT_REPLY,
                      "pass_criteria": [
                          {"field": "pipeline_agent.pipeline_key", "op": "matches_re", "value": rf"^({_alternation(union)})$"}]})
    return {"id": f"pipeline_scenarios.{story.id}", "family": "pipeline_launch",
            "name": f"pipeline scenario {story.id}", "tags": ["pipeline_scenarios", "multi-turn"],
            "requires_env": [], "turns": turns, "status": "active", "origin": "overlay", "is_bayesian": False,
            "hibayes_subtype": None, "expected_behavior": None, "artifact_expected": None,
            "artifact_kind": None, "retirement": None,
            "_why": f"Generated from NessieAI/tests/pipeline_scenarios story {story.id}; edit the story, then re-run build_e2e."}


def variants() -> list[dict]:
    return [_variant(story_by_id(sid)) for sid in json.loads(E2E.read_text())["stories"]]


def main() -> int:
    corpus = json.loads(NESSIE_CORPUS.read_text(encoding="utf-8"))
    family = corpus["families"]["pipeline_launch"]
    family["variants"] = [v for v in family["variants"] if "pipeline_scenarios" not in v.get("tags", [])] + variants()
    NESSIE_CORPUS.write_text(json.dumps(corpus, indent=CORPUS_INDENT) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
