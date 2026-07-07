"""Static tests for startup CLI wiring of generated deploy files."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_install_renders_cc_and_compose_env_files() -> None:
    text = (REPO_ROOT / "startup" / "cli.py").read_text()
    assert "config.render_proxy_secret_env(REPO_ROOT)" in text
    assert "config.render_root_env(REPO_ROOT, compose_env)" in text
    assert "build.start_cc_stack(REPO_ROOT, compose_env)" in text


def test_reset_removes_generated_cc_and_compose_env_files() -> None:
    text = (REPO_ROOT / "startup" / "cli.py").read_text()
    assert 'REPO_ROOT / "docker" / "bedrock-proxy" / "proxy-secret.env"' in text
    assert 'REPO_ROOT / ".env"' in text
