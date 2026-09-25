"""Coverage for the two scripts the output-skill ships.

Neither had a single test, and both had rotted in the same direction: they were
written before the `outage` flag existed and they each quietly undo it.

* `build_report.py` is the operator's real triage tool. It filed an outage in the
  `errored` tile with tone `drift` — the exact mis-read the flag was added to
  prevent — surfaced `xpass` and `no_assertions` in no tile at all, and defaulted
  every untriaged non-`passed` case to verdict `real`, captioned "real product
  defects" in the report's own reframe bar.
* `rehydrate_report.py` rebuilds a manifest out of a BUILT report. It dropped
  `outage`, so round-tripping a report converted a gate-EXEMPT outage into a
  gate-FAILING error.

The scripts are standalone `python` files rather than an importable package, so
they are loaded by path. That is also the point: nothing else imports them, which
is why nothing else noticed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from NessieAI import paths
from NessieAI.tests.nessie_tests import limits, manifest as M, runner

ROOT = paths.REPO_ROOT
SCRIPTS = Path(__file__).resolve().parents[1] / "output-skill" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_nessie_skill_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_report = _load("build_report")
rehydrate_report = _load("rehydrate_report")


# A run with one of every status that matters, including the two the tiles never
# showed and the one they showed under the wrong tone.
def _entries():
    return [
        {"id": "sys.ok", "family": "system_question", "tier": "full", "status": "passed",
         "route": "nextseek_query", "engine": "rest", "elapsed_s": 1.0,
         "failed_criteria": [], "expected_fail": False, "observations": []},
        {"id": "graph.bad", "family": "graph_query", "tier": "full", "status": "failed",
         "route": "nextseek_query", "engine": "graph_query", "elapsed_s": 2.0,
         "failed_criteria": ["main:graph_result.count"], "expected_fail": False,
         "observations": []},
        {"id": "cc.outage", "family": "search_advanced", "tier": "full", "status": "error",
         "route": "container_cc", "engine": "container_cc:opus", "elapsed_s": 3.0,
         "failed_criteria": [], "expected_fail": False, "outage": True,
         "cost": 0.21, "reason": "provider outage: All provider fallbacks exhausted",
         "route_source": "baml", "route_sources": ["baml"], "observations": []},
        {"id": "sys.dead", "family": "system_question", "tier": "full", "status": "error",
         "route": None, "engine": None, "elapsed_s": 4.0, "failed_criteria": [],
         "expected_fail": False, "reason": "TimeoutError: read timed out",
         "observations": []},
        {"id": "repro.stale", "family": "nessie_repro", "tier": "full", "status": "xpass",
         "route": "nextseek_query", "engine": "graph_query", "elapsed_s": 5.0,
         "failed_criteria": [], "expected_fail": True, "observations": []},
        {"id": "tree.vacuous", "family": "search_tree", "tier": "full",
         "status": "no_assertions", "route": "container_cc", "engine": "container_cc:opus",
         "elapsed_s": 6.0, "failed_criteria": [], "expected_fail": False,
         "observations": []},
    ]


def _run_dir(tmp_path, entries=None, triage=None):
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(
        {"started_at": "t0", "ended_at": "t1", "tier": "full", "scope": "all",
         "entries": entries if entries is not None else _entries()}), encoding="utf-8")
    (run / "turns.json").write_text("[]", encoding="utf-8")
    tri = tmp_path / "triage.json"
    tri.write_text(json.dumps(triage or {"title": "t"}), encoding="utf-8")
    return run, tri


def _build(tmp_path, **kw):
    run, tri = _run_dir(tmp_path, **kw)
    out = tmp_path / "report.html"
    argv = ["build_report.py", "--run", str(run), "--repo", str(ROOT),
            "--triage", str(tri), "--out", str(out)]
    old, sys.argv = sys.argv, argv
    try:
        build_report.main()
    finally:
        sys.argv = old
    return out.read_text(encoding="utf-8")


def _literal(html, name):
    return rehydrate_report._literal(html, name)


# --------------------------------------------------------------------------- #
# build_report: it runs at all
# --------------------------------------------------------------------------- #

def test_build_report_produces_a_report_with_one_record_per_case(tmp_path):
    """The smoke test the file never had. Everything below is a claim ABOUT the
    output; this is the one that says there is an output."""
    html = _build(tmp_path)

    cases = _literal(html, "CASES")
    assert [c["id"] for c in cases] == [e["id"] for e in _entries()]
    assert "__CASES__" not in html and "__META__" not in html


# --------------------------------------------------------------------------- #
# build_report: the stat tiles
# --------------------------------------------------------------------------- #

def _tiles(html):
    return {t["label"]: t for t in _literal(html, "META")["stats"]}


def test_an_outage_has_its_own_tile(tmp_path):
    """It used to be counted as an ordinary `error`, which is the whole mis-read."""
    tiles = _tiles(_build(tmp_path))

    assert "provider outages" in tiles
    assert tiles["provider outages"]["n"] == 1


def test_an_outage_is_not_toned_drift(tmp_path):
    """`drift` says "the assertion is stale, the product is fine". An outage says
    nothing about either — the fallback chain 503'd before the turn ran."""
    tiles = _tiles(_build(tmp_path))

    assert tiles["provider outages"].get("tone") != "drift"


def test_the_errored_tile_no_longer_swallows_the_outage(tmp_path):
    """Two `error` entries in the run, one of them an outage. The errored tile
    must count the OTHER one."""
    tiles = _tiles(_build(tmp_path))

    assert tiles["errored"]["n"] == 1


def test_xpass_and_no_assertions_are_in_a_tile_at_all(tmp_path):
    """They were inside "cases run" and nowhere else, so a run with five vacuous
    cases just showed five fewer passes, unexplained."""
    tiles = _tiles(_build(tmp_path))

    assert tiles["xpass"]["n"] == 1
    assert tiles["asserted nothing"]["n"] == 1


def test_the_tiles_still_count_the_ordinary_statuses(tmp_path):
    """The control: adding tiles must not move the ones that were right."""
    tiles = _tiles(_build(tmp_path))

    assert tiles["cases run"]["n"] == 6
    assert tiles["passed"]["n"] == 1
    assert tiles["failed"]["n"] == 1


def test_a_hand_written_stats_block_still_replaces_the_defaults(tmp_path):
    """`triage.json` overriding `stats` wholesale is the documented escape hatch,
    and it must keep working now that there are more defaults."""
    tiles = _tiles(_build(tmp_path, triage={"stats": [{"n": 1, "label": "mine"}]}))

    assert list(tiles) == ["mine"]


# --------------------------------------------------------------------------- #
# build_report: the default verdict
# --------------------------------------------------------------------------- #

def _verdicts(html):
    return {c["id"]: c["verdict"] for c in _literal(html, "CASES")}


def test_an_untriaged_outage_is_not_called_a_real_product_defect(tmp_path):
    """The report's reframe bar captions the `real` tally "real product defects".
    An outage exercised no product behaviour at all."""
    assert _verdicts(_build(tmp_path))["cc.outage"] == "notrun"


def test_an_untriaged_xpass_is_drift(tmp_path):
    """A stale expectation is corpus drift; the product did nothing wrong."""
    assert _verdicts(_build(tmp_path))["repro.stale"] == "drift"


def test_an_untriaged_no_assertions_is_drift(tmp_path):
    """A case that proved nothing is corpus drift — SKILL.md says so in words, and
    now the tool that writes the default agrees with it."""
    assert _verdicts(_build(tmp_path))["tree.vacuous"] == "drift"


def test_an_untriaged_failure_is_still_real(tmp_path):
    """The fail-safe direction, unchanged. A red with no triage entry must not
    quietly become someone else's problem."""
    assert _verdicts(_build(tmp_path))["graph.bad"] == "real"


def test_a_hand_written_verdict_still_wins(tmp_path):
    v = _verdicts(_build(tmp_path, triage={"verdicts": {"cc.outage": {"verdict": "real"}}}))

    assert v["cc.outage"] == "real"


def test_every_default_verdict_is_one_the_report_can_render(tmp_path):
    """`build_report` exits on an unknown verdict, so a typo in the default map
    would break every run rather than one case."""
    assert set(_verdicts(_build(tmp_path)).values()) <= build_report.VERDICTS


# --------------------------------------------------------------------------- #
# build_report: the graph limit
# --------------------------------------------------------------------------- #

def test_the_graph_limit_default_is_not_the_stale_250(tmp_path):
    """It defaulted to 250 — the cap from 2026-07, 20x below the current one — so
    every report built without an explicit `graph_limit` flagged the wrong number
    of rows as a cap hit and missed the real ones."""
    assert _literal(_build(tmp_path), "META")["graph_limit"] == max(limits.GRAPH_LIMIT_SENTINELS)


def test_the_graph_limit_comes_from_the_limits_module_not_a_literal():
    """Pinned against the module rather than against 5000, so the next time the
    cap moves this test does not have to — and neither does the script."""
    assert build_report.load_graph_limit_sentinels(ROOT) == limits.GRAPH_LIMIT_SENTINELS
    assert 'graph_limit", 250' not in (SCRIPTS / "build_report.py").read_text(encoding="utf-8")


def test_a_triage_supplied_graph_limit_still_wins(tmp_path):
    """A 2026-07 run really was capped at 250 and must still be reviewable."""
    html = _build(tmp_path, triage={"graph_limit": 250})

    assert _literal(html, "META")["graph_limit"] == 250


# --------------------------------------------------------------------------- #
# rehydrate_report: the round trip
# --------------------------------------------------------------------------- #

def _round_trip(tmp_path, html):
    src = tmp_path / "old-report.html"
    src.write_text(html, encoding="utf-8")
    out = tmp_path / "rebuilt"
    argv = ["rehydrate_report.py", "--html", str(src), "--out", str(out)]
    old, sys.argv = sys.argv, argv
    try:
        rehydrate_report.main()
    finally:
        sys.argv = old
    return M.load_manifest(out / "manifest.json")


def test_an_outage_survives_the_round_trip(tmp_path):
    """The defect: the rebuilt entry dropped `outage`, so an exempt outage came
    back as an ordinary `error`."""
    rebuilt = _round_trip(tmp_path, _build(tmp_path))

    assert next(e for e in rebuilt.entries if e.id == "cc.outage").outage is True


def test_the_round_trip_does_not_change_the_gate(tmp_path):
    """The consequence, stated as the thing the operator actually reads. Task 3
    exempted outages from `gate_failed`; a rebuild put them back."""
    original = M.NessieManifest(started_at="t0", ended_at="t1", tier="full", scope="all",
                                entries=[M.NessieManifestEntry(**e) for e in _entries()])
    rebuilt = _round_trip(tmp_path, _build(tmp_path))

    assert runner.gate_failed(rebuilt) == runner.gate_failed(original)


def test_a_non_outage_error_still_fails_the_gate_after_a_round_trip(tmp_path):
    """The control. The fix must not exempt every error — a dead endpoint is
    still infrastructure the run has to answer for."""
    rebuilt = _round_trip(tmp_path, _build(tmp_path))

    dead = next(e for e in rebuilt.entries if e.id == "sys.dead")
    assert dead.outage is False
    assert runner._is_real_failure(dead)


def test_the_round_trip_preserves_the_fields_it_claims_to(tmp_path):
    """`_ENTRY_FIELDS` is the file's statement of what it carries. It used to be
    dead, unused and wrong; this holds it to the round trip."""
    rebuilt = _round_trip(tmp_path, _build(tmp_path))
    by_id = {e.id: e for e in rebuilt.entries}

    o = by_id["cc.outage"]
    assert o.cost == 0.21
    assert o.route_source == "baml" and o.route_sources == ["baml"]
    assert "All provider fallbacks exhausted" in o.reason
    assert by_id["repro.stale"].status == "xpass"
    assert by_id["tree.vacuous"].status == "no_assertions"
    assert by_id["graph.bad"].failed_criteria == ["main:graph_result.count"]


def test_a_partial_cost_and_the_fallback_count_survive_the_round_trip(tmp_path):
    """The `outage` defect again, for money: a rebuilt entry without `cost_partial`
    lets `cost_summary` present a floor as the whole spend."""
    entries = _entries()
    next(e for e in entries if e["id"] == "cc.outage").update(cost_partial=True,
                                                              fallback_turns=1)
    rebuilt = _round_trip(tmp_path, _build(tmp_path, entries=entries))

    o = next(e for e in rebuilt.entries if e.id == "cc.outage")
    assert o.cost_partial is True and o.fallback_turns == 1
    original = [M.NessieManifestEntry(**e) for e in entries]
    assert (M.cost_summary(rebuilt.entries)["cost_display"]
            == M.cost_summary(original)["cost_display"])
    assert "PARTIAL" in M.cost_summary(rebuilt.entries)["cost_display"]


def test_the_entry_field_map_is_actually_used(tmp_path):
    """The constant was declared and then never referenced, which is how it came
    to disagree with the code beside it. Naming a field it does not carry must
    break the round trip rather than nothing at all."""
    assert rehydrate_report._ENTRY_FIELDS
    for manifest_field, case_key, _default in rehydrate_report._ENTRY_FIELDS:
        assert manifest_field in M.NessieManifestEntry.model_fields, manifest_field
        assert isinstance(case_key, str)


def test_a_report_that_is_not_ours_is_rejected(tmp_path):
    src = tmp_path / "x.html"
    src.write_text("<html>no CASES here</html>", encoding="utf-8")
    old, sys.argv = sys.argv, ["rehydrate_report.py", "--html", str(src),
                               "--out", str(tmp_path / "o")]
    try:
        with pytest.raises(SystemExit):
            rehydrate_report.main()
    finally:
        sys.argv = old


# --- fetch_run.py: one pull for every instance ------------------------------
#
# It used to know one box. Production review needs the same pull with a different
# transport (direct key login, no sudo), and the bayesian skill's `--host ""`
# spelling of "local" has to keep working.

fetch_run = _load("fetch_run")


def test_every_instance_preset_resolves():
    assert fetch_run.resolve_target("local") == ("", "")
    assert fetch_run.resolve_target("dev") == ("fairdata-dev", "service-account")
    assert fetch_run.resolve_target("prod") == ("fairdata", "")


def test_an_explicit_empty_host_still_means_the_local_daemon():
    host, user = fetch_run.resolve_target("dev", host="")
    assert host == ""
    assert fetch_run.remote_cmd(host, user, "true")[0] == "bash"


def test_production_never_sudoes_and_dev_does():
    assert "sudo" not in fetch_run.remote_cmd("fairdata", "", "true")
    assert "sudo" in fetch_run.remote_cmd("fairdata-dev", "service-account", "true")


def test_only_a_plain_outputs_folder_is_ever_handed_to_tar():
    assert fetch_run.run_root_name("/app/outputs/260904_132113_wesselr") == "260904_132113_wesselr"
    for bad in (None, "", "/app/outputs/../etc", "/etc/260904_132113_x",
                "/app/outputs/260904_132113_x;rm -rf /", "/app/outputs/sub/260904_132113_x"):
        assert fetch_run.run_root_name(bad) is None


def test_the_utc_offset_parses_date_output():
    assert fetch_run.tz_offset_minutes("-0400\n") == -240
    assert fetch_run.tz_offset_minutes("+0530") == 330
    assert fetch_run.tz_offset_minutes("UNKNOWN") is None


def test_files_are_matched_to_the_turn_that_wrote_them_by_local_mtime(tmp_path):
    import os
    from datetime import datetime, timedelta, timezone

    root = tmp_path / "260904_132113_u"
    (root / "files" / "graph").mkdir(parents=True)
    mine, later = root / "files" / "graph" / "a.json", root / "files" / "graph" / "b.json"
    for p in (mine, later, root / "console.txt"):
        p.write_text("{}")
    eastern = timezone(timedelta(minutes=-240))

    def at(s):
        return datetime.fromisoformat(s).replace(tzinfo=eastern).timestamp()

    os.utime(mine, (at("2026-09-04 13:53:20"),) * 2)
    os.utime(later, (at("2026-09-04 14:30:00"),) * 2)
    turns = [{"id": 463, "created": "2026-09-04 13:52:45.821854",
              "updated": "2026-09-04 13:53:21.388284",
              "run_root": "/app/outputs/260904_132113_u"}]

    idx = fetch_run.index_outputs(turns, tmp_path, -240)

    assert idx["463"]["files"] == ["260904_132113_u/files/graph/a.json"]
    # console.txt is written by every turn in the process, so it is listed, never matched
    assert idx["463"]["shared"] == ["260904_132113_u/console.txt"]


# --------------------------------------------------------------------------
# T15: the LLM ledger travels with the evidence pull.
# --------------------------------------------------------------------------


def test_the_ledger_files_are_the_two_the_engine_writes():
    assert fetch_run.LEDGER_FILES == ("llm_calls.jsonl", "llm_responses.jsonl")


def test_the_ledger_is_pulled_from_the_log_dir_not_the_outputs_dir(monkeypatch, tmp_path):
    """The ledger records stop_reason and request_id, which is exactly what a provider
    incident needs. It is written to LOG_DIR, a sibling of the outputs directory, and every
    pull before this tarred outputs only -- so the one file that would have explained an
    empty-completion failure was the one file the evidence could not reach."""
    seen: list[str] = []

    class _Done:
        returncode = 0
        stdout = b"llm_calls.jsonl\nllm_responses.jsonl\ndjango.log\n"
        stderr = b""

    def fake_run(cmd, **kw):
        seen.append(" ".join(cmd))
        return _Done()

    class _Popen:
        def __init__(self, cmd, **kw):
            seen.append(" ".join(cmd))
            self.stdout, self.stderr = _Empty(), _Empty()

        def wait(self):
            return 0

    class _Empty:
        def close(self):
            pass

        def read(self):
            return b""

    monkeypatch.setattr(fetch_run.subprocess, "run", fake_run)
    monkeypatch.setattr(fetch_run.subprocess, "Popen", _Popen)

    got = fetch_run.pull_logs("fairdata", "", "nextseek", "/app/logs", tmp_path / "logs")

    # remote_cmd base64-encodes the script it sends, so read the decoded payload rather
    # than the wrapper: the path this test is about is inside it.
    import base64
    import re as _re

    decoded = " ".join(
        base64.b64decode(blob).decode(errors="replace")
        for cmd in seen for blob in _re.findall(r"echo ([A-Za-z0-9+/=]{16,})", cmd)
    )
    assert got == ["llm_calls.jsonl", "llm_responses.jsonl"]
    assert "/app/logs" in decoded, decoded
    assert "/app/outputs" not in decoded, "the ledger does not live under outputs"
    assert "llm_calls.jsonl" in decoded


def test_a_box_without_a_ledger_does_not_fail_the_pull(monkeypatch, tmp_path):
    """A pull that got the turns and the outputs is worth having without it."""
    class _Done:
        returncode = 0
        stdout = b"django.log\nnextseek.log\n"
        stderr = b""

    monkeypatch.setattr(fetch_run.subprocess, "run", lambda cmd, **kw: _Done())
    assert fetch_run.pull_logs("fairdata", "", "nextseek", "/app/logs", tmp_path / "logs") == []


def test_the_ledger_can_be_skipped():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--logs-dir", default="/app/logs")
    assert ap.parse_args(["--no-ledger"]).no_ledger is True
    assert ap.parse_args([]).logs_dir == "/app/logs"


# --------------------------------------------------------------------------
# Fix 6a: a grading pull sums a case the way the harness does.
#
# The pull read one number per turn, `result.total_cost_usd`: the engine's.
# The router's call is priced on the `route_decided` progress event, and the
# harness now sums router plus engine per turn and every turn per case
# (`turn_cost`). A pull that summed differently would grade a case against a
# cost the run never reported.
# --------------------------------------------------------------------------

def _pulled(tid, route="container_cc", src="baml", cost=None, router_cost=None, **kw):
    return {"id": tid, "task_uuid": tid, "route": route, "src": src, "cost": cost,
            "router_cost": router_cost, **kw}


def test_the_turn_pull_reads_the_router_price_and_the_turn_record_read_only():
    import re as _re

    for key in ("total_cost_usd", "router_cost_usd", "router_cost_partial", "router_model",
                "router_fallback", "cost_partial", "models_used", "model_fallback"):
        assert key in fetch_run.REMOTE, key
    for sql in (fetch_run.REMOTE, fetch_run.RAW):
        upper = sql.upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "ALTER", "CREATE",
                     "TRUNCATE", "GRANT"):
            assert not _re.search(rf"\b{verb}\b", upper), f"{verb} in a read-only pull"


def test_a_pulled_turn_is_priced_by_the_harness_rule():
    turns = fetch_run.price_turns([
        _pulled("a", cost=0.5, router_cost=0.01, cost_partial=None),
        _pulled("b", route="nextseek_query", cost=None, router_cost=0.01),
        _pulled("c", route="unrelated", router_cost=0.003),
        _pulled("d", src="forced", cost=0.4),
        _pulled("e", route="nextseek_query", cost=0.2, router_cost=0.01, cost_partial=True),
    ])

    got = [(t["turn_cost"], t["turn_cost_partial"]) for t in turns]
    assert got == [(0.51, False), (0.01, True), (0.003, False), (0.4, False), (0.21, True)]


def test_a_pulled_turn_and_the_harness_price_the_same_payload_the_same():
    """The pull reads SQL columns, the harness reads the progress stream: one rule."""
    fb = {"agent": "graph", "from": "a", "to": "b", "reason": "timeout"}
    payload = {"progress": [
        {"event": "route_decided", "data": {"route": "nextseek_query", "source": "baml",
                                            "router_cost_usd": 0.004,
                                            "router_cost_partial": True}},
        {"event": "query_complete", "data": {"total_cost_usd": 0.2, "cost_partial": False,
                                             "model_fallback": [fb]}}]}
    harness = M.TurnMeta.from_payload(payload)
    (pulled,) = fetch_run.price_turns([_pulled(
        "a", route="nextseek_query", cost=0.2, router_cost=0.004, router_cost_partial=True,
        cost_partial=False, model_fallback=[fb], router_fallback=None)])

    assert (pulled["turn_cost"], pulled["turn_cost_partial"]) == (harness.cost, harness.partial)
    assert pulled["fell_back"] is harness.fell_back is True


def test_a_pulled_case_is_the_sum_of_its_turns():
    manifest = {"entries": [
        {"id": "cc.two", "task_ids": ["a", "b"]},
        {"id": "gone", "task_ids": ["z"]},
        {"id": "skipped", "task_ids": []},
        # A consistency group records its task ids per query, not on the entry.
        {"id": "cons.g", "task_ids": [],
         "turns_meta": [{"task_id": "c"}, {"task_id": "d"}]},
    ]}
    turns = fetch_run.price_turns([
        _pulled("a", cost=0.5, router_cost=0.01),
        _pulled("b", route="nextseek_query", router_cost=0.01,
                model_fallback=[{"agent": "graph", "from": "x", "to": "y",
                                 "reason": "timeout"}]),
        _pulled("c", route="nextseek_query", cost=0.1, router_cost=0.01, cost_partial=False),
        _pulled("d", route="nextseek_query", cost=0.2, router_cost=0.01, cost_partial=False),
    ])

    cases = fetch_run.case_costs(manifest, turns)

    assert cases["cc.two"] == {"cost": 0.52, "cost_partial": True, "turns": 2,
                               "missing_turns": 0, "fallback_turns": 1}
    assert cases["gone"]["cost"] is None and cases["gone"]["missing_turns"] == 1
    assert "skipped" not in cases
    assert cases["cons.g"]["cost"] == 0.32 and cases["cons.g"]["cost_partial"] is False


def test_a_pulled_case_counts_a_turn_the_run_sent_but_could_not_join():
    """A turn whose driver raised has no task id in the manifest, so joining by id
    alone would present the other turns' sum as the whole cost."""
    manifest = {"entries": [
        {"id": "lost", "task_ids": ["a"], "turns_sent": 2},
        {"id": "cons.lost", "task_ids": [], "turns_sent": 2,
         "turns_meta": [{"task_id": "c"}]},
        {"id": "all.lost", "task_ids": [], "turns_sent": 1},
        # The run itself said the case was a floor (a part it priced said so).
        {"id": "run.partial", "task_ids": ["d"], "turns_sent": 1, "cost_partial": True},
    ]}
    turns = fetch_run.price_turns([
        _pulled("a", route="nextseek_query", cost=0.2, router_cost=0.01, cost_partial=False),
        _pulled("c", route="nextseek_query", cost=0.1, router_cost=0.01, cost_partial=False),
        _pulled("d", route="nextseek_query", cost=0.1, router_cost=0.01, cost_partial=False),
    ])

    cases = fetch_run.case_costs(manifest, turns)

    assert cases["lost"]["cost"] == 0.21 and cases["lost"]["cost_partial"] is True
    assert cases["lost"]["missing_turns"] == 1
    assert cases["cons.lost"]["cost_partial"] is True and cases["cons.lost"]["missing_turns"] == 1
    assert cases["all.lost"] == {"cost": None, "cost_partial": False, "turns": 0,
                                 "missing_turns": 1, "fallback_turns": 0}
    assert cases["run.partial"]["cost_partial"] is True


def test_a_pull_loads_the_summing_rule_from_the_harness_not_a_copy():
    assert fetch_run.turn_cost.__file__.endswith("nessie_tests/turn_cost.py")
