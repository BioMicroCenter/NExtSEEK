"""Make a tape safe for a public repository.

Sample metadata keeps its shape but loses every value under a person-like key; protocol text
keeps only the param-atlas markers found in it (never the lines around them), since nothing else
is read. Every URL on a tape is rewritten to "<url>" unless it is public nf-core test data or on
a public sequence archive (ARCHIVE_URLS), so no instance hostname reaches the repository; an
error message also loses its bare request path and host. A tape that still holds an email
address or an absolute path is refused, with the patterns scripts/reingest/sanitize_fixture.py
uses. The archive allow-list lives here only: sanitize_fixture keeps its narrower one.
"""
from __future__ import annotations

import importlib.util
import json
import re
import urllib.parse
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


#: Public sequence archives whose URLs a tape keeps as they are: (scheme, host, path prefix).
#: A host given as a pattern is matched whole. Real D.SEQ metadata and resolve_accessions carry these.
ARCHIVE_URLS: tuple[tuple[str, "str | re.Pattern", str], ...] = (
    ("https", "sra-pub-run-odp.s3.amazonaws.com", "/"),
    ("s3", re.compile(r"sra-pub-(?:src|run)-[a-z0-9-]+"), "/"),
    ("https", "ftp.sra.ebi.ac.uk", "/"),
    ("ftp", "ftp.sra.ebi.ac.uk", "/"),
    ("https", "www.ebi.ac.uk", "/ena/"),
    ("https", "trace.ncbi.nlm.nih.gov", "/"),
    ("https", "www.ncbi.nlm.nih.gov", "/geo/"),
    ("https", "www.ncbi.nlm.nih.gov", "/sra/"),
)
_ARCHIVE_PATH = re.compile(r"^[A-Za-z0-9._~/+=-]*$")
_ARCHIVE_QUERY = re.compile(r"^[A-Za-z0-9._~=&+-]*$")
#: What stands in for a URL that is not kept.
URL_PLACEHOLDER = "<url>"
#: urllib3's connection errors name the request path bare ("... with url: /api/x?y=1 (Caused by")
#: and the host in quotes; neither is a URL, so both are cut before the URL rewrite.
_WITH_URL = re.compile(r" with url: \S+")
_QUOTED_HOST = re.compile(r"(host=|resolve )'[^']*'")


def archive_url(url: str) -> bool:
    """True only for one plain URL on a public archive: a listed scheme, host and path prefix, no
    userinfo or port, no dot-dot segment, no percent-encoding, no fragment, plain characters only."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if (url.count("://") != 1 or parsed.fragment or "%" in url
            or not _ARCHIVE_PATH.match(parsed.path) or not _ARCHIVE_QUERY.match(parsed.query)
            or ".." in parsed.path.split("/")):
        return False
    for scheme, host, prefix in ARCHIVE_URLS:
        host_ok = host.fullmatch(parsed.netloc) if isinstance(host, re.Pattern) else parsed.netloc == host
        if parsed.scheme == scheme and host_ok and parsed.path.startswith(prefix):
            return True
    return False


def allowed_url(url: str) -> bool:
    """A URL a tape may keep: public nf-core test data, or a public archive's."""
    return _sanitize.allowed_url(url) or archive_url(url)


def _pieces(match: str) -> list[str]:
    """One URL-regex match split on the separators it runs through, separators kept."""
    return re.split(f"({_sanitize._URL_SEPARATORS.pattern})", match)


def disallowed_urls(text: str) -> list[str]:
    """Every URL-like piece of text that allowed_url does not keep (the same splitting as sanitize_fixture)."""
    out = []
    for match in _sanitize.URL.findall(text):
        for piece in _sanitize._URL_SEPARATORS.split(match):
            piece = piece.rstrip(_sanitize._TRAILING_PUNCTUATION)
            if "://" in piece and not allowed_url(piece):
                out.append(piece)
    return out


def rewrite_urls(text: str) -> str:
    """Every URL allowed_url does not keep becomes "<url>"; the punctuation around it stays."""
    def one(m: re.Match) -> str:
        out = []
        for piece in _pieces(m.group(0)):
            core = piece.rstrip(_sanitize._TRAILING_PUNCTUATION)
            if "://" in core and not allowed_url(core):
                piece = URL_PLACEHOLDER + piece[len(core):]
            out.append(piece)
        return "".join(out)
    return _sanitize.URL.sub(one, text)


def _for_tape_text(text: str) -> str:
    text = _QUOTED_HOST.sub(lambda m: f"{m.group(1)}'<host>'", _WITH_URL.sub("", text))
    return rewrite_urls(text)


def _walk_strings(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {(fn(k) if isinstance(k, str) else k): _walk_strings(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_walk_strings(v, fn) for v in value]
    return value


def rewrite_urls_in(value):
    """rewrite_urls over every string leaf and key of a JSON-like value (a new value; tuples become lists)."""
    return _walk_strings(value, rewrite_urls)


def for_tape(value):
    """What a tape keeps of a value: error messages' bare request paths and hosts cut, then
    every URL that is not kept rewritten. Emails and absolute paths are left for refusals_in."""
    return _walk_strings(value, _for_tape_text)


def refusals(text: str) -> list[str]:
    out = [f"email {m}" for m in _sanitize.EMAIL.findall(text)]
    out += [f"path /{m}" for m in _sanitize.ABSOLUTE.findall(text)]
    out += [f"url {u}" for u in disallowed_urls(text)]
    return out


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
