"""Regression tests for startup-generated deploy gap files."""
from __future__ import annotations

from pathlib import Path

from startup.lib.env import read_env
from startup.steps.config import render_proxy_secret_env, render_root_env


def test_render_proxy_secret_env_uses_parent_env_when_present(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    render_proxy_secret_env(
        repo,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "ABSKexample",
            "AWS_REGION": "us-west-2",
            "AWS_DEFAULT_REGION": "us-east-2",
        },
    )

    rendered = read_env(repo / "docker" / "bedrock-proxy" / "proxy-secret.env")
    assert rendered == {
        "AWS_BEARER_TOKEN_BEDROCK": "ABSKexample",
        "AWS_REGION": "us-west-2",
    }


def test_render_proxy_secret_env_defaults_without_token(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    render_proxy_secret_env(repo, {})

    rendered = read_env(repo / "docker" / "bedrock-proxy" / "proxy-secret.env")
    assert rendered == {
        "AWS_BEARER_TOKEN_BEDROCK": "",
        "AWS_REGION": "us-east-1",
    }


def test_render_proxy_secret_env_accepts_default_region(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    render_proxy_secret_env(repo, {"AWS_DEFAULT_REGION": "us-east-2"})

    rendered = read_env(repo / "docker" / "bedrock-proxy" / "proxy-secret.env")
    assert rendered["AWS_REGION"] == "us-east-2"


def test_render_proxy_secret_env_preserves_hand_filled_token(tmp_path: Path) -> None:
    out = tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-hand-filled"\nAWS_REGION="us-east-2"\n')
    render_proxy_secret_env(tmp_path, source_env={})
    kept = read_env(out)
    assert kept["AWS_BEARER_TOKEN_BEDROCK"] == "ABSK-hand-filled"
    assert kept["AWS_REGION"] == "us-east-2"


def test_render_proxy_secret_env_operator_env_beats_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-old"\n')
    render_proxy_secret_env(
        tmp_path, source_env={"AWS_BEARER_TOKEN_BEDROCK": "ABSK-new"}
    )
    assert read_env(out)["AWS_BEARER_TOKEN_BEDROCK"] == "ABSK-new"


def test_render_proxy_secret_env_writes_mode_600(tmp_path: Path) -> None:
    path = render_proxy_secret_env(tmp_path, source_env={})
    assert (path.stat().st_mode & 0o777) == 0o600


def test_render_proxy_secret_env_repairs_existing_file_mode(tmp_path: Path) -> None:
    out = tmp_path / "docker" / "bedrock-proxy" / "proxy-secret.env"
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-hand-filled"\n')
    out.chmod(0o644)
    render_proxy_secret_env(tmp_path, source_env={})
    assert read_env(out)["AWS_BEARER_TOKEN_BEDROCK"] == "ABSK-hand-filled"
    assert (out.stat().st_mode & 0o777) == 0o600


def test_render_root_env_writes_compose_targeting_vars_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    render_root_env(
        repo,
        {
            "INSTANCE_PREFIX": "test-",
            "COMPOSE_PROJECT_NAME": "nextseek-test",
            "NEXTSEEK_PORT": "8001",
            "SEEK_PORT": "3001",
            "NEO4J_HTTP_PORT": "7475",
            "NEO4J_BOLT_PORT": "7688",
            "AWS_BEARER_TOKEN_BEDROCK": "must-not-copy",
        },
    )

    rendered = read_env(repo / ".env")
    assert rendered == {
        "COMPOSE_PROJECT_NAME": "nextseek-test",
        "NEXTSEEK_PORT": "8001",
        "SEEK_PORT": "3001",
        "NEO4J_HTTP_PORT": "7475",
        "NEO4J_BOLT_PORT": "7688",
        "INSTANCE_PREFIX": "test-",
    }
