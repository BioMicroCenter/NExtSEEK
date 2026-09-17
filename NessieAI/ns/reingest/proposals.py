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

Concurrency: two reingest runs recording the same (pipeline, raw_key) at the
same time is ordinary -- two users reingesting different runs of the same
pipeline share raw keys. Each entry's read-then-write is wrapped in its own
``transaction.atomic()`` with ``select_for_update()`` on the existing rows for
that key, so the fold-into-open-row and bump-terminal-row paths above are
consistent under concurrency on a backend that honours row/gap locking
(MySQL/InnoDB): a second transaction's locked read blocks until the first
commits, then sees the row the first just wrote and folds into it rather than
forking. On sqlite (used by the test settings), ``select_for_update()`` is a
documented no-op -- Django's compiler only emits ``FOR UPDATE`` when
``connection.features.has_select_for_update`` is true, which sqlite reports as
``False``, so the call neither locks anything nor raises. The create path is
therefore still reachable there (and as a backend-independent belt-and-braces
path even under MySQL, where isolation-level or lock-timing edge cases could
still let two creates race): a lost race raises ``IntegrityError`` off the
model's ``unique_together``, which this module catches and folds into the row
that won, bumping its evidence instead of propagating a 500 to the loser.
"""
from __future__ import annotations

from NessieAI.ns.reingest import maps


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
    Each entry's read-then-write is one locked transaction; see the module
    docstring's "Concurrency" section for what that does and does not
    guarantee on each backend.
    """
    from django.db import IntegrityError, transaction
    from django.db.models import F

    from nextseek_api.assistant.models_db import ReingestAttributeProposal as Proposal

    non_terminal_statuses = (Proposal.STATUS_PENDING, Proposal.STATUS_NEEDS_DEFINITION)
    terminal_statuses = (Proposal.STATUS_APPROVED, Proposal.STATUS_REJECTED)

    saved = []
    for entry in entries:
        raw_key = entry["raw_key"]
        proposed_attribute = entry["proposed_attribute"]

        with transaction.atomic():
            # select_for_update() locks the rows this entry is about to read
            # and act on, for the life of this transaction. On MySQL/InnoDB
            # this also gap-locks the (pipeline, raw_key) index range when no
            # row exists yet, so a concurrent transaction's read for the same
            # key blocks here until this one commits -- see the module
            # docstring. On sqlite it is a documented no-op (no lock, no
            # error), which is why the create path below still needs its own
            # IntegrityError handling.
            existing = list(
                Proposal.objects.select_for_update()
                .filter(pipeline=pipeline, raw_key=raw_key)
                .order_by("pk")
            )
            open_row = next(
                (row for row in existing if row.status in non_terminal_statuses), None
            )

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
                 if row.status in terminal_statuses
                 and row.proposed_attribute == proposed_attribute),
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

            try:
                # Nested atomic() opens a savepoint (we are already inside
                # the outer atomic() above), so a caught IntegrityError rolls
                # back only this insert, not the whole entry's transaction --
                # the documented Django pattern for handling an expected
                # constraint failure without poisoning the transaction.
                with transaction.atomic():
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
            except IntegrityError:
                # Lost the race: another transaction inserted this exact
                # (pipeline, raw_key, proposed_attribute) row between our
                # locked read and our create. Fold into the row that won
                # instead of propagating -- the loser bumps evidence, same as
                # the terminal-match path above.
                row = Proposal.objects.get(
                    pipeline=pipeline, raw_key=raw_key,
                    proposed_attribute=proposed_attribute,
                )
                Proposal.objects.filter(pk=row.pk).update(
                    times_proposed=F("times_proposed") + 1,
                    last_seen_run=run_dir,
                    manifest_digest=manifest_digest,
                )
                row.refresh_from_db()
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

    `attributes_for` (via `context_catalog.load_sample_type`/
    `load_sample_types`) already returns `[]` for a sample type the catalog
    does not know, so a real "not defined" answer needs no exception
    handling for THAT case. But `load_sample_types` also catches Exception
    and returns `[]` when the sample-type table itself is unreachable (its
    own house rule, shared by every caller of that module -- changing it is
    a repo-wide decision, tracked separately, not something to "simplify
    away" here). That means the single most likely infrastructure failure --
    the database being down -- would otherwise arrive here indistinguishable
    from a genuinely unknown sample type, and get reported as `False`: a
    fabricated schema gap. `known_sample_types()` gives this module a way to
    tell the two apart without reaching past `reingest_lookups` into
    `context_catalog` directly (see the NessieAI/nextseek_api boundary
    allowlist): a populated NExtSEEK always has at least one sample type, so
    an ENTIRELY empty catalog is an outage signal, not an answer, and must
    raise. A genuinely unknown sample type in a populated catalog still
    returns `False`, same as before -- the same rule
    `reingest_lookups.notes_for_uids` follows by omitting a UID whose fetch
    failed instead of pretending it has no Notes.
    """
    from nextseek_api.services.reingest_lookups import attributes_for, known_sample_types

    if not known_sample_types():
        raise RuntimeError(
            "sample type catalog came back empty; treating this as an "
            "outage rather than reporting a fabricated schema gap for "
            f"{attribute!r} on {sample_type!r}"
        )
    return attribute in {a["title"] for a in attributes_for(sample_type)}
