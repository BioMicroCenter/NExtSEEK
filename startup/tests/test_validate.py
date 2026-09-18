"""Tests for startup.steps.validate."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from startup.steps import validate
from startup.steps.validate import (
    HealthResult,
    check_http,
    check_prod_overlay_guard,
    run_django_check,
)


@patch("startup.steps.validate.urllib.request.urlopen")
def test_check_http_ok_returns_health_result(mock_urlopen: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response
    r = check_http("nextseek", "http://localhost:8000")
    assert r.ok is True
    assert r.detail.endswith("200")


@patch("startup.steps.validate.urllib.request.urlopen")
def test_check_http_failure_returns_not_ok(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = OSError("connection refused")
    r = check_http("nextseek", "http://localhost:8000")
    assert r.ok is False


@patch("startup.steps.validate.compose_exec")
def test_run_django_check_ok_on_clean_exit(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "System check identified no issues (0 silenced).\n"
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is True


@patch("startup.steps.validate.compose_exec")
def test_run_django_check_ok_on_zero_exit_warning_output(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "WARNINGS:\n?: (models.W042) Auto-created primary key used\n"
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is True
    assert "Auto-created primary key" in r.detail


@patch("startup.steps.validate.compose_exec")
def test_run_django_check_fail_on_exception(mock_exec: MagicMock) -> None:
    from startup.lib.docker_ops import DockerOpsError
    mock_exec.side_effect = DockerOpsError("boom")
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is False


# --- Review follow-up FU1 (2026-07-07): doctor check for the stale-overlay
# footgun. A deployment whose env sets NEXTSEEK_INTERNAL_BASE_URL while its
# hand-maintained dmac/local_settings.py predates the internal-URL guard
# (no pop, no config_map) would let the internal var silently point the PROD
# ChatConfig at the dev backend.

_STALE_OVERLAY = """\
import os
from chat_nextseek.config import ChatConfig
NEXTSEEK_CHAT_CONFIG = ChatConfig()
_PROD_OVERRIDES = {"NEXTSEEK_BASE_URL": None}
NEXTSEEK_CHAT_CONFIG_PROD = None
if any(v is not None for v in _PROD_OVERRIDES.values()):
    _prev_env = {k: os.environ.get(k) for k in _PROD_OVERRIDES}
    try:
        for _k, _v in _PROD_OVERRIDES.items():
            if _v is not None:
                os.environ[_k] = _v
        NEXTSEEK_CHAT_CONFIG_PROD = ChatConfig()
    finally:
        for _k, _v in _prev_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
"""


def _guard_repo(tmp_path: Path, *, env_text: str, settings_text: str | None) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "docker" / "nextseek.env").write_text(env_text)
    if settings_text is not None:
        (repo / "dmac").mkdir()
        (repo / "dmac" / "local_settings.py").write_text(settings_text)
    return repo


def test_prod_overlay_guard_flags_stale_overlay(tmp_path: Path) -> None:
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text=_STALE_OVERLAY,
    )
    r = check_prod_overlay_guard(repo)
    assert r.ok is False
    assert "regenerate" in r.detail


def test_prod_overlay_guard_ok_when_internal_var_absent(tmp_path: Path) -> None:
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_BASE_URL="http://$NEXTSEEK_HOSTNAME"\n',
        settings_text=_STALE_OVERLAY,
    )
    assert check_prod_overlay_guard(repo).ok is True


def test_prod_overlay_guard_ok_when_internal_var_commented_or_empty(
    tmp_path: Path,
) -> None:
    repo = _guard_repo(
        tmp_path,
        env_text=(
            '# NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n'
            'NEXTSEEK_INTERNAL_BASE_URL=""\n'
        ),
        settings_text=_STALE_OVERLAY,
    )
    assert check_prod_overlay_guard(repo).ok is True


def test_prod_overlay_guard_ok_with_current_template(tmp_path: Path) -> None:
    real_template = (
        Path(__file__).resolve().parents[2]
        / "startup"
        / "templates"
        / "local_settings.py.template"
    ).read_text()
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text=real_template,
    )
    assert check_prod_overlay_guard(repo).ok is True


def test_prod_overlay_guard_ok_without_overlay_block(tmp_path: Path) -> None:
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text="SEEK_URL = 'http://seek:3000'\n",
    )
    assert check_prod_overlay_guard(repo).ok is True


def test_prod_overlay_guard_ok_when_files_missing(tmp_path: Path) -> None:
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text=None,
    )
    assert check_prod_overlay_guard(repo).ok is True


def test_prod_overlay_guard_ignores_config_map_outside_the_overlay(
    tmp_path: Path,
) -> None:
    """Cold-review finding (2026-07-08): a DEV-side ChatConfig(config_map=...)
    ABOVE the overlay (or a mere comment mentioning config_map) must not mark
    a guardless PROD overlay as guarded."""
    stale_with_dev_config_map = (
        "import os\n"
        "from chat_nextseek.config import ChatConfig\n"
        '# tuning note: config_map lets you pin values\n'
        'NEXTSEEK_CHAT_CONFIG = ChatConfig(config_map={"MODEL_MODE": "gcp"})\n'
        + _STALE_OVERLAY.split("NEXTSEEK_CHAT_CONFIG = ChatConfig()\n", 1)[1]
    )
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text=stale_with_dev_config_map,
    )
    r = check_prod_overlay_guard(repo)
    assert r.ok is False


def test_prod_overlay_guard_accepts_single_quoted_pop(tmp_path: Path) -> None:
    """A hand-ported pop with single quotes is a real guard — no false alarm."""
    guarded_single_quote = _STALE_OVERLAY.replace(
        "        NEXTSEEK_CHAT_CONFIG_PROD = ChatConfig()",
        "        os.environ.pop('NEXTSEEK_INTERNAL_BASE_URL', None)\n"
        "        NEXTSEEK_CHAT_CONFIG_PROD = ChatConfig()",
    )
    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text=guarded_single_quote,
    )
    assert check_prod_overlay_guard(repo).ok is True


@patch("startup.steps.validate.compose_ps_running")
@patch("startup.steps.validate.run_django_check")
@patch("startup.steps.validate.check_http")
def test_prod_overlay_guard_wired_into_health_checks(
    mock_http: MagicMock,
    mock_django: MagicMock,
    mock_compose_ps: MagicMock,
    tmp_path: Path,
) -> None:
    mock_http.return_value = HealthResult(name="x", ok=True, detail="")
    mock_django.return_value = HealthResult(name="django check", ok=True, detail="")
    # Hermeticity (review follow-up): run_all_health_checks now also calls
    # check_cc_services -> compose_ps_running, which would otherwise shell
    # out to a real `docker compose ps`. Mock it the same way the Task-2
    # cc-services tests do (e.g. test_check_cc_services_ok_when_both_running).
    mock_compose_ps.return_value = []
    from startup.steps.validate import run_all_health_checks

    repo = _guard_repo(
        tmp_path,
        env_text='NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"\n',
        settings_text=_STALE_OVERLAY,
    )
    results = run_all_health_checks({}, repo, env={})
    guard = [r for r in results if r.name == "prod overlay guard"]
    assert len(guard) == 1
    assert guard[0].ok is False


# --- D1: loud empty-token warning + CC health checks in install ---


def test_check_proxy_token_empty_is_warn_not_fail(tmp_path):
    out = tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK=""\n')
    result = validate.check_proxy_token(tmp_path)
    assert result.ok is True
    assert result.warn is True
    assert "disabled" in result.detail


def test_check_proxy_token_present_is_ok(tmp_path):
    out = tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-x"\n')
    result = validate.check_proxy_token(tmp_path)
    assert result.ok is True and result.warn is False


def test_check_proxy_token_ignores_a_token_left_at_the_pre_move_path(tmp_path):
    """compose reads only the NessieAI/ path now, so a token at the old one is
    not a working token: warn, and say which mv fixes it, never what it holds."""
    legacy = tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-old-home"\n')

    result = validate.check_proxy_token(tmp_path)

    assert result.ok is True and result.warn is True
    assert "disabled" in result.detail
    assert "docker/bedrock-proxy/proxy-secret.env" in result.detail
    assert "NessieAI/docker/bedrock-proxy/" in result.detail
    assert "ABSK-old-home" not in result.detail


# --- C9: rendered docker/nextseek.env values that name pre-NessieAI paths ---

def _pre_move(rel: str) -> str:
    """A unit's pre-NessieAI in-image path. "/app" is joined on separately so
    the stale-path grep the move is verified with (over startup/) keeps
    returning nothing but real regressions."""
    return "/app" + "/" + rel


# The three lines the local box's rendered env carried on 2026-09-10 (the same
# values, rebuilt with _pre_move; fairdata-dev carries the same three keys).
_PRE_MOVE_ENV_LINES = {
    "CATALOG_FILE": (
        'CATALOG_FILE="' + _pre_move("chat_nextseek/agent_model_catalog.json") + '"'
    ),
    "DMAC_ROUTE_CAPABILITIES_FILE": (
        "DMAC_ROUTE_CAPABILITIES_FILE="
        + _pre_move("dmac_assistant/build_context/route_capabilities.json")
    ),
    "DMAC_ROUTER_MODEL_CLASS_MAP_FILE": (
        "DMAC_ROUTER_MODEL_CLASS_MAP_FILE="
        + _pre_move("dmac_assistant/build_context/router_model_class_map.json")
    ),
}


def _env_repo(tmp_path: Path, text: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "docker" / "nextseek.env").write_text(text)
    return repo


@pytest.mark.parametrize("key", sorted(_PRE_MOVE_ENV_LINES))
def test_stale_nessie_env_refuses_each_pre_move_line_on_its_own(tmp_path, key):
    repo = _env_repo(tmp_path, 'SEEK_HOST="seek"\n' + _PRE_MOVE_ENV_LINES[key] + "\n")

    result = validate.check_stale_nessie_env(repo)

    assert result.ok is False
    assert f"line 2 {key}:" in result.detail


def test_stale_nessie_env_lists_every_stale_line_with_its_fix(tmp_path):
    repo = _env_repo(tmp_path, "\n".join(_PRE_MOVE_ENV_LINES.values()) + "\n")

    result = validate.check_stale_nessie_env(repo)

    assert result.ok is False
    assert (
        'line 1 CATALOG_FILE: set it to "/app/NessieAI/chat_nextseek/agent_model_catalog.json"'
        in result.detail
    )
    # Deleted, not repointed: the package default is right, and a repointed
    # override would silently go stale again at the next move.
    assert "line 2 DMAC_ROUTE_CAPABILITIES_FILE: delete the line" in result.detail
    assert "line 3 DMAC_ROUTER_MODEL_CLASS_MAP_FILE: delete the line" in result.detail


def test_stale_nessie_env_repoints_any_other_key_by_its_unit(tmp_path):
    repo = _env_repo(
        tmp_path,
        "NESSIE_CORPUS=" + _pre_move("nessie_tests/corpus.json") + "\n"
        "EXTRA_PATH=/app:" + _pre_move("chat_nextseek") + "\n",
    )

    result = validate.check_stale_nessie_env(repo)

    assert result.ok is False
    assert "line 1 NESSIE_CORPUS: repoint it under /app/NessieAI/tests/nessie_tests/" in result.detail
    assert "line 2 EXTRA_PATH: repoint it under /app/NessieAI/chat_nextseek/" in result.detail


def test_stale_nessie_env_passes_current_paths_comments_and_near_misses(tmp_path):
    repo = _env_repo(
        tmp_path,
        'CATALOG_FILE="/app/NessieAI/chat_nextseek/agent_model_catalog.json"\n'
        "# " + _PRE_MOVE_ENV_LINES["CATALOG_FILE"] + "\n"
        "ARCHIVE_DIR=" + _pre_move("chat_nextseek_archive/x") + "\n"
        'LOG_DIR="/app/logs"\n',
    )

    result = validate.check_stale_nessie_env(repo)

    assert result.ok is True, result.detail


def test_stale_nessie_env_is_ok_when_nothing_is_rendered(tmp_path):
    result = validate.check_stale_nessie_env(tmp_path)
    assert result.ok is True
    assert "not rendered" in result.detail


def test_stale_nessie_env_names_keys_and_never_values(tmp_path):
    """The same file carries the Django secret and the LLM keys."""
    secret = "not-a-real-django-secret-7f3a"
    repo = _env_repo(
        tmp_path,
        f'DJANGO_SECRET_KEY="{secret}"\n' + _PRE_MOVE_ENV_LINES["CATALOG_FILE"] + "\n",
    )

    result = validate.check_stale_nessie_env(repo)

    assert result.ok is False
    assert secret not in result.detail
    # Only the stale line is reported, never its neighbour.
    assert "line 2 CATALOG_FILE:" in result.detail
    assert "line 1 " not in result.detail


def test_rendered_template_passes_the_stale_env_check(tmp_path):
    """install renders this file; the rebuild that follows must not refuse it."""
    import shutil

    from startup.steps import config

    real_root = Path(__file__).resolve().parents[2]
    repo = tmp_path / "repo"
    (repo / "startup" / "templates").mkdir(parents=True)
    shutil.copy(
        real_root / "startup" / "templates" / "nextseek.env.template",
        repo / "startup" / "templates" / "nextseek.env.template",
    )
    config.render_nextseek_env(repo, config.default_values(nextseek_port=8000, seek_port=3000))

    result = validate.check_stale_nessie_env(repo)

    assert result.ok is True, result.detail


@pytest.mark.parametrize("which", ["run_all_health_checks", "run_app_health_checks"])
def test_stale_nessie_env_is_reported_by_both_health_check_sets(monkeypatch, tmp_path, which):
    """doctor (either scope) and install's final checks both surface it."""
    repo = _env_repo(tmp_path, "\n".join(_PRE_MOVE_ENV_LINES.values()) + "\n")
    _stub_unrelated_health_checks(monkeypatch)
    monkeypatch.setattr(validate, "image_exists", lambda name: True)
    monkeypatch.setattr(
        validate,
        "check_seek_url_consistency",
        lambda repo_root, state, env: validate.HealthResult("SEEK public URL", True, "ok"),
    )

    results = getattr(validate, which)({"nextseek": 8000}, repo, env={})

    by_name = {r.name: r for r in results}
    assert by_name["nextseek.env paths"].ok is False


def test_run_all_health_checks_surfaces_cc_warnings_and_services(monkeypatch, tmp_path):
    calls = []
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        'AWS_BEARER_TOKEN_BEDROCK=""\n'
    )

    monkeypatch.setattr(validate, "check_http", lambda name, url: validate.HealthResult(name, True, url))
    monkeypatch.setattr(validate, "run_django_check", lambda repo, env: validate.HealthResult("django check", True, "ok"))
    monkeypatch.setattr(validate, "check_prod_overlay_guard", lambda repo: validate.HealthResult("prod overlay", True, "ok"))
    monkeypatch.setattr(validate, "compose_ps_running", lambda services, project_dir, env: calls.append(list(services)) or services)
    monkeypatch.setattr(validate, "image_exists", lambda name: True)
    monkeypatch.setattr(
        validate,
        "check_cc_runner",
        lambda repo, env: validate.HealthResult("CC runner", True, "(True, 'ok')"),
    )

    results = validate.run_all_health_checks({}, tmp_path, env={})
    by_name = {r.name: r for r in results}
    assert by_name["bedrock proxy token"].warn is True
    assert by_name["cc services"].ok is True
    assert calls == [["bedrock-proxy", "nextseek-sidecar"]]


def test_app_health_checks_exclude_seek_and_neo4j(monkeypatch, tmp_path):
    calls = []
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        'AWS_BEARER_TOKEN_BEDROCK=""\n'
    )
    monkeypatch.setattr(
        validate,
        "check_http",
        lambda name, url: calls.append((name, url))
        or validate.HealthResult(name, True, url),
    )
    monkeypatch.setattr(
        validate,
        "run_django_check",
        lambda repo, env: validate.HealthResult("django check", True, "ok"),
    )
    monkeypatch.setattr(
        validate,
        "check_prod_overlay_guard",
        lambda repo: validate.HealthResult("prod overlay guard", True, "ok"),
    )
    monkeypatch.setattr(
        validate,
        "compose_ps_running",
        lambda services, project_dir, env: list(services),
    )
    monkeypatch.setattr(validate, "image_exists", lambda name: True)
    monkeypatch.setattr(
        validate,
        "check_cc_runner",
        lambda repo, env: validate.HealthResult("CC runner", True, "(True, 'ok')"),
    )

    results = validate.run_app_health_checks(
        {"nextseek": 18000, "seek": 13000, "neo4j_http": 17474},
        tmp_path,
        env={},
    )

    assert calls == [("NExtSEEK", "http://localhost:18000")]
    assert {result.name for result in results} == {
        "NExtSEEK", "django check", "prod overlay guard", "nextseek.env paths",
        "bedrock proxy token", "cc services",
        "first-party images", "CC runner",
    }


@pytest.mark.parametrize(
    ("in_seek", "configured", "rendered", "ok", "message"),
    [
        (None, "https://seek.example", "https://seek.example", False, "not set"),
        (
            "https://seek-db.example", "https://seek.example",
            "https://seek.example", False, "drift",
        ),
        (
            "https://seek.example", "https://seek.example",
            "https://seek.example", True, "agree",
        ),
    ],
)
def test_seek_url_consistency_covers_absent_drift_and_agreement(
    monkeypatch, tmp_path, in_seek, configured, rendered, ok, message,
):
    monkeypatch.setattr(validate, "read_rendered_seek_public_url", lambda root: rendered)
    monkeypatch.setattr(
        validate.seek_settings,
        "read_site_base_host",
        lambda root, env: in_seek,
    )

    result = validate.check_seek_url_consistency(
        tmp_path,
        SimpleNamespace(seek_public_url=configured),
        {},
    )

    assert result.ok is ok
    assert message in result.detail


def test_check_cc_services_ok_when_both_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        validate, "compose_ps_running", lambda services, project_dir, env: list(services)
    )
    result = validate.check_cc_services(tmp_path, env={})
    assert result.ok is True
    assert result.warn is False
    assert "bedrock-proxy" in result.detail and "nextseek-sidecar" in result.detail


def test_check_cc_services_fails_when_one_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        validate, "compose_ps_running", lambda services, project_dir, env: ["bedrock-proxy"]
    )
    result = validate.check_cc_services(tmp_path, env={})
    assert result.ok is False
    assert "nextseek-sidecar" in result.detail


def test_check_cc_services_fails_on_docker_ops_error(monkeypatch, tmp_path):
    def _boom(services, project_dir, env):
        raise validate.DockerOpsError("compose ps failed")

    monkeypatch.setattr(validate, "compose_ps_running", _boom)
    result = validate.check_cc_services(tmp_path, env={})
    assert result.ok is False
    assert "compose ps failed" in result.detail


# --- D1: CLI-level warning transcript (install summary print loop + the
# pre-summary empty-token warning). Exercises the real startup.cli helpers
# rather than duplicating their logic, without invoking the full install()
# pipeline (which needs docker/network access covered by Task 16's live
# proxy/sidecar readiness probe on an isolated instance).


def test_cli_warns_on_render_when_proxy_token_empty(monkeypatch, tmp_path):
    from startup import cli

    warnings = []
    monkeypatch.setattr(cli.ui, "warn", lambda msg: warnings.append(msg))

    proxy_env_path = tmp_path / "proxy-secret.env"
    proxy_env_path.write_text('AWS_BEARER_TOKEN_BEDROCK=""\n')
    cli._warn_if_proxy_token_empty(proxy_env_path)

    assert len(warnings) == 1
    assert "CC model calls disabled" in warnings[0]


def test_cli_no_warning_on_render_when_proxy_token_present(monkeypatch, tmp_path):
    from startup import cli

    warnings = []
    monkeypatch.setattr(cli.ui, "warn", lambda msg: warnings.append(msg))

    proxy_env_path = tmp_path / "proxy-secret.env"
    proxy_env_path.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-x"\n')
    cli._warn_if_proxy_token_empty(proxy_env_path)

    assert warnings == []


def test_cli_health_summary_warns_on_empty_token_and_lists_cc_services(monkeypatch, tmp_path):
    from startup import cli

    warn_lines = []
    ok_lines = []
    fail_lines = []
    monkeypatch.setattr(cli.ui, "warn", lambda msg: warn_lines.append(msg))
    monkeypatch.setattr(cli.ui, "ok", lambda msg: ok_lines.append(msg))
    monkeypatch.setattr(cli.ui, "fail", lambda msg: fail_lines.append(msg))

    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        'AWS_BEARER_TOKEN_BEDROCK=""\n'
    )
    monkeypatch.setattr(
        validate, "compose_ps_running", lambda services, project_dir, env: list(services)
    )

    results = [
        validate.check_proxy_token(tmp_path),
        validate.check_cc_services(tmp_path, env={}),
    ]
    cli._print_health_results(results)

    # empty token prints an explicit WARN line naming CC model calls disabled
    assert len(warn_lines) == 1
    assert "CC model calls are disabled" in warn_lines[0]
    # cc services appears in the health summary (printed via ui.ok, not a warning)
    assert any("cc services" in line for line in ok_lines)
    assert not any("cc services" in line for line in warn_lines)
    assert fail_lines == []


def test_cli_health_summary_no_token_warning_when_token_present(monkeypatch, tmp_path):
    from startup import cli

    warn_lines = []
    ok_lines = []
    monkeypatch.setattr(cli.ui, "warn", lambda msg: warn_lines.append(msg))
    monkeypatch.setattr(cli.ui, "ok", lambda msg: ok_lines.append(msg))
    monkeypatch.setattr(cli.ui, "fail", lambda msg: (_ for _ in ()).throw(AssertionError(msg)))

    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        'AWS_BEARER_TOKEN_BEDROCK="ABSK-x"\n'
    )
    monkeypatch.setattr(
        validate, "compose_ps_running", lambda services, project_dir, env: list(services)
    )

    results = [
        validate.check_proxy_token(tmp_path),
        validate.check_cc_services(tmp_path, env={}),
    ]
    cli._print_health_results(results)

    assert warn_lines == []
    assert any("bedrock proxy token" in line for line in ok_lines)
    assert any("cc services" in line for line in ok_lines)


# ---------------------------------------------------------------------------
# first-party image health (the gap that let Container-CC die under a green deploy)
# ---------------------------------------------------------------------------

def _proxy_secret(tmp_path: Path, token: str = "") -> None:
    out = tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f'AWS_BEARER_TOKEN_BEDROCK="{token}"\n')


def test_check_first_party_images_probes_every_canonical_image(monkeypatch):
    probed = []
    monkeypatch.setattr(
        validate, "image_exists", lambda name: probed.append(name) or True
    )

    result = validate.check_first_party_images("nextseek")

    assert result.ok is True
    assert sorted(probed) == [
        "dmac-assistant:poc",
        "nextseek-bedrock-proxy:latest",
        "nextseek-nextseek:latest",
        "nextseek-ns-sidecar:latest",
    ]


def test_check_first_party_images_honours_the_compose_project_name(monkeypatch):
    """A namespaced instance builds its app image under its own project name."""
    probed = []
    monkeypatch.setattr(
        validate, "image_exists", lambda name: probed.append(name) or True
    )

    validate.check_first_party_images("nextseek-v2")

    assert "nextseek-v2-nextseek:latest" in probed


def test_check_first_party_images_names_the_absent_one_and_how_to_build_it(monkeypatch):
    monkeypatch.setattr(
        validate, "image_exists", lambda name: name != "dmac-assistant:poc"
    )

    result = validate.check_first_party_images("nextseek")

    assert result.ok is False
    assert "dmac-assistant:poc" in result.detail
    assert "--component cc-agent" in result.detail
    # The three that are present must not be reported as problems.
    assert "nextseek-ns-sidecar:latest" not in result.detail


def test_check_first_party_images_reports_a_daemon_outage_rather_than_four_absences(
    monkeypatch,
):
    def explode(name):
        raise validate.DockerOpsError("docker image ls failed (exit 1): daemon unreachable")

    monkeypatch.setattr(validate, "image_exists", explode)

    result = validate.check_first_party_images("nextseek")

    assert result.ok is False
    assert "daemon unreachable" in result.detail
    assert "--component" not in result.detail


def test_check_app_runtimes_names_a_stopped_front_door_and_how_to_start_it(monkeypatch):
    """2026-09-10: nginx was stopped, the app was healthy, and the smoke suite
    spent five minutes finding out. This is the line that says so at once."""
    monkeypatch.setattr(
        validate, "compose_ps_running",
        lambda services, project_dir, env: ["nextseek"],
    )

    result = validate.check_app_runtimes(Path("/repo"), {})

    assert result.ok is False
    assert "nextseek_nginx" in result.detail
    assert "docker compose up -d --no-deps nextseek_nginx" in result.detail
    # The one that is running must not be reported as a problem.
    assert "not running: nextseek," not in result.detail


def test_check_app_runtimes_asks_for_the_app_and_the_front_door(monkeypatch):
    asked = []
    monkeypatch.setattr(
        validate, "compose_ps_running",
        lambda services, project_dir, env: asked.extend(services) or list(services),
    )

    result = validate.check_app_runtimes(Path("/repo"), {})

    assert result.ok is True
    assert sorted(asked) == ["nextseek", "nextseek_nginx"]


def test_check_app_runtimes_reports_a_daemon_outage(monkeypatch):
    def explode(services, project_dir, env):
        raise validate.DockerOpsError("docker compose ps failed (exit 1): daemon unreachable")

    monkeypatch.setattr(validate, "compose_ps_running", explode)

    result = validate.check_app_runtimes(Path("/repo"), {})

    assert result.ok is False
    assert "daemon unreachable" in result.detail


def _stub_checks(monkeypatch, *, runtimes=True, images=True, cc=True, context=True):
    """Answer every stack-health check without docker; return the context check's calls."""
    monkeypatch.setattr(validate, "check_app_runtimes", lambda repo_root, env:
                        validate.HealthResult("app + front door", runtimes, "d"))
    monkeypatch.setattr(validate, "check_first_party_images", lambda name:
                        validate.HealthResult("first-party images", images, "d"))
    monkeypatch.setattr(validate, "check_cc_services", lambda repo_root, env:
                        validate.HealthResult("cc services", cc, "d"))
    asked: list[tuple] = []
    monkeypatch.setattr(validate, "check_cc_agent_context",
                        lambda checkout, compose_project_name:
                        asked.append((checkout, compose_project_name))
                        or validate.HealthResult("cc-agent context", context, "d"))
    return asked


def test_stack_health_lets_ci_run_when_only_an_advisory_check_fails(monkeypatch):
    """A missing CC image or a stopped sidecar changes nothing the smoke suite
    requests, so the run still says something true about the deploy."""
    _stub_checks(monkeypatch, images=False, cc=False)

    health = validate.stack_health(Path("/repo"), {}, "nextseek")

    assert health.testable is True
    assert health.ok is False


def test_stack_health_blocks_ci_when_the_app_or_front_door_is_down(monkeypatch):
    """Every smoke test enters through nginx to the app. With either down, each
    one fails the same way, and the run says nothing about the deploy."""
    _stub_checks(monkeypatch, runtimes=False)

    health = validate.stack_health(Path("/repo"), {}, "nextseek")

    assert health.testable is False
    assert health.ok is False


def test_stack_health_reports_every_check_blocking_first(monkeypatch):
    _stub_checks(monkeypatch)

    health = validate.stack_health(Path("/repo"), {}, "nextseek")

    assert [r.name for r in health.results] == [
        "app + front door", "first-party images", "cc services", "cc-agent context",
    ]
    assert health.ok is True and health.testable is True


def test_stack_health_lets_ci_run_when_the_cc_agent_context_is_stale(monkeypatch):
    """The smoke suite never asks the CC agent anything, so a stale baked copy
    changes nothing it tests; it is the rebuild's exit code that must say so."""
    _stub_checks(monkeypatch, context=False)

    health = validate.stack_health(Path("/repo"), {}, "nextseek")

    assert health.testable is True
    assert health.ok is False


def test_stack_health_compares_the_cc_agent_context_with_the_tree_it_is_given(monkeypatch):
    """A --source-tree rebuild bakes a clean tree, not the runtime checkout, so
    the comparison is against the tree the images were built from."""
    asked = _stub_checks(monkeypatch)

    validate.stack_health(Path("/repo"), {}, "nextseek-v2", checkout=Path("/clean"))
    validate.stack_health(Path("/repo"), {}, "nextseek")

    assert asked == [(Path("/clean"), "nextseek-v2"), (Path("/repo"), "nextseek")]


def test_check_cc_runner_runs_deployment_step_6_in_the_app_container(monkeypatch):
    """Host-side image presence does not prove the APP CONTAINER's docker socket
    can see the image, which is the thing that actually failed. This runs the
    command DEPLOYMENT.md asks the operator to run by hand, in the same place."""
    seen = {}

    def fake_exec(service, command, project_dir, env):
        seen["service"] = service
        seen["command"] = command
        return "(True, 'ok')\n"

    monkeypatch.setattr(validate, "compose_exec", fake_exec)

    result = validate.check_cc_runner(Path("/repo"), env={})

    assert result.ok is True
    assert seen["service"] == "nextseek"
    joined = " ".join(seen["command"])
    assert "cc_runner_available" in joined
    # The app image has no bare `python` on PATH (DEPLOYMENT.md 6.6).
    assert "--no-sync" in seen["command"]


def test_check_cc_runner_imports_the_engine_from_its_nessieai_home(monkeypatch):
    """cc_engine moved to NessieAI/cc/; the old import path no longer exists."""
    seen = {}

    def fake_exec(service, command, project_dir, env):
        seen["command"] = command
        return "(True, 'ok')\n"

    monkeypatch.setattr(validate, "compose_exec", fake_exec)

    validate.check_cc_runner(Path("/repo"), env={})

    code = seen["command"][seen["command"].index("-c") + 1]
    assert code.startswith("from NessieAI.cc import cc_engine;")
    assert "nextseek_api.cc_assistant" not in code


def test_check_cc_runner_fails_and_carries_the_engine_s_own_reason(monkeypatch):
    monkeypatch.setattr(
        validate,
        "compose_exec",
        lambda **kwargs: "(False, \"CC image 'dmac-assistant:poc' not found\")\n",
    )

    result = validate.check_cc_runner(Path("/repo"), env={})

    assert result.ok is False
    assert "dmac-assistant:poc" in result.detail


def test_check_cc_runner_fails_when_the_container_cannot_be_reached(monkeypatch):
    def explode(**kwargs):
        raise validate.DockerOpsError("docker compose exec nextseek ... failed (exit 1)")

    monkeypatch.setattr(validate, "compose_exec", explode)

    result = validate.check_cc_runner(Path("/repo"), env={})

    assert result.ok is False
    assert "exit 1" in result.detail


def _stub_unrelated_health_checks(monkeypatch):
    monkeypatch.setattr(
        validate, "check_http", lambda name, url: validate.HealthResult(name, True, url)
    )
    monkeypatch.setattr(
        validate, "run_django_check",
        lambda repo, env: validate.HealthResult("django check", True, "ok"),
    )
    monkeypatch.setattr(
        validate, "check_prod_overlay_guard",
        lambda repo: validate.HealthResult("prod overlay guard", True, "ok"),
    )
    monkeypatch.setattr(
        validate, "compose_ps_running", lambda services, project_dir, env: list(services)
    )
    monkeypatch.setattr(
        validate, "check_cc_runner",
        lambda repo, env: validate.HealthResult("CC runner", True, "(True, 'ok')"),
    )


def test_run_all_health_checks_reports_image_health(monkeypatch, tmp_path):
    _proxy_secret(tmp_path)
    _stub_unrelated_health_checks(monkeypatch)
    monkeypatch.setattr(validate, "image_exists", lambda name: False)

    results = validate.run_all_health_checks(
        {}, tmp_path, env={"COMPOSE_PROJECT_NAME": "nextseek"}
    )

    by_name = {r.name: r for r in results}
    assert by_name["first-party images"].ok is False
    assert by_name["CC runner"].ok is True


def test_app_health_checks_report_image_health_too(monkeypatch, tmp_path):
    """An app-only deploy is precisely the cohort that never rebuilds cc-agent."""
    _proxy_secret(tmp_path)
    _stub_unrelated_health_checks(monkeypatch)
    monkeypatch.setattr(validate, "image_exists", lambda name: False)

    results = validate.run_app_health_checks(
        {"nextseek": 8000}, tmp_path, env={"COMPOSE_PROJECT_NAME": "nextseek"}
    )

    by_name = {r.name: r for r in results}
    assert by_name["first-party images"].ok is False
    assert by_name["CC runner"].ok is True


# ---------------------------------------------------------------------------
# the Nessie lane's prerequisites: advisory in stack health, failures here
# ---------------------------------------------------------------------------

def test_nessie_prerequisites_fail_on_an_empty_proxy_token(tmp_path, monkeypatch):
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        "AWS_BEARER_TOKEN_BEDROCK=\nAWS_REGION=us-east-1\n")
    ok = validate.HealthResult(name="x", ok=True, detail="fine")
    monkeypatch.setattr(validate, "check_first_party_images", lambda *a, **k: ok)
    monkeypatch.setattr(validate, "check_cc_services", lambda *a, **k: ok)
    monkeypatch.setattr(validate, "check_cc_runner", lambda *a, **k: ok)
    results = validate.nessie_prerequisites(tmp_path, {}, "nextseek")
    token = results[0]
    assert token.name == "bedrock proxy token" and token.ok is False


def test_nessie_prerequisites_never_print_the_token(tmp_path, monkeypatch):
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        "AWS_BEARER_TOKEN_BEDROCK=sekrit-value\n")
    ok = validate.HealthResult(name="x", ok=True, detail="fine")
    monkeypatch.setattr(validate, "check_first_party_images", lambda *a, **k: ok)
    monkeypatch.setattr(validate, "check_cc_services", lambda *a, **k: ok)
    monkeypatch.setattr(validate, "check_cc_runner", lambda *a, **k: ok)
    results = validate.nessie_prerequisites(tmp_path, {}, "nextseek")
    assert all(r.ok for r in results)
    assert all("sekrit" not in r.detail for r in results)


def test_nessie_prerequisites_fail_on_a_missing_token_file(tmp_path, monkeypatch):
    """No file at all is the same missing credential as an empty value."""
    ok = validate.HealthResult(name="x", ok=True, detail="fine")
    monkeypatch.setattr(validate, "check_first_party_images", lambda *a, **k: ok)
    monkeypatch.setattr(validate, "check_cc_services", lambda *a, **k: ok)
    monkeypatch.setattr(validate, "check_cc_runner", lambda *a, **k: ok)
    token = validate.nessie_prerequisites(tmp_path, {}, "nextseek")[0]
    assert token.ok is False and token.warn is False


def test_nessie_prerequisites_carry_the_image_service_and_runner_checks(tmp_path, monkeypatch):
    """The cc-agent image, the two CC services and the in-container runner check,
    asked with this box's own project name and compose environment."""
    _proxy_secret(tmp_path, token="present")
    asked = {}

    def images(name):
        asked["project"] = name
        return validate.HealthResult("first-party images", False, "ABSENT: dmac-assistant:poc")

    def services(repo_root, env):
        asked["services_env"] = env
        return validate.HealthResult("cc services", False, "not running: nextseek-sidecar")

    def cc_runner(repo_root, env):
        asked["runner_env"] = env
        return validate.HealthResult("CC runner", True, "(True, 'ok')")

    monkeypatch.setattr(validate, "check_first_party_images", images)
    monkeypatch.setattr(validate, "check_cc_services", services)
    monkeypatch.setattr(validate, "check_cc_runner", cc_runner)
    env = {"COMPOSE_PROJECT_NAME": "nextseek-v2"}

    results = validate.nessie_prerequisites(tmp_path, env, "nextseek-v2")

    assert [r.name for r in results] == [
        "bedrock proxy token", "first-party images", "cc services", "CC runner",
    ]
    assert [r.ok for r in results] == [True, False, False, True]
    assert asked == {"project": "nextseek-v2", "services_env": env, "runner_env": env}
