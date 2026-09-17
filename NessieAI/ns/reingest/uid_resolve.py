"""Resolve each nf-core sample back to the D.SEQ record it came from.

Order: the launch record, then an exact fastq path match, then a basename
match. Two candidates is never resolved to one of them -- picking a parent by
coin flip writes a wrong lineage into a scientific database of record.

A launch record that lists the sample in its cohort but never got a UID for
it (`PipelineRun.knows_sample` True, `uid_for` None) is reported unresolved
directly, without trying the fastq fallback: the launch already established
there is nothing in NExtSEEK to match against, so retrying by path would only
manufacture a same-run coincidence, not a real answer. The fastq fallback is
tried only when the sample is absent from the cohort altogether -- or there is
no launch record for this run_dir at all, which happens for a run launched
outside Nessie.

A multi-run sample (several samplesheet rows sharing one `sample` value) is
marked MULTIRUN and left unresolved -- as a SINGLE parent -- on purpose,
regardless of anything above. nf-core concatenates those reads before QC, so
the single figure it reports cannot honestly be attributed to any one
contributing D.SEQ. This is a measurement limit, not an identity one:
analysis children can still cite every contributing D.SEQ as `Parent` (see
the fourth element `resolve()` returns, below), but the QC backfill cannot
pick one.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Callable

from NessieAI.ns.reingest import manifest


def resolve(
    samplesheet_rows: list[dict],
    run_dir: str,
    lookup_by_fastq: Callable[[str], list[str]],
    *,
    multirun_resolved_rows: dict[str, int] | None = None,
) -> list[tuple[str, str | None, str, tuple[str, ...]]]:
    """One (sample, uid_or_None, resolution, multirun_parents) per
    samplesheet row, input order.

    `multirun_parents` is empty on every row except a RESOLUTION_MULTIRUN
    one, where it carries the D.SEQ UIDs recovered for that SAME sample's own
    contributing rows (see `_resolve_multirun_parents`) -- first-occurrence
    de-duplicated, in the rows' own order. It is computed once per multi-run
    sample name and is identical on every row that shares that name, so a
    caller may read it off any one of them. It is empty (not partial-looking,
    just empty) when none of the sample's contributing rows resolved --
    exactly like today's behaviour before this field existed.

    `multirun_resolved_rows`, when given, is filled in as a side effect with
    ``{sample: resolved_row_count}`` for every multi-run sample -- the number
    of that sample's OWN contributing rows that resolved to a UID, counted
    BEFORE de-duplication. This is deliberately not folded into the returned
    per-row tuples: `multirun_parents` is a de-duplicated set of UIDs, so two
    rows resolving to the SAME UID (one D.SEQ record whose `File_PrimaryData`
    lists both lanes, say) collapses `len(multirun_parents)` below the row
    count even though every row resolved. `harvest.py` needs the un-collapsed
    row count, not the UID count, to tell that genuinely-complete case apart
    from an actually-partial one -- see its partial-resolution warning.
    """
    from nextseek_api.assistant.models_db import PipelineRun

    record = PipelineRun.objects.filter(run_dir=run_dir).first()
    # Assumes every row carries a `sample` key, as a real samplesheet always
    # does -- two rows both missing it would collapse to the same "" key and
    # be flagged multi-run together.
    multi = {s for s, n in Counter(
        str(r.get("sample") or "") for r in samplesheet_rows).items() if n > 1}

    multirun_parents: dict[str, tuple[str, ...]] = {}
    for sample in multi:
        rows_for_sample = [r for r in samplesheet_rows
                            if str(r.get("sample") or "") == sample]
        parents, resolved_rows = _resolve_multirun_parents(
            sample, rows_for_sample, record, lookup_by_fastq)
        multirun_parents[sample] = parents
        if multirun_resolved_rows is not None:
            multirun_resolved_rows[sample] = resolved_rows

    out: list[tuple[str, str | None, str, tuple[str, ...]]] = []
    for row in samplesheet_rows:
        sample = str(row.get("sample") or "")
        if sample in multi:
            out.append((sample, None, manifest.RESOLUTION_MULTIRUN,
                        multirun_parents[sample]))
            continue
        if record is not None and record.knows_sample(sample):
            # The run's cohort has an entry for this sample: the launch
            # record is authoritative, resolved or not. Never fall through
            # to the fastq path here -- see module docstring.
            uid = record.uid_for(sample)
            if uid:
                out.append((sample, uid, manifest.RESOLUTION_LAUNCH_RECORD, ()))
            else:
                out.append((sample, None, manifest.RESOLUTION_UNRESOLVED, ()))
            continue
        fastq = str(row.get("fastq_1") or "")
        uid, resolution = _by_path(fastq, lookup_by_fastq)
        out.append((sample, uid, resolution, ()))
    return out


def _resolve_multirun_parents(
    sample: str, rows: list[dict], record,
    lookup_by_fastq: Callable[[str], list[str]],
) -> tuple[tuple[str, ...], int]:
    """The D.SEQ UIDs behind a multi-run sample's OWN contributing rows, and
    how many of those rows resolved.

    Each row is resolved independently, by the same order of authority a
    single-run sample gets -- the launch record first, then an exact fastq
    match, then a basename match (`_resolve_multirun_row`) -- except the
    launch-record step cannot key off `nfcore_sample` the way
    `PipelineRun.uid_for`/`knows_sample` do: every contributing row shares
    that same sample name, so a name-keyed lookup cannot tell them apart and
    would return the same single cohort entry for all of them. Cohort entries
    are matched by this row's own `fastq_1` instead, which -- unlike the
    sample name -- is unique per row.

    A row that resolves ambiguously (two candidates, from the cohort or the
    fastq fallback) or not at all contributes nothing: fabricating a parent
    for it, or dropping the whole sample's lineage because of it, would
    misrepresent lineage either way. This makes the returned tuple a real,
    honest partial list when only some rows resolve, first-occurrence
    de-duplicated and in the rows' own order -- `harvest.py` is the layer
    that notices and warns when the result is partial, since only it knows
    how many contributing rows there were to compare against.

    The second element, `resolved_row_count`, counts every row whose OWN uid
    resolved to something -- BEFORE de-duplication, so two rows that resolve
    to the SAME uid (one D.SEQ record whose `File_PrimaryData` lists both
    lanes) both count, even though they collapse to one entry in the parents
    tuple. Comparing THAT count against the raw row count, not
    `len(parents)`, is what lets `harvest.py` tell a genuinely partial parent
    list apart from a complete one that merely de-duplicated.
    """
    seen: set[str] = set()
    parents: list[str] = []
    resolved_rows = 0
    for row in rows:
        uid = _resolve_multirun_row(sample, row, record, lookup_by_fastq)
        if uid:
            resolved_rows += 1
            if uid not in seen:
                seen.add(uid)
                parents.append(uid)
    return tuple(parents), resolved_rows


def _resolve_multirun_row(
    sample: str, row: dict, record, lookup_by_fastq: Callable[[str], list[str]],
) -> str | None:
    """One contributing row's own D.SEQ UID, or None -- see
    `_resolve_multirun_parents` for why this cannot reuse `record.uid_for`.

    Cohort entries are matched on the ``(nfcore_sample, fastq_1)`` pair, not
    on the UID: if the launch record has ANY entry for this exact pair, it is
    authoritative for this row -- resolved or not -- exactly the guarantee a
    single-run sample gets from `resolve`'s own `knows_sample`/`uid_for`
    check. A matching entry with `d_seq_uid: None` means the launch already
    established there is nothing in NExtSEEK to match this row against, so
    the fastq fallback below is never tried for it: retrying by path would
    only manufacture a same-run coincidence, not a real answer (see this
    module's docstring). Only when NO cohort entry matches the pair at all --
    the launch record has nothing to say about this row -- does the fastq
    fallback run.
    """
    fastq = str(row.get("fastq_1") or "")
    if record is not None and fastq:
        entries = [entry for entry in (record.cohort or [])
                   if entry.get("nfcore_sample") == sample
                   and entry.get("fastq_1") == fastq]
        if entries:
            uids = {entry.get("d_seq_uid") for entry in entries if entry.get("d_seq_uid")}
            return next(iter(uids)) if len(uids) == 1 else None
    uid, _resolution = _by_path(fastq, lookup_by_fastq)
    return uid


def _by_path(fastq: str, lookup: Callable[[str], list[str]]) -> tuple[str | None, str]:
    if not fastq:
        return None, manifest.RESOLUTION_UNRESOLVED
    exact = lookup(fastq)
    if len(exact) == 1:
        return exact[0], manifest.RESOLUTION_FASTQ_EXACT
    if len(exact) > 1:
        return None, manifest.RESOLUTION_AMBIGUOUS
    base = lookup(os.path.basename(fastq))
    if len(base) == 1:
        return base[0], manifest.RESOLUTION_FASTQ_BASENAME
    if len(base) > 1:
        return None, manifest.RESOLUTION_AMBIGUOUS
    return None, manifest.RESOLUTION_UNRESOLVED
