"""Tests for startup.steps.validate."""
from __future__ import annotations

from pathlib import Path
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
    out = tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK=""\n')
    result = validate.check_proxy_token(tmp_path)
    assert result.ok is True
    assert result.warn is True
    assert "disabled" in result.detail


def test_check_proxy_token_present_is_ok(tmp_path):
    out = tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-x"\n')
    result = validate.check_proxy_token(tmp_path)
    assert result.ok is True and result.warn is False


def test_run_all_health_checks_surfaces_cc_warnings_and_services(monkeypatch, tmp_path):
    calls = []
    (tmp_path / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
        'AWS_BEARER_TOKEN_BEDROCK=""\n'
    )

    monkeypatch.setattr(validate, "check_http", lambda name, url: validate.HealthResult(name, True, url))
    monkeypatch.setattr(validate, "run_django_check", lambda repo, env: validate.HealthResult("django check", True, "ok"))
    monkeypatch.setattr(validate, "check_prod_overlay_guard", lambda repo: validate.HealthResult("prod overlay", True, "ok"))
    monkeypatch.setattr(validate, "compose_ps_running", lambda services, project_dir, env: calls.append(list(services)) or services)

    results = validate.run_all_health_checks({}, tmp_path, env={})
    by_name = {r.name: r for r in results}
    assert by_name["bedrock proxy token"].warn is True
    assert by_name["cc services"].ok is True
    assert calls == [["bedrock-proxy", "nextseek-sidecar"]]


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

    (tmp_path / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
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

    (tmp_path / "docker" / "bedrock-proxy").mkdir(parents=True)
    (tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env").write_text(
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
