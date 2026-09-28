"""The committed chat bundle was built from this checkout's chat source (CI-COVERAGE gap 8).

A chat UI change is two commits: the source, then the rebuilt bundle in ``static/js/chat_assistant/`` (only the
bundle ships). Nothing tied the two together, so a source change whose rebuild was forgotten, or a bundle rebuilt
from another branch, passed every test. These read the entry file the Vite manifest names and check it carries the
newest user-visible text of two source files: the About dialog's "tries a second one" line and the Debug panel's
``detail`` line (both 2026-09-25). No build and no node: the bundle is read as text.
"""
from __future__ import annotations

import json
import re

from NessieAI import paths

BUNDLE_DIR = paths.REPO_ROOT / "static" / "js" / "chat_assistant"
SRC = paths.NESSIE_ROOT / "chat_frontend" / "src"


def _entry_js() -> str:
    manifest = json.loads((BUNDLE_DIR / ".vite" / "manifest.json").read_text(encoding="utf-8"))
    (entry,) = [e for e in manifest.values() if e.get("isEntry")]
    return (BUNDLE_DIR / entry["file"]).read_text(encoding="utf-8")


def _about_line(marker: str) -> str:
    """The About dialog's list item holding ``marker``, with its source line breaks collapsed as JSX does."""
    src = (SRC / "components" / "Layout" / "AboutDialog.tsx").read_text(encoding="utf-8")
    for item in re.findall(r"<li>(.*?)</li>", src, re.S):
        text = " ".join(item.split())
        if marker in text:
            return text
    raise AssertionError(f"AboutDialog.tsx has no list item with {marker!r}")


def test_the_bundle_carries_the_about_dialogs_second_model_line():
    line = _about_line("tries a second one")
    assert line in _entry_js(), "rebuild the bundle (npm run build:embedded) after an AboutDialog.tsx change"


def test_the_bundle_carries_the_debug_panels_detail_line():
    src = (SRC / "lib" / "debugEntries.ts").read_text(encoding="utf-8")
    assert "\\ndetail=${" in src, "debugEntries.ts no longer writes the detail line this test looks for"
    js = _entry_js()
    # The minifier may write the template's line break as a real newline or keep the escape.
    assert "\ndetail=${" in js or "\\ndetail=${" in js, (
        "rebuild the bundle (npm run build:embedded) after a debugEntries.ts change")
