"""AST-based tests for startup CLI wiring of generated deploy files.

Replaces source-substring greps (`"config.render_proxy_secret_env(REPO_ROOT)"
in text`) with assertions on the real parsed AST of `_install_impl()`'s body
(the plain-Python install body; `install` itself is a bare typer delegate) — a
substring grep still passes if the call is dead/commented-out differently
(e.g. reformatted, or `# config.render_proxy_secret_env(REPO_ROOT)`), an AST
walk does not.

Two properties are checked:
  1. Presence — _install_impl() (and validate.run_all_health_checks()) still
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
import inspect
from pathlib import Path

import pytest
import typer

from startup import cli

CLI_PATH = Path(__file__).resolve().parents[1] / "cli.py"

# Calls _install_impl() must make directly (walked from its own AST; the
# @app.command() install is a bare delegate to it). Verified via
# test_install_calls_all_render_and_cc_phases below. check_proxy_token /
# check_cc_services are NOT _install_impl()-level calls -- they're made inside
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
    impl = _find_function(tree, "_install_impl")
    calls = _called_names(impl)
    for required in REQUIRED_INSTALL_CALLS:
        assert required in calls, f"_install_impl() no longer calls {required}"


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
    # Same reason: the site_base_host apply queries the live SEEK DB.
    monkeypatch.setattr(
        cli.seek_settings, "apply_site_base_host", lambda root, env, url: "applied"
    )

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

    cli.install(instance=None, port_offset=None, no_seed=True, seek_public_url=None, yes=True)

    assert calls.index("proxy") < calls.index("root") < calls.index("start") < calls.index("health")


def test_install_applies_site_base_host_before_seek_first_boot(monkeypatch) -> None:
    """SEEK's site_base_host must be set BEFORE seek ever boots.

    SEEK builds its sitemap at container start from this value and caches the
    config on first read. Applying it beforehand is what makes a fresh install
    correct from minute one and removes any need to restart seek afterwards --
    a restart being a real outage on a long-lived instance. If the apply ever
    drifts after start_seek_side, this test fails.
    """
    calls: list[str] = []

    monkeypatch.setattr(cli.prereqs, "run_all", lambda: [])
    monkeypatch.setattr(cli, "load_instance", lambda root: None)
    monkeypatch.setattr(cli, "save_instance", lambda root, state: None)
    monkeypatch.setattr(cli, "allocate_ports", lambda desired: dict(desired))
    for name in ("render_db_env", "render_nextseek_env", "render_local_settings"):
        monkeypatch.setattr(cli.config, name, lambda root, values: None)
    monkeypatch.setattr(cli.config, "render_proxy_secret_env", lambda root: root / "p.env")
    monkeypatch.setattr(cli.config, "render_root_env", lambda root, env: root / ".env")
    monkeypatch.setattr(cli.volumes, "ensure_volumes", lambda prefix: [])
    monkeypatch.setattr(cli.volumes, "ensure_cc_staging_dir", lambda prefix: None)
    monkeypatch.setattr(cli.build, "start_databases", lambda root, env: None)
    monkeypatch.setattr(cli.schema_fixups, "apply_all", lambda root, env: [])
    monkeypatch.setattr(
        cli.seek_settings,
        "apply_site_base_host",
        lambda root, env, url: calls.append("site_base_host") or "applied",
    )
    monkeypatch.setattr(
        cli.build, "start_seek_side", lambda root, env: calls.append("seek_boot")
    )
    monkeypatch.setattr(cli.build, "build_and_start_nextseek", lambda root, env: None)
    monkeypatch.setattr(cli.build, "start_cc_stack", lambda root, env: None)
    monkeypatch.setattr(cli.build, "wait_for_nextseek_http", lambda port: None)
    monkeypatch.setattr(cli.seed_cleanup, "clear_stale_chat_sessions", lambda root, env: 0)
    monkeypatch.setattr(cli.users, "verify_users_present", lambda root, env: [])
    monkeypatch.setattr(cli.validate, "run_all_health_checks", lambda ports, root, env: [])

    cli.install(instance=None, port_offset=None, no_seed=True, seek_public_url=None, yes=True)

    assert "site_base_host" in calls, "install must apply SEEK's site_base_host"
    assert "seek_boot" in calls
    assert calls.index("site_base_host") < calls.index("seek_boot"), (
        "site_base_host must be applied before seek's first boot, or the boot-time "
        "sitemap is built from the wrong value and a restart becomes necessary"
    )


def test_health_checks_include_cc_checks() -> None:
    validate_src = (CLI_PATH.parent / "steps" / "validate.py").read_text()
    tree = ast.parse(validate_src)
    run_all = _find_function(tree, "run_all_health_checks")
    calls = _called_names(run_all)
    assert "check_proxy_token" in calls and "check_cc_services" in calls


def test_reset_carries_seek_public_url_into_the_reinstall(monkeypatch) -> None:
    """reset() deletes .instance.json, so it must hand the URL to the re-install.

    Without this, `reset` silently regresses a dev/prod instance's SEEK URL to the
    localhost default -- the value would be destroyed with the instance state and
    nothing would put it back.
    """
    from startup.lib.instance import InstanceState

    captured: dict = {}
    monkeypatch.setattr(
        cli,
        "load_instance",
        lambda root: InstanceState(
            name="dev",
            prefix="",
            ports={"nextseek": 8000, "seek": 3000},
            compose_project_name="nextseek",
            created="2026-07-16T00:00:00Z",
            seek_public_url="https://fairdata-dev.mit.edu",
        ),
    )
    monkeypatch.setattr("startup.lib.docker_ops.compose_down", lambda **kw: None)
    monkeypatch.setattr(cli, "install", lambda **kw: captured.update(kw))

    cli.reset(instance=None, keep_config=True, yes=True)

    assert captured.get("seek_public_url") == "https://fairdata-dev.mit.edu", (
        "reset must carry the instance's SEEK public URL into the re-install"
    )


def _stub_instance_state():
    from startup.lib.instance import InstanceState

    return InstanceState(
        name="dev",
        prefix="",
        ports={"nextseek": 8000, "seek": 3000},
        compose_project_name="nextseek",
        created="2026-07-16T00:00:00Z",
        seek_public_url="https://fairdata-dev.mit.edu",
    )


def test_reset_reinstall_requests_seeding(monkeypatch) -> None:
    """reset() is the drop-volumes path — its re-install MUST seed.

    Regression test for the OptionInfo-sentinel bug: reset() called install()
    directly as a Python function without passing no_seed, so no_seed arrived
    as its typer.Option(...) default — a truthy typer.models.OptionInfo — and
    the reinstall silently skipped all three DB seeds AND the filestore seed
    over freshly-wiped volumes. This runs reset() end-to-end through the real
    install chain (every side-effecting phase stubbed) and asserts both seed
    gates are actually entered.
    """
    calls: list[str] = []

    # --- reset()'s own phases: instance state + compose down. keep_config=True
    # so no real config files in this checkout are unlinked.
    monkeypatch.setattr(cli, "load_instance", lambda root: _stub_instance_state())
    monkeypatch.setattr("startup.lib.docker_ops.compose_down", lambda **kw: None)

    # --- install phases, stubbed exactly like the in-order test above.
    monkeypatch.setattr(cli.prereqs, "run_all", lambda: [])
    monkeypatch.setattr(cli, "save_instance", lambda root, state: None)
    monkeypatch.setattr(cli, "allocate_ports", lambda desired: dict(desired))
    for name in ("render_db_env", "render_nextseek_env", "render_local_settings"):
        monkeypatch.setattr(cli.config, name, lambda root, values: None)
    monkeypatch.setattr(cli.config, "render_proxy_secret_env", lambda root: root / "p.env")
    monkeypatch.setattr(cli.config, "render_root_env", lambda root, env: root / ".env")
    monkeypatch.setattr(cli.volumes, "ensure_volumes", lambda prefix: [])
    monkeypatch.setattr(cli.volumes, "ensure_cc_staging_dir", lambda prefix: None)
    monkeypatch.setattr(cli.build, "start_databases", lambda root, env: None)
    monkeypatch.setattr(cli.schema_fixups, "apply_all", lambda root, env: [])
    monkeypatch.setattr(
        cli.seek_settings, "apply_site_base_host", lambda root, env, url: "applied"
    )
    monkeypatch.setattr(cli.build, "start_seek_side", lambda root, env: None)
    monkeypatch.setattr(cli.build, "build_and_start_nextseek", lambda root, env: None)
    monkeypatch.setattr(cli.build, "start_cc_stack", lambda root, env: None)
    monkeypatch.setattr(cli.build, "wait_for_nextseek_http", lambda port: None)
    monkeypatch.setattr(cli.seed_cleanup, "clear_stale_chat_sessions", lambda root, env: 0)
    monkeypatch.setattr(cli.users, "verify_users_present", lambda root, env: [])
    monkeypatch.setattr(cli.validate, "run_all_health_checks", lambda ports, root, env: [])

    # --- The two seed gates under test. "Already populated" so no dump loads
    # run — reaching the populated-checks at all proves each gate was entered.
    monkeypatch.setattr(cli.seed, "seed_files_present", lambda root: [])
    monkeypatch.setattr(
        cli.seed,
        "mysql_db_is_populated",
        lambda db, root, env: calls.append(f"db_seed_gate:{db}") or True,
    )
    monkeypatch.setattr(
        cli.seed,
        "neo4j_is_populated",
        lambda password, root, env: calls.append("db_seed_gate:neo4j") or True,
    )
    monkeypatch.setattr(
        cli.seed_filestore,
        "filestore_is_populated",
        lambda root, env: calls.append("filestore_gate") or True,
    )

    cli.reset(instance=None, keep_config=True, yes=True)

    assert "db_seed_gate:dmac" in calls, "reset's reinstall skipped the DB seed phase"
    assert "db_seed_gate:seek_production" in calls
    assert "db_seed_gate:neo4j" in calls
    assert "filestore_gate" in calls, "reset's reinstall skipped the filestore seed"


def test_reset_passes_no_seed_literal_false(monkeypatch) -> None:
    """The value reset hands across the install boundary must be the real
    Python literal False — asserted with `is`, so a truthy OptionInfo sentinel
    (or any other stand-in) can never satisfy it.
    """
    captured: dict = {}
    monkeypatch.setattr(cli, "load_instance", lambda root: _stub_instance_state())
    monkeypatch.setattr("startup.lib.docker_ops.compose_down", lambda **kw: None)
    monkeypatch.setattr(cli, "install", lambda **kw: captured.update(kw))

    cli.reset(instance=None, keep_config=True, yes=True)

    assert "no_seed" in captured, "reset must pass no_seed to install explicitly"
    assert captured["no_seed"] is False, (
        f"reset must pass the literal False, got {captured['no_seed']!r}"
    )


def test_reset_call_passes_every_install_parameter() -> None:
    """Signature parity: reset's install(...) call must pass EVERY parameter
    of install by keyword. A future sixth install parameter that reset fails
    to forward would otherwise arrive as its typer.Option(...) sentinel — the
    exact shape of the no_seed bug — so this turns red the moment the two
    drift apart.
    """
    tree = ast.parse(CLI_PATH.read_text())
    reset = _find_function(tree, "reset")
    call = next(
        n for n in ast.walk(reset)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "install"
    )
    passed = {kw.arg for kw in call.keywords}
    expected = set(inspect.signature(cli.install).parameters)
    assert passed == expected, (
        f"reset's install(...) call is missing parameter(s): {sorted(expected - passed)}"
    )


def test_install_command_body_is_only_the_impl_delegate() -> None:
    """The @app.command() install wrapper must contain NOTHING but the
    delegate call to _install_impl, forwarding every parameter by keyword.
    Any logic living in the typer wrapper would run with OptionInfo defaults
    when called directly as a Python function — the class of bug this file
    exists to prevent.
    """
    tree = ast.parse(CLI_PATH.read_text())
    install = _find_function(tree, "install")
    body = [
        n for n in install.body
        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
    ]  # drop the docstring
    assert len(body) == 1, "install() wrapper must be a single delegate statement"
    stmt = body[0]
    value = stmt.value if isinstance(stmt, (ast.Expr, ast.Return)) else None
    assert isinstance(value, ast.Call) and getattr(value.func, "id", "") == "_install_impl", (
        "install() wrapper must delegate to _install_impl"
    )
    forwarded = {kw.arg for kw in value.keywords}
    expected = set(inspect.signature(cli.install).parameters)
    assert forwarded == expected, (
        f"install() wrapper drops parameter(s): {sorted(expected - forwarded)}"
    )


def test_install_impl_defaults_are_plain_python_values() -> None:
    """_install_impl is plain Python: its defaults must be real values, never
    typer.models.OptionInfo sentinels, so direct calls behave like the CLI.
    """
    for name, param in inspect.signature(cli._install_impl).parameters.items():
        assert not isinstance(param.default, typer.models.OptionInfo), (
            f"_install_impl parameter {name!r} defaults to an OptionInfo sentinel"
        )


def test_install_impl_rejects_leaked_optioninfo_sentinel() -> None:
    """Defense-in-depth: if a future direct call leaks an unparsed
    typer.Option(...) default through to _install_impl, it must crash loudly
    at entry instead of silently misbehaving (an OptionInfo is truthy).
    """
    with pytest.raises(TypeError, match="OptionInfo"):
        cli._install_impl(no_seed=typer.Option(False, "--no-seed"))
