"""Does a UID the user named exist in the graph, and under which spelling?

Pilot A v2 (2026-09-18) asked about ``TIS-230830ENG-1-PUB`` and ``NHP-220830FLY-42-PUB``.
The local graph stores no UID with a -PUB suffix (0 of 1,084,754), while
``TIS-230830ENG-1`` exists with 938 direct children. Both turns queried the -PUB spelling,
matched nothing, and one reply said "0 samples are directly derived": a confident answer
about a sample the query never found. The graph agent could not tell "absent" from
"childless", and the zero-row retry only lower-cased the same UID.

Other graphs have stored the suffix (the 2026-07-24 run returned
``D.SEQ-220823SHA-1..6-PUB``), so the check goes both ways: a -PUB UID is also tried
without the suffix, and a bare UID is also tried with one. One read-only query covers
every UID of the turn. The graph agent is told the stored spelling before it writes a
query, and the reply is told what was found, so a zero about a missing UID reads as "not
found".

Nothing here changes what is stored; a failed check says nothing either way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

#: A NExtSEEK UID: <TYPE>-<YYMMDD><LAB>-<n>, optionally -PUB<n>. The same grammar the parser
#: checks (``agents/parser.py`` ``_WELL_FORMED_UID_RE``), matched case-insensitively here
#: because users type them in any case.
UID_RE = re.compile(r"\b[A-Z][A-Z.]{1,6}-\d{6}[A-Z]{3}-\d+(?:-PUB\d*)?\b", re.IGNORECASE)
_PUB_SUFFIX = re.compile(r"-PUB\d*$", re.IGNORECASE)

#: Written in the subset of Cypher the graph scope prover (``cypher_scope.scope_cypher``) can prove, so a
#: caller who is not a superuser gets the check with a project clause on every Sample node instead of a
#: refusal: no COLLECT or CALL subquery. A sample outside the caller's projects is then "not found",
#: which tells them nothing about other projects. The suffixed prefix is computed before the last MATCH, so
#: its WHERE compares the property with a plain name: the prover leaves that outside its guard and Neo4j
#: seeks the uuid index instead of scanning every sample for each UID.
CHECK_CYPHER = (
    "UNWIND $checks AS c\n"
    "WITH c, c.base + '-PUB' AS pub\n"
    "OPTIONAL MATCH (a:Sample {uuid: c.uid})\n"
    "OPTIONAL MATCH (b:Sample {uuid: c.base})\n"
    "OPTIONAL MATCH (p:Sample) WHERE p.uuid STARTS WITH pub\n"
    "RETURN c.uid AS uid, count(a) > 0 AS exact, head(collect(DISTINCT b.uuid)) AS base_uuid,\n"
    "       collect(DISTINCT p.uuid)[..2] AS suffixed"
)


@dataclass(frozen=True)
class UidCheck:
    """A UID as the user wrote it, and as the graph stores it (None when absent)."""

    asked: str
    stored: str | None


def uids_in(text: str, filter_uids: Iterable[str] | None = None) -> list[str]:
    """Every UID of the turn, upper-cased, once each: the parser's filters first, then any
    well-formed UID in the question the parser did not copy."""
    out: list[str] = []
    for uid in list(filter_uids or []) + UID_RE.findall(text or ""):
        value = str(uid).strip().upper()
        if value and UID_RE.fullmatch(value) and value not in out:
            out.append(value)
    return out


def check_uids(config: Any, uids: list[str], *, run: Callable[..., dict]) -> list[UidCheck] | None:
    """One read-only query; ``run`` is ``tool_neo4j_query``, so the caller's graph scope applies. None when the
    query failed or was refused, which claims nothing either way."""
    if not uids:
        return []
    checks = [{"uid": uid, "base": _PUB_SUFFIX.sub("", uid)} for uid in uids]
    result = run(config, CHECK_CYPHER, {"checks": checks})
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    rows = {str(row.get("uid")): row for row in result.get("data") or [] if isinstance(row, dict)}
    out: list[UidCheck] = []
    for check in checks:
        row = rows.get(check["uid"])
        if row is None:
            return None
        if row.get("exact"):
            stored = check["uid"]
        elif row.get("base_uuid") and check["base"] != check["uid"]:
            stored = str(row["base_uuid"])
        elif len(row.get("suffixed") or []) == 1:
            stored = str(row["suffixed"][0])
        else:
            stored = None
        out.append(UidCheck(asked=check["uid"], stored=stored))
    return out


def uid_notes(checks: list[UidCheck] | None) -> tuple[str | None, list[str]]:
    """``(note for the graph agent, notes for the reply)``; nothing when every UID was found
    as written or the check could not run."""
    if not checks:
        return None, []
    agent: list[str] = []
    reply: list[str] = []
    for check in checks:
        if check.stored == check.asked:
            continue
        if check.stored:
            agent.append(
                f"UID {check.asked} is not in the graph under that spelling; the same sample is stored as "
                f"{check.stored}, so use {check.stored} in the query."
            )
            reply.append(
                f"The user wrote {check.asked}; the graph stores that sample as {check.stored}, and the "
                f"answer uses {check.stored}. Say so in one short clause."
            )
        else:
            agent.append(
                f"UID {check.asked} was not found, with or without a -PUB suffix, among the samples this "
                "search can see. Do not describe it as a sample with no parents or no children."
            )
            reply.append(
                f"{check.asked} was not found among the samples the user can see (checked with and without "
                "a -PUB suffix). Say plainly that this UID was not found; a zero in the result means the UID "
                "is absent, not that it has no parents or children."
            )
    if not agent:
        return None, []
    return "UID CHECK (run before your query):\n" + "\n".join(f"- {line}" for line in agent), reply
