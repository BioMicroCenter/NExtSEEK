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

from typing import Callable, Optional, Tuple

PUB_SUFFIX = "-PUB"


def uid_spellings(uid: str) -> list[str]:
    """Every spelling of ``uid`` worth trying, in order, without repeats.

    As given, then the same UID with ``-PUB`` removed, then with ``-PUB`` added. A numeric id
    or an empty string yields only itself: there is nothing to suffix.
    """
    text = str(uid or "").strip()
    if not text or text.isdigit():
        return [text] if text else []

    out = [text]
    upper = text.upper()
    if upper.endswith(PUB_SUFFIX):
        stripped = text[: -len(PUB_SUFFIX)]
        if stripped and stripped not in out:
            out.append(stripped)
    else:
        suffixed = text + PUB_SUFFIX
        if suffixed not in out:
            out.append(suffixed)
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
