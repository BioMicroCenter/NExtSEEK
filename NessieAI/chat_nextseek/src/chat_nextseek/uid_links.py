"""Turn every sample UID a reply names into a link to that sample's page.

The page is ``/seek/sampletree/uid=<UID>/`` (``seek/urls.py``, view
``seek.views.samples.sampleTree``): it resolves the UID to the sample's id and renders
the same page as ``/seek/sample/id=<n>/``, including the project-scope 404. It is the
URL the search results table already uses (``seek/sample/table.py``) and the one the
chat UI's own ``remark-uid-links.ts`` builds. Links are relative so they work on every
instance.

The chat UI linkifies bare UIDs itself, but only in markdown text nodes, so a UID the
model wraps in backticks (the chatter's habit: ``**`TIS-200901ENG-1`**``) stays plain
code. Rewriting the reply text here covers both forms, and the UI then leaves the
existing links alone (the plugin skips text under a link).

Conservative by construction: fenced code blocks (the ``**Debug info**`` JSON among
them), existing markdown links, autolinks, URLs and inline code that is anything more
than a single UID are copied through untouched, and a UID glued to another word, path
or suffix is not a UID. Running it twice changes nothing, because its own output is an
existing link.
"""
from __future__ import annotations

import re
from urllib.parse import quote

# <TYPE>-<YYMMDD><LAB>-<n>[-PUB[n]]. TYPE is a sample type code: two or more capitals,
# optionally behind a one-letter class and a dot (D.SEQ, A.GEX); every code in
# sampletypes_db.json fits. LAB is three capitals, as in parser._WELL_FORMED_UID_RE.
_UID_CORE = r"(?:[A-Z]\.)?[A-Z]{2,8}-\d{6}[A-Z]{3}-\d+(?:-PUB\d*)?"

# Not preceded by a word character, a dot, a hyphen, a slash or '=' (so nothing inside
# a longer token, a path or a query string), and not followed by a word character, a
# hyphen or a dot that starts another word (a file name like NHP-...-1.fastq).
_BARE_UID = rf"(?<![\w./=-])(?P<uid>{_UID_CORE})(?![\w-]|\.\w)"

_ONLY_UID = re.compile(rf"\s*({_UID_CORE})\s*")

_INLINE = re.compile(
    # An existing markdown link or image: [text](target). One level of nested brackets.
    r"(?P<link>!?\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]\([^)\n]*\))"
    # An autolink or inline HTML tag.
    r"|(?P<angle><[^<>\s]+>)"
    # A bare URL.
    r"|(?P<url>\b[A-Za-z][A-Za-z0-9+.-]*://\S+)"
    # An inline code span, delimited by a run of backticks of the same length.
    r"|(?P<code>(?P<ticks>`+)(?P<body>.+?)(?<!`)(?P=ticks)(?!`))"
    rf"|{_BARE_UID}"
)

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def sample_url(uid: str) -> str:
    """The relative URL of the sample page for ``uid``."""
    return f"/seek/sampletree/uid={quote(uid, safe='')}/"


def _rewrite_inline(match: re.Match) -> str:
    uid = match.group("uid")
    if uid:
        return f"[{uid}]({sample_url(uid)})"
    code = match.group("code")
    if code:
        only = _ONLY_UID.fullmatch(match.group("body"))
        if only:
            return f"[{code}]({sample_url(only.group(1))})"
    return match.group(0)


def _rewrite_prose(text: str) -> str:
    return _INLINE.sub(_rewrite_inline, text)


def link_sample_uids(text: str | None) -> str | None:
    """``text`` with each sample UID outside code and links made a markdown link."""
    if not text:
        return text
    out: list[str] = []
    prose: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        opener = _FENCE.match(line)
        if fence is None:
            if opener:
                out.append(_rewrite_prose("".join(prose)))
                prose = []
                fence = opener.group(1)
                out.append(line)
            else:
                prose.append(line)
            continue
        out.append(line)
        # A closing fence is the same character, at least as long, and nothing after it.
        if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence) \
                and not line[opener.end():].strip():
            fence = None
    out.append(_rewrite_prose("".join(prose)))
    return "".join(out)
