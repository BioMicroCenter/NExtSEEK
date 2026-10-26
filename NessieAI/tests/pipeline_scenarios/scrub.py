"""Make a tape safe for a public repository.

Sample metadata keeps its shape but loses every value under a person-like key; protocol text
keeps only the param-atlas markers found in it (never the lines around them), since nothing else
is read; and a
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

#: NExtSEEK's person and group fields by exact name (context/ and the sample-type attributes
#: hold Scientist, CoScientist, Coscientist, InstrumentUser, PrepUser, QCUser, Lab, TeamLab,
#: Contact and Funder), then the families of names a new sample type could add.
PERSON_NAMES = ("Scientist", "CoScientist", "Coscientist", "InstrumentUser", "PrepUser", "QCUser",
                "Lab", "TeamLab", "Contact", "Funder", "Submitter", "Owner", "Creator", "PI",
                "PrincipalInvestigator")
PERSON_KEYS = re.compile(
    r"(?i)^(" + "|".join(re.escape(n) for n in PERSON_NAMES) + r"|principal[_ ]?investigator|"
    r"co[_ ]?scientist|team[_ ]?lab|contact.*|author.*|person.*|contributor.*|.*e-?mail.*|"
    r".*_by|(?:submitted|created|collected|prepared|processed|performed|requested|owned)[_ ]?by|"
    r".*user)$")
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


def protocol_markers_found(text: str, markers: set[str] | None = None) -> str:
    """The markers (lower case, one per line, sorted) that occur in the text, and nothing else.

    param_atlas tests each marker as a case-insensitive substring of the whole text, so this
    reproduces every match it can make without keeping a word of the prose around it.
    """
    markers = atlas_markers() if markers is None else {m.lower() for m in markers}
    low = (text or "").lower()
    return "\n".join(sorted(m for m in markers if m in low))


def refusals(text: str) -> list[str]:
    out = [f"email {m}" for m in _sanitize.EMAIL.findall(text)]
    out += [f"path /{m}" for m in _sanitize.ABSOLUTE.findall(text)]
    out += [f"url {u}" for u in _sanitize.disallowed_urls(text)]
    return out


def allowed_url(url: str) -> bool:
    return _sanitize.allowed_url(url)


def refusals_in(value) -> list[str]:
    """`refusals` over every string leaf (and key) of a value, so an escaped newline in JSON text
    can never hide an absolute path from the path pattern."""
    if isinstance(value, str):
        return refusals(value)
    if isinstance(value, dict):
        return [r for k, v in value.items() for r in refusals_in(str(k)) + refusals_in(v)]
    if isinstance(value, (list, tuple, set)):
        return [r for v in value for r in refusals_in(v)]
    return []
