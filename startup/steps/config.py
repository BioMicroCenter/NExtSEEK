"""Render startup templates to docker/db.env, docker/nextseek.env, dmac/local_settings.py."""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from string import Template


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
