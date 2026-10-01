"""Pure helpers for test_deploy_live.py: what a page or a log says about the deploy.

Standard library only, like the rest of the smoke suite's helpers: it runs
outside the container, with no Django.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The chat bundle as the checkout commits it (the app image has no npm step), and
# the entry the chat page loads (seek/templates/smartSearch.html's vite_assets).
CHAT_BUNDLE_DIR = Path("static") / "js" / "chat_assistant"
CHAT_MANIFEST = CHAT_BUNDLE_DIR / ".vite" / "manifest.json"
CHAT_ENTRY = "src/main.embedded.tsx"

# What Django logs, with a formatted traceback, for each template variable it
# cannot resolve while the django.template logger runs at DEBUG
# (django/template/base.py). dmac/settings.py runs that logger at INFO.
TEMPLATE_LOOKUP_FAILED = b"Exception while resolving variable"


def chat_bundle_files(repo_root: Path = REPO_ROOT) -> list[str]:
    """The chat page entry's JS and CSS, relative to the bundle directory, as the
    checkout's manifest names them. Raises KeyError when the entry is missing."""
    manifest = json.loads((repo_root / CHAT_MANIFEST).read_text(encoding="utf-8"))
    entry = manifest[CHAT_ENTRY]
    return [entry["file"], *entry.get("css", [])]


_SELECT = re.compile(r'<select id="m_sampletype">(.*?)</select>', re.S)
_OPTION = re.compile(r'<option value="([^"]*)">')


def phone_type_options(html: str) -> list[str] | None:
    """The values of the Sample Search page's phone "Sample type" dropdown, or
    None when the page has no such dropdown."""
    select = _SELECT.search(html)
    if select is None:
        return None
    return _OPTION.findall(select.group(1))


def template_lookup_failures(chunk: bytes) -> int:
    """How many failed template lookups a stretch of django.log records."""
    return chunk.count(TEMPLATE_LOOKUP_FAILED)


def appended_since(path: Path, offset: int) -> bytes:
    """What was written to ``path`` after ``offset``; all of it when the file was
    rotated (it is now shorter than the offset)."""
    size = path.stat().st_size
    start = offset if size >= offset else 0
    with path.open("rb") as f:
        f.seek(start)
        return f.read()
