"""Tests for startup.lib.instance."""
from __future__ import annotations

import json
from pathlib import Path

from startup.lib.instance import (
    InstanceState,
    resolve_instance_name,
    load_instance,
    save_instance,
)


def test_resolve_instance_name_uses_explicit_when_given(tmp_path: Path) -> None:
    assert resolve_instance_name(repo_root=tmp_path, explicit="myname") == "myname"


def test_resolve_instance_name_uses_cwd_basename_when_explicit_none(tmp_path: Path) -> None:
    sub = tmp_path / "NExtSEEK-test"
    sub.mkdir()
    assert resolve_instance_name(repo_root=sub, explicit=None) == "NExtSEEK-test"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    state = InstanceState(
        name="test",
        prefix="test-",
        ports={"nextseek": 8001, "seek": 3001, "neo4j_http": 7475, "neo4j_bolt": 7688},
        compose_project_name="nextseek-test",
        created="2026-05-14T12:34:56-04:00",
    )
    save_instance(tmp_path, state)
    loaded = load_instance(tmp_path)
    assert loaded == state


def test_load_returns_none_if_missing(tmp_path: Path) -> None:
    assert load_instance(tmp_path) is None


def test_save_writes_to_startup_subdir(tmp_path: Path) -> None:
    (tmp_path / "startup").mkdir()
    state = InstanceState(
        name="x", prefix="", ports={"a": 1}, compose_project_name="x", created="now"
    )
    save_instance(tmp_path, state)
    assert (tmp_path / "startup" / ".instance.json").exists()


def test_compose_env_returns_correct_env_dict() -> None:
    state = InstanceState(
        name="test",
        prefix="test-",
        ports={"nextseek": 8001, "seek": 3001, "neo4j_http": 7475, "neo4j_bolt": 7688},
        compose_project_name="nextseek-test",
        created="2026-05-14T12:34:56-04:00",
    )
    env = state.compose_env()
    assert env["INSTANCE_PREFIX"] == "test-"
    assert env["NEXTSEEK_PORT"] == "8001"
    assert env["SEEK_PORT"] == "3001"
    assert env["NEO4J_HTTP_PORT"] == "7475"
    assert env["NEO4J_BOLT_PORT"] == "7688"
    assert env["COMPOSE_PROJECT_NAME"] == "nextseek-test"


def test_load_tolerates_instance_json_written_before_seek_public_url(tmp_path: Path) -> None:
    """An .instance.json from an older install must still load.

    load_instance does InstanceState(**data); a new field without a default
    would raise TypeError on every pre-existing install.
    """
    (tmp_path / "startup").mkdir(parents=True, exist_ok=True)
    (tmp_path / "startup" / ".instance.json").write_text(
        json.dumps(
            {
                "name": "legacy",
                "prefix": "",
                "ports": {"nextseek": 8000, "seek": 3000},
                "compose_project_name": "nextseek",
                "created": "2026-01-01T00:00:00Z",
            }
        )
    )
    loaded = load_instance(tmp_path)
    assert loaded is not None
    assert loaded.name == "legacy"
    assert loaded.seek_public_url == ""


def test_seek_public_url_survives_save_load_roundtrip(tmp_path: Path) -> None:
    state = InstanceState(
        name="dev",
        prefix="",
        ports={"nextseek": 8000, "seek": 3000},
        compose_project_name="nextseek",
        created="2026-07-16T00:00:00Z",
        seek_public_url="https://fairdata-dev.mit.edu",
    )
    save_instance(tmp_path, state)
    assert load_instance(tmp_path).seek_public_url == "https://fairdata-dev.mit.edu"
