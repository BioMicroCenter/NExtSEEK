"""Tests for startup.steps.validate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


@patch("startup.steps.validate.run_django_check")
@patch("startup.steps.validate.check_http")
def test_prod_overlay_guard_wired_into_health_checks(
    mock_http: MagicMock, mock_django: MagicMock, tmp_path: Path
) -> None:
    mock_http.return_value = HealthResult(name="x", ok=True, detail="")
    mock_django.return_value = HealthResult(name="django check", ok=True, detail="")
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
