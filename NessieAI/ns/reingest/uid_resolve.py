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
    """
    from nextseek_api.assistant.models_db import PipelineRun

    record = PipelineRun.objects.filter(run_dir=run_dir).first()
    # Assumes every row carries a `sample` key, as a real samplesheet always
    # does -- two rows both missing it would collapse to the same "" key and
    # be flagged multi-run together.
    multi = {s for s, n in Counter(
        str(r.get("sample") or "") for r in samplesheet_rows).items() if n > 1}

    multirun_parents: dict[str, tuple[str, ...]] = {
        sample: _resolve_multirun_parents(
            sample,
            [r for r in samplesheet_rows if str(r.get("sample") or "") == sample],
            record, lookup_by_fastq)
        for sample in multi
    }

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
) -> tuple[str, ...]:
    """The D.SEQ UIDs behind a multi-run sample's OWN contributing rows.

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
    """
    seen: set[str] = set()
    parents: list[str] = []
    for row in rows:
        uid = _resolve_multirun_row(sample, row, record, lookup_by_fastq)
        if uid and uid not in seen:
            seen.add(uid)
            parents.append(uid)
    return tuple(parents)


def _resolve_multirun_row(
    sample: str, row: dict, record, lookup_by_fastq: Callable[[str], list[str]],
) -> str | None:
    """One contributing row's own D.SEQ UID, or None -- see
    `_resolve_multirun_parents` for why this cannot reuse `record.uid_for`.
    """
    fastq = str(row.get("fastq_1") or "")
    if record is not None and fastq:
        matches = {entry.get("d_seq_uid") for entry in (record.cohort or [])
                   if entry.get("nfcore_sample") == sample
                   and entry.get("fastq_1") == fastq
                   and entry.get("d_seq_uid")}
        if len(matches) == 1:
            return next(iter(matches))
        if len(matches) > 1:
            return None
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
