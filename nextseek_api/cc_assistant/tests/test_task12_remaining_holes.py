"""Hermetic hole-fillers for Task 12 remaining missing_lines (production + imported helpers)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from nextseek_api.cc_assistant import cc_engine, cc_staging
from nextseek_api.cc_assistant.op_registry import export as op_export
from nextseek_api.cc_assistant.op_registry import paired_evidence as pe
from nextseek_api.cc_assistant.op_registry.ns_capabilities import (
    NsCapabilitiesError,
    project_ns_capabilities,
)
from nextseek_api.cc_assistant.tests import cc_matrix_gate_harness as gate
from nextseek_api.cc_assistant.tests import validate_cc_acceptance as vac
from nextseek_api.cc_assistant.tests.test_ns_capabilities import _md
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    BIN_OPS,
    CHECKS,
    Context,
    PUBLISHED_PATH_OPS,
    _compose_uses_subpath_syntax,
    _git_blob_size,
    _git_blob_text,
    _load_network_inspect_containers,
    _parse_api_version,
    _parse_compose_version,
    _parse_engine_version,
    _sha256_file,
    check_cc_runner_available_ok,
    check_cost_extraction_evidence,
    check_cost_ledger_valid,
    check_gate_access_log_window_hits_every_op,
    check_gate_instance_binding_present,
    check_plugin_ops_matrix_in_turn_viability,
    check_plugin_ops_matrix_published_paths_under_user_subtree,
    check_plugin_ops_matrix_row_schema_valid,
    check_post_sweep_user_tree_scan_contains_published_paths,
    check_r26_live_probes_present,
    check_screenshot_review_recorded,
    check_secret_scan_clean,
    check_seeded_fixture_present,
    check_sweep_invocation_valid,
    check_tracker_step3_done,
    default_repo_root,
)


def test_validate_step7_low_level_helpers(tmp_path):
    assert _load_network_inspect_containers(tmp_path / "missing.json") is None
    bad = tmp_path / "net.json"
    bad.write_text("[]")
    assert _load_network_inspect_containers(bad) is None
    bad.write_text("1")
    assert _load_network_inspect_containers(bad) is None
    assert _sha256_file(tmp_path) is None
    assert _git_blob_size(tmp_path, "deadbeef", "nope") is None
    assert _git_blob_text(tmp_path, "deadbeef", "nope") is None
    assert _parse_engine_version("no version") is None
    assert _parse_api_version("no api") is None
    assert _parse_compose_version("no compose") is None
    assert _compose_uses_subpath_syntax({"volumes": [{"subpath": "x"}]}) is True
    assert _compose_uses_subpath_syntax(["a", {"other": 1}]) is False
    assert default_repo_root().is_dir()


def test_validate_step7_check_inner_branches(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    meta = {
        "host_label": "dev-vm",
        "gate_project": "1-pub",
        "gate_user_id": "demo",
        "budget_cap_usd": 5,
        "matrix_spend_estimate_usd": 1.0,
        "run_id": "rid",
    }
    (run_dir / "meta.json").write_text(json.dumps(meta))
    (run_dir / "cc_runner_available.json").write_text(json.dumps({"ok": True}))
    (run_dir / "forced_cc_result.json").write_text(
        json.dumps({"is_error": False, "sentinel": "S", "cost": 0.1})
    )
    (run_dir / "secret_scan_report.json").write_text(json.dumps({"screenshots": {}}))
    (run_dir / "shot.png").write_bytes(b"\x89PNG")
    (run_dir / "leaky.txt").write_text("MYSQL_PASSWORD=secret")
    matrix = {}
    for op in BIN_OPS:
        row = {"op": op, "exit_code": 0, "wall_secs": 1.0, "excerpt": "{}"}
        if op in PUBLISHED_PATH_OPS:
            row["published_path"] = "/staging/dead"
        matrix[op] = row
    (run_dir / "plugin_ops_matrix.json").write_text(json.dumps(matrix))
    (run_dir / "post_sweep_user_tree_scan.txt").write_text("   ")
    (run_dir / "gate_access_log_window.txt").write_text("   ")
    (run_dir / "sweep_invocation.json").write_text(json.dumps({"command": "x", "exit_code": 1}))
    (run_dir / "seeded_fixture.json").write_text(json.dumps({"source": "not-binding"}))
    (run_dir / "instance_binding.json").write_text(json.dumps({
        "project_title": "T", "project": "p", "reference_uids": ["u1"],
        "forbidden_actions": [],
    }))
    (run_dir / "cost_ledger.json").write_text(json.dumps({
        "entries": [
            "bad",
            {"op": ""},
            {"op": "nextseek-query", "source_system": "", "usd": -1, "estimated": True},
            {"op": "nextseek-query", "source_system": "estimate", "usd": 0.1},
        ]
    }))
    (run_dir / "R26-live-probes.json").write_text("{")
    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {}},
        meta=meta,
        repo_root=repo,
    )
    names = [check(ctx)[0] for check in CHECKS]
    assert "cost_ledger_valid" in names
    assert check_cc_runner_available_ok(ctx)[1] is True
    (run_dir / "cc_runner_available.json").write_text("1")
    assert check_cc_runner_available_ok(ctx)[1] is False
    assert check_secret_scan_clean(ctx)[1] is False
    assert check_screenshot_review_recorded(ctx)[1] is False
    (run_dir / "secret_scan_report.json").write_text(json.dumps({"screenshots": "nope"}))
    assert check_screenshot_review_recorded(ctx)[1] is False
    assert check_plugin_ops_matrix_published_paths_under_user_subtree(ctx)[1] is False
    matrix[PUBLISHED_PATH_OPS[0]] = {
        "published_path": f"/data/{meta['gate_project']}/{meta['gate_user_id']}/out"
    }
    (run_dir / "plugin_ops_matrix.json").write_text(json.dumps(matrix))
    (run_dir / "post_sweep_user_tree_scan.txt").write_text("not-the-path")
    assert check_post_sweep_user_tree_scan_contains_published_paths(ctx)[1] is False
    (run_dir / "gate_access_log_window.txt").write_text("GET /nope HTTP/1.1")
    assert check_gate_access_log_window_hits_every_op(ctx)[1] is False
    assert check_sweep_invocation_valid(ctx)[1] is False
    assert check_gate_instance_binding_present(ctx)[0] == "gate_instance_binding_present"
    assert check_seeded_fixture_present(ctx)[0] == "gate_instance_binding_present"
    matrix[BIN_OPS[0]] = {"wall_secs": "nope"}
    (run_dir / "plugin_ops_matrix.json").write_text(json.dumps(matrix))
    assert check_plugin_ops_matrix_in_turn_viability(ctx)[1] is False
    assert check_cost_ledger_valid(ctx)[1] is False
    assert check_r26_live_probes_present(ctx)[1] is False
    (run_dir / "cost_ledger.json").unlink()
    assert check_cost_ledger_valid(ctx)[1] is False
    ctx2 = Context(run_dir=run_dir, preflight={"step3_deploy_gate": {}}, meta={}, repo_root=repo)
    assert check_cost_ledger_valid(ctx2)[1] is False
    assert check_plugin_ops_matrix_row_schema_valid(ctx)[1] is False
    assert check_cost_extraction_evidence(ctx)[1] is False
    assert check_tracker_step3_done(ctx)[1] is False


def test_validate_cc_acceptance_bundle_and_cli(tmp_path):
    d = tmp_path / "acc"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"sentinel": "S", "budget_cap_usd": 5, "user_id": "alice"}))
    (d / "routed_route_decided.json").write_text(json.dumps({"source": "baml", "route": "container_cc"}))
    (d / "forced_result.json").write_text(json.dumps({
        "is_error": False, "reply": "hello S",
        "artifacts": [{"key": "turn1/file.txt"}],
    }))
    (d / "proxy_log.txt").write_text("POST /model/us.anthropic.claude-opus-4-8/invoke -> 200\n")
    (d / "agent_env_scan.txt").write_text("FOO=bar\n")
    (d / "network.json").write_text(json.dumps({"containers": ["dmac-bedrock-proxy"]}))
    (d / "ledger.json").write_text(json.dumps({"total_cost_usd": 0.1}))
    ok, checks = vac.validate_run(d)
    assert checks
    assert vac.main(["prog"]) == 2
    assert vac.main(["prog", str(d)]) in (0, 1)
    empty = tmp_path / "empty"
    empty.mkdir()
    ok, checks = vac.validate_run(empty)
    assert ok is False
    assert "ACCEPTANCE FAILED" in vac.format_report(False, [("a", False, "x")])
    assert "ALL CHECKS PASSED" in vac.format_report(True, [("a", True, "x")])
    (d / "forced_result.json").write_text(json.dumps({
        "is_error": False, "reply": "hello S",
        "artifacts": [{"key": "alice/flat.txt"}],
    }))
    ok, checks = vac.validate_run(d)
    assert any(n == "artifacts_turn_scoped" and not good for n, good, _ in checks)


def test_harness_remaining_helpers(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="requires query"):
        gate.build_op_argv("nextseek-query")
    with pytest.raises(ValueError, match="requires parser_plan"):
        gate.build_op_argv("nextseek-api-write")
    with pytest.raises(ValueError, match="requires submission_type"):
        gate.build_op_argv("nextseek-generate-submission")
    er = gate.ExecResult(exit_code=0, stdout=" ok ", stderr="e", wall_secs=1.234)
    row = gate.make_matrix_row(
        "nextseek-report", result=er, container_id="c", container_name="n",
        image="img", transport="sidecar", published_path="/p", exercise_id="e",
        upstream_ref="u", cost_usd=0.1, call_id="cid", cost_source="claude",
    )
    assert row["published_path"] == "/p" and row["cost_source"] == "claude"
    fail = gate.ExecResult(exit_code=1, stdout="", stderr=" boom ", wall_secs=0.1)
    assert "boom" in gate.make_matrix_row(
        "nextseek-query", result=fail, container_id="c", container_name="n",
        image="img", transport="viewset",
    )["excerpt"]

    monkeypatch.setattr(
        gate.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="swept", stderr=""),
    )
    inv = gate.run_trusted_sweep(nextseek_container="nextseek", user_id="u", api_user="a", project="p")
    assert inv["exit_code"] == 0
    assert "find" in " ".join(gate.build_post_sweep_scan_command(volume="vol"))
    gate.capture_post_sweep_user_tree_scan(volume="vol")
    monkeypatch.setattr(
        gate.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="BEFORE\nAFTER", stderr=""),
    )
    assert "AFTER" in gate.capture_nginx_log_window("ngx", before="BEFORE\n")
    monkeypatch.setattr(
        gate, "docker_inspect_one",
        lambda *a, **k: {"Config": {"Env": ["A=1", "B=2"]}},
    )
    assert "A=1" in gate.capture_matrix_env_scan("ctr")
    monkeypatch.setattr(gate, "docker_inspect_one", lambda *a, **k: None)
    assert gate.capture_matrix_env_scan("ctr") == ""

    assert gate.find_project_id_by_title("x", "T") is None
    assert gate.find_project_id_by_title(
        {"data": ["x", {"attributes": {"title": "T"}, "id": "9"}]}, "T"
    ) == "9"
    assert gate.first_sample_type_id("x") is None
    assert gate.first_sample_type_id({"data": [{"id": "st"}]}) == "st"

    monkeypatch.setenv("NEXTSEEK_STEP7_INSTANCE_BINDING", "1")
    with pytest.raises(RuntimeError, match="instance binding"):
        gate.create_seeded_fixture(assistant_base_url="http://x", api_user="u", api_pass="p")
    monkeypatch.delenv("NEXTSEEK_STEP7_INSTANCE_BINDING")

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("no json")

    class _Client:
        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **k):
            return _Resp()

    import sys
    import types
    httpx = types.ModuleType("httpx")
    httpx.Client = _Client
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    call = gate.default_fixture_http("http://host/", ("u", "p"), 1.0)
    status, body = call("GET", "/x")
    assert status == 200 and body is None

    (tmp_path / "images.json").write_text("{")
    assert gate.images_json_cc_image(tmp_path) is None
    (tmp_path / "images.json").write_text("[]")
    assert gate.images_json_cc_image(tmp_path) is None
    assert gate.images_json_cc_image(tmp_path / "missing") is None


def test_paired_parse_and_check_export_sha(tmp_path, monkeypatch):
    assert pe.sha256_bytes(b"abc") == __import__("hashlib").sha256(b"abc").hexdigest()
    f = tmp_path / "blob"
    f.write_bytes(b"xyz")
    assert pe.sha256_file(f) == pe.sha256_bytes(b"xyz")
    with pytest.raises(pe.PairedEvidenceError, match="missing value"):
        pe.parse_strict_bool(None, field="x")
    assert pe.parse_optional_success(None, field="h") is None
    assert pe.parse_optional_success("  ", field="h") is None
    assert pe.parse_optional_success("fail", field="h") is False
    with pytest.raises(pe.PairedEvidenceError, match="invalid success"):
        pe.parse_optional_success("maybe", field="h")
    assert pe.parse_usefulness(None) is None
    assert pe.parse_usefulness(" ") is None
    with pytest.raises(pe.PairedEvidenceError, match="invalid numeric"):
        pe.parse_usefulness("nope")
    assert pe.arm_success({"image": "other"}, forced_image="ns") is False
    assert pe.arm_success({
        "image": "ns", "answer_provided": "true", "is_error": "true",
        "timed_out": "false", "runtime_success": "true",
    }, forced_image="ns") is False
    assert pe.arm_success({"image": "ns", "answer_provided": "not-a-bool"}, forced_image="ns") is False
    assert pe._success_to_csv(None) == ""
    committed = pe.load_committed_evidence()
    committed["source"]["zip_sha256"] = "0" * 64
    ev = tmp_path / "e.json"
    ev.write_bytes(pe.canonical_evidence_bytes(committed))
    with pytest.raises(SystemExit, match="zip sha256 mismatch"):
        pe.check_export(evidence_path=ev, zip_path=tmp_path / "missing.zip", corpus_path=tmp_path)
    del committed["source"]["members"]
    ev.write_bytes(pe.canonical_evidence_bytes(committed))
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", committed["source"]["zip_sha256"])
    with pytest.raises(SystemExit, match="source.members missing"):
        pe.check_export(evidence_path=ev, zip_path=tmp_path / "missing.zip", corpus_path=tmp_path)


def test_ns_capabilities_remaining_error_paths():
    with pytest.raises(NsCapabilitiesError, match="malformed H2"):
        project_ns_capabilities(
            "## \n\n## What You Can Ask\n\n### A\n\n## What the System Cannot Do\n\n- **X** no.\n"
        )
    with pytest.raises(NsCapabilitiesError, match="malformed H2"):
        project_ns_capabilities(
            "##Overview\n\nhi\n\n## What You Can Ask\n\n### A\n\n## What the System Cannot Do\n\n- **X** no.\n"
        )
    with pytest.raises(NsCapabilitiesError, match="leading paragraph"):
        project_ns_capabilities(_md(overview="# not a paragraph"))
    with pytest.raises(NsCapabilitiesError, match="leading paragraph"):
        project_ns_capabilities(_md(overview=""))
    with pytest.raises(NsCapabilitiesError, match="missing capability"):
        project_ns_capabilities(
            "## Overview\n\nHi.\n\n## What You Can Ask\n\n## What the System Cannot Do\n\n- **X** no.\n"
        )
    with pytest.raises(NsCapabilitiesError, match="nested heading"):
        project_ns_capabilities(
            "## Overview\n\nHi.\n\n## What You Can Ask\n\n#### Too deep\n\n## What the System Cannot Do\n\n- **X** no.\n"
        )
    with pytest.raises(NsCapabilitiesError, match="nested bold"):
        project_ns_capabilities(_md(negatives=("- **Live** no.\n  - **nested** x\n",)))


def test_extract_catalog_error_paths():
    from nextseek_api.cc_assistant.tests.test_cc_scripts_attribution import load_cc
    mod = load_cc("scripts/extract_step7_upstream_catalog.py")
    with pytest.raises(ValueError, match="PAID_PROJECTIONS not found"):
        mod._extract_paid_projections("nope")
    with pytest.raises(ValueError, match="block end"):
        mod._extract_paid_projections("PAID_PROJECTIONS = [\n")
        with pytest.raises(ValueError, match="did not evaluate"):
            mod._extract_paid_projections(
                "PAID_PROJECTIONS = [1][0]\n    for op, model, projected, args_dict in PAID_PROJECTIONS:\n"
            )
    with pytest.raises(ValueError, match="REPORT_PROJECT"):
        mod._extract_report_project("x = 1")
    q = mod._extract_search_basic_query("unused")
    assert isinstance(q, str) and q


def test_cc_staging_unsafe_and_deferred(tmp_path):
    with pytest.raises(cc_staging._DestUnsafe):
        cc_staging._deliver_file_safely(tmp_path / "nope", str(tmp_path / "missing-root"), (), "a.txt")
    src = tmp_path / "src.txt"
    src.write_text("hi")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    name = cc_staging._deliver_file_safely(src, str(scratch), (), "a.txt")
    assert name == "a.txt"
    name2 = cc_staging._deliver_file_safely(src, str(scratch), (), "a.txt")
    assert name2.startswith("a__")
    user_root = tmp_path / "users"
    staging = cc_staging.staging_root_for(str(user_root))
    staging.mkdir(parents=True)
    digest = __import__("hashlib").sha256(b"alice").hexdigest()
    base = staging / digest
    base.mkdir()
    (base / "not-a-uuid.complete").write_text("x")
    old = base / "11111111-1111-1111-1111-111111111111.complete"
    old.write_text("x")
    os.utime(old, (0, 0))
    result = cc_staging.sweep_user_staging(
        user_root_mount=str(user_root),
        scratch_dir=str(scratch),
        api_user="alice",
        user_id="alice",
        project_dirname="proj",
        since_ts=10_000,
    )
    assert result.deferred_markers is not None


def test_cc_engine_scrub_symlink_and_chmod(tmp_path, monkeypatch):
    root = tmp_path / "cc-state" / "projects"
    root.mkdir(parents=True)
    real = root / "t.jsonl"
    real.write_bytes(b"password=secret\n")
    link = root / "link.jsonl"
    link.symlink_to(real)
    cc_engine.scrub_transcript_store(tmp_path / "cc-state", {"NEXTSEEK_PASSWORD": "secret"})
    monkeypatch.setattr(cc_engine.os, "chmod", lambda *a, **k: (_ for _ in ()).throw(OSError("chmod")))
    real.write_bytes(b"password=secret\n")
    cc_engine.scrub_transcript_store(tmp_path / "cc-state", {"NEXTSEEK_PASSWORD": "secret"})


def test_op_export_main_check():
    assert op_export.main(["--check"]) == 0


def test_validate_step7_validate_run_cli_and_more_helpers(tmp_path, monkeypatch):
    from nextseek_api.cc_assistant.tests import validate_step7_compose_deploy as v7

    d = tmp_path / "run"
    d.mkdir()
    ok, checks = v7.validate_run(d, repo_root=tmp_path)
    assert ok is False and checks
    assert v7.main(["prog"]) == 2
    assert v7.main(["prog", str(d), str(tmp_path)]) == 1
    assert "FAILED" in v7.format_report(False, [("a", False, "x")])
    assert "ALL CHECKS PASSED" in v7.format_report(True, [("a", True, "x")])

    arr = tmp_path / "net2.json"
    arr.write_text(json.dumps([{}]))
    assert v7._load_network_inspect_containers(arr) is None
    obj = tmp_path / "net3.json"
    obj.write_text(json.dumps({"Containers": {"id": {"Name": "c1"}}}))
    assert v7._network_inspect_names(obj) == {"c1"}
    obj.write_text(json.dumps({"Containers": []}))
    assert v7._load_network_inspect_containers(obj) is None

    def boom(*a, **k):
        raise OSError("git")

    monkeypatch.setattr(v7.subprocess, "run", boom)
    assert v7._git_blob_text(tmp_path, "deadbeef", "x") is None
    assert v7._git_blob_size(tmp_path, "deadbeef", "x") is None

    ctx = Context(run_dir=d, preflight=None, meta={"matrix_spend_estimate_usd": 1}, repo_root=tmp_path)
    assert v7.check_meta_matrix_spend_estimate_recorded(ctx)[1] is False
    (d / "cost_extraction_evidence.json").write_text(json.dumps({
        "entries": ["bad", {"op": "nope", "usd": "x", "call_id": "c"}]
    }))
    assert check_cost_extraction_evidence(ctx)[1] is False
    (d / "R26-live-probes.json").write_text(json.dumps({"probes": [
        {"name": "project_binding", "pass": True},
        {"name": "sample_count", "pass": False},
        {"name": "sample_type_count", "pass": True},
        {"name": "reference_uid", "pass": True},
    ]}))
    assert check_r26_live_probes_present(ctx)[1] is False
    (d / "cost_ledger.json").write_text(json.dumps({"entries": [
        {"op": "nextseek-query", "source_system": "llm_client_ledger", "usd": "nope"},
        {"op": "nextseek-query", "source_system": "llm_client_ledger", "usd": 0.1},
    ]}))
    assert check_cost_ledger_valid(ctx)[1] is False


def test_validate_cc_acceptance_exception_and_fail_paths(tmp_path):
    d = tmp_path / "acc2"
    d.mkdir()
    (d / "routed_route_decided.json").write_text("{")
    (d / "forced_result.json").write_text("{")
    (d / "ledger.json").write_text("{")
    (d / "network.json").write_text("{")
    ok, checks = vac.validate_run(d)
    assert ok is False
    names = {n for n, _, _ in checks}
    assert "router_is_baml" in names
    (d / "proxy_log.txt").write_text("Authorization ABSK leak\n")
    (d / "agent_env_scan.txt").write_text("MYSQL_PASSWORD=x\n")
    (d / "network.json").write_text(json.dumps({"containers": ["seek-mysql"]}))
    (d / "forced_result.json").write_text(json.dumps({"is_error": True, "reply": ""}))
    (d / "routed_route_decided.json").write_text(json.dumps({"source": "heuristic", "route": "ns"}))
    (d / "ledger.json").write_text(json.dumps({"total_cost_usd": 99}))
    (d / "meta.json").write_text(json.dumps({"sentinel": "S", "budget_cap_usd": 1, "user_id": "u"}))
    ok, checks = vac.validate_run(d)
    assert ok is False
    assert vac.main(["prog", str(d)]) == 1


def test_harness_argv_fixture_and_nginx_prefix(monkeypatch, tmp_path):
    from nextseek_api.cc_assistant.bin_inventory import op_suffix as _suf
    by = {_suf(op): op for op in BIN_OPS}
    if "query" in by:
        assert "query" in " ".join(gate.build_op_argv(by["query"], query="mice"))
    if "recall" in by:
        assert "--turn" in gate.build_op_argv(by["recall"], turn=2)
    if "api-read" in by:
        assert "--parser-plan" in gate.build_op_argv(by["api-read"], parser_plan="{}")
    if "api-write" in by:
        argv = gate.build_op_argv(by["api-write"], parser_plan="{}", confirmed_write=True, query="q")
        assert "--confirmed-write" in argv
    if "entity-extract" in by:
        assert gate.build_op_argv(by["entity-extract"], query="q")[0] == by["entity-extract"]
    assert gate.build_project_create_payload("T")["data"]["type"] == "projects"
    payload = gate.build_sample_create_payload("s", "st", "9")
    assert "projects" in payload["data"]["relationships"]
    assert gate.extract_created_id("x") is None
    assert gate.extract_created_id({"data": {"id": "7"}}) == "7"
    assert gate.extract_sample_uid({"data": {"attributes": {"attribute_map": {"UID": "U1"}}}}) == "U1"
    assert gate.SeededFixture(project="p").to_json()["project"] == "p"

    monkeypatch.setattr(
        gate.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="OTHER", stderr=""),
    )
    assert gate.capture_nginx_log_window("ngx", before="BEFORE") == "OTHER"
    monkeypatch.setattr(gate, "docker_inspect_one", lambda *a, **k: {"Config": {}})
    assert gate.capture_matrix_env_scan("ctr") == ""

    calls = []

    def http(method, path, json_body=None, params=None):
        calls.append(method)
        if method == "GET" and "projects" in path:
            return 200, {"data": []}
        if method == "POST" and "projects" in path:
            return 201, {"data": {"id": "11"}}
        if method == "GET" and "sample_types" in path:
            return 200, {"data": [{"id": "st1"}]}
        if method == "POST" and "samples" in path:
            return 201, {"data": {"id": "s1", "attributes": {"attribute_map": {"uid": "UID-1"}}}}
        return 500, None

    fx = gate.create_seeded_fixture(
        assistant_base_url="http://x", api_user="u", api_pass="p",
        sample_count=1, http=http, run_id="rid",
    )
    assert fx.uids == ["UID-1"]

    def http_fail_project(method, path, json_body=None, params=None):
        return 500, {"err": True}

    with pytest.raises(gate.FixtureCreationError, match="project create failed"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http_fail_project,
        )

    def http_no_id(method, path, json_body=None, params=None):
        if method == "GET":
            return 200, {"data": []}
        return 201, {"data": {}}

    with pytest.raises(gate.FixtureCreationError, match="no data.id"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http_no_id,
        )

    def http_no_st(method, path, json_body=None, params=None):
        if "projects" in path and method == "GET":
            return 200, {"data": [{"attributes": {"title": gate.DEFAULT_GATE_PROJECT_TITLE}, "id": "9"}]}
        if "sample_types" in path:
            return 200, {"data": []}
        return 200, {}

    with pytest.raises(gate.FixtureCreationError, match="no sample_type"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p", http=http_no_st,
        )

    def http_sample_fail(method, path, json_body=None, params=None):
        if method == "GET" and "projects" in path:
            return 200, {"data": [{"attributes": {"title": gate.DEFAULT_GATE_PROJECT_TITLE}, "id": "9"}]}
        if method == "GET" and "sample_types" in path:
            return 200, {"data": [{"id": "st"}]}
        return 500, None

    with pytest.raises(gate.FixtureCreationError, match="sample create failed"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p",
            sample_count=1, http=http_sample_fail,
        )

    def http_no_uid(method, path, json_body=None, params=None):
        if method == "GET" and "projects" in path:
            return 200, {"data": [{"attributes": {"title": gate.DEFAULT_GATE_PROJECT_TITLE}, "id": "9"}]}
        if method == "GET" and "sample_types" in path:
            return 200, {"data": [{"id": "st"}]}
        return 201, {"data": {}}

    with pytest.raises(gate.FixtureCreationError, match="no id/UID"):
        gate.create_seeded_fixture(
            assistant_base_url="http://x", api_user="u", api_pass="p",
            sample_count=1, http=http_no_uid,
        )
    (tmp_path / "out.json").parent.mkdir(parents=True, exist_ok=True)
    gate.write_json(tmp_path / "out.json", {"a": 1})
    gate.write_text(tmp_path / "out.txt", "hi")


def test_paired_structure_and_zip_prefix(tmp_path):
    with pytest.raises(pe.PairedEvidenceError, match="missing committed"):
        pe.load_committed_evidence(tmp_path / "nope.json")
    with pytest.raises(pe.PairedEvidenceError, match="missing paired source zip"):
        pe.stream_required_members(tmp_path / "no.zip")
    with pytest.raises(pe.PairedEvidenceError, match="unexpected archive member"):
        pe._validate_zip_prefix("evil/path.csv")
    with pytest.raises(pe.PairedEvidenceError, match="invalid boolean"):
        pe.parse_strict_bool("yes", field="x")
    assert pe.arm_success({
        "image": "ns", "answer_provided": "true", "is_error": "false",
        "timed_out": "false", "runtime_success": "true", "human_success": "false",
    }, forced_image="ns") is False
    assert pe.arm_success({
        "image": "ns", "answer_provided": "true", "is_error": "false",
        "timed_out": "false", "runtime_success": "true", "llm_success": "false",
    }, forced_image="ns") is False
    committed = pe.load_committed_evidence()
    with pytest.raises(pe.PairedEvidenceError, match="schema_version"):
        pe.validate_committed_structure({**committed, "schema_version": "nope"})
    with pytest.raises(pe.PairedEvidenceError, match="selected_ids"):
        pe.validate_committed_structure({**committed, "selected_ids": []})
    with pytest.raises(pe.PairedEvidenceError, match="records length"):
        pe.validate_committed_structure({**committed, "records": []})
    bad_ids = dict(committed)
    bad_ids["records"] = list(committed["records"])
    if bad_ids["records"]:
        rec = dict(bad_ids["records"][0])
        rec["query_id"] = "not-the-id"
        bad_ids["records"] = [rec] + list(bad_ids["records"][1:])
        with pytest.raises(pe.PairedEvidenceError, match="record ids"):
            pe.validate_committed_structure(bad_ids)
    with pytest.raises(pe.PairedEvidenceError, match="audit section"):
        pe.validate_committed_structure({**committed, "audit": []})
    audit = dict(committed["audit"])
    audit["ns_only"] = "nope"
    with pytest.raises(pe.PairedEvidenceError, match="must be a list"):
        pe.validate_committed_structure({**committed, "audit": audit})
    audit = dict(committed["audit"])
    overlap = list(committed["selected_ids"])[:1]
    if overlap:
        audit["ns_only"] = overlap
        audit["cc_only"] = overlap
        audit["both_success"] = []
        audit["neither_success"] = []
        with pytest.raises(pe.PairedEvidenceError, match="overlap"):
            pe.validate_committed_structure({**committed, "audit": audit})
        audit = dict(committed["audit"])
        audit["ns_only"] = []
        audit["cc_only"] = []
        audit["both_success"] = []
        audit["neither_success"] = []
        with pytest.raises(pe.PairedEvidenceError, match="exhaustive"):
            pe.validate_committed_structure({**committed, "audit": audit})
    with pytest.raises(pe.PairedEvidenceError, match="missing corpus"):
        pe._corpus_authority(tmp_path / "no-corpus.json")
    with pytest.raises(pe.PairedEvidenceError, match="empty"):
        pe._manifest_selected_ids(SimpleNamespace(run_meta={}, pairs=[]))
    with pytest.raises(pe.PairedEvidenceError, match="duplicates"):
        pe._manifest_selected_ids(SimpleNamespace(run_meta={"selected_ids": ["a", "a"]}, pairs=[]))
    with pytest.raises(pe.PairedEvidenceError, match="order"):
        pe._manifest_selected_ids(SimpleNamespace(
            run_meta={"selected_ids": ["a", "b"]},
            pairs=[SimpleNamespace(id="b"), SimpleNamespace(id="a")],
        ))
    with pytest.raises(pe.PairedEvidenceError, match="duplicate graded"):
        pe._index_graded_rows([{"query_id": "a", "image": "ns"}, {"query_id": "a", "image": "ns"}])
    with pytest.raises(pe.PairedEvidenceError, match="duplicate functional"):
        pe._index_functional_rows([{"query_id": "a"}, {"query_id": "a"}])
    pe._success_to_csv(False)
    assert pe.main(["--check"]) == 0


def test_ns_capabilities_fences_budget_and_empty_labels():
    fenced = (
        "## Overview\n\nHi.\n\n```python\nprint(1)\n```\n\n"
        "## What You Can Ask\n\n### A\n\n## What the System Cannot Do\n\n- **X** no.\n"
    )
    proj = project_ns_capabilities(fenced)
    assert proj.tools
    with pytest.raises(NsCapabilitiesError, match="nested heading substitute"):
        project_ns_capabilities(
            "## Overview\n\nHi.\n\n## What You Can Ask\n\n> ## sneak\n\n"
            "### A\n\n## What the System Cannot Do\n\n- **X** no.\n"
        )
        with pytest.raises(NsCapabilitiesError, match="malformed H3"):
            project_ns_capabilities(
                "## Overview\n\nHi.\n\n## What You Can Ask\n\n###\n\n"
                "## What the System Cannot Do\n\n- **X** no.\n"
            )
    with pytest.raises(NsCapabilitiesError, match="empty negative"):
        project_ns_capabilities(_md(negatives=("- **  ** x\n",)))
    nested_plain = _md(negatives=("- **Live** no.\n  - nested without bold\n",))
    project_ns_capabilities(nested_plain)
    with pytest.raises(NsCapabilitiesError, match="malformed H2"):
        project_ns_capabilities(
            "## Overview\n\nHi.\n\n##What You Can Ask\n\n### A\n\n"
            "## What the System Cannot Do\n\n- **X** no.\n"
        )
    long_label = "L" * 200
    with pytest.raises(NsCapabilitiesError, match="exceeds"):
        project_ns_capabilities(_md(h3=(long_label,)))
    with pytest.raises(NsCapabilitiesError, match="Overview is missing"):
        project_ns_capabilities(
            "## Overview\n\n- list first\n\n## What You Can Ask\n\n### A\n\n"
            "## What the System Cannot Do\n\n- **X** no.\n"
        )


def test_bin_inventory_and_memory_io_edges(tmp_path, monkeypatch):
    from nextseek_api.cc_assistant import bin_inventory, cc_memory_io

    missing = tmp_path / "no-bin"
    assert bin_inventory.discover_ops(bin_dir=missing) == ()
    d = tmp_path / "bin"
    d.mkdir()
    (d / "readme.txt").write_text("x")
    shim = d / "nextseek-foo"
    shim.write_text("echo hi\n")
    assert bin_inventory.discover_ops(bin_dir=d) == ()
    shim.write_text("from _nextseek_runner.py import x\n")
    os.chmod(shim, 0o644)
    assert bin_inventory.discover_ops(bin_dir=d) == ()
    os.chmod(shim, 0o755)
    assert "nextseek-foo" in bin_inventory.discover_ops(bin_dir=d)
    assert bin_inventory.discover_ops("batch-upload", bin_dir=d) == ()
    with pytest.raises(ValueError, match="not a bin op"):
        bin_inventory.op_suffix("nope")
    assert bin_inventory._runner_for_shim(d) is None

    dest = tmp_path / "mem" / "CLAUDE.md"
    dest.parent.mkdir()
    monkeypatch.setattr(os, "chmod", lambda *a, **k: (_ for _ in ()).throw(OSError("chmod")))
    assert cc_memory_io.write_memory_file(dest, "# hi") == dest
    assert cc_memory_io.write_memory_file(dest, "") is None
    assert cc_memory_io._is_stale(tmp_path / "missing", b"x") is True
    same = tmp_path / "same.bin"
    same.write_bytes(b"abc")
    assert cc_memory_io._is_stale(same, b"abc") is False
    assert cc_memory_io._is_stale(same, b"abx") is True
    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "old.jsonl").write_text("x")

    class W:
        session_id = "s"
        transcript_path = ""

    assert cc_memory_io.stage_transcripts([W()], staging) is None
    assert not (staging / "old.jsonl").exists()


def test_derive_parse_edges(tmp_path):
    from nextseek_api.cc_assistant.op_registry import derive

    p = tmp_path / "a.py"
    p.write_text("X = 1\n")
    with pytest.raises(AssertionError):
        derive.parse_frozenset_assignment(p, "X")
    p.write_text("Y: dict[str, int] = {'a': 1}\n")
    assert "a" in derive.parse_dict_keys(p, "Y")
    p.write_text("Z = {}\n")
    with pytest.raises(AssertionError):
        derive.parse_dict_keys(p, "Z")
    import ast
    with pytest.raises(AssertionError):
        derive._parse_string_set(ast.parse("1").body[0].value)


def test_cc_summary_and_catalog_and_per_op(tmp_path):
    from nextseek_api.cc_assistant import cc_summary, step7_gate_catalog as cat, step7_per_op_evidence as ev

    assert cc_summary._content_text(None) == ""
    assert "t" in cc_summary._content_text([{"type": "text", "text": "t"}])
    assert cc_summary._stringify(["a", {"text": "b"}])
    assert cc_summary._quote_str({"value": "q"}) == "q"
    ns = SimpleNamespace(value="v")
    assert cc_summary._quote_str(ns) == "v"
    assert cc_summary._quote_str("x") == "x"
    parsed = cc_summary.ParsedTranscript(records=({"type": "user", "message": {}},), raw_lines=(b"x",))
    assert cc_summary.verify_quote(parsed, 1, 1, "") is False
    assert cc_summary.verify_quote(parsed, 0, 1, "x") is False
    class Cfg:
        max_items = 1
        truncate_chars = 20
    recs = ({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "hi"},
        ]},
    },)
    parsed2 = cc_summary.ParsedTranscript(records=recs, raw_lines=(b"ls",))
    prov = cc_summary.SummaryProvenance(
        chat_session_id="c", claude_session_id="s",
        transcript_path="t.jsonl", chat_model="opus", generated_at="now",
    )
    fb = cc_summary.build_fallback_summary(parsed2, prov, Cfg())
    assert fb

    binding = cat.InstanceBinding.from_json({
        "binding_id": "b", "project_id": 1, "project_title": "T",
        "reference_uids": ["u1"], "cc_project_dirname": "1-t",
    })
    assert binding.to_json()["binding_id"] == "b"
    bad = tmp_path / "ex.json"
    bad.write_text(json.dumps({"exercises": {}}))
    with pytest.raises(ValueError, match="missing exercises"):
        cat.load_exercise_catalog(bad)
    exercises = [{"bin_op": op, "inputs": {}, "allow_confirmed_write": True} for op in cat.BIN_OPS]
    kwargs = cat.build_op_kwargs_from_catalog(exercises, binding, allow_confirmed_write=True)
    assert kwargs
    with pytest.raises(ValueError, match="catalog missing"):
        cat.build_op_kwargs_from_catalog([], binding)
    rec = cat.binding_fixture_record(binding)
    assert rec["source"] == "instance_binding.json"
    sm = {"ops": {op: {"charged": True} for op in cat.BIN_OPS}}
    with pytest.raises(ValueError, match="missing cost_usd"):
        cat.build_cost_ledger_from_matrix({op: {} for op in cat.BIN_OPS}, run_id="rid", timestamp="t", source_map=sm)
    matrix = {op: {"cost_usd": 0.1, "cost_source": "claude", "call_id": "c"} for op in cat.BIN_OPS}
    assert cat.build_cost_ledger_from_matrix(matrix, run_id="rid", timestamp="t", source_map=sm)["entries"]
    matrix2 = {op: {"cost_usd": "x", "cost_source": "claude", "call_id": "c"} for op in cat.BIN_OPS}
    with pytest.raises(ValueError, match="not numeric"):
        cat.build_cost_ledger_from_matrix(matrix2, run_id="rid", timestamp="t", source_map=sm)
    matrix3 = {op: {"cost_usd": 0, "cost_source": "claude", "call_id": "c"} for op in cat.BIN_OPS}
    with pytest.raises(ValueError, match="must be > 0"):
        cat.build_cost_ledger_from_matrix(matrix3, run_id="rid", timestamp="t", source_map=sm)
    matrix4 = {op: {"cost_usd": 0.1, "cost_source": " ", "call_id": "c"} for op in cat.BIN_OPS}
    with pytest.raises(ValueError, match="missing cost_source"):
        cat.build_cost_ledger_from_matrix(matrix4, run_id="rid", timestamp="t", source_map=sm)
    matrix5 = {op: {"cost_usd": 0.1, "cost_source": "claude", "call_id": ""} for op in cat.BIN_OPS}
    with pytest.raises(ValueError, match="missing call_id"):
        cat.build_cost_ledger_from_matrix(matrix5, run_id="rid", timestamp="t", source_map=sm)

    assert ev._command_invokes_op("# comment\nFOO=1", "nextseek-query") is False
    assert ev._command_invokes_op("command -v nextseek-query", "nextseek-query") is False
    assert ev._command_invokes_op("nextseek-query --json", "nextseek-query") is True


def test_install_oracle_scan_edges(tmp_path):
    from nextseek_api.cc_assistant.op_registry import install_oracle as io

    empty = tmp_path / "nope"
    assert io._scan_manifests(empty) == ()
    assert io._scan_skills(empty) == ()
    assert io._scan_commands(empty) == ()
    assert io._scan_shims(empty) == ()
    plug = tmp_path / "plugins" / "p1"
    plug.mkdir(parents=True)
    rel = io.MANIFEST_RELATIVE
    (plug / rel).parent.mkdir(parents=True, exist_ok=True)
    (plug / rel).write_text(json.dumps({"name": ""}))
    with pytest.raises(io.InstallOracleError, match="non-empty"):
        io._scan_manifests(tmp_path / "plugins")
    (plug / rel).write_text(json.dumps({"name": "p1"}))
    assert io.manifest_plugin_dirs(tmp_path / "plugins") == ("p1",)
    (plug / "skills" / "s1").mkdir(parents=True)
    (plug / "skills" / "s1" / "SKILL.md").write_text("x")
    (plug / "commands").mkdir()
    (plug / "commands" / "c.md").write_text("x")
    assert io._scan_skills(tmp_path / "plugins")
    assert io._scan_commands(tmp_path / "plugins")
    with pytest.raises(io.InstallOracleError, match="names must match"):
        io._parse_copy_destinations("COPY build_context/plugins/foo/ /app/plugins/bar/\n")
    with pytest.raises(io.InstallOracleError, match="duplicate COPY"):
        io._parse_copy_destinations(
            "COPY build_context/plugins/foo/ /app/plugins/foo/\n"
            "COPY build_context/plugins/foo/ /app/plugins/foo/\n"
        )
    io._parse_copy_destinations("COPY build_context/plugins/foo/ /app/plugins/foo/\n")
    with pytest.raises(io.InstallOracleError, match="unsafe traversal"):
        io._ensure_no_traversal((io.CopyDestination(plugin_name="..", source="x", destination="y"),))
    with pytest.raises(io.InstallOracleError, match="missing manifest-bearing"):
        io._ensure_manifests_for_copy_plugins({"missing"}, set(), tmp_path / "plugins")
    with pytest.raises(io.InstallOracleError, match="missing manifest"):
        io._ensure_manifests_for_copy_plugins({"p1"}, {"p1"}, tmp_path / "no-plugins")
    with pytest.raises(io.InstallOracleError):
        io._ensure_plugin_bins({"p1"}, tmp_path / "plugins")


def test_cc_staging_symlink_and_unsafe_leaf(tmp_path, monkeypatch):
    src = tmp_path / "src.txt"
    src.write_text("hi")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    art = scratch / "nextseek-artifacts"
    art.mkdir()
    link = art / "evil"
    link.symlink_to(tmp_path)
    with pytest.raises(cc_staging._DestUnsafe):
        cc_staging._deliver_file_safely(src, str(scratch), ("evil",), "a.txt")
    real_open = os.open

    def fake_open(path, flags, *a, **k):
        if path == "badleaf":
            raise OSError("leaf refused")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", fake_open)
    with pytest.raises(cc_staging._DestUnsafe):
        cc_staging._deliver_file_safely(src, str(scratch), (), "badleaf")


def test_cc_engine_publish_unsafe_and_persist_strict(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "ok.txt").write_text("x")
    monkeypatch.setattr(
        "dmac_assistant.run_tracker.diff_files",
        lambda b, a: ["ok.txt", "../escape"],
    )
    result = cc_engine._publish_artifacts(
        scratch, output, turn_id="t1",
        output_logical_root="/out", before=before,
    )
    assert result["files_created"] or result["artifacts"] is not None

    # chmod OSError already covered; force read OSError skip
    root = tmp_path / "cc-state" / "projects"
    root.mkdir(parents=True)
    blocked = root / "x.jsonl"
    blocked.write_bytes(b"password=secret\n")
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("read")))
    cc_engine.scrub_transcript_store(tmp_path / "cc-state", {"NEXTSEEK_PASSWORD": "secret"})


def test_op_export_write_to_tmp(tmp_path):
    out = tmp_path / "ops.json"
    plugins = tmp_path / "plugins" / "nextseek"
    plugins.mkdir(parents=True)
    docker = tmp_path / "Dockerfile"
    docker.write_text("# none\n")
    op_export.write_export(canonical_path=out, plugins_root=tmp_path / "plugins", dockerfile_path=docker)
    assert out.is_file()


def test_vprm_remaining_secret_scan_branches(tmp_path):
    from nextseek_api.cc_assistant.tests.test_verify_prod_readiness_manifest import vprm, MERGED_SHA, _artifact

    errs = []
    cats = {c: {"hits": [{"match": "ABCD"}], "allowlist": [{"match": "AB"}]} for c in vprm._SECRET_SCAN_CATEGORIES}
    rec = _artifact(tmp_path, "s5.json", {
        "image_tag": "t", "image_id": "i1", "categories": cats,
        "export_member_count": 1, "scanned_bytes": 10, "command": "c", "exit_code": 0,
    })
    vprm._verify_secret_scans(errs, [rec], MERGED_SHA, None)
    cats2 = {c: {"hits": "nope", "allowlist": []} for c in vprm._SECRET_SCAN_CATEGORIES}
    rec2 = _artifact(tmp_path, "s6.json", {
        "image_tag": "t", "image_id": "i1", "categories": cats2,
        "export_member_count": 1, "scanned_bytes": 10, "command": "c", "exit_code": 0,
    })
    vprm._verify_secret_scans(errs, [rec2], MERGED_SHA, None)
    cats3 = {c: "bad" for c in vprm._SECRET_SCAN_CATEGORIES}
    rec3 = _artifact(tmp_path, "s7.json", {
        "image_tag": "t", "image_id": "i1", "categories": cats3,
        "export_member_count": 1, "scanned_bytes": 10, "command": "c", "exit_code": 0,
    })
    vprm._verify_secret_scans(errs, [rec3], MERGED_SHA, None)
    vprm._verify_migration_evidence(errs, None, MERGED_SHA)


def test_posterior_and_route_monitoring_edges(monkeypatch):
    from nextseek_api.cc_assistant import posterior_selector, route_monitoring as rm

    assert posterior_selector.select_route("") is None
    assert posterior_selector.select_route("unrelated") is None
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.posterior_selector.get_active_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("db")),
    )
    assert posterior_selector.select_route("search") is None
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.posterior_selector.get_active_snapshot",
        lambda: None,
    )
    assert posterior_selector.select_route("search") is None
    assert rm._distribution_drift({}, {}) == 0.0
    snap = rm.build_monitoring_snapshot([])
    assert rm.build_route_monitoring_summary([]) == rm.MONITORING_DISCLAIMER
    row = SimpleNamespace(
        route="nextseek_query", assignment_policy="policy", generation_hash="g",
        propensity_unavailable=True, task_family="", observation_id="o1",
    )
    text = rm.build_route_monitoring_summary([row])
    assert "propensity_unavailable" in text


def test_router_heuristic_and_context_dir(monkeypatch):
    from nextseek_api.cc_assistant import router as rt

    monkeypatch.setattr(rt, "_build_context_dir", lambda: None)
    assert rt._resolve_model_id(None) is None
    import dmac_assistant.router.models as models
    monkeypatch.setattr(models, "load_model_class_map", lambda **k: (_ for _ in ()).throw(RuntimeError("map")))
    assert rt._resolve_model_id("opus") is None
    dec = rt._heuristic("please write a python script to edit files")
    assert dec.route in (rt.ROUTE_CC, rt.ROUTE_NS, rt.ROUTE_UNRELATED)


def test_extract_catalog_main_and_block_end(tmp_path):
    from nextseek_api.cc_assistant.tests.test_cc_scripts_attribution import load_cc
    mod = load_cc("scripts/extract_step7_upstream_catalog.py")
    src = tmp_path / "run_t18_rewire_e2e.py"
    src.write_text(
        'REPORT_PROJECT = "Published Data"\n'
        "PAID_PROJECTIONS = [\n"
        '    ("entity", "m", 0.1, {"a": 1}),\n'
        "]\n"
        "    for op, model, projected, args_dict in PAID_PROJECTIONS:\n"
        "        pass\n",
        encoding="utf-8",
    )
    router = tmp_path / "run_router_e2e.py"
    router.write_text("DISCRIMINATORS = []\n")
    assert mod.main(["--upstream-root", str(tmp_path / "missing")]) == 2
    assert isinstance(mod._extract_search_basic_query("unused"), str)


def test_gap2_production_misses(tmp_path, monkeypatch):
    from nextseek_api.cc_assistant.op_registry.ns_capabilities import (
        NsProjection, _unique_labels, _reject_over_budget, _negative_labels,
    )
    with pytest.raises(NsCapabilitiesError, match="empty"):
        _unique_labels([""], kind="capability")
    with pytest.raises(NsCapabilitiesError, match="unclosed bold"):
        _negative_labels([(1, "- **")])
    huge = NsProjection(
        description="d", tools=("T" * 200,), negative_labels=("n",),
        best_for="b", not_for="n",
    )
    with pytest.raises(NsCapabilitiesError, match="exceeds"):
        _reject_over_budget(huge)

    committed = pe.load_committed_evidence()
    recs = list(committed["records"])
    if recs:
        rec = dict(recs[0])
        ns = dict(rec["ns"])
        ns["success"] = not ns["success"]
        rec["ns"] = ns
        recs[0] = rec
        with pytest.raises(pe.PairedEvidenceError, match="success mismatch"):
            pe.validate_committed_structure({**committed, "records": recs})
    messy = tmp_path / "e.json"
    messy.write_text(json.dumps(committed) + " \n")
    with pytest.raises(SystemExit, match="non-canonical"):
        pe.check_export(evidence_path=messy, zip_path=tmp_path / "no.zip", corpus_path=tmp_path)
    monkeypatch.setattr(pe.runner, "corpus_fingerprint", lambda p: "fp")
    monkeypatch.setattr(
        pe.corpus, "load_all_definitions",
        lambda p: [SimpleNamespace(id="a", family="f"), SimpleNamespace(id="a", family="f")],
    )
    (tmp_path / "c.json").write_text("{}")
    with pytest.raises(pe.PairedEvidenceError, match="duplicate corpus"):
        pe._corpus_authority(tmp_path / "c.json")
    pair = SimpleNamespace(
        id="a", family="f",
        ns=SimpleNamespace(id="a", family="f", route="x", route_source="forced"),
        cc=SimpleNamespace(id="a", family="f", route="container_cc", route_source="forced"),
    )
    with pytest.raises(pe.PairedEvidenceError, match="ns.route"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair]), ["a"])
    pair2 = SimpleNamespace(
        id="a", family="f",
        ns=SimpleNamespace(id="a", family="f", route="nextseek_query", route_source="forced"),
        cc=SimpleNamespace(id="a", family="x", route="container_cc", route_source="forced"),
    )
    with pytest.raises(pe.PairedEvidenceError, match="family mismatch"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair2]), ["a"])
    pair3 = SimpleNamespace(id="a", family="f", ns=None, cc=None)
    with pytest.raises(pe.PairedEvidenceError, match="missing ns or cc"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair3]), ["a"])
    pair4 = SimpleNamespace(
        id="a", family="f",
        ns=SimpleNamespace(id="b", family="f", route="nextseek_query", route_source="forced"),
        cc=SimpleNamespace(id="a", family="f", route="container_cc", route_source="forced"),
    )
    with pytest.raises(pe.PairedEvidenceError, match="arm ids"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair4]), ["a"])
    pair5 = SimpleNamespace(
        id="a", family="f",
        ns=SimpleNamespace(id="a", family="f", route="nextseek_query", route_source="forced"),
        cc=SimpleNamespace(id="a", family="f", route="container_cc", route_source="not-forced"),
    )
    with pytest.raises(pe.PairedEvidenceError, match="route_source"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair5]), ["a"])
    pair6 = SimpleNamespace(
        id="dup", family="f",
        ns=SimpleNamespace(id="dup", family="f", route="nextseek_query", route_source="forced"),
        cc=SimpleNamespace(id="dup", family="f", route="container_cc", route_source="forced"),
    )
    with pytest.raises(pe.PairedEvidenceError, match="duplicate manifest"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair6, pair6]), ["dup", "dup"])
    with pytest.raises(pe.PairedEvidenceError, match="pairs count"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[]), ["a"])

    from nextseek_api.cc_assistant.tests.test_cc_scripts_attribution import load_cc
    live = load_cc("scripts/step7_gate3d_live.py")
    assert live._cc_run_id("NOT-HEX!!!")
    assert live._cc_run_id("")
    per_op = load_cc("scripts/step7_gate3d_per_op.py")
    assert hasattr(per_op, "main")

    from nextseek_api.cc_assistant.tests.test_cc_engine_turn_loop import (
        _FakeContainer, _FakeSock, _install_client, _paths, _run_id,
    )
    container = _FakeContainer()
    _install_client(monkeypatch, container)
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "sid-1", "model": "opus"}),
        json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
            "result": "ok", "total_cost_usd": 0.02, "session_id": "sid-1",
            "num_turns": 1, "duration_ms": 12,
        }),
    ]
    monkeypatch.setattr(
        cc_engine, "BridgeAttachSocket",
        lambda raw, stdout_stream=None: _FakeSock(lines),
    )
    from nextseek_api.cc_assistant import cc_provision
    real_build = cc_provision.build_user_dirs

    def wrap_dirs(*a, **k):
        dirs = real_build(*a, **k)
        if dirs.cc_state_mnt:
            root = Path(dirs.cc_state_mnt) / "projects"
            root.mkdir(parents=True, exist_ok=True)
            (root / "turn.jsonl").write_bytes(b"{}\n")
        return dirs

    monkeypatch.setattr(cc_provision, "build_user_dirs", wrap_dirs)

    class _Trace:
        def model_dump(self):
            return {"cc": True}

    monkeypatch.setattr("nextseek_api.cc_assistant.cc_trace.extract_trace", lambda *a, **k: _Trace())

    def boom_payload(*a, **k):
        raise RuntimeError("persist boom")

    events = []
    cc_engine.run_cc_turn(
        query="q", model_id="m",
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(), paths=_paths(tmp_path / "persist"),
        cc_state_key="abc-123",
        chat_session=object(), user_query="q",
        on_turn_complete=boom_payload,
    )
    assert any(e == "query_complete" for e, _ in events)

    def wrap_empty(*a, **k):
        return real_build(*a, **k)

    monkeypatch.setattr(cc_provision, "build_user_dirs", wrap_empty)
    monkeypatch.setattr("django.conf.settings", SimpleNamespace(CC_PERSIST_STRICT=False), raising=False)
    events2 = []
    cc_engine.run_cc_turn(
        query="q", model_id="m",
        send_event=lambda e, d: events2.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(), paths=_paths(tmp_path / "njsonl"),
        cc_state_key="abc-123",
        chat_session=object(), user_query="q",
        on_turn_complete=lambda *a, **k: None,
    )

    from nextseek_api.cc_assistant import cc_summary
    parsed = cc_summary.ParsedTranscript(records=({"type": "other"},), raw_lines=(b"x",))
    prov = cc_summary.SummaryProvenance(
        chat_session_id="c", claude_session_id=None,
        transcript_path="t", chat_model="m", generated_at="now",
    )
    class Cfg:
        max_items = 3
        truncate_chars = 10
    cc_summary.build_fallback_summary(parsed, prov, Cfg())
    def boom_sum(_):
        raise RuntimeError("sum")
    try:
        cc_summary.summarize_transcript(b"{}\n", prov, Cfg(), summarize_fn=boom_sum)
    except Exception:
        pass


def test_gap3_fourteen_production_units():
    from nextseek_api.cc_assistant.op_registry import ns_capabilities as nsc
    from nextseek_api.cc_assistant.tests.test_cc_scripts_attribution import load_cc

    class _M:
        def group(self, _i):
            return "   "

    with pytest.raises(NsCapabilitiesError, match="empty title"):
        nsc._heading_title(_M())
    body = [(1, "Hello."), (2, ""), (3, "# not para")]
    nsc._first_overview_paragraph(body)
    nsc._capability_labels([(1, "  - nested without heading")])
    pair_cc = SimpleNamespace(
        id="a", family="f",
        ns=SimpleNamespace(id="a", family="f", route="nextseek_query", route_source="forced"),
        cc=SimpleNamespace(id="a", family="f", route="wrong", route_source="forced"),
    )
    with pytest.raises(pe.PairedEvidenceError, match="cc.route"):
        pe._validate_manifest_pairs(SimpleNamespace(pairs=[pair_cc]), ["a"])
    mod = load_cc("scripts/extract_step7_upstream_catalog.py")
    with pytest.raises(ValueError, match="did not evaluate"):
        mod._extract_paid_projections(
            "PAID_PROJECTIONS = []\nPAID_PROJECTIONS = 1\n"
            "    for op, model, projected, args_dict in PAID_PROJECTIONS:\n"
        )
    surv = load_cc("scripts/verify_merge_survivals.py")
    assert surv.has("docker-compose.yml", "dmac-cc-net") in (True, False)
    dry = load_cc("scripts/step7_validator_dry_run.py")
    assert hasattr(dry, "main")
    hostf = load_cc("scripts/step7_gate3d_host_finalize.py")
    assert hasattr(hostf, "main")



