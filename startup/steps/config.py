"""Render startup-managed env files and Django settings."""
from __future__ import annotations

import os
import secrets
import string
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template

from startup.lib.env import read_env, write_env


@dataclass
class ConfigValues:
    mysql_root_password: str
    mysql_password: str
    neo4j_password: str
    django_secret_key: str
    django_csrf_trusted_origins: str
    nextseek_port: int
    seek_port: int


def _generate_secret_key(length: int = 64) -> str:
    # Excludes `$`, `\\`, `"`, `'`, `` ` ``, and `#` so the rendered DJANGO_SECRET_KEY in
    # docker/nextseek.env is never re-interpolated or quote-corrupted when docker
    # compose loads the env file. 64 chars across 76 symbols ≈ 400 bits of entropy.
    alphabet = string.ascii_letters + string.digits + "!@%^&*()-_=+:.<>?"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def csrf_origins_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port} http://localhost:{port}"


def default_values(nextseek_port: int, seek_port: int) -> ConfigValues:
    return ConfigValues(
        mysql_root_password="seek_root",
        mysql_password="seek_db_password",
        neo4j_password="demopassword",
        django_secret_key=_generate_secret_key(),
        django_csrf_trusted_origins=csrf_origins_for_port(nextseek_port),
        nextseek_port=nextseek_port,
        seek_port=seek_port,
    )


def _render(template_path: Path, values: ConfigValues) -> str:
    text = template_path.read_text()
    substitutions = {
        "MYSQL_ROOT_PASSWORD": values.mysql_root_password,
        "MYSQL_PASSWORD": values.mysql_password,
        "NEO4J_PASSWORD": values.neo4j_password,
        "DJANGO_SECRET_KEY": values.django_secret_key,
        "DJANGO_CSRF_TRUSTED_ORIGINS": values.django_csrf_trusted_origins,
        "NEXTSEEK_PORT": str(values.nextseek_port),
        "SEEK_PORT": str(values.seek_port),
    }
    return Template(text).safe_substitute(substitutions)


def render_db_env(repo_root: Path, values: ConfigValues) -> Path:
    template = repo_root / "startup" / "templates" / "db.env.template"
    output = repo_root / "docker" / "db.env"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(template, values))
    return output


def render_nextseek_env(repo_root: Path, values: ConfigValues) -> Path:
    template = repo_root / "startup" / "templates" / "nextseek.env.template"
    output = repo_root / "docker" / "nextseek.env"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(template, values))
    return output


def render_local_settings(repo_root: Path, values: ConfigValues) -> Path:
    template = repo_root / "startup" / "templates" / "local_settings.py.template"
    output = repo_root / "dmac" / "local_settings.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(template, values))
    return output


def render_proxy_secret_env(
    repo_root: Path,
    source_env: Mapping[str, str] | None = None,
) -> Path:
    """Render the bedrock proxy env_file required by docker compose.

    The institutional Bedrock token is runtime-only. If the operator exported it
    before running startup, copy it into the proxy env_file; otherwise preserve
    whatever token is already on disk (a hand-filled value must never be
    clobbered back to empty by a re-run of install); otherwise write an empty
    token so compose can still create the service and paid CC calls remain
    disabled until the file is filled in. The file is written mode 0600 since
    it holds an institutional Bedrock token.
    """
    source = source_env if source_env is not None else os.environ
    output = repo_root / "docker" / "bedrock-proxy" / "proxy-secret.env"
    existing = read_env(output)
    # Precedence: operator env > existing hand-filled file > empty.
    # Re-running install must never clobber a filled token back to empty (D2).
    token = source.get("AWS_BEARER_TOKEN_BEDROCK", "") or existing.get(
        "AWS_BEARER_TOKEN_BEDROCK", ""
    )
    region = (
        source.get("AWS_REGION")
        or source.get("AWS_DEFAULT_REGION")
        or existing.get("AWS_REGION")
        or "us-east-1"
    )
    write_env(output, {"AWS_BEARER_TOKEN_BEDROCK": token, "AWS_REGION": region})
    output.chmod(0o600)  # institutional Bedrock token (D3)
    return output


def render_root_env(repo_root: Path, compose_env: Mapping[str, str]) -> Path:
    """Persist non-secret compose targeting vars for manual docker compose use."""
    ordered_keys = [
        "COMPOSE_PROJECT_NAME",
        "NEXTSEEK_PORT",
        "SEEK_PORT",
        "NEO4J_HTTP_PORT",
        "NEO4J_BOLT_PORT",
        "INSTANCE_PREFIX",
    ]
    output = repo_root / ".env"
    write_env(output, {key: compose_env[key] for key in ordered_keys if key in compose_env})
    return output
