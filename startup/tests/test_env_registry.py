"""Tests for startup.lib.env_registry: the schema of startup/env_registry.toml (#103)."""
from __future__ import annotations

from pathlib import Path

import pytest

from startup.lib import env_registry
from startup.lib.env_registry import FILES, REGISTRY_PATH, Entry, load


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "registry.toml"
    path.write_text(text)
    return path


def test_the_committed_registry_loads() -> None:
    entries = load(REGISTRY_PATH)
    assert entries, "startup/env_registry.toml declares no key"
    assert all(isinstance(e, Entry) for e in entries.values())


def test_every_files_item_is_known() -> None:
    for key, entry in load(REGISTRY_PATH).items():
        assert entry.files, key
        assert set(entry.files) <= FILES, (key, entry.files)


def test_every_alias_names_an_entry() -> None:
    entries = load(REGISTRY_PATH)
    for key, entry in entries.items():
        if entry.alias_of is not None:
            assert entry.alias_of in entries, (key, entry.alias_of)


def test_no_secret_has_a_real_default() -> None:
    for key, entry in load(REGISTRY_PATH).items():
        if entry.secret:
            assert entry.default is None or entry.default in env_registry.PLACEHOLDERS, key


def test_no_forbidden_key_is_required() -> None:
    for key, entry in load(REGISTRY_PATH).items():
        if entry.forbidden:
            assert not entry.required, key


def test_the_install_templates_secrets_are_flagged() -> None:
    entries = load(REGISTRY_PATH)
    for key in ("DJANGO_SECRET_KEY", "MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD", "NEXTSEEK_NEO4J_PASSWORD",
                "NEO4J_PASSWORD", "GCP_API_KEY", "AWS_BEARER_TOKEN_BEDROCK", "LAYA_API_KEY", "FDH_API"):
        assert entries[key].secret, key


def test_a_minimal_entry_takes_the_defaults(tmp_path: Path) -> None:
    entries = load(_write(tmp_path, '[FOO]\nfiles = ["nextseek.env"]\n'))
    assert entries == {"FOO": Entry(files=("nextseek.env",))}
    assert entries["FOO"].default is None and not entries["FOO"].secret


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[FOO]\nfiles = ["nextseek.env"]\ncolour = "red"\n', "unknown field"),
        ("[FOO]\ndoc = \"x\"\n", "files"),
        ('[FOO]\nfiles = ["nextseek.env", "elsewhere.env"]\n', "elsewhere.env"),
        ('[FOO]\nfiles = ["nextseek.env"]\nalias_of = "BAR"\n', "BAR"),
        ('[FOO]\nfiles = ["nextseek.env"]\nsecret = true\ndefault = "hunter2"\n', "secret"),
        ('[FOO]\nfiles = ["nextseek.env"]\nforbidden = true\nrequired = true\n', "forbidden"),
        ('[FOO]\nfiles = ["nextseek.env"]\ndefault = 5\n', "default"),
        ('[FOO]\nfiles = ["nextseek.env"]\nsecret = "yes"\n', "secret"),
        ('[foo]\nfiles = ["nextseek.env"]\n', "foo"),
    ],
)
def test_the_loader_refuses_a_bad_entry(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load(_write(tmp_path, text))


def test_a_secret_may_carry_a_placeholder_default(tmp_path: Path) -> None:
    entries = load(_write(tmp_path, '[K]\nfiles = ["nextseek.env"]\nsecret = true\ndefault = "SET_IN_LOCAL_ENV"\n'))
    assert entries["K"].default == "SET_IN_LOCAL_ENV"
