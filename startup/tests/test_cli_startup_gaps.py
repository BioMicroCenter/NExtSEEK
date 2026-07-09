"""AST-based tests for startup CLI wiring of generated deploy files.

Replaces source-substring greps (`"config.render_proxy_secret_env(REPO_ROOT)"
in text`) with assertions on the real parsed AST of `install()`'s body — a
substring grep still passes if the call is dead/commented-out differently
(e.g. reformatted, or `# config.render_proxy_secret_env(REPO_ROOT)`), an AST
walk does not.

Two properties are checked:
  1. Presence — install() (and validate.run_all_health_checks()) still
     contain Call nodes for the required helpers (test_install_calls_all_
     render_and_cc_phases, test_health_checks_include_cc_checks).
  2. Order — the calls that matter (render proxy secret -> render root .env
     -> start the CC stack -> run health checks) fire in that order when
     install() is actually run (test_install_calls_render_start_health_in_
     order). Presence alone can't catch a call moved before its
     dependencies exist, or hidden in an unreachable branch.
"""
from __future__ import annotations

import ast
from pathlib import Path

from startup import cli

CLI_PATH = Path(__file__).resolve().parents[1] / "cli.py"

# Calls install() must make directly (walked from its own AST). Verified via
# test_install_calls_all_render_and_cc_phases below. check_proxy_token /
# check_cc_services are NOT install()-level calls -- they're made inside
# validate.run_all_health_checks (a different function, different file) and
# are covered separately by test_health_checks_include_cc_checks.
REQUIRED_INSTALL_CALLS = {
    "render_proxy_secret_env",
    "render_root_env",
    "start_cc_stack",
}


def _called_names(func: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_install_calls_all_render_and_cc_phases() -> None:
    tree = ast.parse(CLI_PATH.read_text())
    install = _find_function(tree, "install")
    calls = _called_names(install)
    for required in REQUIRED_INSTALL_CALLS:
        assert required in calls, f"install() no longer calls {required}"


def test_install_calls_render_start_health_in_order(monkeypatch) -> None:
    """Run cli.install() end-to-end with every side-effecting phase stubbed,
    and assert the four load-bearing calls fire in order: proxy secret
    rendered -> root .env rendered -> CC stack started -> health checked.

    This is the live-invocation counterpart to the AST presence test above:
    it closes the dead-code/reorder bypass that a pure AST walk of install()
    cannot catch (e.g. start_cc_stack moved before the env files it depends
    on exist, or into a branch that never runs for this call).
    """
    calls: list[str] = []

    # --- [1/9] Prereqs: no real docker/compose/uv/disk probing.
    monkeypatch.setattr(cli.prereqs, "run_all", lambda: [])

    # --- [3/9] Instance + ports: deterministic, no real state file touched.
    monkeypatch.setattr(cli, "load_instance", lambda root: None)
    monkeypatch.setattr(cli, "save_instance", lambda root, state: None)
    monkeypatch.setattr(cli, "allocate_ports", lambda desired: dict(desired))

    # --- [4/9] Config templates: only the two tracked renders are allowed to
    # "run" (as no-op trackers below); the others would otherwise overwrite
    # this checkout's real docker/db.env, docker/nextseek.env, and
    # dmac/local_settings.py.
    monkeypatch.setattr(cli.config, "render_db_env", lambda root, values: None)
    monkeypatch.setattr(cli.config, "render_nextseek_env", lambda root, values: None)
    monkeypatch.setattr(cli.config, "render_local_settings", lambda root, values: None)
    monkeypatch.setattr(
        cli.config,
        "render_proxy_secret_env",
        lambda root: calls.append("proxy") or root / "proxy-secret.env",
    )
    monkeypatch.setattr(
        cli.config,
        "render_root_env",
        lambda root, env: calls.append("root") or root / ".env",
    )

    # --- [5/9] Volumes: no real `docker volume create`.
    monkeypatch.setattr(cli.volumes, "ensure_volumes", lambda prefix: [])
    monkeypatch.setattr(cli.volumes, "ensure_cc_staging_dir", lambda prefix: None)

    # --- [6/9] Seeds: no_seed=True still starts the DB containers on the
    # real path, and schema fixups query the live DB -- stub both.
    monkeypatch.setattr(cli.build, "start_databases", lambda root, env: None)
    monkeypatch.setattr(cli.schema_fixups, "apply_all", lambda root, env: [])

    # --- [7/9] Build + start: the phase under test for start_cc_stack, plus
    # its neighboring real-docker/network calls.
    monkeypatch.setattr(cli.build, "start_seek_side", lambda root, env: None)
    monkeypatch.setattr(cli.build, "build_and_start_nextseek", lambda root, env: None)
    monkeypatch.setattr(
        cli.build, "start_cc_stack", lambda root, env: calls.append("start")
    )
    monkeypatch.setattr(cli.build, "wait_for_nextseek_http", lambda port: None)
    monkeypatch.setattr(
        cli.seed_cleanup, "clear_stale_chat_sessions", lambda root, env: 0
    )

    # --- [8/9] Users: no real DB lookup.
    monkeypatch.setattr(cli.users, "verify_users_present", lambda root, env: [])

    # --- [9/9] Health: the fourth tracked call.
    monkeypatch.setattr(
        cli.validate,
        "run_all_health_checks",
        lambda ports, root, env: calls.append("health") or [],
    )

    cli.install(instance=None, port_offset=None, no_seed=True, yes=True)

    assert calls.index("proxy") < calls.index("root") < calls.index("start") < calls.index("health")


def test_health_checks_include_cc_checks() -> None:
    validate_src = (CLI_PATH.parent / "steps" / "validate.py").read_text()
    tree = ast.parse(validate_src)
    run_all = _find_function(tree, "run_all_health_checks")
    calls = _called_names(run_all)
    assert "check_proxy_token" in calls and "check_cc_services" in calls
