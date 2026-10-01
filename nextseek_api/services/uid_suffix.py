"""One place that knows a UID may be written with or without its publication suffix (F14).

A published sample's UID carries a ``-PUB`` suffix in SEEK. Researchers write it either way,
and so do the UIDs they copy out of a paper or a previous reply. The graph path has resolved
both spellings since ``chat_nextseek.helpers.uid_check`` was added, and tells the user which
one it found. Every REST path resolved the string exactly as given, through a private
``_resolve_uid_to_seek_id`` copied into each service, so a ``-PUB`` UID simply 404ed: thirteen
questions in the 2026-09-10 production review hit the right endpoint and failed on the
spelling alone, with no product defect behind any of them.

Ruling D2: normalise wherever a UID is resolved, through one shared resolver, and have the
reply name the spelling it resolved.

The order matters. As given first, so an exact match is never displaced by a guess; then the
two alternatives. Nothing here reaches a database: callers pass their own lookup.
"""
from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

PUB_SUFFIX = "-PUB"
# A publication suffix is -PUB or -PUB<n> (a sample published more than once). All of them name the same sample
# as the bare UID (operator ruling, 1 Oct 2026).
_PUB_RE = re.compile(r"-PUB\d*$", re.IGNORECASE)
# ponytail: a bare UID is also tried as -PUB1..-PUB9, because a lookup by exact string cannot enumerate; raise if a
# sample is ever published more than nine times.
MAX_PUB_NUMBER = 9


def uid_spellings(uid: str) -> list[str]:
    """Every spelling of ``uid`` worth trying, in order, without repeats.

    As given, then the UID without any ``-PUB`` or ``-PUB<n>`` suffix, then with ``-PUB``, then with ``-PUB1`` to
    ``-PUB<MAX_PUB_NUMBER>``. A numeric id or an empty string yields only itself: there is nothing to suffix.
    Repeats are judged without case, as SEEK's database compares.
    """
    text = str(uid or "").strip()
    if not text or text.isdigit():
        return [text] if text else []

    stem = _PUB_RE.sub("", text)
    out = [text]
    if stem:
        for spelling in [stem, stem + PUB_SUFFIX, *(f"{stem}{PUB_SUFFIX}{n}" for n in range(1, MAX_PUB_NUMBER + 1))]:
            if spelling.casefold() not in {o.casefold() for o in out}:
                out.append(spelling)
    return out


def resolve_uid_with_suffix(
    uid: str, lookup: Callable[[str], Optional[str]],
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve ``uid`` through ``lookup``, trying each spelling.

    Returns ``(resolved_id, spelling_that_resolved)``, or ``(None, None)``. The second value is
    what a reply names when it differs from what the user wrote; a caller that does not care
    can ignore it. ``lookup`` raising is treated as "not this spelling", because the per-service
    resolvers this replaces all swallowed their own exceptions.
    """
    for spelling in uid_spellings(uid):
        try:
            found = lookup(spelling)
        except Exception:
            found = None
        if found:
            return found, spelling
    return None, None
