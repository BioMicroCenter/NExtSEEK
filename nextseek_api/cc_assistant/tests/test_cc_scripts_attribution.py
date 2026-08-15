"""Load production scripts/evidence under package names so coverage JSON keys match."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

CC_ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]


def load_cc(rel: str):
    dotted = "nextseek_api.cc_assistant." + rel.replace("/", ".").removesuffix(".py")
    path = CC_ROOT / rel
    spec = importlib.util.spec_from_file_location(dotted, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = mod
    spec.loader.exec_module(mod)
    return mod


def test_verify_host_only_allowlist_pass_and_fail(tmp_path):
    mod = load_cc("scripts/verify_host_only_allowlist.py")
    discovered = mod.discover_host_only_nodes()
    assert discovered
    allow = tmp_path / "allow.md"
    allow.write_text("\n".join(f"- {n}" for n in sorted(discovered)) + "\n")
    assert mod.main(["prog", str(allow)]) == 0
    allow.write_text("- MODULE does/not/exist.py\n")
    assert mod.main(["prog", str(allow)]) == 1
    missing = tmp_path / "nope.md"
    with pytest.raises(SystemExit):
        mod.parse_allowlist(missing)
    tree = __import__("ast").parse("pytestmark = pytest.mark.host_only\n")
    assert mod._module_level_host_only(tree) is True
    tree2 = __import__("ast").parse("pytestmark = [pytest.mark.host_only]\n")
    assert mod._module_level_host_only(tree2) is True
    tree3 = __import__("ast").parse("x = 1\n")
    assert mod._module_level_host_only(tree3) is False


def test_verify_merge_survivals_import_does_not_kill_pytest(monkeypatch):
    exits = []
    monkeypatch.setattr(sys, "exit", lambda code=0: exits.append(code))
    mod = load_cc("scripts/verify_merge_survivals.py")
    assert hasattr(mod, "check")
    assert mod.has("docker-compose.yml", "dmac-cc-net") is True
    assert mod.has("no/such.py", "x") is False
    assert mod.python_imports("nextseek_api/cc_assistant/router.py", "posterior_selector") is True
    assert mod.python_imports("nextseek_api/cc_assistant/apps.py", "does_not_exist") is False
    assert isinstance(mod.python_calls_attr(
        "nextseek_api/cc_assistant/cc_engine.py", "logger", "info"
    ), bool)
    assert exits  # module-level sys.exit was captured


def test_extract_step7_upstream_catalog_from_fixtures(tmp_path):
    mod = load_cc("scripts/extract_step7_upstream_catalog.py")
    t18 = tmp_path / "tools" / "e2e" / "run_t18_rewire_e2e.py"
    router = tmp_path / "tools" / "e2e" / "run_router_e2e.py"
    t18.parent.mkdir(parents=True)
    t18.write_text(
        'REPORT_PROJECT = "Published Data"\n'
        "PAID_PROJECTIONS = [\n"
        '    ("entity", "m", 0.1, {"a": 1}),\n'
        '    ("skip-me", "m", 0.1, {}),\n'
        '    ("api-read", "m", 0.1, {"parser_plan": {"ok": True}}),\n'
        '    ("generate-submission", "m", 0.1, {"uids": "old"}),\n'
        "]\n"
        "    for op, model, projected, args_dict in PAID_PROJECTIONS:\n"
        "        pass\n",
        encoding="utf-8",
    )
    router.write_text("DISCRIMINATORS = []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PAID_PROJECTIONS not found"):
        mod._extract_paid_projections("nope")
    with pytest.raises(ValueError, match="REPORT_PROJECT"):
        mod._extract_report_project("nope")
    catalog = mod.build_catalog(tmp_path, geo_uid_override="UID-1")
    ops = {e["bin_op"] for e in catalog["exercises"]}
    assert "nextseek-entity-extract" in ops
    assert "nextseek-api-write" in ops
    gen = next(e for e in catalog["exercises"] if e["bin_op"] == "nextseek-generate-submission")
    assert gen["inputs"]["uids"] == "UID-1"
    assert gen["mapping_rationale"].startswith("Instance")
    out = tmp_path / "out.json"
    assert mod.main(["--upstream-root", str(tmp_path), "--out", str(out), "--uid", "UID-1"]) == 0
    assert out.is_file()
    assert mod.main(["--upstream-root", str(tmp_path / "missing")]) == 2
    assert mod._extract_search_basic_query("x") == "find mice"


def test_step7_validator_dry_run_estimate_and_unexpected(monkeypatch, tmp_path):
    mod = load_cc("scripts/step7_validator_dry_run.py")
    from nextseek_api.cc_assistant.tests import step7_compose_fixtures as fx

    def fake_run(cmd, **kw):
        b = Path(cmd[-2])
        if b.name == "estimate":
            return types.SimpleNamespace(returncode=1, stdout="cost_ledger.json missing", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="cost_ledger ok", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    code, out = mod._run_validator(tmp_path / "estimate", tmp_path)
    assert code == 1 and "missing" in out

    def fake_repo(root, content=b""):
        return tmp_path / "git", "a" * 40

    def fake_bundle(bundle, tracker, deploy_commit=None, meta_overrides=None, **kw):
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "cost_ledger.json").write_text("{}")

    monkeypatch.setattr(fx, "_repo_with_transcript", fake_repo)
    monkeypatch.setattr(fx, "_full_bundle", fake_bundle)
    assert mod.main() == 0

    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="other fail", stderr=""),
    )
    assert mod.main() == 1

    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    assert mod.main() == 1


def test_step7_host_finalize_writes_preflight_and_syncs_meta(monkeypatch, tmp_path):
    mod = load_cc("scripts/step7_gate3d_host_finalize.py")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "meta.json").write_text(json.dumps({"repo_branch": "old"}))
    (bundle / "live_bundle_manifest.json").write_text(json.dumps({"repo_commit": "x"}))
    monkeypatch.setattr(
        mod.preflight_mod,
        "default_git_probe",
        lambda root: object(),
    )
    monkeypatch.setattr(mod.preflight_mod, "default_docker_probe", lambda: object())
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(stdout="deadbeef\n", returncode=0),
    )
    monkeypatch.setattr(
        mod.preflight_mod,
        "collect_preflight",
        lambda **k: {"commit": "c" * 40, "branch": "cc-step7-compose-native", "step3_deploy_gate": {}},
    )
    data = mod.collect_and_write_preflight(bundle, repo_root=tmp_path)
    assert data["commit"].startswith("c")
    mod.sync_meta_from_preflight(bundle, data)
    meta = json.loads((bundle / "meta.json").read_text())
    assert meta["repo_commit"] == "c" * 40
    monkeypatch.setattr(mod, "collect_and_write_preflight", lambda *a, **k: data)
    monkeypatch.setattr(mod, "validate_run", lambda *a, **k: (True, []))
    monkeypatch.setattr(mod, "format_report", lambda *a, **k: "ok")
    assert mod.main([str(bundle), "--repo-root", str(tmp_path)]) == 0
    assert mod.main([str(tmp_path / "missing")]) == 2
    monkeypatch.setattr(mod, "validate_run", lambda *a, **k: (False, []))
    assert mod.main([str(bundle), "--repo-root", str(tmp_path)]) == 1


def test_gate3d_live_and_per_op_package_load(monkeypatch, tmp_path):
    monkeypatch.setattr("django.setup", lambda: None)
    live = load_cc("scripts/step7_gate3d_live.py")
    assert live._cc_run_id("a1b2c3") == "a1b2c3"
    stripped = live._cc_run_id("dead-beef-XYZ")
    assert stripped == "deadbeef" or set(stripped) <= set("0123456789abcdef-")
    uuid_fallback = live._cc_run_id("ZZZ")
    assert len(uuid_fallback) >= 8
    out = tmp_path / "gate3d.json"
    live._write_json(out, {"ok": 1})
    assert json.loads(out.read_text())["ok"] == 1
    per_op = load_cc("scripts/step7_gate3d_per_op.py")
    assert per_op.OP_QUERIES["nextseek-report"]
    steps, sid, raw = per_op._read_transcript_steps(None, since=0)
    assert steps == [] and raw == b""
    steps, sid, raw = per_op._read_transcript_steps(str(tmp_path / "nope"), since=0)
    assert steps == []


def test_live_probe_missing_memory_and_runner(monkeypatch, tmp_path):
    events = []

    class Paths:
        @staticmethod
        def from_env():
            return object()

    class Dirs:
        memory_mnt = str(tmp_path / "missing-dir")

    monkeypatch.setenv("PROBE_MEMORY_MNT", str(tmp_path / "no-file.md"))
    probe = load_cc("evidence/run_1c_claude_md_live_probe.py")
    monkeypatch.setattr(probe.cc_config.CCPaths, "from_env", Paths.from_env)
    monkeypatch.setattr(probe, "build_user_dirs", lambda *a, **k: Dirs())
    assert probe.main() == 2
    mem = tmp_path / "CLAUDE.md"
    mem.write_text("x")
    monkeypatch.setenv("PROBE_MEMORY_MNT", str(mem))
    monkeypatch.setattr(probe.cc_engine, "cc_runner_available", lambda: (False, "down"))
    assert probe.main() == 3
    monkeypatch.setattr(probe.cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(probe.cc_router, "_resolve_cc_model_id", lambda: "opus")

    def fake_turn(**kw):
        kw["send_event"]("query_complete", {"reply": '{"saw_write_safety": true, "saw_user_memory_marker": true}'})

    monkeypatch.setattr(probe.cc_engine, "run_cc_turn", fake_turn)
    assert probe.main() == 0

    def no_terminal(**kw):
        pass

    monkeypatch.setattr(probe.cc_engine, "run_cc_turn", no_terminal)
    assert probe.main() == 4

    def err(**kw):
        kw["send_event"]("query_error", {"error": "x"})

    monkeypatch.setattr(probe.cc_engine, "run_cc_turn", err)
    assert probe.main() == 5

    def bad_json(**kw):
        kw["send_event"]("query_complete", {"reply": "no-json"})

    monkeypatch.setattr(probe.cc_engine, "run_cc_turn", bad_json)
    assert probe.main() == 6
