"""Tests for startup.steps.deploy_checks: what runs is what the checkout builds.

No docker here: every container answer is a canned string, and the pins are read
from the real tree (so a moved Dockerfile, pyproject, proxy config or model map
fails here, not on a box).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from startup.steps import deploy_checks as dc
from startup.steps import validate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tree(tmp_path: Path, *, node="22", claude="2.1.282",
          extra=('"markitdown==0.1.6"', '"matplotlib>=3.10.7"'),
          allowed=("us.anthropic.claude-opus-4-8", "us.anthropic.claude-opus-4-7",
                   "us.anthropic.claude-sonnet-4-6"),
          model_map=None) -> Path:
    """A checkout holding just the four files the checks read."""
    (tmp_path / dc.CC_RUNTIME_DOCKERFILE).parent.mkdir(parents=True)
    (tmp_path / dc.CC_RUNTIME_DOCKERFILE).write_text(
        f"FROM --platform=linux/amd64 node:{node}-bookworm-slim AS base\n"
        f"RUN npm install -g @anthropic-ai/claude-code@{claude}\n")
    (tmp_path / dc.CC_RUNTIME_PYPROJECT).write_text(
        "[project]\nname = 'x'\n[project.optional-dependencies]\n"
        f"container = [{', '.join(extra)}]\n")
    (tmp_path / dc.PROXY_CONFIG).parent.mkdir(parents=True)
    (tmp_path / dc.PROXY_CONFIG).write_text(
        f"_DEFAULT_ALLOWED_MODELS: tuple[str, ...] = {tuple(allowed)!r}\n")
    (tmp_path / dc.MODEL_CLASS_MAP).parent.mkdir(parents=True)
    (tmp_path / dc.MODEL_CLASS_MAP).write_text(json.dumps(model_map or {
        "opus": "us.anthropic.claude-opus-4-8", "sonnet": "us.anthropic.claude-sonnet-4-6",
        "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "opus_fallback": "us.anthropic.claude-opus-4-7"}))
    return tmp_path


# ---- the pins are read from the real tree -------------------------------------------

def test_every_file_the_checks_read_is_in_the_tree():
    for rel in (dc.CC_RUNTIME_DOCKERFILE, dc.CC_RUNTIME_PYPROJECT, dc.PROXY_CONFIG,
                dc.MODEL_CLASS_MAP):
        assert (REPO_ROOT / rel).is_file(), rel


def test_the_real_tree_states_every_cc_runtime_pin():
    expected = dc.expected_cc_runtime(REPO_ROOT)
    assert isinstance(expected["node_major"], int)
    assert expected["claude_code"] and expected["claude_code"][0].isdigit()
    assert expected["dists"], "the cc-runtime container extra lists nothing"


def test_the_real_proxy_allow_list_starts_with_the_maps_opus():
    assert dc.expected_proxy_allow_list(REPO_ROOT)[0] == dc.model_class_map(REPO_ROOT)["opus"]


def test_the_app_code_roots_exist_and_the_context_files_are_left_out():
    paths = dc.app_code_paths(REPO_ROOT)
    assert len(paths) > 500
    assert "seek/views/search.py" in paths
    assert "static/js/chat_assistant/.vite/manifest.json" in paths
    assert not [p for p in paths if p.startswith("NessieAI/history/")]
    assert not [p for p in paths if "/chat_nextseek/context/" in p]


# ---- cc-agent runtime ------------------------------------------------------------------

def test_expected_cc_runtime_reads_node_claude_code_and_the_container_extra(tmp_path):
    expected = dc.expected_cc_runtime(_tree(tmp_path))
    assert expected == {"node_major": 22, "claude_code": "2.1.282",
                        "dists": ["markitdown", "matplotlib"]}


def _seen(node="v22.11.0", claude="2.1.282 (Claude Code)", dists=None, imports=None):
    return {"node": node, "claude": claude,
            "dists": dists if dists is not None else {"markitdown": "0.1.6", "matplotlib": "3.10.7"},
            "imports": imports if imports is not None else {"matplotlib.pyplot": "ok"}}


def test_an_image_as_the_checkout_builds_it_has_no_problem(tmp_path):
    assert dc.cc_runtime_problems(dc.expected_cc_runtime(_tree(tmp_path)), _seen()) == []


def test_the_old_image_names_every_expected_value(tmp_path):
    """The 25 Sep image: node 20, Claude Code 2.1.163, no matplotlib."""
    problems = dc.cc_runtime_problems(
        dc.expected_cc_runtime(_tree(tmp_path)),
        _seen(node="v20.20.2", claude="2.1.163 (Claude Code)",
              dists={"markitdown": "0.1.6", "matplotlib": None}, imports={}))
    text = "; ".join(problems)
    assert "node v20.20.2 where the checkout builds FROM node:22" in text
    assert "where the checkout pins @anthropic-ai/claude-code@2.1.282" in text
    assert "matplotlib is not installed" in text


def test_an_installed_chart_library_that_does_not_import_is_a_problem(tmp_path):
    problems = dc.cc_runtime_problems(
        dc.expected_cc_runtime(_tree(tmp_path)),
        _seen(imports={"matplotlib.pyplot": "ImportError: libfreetype.so.6"}))
    assert problems == ["matplotlib.pyplot does not import: ImportError: libfreetype.so.6"]


def test_check_cc_agent_runtime_runs_no_agent_and_no_network(tmp_path, monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(dc, "image_exists", lambda image: True)

    def fake_run(cmd, **kwargs):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "noise\nCC-RUNTIME " + json.dumps(_seen()) + "\n", "")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    result = dc.check_cc_agent_runtime(_tree(tmp_path), "nextseek")

    assert result.ok is True and not result.warn
    assert "Claude Code 2.1.282" in result.detail and "matplotlib.pyplot imports" in result.detail
    (cmd,) = ran
    assert cmd[:7] == ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python"]
    assert cmd[7] == validate._cc_agent_image("nextseek")
    assert cmd[-2:] == ["markitdown", "matplotlib"]
    assert not [a for a in cmd if a in ("-v", "--volume", "--mount")]


def test_check_cc_agent_runtime_fails_stale_with_the_rebuild_command(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "image_exists", lambda image: True)
    old = _seen(node="v20.20.2", claude="2.1.163 (Claude Code)")
    monkeypatch.setattr(dc.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, "CC-RUNTIME " + json.dumps(old), ""))

    result = dc.check_cc_agent_runtime(_tree(tmp_path), "nextseek")

    assert result.ok is False
    assert result.detail.startswith("STALE: ")
    assert result.detail.endswith("Rebuild with: ./startup.sh rebuild --component cc-agent")


def test_check_cc_agent_runtime_skips_an_absent_image(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "image_exists", lambda image: False)
    result = dc.check_cc_agent_runtime(_tree(tmp_path), "nextseek")
    assert result.ok is True and result.warn is True and "absent" in result.detail


def test_check_cc_agent_runtime_reports_an_image_that_prints_no_report(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "image_exists", lambda image: True)
    monkeypatch.setattr(dc.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "exec: python: not found"))
    result = dc.check_cc_agent_runtime(_tree(tmp_path), "nextseek")
    assert result.ok is False and "python: not found" in result.detail


# ---- bedrock-proxy allow list and the CC command --------------------------------------

ALLOWED = ["us.anthropic.claude-opus-4-8", "us.anthropic.claude-opus-4-7",
           "us.anthropic.claude-sonnet-4-6"]


def test_the_running_proxy_allowing_the_checkouts_list_passes(tmp_path):
    expected = dc.expected_proxy_allow_list(_tree(tmp_path))
    assert dc.proxy_result(expected, list(reversed(ALLOWED))).ok is True


def test_an_old_proxy_names_what_it_misses(tmp_path):
    expected = dc.expected_proxy_allow_list(_tree(tmp_path))
    result = dc.proxy_result(expected, ["us.anthropic.claude-opus-4-8"])
    assert result.ok is False
    assert "missing us.anthropic.claude-opus-4-7, us.anthropic.claude-sonnet-4-6" in result.detail
    assert "./startup.sh rebuild --component bedrock-proxy" in result.detail


def _wiring(model="us.anthropic.claude-opus-4-8", fallback="us.anthropic.claude-opus-4-7",
            retries="3", timeout="60000", sonnet="us.anthropic.claude-sonnet-4-6"):
    return {"model": model, "fallback_model": fallback,
            "env": {"CLAUDE_CODE_MAX_RETRIES": retries, "API_TIMEOUT_MS": timeout,
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": sonnet}}


def test_the_new_app_and_the_new_proxy_agree(tmp_path):
    result = dc.cc_wiring_result(dc.model_class_map(_tree(tmp_path)), _wiring(), ALLOWED)
    assert result.ok is True
    assert "--fallback-model us.anthropic.claude-opus-4-7" in result.detail
    assert "retries 3" in result.detail and "60000 ms" in result.detail


def test_an_old_app_names_the_fallback_it_does_not_pass(tmp_path):
    """The 25 Sep app image: no --fallback-model, none of the three env keys."""
    old = _wiring(fallback=None, retries=None, timeout=None, sonnet=None)
    result = dc.cc_wiring_result(dc.model_class_map(_tree(tmp_path)), old, ALLOWED)
    assert result.ok is False
    assert ("--fallback-model is None where the checkout's map says "
            "us.anthropic.claude-opus-4-7") in result.detail
    assert "no CLAUDE_CODE_MAX_RETRIES" in result.detail
    assert result.detail.endswith("Rebuild with: ./startup.sh rebuild")


def test_a_new_app_behind_an_old_proxy_is_the_deploy_order_failure(tmp_path):
    result = dc.cc_wiring_result(dc.model_class_map(_tree(tmp_path)), _wiring(),
                                 ["us.anthropic.claude-opus-4-8"])
    assert result.ok is False
    assert "us.anthropic.claude-opus-4-7, us.anthropic.claude-sonnet-4-6" in result.detail
    assert "which the running bedrock-proxy refuses" in result.detail


def test_a_checkout_without_a_fallback_expects_none(tmp_path):
    """Before the CC fallback, the map has no opus_fallback and nothing is required."""
    tree = _tree(tmp_path, model_map={"opus": "us.anthropic.claude-opus-4-8",
                                      "sonnet": "us.anthropic.claude-sonnet-4-6",
                                      "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0"})
    old = _wiring(fallback=None, retries=None, timeout=None, sonnet=None)
    result = dc.cc_wiring_result(dc.model_class_map(tree), old, ["us.anthropic.claude-opus-4-8"])
    assert result.ok is True and "no fallback model" in result.detail


def test_check_cc_models_reads_the_running_proxy_and_app(tmp_path, monkeypatch):
    asked: list[str] = []

    def fake_exec(service, command, project_dir, env, **kwargs):
        asked.append(service)
        if service == "bedrock-proxy":
            assert command[:2] == ["python", "-c"]
            return "PROXY-ALLOW " + json.dumps(ALLOWED) + "\n"
        assert command[:4] == ["uv", "run", "--no-sync", "python"]
        return "CC-WIRING " + json.dumps(_wiring()) + "\n"

    monkeypatch.setattr(dc, "compose_exec", fake_exec)
    proxy, wiring = dc.check_cc_models(Path("/repo"), {}, _tree(tmp_path))
    assert asked == ["bedrock-proxy", "nextseek"]
    assert proxy.ok and wiring.ok


def test_a_stopped_proxy_fails_both_lines_with_the_reason(tmp_path, monkeypatch):
    def fake_exec(service, command, project_dir, env, **kwargs):
        if service == "bedrock-proxy":
            raise validate.DockerOpsError("service \"bedrock-proxy\" is not running")
        return "CC-WIRING " + json.dumps(_wiring()) + "\n"

    monkeypatch.setattr(dc, "compose_exec", fake_exec)
    proxy, wiring = dc.check_cc_models(Path("/repo"), {}, _tree(tmp_path))
    assert proxy.ok is False and "is not running" in proxy.detail
    # With no proxy answer, the wiring line still judges the app against the map.
    assert wiring.ok is True and "allowed by the running proxy" not in wiring.detail


# ---- app image code --------------------------------------------------------------------

def test_the_app_code_probe_reads_digests_on_stdin_and_names_the_mismatches(tmp_path, monkeypatch):
    sent: dict = {}

    def fake_exec(service, command, project_dir, env, stdin=None, **kwargs):
        sent.update(json.loads(stdin))
        assert service == "nextseek"
        return "APP-CODE " + json.dumps({"checked": len(sent), "differs": ["seek/views/search.py"],
                                         "absent": []})

    monkeypatch.setattr(dc, "compose_exec", fake_exec)
    monkeypatch.setattr(dc, "app_code_paths", lambda checkout: ["seek/views/search.py"])
    (tmp_path / "seek" / "views").mkdir(parents=True)
    (tmp_path / "seek" / "views" / "search.py").write_text("x = 1\n")

    result = dc.check_app_code(Path("/repo"), {}, tmp_path)

    assert list(sent) == ["seek/views/search.py"]
    assert result.ok is False
    assert "1 differ (seek/views/search.py)" in result.detail
    assert result.detail.endswith("Rebuild with: ./startup.sh rebuild")


def test_the_app_code_probe_itself_hashes_what_it_is_sent(tmp_path, monkeypatch):
    """Run the in-container script here, against a fake /app."""
    (tmp_path / "a.py").write_text("same\n")
    (tmp_path / "b.py").write_text("changed\n")
    import hashlib
    want = {"a.py": hashlib.sha256(b"same\n").hexdigest(),
            "b.py": hashlib.sha256(b"original\n").hexdigest(),
            "c.py": "0" * 64}
    script = dc.APP_CODE_PROBE.replace("'/app'", repr(str(tmp_path)))
    out = subprocess.run(["python3", "-c", script], input=json.dumps(want),
                         capture_output=True, text=True, check=True).stdout
    report = dc._marked_json(out, "APP-CODE")
    assert report == {"checked": 3, "differs": ["b.py"], "absent": ["c.py"]}


def test_every_file_matching_is_a_pass():
    result = dc.app_code_result({"checked": 2119, "differs": [], "absent": []})
    assert result.ok is True and "all 2119" in result.detail


# ---- wiring into stack health ----------------------------------------------------------

def test_deploy_checks_prints_in_a_fixed_order(monkeypatch):
    ok = lambda name: validate.HealthResult(name, True, "d")  # noqa: E731
    monkeypatch.setattr(dc, "check_app_code", lambda r, e, c: ok("app image code"))
    monkeypatch.setattr(dc, "check_cc_agent_runtime", lambda c, n: ok("cc-agent runtime"))
    monkeypatch.setattr(dc, "check_cc_models", lambda r, e, c: (
        ok("bedrock-proxy allow list"), ok("CC fallback wiring")))
    names = [r.name for r in dc.deploy_checks(Path("/repo"), {}, "nextseek", Path("/repo"))]
    assert names == ["app image code", "cc-agent runtime", "bedrock-proxy allow list",
                     "CC fallback wiring"]


@pytest.mark.parametrize("probe", [dc.CC_RUNTIME_PROBE, dc.PROXY_PROBE, dc.CC_WIRING_PROBE,
                                   dc.APP_CODE_PROBE])
def test_every_in_container_script_is_valid_python(probe):
    compile(probe, "<probe>", "exec")
