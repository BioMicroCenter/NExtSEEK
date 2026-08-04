"""Per-arm HiBayes CSVs. The column tuples are locked upstream and copied verbatim."""
import csv
import json
import pathlib
import subprocess
import sys

import pytest

from nessie_tests import collect, export, outage
from nessie_tests.bayes_manifest import BayesManifest, BayesPair
from nessie_tests.manifest import NessieManifestEntry


def _entry(vid="a.one", status="passed", cost=None, outaged=False, elapsed=1.5):
    return NessieManifestEntry(id=vid, family="f", tier="full", status=status,
                               cost=cost, elapsed_s=elapsed, outage=outaged)


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- fixtures that build the COLLECTOR's actual output tree -------------------
# `collect.collect` writes `<out>/artifacts/<variant>/<arm>/`, so `artifacts_dir`
# is that `artifacts` directory. Layout per collect.py:400-498.

def _arm_dir(artifacts_dir, vid, arm):
    d = pathlib.Path(artifacts_dir) / vid / arm
    d.mkdir(parents=True, exist_ok=True)
    return d


def _task_json(artifacts_dir, vid, arm, progress, status="completed"):
    (_arm_dir(artifacts_dir, vid, arm) / "task.json").write_text(
        json.dumps({"status": status, "progress": progress}), encoding="utf-8")


def _turns_json(artifacts_dir, vid, arm, progress_per_turn, statuses=None):
    statuses = statuses or ["completed"] * len(progress_per_turn)
    (_arm_dir(artifacts_dir, vid, arm) / "turns.json").write_text(
        json.dumps([{"task_id": f"t{i}", "row": {"progress": p, "status": s}}
                    for i, (p, s) in enumerate(zip(progress_per_turn, statuses))]),
        encoding="utf-8")


def _search(source):
    return {"event": "search_started", "data": {"source": source}}


def _file(path, body=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


ONE_TURN = [{"label": "main", "query": "How many mice?"}]


def _corpus(tmp_path, *, artifact_expected=True, artifact_kind="GEO-xlsx",
            subtype="S", behavior="GenerateArtifact", vid="a.one", ids=None,
            turns=None):
    """A real v2 corpus row, `name` and `turns` included.

    Both are REQUIRED by `e2e.catalog.Variant`, which is what `export_stage_b`
    now loads the question text through. A fixture that omits them is not a
    corpus this harness could ever read.
    """
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps({
        "version": 2,
        "families": {"f": {"variants": [{
            "id": i, "family": "f", "name": i, "status": "active",
            "is_bayesian": True, "turns": turns if turns is not None else ONE_TURN,
            "hibayes_subtype": subtype, "expected_behavior": behavior,
            "artifact_expected": artifact_expected, "artifact_kind": artifact_kind,
        } for i in (ids or [vid])]}},
        "family_defaults": {"f": {}},
    }), encoding="utf-8")
    return p


def _reply(artifacts_dir, vid, arm, reply, status="completed"):
    """A collected task row carrying an answer, the way the endpoint records one."""
    (_arm_dir(artifacts_dir, vid, arm) / "task.json").write_text(
        json.dumps({"status": status, "progress": [],
                    "result": {"reply": reply}}), encoding="utf-8")


# --- the brief's pinned tests -------------------------------------------------

def test_the_locked_column_tuples_match_the_pinned_upstream_copies():
    """Catches OUR drift. It cannot catch upstream's: the repos are separate and
    nothing here reads dmac-assistant. See this module's docstring."""
    assert export.HIBAYES_CSV_COLUMNS == (
        "query_id", "task_family", "task_subtype", "image", "answer_provided",
        "is_error", "timed_out", "runtime_success", "failure_mode",
        "latency_seconds", "cost_usd", "tool_calls_total", "artifact_count", "is_opus")
    assert export.CSV_HEADER_12 == (
        "query_id", "task_family", "query_text", "final_answer", "answer_provided",
        "runtime_success", "failure_mode", "artifact_expected", "artifact_status",
        "artifact_kind", "declared_artifact_count", "expected_behavior")


def test_one_csv_per_arm_because_is_opus_must_be_uniform(tmp_path):
    """dmac-assistant's _validate_consistency check #6 requires is_opus uniform
    within a file, and an NS turn is not Opus at all."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", hibayes_subtype="S",
                                       ns=_entry(), cc=_entry(cost=0.2))])
    export.export(m, tmp_path)
    ns = _rows(tmp_path / "hibayes_eval_rows_ns.csv")
    cc = _rows(tmp_path / "hibayes_eval_rows_cc.csv")
    assert {r["is_opus"] for r in ns} == {"0"}
    assert {r["is_opus"] for r in cc} == {"1"}
    assert ns[0]["image"] == "nextseek_query" and cc[0]["image"] == "container_cc"


def test_an_unobserved_cost_is_empty_not_zero(tmp_path):
    """NS emits no total_cost_usd. `0` would be an accounting claim the harness
    cannot support, and cost_summary already refuses to make it."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(cost=None),
                                       cc=_entry(cost=0.25))])
    export.export(m, tmp_path)
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["cost_usd"] == ""
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["cost_usd"] == "0.25"


def test_an_outage_row_is_excluded_not_scored(tmp_path):
    """An outage means the fallback chain died BEFORE the product ran. Scoring it
    as is_error teaches the posterior that Bedrock downtime is CC incapability."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(),
                                       cc=_entry(status="error", outaged=True))])
    export.export(m, tmp_path)
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv") == []
    ex = _rows(tmp_path / "excluded.csv")
    assert len(ex) == 1 and ex[0]["arm"] == "cc"
    assert outage.PROVIDER_OUTAGE_MARKER.split()[0].lower() in ex[0]["reason"].lower()


def test_a_non_outage_error_IS_scored(tmp_path):
    """A dead endpoint is not a provider outage. It still failed."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f",
                                       cc=_entry(status="error", outaged=False))])
    export.export(m, tmp_path)
    rows = _rows(tmp_path / "hibayes_eval_rows_cc.csv")
    assert len(rows) == 1 and rows[0]["is_error"] == "true"
    assert rows[0]["runtime_success"] == "false"
    assert rows[0]["failure_mode"] == "error"


def test_failure_mode_priority_is_timeout_then_error_then_no_answer():
    assert export.failure_mode(answer_provided=False, is_error=True, timed_out=True) == "timeout"
    assert export.failure_mode(answer_provided=False, is_error=True, timed_out=False) == "error"
    assert export.failure_mode(answer_provided=False, is_error=False, timed_out=False) == "no_answer"
    assert export.failure_mode(answer_provided=True, is_error=False, timed_out=False) == "none"


# The outcome variable, once, as literals. Used BOTH for the 14-column row and
# for Stage B's copy of the same three columns: `runtime_success` is derived
# twice in this module, so a table that only ever reaches one of them leaves the
# other free to drift -- and it is the Stage B copy that the LLM grader is fed.
_RUNTIME_CASES = [
    # answered, no error, no timeout -- the only combination that is a success
    ("passed", "", {"answer_provided": True, "is_error": False, "timed_out": False,
                    "runtime_success": True, "failure_mode": "none"}),
    # `failed` means the CRITERIA failed; the engine still answered
    ("failed", "", {"answer_provided": True, "is_error": False, "timed_out": False,
                    "runtime_success": True, "failure_mode": "none"}),
    ("no_assertions", "", {"answer_provided": True, "is_error": False,
                           "timed_out": False, "runtime_success": True,
                           "failure_mode": "none"}),
    ("error", "", {"answer_provided": False, "is_error": True, "timed_out": False,
                   "runtime_success": False, "failure_mode": "error"}),
    # THE CASE THAT SEPARATES `runtime_success` FROM `answer_provided`. A socket
    # TimeoutError out of urlopen leaves the status inside `_ANSWERED` and the
    # exception's text in `reason`, so `answer_provided` is true and the turn
    # still did not succeed.
    ("passed", "TimeOut waiting for query_complete",
     {"answer_provided": True, "is_error": False, "timed_out": True,
      "runtime_success": False, "failure_mode": "timeout"}),
]

# The three column names that appear in BOTH locked tuples.
_SHARED_COLUMNS = ("answer_provided", "runtime_success", "failure_mode")


def _as_csv(value) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


@pytest.mark.parametrize("status, reason, expected", _RUNTIME_CASES)
def test_runtime_success_is_the_conjunction_upstream_validates(status, reason, expected):
    """The study's OUTCOME VARIABLE, pinned against literals.

    This test used to recompute its expectation from the row under test
    (`row["runtime_success"] is (row["answer_provided"] and ...)`), which is an
    identity: reducing the implementation to `answer_provided` alone left the
    whole suite green. A tautology over the one column the posterior is fitted
    to is worse than no test, because it reads as coverage.
    """
    e = _entry(status=status)
    e.reason = reason
    row = export.runtime_row(e, arm="ns", family="f", subtype="S", artifact_count=0)
    assert {k: row[k] for k in expected} == expected


def test_tool_calls_total_is_never_a_false_zero_for_ns(tmp_path):
    """The column is int, not nullable. 0 for NS would be false: NS really does
    issue API and graph calls. It is defined as engine operation count."""
    row = export.runtime_row(
        _entry(), arm="ns", family="f", subtype="S", artifact_count=0,
        engine_ops=3)
    assert row["tool_calls_total"] == 3


# --- tool_calls_total: what it counts, and on which evidence -----------------

def test_engine_ops_counts_search_started_on_both_arms(tmp_path):
    """`search_started` is the ONE progress event both engines emit per external
    operation: NS from the graph/REST steps, CC from every tool_use block. That
    is what makes the column mean the same KIND of thing on both arms."""
    art = tmp_path / "artifacts"
    _task_json(art, "a.one", "ns", [{"event": "agent_started", "data": {}},
                                    _search("neo4j"), _search("rest")])
    _task_json(art, "a.one", "cc", [_search("Bash"), _search("Read"),
                                    _search("Bash"), {"event": "query_complete", "data": {}}])
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(), cc=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["tool_calls_total"] == "2"
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["tool_calls_total"] == "3"


def test_a_thinking_step_is_narration_not_an_engine_operation(tmp_path):
    """translate.py renders every CC thinking/narration block as a
    `search_started`/`search_complete` pair with `source: "thinking"`. Counting
    those would inflate CC by a number NS has no counterpart for, and the column
    would stop comparing engines."""
    art = tmp_path / "artifacts"
    _task_json(art, "a.one", "cc", [_search("thinking"), _search("Bash"),
                                    _search("thinking")])
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["tool_calls_total"] == "1"


def test_engine_ops_sum_across_every_turn_of_a_multi_turn_arm(tmp_path):
    """25 of the 30 multi-turn variants are refine_and_recall, whose whole subject
    is the follow-up. `task.json` is the LAST turn only, so counting from it alone
    would under-report every one of them."""
    art = tmp_path / "artifacts"
    _task_json(art, "a.one", "cc", [_search("Bash")])                 # last turn
    _turns_json(art, "a.one", "cc", [[_search("Bash"), _search("Read")],
                                     [_search("Bash")]])
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["tool_calls_total"] == "3"


def test_an_unobservable_engine_op_count_is_reported_not_buried(tmp_path):
    """The column cannot hold null, so an arm with no collected row emits 0 — and
    that 0 is the false one the docstring warns about. The count of rows it
    applies to is returned rather than left for a reader to infer."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(), cc=_entry())])
    out = export.export(m, tmp_path)                    # no artifacts_dir at all
    assert out["engine_ops_unobserved"] == 2
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["tool_calls_total"] == "0"


# --- exclusions ---------------------------------------------------------------

def test_a_skipped_arm_is_excluded_not_scored(tmp_path):
    """`requires_env` unset is the only route to `skipped` at full tier, and it
    returns BEFORE http_driver.drive — the engine was never asked. Scoring it
    `no_answer` says the engine was asked and stayed silent, which is false in
    the same way an outage row is."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(status="skipped"),
                                       cc=_entry())])
    out = export.export(m, tmp_path)
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv") == []
    ex = _rows(tmp_path / "excluded.csv")
    assert [e["arm"] for e in ex] == ["ns"] and "never issued" in ex[0]["reason"]
    assert ex[0]["cause"] == export.CAUSE_NEVER_EXECUTED
    assert out["excluded"] == out["excluded_never_executed"] == 1
    assert out["excluded_outage"] == out["excluded_deadline"] == 0
    assert (out["ns"], out["cc"], out["engine_ops_unobserved"]) == (0, 1, 1)


# --- the deadline abort -------------------------------------------------------

def _pending(tmp_path, *, status="failed", reason=""):
    """A CC arm whose collected row is still `pending`: `http_driver.drive` broke
    on the `full_timeout_s` deadline (http_driver.py:107-108) rather than raising,
    so `run_case` saw no exception, the criteria failed against a missing answer
    and the entry reads `failed`."""
    art = tmp_path / "artifacts"
    _task_json(art, "a.one", "cc", [_search("Bash")], status="pending")
    e = _entry(status=status)
    e.reason = reason
    return art, BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=e)])


def test_a_deadline_abort_is_excluded_not_scored_as_a_success(tmp_path):
    """THE corruption this guard exists for. `full_timeout_s` elapsing is a BREAK,
    not a raise, so nothing reaches `reason` and the entry lands `failed` -- which
    is in `_ANSWERED`. Scored, that arm reads answer_provided=true, timed_out=false,
    runtime_success=true, failure_mode=none: the posterior is taught that an engine
    which never finished answered successfully."""
    art, m = _pending(tmp_path)
    out = export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv") == []
    ex = _rows(tmp_path / "excluded.csv")
    assert len(ex) == 1 and ex[0]["cause"] == export.CAUSE_DEADLINE
    assert out["excluded_deadline"] == 1 and out["cc"] == 0


def test_a_deadline_abort_is_not_filed_as_a_provider_outage(tmp_path):
    """Different causes, different remedies. An outage means the provider chain
    died BEFORE the product ran; a deadline abort means the product ran and did
    not finish. Anyone triaging a run has to tell them apart."""
    art, m = _pending(tmp_path)
    m.pairs[0].ns = _entry(status="error", outaged=True)
    export.export(m, tmp_path, artifacts_dir=art)
    ex = {r["arm"]: r for r in _rows(tmp_path / "excluded.csv")}
    assert ex["cc"]["cause"] == export.CAUSE_DEADLINE
    assert ex["ns"]["cause"] == export.CAUSE_OUTAGE
    assert ex["cc"]["cause"] != ex["ns"]["cause"]
    assert ex["cc"]["reason"] != ex["ns"]["reason"]
    assert outage.PROVIDER_OUTAGE_MARKER.lower() not in ex["cc"]["reason"].lower()


def test_a_deadline_abort_never_reaches_the_grader_either(tmp_path):
    """Excluded means excluded from BOTH files. A row Stage C grades but the
    model never scores is wasted grading; the reverse is worse."""
    art, m = _pending(tmp_path)
    assert export.export_stage_b(m, tmp_path, artifacts_dir=art,
                                 corpus_path=_corpus(tmp_path)) == 0


def test_any_non_terminal_turn_condemns_the_whole_arm(tmp_path):
    """A case is up to 3 turns. If turn 1 never finished, the follow-up was asked
    against a turn that produced nothing, and the case's answer is not the
    engine's answer to the question."""
    art = tmp_path / "artifacts"
    _turns_json(art, "a.one", "cc", [[_search("Bash")], [_search("Bash")]],
                statuses=["pending", "completed"])
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    out = export.export(m, tmp_path, artifacts_dir=art)
    assert out["excluded_deadline"] == 1


def test_a_terminal_row_is_scored_normally(tmp_path):
    """The guard must not swallow the run. `completed` and `error` are terminal."""
    art = tmp_path / "artifacts"
    _task_json(art, "a.one", "cc", [_search("Bash")], status="error")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    out = export.export(m, tmp_path, artifacts_dir=art)
    assert out["excluded_deadline"] == 0 and out["cc"] == 1


@pytest.mark.parametrize("row, why", [
    ({"progress": []}, "the key is absent"),
    ({"status": None, "progress": []}, "the key is present and null"),
    ({"status": "", "progress": []}, "a NULL status column mapped to an empty string"),
    ({"status": "   ", "progress": []}, "whitespace is the same absence in a hat"),
])
def test_a_row_with_no_status_at_all_is_not_evidence_of_a_deadline_abort(
        row, why, tmp_path):
    """Positive evidence only, and `""` is not evidence either.

    The docstring on `_deadline_aborted` has always said so; the code said
    otherwise, and `""` is the obvious shape for a `Sources` that maps a NULL
    MySQL status column straight through. On a reproduced run it excluded 8 of 8
    arms: both eval CSVs and Stage B were written EMPTY, and every arm was
    stamped "the turn blew full_timeout_s" beside `status=passed`.
    """
    art = tmp_path / "artifacts"
    (_arm_dir(art, "a.one", "cc") / "task.json").write_text(
        json.dumps(row), encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    out = export.export(m, tmp_path, artifacts_dir=art)
    assert (out["excluded_deadline"], out["cc"]) == (0, 1), why
    assert export.export_stage_b(m, tmp_path, artifacts_dir=art,
                                 corpus_path=_corpus(tmp_path)) == 1, why


def test_an_outage_outranks_a_deadline_abort(tmp_path):
    """Both are true of the same arm when the provider died and the turn then sat
    pending. The outage is the CAUSE; reporting the symptom would hide it."""
    art, m = _pending(tmp_path)
    m.pairs[0].cc.outage = True
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "excluded.csv")[0]["cause"] == export.CAUSE_OUTAGE


# --- unobserved cells reach disk ---------------------------------------------

def test_an_unobserved_cell_names_its_row_on_disk(tmp_path):
    """`excluded.csv` exists because a count in a return dict identifies nothing.
    A `tool_calls_total` of 0 that means "not collected" sits indistinguishable
    among real counts unless something on disk says which rows they were."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(), cc=_entry())])
    out = export.export(m, tmp_path)
    rows = _rows(tmp_path / "unobserved.csv")
    assert {(r["query_id"], r["arm"], r["column"]) for r in rows} == {
        ("a.one", "ns", "tool_calls_total"), ("a.one", "cc", "tool_calls_total"),
        ("a.one", "ns", "artifact_count"), ("a.one", "cc", "artifact_count")}
    assert {r["emitted"] for r in rows} == {"0"}
    assert out["unobserved_cells"] == 4


def test_a_collected_arm_records_no_unobserved_cell(tmp_path):
    art = tmp_path / "artifacts"
    _task_json(art, "a.one", "cc", [_search("Bash")])
    (_arm_dir(art, "a.one", "cc") / "artifacts.json").write_text(
        json.dumps({"artifacts": [], "cc_raw_files": []}), encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    out = export.export(m, tmp_path, artifacts_dir=art)
    assert out["unobserved_cells"] == 0
    assert _rows(tmp_path / "unobserved.csv") == []
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["artifact_count"] == "0"


def test_the_unobserved_file_is_written_even_when_nothing_was_unobserved(tmp_path):
    export.export(BayesManifest(pairs=[]), tmp_path)
    with open(tmp_path / "unobserved.csv", encoding="utf-8") as fh:
        assert fh.readline().strip() == ",".join(export.UNOBSERVED_COLUMNS)


# --- every NS root the collector wrote ---------------------------------------

def test_a_second_ns_run_root_is_not_invisible(tmp_path):
    """`collect.py:415-416` names an arm's extra roots `run_root_2/`. Reading
    `run_root/` alone exports artifact_count=0 for an arm that produced files."""
    art = tmp_path / "artifacts"
    base = _arm_dir(art, "a.one", "ns")
    _file(base / "run_root" / "files" / "one.xlsx")
    _file(base / "run_root_2" / "files" / "two.csv")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["artifact_count"] == "2"


def test_a_shared_run_root_is_not_reported_as_Missing(tmp_path):
    """When another arm already holds the copy, `collect.py:430-431` writes only a
    `run_root.shared.json` pointer and NO directory. The tree exists; this arm's
    evidence is filed elsewhere. `Missing` would be a false negative."""
    art = tmp_path / "artifacts"
    base = _arm_dir(art, "a.one", "ns")
    (base / "run_root.shared.json").write_text(
        json.dumps({"run_root": "/o/x", "copied_under": "b.two/ns"}), encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    export.export_stage_b(m, tmp_path, artifacts_dir=art, corpus_path=_corpus(tmp_path))
    assert _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0][
        "artifact_status"] == "Indeterminate"


def test_a_shared_run_root_makes_the_count_unobserved_rather_than_guessed(tmp_path):
    """The collector records WHICH arm owns the copy but not which of its
    `run_root*` slots holds it, so this arm's file count cannot be recovered from
    disk. Recorded as unobserved rather than guessed at or silently reported 0."""
    art = tmp_path / "artifacts"
    base = _arm_dir(art, "a.one", "ns")
    (base / "run_root.shared.json").write_text(
        json.dumps({"run_root": "/o/x", "copied_under": "b.two/ns"}), encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    cells = {(r["column"], r["arm"]) for r in _rows(tmp_path / "unobserved.csv")}
    assert ("artifact_count", "ns") in cells


def test_the_never_executed_vocabulary_is_manifests_and_not_a_second_copy():
    """manifest.py:150 already owns the statuses that mean the harness never
    issued a request. Two copies is the duplication the outage rule forbids."""
    from nessie_tests import manifest
    assert export._NEVER_EXECUTED is manifest._NEVER_EXECUTED


def test_the_exclusions_file_is_written_even_when_nothing_was_excluded(tmp_path):
    """A run with no exclusions must still produce the file with its header. A
    missing file and an empty one read the same to a careless consumer; only one
    of them proves the export ran."""
    export.export(BayesManifest(pairs=[]), tmp_path)
    with open(tmp_path / "excluded.csv", encoding="utf-8") as fh:
        assert fh.readline().strip() == ",".join(export.EXCLUDED_COLUMNS)


def test_a_half_written_pair_exports_the_arm_it_has(tmp_path):
    """Either arm may be None: pairs are written as they complete so --resume
    works. A missing arm is not a failed one."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(), cc=None)])
    out = export.export(m, tmp_path)
    assert out["ns"] == 1 and out["cc"] == 0
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv") == []


# --- the header on disk -------------------------------------------------------

def test_the_header_written_to_disk_is_the_locked_order(tmp_path):
    export.export(BayesManifest(pairs=[]), tmp_path)
    for arm in ("ns", "cc"):
        with open(tmp_path / f"hibayes_eval_rows_{arm}.csv", encoding="utf-8") as fh:
            assert fh.readline().strip() == ",".join(export.HIBAYES_CSV_COLUMNS)


def test_the_paired_design_keeps_ONE_query_id_across_the_two_arms(tmp_path):
    """`image` is the discriminator, not the id. The two files concatenate, and a
    paired model pairs on query_id — mangling it per arm would unpair the design
    the whole run exists to produce."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(), cc=_entry())])
    export.export(m, tmp_path)
    assert (_rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["query_id"]
            == _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["query_id"] == "a.one")


# --- artifact_status: which directory, and what an empty one means ------------

def test_artifact_status_reads_cc_artifacts_because_cc_scratch_is_always_empty(tmp_path):
    """`<scratch_mnt>/<run_id>` is created per turn but is not where the agent
    works: working_dir is /home/user (cc_engine.py:137) and the plugin writes to
    the per-USER /data/scratch (:128). Published deliverables exist only under
    output/artifacts/<turn_id> (:931). Pointing at cc_scratch reads Missing for
    every CC arm in the run."""
    art = tmp_path / "artifacts"
    arm = _arm_dir(art, "a.one", "cc")
    (arm / "cc_scratch" / "t0").mkdir(parents=True)          # copied, and empty
    _file(arm / "cc_artifacts" / "t0" / "upload.xlsx")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    export.export_stage_b(m, tmp_path, artifacts_dir=art, corpus_path=_corpus(tmp_path))
    row = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]
    assert row["artifact_status"] == "Indeterminate"


def test_an_empty_artifact_directory_is_Missing_not_Indeterminate(tmp_path):
    """Both arms create their directory unconditionally — `_ensure_query_log_dir`
    mkdirs `<run_root>/files` on every NS turn, and the collector copies an empty
    cc_scratch happily. Presence of a directory is not presence of an artifact."""
    art = tmp_path / "artifacts"
    (_arm_dir(art, "a.one", "ns") / "run_root" / "files").mkdir(parents=True)
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    export.export_stage_b(m, tmp_path, artifacts_dir=art, corpus_path=_corpus(tmp_path))
    assert _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]["artifact_status"] == "Missing"


def test_artifact_status_vocabulary():
    p = pathlib.Path("/definitely/not/here")
    assert export.artifact_status(expected=False, path=p) == "NotExpected"
    assert export.artifact_status(expected=True, path=None) == "Missing"
    assert export.artifact_status(expected=True, path=p) == "Missing"


def test_artifact_status_is_Indeterminate_and_never_claims_the_artifact_is_VALID(tmp_path):
    """Presence and readability ONLY. Schema validation (the GEO xlsx template
    parity dmac-assistant's artifact_validator.py does) is deferred, so anything
    it would have judged is emitted Indeterminate rather than guessed at."""
    f = tmp_path / "a.xlsx"
    _file(f)
    assert export.artifact_status(expected=True, path=f) == "Indeterminate"


def test_an_unreadable_artifact_is_Unreadable_not_Missing(tmp_path):
    d = tmp_path / "locked"
    _file(d / "x.bin")
    d.chmod(0o000)
    try:
        status = export.artifact_status(expected=True, path=d)
    finally:
        d.chmod(0o755)
    if status == "Indeterminate":
        pytest.skip("running as root: an unreadable directory is not reachable")
    assert status == "Unreadable"


# --- artifact_count -----------------------------------------------------------

def test_cc_artifact_count_is_the_published_artifact_list(tmp_path):
    art = tmp_path / "artifacts"
    (_arm_dir(art, "a.one", "cc") / "artifacts.json").write_text(
        json.dumps({"artifacts": ["a/x.xlsx", "a/y.csv"], "cc_raw_files": []}),
        encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["artifact_count"] == "2"


def test_ns_artifact_count_counts_files_not_the_directories_holding_them(tmp_path):
    """`rglob("*")` yields directories too. Counting them turns one file in one
    subdirectory into an artifact_count of 2."""
    art = tmp_path / "artifacts"
    files = _arm_dir(art, "a.one", "ns") / "run_root" / "files"
    _file(files / "sub" / "one.xlsx")
    _file(files / "two.csv")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    export.export(m, tmp_path, artifacts_dir=art)
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["artifact_count"] == "2"


# --- Stage B ------------------------------------------------------------------

def test_stage_b_is_one_file_covering_both_arms(tmp_path):
    """Stage C grades an ANSWER; which engine produced it is not part of that
    judgement, and telling the grader would invite it to grade the engine."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(), cc=_entry())])
    n = export.export_stage_b(m, tmp_path, corpus_path=_corpus(tmp_path))
    rows = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")
    assert n == 2
    assert [r["query_id"] for r in rows] == ["a.one::ns", "a.one::cc"]
    assert {r["task_family"] for r in rows} == {"f"}
    assert "image" not in rows[0] and "is_opus" not in rows[0]


@pytest.mark.parametrize("vid", [
    "cons.nhp_sequencing_engine",
    # Nothing forbids a variant id containing the separator, which is the whole
    # reason the split is a `rpartition`. Under `partition` this id round-trips
    # to ("weird", "held::cc") -- a pair id no manifest holds and an arm no CSV
    # has, so `merge_grades` reports the row ungraded and refuses the table.
    "weird::held",
])
def test_a_stage_b_query_id_round_trips_to_its_pair_and_arm(vid):
    """Task 4 joins Stage C's grades back onto the 14-column rows through this."""
    qid = export.stage_b_query_id(vid, "cc")
    assert export.split_stage_b_query_id(qid) == (vid, "cc")


def test_stage_b_excludes_exactly_what_the_runtime_export_excludes(tmp_path):
    """EXACTLY: the same rows, not merely no more than the same rows.

    Asserting `== 0` over an all-excluded manifest -- which this test used to do
    -- passes just as happily when Stage B drops arms the runtime export scored,
    and an over-excluding Stage B is the same defect as an under-excluding one:
    `merge_grades` then names a row the page offered no controls for.
    """
    m = BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry(status="skipped"),
                  cc=_entry(status="error", outaged=True)),
        BayesPair(id="a.two", family="f", ns=_entry("a.two"), cc=_entry("a.two")),
    ])
    corpus_path = _corpus(tmp_path, ids=["a.one", "a.two"])

    n = export.export_stage_b(m, tmp_path, corpus_path=corpus_path)
    export.export(m, tmp_path, corpus_path=corpus_path)

    stage_b = {r["query_id"] for r in _rows(tmp_path / "hibayes_functional_eval_inputs.csv")}
    runtime = {export.stage_b_query_id(r["query_id"], arm) for arm in export.ARMS
               for r in _rows(tmp_path / f"hibayes_eval_rows_{arm}.csv")}
    assert stage_b == runtime == {"a.two::ns", "a.two::cc"}
    assert n == 2


@pytest.mark.parametrize("status, reason, expected", _RUNTIME_CASES)
def test_the_two_files_never_disagree_about_the_same_column(
        status, reason, expected, tmp_path):
    """`runtime_success`, `answer_provided` and `failure_mode` appear in BOTH
    locked tuples. Two derivations of one column name is the drift this codebase
    refuses everywhere else.

    `export_stage_b` restates the `runtime_success` conjunction rather than
    calling `runtime_row`, so it is a SECOND copy -- and this test ran one entry
    (`no_assertions`) whose three columns are the same under every plausible
    mutation, so reducing the Stage B copy to `answer_provided` left the whole
    suite green. That copy is the one the LLM grader is fed.

    So: every case in `_RUNTIME_CASES`, both files, and against the LITERALS
    rather than against each other -- two copies that drift TOGETHER would
    satisfy an equality check while still teaching the posterior the wrong thing.
    """
    e = _entry(status=status)
    e.reason = reason
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=e)])
    corpus_path = _corpus(tmp_path)

    export.export(m, tmp_path, corpus_path=corpus_path)
    export.export_stage_b(m, tmp_path, corpus_path=corpus_path)

    a = _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]
    b = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]
    for col in _SHARED_COLUMNS:
        assert a[col] == b[col] == _as_csv(expected[col]), col


def test_stage_b_carries_the_corpus_metadata_the_grader_is_given(tmp_path):
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    export.export_stage_b(m, tmp_path, corpus_path=_corpus(
        tmp_path, artifact_expected=True, artifact_kind="GEO-xlsx",
        behavior="GenerateArtifact"))
    row = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]
    assert row["artifact_expected"] == "true"
    assert row["artifact_kind"] == "GEO-xlsx"
    assert row["expected_behavior"] == "GenerateArtifact"


# --- the two columns Stage C actually grades on ------------------------------
# These were emitted EMPTY on every row and nothing on the branch ever filled
# them, so the LLM grader was handed 254 blank answers: `functional_evaluator.py`
# maps `""` to None, the BAML template renders an empty `final_answer`, the
# verdicts join without error, and the disagreement set -- the whole output of
# the paired design -- is noise. The old test here pinned the emptiness.

def test_a_fully_collected_arm_carries_the_answer_stage_c_grades(tmp_path):
    """THE finding, as the two columns on disk."""
    art = tmp_path / "artifacts"
    _reply(art, "a.one", "ns", "There are 705 mice.")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])

    export.export_stage_b(m, tmp_path, artifacts_dir=art,
                          corpus_path=_corpus(tmp_path))

    row = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]
    assert row["final_answer"] == "There are 705 mice."
    assert row["query_text"] == "How many mice?"


def test_the_answer_is_the_last_turns_and_the_question_is_the_conversation(tmp_path):
    """A `refine_and_recall` arm: the follow-up's answer is the one under grade,
    and the grader is shown every turn that led to it -- a bare "and only the
    females?" has nothing to resolve against, while the human grading the same
    row reads the whole card."""
    art = tmp_path / "artifacts"
    (_arm_dir(art, "a.one", "cc") / "turns.json").write_text(json.dumps([
        {"task_id": "t1", "row": {"status": "completed", "progress": [],
                                  "result": {"reply": "705 mice."}}},
        {"task_id": "t2", "row": {"status": "completed", "progress": [],
                                  "result": {"reply": "412 of them are female."}}},
    ]), encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry())])

    export.export_stage_b(m, tmp_path, artifacts_dir=art, corpus_path=_corpus(
        tmp_path, turns=[{"label": "main", "query": "How many mice?"},
                         {"label": "refine", "query": "And only the females?"}]))

    row = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]
    assert row["final_answer"] == "412 of them are female."
    assert row["query_text"] == ("turn 1 (main): How many mice?\n"
                                 "turn 2 (refine): And only the females?")


def test_an_arm_with_no_reply_is_blank_in_ONE_column_not_two(tmp_path):
    """What an empty `final_answer` is allowed to mean.

    It means this arm's row carried no reply. It must not be able to mean "the
    column was never wired up", and the two are told apart structurally rather
    than by trust: `query_text` comes from the corpus, which RAISES on a variant
    it does not hold, so no row can be blank in both columns.
    """
    art = tmp_path / "artifacts"
    (_arm_dir(art, "a.one", "ns") / "task.json").write_text(
        json.dumps({"status": "completed", "progress": [], "result": None}),
        encoding="utf-8")
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    stats: dict = {}

    export.export_stage_b(m, tmp_path, artifacts_dir=art,
                          corpus_path=_corpus(tmp_path), stats=stats)

    row = _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0]
    assert row["final_answer"] == ""
    assert row["query_text"] == "How many mice?"
    assert stats == {"rows": 1, "no_reply": 1}


def test_the_reply_is_recovered_from_query_complete_when_the_result_never_landed(tmp_path):
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry())])
    art = tmp_path / "artifacts"
    (_arm_dir(art, "a.one", "ns") / "task.json").write_text(json.dumps(
        {"status": "completed", "result": None,
         "progress": [{"event": "query_complete", "data": {"reply": "705 mice."}}]}),
        encoding="utf-8")

    export.export_stage_b(m, tmp_path, artifacts_dir=art,
                          corpus_path=_corpus(tmp_path))

    assert _rows(tmp_path / "hibayes_functional_eval_inputs.csv")[0][
        "final_answer"] == "705 mice."


def test_the_operator_is_told_how_many_rows_have_no_answer_to_grade(tmp_path, capsys):
    """The count reaches the terminal on the line before the operator pays for a
    grading pass over it. A run whose Stage B is answer-less costs exactly as
    much to grade as one that is not."""
    run = _run_dir(tmp_path)
    assert export.main(["--run", str(run), "--corpus", str(_corpus(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "2 row(s), 2 with NO answer to grade" in out


def test_a_variant_missing_from_the_corpus_names_itself(tmp_path):
    """The manifest and the corpus disagree. That is a fact about the whole
    export, not one row, so it raises — but it must say which id."""
    m = BayesManifest(pairs=[BayesPair(id="ghost.id", family="f", ns=_entry())])
    with pytest.raises(KeyError, match="ghost.id"):
        export.export_stage_b(m, tmp_path, corpus_path=_corpus(tmp_path))


def test_a_timeout_reason_outranks_the_error_status(tmp_path):
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=_entry(status="error"))])
    m.pairs[0].cc.reason = "TimeOut waiting for query_complete"
    export.export(m, tmp_path)
    row = _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]
    assert row["timed_out"] == "true" and row["failure_mode"] == "timeout"


# --------------------------------------------------------------------------- #
# The command SKILL.md names. This module had no `main` and no `__main__` block
# at all, so the documented `python -m nessie_tests.export --run <dir>` exited 0,
# printed nothing and wrote nothing -- and the operator found out four steps
# later, after grading 254 blank arms, when `merge_grades` raised
# FileNotFoundError over the CSVs this step was supposed to write.
# --------------------------------------------------------------------------- #

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _run_dir(tmp_path, *, pairs=None, artifacts=True):
    from nessie_tests import bayes_manifest

    run = tmp_path / "run"
    run.mkdir()
    m = BayesManifest(run_meta={"mode": "bayesian"},
                      pairs=pairs if pairs is not None else
                      [BayesPair(id="a.one", family="f", hibayes_subtype="S",
                                 ns=_entry(), cc=_entry())])
    bayes_manifest.write_bayes_manifest(m, run)
    if artifacts:
        collect.artifacts_dir(run).mkdir()
    return run


def test_the_documented_export_command_actually_writes_the_csvs(tmp_path):
    """The whole finding, as the operator's own command line. Run as a
    SUBPROCESS through `-m`, because that is the form SKILL.md prints and a
    module with no `__main__` block satisfies an in-process `main()` test while
    still exiting 0 and doing nothing from the shell."""
    run = _run_dir(tmp_path)

    proc = subprocess.run([sys.executable, "-m", "nessie_tests.export",
                           "--run", str(run), "--corpus", str(_corpus(tmp_path))],
                          cwd=str(ROOT), capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    for name in ("hibayes_eval_rows_ns.csv", "hibayes_eval_rows_cc.csv",
                 "hibayes_functional_eval_inputs.csv", "excluded.csv",
                 "unobserved.csv"):
        assert (run / name).is_file(), f"{name} was not written"
    assert _rows(run / "hibayes_eval_rows_ns.csv")[0]["query_id"] == "a.one"
    # It also SAYS what it did. A silent success is how the no-op survived.
    assert "hibayes_eval_rows_ns.csv" in proc.stdout


def test_the_export_cli_reads_the_collected_tree_the_report_reads(tmp_path):
    """`artifacts_dir` is DERIVED, not defaulted to None. The deadline abort below
    is visible only in the collected task row, so a CLI that passed no tree would
    score this arm as a success -- while the report, which does read the tree,
    bands it ungradable and gives the operator no way to grade it."""
    run = _run_dir(tmp_path)
    _task_json(collect.artifacts_dir(run), "a.one", "cc", [], status="pending")

    assert export.main(["--run", str(run), "--corpus", str(_corpus(tmp_path))]) == 0

    assert _rows(run / "hibayes_eval_rows_cc.csv") == []
    excluded = _rows(run / "excluded.csv")
    assert [(e["arm"], e["cause"]) for e in excluded] == [("cc", export.CAUSE_DEADLINE)]


def test_an_export_with_no_collected_tree_says_the_run_is_degraded(tmp_path, capsys):
    """Collection is not runnable yet, so exporting without an artifacts tree is
    supported -- but it cannot see a deadline abort at all, and reading the result
    as a measured run is the mistake this warning exists to prevent."""
    run = _run_dir(tmp_path, artifacts=False)

    assert export.main(["--run", str(run), "--corpus", str(_corpus(tmp_path))]) == 0

    err = capsys.readouterr().err
    assert str(collect.artifacts_dir(run)) in err
    assert export.CAUSE_DEADLINE in err


def test_the_export_cli_refuses_a_normal_runs_manifest(tmp_path):
    """The same collision the report builder refuses. A normal run's
    `manifest.json` validates as an EMPTY paired manifest, so reading it here
    would write four empty CSVs and call the run exported."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(
        {"started_at": "t0", "ended_at": "t1", "tier": "full", "scope": "all",
         "entries": []}), encoding="utf-8")

    proc = subprocess.run([sys.executable, "-m", "nessie_tests.export",
                           "--run", str(run)],
                          cwd=str(ROOT), capture_output=True, text=True)

    assert proc.returncode != 0
    assert "bayes_manifest.json" in proc.stderr
    assert not (run / "hibayes_eval_rows_ns.csv").exists()


def test_the_export_cli_names_the_manifest_through_the_constant():
    """A filename literal would survive a rename of the constant and reintroduce
    the collision above."""
    from nessie_tests import bayes_manifest

    src = (ROOT / "nessie_tests" / "export.py").read_text(encoding="utf-8")

    assert bayes_manifest.MANIFEST_NAME not in src
    assert "MANIFEST_NAME" in src
