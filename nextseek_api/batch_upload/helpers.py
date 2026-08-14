"""Shared helpers for the batch upload pipeline."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from sqlalchemy import text

# UID format: optional single-letter dotted prefix (A./D./M./…), 2+ uppercase
# type letters, 6-digit date, 2-5 uppercase lab abbreviation, dash, index,
# optional -PUB suffix
UID_RE = re.compile(
    r"\A([A-Z]\.)?[A-Z]{2,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?\Z"
)

# Semicolons only — names may contain spaces, commas, hyphens
_PARENT_SPLIT_RE = re.compile(r";")


def split_parent_field(parent_raw: str) -> List[str]:
    """Split a Parent metadata field into individual tokens.

    Splits on semicolons only. Strips whitespace from each token.
    Returns only non-empty tokens.
    """
    return [t.strip() for t in _PARENT_SPLIT_RE.split(parent_raw.strip()) if t.strip()]


def collect_parent_tokens(meta: dict) -> List[str]:
    """Collect parent tokens from all keys containing 'parent' (case-insensitive).

    Scans all keys in the metadata dict. For each key whose name contains
    'parent' (case-insensitive), splits the value on semicolons using
    ``split_parent_field`` and collects the tokens.

    Returns a deduplicated list preserving first-seen order.
    """
    seen: set = set()
    result: List[str] = []
    for key, value in meta.items():
        if "parent" not in key.lower():
            continue
        if not value or not isinstance(value, str):
            continue
        for token in split_parent_field(value):
            if token not in seen:
                seen.add(token)
                result.append(token)
    return result


# ── Protocol → SOP resolution ─────────────────────────────────────────────
#
# A sample's ``Protocol`` metadata field names the SOP that produced it, and
# the DERIVED_FROM edge records that as protocol_id / protocol_title. There is
# exactly ONE definition of how a Protocol value maps to a SOP, and this is it:
# neo4j_sync (ingest), orphan_resolution (late-arriving parents) and models
# (sheet validation) all read it from here.
#
# Production stores three shapes, measured across 163,393 samples:
#
#   bare SOP title  (P.FOR-200623-V1_….docx)   97,767   ← the overwhelming case
#   internal /sops/<id> URL                     4,446
#   http URL of any kind                        4,510
#
# and ``sops.title`` is unique (553 rows, 553 distinct titles), so a title is
# an unambiguous key. Resolving by URL alone — which is what the ingest path
# used to do — therefore wrote a null protocol on nearly every upload. The
# three-format rule below was replayed against the live database and
# reproduced the stored protocol_id on 200,000 of 200,000 sampled existing
# DERIVED_FROM edges with zero disagreements. It is also the rule
# ``seek/dbtable_sample.py::__formatSopUIDLink`` already applies when it
# renders the protocol link on a sample page, ambiguity guard included.

_SOP_URL_RE = re.compile(r"/sops/(\d+)")
_SOP_UID_MARKER = "uid="

# Hosts that are always this machine, whatever the deployment.
_ALWAYS_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _hostname_of(raw: str) -> str:
    """Best-effort hostname from a URL, a ``host:port``, or a bare host."""
    value = (raw or "").strip()
    if not value:
        return ""
    try:
        if "//" in value:
            return (urlsplit(value).hostname or "").lower()
        # urlsplit only populates netloc after '//', so synthesise one; this
        # handles both "example.org" and "example.org:8000".
        return (urlsplit("//" + value).hostname or "").lower()
    except ValueError:  # malformed IPv6 literal, bad port, …
        return ""


def _local_url_hosts() -> Tuple[Set[str], Tuple[str, ...]]:
    """Hostnames that mean "a SOP id in this URL is one of OUR sops.id".

    Returns ``(exact_hosts, dotted_suffixes)``. Sources are this instance's own
    names: the SEEK base URLs (public and container-internal), the NExtSEEK
    hostname, and ``ALLOWED_HOSTS``.

    The PORT is deliberately not part of the comparison, unlike
    ``schema_rag.is_self_schema_url``. That check decides whether to serve our
    own document and a same-host-different-port service would be a different
    service; here the question is only whether an integer indexes our ``sops``
    table, and the same SEEK instance is reached on :3000 internally and :443
    publicly, with historical Protocol values written under both. Requiring an
    exact port would discard local ids we can resolve correctly.

    Every read is guarded: this module is imported by ``models``, which is
    imported in contexts where Django settings may be unconfigured.
    """
    exact: Set[str] = set(_ALWAYS_LOCAL_HOSTS)
    suffixes: List[str] = []

    try:
        from django.conf import settings
    except Exception:  # pragma: no cover - django is always installed here
        return exact, tuple(suffixes)

    for attr in ("SEEK_PUBLIC_URL", "SEEK_URL", "SEEK_HOSTNAME", "SERVER_IPADDRESS"):
        try:
            host = _hostname_of(str(getattr(settings, attr, "") or ""))
        except Exception:  # settings not configured
            continue
        if host:
            exact.add(host)

    try:
        allowed = list(getattr(settings, "ALLOWED_HOSTS", None) or [])
    except Exception:
        allowed = []
    for raw in allowed:
        entry = str(raw or "").strip().lower()
        if not entry or "*" in entry:
            # "*" allows everything, which would defeat the anchoring outright.
            continue
        if entry.startswith("."):
            # Django's subdomain wildcard: ".mit.edu" means any *.mit.edu.
            suffixes.append(entry)
            exact.add(entry[1:])
            continue
        host = _hostname_of(entry)
        if host:
            exact.add(host)

    return exact, tuple(suffixes)


def _local_sop_id_from_url(value: str) -> Optional[int]:
    """The SOP id in a ``/sops/<id>`` URL, but only if the URL is OURS.

    ``_SOP_URL_RE`` is unanchored, so before this guard existed
    ``https://fairdomhub.org/sops/795`` yielded 795, which was then looked up
    in the LOCAL ``sops`` table and stamped an unrelated protocol onto the
    edge. 1,855 live Protocol values are fairdomhub.org URLs. An external URL
    must resolve by title or not at all — never by a foreign integer.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return None

    if parts.scheme or parts.netloc:
        if parts.scheme.lower() not in ("http", "https"):
            return None
        try:
            host = (parts.hostname or "").lower()
        except ValueError:
            return None
        if not host:
            return None
        exact, suffixes = _local_url_hosts()
        if host not in exact and not any(host.endswith(s) for s in suffixes):
            return None
        path = parts.path
    else:
        # No scheme and no host: a site-relative path, so it is ours by
        # construction. This is the form production actually stores (4,446).
        path = value

    m = _SOP_URL_RE.search(path)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:  # pragma: no cover - the group is \d+
        return None


def parse_protocol_value(value: Any) -> Tuple[Optional[int], Optional[str]]:
    """Split a ``Protocol`` metadata value into a SOP id or a SOP title.

    Returns ``(sop_id, title)`` with at most one of them set:

    1. ``/sops/<id>`` on THIS instance  → ``(id, None)``   — id taken verbatim
    2. ``…/uid=<title>[/]``             → ``(None, title)`` — one trailing slash removed
    3. anything else                    → ``(None, value)`` — look it up by title

    Case 3 deliberately includes external URLs: they carry no id we can trust,
    and a title lookup on them simply finds nothing, which the caller then
    reports rather than silently dropping.

    ``(None, None)`` means the sample records no protocol at all — nothing to
    resolve and nothing to report.
    """
    s = "" if value is None else str(value).strip()
    if not s:
        return None, None

    sop_id = _local_sop_id_from_url(s)
    if sop_id is not None:
        return sop_id, None

    _before, marker, after = s.partition(_SOP_UID_MARKER)
    if marker:
        title = (after[:-1] if after.endswith("/") else after).strip()
        return (None, title) if title else (None, None)

    return None, s


def lookup_sop_ids_by_title(
    titles: Iterable[Optional[str]],
    sql_conn: Any,
    chunk_size: int = 500,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Bulk-resolve SOP titles to ``sops.id``.

    Returns ``(resolved, ambiguous)`` where ``resolved`` maps the title as it
    was asked for to its id, and ``ambiguous`` maps a title that matched more
    than one SOP to how many it matched. A title in neither dict matched
    nothing.

    The ``len(records) == 1`` requirement is
    ``dbtable_sample.__formatSopUIDLink``'s: production titles are unique
    (553/553), but guessing between two SOPs would silently mis-record an
    edge, and the caller can report the ambiguity instead.

    Matching is casefolded on the way back because MySQL's default collation
    is case-insensitive, so the row returned for a title can differ in case
    from the value queried.
    """
    wanted = {t.strip() for t in titles if t and str(t).strip()}
    if not wanted:
        return {}, {}

    ids_by_ci: Dict[str, Set[int]] = {}
    ordered = sorted(wanted)
    for start in range(0, len(ordered), chunk_size):
        chunk = ordered[start : start + chunk_size]
        params = {f"t_{i}": t for i, t in enumerate(chunk)}
        placeholders = ", ".join(f":t_{i}" for i in range(len(chunk)))
        sql = text(f"SELECT id, title FROM sops WHERE title IN ({placeholders})")
        for sop_id, title in sql_conn.execute(sql, params).fetchall():
            if title is None or sop_id is None:
                continue
            ids_by_ci.setdefault(str(title).strip().casefold(), set()).add(sop_id)

    resolved: Dict[str, int] = {}
    ambiguous: Dict[str, int] = {}
    for title in wanted:
        ids = ids_by_ci.get(title.casefold())
        if not ids:
            continue
        if len(ids) == 1:
            resolved[title] = next(iter(ids))
        else:
            ambiguous[title] = len(ids)
    return resolved, ambiguous
