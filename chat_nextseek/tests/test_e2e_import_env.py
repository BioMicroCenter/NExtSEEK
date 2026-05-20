"""import_env tests."""
from pathlib import Path

import pytest

from e2e.import_env import (
    _read_env_file, _interpolate, load_docker_env, load_prod_overrides,
    render_dotenv, write_env,
)


def test_read_env_file_basic(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text('KEY1="value1"\nKEY2=value2\n# comment\nKEY3=\'sq\'\n')
    out = _read_env_file(p)
    assert out == {"KEY1": "value1", "KEY2": "value2", "KEY3": "sq"}


def test_read_env_file_missing(tmp_path: Path):
    assert _read_env_file(tmp_path / "missing.env") == {}


def test_interpolate_dollar_var():
    env = {"MYSQL_HOST": "db"}
    assert _interpolate("$MYSQL_HOST", env) == "db"
    assert _interpolate("neo4j://$MYSQL_HOST", env) == "neo4j://db"


def test_interpolate_braced_var():
    env = {"HOST": "h"}
    assert _interpolate("${HOST}:3306", env) == "h:3306"


def test_interpolate_missing_var_to_empty():
    assert _interpolate("$UNSET", {}) == ""


def test_load_docker_env_real(tmp_path: Path, monkeypatch):
    """Smoke against the actual repo files; verifies interpolation works end-to-end."""
    out = load_docker_env()
    # MYSQL_HOST is set in db.env literally
    assert out.get("MYSQL_HOST") == "db"
    # SESSION_DB_HOST is set in nextseek.env via $MYSQL_HOST; should resolve to "db"
    assert out.get("SESSION_DB_HOST") == "db"
    # NEO4J_URI in nextseek.env: "neo4j://$NEXTSEEK_NEO4J_HOST"
    assert out.get("NEO4J_URI") == "neo4j://neo4j"


def test_load_prod_overrides_real():
    """Smoke against dmac/local_settings.py; verifies _PROD_OVERRIDES parse."""
    out = load_prod_overrides()
    # The dict in local_settings.py has these keys (with real prod values)
    assert "NEXTSEEK_BASE_URL" in out
    assert "API_USER" in out
    assert "NEO4J_URI" in out
    # Real prod URL should be there
    assert out.get("NEXTSEEK_BASE_URL", "").startswith("https://nextseek")


def test_render_dotenv_docker_local_smoke():
    content = render_dotenv(target="docker-local")
    # Required lines
    assert 'API_USER=""' in content
    assert 'API_PASS=""' in content
    assert 'SESSION_DB_TYPE="sqlite"' in content
    assert 'SESSION_DB_PATH="db.sqlite"' in content
    # Prod overrides present as comments
    assert "# NEXTSEEK_BASE_URL=" in content
    # Header mentions sources
    assert "docker/nextseek.env" in content
    assert "_PROD_OVERRIDES" in content


def test_render_dotenv_unknown_target_raises():
    with pytest.raises(ValueError, match="Only target='docker-local'"):
        render_dotenv(target="prod")


def test_write_env_refuses_overwrite(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text("existing")
    with pytest.raises(FileExistsError):
        write_env(path=p)
    # Force succeeds
    write_env(path=p, force=True)
    assert "API_USER" in p.read_text()
