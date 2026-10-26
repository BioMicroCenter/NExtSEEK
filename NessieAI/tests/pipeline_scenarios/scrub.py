"""Make a tape safe for a public repository.

Sample metadata keeps its shape but loses every value under a person-like key; protocol text
keeps only the lines a param-atlas marker can match, since nothing else in it is read; and a
tape that still holds an email address, an absolute path or a URL (other than public nf-core
test data) is refused, with the same patterns scripts/reingest/sanitize_fixture.py uses.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("sanitize_fixture", _REPO / "scripts" / "reingest" / "sanitize_fixture.py")
_sanitize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sanitize)

PERSON_KEYS = re.compile(r"(?i)^(scientist|submitter|owner|creator|pi|principal[_ ]?investigator|"
                         r"contact.*|.*e-?mail.*|author.*|person.*|contributor.*)$")
PARAM_ATLAS = (_REPO / "NessieAI" / "chat_nextseek" / "src" / "chat_nextseek" / "seqera"
               / "nfcore_param_atlas.json")


def scrub_metadata(obj):
    if isinstance(obj, dict):
        return {k: ("REDACTED" if PERSON_KEYS.match(str(k)) and v not in (None, "") else scrub_metadata(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_metadata(v) for v in obj]
    return obj


def atlas_markers() -> set[str]:
    """Every string listed anywhere in the param atlas except its prose notes."""
    found: set[str] = set()

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k != "guidance":
                    walk(v, k)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str):
                    found.add(v.lower())
                else:
                    walk(v, key)
    walk(json.loads(PARAM_ATLAS.read_text(encoding="utf-8")))
    return {m for m in found if len(m) >= 2}


def protocol_marker_lines(text: str, markers: set[str] | None = None) -> str:
    markers = atlas_markers() if markers is None else {m.lower() for m in markers}
    return "\n".join(line for line in (text or "").splitlines()
                     if any(m in line.lower() for m in markers))


def refusals(text: str) -> list[str]:
    out = [f"email {m}" for m in _sanitize.EMAIL.findall(text)]
    out += [f"path /{m}" for m in _sanitize.ABSOLUTE.findall(text)]
    out += [f"url {u}" for u in _sanitize.URL.findall(text) if not u.startswith(_sanitize.ALLOWED_URL_PREFIXES)]
    return out
