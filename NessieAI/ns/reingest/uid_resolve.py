"""Resolve each nf-core sample back to the D.SEQ (or already-analysed A.*)
record it came from.

Order: the launch record, then a path match against every path-bearing
column the row actually carries (see `_PATH_COLUMNS`), tried exact-then-
basename within each column. Two candidates -- whether from one column or
from two DIFFERENT columns naming two different real parents -- is never
resolved to one of them: picking a parent by coin flip writes a wrong
lineage into a scientific database of record. See `_resolve_row_path` for
exactly how columns are combined and why `fastq_2` is not tried the same way
the others are.

A launch record that lists the sample in its cohort but never got a UID for
it (`PipelineRun.knows_sample` True, `uid_for` None) is reported unresolved
directly, without trying the path fallback: the launch already established
there is nothing in NExtSEEK to match against, so retrying by path would only
manufacture a same-run coincidence, not a real answer. The path fallback is
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

# Every path-bearing INPUT column across the ten committed pipeline maps'
# pinned samplesheet schemas -- cross-checked two ways: the fixture schemas
# NessieAI/tests/chat_nextseek/fixtures/nfcore/*.schema_input.json
# (hlatyping, rnafusion, rnaseq, rnasplice, scrnaseq, smrnaseq) and, for the
# four maps with no fixture file (denovotranscript, differentialabundance,
# riboseq, rnavar), their `all_cols` entries in
# docs/nfcore-schema-census-2026-08-05.json. No pinned schema among the ten
# uses any other path-shaped column: rnafusion/rnavar's own "bai"/"crai"/
# "tbi"/"junctions"/"splice_junctions" columns are companion index or
# annotation files, never a parent's own primary-data path, so they are
# deliberately excluded here.
#
# Order matters -- see `_resolve_row_path` for how it is used. "fastq_1"
# first (the original, most common case); "fastq_2" is NOT tried the same
# way as the rest (see that function); then "bam", "cram", "vcf" in the
# order hlatyping (bam), rnafusion/rnavar (bam, cram) and rnavar (vcf) were
# reviewed.
_PATH_COLUMNS: tuple[str, ...] = ("fastq_1", "fastq_2", "bam", "cram", "vcf")


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
        uid, resolution = _resolve_row_path(row, lookup_by_fastq)
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
    """One contributing row's own D.SEQ (or already-analysed A.*) UID, or
    None -- see `_resolve_multirun_parents` for why this cannot reuse
    `record.uid_for`.

    Cohort entries are matched on the ``(nfcore_sample, fastq_1)`` pair, not
    on the UID: if the launch record has ANY entry for this exact pair, it is
    authoritative for this row -- resolved or not -- exactly the guarantee a
    single-run sample gets from `resolve`'s own `knows_sample`/`uid_for`
    check. A matching entry with `d_seq_uid: None` means the launch already
    established there is nothing in NExtSEEK to match this row against, so
    the path fallback below is never tried for it: retrying by path would
    only manufacture a same-run coincidence, not a real answer (see this
    module's docstring). Only when NO cohort entry matches the pair at all --
    the launch record has nothing to say about this row -- does the path
    fallback run.

    The pair is deliberately still keyed on ``fastq_1``, never on whichever
    column this row's OWN path actually lives in: `record_launch`'s cohort
    entries (built by the emitter's `_write_cohort_sidecar`, which pops
    `fastq_1`/`fastq_2` for an alignment-input row and writes an already-
    analysed row's path into `mapped`/`bam` instead) carry ONLY
    `fastq_1`/`fastq_2` keys, for every pipeline, fastq-input or not -- there
    is no `bam`/`cram`/`vcf` key a cohort entry could ever be matched on. A
    bam-column row's own `fastq_1` is therefore always ``""``, so this
    lookup finds nothing to key on and correctly falls straight through to
    the path fallback below, which DOES search every populated column (see
    `_resolve_row_path`). Concretely: a cohort entry can match a row on
    `fastq_1`/`fastq_2` identity only -- it can never confirm or refute a
    `bam`/`cram`/`vcf` row's parent, launched by Nessie or not.
    """
    fastq = str(row.get("fastq_1") or "")
    if record is not None and fastq:
        entries = [entry for entry in (record.cohort or [])
                   if entry.get("nfcore_sample") == sample
                   and entry.get("fastq_1") == fastq]
        if entries:
            uids = {entry.get("d_seq_uid") for entry in entries if entry.get("d_seq_uid")}
            return next(iter(uids)) if len(uids) == 1 else None
    uid, _resolution = _resolve_row_path(row, lookup_by_fastq)
    return uid


def _resolve_row_path(
    row: dict, lookup: Callable[[str], list[str]],
) -> tuple[str | None, str]:
    """One row's parent UID, searched across every `_PATH_COLUMNS` column it
    actually has populated -- not just `fastq_1`.

    Each populated column is resolved independently, exact-then-basename,
    exactly as `_by_path` always has for a single value. The results are
    then combined by the rule this module lives by -- never guess between
    candidate parents:
      - any column that is itself internally ambiguous (two candidates for
        one path) makes the WHOLE row ambiguous; a real conflict inside one
        column is not something a clean match in another column gets to
        override.
      - otherwise, if the columns that did resolve agree on exactly one
        distinct UID, that is the parent (two columns naming the SAME
        record -- e.g. `fastq_1` and `bam` both pointing at the row's own
        source -- collapse to one match, not two).
      - if they resolve to MORE THAN ONE distinct UID, that is a row naming
        two different real parents: `RESOLUTION_AMBIGUOUS`, no parent.
      - if nothing resolves, `RESOLUTION_UNRESOLVED`, exactly as before this
        change.
    When more than one column contributes the same winning UID, the
    reported resolution strength (`RESOLUTION_FASTQ_EXACT` vs
    `RESOLUTION_FASTQ_BASENAME`) is whichever column found it first in
    `_PATH_COLUMNS` order -- every contributing column already agrees on
    identity, so only the reported strength can differ, and the first
    column tried is the one this function reports for.

    `fastq_2` is handled differently from every other column: a paired-end
    row's `fastq_2` almost always names the SAME D.SEQ record as its
    `fastq_1`, so trying it in parallel on every ordinary row would double
    this function's lookup cost for no information gain on the common case.
    `bam`/`cram`/`vcf`, by contrast, are mutually exclusive with `fastq_1` in
    every pinned schema that has them (a row is fastq-input or already-
    analysed-input, never both) -- always trying them costs at most one
    extra lookup on a row that already resolved via `fastq_1`. So `fastq_2`
    is consulted only when `fastq_1` gave NOTHING to work with at all --
    empty, or a genuine `RESOLUTION_UNRESOLVED` -- which is exactly the case
    where `fastq_1` having been renamed on disk (so its own path lookup
    misses) would otherwise lose a real, findable parent for no reason. A
    `fastq_1` that came back internally ambiguous is a real signal, not
    nothing, so `fastq_2` is not consulted then either.
    """
    found: dict[str, str] = {}          # uid -> resolution that first found it
    fastq_1_gave_nothing = True

    for column in _PATH_COLUMNS:
        if column == "fastq_2" and not fastq_1_gave_nothing:
            # See the docstring: fastq_2 is a fallback for a renamed
            # fastq_1, not a second opinion on a fastq_1 that already spoke.
            continue
        value = str(row.get(column) or "")
        if not value:
            continue
        uid, resolution = _by_path(value, lookup)
        if resolution == manifest.RESOLUTION_AMBIGUOUS:
            # A real conflict inside one column is not something a clean
            # match in another column gets to override.
            return None, manifest.RESOLUTION_AMBIGUOUS
        if column == "fastq_1":
            fastq_1_gave_nothing = uid is None
        if uid is not None and uid not in found:
            found[uid] = resolution

    if not found:
        return None, manifest.RESOLUTION_UNRESOLVED
    if len(found) > 1:
        # The row names two different real parents. Refuse, never pick.
        return None, manifest.RESOLUTION_AMBIGUOUS
    uid, resolution = next(iter(found.items()))
    return uid, resolution


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
