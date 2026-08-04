"""`--cases <file.json>`: run an explicit, hand-authored list instead of a seeded sample.

A seeded sample answers "is the corpus still healthy". It cannot answer "does THIS
work", which is what a manual probe needs: reingestion of an nf-core run, a
harmonization conversation, cross-mode memory between Container-CC and NExtSEEK.
Those questions are not in the corpus, and waiting for a seed to draw the five
cases you care about is not a plan — the 2026-07-28 run dropped three of the
fixes it was meant to verify because the seed did not select them.

The file is CATALOG-SHAPED on purpose, so `families` blocks can be copy-pasted
straight out of corpus.json and get the same PassCriterion validation. Two keys:

CAREFUL when you copy a block: it is parsed by `load_catalog`, which does not
look at `status`, so RETIRED bodies come along and RUN. The `search_advanced`
block copies as 69 cases, 5 of them retired (mostly GBM questions retired
because the study does not exist). Strip them, or use `include_ids`, which
resolves against `merged()` and cannot select a retired case.

  include_ids   pull existing corpus variants in, by id, in file order
  families      define new ad-hoc variants inline (plain catalog structure --
                no `status`/`origin`, so it is NOT a unified corpus)

Inline variants run EXACTLY as written: no family floor, no route policy. A
hand-authored probe is a precise instrument, and silently bolting extra
assertions onto ten questions someone wrote deliberately is the opposite of what
this mode is for.
"""
import json
import pathlib

import pytest

from nessie_tests import corpus

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _write(tmp_path, payload):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_include_ids_pull_existing_corpus_variants(tmp_path):
    path = _write(tmp_path, {"include_ids": ["route.unrelated", "green.mus_ndma"]})
    picked = corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))
    assert [v.id for v in picked] == ["route.unrelated", "green.mus_ndma"]


def test_include_ids_keep_file_order_not_corpus_order(tmp_path):
    """The file is a running order. A probe usually wants seed-then-followup."""
    path = _write(tmp_path, {"include_ids": ["green.mus_ndma", "route.unrelated"]})
    picked = corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))
    assert [v.id for v in picked] == ["green.mus_ndma", "route.unrelated"]


def test_an_unknown_include_id_fails_loudly(tmp_path):
    """A typo must not silently shrink the run. Every id is paid for."""
    path = _write(tmp_path, {"include_ids": ["route.unrelated", "route.typoed_id"]})
    with pytest.raises(ValueError, match="route.typoed_id"):
        corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))


def test_inline_variants_are_loaded_and_validated(tmp_path):
    path = _write(tmp_path, {"families": {"manual": {"description": "probe", "variants": [
        {"family": "manual", "id": "manual.reingest", "name": "Reingest an nf-core run",
         "tags": ["nessie", "full"], "requires_env": [],
         "turns": [{"label": "main",
                    "query": "Build a NExtSEEK upload sheet from the nf-core rnaseq outputs.",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "container_cc"}]}]}
    ]}}})
    picked = corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))
    assert [v.id for v in picked] == ["manual.reingest"]
    assert picked[0].turns[0].pass_criteria[0].field == "route"


def test_inline_variants_run_exactly_as_written_with_no_floor(tmp_path):
    """A hand-authored probe is a precise instrument; the floor must not add to it."""
    path = _write(tmp_path, {"families": {"graph_query": {"description": "probe", "variants": [
        {"family": "graph_query", "id": "manual.graph_probe", "name": "probe",
         "tags": ["nessie", "full"], "requires_env": [],
         "turns": [{"label": "main", "query": "What studies have monkeys",
                    "pass_criteria": [{"field": "neo4j_ok", "op": "true", "value": None}]}]}
    ]}}})
    picked = corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))
    fields = {c.field for t in picked[0].turns for c in t.pass_criteria}
    assert fields == {"neo4j_ok"}, f"the floor leaked into a hand-authored probe: {fields}"


def test_include_ids_and_inline_variants_compose(tmp_path):
    path = _write(tmp_path, {
        "include_ids": ["route.unrelated"],
        "families": {"manual": {"description": "probe", "variants": [
            {"family": "manual", "id": "manual.x", "name": "x", "tags": ["nessie"], "requires_env": [],
             "turns": [{"label": "main", "query": "q",
                        "pass_criteria": [{"field": "route", "op": "eq", "value": "unrelated"}]}]}
        ]}}})
    picked = corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))
    assert [v.id for v in picked] == ["route.unrelated", "manual.x"]


def test_an_empty_case_file_fails_rather_than_running_nothing(tmp_path):
    path = _write(tmp_path, {})
    with pytest.raises(ValueError, match="no cases"):
        corpus.select_cases(corpus.merged(CORPUS), *corpus.load_case_file(path))
