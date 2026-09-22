"""CATALOG_FILE must be an optional override, not a requirement (branch-switch crash-loop).

`docker/nextseek.env` is gitignored and survives branch switches. A hardcoded
absolute `CATALOG_FILE=...` left over from one branch's directory layout
pointed at a path that does not exist on other branches, so rebuilding the
stack there raised a bare `FileNotFoundError` deep inside `pathlib` — and the
Django entrypoint's own failure message then blamed a full static-volume disk,
nowhere near the real cause.

`REPO_ROOT` (config.py `_get_env_config`, `BASE_DIR.parent.parent`) is derived
from `__file__` exactly like `PROMPTS_DIR`/`CONTEXT_DIR`/`SEQ_TEMPLATE_PATH`
are derived from `BASE_DIR` — correct regardless of where the package is
checked out. `CATALOG_FILE` now follows the same pattern: unset, it resolves
to `REPO_ROOT/agent_model_catalog.json`; set, the env value stays authoritative
(explicit config is never silently overridden) but a missing target now fails
with a message that names the resolved path, says where it came from, and — for
an env override specifically — names the likely cause and the fix.
"""
from __future__ import annotations

import pytest

from chat_nextseek.config import ChatConfig


def _clear_catalog_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATALOG_FILE", raising=False)
    monkeypatch.delenv("AGENT_MODEL_CATALOG", raising=False)
    # Pin MODEL_MODE so this test doesn't depend on ambient machine env.
    monkeypatch.setenv("NEXTSEEK_MODE", "gcp")
    monkeypatch.setenv("GCP_API_KEY", "dummy-for-test-only")


def test_catalog_file_unset_resolves_to_repo_root_and_loads(monkeypatch):
    """With CATALOG_FILE unset, ChatConfig derives REPO_ROOT/agent_model_catalog.json
    and loads it successfully — proven by executing the real derivation, not by
    asserting the expression."""
    _clear_catalog_env(monkeypatch)

    cfg = ChatConfig()

    expected_default = str(cfg.REPO_ROOT / "agent_model_catalog.json")
    assert cfg.CATALOG_FILE == expected_default
    assert cfg.AGENT_MODEL_CATALOG, "catalog should have loaded from the derived path"
    assert "default" in cfg.AGENT_MODEL_CATALOG


def test_catalog_file_env_override_missing_file_fails_legibly(monkeypatch, tmp_path):
    """An explicitly-set CATALOG_FILE stays authoritative (never silently
    overridden), but a missing target must fail with a message naming the
    resolved path, the env-var source, and the fix — not a bare
    FileNotFoundError from inside pathlib."""
    _clear_catalog_env(monkeypatch)
    stale_path = tmp_path / "stale-branch-layout" / "agent_model_catalog.json"
    monkeypatch.setenv("CATALOG_FILE", str(stale_path))

    with pytest.raises(RuntimeError) as exc_info:
        ChatConfig()

    message = str(exc_info.value)
    assert str(stale_path) in message
    assert "CATALOG_FILE" in message
    assert "unset" in message.lower()

