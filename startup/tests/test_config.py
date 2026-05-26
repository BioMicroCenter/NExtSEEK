"""Tests for startup.steps.config."""
from __future__ import annotations

from pathlib import Path

import pytest

from startup.steps.config import (
    ConfigValues,
    default_values,
    render_db_env,
    render_nextseek_env,
    render_local_settings,
    csrf_origins_for_port,
)


def test_default_values_has_demo_creds() -> None:
    v = default_values(nextseek_port=8000)
    assert v.mysql_root_password == "seek_root"
    assert v.mysql_password == "seek_db_password"
    assert v.neo4j_password == "demopassword"
    assert v.django_secret_key  # auto-generated, non-empty


def test_default_values_csrf_origins_match_port() -> None:
    v = default_values(nextseek_port=8042)
    assert "127.0.0.1:8042" in v.django_csrf_trusted_origins
    assert "localhost:8042" in v.django_csrf_trusted_origins


def test_csrf_origins_for_port_returns_both_hosts() -> None:
    result = csrf_origins_for_port(8001)
    assert "http://127.0.0.1:8001" in result
    assert "http://localhost:8001" in result


def test_render_db_env_substitutes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "startup" / "templates").mkdir(parents=True)
    template_path = repo / "startup" / "templates" / "db.env.template"
    template_path.write_text('MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}"\nMYSQL_PASSWORD="${MYSQL_PASSWORD}"\n')

    v = ConfigValues(
        mysql_root_password="root_pw",
        mysql_password="user_pw",
        neo4j_password="np",
        django_secret_key="dsk",
        django_csrf_trusted_origins="origins",
        nextseek_port=8000,
    )
    render_db_env(repo, v)
    rendered = (repo / "docker" / "db.env").read_text()
    assert 'MYSQL_ROOT_PASSWORD="root_pw"' in rendered
    assert 'MYSQL_PASSWORD="user_pw"' in rendered


def test_render_nextseek_env_substitutes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "startup" / "templates").mkdir(parents=True)
    template_path = repo / "startup" / "templates" / "nextseek.env.template"
    template_path.write_text(
        'NEXTSEEK_HOSTNAME="127.0.0.1:${NEXTSEEK_PORT}"\n'
        'NEXTSEEK_NEO4J_PASSWORD="${NEO4J_PASSWORD}"\n'
        'DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY}"\n'
        'DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS}"\n'
    )
    v = ConfigValues(
        mysql_root_password="r", mysql_password="p", neo4j_password="np",
        django_secret_key="dsk", django_csrf_trusted_origins="csrforigins",
        nextseek_port=8042,
    )
    render_nextseek_env(repo, v)
    rendered = (repo / "docker" / "nextseek.env").read_text()
    assert 'NEXTSEEK_HOSTNAME="127.0.0.1:8042"' in rendered
    assert 'NEO4J_PASSWORD="np"' in rendered
    assert 'DJANGO_SECRET_KEY="dsk"' in rendered
    assert 'DJANGO_CSRF_TRUSTED_ORIGINS="csrforigins"' in rendered


def test_render_local_settings_writes_to_dmac(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "dmac").mkdir()
    (repo / "startup" / "templates").mkdir(parents=True)
    (repo / "startup" / "templates" / "local_settings.py.template").write_text("# template content\n")
    v = ConfigValues(
        mysql_root_password="r", mysql_password="p", neo4j_password="np",
        django_secret_key="dsk", django_csrf_trusted_origins="o", nextseek_port=8000,
    )
    render_local_settings(repo, v)
    assert (repo / "dmac" / "local_settings.py").exists()
    assert "# template content" in (repo / "dmac" / "local_settings.py").read_text()
