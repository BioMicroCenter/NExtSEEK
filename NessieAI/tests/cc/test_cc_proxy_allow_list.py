"""The Bedrock proxy allows every model a Container-CC turn can ask for, and no other.

The CC 503 fallback makes a turn name three models: its main Opus id (``--model``),
the fallback (``--fallback-model``, the map's ``opus_fallback``) and the auto-mode
classifier's Sonnet (``ANTHROPIC_DEFAULT_SONNET_MODEL``, the map's ``sonnet``). The
proxy refuses any other id with a 403 before Bedrock is reached, and Claude Code never
falls back on a 403, so an id missing here would turn every fallback into a failure.

Read from ``app/config.py`` with ``ast``: the proxy directory is hyphenated and is not
an importable package here.
"""
from __future__ import annotations

import ast

from dmac_assistant.router import models

from NessieAI import paths

CONFIG = paths.BEDROCK_PROXY_DIR / "app" / "config.py"


def _default_allowed_models() -> tuple[str, ...]:
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "_DEFAULT_ALLOWED_MODELS"):
            return ast.literal_eval(node.value)
    raise AssertionError("app/config.py no longer defines _DEFAULT_ALLOWED_MODELS")


def _map() -> dict[str, str]:
    return models.load_model_class_map(path=models._DEFAULT_PATH)


def test_the_main_cc_model_is_allowed_and_listed_first():
    assert _default_allowed_models()[0] == _map()["opus"]


def test_the_allow_list_is_exactly_the_models_a_cc_turn_names():
    mapping = _map()
    allowed = _default_allowed_models()
    assert len(allowed) == len(set(allowed))
    assert set(allowed) == {mapping["opus"], mapping["opus_fallback"], mapping["sonnet"]}


def test_a_model_no_cc_turn_names_stays_refused():
    assert _map()["haiku"] not in _default_allowed_models()
