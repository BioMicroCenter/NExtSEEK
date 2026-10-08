"""Generic "@" tokens, available to every pipeline map. Each resolver takes
(run_manifest, sample, context) and returns a string or None, never a
placeholder: a blank cell a curator fills beats a fabricated one."""
from __future__ import annotations

import fnmatch
import os
import re

from NessieAI.ns.reingest.maps import expand_braces as _expand_braces


_FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")


def _computed_fastq_stem(run_manifest, sample, context) -> str | None:
    """"SAMPLE01_R1" from ".../SAMPLE01_R1.fastq.gz" -- the read-1 basename, no suffix.

    Read 1 specifically: it is the one column every nf-core samplesheet has
    (fastq_2 is empty for single-end), so it is the only basename guaranteed to
    exist for every sample.
    """
    if sample is None:
        return None
    raw = str(getattr(sample, "fastq_1", "") or "")
    if not raw:
        return None
    name = os.path.basename(raw)
    for suffix in _FASTQ_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)] or None
    stem = os.path.splitext(name)[0]
    return stem or None


def _computed_run_date(run_manifest, sample, context) -> str | None:
    """"2026-01-26" -- the day the pipeline ran.

    From ``params.trace_report_suffix`` ("2026-01-26_13-53-17"), which nf-core
    fixes at launch. The date half only: the time is deliberately dropped.

    Validated against the exact YYYY-MM-DD shape rather than just split on "_",
    so a future nf-core that changes this key's format yields None (a blank
    cell) instead of a plausible-looking wrong date.
    """
    raw = str((run_manifest.params or {}).get("trace_report_suffix") or "")
    date = raw.split("_")[0]
    return date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None


def _computed_gex_name(run_manifest, sample, context) -> str | None:
    """"gex_2026-01-26_1" -- the per_run expression row's name.

    A.GEX is one row for the whole run, so it cannot inherit a parent's name
    the way a per_sample row can: it has as many parents as the run had
    samples. This names it by what it is, when it was made, and a running
    counter -- two runs on one day would otherwise collide.

    The counter needs the catalog, which this module deliberately never
    reaches: `next_name_ordinal` is INJECTED through `context` by
    `granular.py`, the same dependency-injection shape `harvest.py` uses for
    `lookup_by_fastq`, so `mapper` stays a pure function of manifest + map.

    Without that callable there is no way to number the row, so this returns
    None and the cell is blank. Deliberately NOT an unnumbered "gex_<date>":
    that is the exact name a second run of the day would also produce, and a
    blank a curator fills is better than two samples quietly sharing a Name.
    """
    date = _computed_run_date(run_manifest, sample, context)
    if not date:
        return None
    prefix = f"gex_{date}"
    next_ordinal = (context or {}).get("next_name_ordinal")
    if not callable(next_ordinal):
        return None
    return f"{prefix}_{next_ordinal('A.GEX', prefix)}"


def _computed_nextseek_user(run_manifest, sample, context) -> str | None:
    """The full name of whoever is running the reingest, e.g. "Ada Lovelace".

    Comes from the chat session's user, threaded in by
    ``granular._build_upload_xlsx``; None when there is no session (a test, or
    the legacy rows path), leaving Scientist blank for a curator to fill rather
    than attributing the samples to nobody.
    """
    return (context or {}).get("user_full_name") or None


def _computed_lab(run_manifest, sample, context) -> str | None:
    """The lab that owns these samples, as the database already spells it.

    Derived from the lab segment of the PARENT UIDs ("D.SEQ-230512ABC-287-PUB"
    -> "ABC"), because `Lab` is not an attribute on D.SEQ and so there is
    nothing to inherit -- the UID is the only carrier.

    Every parent must agree. A per_run A.GEX row descends from every sample in
    the run, and a run mixing two labs has no single right answer, so a
    disagreement returns None rather than picking the majority: a wrong Lab on
    a shared analysis record is worse than a blank one.

    Both halves are INJECTED through `context` (`lab_code_from_uid`,
    `lab_for_code`), keeping this module free of database imports, and the
    resolution is a lookup of an EXISTING Lab string, never a composed one --
    see `reingest_lookups.lab_for_code_strict` on why the catalog has no
    convention to compose to.
    """
    code_of = (context or {}).get("lab_code_from_uid")
    lookup = (context or {}).get("lab_for_code")
    if not callable(code_of) or not callable(lookup):
        return None
    if sample is not None:
        uids = [sample.d_seq_uid] if sample.d_seq_uid else []
    else:
        uids = [s.d_seq_uid for s in run_manifest.samples if s.d_seq_uid]
    codes = {c for c in (code_of(u) for u in uids) if c}
    if len(codes) != 1:
        return None
    return lookup(codes.pop())


#: The path component from which a reference path is safe to record. Everything
#: to its left is site layout -- mount points, group directories, and the home
#: directory of whoever built the index -- which identifies people and
#: infrastructure while saying nothing about the science.
_REFERENCE_KEEP_FROM = "genomes"


#: How many trailing components to keep when `_REFERENCE_KEEP_FROM` is absent.
#: Two, so the build directory survives beside the filename: "mm39_ens113/
#: Mus_musculus.GRCm39.113.gtf" still says which assembly and which annotation
#: release, which is the whole reason these attributes exist.
_REFERENCE_KEEP_TAIL = 2


def _redact_reference_path(raw) -> str | None:
    """Drop the identifying leading path from a reference file path.

    "/net/somewhere/data/grp/alice/Genomes/mm39_ens113/x.gtf"
      -> "Genomes/mm39_ens113/x.gtf"

    Truncates at the first path component named "Genomes" (case-insensitively),
    which is the house layout. A path with no such component -- another site, or
    a reference kept somewhere else entirely -- keeps its last two components
    instead, so the redaction still holds rather than falling through and
    emitting the whole path.

    A bare filename passes through unchanged: there is nothing in front of it to
    remove.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    parts = [p for p in text.replace("\\", "/").split("/") if p]
    if not parts:
        return None
    lowered = [p.lower() for p in parts]
    if _REFERENCE_KEEP_FROM in lowered:
        return "/".join(parts[lowered.index(_REFERENCE_KEEP_FROM):])
    return "/".join(parts[-_REFERENCE_KEEP_TAIL:])


def _computed_genome_path(run_manifest, sample, context) -> str | None:
    """`params.fasta`, with the identifying leading path removed."""
    return _redact_reference_path((run_manifest.params or {}).get("fasta"))


def _computed_gtf_path(run_manifest, sample, context) -> str | None:
    """`params.gtf`, with the identifying leading path removed."""
    return _redact_reference_path((run_manifest.params or {}).get("gtf"))


def _computed_file_stem(run_manifest, sample, context) -> str | None:
    """"LPS_vs_Ctrl" from "tables/differential/LPS_vs_Ctrl.deseq2.results.tsv"
    under the glob "tables/differential/*.deseq2.results.tsv" -- the part of a
    per_file row's own file name that the glob's leading "*" matched.

    nf-core names a per-contrast output after the contrast id, so this is the
    contrast; the same holds for any other output written once per comparison.
    Only a per_file row has one file to name (``apply`` puts it in
    ``context``), so every other row gets None. So does a glob whose file-name
    part is not "*" followed by a literal suffix: there is no single matched
    part to report, and a guess would put a wrong label on the row.
    """
    path = (context or {}).get("file_path")
    glob = (context or {}).get("file_glob")
    if not path or not glob:
        return None
    name = os.path.basename(path)
    for pattern in _expand_braces(glob):
        if not fnmatch.fnmatch(path, pattern):
            continue
        name_pattern = pattern.rsplit("/", 1)[-1]
        suffix = name_pattern[1:]
        if (name_pattern.startswith("*") and not any(c in suffix for c in "*?[")
                and len(name) > len(suffix) and name.endswith(suffix)):
            return name[:-len(suffix)] if suffix else name
    return None


def _all_outputs(run_manifest, context) -> list:
    return (context or {}).get("all_outputs") or list(run_manifest.outputs)


def file_stem(context: dict | None) -> str | None:
    """The part of a per_file row's file name its glob's leading "*" matched."""
    return _computed_file_stem(None, None, context)


def all_outputs(run_manifest, context) -> list:
    return _all_outputs(run_manifest, context)


def _computed_row_name(run_manifest, sample, context) -> str | None:
    """"scxp_2026-01-26_3": like @gex_name, but prefixed and numbered by the
    row's own sample type (``context["row_sample_type"]``, set by mapper), so
    one token names rows of any type. None without the type, the date or the
    injected ``next_name_ordinal``, as @gex_name."""
    sample_type = str((context or {}).get("row_sample_type") or "")
    date = _computed_run_date(run_manifest, sample, context)
    next_ordinal = (context or {}).get("next_name_ordinal")
    if not sample_type or not date or not callable(next_ordinal):
        return None
    prefix = f"{sample_type.split('.')[-1].lower()}_{date}"
    return f"{prefix}_{next_ordinal(sample_type, prefix)}"


COMPUTED = {
    "fastq_stem": _computed_fastq_stem,
    "run_date": _computed_run_date,
    "gex_name": _computed_gex_name,
    "row_name": _computed_row_name,
    "nextseek_user": _computed_nextseek_user,
    "lab": _computed_lab,
    "genome_path": _computed_genome_path,
    "gtf_path": _computed_gtf_path,
    "file_stem": _computed_file_stem,
}
