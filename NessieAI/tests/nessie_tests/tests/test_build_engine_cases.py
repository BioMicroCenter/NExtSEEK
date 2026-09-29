"""The truth skeletons and the `--cases` files (graph_search Nessie POC, spec E4 and section 5).

Every input is synthetic: the real selection and truth files live under the
operator's work directory and never in the repository.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import corpus, engine_truth as et
from NessieAI.tests.nessie_tests.scripts import build_engine_cases as bec


def _variant(vid, family, query, *, criteria=(), turns=1):
    return {
        "id": vid, "family": family, "name": vid, "tags": ["smoke"], "requires_env": [],
        "turns": [{"label": "main" if k == 0 else f"t{k}", "query": query if k == 0 else f"{query} {k}",
                   "pass_criteria": list(criteria)} for k in range(turns)],
        "status": "active", "origin": "base", "is_bayesian": False,
    }


def _reply(regex):
    return {"field": "last_reply", "op": "matches_re", "value": regex}


def _endpoint(name):
    return {"field": "api_plan.endpoint", "op": "contains", "value": name}


def _corpus(tmp_path) -> Path:
    fams = {
        "sample_search": [_variant(f"ss.q{i}", "sample_search", f"Find sample set {i}",
                                   criteria=[_reply(r"\b19%d\b" % i)]) for i in range(1, 8)],
        "harmonization": [_variant("harm.one", "harmonization", "Harmonize the organs",
                                   criteria=[_reply("3,?061")])],
        "vocabulary_resolution": [_variant("voc.one", "vocabulary_resolution", "How many PBMCs?",
                                           criteria=[_reply(r"\b22[12]\b")])],
        "lineage_tree": [
            _variant("tree.native", "lineage_tree", "What derives from TIS-1?"),
            _variant("tree.rest_a", "lineage_tree", "Show the tree of MUS-2",
                     criteria=[_endpoint("sample-tree"), _endpoint("MUS-220122SAS-334-PUB")]),
            _variant("pbct.rest_b", "lineage_tree", "Monkeys with flow and seq",
                     criteria=[_endpoint("parents_by_child_types")]),
            _variant("tree.rest_c", "lineage_tree", "Walk up from D.SEQ-3",
                     criteria=[_endpoint("sample-tree")]),
        ],
        "graph_traversal": [_variant(f"graph.g{i}", "graph_traversal", f"Traverse {i}")
                            for i in range(1, 5)],
        "project_summary_report": [_variant("report.inv", "project_summary_report",
                                            "Inventory the CSBC investigation")],
        "followup_over_results": [_variant("multi.turn", "followup_over_results", "Two turns",
                                           turns=2)],
    }
    payload = {"version": 2, "families": {name: {"description": name, "variants": vs}
                                           for name, vs in fams.items()}}
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _selection(tmp_path) -> Path:
    payload = {
        "generated": "2026-09-15", "rule": "synthetic",
        "families": {
            # Deliberately not in corpus order: the skeleton keeps the selection's order.
            "sample_search": {"kept": [{"id": f"ss.q{i}", "note": ""} for i in (3, 1, 2, 5, 4)],
                              "dropped": [{"id": "ss.q6", "reason": "scope"}]},
            "harmonization": {"kept": [{"id": "harm.one", "note": ""}], "dropped": []},
            "vocabulary_resolution": {"kept": [{"id": "voc.one", "note": ""}], "dropped": []},
            "multi": {"kept": [{"id": "multi.turn", "note": ""}], "dropped": []},
        },
        "ladder": {"kept": "all compat rungs"},
        "b2": {"kept": ["narrow_eq"], "dropped": [], "to_group_b": ["lineage_descendant"]},
        "group_b": {
            "rule": "synthetic",
            "kept": [
                {"id": "graph.g1", "family": "graph_traversal", "reason": "rule b: family"},
                {"id": "graph.g2", "family": "graph_traversal", "reason": "rule b: family"},
                {"id": "tree.native", "family": "lineage_tree", "reason": "rule a: graph path"},
                {"id": "tree.rest_a", "family": "lineage_tree", "reason": "rule d: operator ruling"},
                {"id": "pbct.rest_b", "family": "lineage_tree", "reason": "rule d: operator ruling"},
                {"id": "ss.q7", "family": "sample_search", "reason": "rule c: names a study"},
                {"id": "report.inv", "family": "project_summary_report", "reason": "rule c: inv"},
            ],
            "b2": [{"id": "lineage_descendant", "reason": "structure"}],
            "excluded": [],
        },
    }
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _skeleton(tmp_path, **kw):
    return bec.skeleton_from_selection(
        json.loads(_selection(tmp_path).read_text(encoding="utf-8")),
        corpus_path=_corpus(tmp_path), **kw)


# ── skeletons from the selection ─────────────────────────────────────────────

def test_the_skeleton_takes_exactly_the_kept_ids_in_selection_order(tmp_path):
    truth = _skeleton(tmp_path, group="a", family="sample_search")
    assert [q.id for q in truth.questions] == ["ss.q3", "ss.q1", "ss.q2", "ss.q5", "ss.q4"]
    assert truth.group == "A" and all(q.group == "A" and q.source == "corpus"
                                      for q in truth.questions)
    first = truth.questions[0]
    assert first.family == "sample_search"
    assert first.turns[0].query == "Find sample set 3" and first.turns[0].label == "main"
    assert first.turns[0].oracle is None and first.corpus_numbers == [193.0]


def test_the_halves_split_the_kept_list_stably(tmp_path):
    one = _skeleton(tmp_path, group="a", family="sample_search", part=1)
    two = _skeleton(tmp_path, group="a", family="sample_search", part=2)
    assert [q.id for q in one.questions] == ["ss.q3", "ss.q1", "ss.q2"]
    assert [q.id for q in two.questions] == ["ss.q5", "ss.q4"]
    again = _skeleton(tmp_path, group="a", family="sample_search", part=1)
    assert again.model_dump_json() == one.model_dump_json()


def test_group_b_takes_its_own_kept_list_and_marks_rest_routed_lineage(tmp_path):
    truth = _skeleton(tmp_path, group="b", family="lineage_tree")
    assert [q.id for q in truth.questions] == ["tree.native", "tree.rest_a", "pbct.rest_b"]
    flags = {q.id: q.flags for q in truth.questions}
    assert "rest_routed_today" not in flags["tree.native"]
    assert "rest_routed_today" in flags["tree.rest_a"]
    # The endpoint's name, never the UID a criterion asserts inside the path.
    assert [f for f in flags["tree.rest_a"] if f.startswith("rest_endpoint:")] == [
        "rest_endpoint:sample-tree"]
    assert "rest_endpoint:parents_by_child_types" in flags["pbct.rest_b"]
    assert all(q.group == "B" for q in truth.questions)


def test_family_other_takes_group_b_outside_traversal_and_lineage(tmp_path):
    truth = _skeleton(tmp_path, group="b", family="other")
    assert [q.id for q in truth.questions] == ["ss.q7", "report.inv"]


def test_a_multi_turn_variant_or_an_unknown_id_is_refused(tmp_path):
    with pytest.raises(ValueError, match="single-turn"):
        _skeleton(tmp_path, group="a", family="multi")
    with pytest.raises(ValueError):
        _skeleton(tmp_path, group="a", family="no_such_family")


@pytest.mark.parametrize("regex,numbers", [
    (r"\b195\b", [195.0]), ("3,?061", [3061.0]), (r"\b52[59]\b", [525.0, 529.0]),
    (r"\b(420|610)\b", [420.0, 610.0]), ("50,?88[0-9]", []), (r"4,?264\b", [4264.0]),
    (r"(?is)(\b0\b|\bno\b|zero)", [0.0]), (r"(?s)^(?!.*\b[45]\d[,.]\d{3}\b).*$", []),
    (r"1,?084,?754", [1084754.0]),
])
def test_corpus_numbers_are_read_from_the_old_reply_regexes(regex, numbers):
    assert bec.regex_numbers(regex) == numbers


def test_main_skeleton_writes_a_private_file_and_refuses_to_overwrite(tmp_path):
    out = tmp_path / "out" / "a_ss_1.json"
    argv = ["--skeleton", "--selection", str(_selection(tmp_path)), "--group", "a",
            "--family", "sample_search", "--part", "1", "--out", str(out),
            "--corpus", str(_corpus(tmp_path))]
    assert bec.main(argv) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    truth = et.TruthFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert truth.name == "a_ss_1" and len(truth.questions) == 3
    with pytest.raises(SystemExit):
        bec.main(argv)


def test_main_questions_makes_a_ladder_skeleton(tmp_path):
    qfile = tmp_path / "ladder.json"
    qfile.write_text(json.dumps([
        {"id": "attr_organ_exact", "query": "How many tissue samples have Organ equal to Lung?"},
        {"id": "st_tis", "query": "How many tissue samples are there?", "family": "type_only"}]),
        encoding="utf-8")
    out = tmp_path / "a_ladder.json"
    assert bec.main(["--questions", str(qfile), "--source", "ladder", "--group", "a",
                     "--out", str(out)]) == 0
    truth = et.TruthFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert [(q.id, q.source, q.family) for q in truth.questions] == [
        ("attr_organ_exact", "ladder", "ladder"), ("st_tis", "ladder", "type_only")]
    assert truth.questions[0].turns[0].query.startswith("How many tissue samples have Organ")


# ── the cases files ──────────────────────────────────────────────────────────

def _q(qid, family, *, group="A", source="corpus", numbers=(100,), types=("TIS",), flags=(),
       scorable=True, merged_into=None, items=()):
    return et.TruthQuestion(
        id=qid, family=family, group=group, source=source,
        turns=[et.TruthTurn(label="main", query=f"Question {qid}?", reading="r", oracle=None,
                            expected=et.Expected(kind="count", value=numbers[0] if numbers else None,
                                                 required_numbers=list(numbers),
                                                 required_items=list(items),
                                                 sampletypes=list(types)))],
        flags=list(flags), scorable=scorable, merged_into=merged_into,
        exclusion=None if scorable else "no referent")


def _write_truth(directory: Path, name, group, questions):
    directory.mkdir(parents=True, exist_ok=True)
    truth = et.TruthFile(name=name, group=group, questions=questions)
    (directory / f"{name}.json").write_text(truth.model_dump_json(indent=2), encoding="utf-8")


def _group_a_truth(directory: Path):
    _write_truth(directory, "a_sample_search_1", "A", [
        _q("ss.1", "sample_search"), _q("ss.2", "sample_search", flags=["broad_match"]),
        _q("ss.3", "sample_search", items=("TIS-220831FLY-26",)), _q("ss.4", "sample_search"),
        _q("ss.excluded", "sample_search", scorable=False)])
    _write_truth(directory, "a_sample_search_2", "A", [
        _q("ss.5", "sample_search"), _q("ss.6", "sample_search"), _q("ss.7", "sample_search")])
    _write_truth(directory, "a_harmonization", "A", [
        _q("harm.1", "harmonization", types=()), _q("harm.2", "harmonization")])
    _write_truth(directory, "a_vocabulary", "A", [
        _q("voc.1", "vocabulary_resolution"), _q("voc.2", "vocabulary_resolution")])
    _write_truth(directory, "a_ladder", "A", [
        _q("attr_organ_exact", "ladder", source="ladder"),
        _q("st_tis", "ladder", source="ladder"),
        _q("err_empty_text", "ladder", source="ladder", merged_into="ss.1")])
    _write_truth(directory, "a_b2", "A", [
        _q("narrow_eq", "b2", source="b2"), _q("uids_50", "b2", source="b2")])
    # A Group B file in the same directory is ignored by a Group A build.
    _write_truth(directory, "b_other", "B", [_q("b.only", "sample_search", group="B")])


def _build(tmp_path, group, pilot, truth_dir):
    out = tmp_path / f"cases-{group}"
    assert bec.main(["--cases", "--group", group, "--truth", str(truth_dir),
                     "--out-dir", str(out), "--pilot", str(pilot)]) == 0
    return out


def _load(path: Path):
    include, inline = corpus.load_case_file(path)
    return corpus.select_cases(corpus.merged(), include, inline)


def test_each_cases_file_loads_through_the_harness_loaders(tmp_path):
    _group_a_truth(tmp_path / "truth")
    out = _build(tmp_path, "a", 10, tmp_path / "truth")
    for name in ("group-a.json", "pilot-a.json", "rest-a.json"):
        variants = _load(out / name)
        assert variants and all(len(v.turns) == 1 for v in variants)
        assert all("engine_compare" in v.tags and "group:A" in v.tags for v in variants)
        assert stat.S_IMODE(os.stat(out / name).st_mode) == 0o600


def test_pilot_and_rest_partition_the_group_and_skip_merged_and_excluded(tmp_path):
    _group_a_truth(tmp_path / "truth")
    out = _build(tmp_path, "a", 10, tmp_path / "truth")
    group = [v.id for v in _load(out / "group-a.json")]
    pilot = [v.id for v in _load(out / "pilot-a.json")]
    rest = [v.id for v in _load(out / "rest-a.json")]
    assert "err_empty_text" not in group and "ss.excluded" not in group and "b.only" not in group
    assert len(group) == 15
    assert sorted(pilot + rest) == sorted(group) and not set(pilot) & set(rest)


def test_broad_match_questions_run_last(tmp_path):
    _group_a_truth(tmp_path / "truth")
    out = _build(tmp_path, "a", 10, tmp_path / "truth")
    group = [v.id for v in _load(out / "group-a.json")]
    assert group[-1] == "ss.2"
    rest = [v.id for v in _load(out / "rest-a.json")]
    if "ss.2" in rest:
        assert rest[-1] == "ss.2"


def test_blocks_follow_the_original_families(tmp_path):
    _group_a_truth(tmp_path / "truth")
    out = _build(tmp_path, "a", 10, tmp_path / "truth")
    payload = json.loads((out / "group-a.json").read_text(encoding="utf-8"))
    for block in payload["families"].values():
        assert len({v["family"] for v in block["variants"]}) == 1
    variants = _load(out / "group-a.json")
    assert [v.family for v in variants if v.id.startswith("ss.")][0] == "sample_search"


def test_every_criterion_is_engine_neutral(tmp_path):
    _group_a_truth(tmp_path / "truth")
    out = _build(tmp_path, "a", 10, tmp_path / "truth")
    allowed = {("last_reply", "matches_re"), ("entity_sampletype_codes", "contains"),
               ("outcome_observed", "true")}
    by_id = {v.id: v for v in _load(out / "group-a.json")}
    for v in by_id.values():
        crits = v.turns[0].pass_criteria
        assert {(c.field, c.op) for c in crits} <= allowed
        assert ("outcome_observed", "true") in {(c.field, c.op) for c in crits}
    ss1 = by_id["ss.1"].turns[0].pass_criteria
    assert any(c.field == "entity_sampletype_codes" and c.value == "TIS" for c in ss1)
    reply = [c.value for c in ss1 if c.field == "last_reply"]
    assert reply == [et.number_patterns(100).pattern]
    assert not any(c.field == "entity_sampletype_codes"
                   for c in by_id["harm.1"].turns[0].pass_criteria)


def test_group_a_pilot_quotas(tmp_path):
    _group_a_truth(tmp_path / "truth")
    out = _build(tmp_path, "a", 10, tmp_path / "truth")
    pilot = _load(out / "pilot-a.json")
    truth = {q.id: q for q in bec.load_truth_dir(tmp_path / "truth", "A")}
    kinds = [(truth[v.id].source, truth[v.id].family) for v in pilot]
    assert kinds.count(("corpus", "sample_search")) == 5
    assert kinds.count(("corpus", "harmonization")) == 1
    assert kinds.count(("corpus", "vocabulary_resolution")) == 1
    assert sum(1 for s, _ in kinds if s == "ladder") == 2
    assert sum(1 for s, _ in kinds if s == "b2") == 1
    assert "ss.2" not in [v.id for v in pilot]  # a broad question is not piloted when others exist


def test_the_build_is_stable_for_a_fixed_input(tmp_path):
    _group_a_truth(tmp_path / "truth")
    one = _build(tmp_path / "one", "a", 10, tmp_path / "truth")
    two = _build(tmp_path / "two", "a", 10, tmp_path / "truth")
    for name in ("group-a.json", "pilot-a.json", "rest-a.json"):
        assert (one / name).read_bytes() == (two / name).read_bytes()


def _group_b_truth(directory: Path):
    _write_truth(directory, "b_graph_traversal_1", "B", [
        _q(f"graph.{i}", "graph_traversal", group="B") for i in range(1, 6)])
    _write_truth(directory, "b_lineage", "B", [
        _q("tree.native", "lineage_tree", group="B"),
        _q("tree.native2", "lineage_tree", group="B"),
        _q("tree.rest_a", "lineage_tree", group="B",
           flags=["rest_routed_today", "rest_endpoint:sample-tree"]),
        _q("pbct.rest_b", "lineage_tree", group="B",
           flags=["rest_routed_today", "rest_endpoint:parents_by_child_types"]),
        _q("tree.rest_c", "lineage_tree", group="B",
           flags=["rest_routed_today", "rest_endpoint:sample-tree"])])
    _write_truth(directory, "b_other", "B", [
        _q("ss.study", "sample_search", group="B"),
        _q("report.inv", "project_summary_report", group="B"),
        _q("route.one", "engine_routing", group="B")])


def test_group_b_pilot_quotas(tmp_path):
    _group_b_truth(tmp_path / "truth")
    out = _build(tmp_path, "b", 8, tmp_path / "truth")
    pilot = _load(out / "pilot-b.json")
    assert len(pilot) == 8
    truth = {q.id: q for q in bec.load_truth_dir(tmp_path / "truth", "B")}
    fams = [truth[v.id].family for v in pilot]
    assert fams.count("graph_traversal") == 3
    lineage = [truth[v.id] for v in pilot if truth[v.id].family == "lineage_tree"]
    assert sum(1 for q in lineage if "rest_routed_today" not in q.flags) == 1
    rest_routed = [q for q in lineage if "rest_routed_today" in q.flags]
    assert {f for q in rest_routed for f in q.flags if f.startswith("rest_endpoint:")} == {
        "rest_endpoint:sample-tree", "rest_endpoint:parents_by_child_types"}
    assert fams.count("sample_search") == 1 and fams.count("project_summary_report") == 1
    assert all("group:B" in v.tags for v in pilot)


def test_a_short_quota_or_a_duplicate_id_is_refused(tmp_path):
    _write_truth(tmp_path / "truth", "a_x", "A", [_q("ss.1", "sample_search")])
    with pytest.raises(SystemExit):
        bec.main(["--cases", "--group", "a", "--truth", str(tmp_path / "truth"),
                  "--out-dir", str(tmp_path / "o"), "--pilot", "10"])
    _write_truth(tmp_path / "truth", "a_y", "A", [_q("ss.1", "sample_search")])
    with pytest.raises(SystemExit):
        bec.main(["--cases", "--group", "a", "--truth", str(tmp_path / "truth"),
                  "--out-dir", str(tmp_path / "o"), "--pilot", "0"])
