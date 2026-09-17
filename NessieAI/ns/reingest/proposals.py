"""The attribute approval queue.

A raw pipeline key the agent could not map through a committed rule becomes a
row in ``ReingestAttributeProposal`` -- never a silently-invented attribute.
Recording is idempotent per (pipeline, raw_key), not per (pipeline, raw_key,
proposed_attribute): the agent proposing a mapping run to run is not
deterministic, so keying strictly on all three columns would let a later run
that guesses a different attribute for the same key fork a second row --
a human opening the queue would see the same unmapped key several times, as
several separate questions, rather than once with competing suggestions.

The dedupe policy, by status:

* No row yet for (pipeline, raw_key) -> a new row is created (``pending`` by
  default, or ``needs_definition`` when the caller says so).
* An open row exists (``pending`` or ``needs_definition`` -- nobody has ruled
  on it yet) -> that row absorbs the sighting, EVEN WHEN the newly proposed
  attribute differs from the one already on the row: ``times_proposed`` is
  bumped, ``last_seen_run``/``manifest_digest`` are refreshed, and ``status``
  is never touched. When the attribute differs, the alternative candidate is
  folded into ``rationale`` (deduplicated by candidate name) rather than
  forking a row, so a reviewer sees one gap with competing suggestions.
* Every row for (pipeline, raw_key) is terminal (``approved`` or
  ``rejected`` -- a human already ruled) and this sighting repeats the SAME
  attribute that row was ruled on -> the matching terminal row absorbs only
  the evidence (``times_proposed``, ``last_seen_run``, ``manifest_digest``).
  ``status`` is never reopened: repetition is evidence, not a veto over a
  human's ruling.
* Every row for (pipeline, raw_key) is terminal and this sighting proposes a
  genuinely DIFFERENT attribute than any of them were ruled on -> that is a
  new question, so a new row is created.

The three-column ``unique_together`` on the model stays the database
backstop; this module never relies on being able to fork
(pipeline, raw_key, proposed_attribute) rows the way an unqualified
``get_or_create`` on all three columns would.
"""
from __future__ import annotations

import logging

from NessieAI.ns.reingest import maps

log = logging.getLogger(__name__)


def _alt_marker(attribute: str) -> str:
    return f"[alt candidate: {attribute}]"


def _fold_alternative(rationale: str, entry: dict) -> str:
    """``rationale`` with ``entry``'s candidate attribute noted, once.

    Repeated sightings of the same alternative do not pile up duplicate
    lines: the marker is checked for first, so twelve runs proposing the
    same alternative still leave one note, not twelve.
    """
    attribute = entry.get("proposed_attribute", "")
    marker = _alt_marker(attribute)
    rationale = rationale or ""
    if marker in rationale:
        return rationale
    detail = str(entry.get("rationale") or "").strip()
    note = f"{marker} {detail}".strip()
    return f"{rationale}\n{note}" if rationale else note


def record(entries, *, pipeline, run_dir, manifest_digest, user_id):
    """Upsert one proposal row per entry, per the dedupe policy above.

    Returns the list of rows touched, one per entry in ``entries``, in order.
    """
    from django.db.models import F

    from nextseek_api.assistant.models_db import ReingestAttributeProposal as Proposal

    non_terminal_statuses = (Proposal.STATUS_PENDING, Proposal.STATUS_NEEDS_DEFINITION)
    terminal_statuses = (Proposal.STATUS_APPROVED, Proposal.STATUS_REJECTED)

    saved = []
    for entry in entries:
        raw_key = entry["raw_key"]
        proposed_attribute = entry["proposed_attribute"]

        existing = list(
            Proposal.objects.filter(pipeline=pipeline, raw_key=raw_key).order_by("pk")
        )
        open_row = next((row for row in existing if row.status in non_terminal_statuses), None)

        if open_row is not None:
            updates = {
                "times_proposed": F("times_proposed") + 1,
                "last_seen_run": run_dir,
                "manifest_digest": manifest_digest,
            }
            if open_row.proposed_attribute != proposed_attribute:
                updates["rationale"] = _fold_alternative(open_row.rationale, entry)
            Proposal.objects.filter(pk=open_row.pk).update(**updates)
            open_row.refresh_from_db()
            saved.append(open_row)
            continue

        terminal_match = next(
            (row for row in existing
             if row.status in terminal_statuses and row.proposed_attribute == proposed_attribute),
            None,
        )
        if terminal_match is not None:
            # A human already ruled on this exact attribute. Repetition is
            # evidence, not a veto: bump the count, never the status.
            Proposal.objects.filter(pk=terminal_match.pk).update(
                times_proposed=F("times_proposed") + 1,
                last_seen_run=run_dir,
                manifest_digest=manifest_digest,
            )
            terminal_match.refresh_from_db()
            saved.append(terminal_match)
            continue

        row = Proposal.objects.create(
            pipeline=pipeline,
            raw_key=raw_key,
            proposed_target=entry.get("proposed_target", ""),
            proposed_attribute=proposed_attribute,
            datatype=entry.get("datatype", "string"),
            example_value=str(entry.get("example_value", "")),
            source_file=entry.get("source_file", ""),
            rationale=entry.get("rationale", ""),
            status=entry.get("status", Proposal.STATUS_PENDING),
            first_seen_run=run_dir,
            last_seen_run=run_dir,
            manifest_digest=manifest_digest,
            proposed_by_id=user_id,
        )
        saved.append(row)
    return saved


def approved_rules(pipeline: str) -> dict[str, maps.AttributeRule]:
    """{attribute: AttributeRule} for rows a superuser approved."""
    from nextseek_api.assistant.models_db import ReingestAttributeProposal as Proposal

    out: dict[str, maps.AttributeRule] = {}
    for row in Proposal.objects.filter(pipeline=pipeline,
                                       status=Proposal.STATUS_APPROVED):
        out[row.proposed_attribute] = maps.AttributeRule(
            **{"from": row.raw_key, "target": row.proposed_target,
               "datatype": row.datatype,
               "provenance": f"approved {row.reviewed_at:%Y-%m-%d} "
                             f"by {getattr(row.reviewed_by, 'username', 'unknown')}"
                             if row.reviewed_at else "approved"})
    return out


def attribute_exists(sample_type: str, attribute: str) -> bool:
    """True when ``attribute`` is already defined on ``sample_type``.

    Reingest never creates an attribute -- definitions belong to the native
    Attribute API. A False here means the value gets parked in Notes and a
    needs_definition row is queued for superusers, so it must mean a genuine
    "not defined on this sample type", never an infrastructure hiccup.

    Deliberately no try/except: `attributes_for` (via
    `context_catalog.load_sample_type`/`load_sample_types`) already returns
    `[]` for a sample type the catalog does not know, so a real "not defined"
    answer needs no exception handling here at all. Anything that DOES raise
    out of that call is an outage, not a schema fact, and must propagate to
    the caller rather than being reported as `False` -- the same rule
    `reingest_lookups.notes_for_uids` follows by omitting a UID whose fetch
    failed instead of pretending it has no Notes.
    """
    from nextseek_api.services.reingest_lookups import attributes_for

    return attribute in {a["title"] for a in attributes_for(sample_type)}
