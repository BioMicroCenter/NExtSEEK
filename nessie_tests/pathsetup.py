"""Put repo-root chat_nextseek/ on sys.path so `import e2e...` resolves.

The e2e/ package lives at chat_nextseek/e2e/ (sibling of src/), NOT in the
installed chat_nextseek dist — mirror chat_nextseek/conftest.py:11-14.
"""
from __future__ import annotations
import sys
from pathlib import Path

_CHAT_NEXTSEEK = Path(__file__).resolve().parents[1] / "chat_nextseek"


def ensure_e2e_importable() -> None:
    p = str(_CHAT_NEXTSEEK)
    if p not in sys.path:
        sys.path.insert(0, p)
