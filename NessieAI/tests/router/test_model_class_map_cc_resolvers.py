"""The two extra model ids a Container-CC turn needs, read from the one model-id file.

``router_model_class_map.json`` is the only place model ids live. A CC turn now needs
two more than its main Opus id:

- ``opus_fallback``: the model Claude Code switches to (``--fallback-model``) when the
  main one answers with a server error, so a Bedrock 503 no longer ends the turn.
- ``sonnet``: Claude Code 2.1.282's auto-mode classifier asks a Sonnet model about each
  tool call; the engine points it at this id (``ANTHROPIC_DEFAULT_SONNET_MODEL``) so it
  does not ask for one the Bedrock proxy refuses.

The fallback entry is optional and checked in its own resolver, so a missing or
malformed one costs the fallback flag and never the main CC model id.
"""
from __future__ import annotations

import json

import pytest

from dmac_assistant.config import ConfigError
from dmac_assistant.router import models


@pytest.fixture
def use_map(tmp_path, monkeypatch):
    """Point the module cache at a map written to a temp file."""
    def _use(mapping: dict) -> None:
        path = tmp_path / "map.json"
        path.write_text(json.dumps(mapping), encoding="utf-8")
        monkeypatch.setattr(models, "_cache", models.load_model_class_map(path=path))
    return _use


BASE = {
    "opus": "us.anthropic.claude-opus-4-8",
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def test_the_committed_map_declares_a_cc_fallback_distinct_from_the_main_model():
    mapping = models.load_model_class_map(path=models._DEFAULT_PATH)
    fallback = mapping.get("opus_fallback")
    assert isinstance(fallback, str)
    assert models._BEDROCK_ID_RE.match(fallback)
    assert fallback != mapping["opus"]


def test_the_fallback_resolver_reads_the_map(use_map):
    use_map({**BASE, "opus_fallback": "us.anthropic.claude-opus-4-7"})
    assert models.resolve_cc_fallback_model() == "us.anthropic.claude-opus-4-7"


def test_a_map_with_no_fallback_entry_resolves_to_none(use_map):
    use_map(dict(BASE))
    assert models.resolve_cc_fallback_model() is None


@pytest.mark.parametrize("bad", ["claude-opus-4-7", "", "anthropic.claude-opus-4-7", 7])
def test_a_malformed_fallback_entry_is_refused_by_its_resolver_only(use_map, bad):
    use_map({**BASE, "opus_fallback": bad})
    with pytest.raises(ConfigError):
        models.resolve_cc_fallback_model()
    # The main CC model id does not depend on the fallback entry.
    assert models.resolve_cc_model() == BASE["opus"]


def test_the_classifier_resolver_returns_the_sonnet_entry(use_map):
    use_map({**BASE, "opus_fallback": "us.anthropic.claude-opus-4-7"})
    assert models.resolve_cc_classifier_model() == BASE["sonnet"]


def test_the_required_entries_are_still_required(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"opus": BASE["opus"], "opus_fallback": BASE["opus"]}))
    with pytest.raises(ConfigError):
        models.load_model_class_map(path=path)


@pytest.mark.parametrize("value, ok", [
    ("us.anthropic.claude-sonnet-4-6", True),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", True),
    ("claude-sonnet-4-6", False), ("us.anthropic.", False), ("us.anthropic.A B", False),
    ("", False), (None, False), (7, False),
])
def test_the_bedrock_id_check_is_public_for_callers_that_take_an_override(value, ok):
    assert models.is_bedrock_model_id(value) is ok
