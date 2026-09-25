"""The CC agent image runs a Claude Code that reports its own model fallback.

The CC 503 fallback passes ``--fallback-model`` and records each switch from the
``system/model_fallback`` stdout frame (``NessieAI/cc/translate.py``). A local run
against a fake Bedrock endpoint on 2026-09-25 showed 2.1.163, the previous pin, switch
model silently with no such frame, and send its auto-mode classifier to the main model
whatever ``ANTHROPIC_DEFAULT_SONNET_MODEL`` said; 2.1.282 emits the frame and honours
the variable. 2.1.280 is the first release that knows Opus 5.5.
"""
from __future__ import annotations

import re

from NessieAI import paths

DOCKERFILE = paths.CC_RUNTIME_DIR / "Dockerfile"
ARCHITECTURE = paths.NESSIE_ROOT / "docs" / "architecture.md"
_PIN = re.compile(r"@anthropic-ai/claude-code@(\d+)\.(\d+)\.(\d+)\b")


def _pins(text: str) -> list[tuple[int, int, int]]:
    return [tuple(int(part) for part in m.groups()) for m in _PIN.finditer(text)]


def test_the_image_pins_one_exact_claude_code_new_enough_for_the_fallback_frames():
    pins = _pins(DOCKERFILE.read_text(encoding="utf-8"))
    assert len(pins) == 1
    assert pins[0] >= (2, 1, 282)


def test_the_auto_mode_note_stays_true():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "OI-5: >= 2.1.158 required for CLAUDE_CODE_ENABLE_AUTO_MODE on Bedrock." in text
    assert _pins(text)[0] >= (2, 1, 158)


def test_the_architecture_doc_names_the_pin_the_image_ships():
    assert _pins(ARCHITECTURE.read_text(encoding="utf-8")) == _pins(
        DOCKERFILE.read_text(encoding="utf-8"))
