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


# --- Bug B (2026-07-07): chat_nextseek's REST self-calls run INSIDE the
# nextseek container, which listens on :8000 regardless of the published host
# port (gunicorn.conf.py, entrypoint.sh daphne branch). NEXTSEEK_BASE_URL keeps
# its public meaning (derives from NEXTSEEK_HOSTNAME = host-published port);
# the NEW NEXTSEEK_INTERNAL_BASE_URL carries the container-internal transport
# URL, decoupled from NEXTSEEK_PORT. Deriving the self-call URL from the host
# port broke every REST self-call on a port-bumped install (Step 7d greenfield).

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_real_template(tmp_path: Path, port: int) -> str:
    """Render the REAL nextseek.env.template into a tmp repo skeleton."""
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    (repo / "startup" / "templates").mkdir(parents=True)
    real_template = _REPO_ROOT / "startup" / "templates" / "nextseek.env.template"
    (repo / "startup" / "templates" / "nextseek.env.template").write_text(
        real_template.read_text()
    )
    render_nextseek_env(repo, default_values(nextseek_port=port))
    return (repo / "docker" / "nextseek.env").read_text()


def test_real_template_renders_public_and_internal_urls(tmp_path: Path) -> None:
    rendered = _render_real_template(tmp_path, port=8042)
    # Public hostname tracks the published host port ...
    assert 'NEXTSEEK_HOSTNAME="127.0.0.1:8042"' in rendered
    # ... the public base URL still derives from it (compose interpolation) ...
    assert 'NEXTSEEK_BASE_URL="http://$NEXTSEEK_HOSTNAME"' in rendered
    # ... and the internal transport URL is pinned to the in-container listener.
    assert 'NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"' in rendered


def test_real_template_internal_url_never_inherits_bumped_port(tmp_path: Path) -> None:
    rendered = _render_real_template(tmp_path, port=8001)
    internal_lines = [
        l for l in rendered.splitlines() if l.startswith("NEXTSEEK_INTERNAL_BASE_URL=")
    ]
    assert internal_lines == ['NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"']


def test_env_example_internal_url_parity_with_template() -> None:
    """Tripwire: the hand-maintained example must carry the same internal URL."""
    example = (_REPO_ROOT / "docker" / "nextseek.env.example").read_text()
    lines = [
        l for l in example.splitlines() if l.startswith("NEXTSEEK_INTERNAL_BASE_URL=")
    ]
    assert lines == ['NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"']
    # The public base URL keeps its NEXTSEEK_HOSTNAME derivation.
    assert 'NEXTSEEK_BASE_URL="http://${NEXTSEEK_HOSTNAME}"' in example


def test_local_settings_template_prod_overlay_suppresses_internal_url() -> None:
    """The PROD ChatConfig overlay must not be shadowed by the internal URL.

    ChatConfig prefers NEXTSEEK_INTERNAL_BASE_URL over NEXTSEEK_BASE_URL, so
    when _PROD_OVERRIDES sets a prod NEXTSEEK_BASE_URL the overlay block must
    pop NEXTSEEK_INTERNAL_BASE_URL for the duration of that construction.
    """
    template = (
        _REPO_ROOT / "startup" / "templates" / "local_settings.py.template"
    ).read_text()
    assert 'os.environ.pop("NEXTSEEK_INTERNAL_BASE_URL", None)' in template
    # And it must restore it afterwards (tracked in the saved-env snapshot).
    assert '"NEXTSEEK_INTERNAL_BASE_URL"' in template.split("_prev_env")[1]
