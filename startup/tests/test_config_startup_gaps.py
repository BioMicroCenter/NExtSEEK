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
