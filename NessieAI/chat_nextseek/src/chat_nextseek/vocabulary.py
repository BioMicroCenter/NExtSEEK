"""The turn's vocabulary: one way to build it, for the NS turn, its pre-run and a Container-CC turn (2026-09-28).

``resolve_vocabulary`` is the NS turn's own prelude, moved here unchanged: the lexical shortlist of the catalogs
(``SHORTLIST_SAMPLETYPES`` sample types, ``SHORTLIST_ASSAYS`` assays), then the entity agent over that shortlist. Its
input is always the user's own question. The NS turn calls it (``orchestrator._turn_vocabulary``); so does the
pre-run that starts it as the question arrives, beside the router (``NessieAI/cc/prerun.py``), and the first
Container-CC op of a turn that finds the turn's vocabulary empty (``NessieAI/ns/granular.py``).

``session`` is part of the shared signature and is never read: the pre-run runs on a pool thread and must not touch
the live chat session.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from . import call_scope, turn_spend
from .agents import entity_agent
from .helpers import shortlist_catalog
from .schemas import EntityAgentOutput

__all__ = ["SHORTLIST_SAMPLETYPES", "SHORTLIST_ASSAYS", "resolve_vocabulary", "NS_WAIT_S", "VocabularySource", "take"]

SHORTLIST_SAMPLETYPES = 50
SHORTLIST_ASSAYS = 75


def resolve_vocabulary(session: Any, config: Any, query: str, *, diagnostics: dict[str, Any] | None = None,
                       on_shortlisted: Callable[[], None] | None = None,
                       entity: Callable[..., EntityAgentOutput] | None = None,
                       shortlist: Callable[..., tuple] | None = None) -> EntityAgentOutput:
    """The vocabulary of ``query``: the catalog shortlist, then the entity agent over it.

    ``diagnostics`` (when given) receives the shortlist's diagnostics, which the NS debug payload shows;
    ``on_shortlisted`` runs between the two steps (the NS turn sends its catalog and entity events there);
    ``entity`` and ``shortlist`` replace this module's agents (the orchestrator passes its own names, which its
    tests patch). ``session`` is not read.
    """
    del session  # the shared signature; see the module docstring
    shortlist = shortlist or shortlist_catalog
    entity = entity or entity_agent
    sampletypes_short, assays_short, shortlist_diag = shortlist(
        query,
        config.MIN_SAMPLETYPES or [],
        config.MIN_ASSAYS or [],
        k_st=SHORTLIST_SAMPLETYPES,
        k_a=SHORTLIST_ASSAYS,
        sampletype_index=getattr(config, "SAMPLETYPE_INDEX", None),
        assay_index=getattr(config, "ASSAY_INDEX", None),
        ratio=getattr(config, "SEMANTIC_RATIO", 0.7),
        min_k=getattr(config, "SEMANTIC_MIN_K", 10),
        max_k=getattr(config, "SEMANTIC_MAX_K", 80),
    )
    if not sampletypes_short:
        sampletypes_short = config.MIN_SAMPLETYPES or []
    if not assays_short:
        assays_short = config.MIN_ASSAYS or []
    if diagnostics is not None and isinstance(shortlist_diag, dict):
        diagnostics.update(shortlist_diag)
    if on_shortlisted is not None:
        on_shortlisted()
    t0 = time.perf_counter()
    result = entity(config, query, sampletypes_short, assays_short)
    print(f"[TIMING][ENTITY] {time.perf_counter() - t0:.2f}s")
    return result


#: How long an NS turn waits for a pre-run still running: above the entity agent's own wall clocks, so in practice
#: the pre-run has answered or failed long before.
NS_WAIT_S = 120.0


class VocabularySource(Protocol):
    """What an NS turn reads of its vocabulary pre-run (``NessieAI/cc/prerun.Prerun``)."""

    diagnostics: dict[str, Any]
    plan: dict[str, Any] | None
    strikes: list[list[str]]
    spend: Any

    def done(self) -> bool: ...

    def result(self, timeout_s: float) -> EntityAgentOutput | None: ...

    # Optional (the real Prerun has both; a source without them is read as never queued and never counted):
    # cancel() -> bool drops a pre-run still queued; note_resolved_in_turn() counts the turn's own resolution.


def take(source: VocabularySource | None) -> tuple[EntityAgentOutput, dict[str, Any], dict[str, Any] | None] | None:
    """The pre-run's vocabulary, its shortlist diagnostics and its early plan, or None to resolve it in the turn.

    Called in the turn thread, inside the turn's cost collector and call scope: the pre-run's spend becomes the
    turn's (partial when it is still running) and the models that failed in it are not asked again in the turn.
    A pre-run still queued (the pool was busy) is cancelled first, so the turn resolves the vocabulary now instead of
    waiting behind the queue; when this hands out nothing, the source is told the turn resolves it itself."""
    if source is None:
        return None
    cancel = getattr(source, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception as exc:  # noqa: BLE001 - then it is waited for as before
            print(f"[VOCAB] a queued pre-run could not be cancelled: {exc!r}")
    taken = _take(source)
    if taken is None:
        note = getattr(source, "note_resolved_in_turn", None)
        if callable(note):
            note()
    return taken


def _take(source: VocabularySource) -> tuple[EntityAgentOutput, dict[str, Any], dict[str, Any] | None] | None:
    try:
        out = source.result(NS_WAIT_S)
    except Exception as exc:  # noqa: BLE001 - the turn resolves it itself
        print(f"[VOCAB] the pre-run's result could not be read; resolving it in the turn: {exc!r}")
        out = None
    finished = bool(source.done())
    spend = turn_spend.current()
    if spend is not None:
        spend.absorb(getattr(source, "spend", None), finished=finished)
    scope = call_scope.current()
    if scope is not None and finished:
        scope.seed(getattr(source, "strikes", None) or [])
    if not isinstance(out, EntityAgentOutput):
        return None
    plan = getattr(source, "plan", None)
    return out, dict(getattr(source, "diagnostics", None) or {}), plan if isinstance(plan, dict) else None
