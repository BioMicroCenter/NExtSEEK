"""Regression tests for startup-generated deploy gap files."""
from __future__ import annotations

import dataclasses
import importlib.util
import shutil
from pathlib import Path

from startup.lib.env import read_env
from startup.steps.config import (
    default_values,
    render_db_env,
    render_nextseek_env,
    render_proxy_secret_env,
    render_root_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Pinned literally rather than read from startup.lib.layout, so moving the
# constant alone cannot move these expectations with it.
PROXY = Path("NessieAI") / "docker" / "bedrock-proxy" / "proxy-secret.env"
LEGACY_PROXY = Path("docker") / "bedrock-proxy" / "proxy-secret.env"


def test_render_proxy_secret_env_writes_only_under_nessieai(tmp_path: Path) -> None:
    """write_env() creates missing parents, so a stale path would not fail: it
    would quietly recreate docker/bedrock-proxy/ and put the token there."""
    path = render_proxy_secret_env(tmp_path, source_env={})
    assert path == tmp_path / PROXY
    assert not (tmp_path / "docker").exists()


def test_render_proxy_secret_env_carries_a_pre_move_token_across(tmp_path: Path) -> None:
    """A re-run on a box that has not moved its token yet must not render an
    empty one at the new path (the never-clobber rule, D2)."""
    legacy = tmp_path / LEGACY_PROXY
    legacy.parent.mkdir(parents=True)
    original = 'AWS_BEARER_TOKEN_BEDROCK="ABSK-pre-move"\nAWS_REGION="us-west-2"\n'
    legacy.write_text(original)

    out = render_proxy_secret_env(tmp_path, source_env={})

    assert read_env(out) == {
        "AWS_BEARER_TOKEN_BEDROCK": "ABSK-pre-move",
        "AWS_REGION": "us-west-2",
    }
    # Read only: the pre-move file is neither rewritten nor removed here.
    assert legacy.read_text() == original


def test_render_proxy_secret_env_prefers_the_current_file_to_the_pre_move_one(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / LEGACY_PROXY
    legacy.parent.mkdir(parents=True)
    legacy.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-pre-move"\n')
    current = tmp_path / PROXY
    current.parent.mkdir(parents=True)
    current.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-current"\n')

    render_proxy_secret_env(tmp_path, source_env={})

    assert read_env(current)["AWS_BEARER_TOKEN_BEDROCK"] == "ABSK-current"


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

    rendered = read_env(repo / PROXY)
    assert rendered == {
        "AWS_BEARER_TOKEN_BEDROCK": "ABSKexample",
        "AWS_REGION": "us-west-2",
    }


def test_render_proxy_secret_env_defaults_without_token(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    render_proxy_secret_env(repo, {})

    rendered = read_env(repo / PROXY)
    assert rendered == {
        "AWS_BEARER_TOKEN_BEDROCK": "",
        "AWS_REGION": "us-east-1",
    }


def test_render_proxy_secret_env_accepts_default_region(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    render_proxy_secret_env(repo, {"AWS_DEFAULT_REGION": "us-east-2"})

    rendered = read_env(repo / PROXY)
    assert rendered["AWS_REGION"] == "us-east-2"


def test_render_proxy_secret_env_preserves_hand_filled_token(tmp_path: Path) -> None:
    out = tmp_path / PROXY
    out.parent.mkdir(parents=True)
    out.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-hand-filled"\nAWS_REGION="us-east-2"\n')
    render_proxy_secret_env(tmp_path, source_env={})
    kept = read_env(out)
    assert kept["AWS_BEARER_TOKEN_BEDROCK"] == "ABSK-hand-filled"
    assert kept["AWS_REGION"] == "us-east-2"


def test_render_proxy_secret_env_operator_env_beats_existing_file(tmp_path: Path) -> None:
    out = tmp_path / PROXY
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
    out = tmp_path / PROXY
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


def test_render_root_env_writes_neo4j_password_when_given(tmp_path: Path) -> None:
    """docker-compose.yml uses ${NEO4J_PASSWORD:?...}, which aborts every verb.

    Compose resolves interpolation before it validates, so an unset value fails
    `config` and `ps`, not just `up`. Nothing else writes this key, so if it is
    absent here a bare `docker compose` cannot run at all.
    """
    repo = tmp_path / "repo"

    render_root_env(
        repo,
        {"COMPOSE_PROJECT_NAME": "nextseek-test", "INSTANCE_PREFIX": "test-"},
        neo4j_password="s3cret",
    )

    out = repo / ".env"
    assert read_env(out) == {
        "COMPOSE_PROJECT_NAME": "nextseek-test",
        "INSTANCE_PREFIX": "test-",
        "NEO4J_PASSWORD": "s3cret",
    }
    assert (out.stat().st_mode & 0o777) == 0o600


def test_render_root_env_never_copies_a_secret_from_compose_env(tmp_path: Path) -> None:
    """The password arrives as an explicit argument, never through the mapping.

    Pins the filter itself: a credential that happens to be named NEO4J_PASSWORD
    in the instance environment must still be ignored, so the only way this key
    reaches .env is a caller that meant it.
    """
    repo = tmp_path / "repo"

    render_root_env(
        repo,
        {"COMPOSE_PROJECT_NAME": "nextseek-test", "NEO4J_PASSWORD": "must-not-copy"},
    )

    assert read_env(repo / ".env") == {"COMPOSE_PROJECT_NAME": "nextseek-test"}


def test_render_root_env_persists_db_port(tmp_path: Path) -> None:
    """.env is what a bare `docker compose` reads, so DB_PORT must land there.

    startup passes compose_env directly during install, so the port is correct
    then. But the documented dev workflow (`docker compose up -d --build
    nextseek`) reads .env instead -- without DB_PORT it falls back to the
    compose default 3306 and collides with any other instance on this host.
    """
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
            "DB_PORT": "3307",
        },
    )

    assert read_env(repo / ".env")["DB_PORT"] == "3307"


def test_render_root_env_escapes_dollar_in_neo4j_password(tmp_path: Path) -> None:
    """Compose interpolates $ in .env, even inside double quotes; $$ is a literal $."""
    repo = tmp_path / "repo"

    render_root_env(repo, {"COMPOSE_PROJECT_NAME": "x"}, neo4j_password="pa$word$1")

    assert 'NEO4J_PASSWORD="pa$$word$$1"' in (repo / ".env").read_text()


def test_dollar_neo4j_password_reaches_server_and_app_through_compose(tmp_path: Path) -> None:
    """#112: the rendered files, parsed the way compose parses them, give the
    server (root .env) and the app (docker/nextseek.env) the same password.

    Uses the repo's own emulation of compose's env-file rules
    (scripts/graph_search/nessie_venue_check.py), fed the way nessie_venue.sh
    feeds it: the root .env as the lookup, then db.env and nextseek.env.
    """
    path = REPO_ROOT / "scripts" / "graph_search" / "nessie_venue_check.py"
    spec = importlib.util.spec_from_file_location("nessie_venue_check", path)
    venue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(venue)

    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "startup" / "templates", repo / "startup" / "templates")
    password = "$Abc_d$1"  # leading $ as in the issue; $1 is not a name, so compose keeps it
    values = dataclasses.replace(default_values(8000, 3000), neo4j_password=password)
    render_db_env(repo, values)
    render_nextseek_env(repo, values)
    render_root_env(repo, {"COMPOSE_PROJECT_NAME": "x"}, neo4j_password=password)

    lookup = venue.dotenv_values((repo / ".env").read_text())
    env, _ = venue.compose_env(
        [(repo / "docker" / name).read_text() for name in ("db.env", "nextseek.env")], lookup
    )

    assert lookup["NEO4J_PASSWORD"] == password  # NEO4J_AUTH seeds the server from this
    assert env["NEXTSEEK_NEO4J_PASSWORD"] == password
    assert env["NEO4J_PASSWORD"] == password
