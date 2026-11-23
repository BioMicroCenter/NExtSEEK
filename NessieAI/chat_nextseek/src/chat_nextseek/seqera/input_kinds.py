"""What a catalog entry's run takes as input, and how its input file is written.

Every catalog entry names a `samplesheet_input_kind`. The kind says which file each
samplesheet row points at (and so how the emitter finds that file in a sample's
metadata), whether a row with no file may be downloaded by the fetchngs pre-stage, and
whether the run reads a samplesheet at all. The sample types the kind's files belong to
are a separate knob, the entry's `accepted_leaf_sample_types` (pacvar takes a BAM that is
raw D.SEQ data; bamtofastq takes a BAM that is an A.ALN alignment).

Three optional entry fields refine a kind:

- `input_extensions`: the file endings this pipeline's input schema accepts, a subset of
  the kind's (proteinfold 2.0.0 refuses a gzipped FASTA that funcscan accepts).
- `samplesheet_format`: `csv` (the default) or `tsv` (nf-core/eager 2.5.3 reads its
  `--input` as a TSV only when the name ends in `.tsv`).

A kind with `samplesheet=False` (pangenome) takes ONE file as `--input`; the emitter still
writes a one-row sheet beside it, so the launch record and cohort.json say which sample
the file came from, and the submitter passes that row's path to `--input`.

Standard library only: the submitter, the emitter and the pipeline agent all import it.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_GZ = ".gz"


@dataclass(frozen=True)
class InputKind:
    name: str
    #: True: one samplesheet row per leaf sample, `--input` is the sheet. False: `--input`
    #: is the single file named in the sheet's one row.
    samplesheet: bool
    #: The generic column a row's file path is written to (before PIPELINE_COLUMN_ALIASES),
    #: or None for the kinds the emitter fills its own way (fastq_1/fastq_2, mapped/index).
    path_column: str | None
    #: Every file ending the kind can hold, lowercase. An entry narrows it with
    #: `input_extensions`.
    extensions: tuple[str, ...]
    #: A row with no file may be fetched from an archive by the fetchngs pre-stage.
    fetchable: bool
    summary: str


def _with_gz(*exts: str) -> tuple[str, ...]:
    return tuple(exts) + tuple(e + _GZ for e in exts)


_NUCLEOTIDE_FASTA = (".fasta", ".fa", ".fna", ".fas")

INPUT_KINDS: dict[str, InputKind] = {
    "fastq": InputKind("fastq", True, None, _with_gz(".fastq", ".fq"), True,
                       "raw reads (FASTQ)"),
    "bam": InputKind("bam", True, None, (".bam", ".cram"), False,
                     "reads as BAM or CRAM"),
    "accession": InputKind("accession", True, None, (), False,
                           "archive accessions (SRR/ERR/GSE...), not NExtSEEK lineage"),
    "fasta": InputKind("fasta", True, "fasta", _with_gz(*_NUCLEOTIDE_FASTA), False,
                       "one nucleotide FASTA per sample (an assembly or contigs)"),
    "protein_fasta": InputKind("protein_fasta", True, "fasta",
                               _with_gz(".fasta", ".fa", ".faa"), False,
                               "one protein-sequence FASTA per sample"),
    "matrix": InputKind("matrix", True, "matrix", (".h5ad", ".h5", ".rds", ".csv"), False,
                        "one expression matrix per sample (h5ad, h5, rds or csv)"),
    "combined_fasta": InputKind("combined_fasta", False, "fasta",
                                _with_gz(*_NUCLEOTIDE_FASTA), False,
                                "ONE FASTA holding every genome, passed as --input; no samplesheet"),
}

SAMPLESHEET_FORMATS: dict[str, str] = {"csv": ",", "tsv": "\t"}

#: Metadata fields that carry a file's checksum beside its name; never a path.
_NON_PATH_NAME_HINTS = ("checksum", "md5", "sha")


def _entry(entry_or_key: Mapping[str, Any] | str | None) -> Mapping[str, Any]:
    if isinstance(entry_or_key, Mapping):
        return entry_or_key
    from .catalog import NFCORE_PIPELINE_CATALOG
    return NFCORE_PIPELINE_CATALOG.get(str(entry_or_key or "").strip().lower(), {})


def input_kind(entry_or_key: Mapping[str, Any] | str | None) -> InputKind:
    """The InputKind of a catalog entry (or catalog key). An entry that names none, or an
    unknown key, is a FASTQ pipeline: that was the only kind before the field existed."""
    name = _entry(entry_or_key).get("samplesheet_input_kind") or "fastq"
    try:
        return INPUT_KINDS[name]
    except KeyError:
        raise ValueError(f"unknown samplesheet_input_kind {name!r}; known: {sorted(INPUT_KINDS)}")


def input_extensions(entry_or_key: Mapping[str, Any] | str | None) -> tuple[str, ...]:
    """The file endings this entry's input accepts: its `input_extensions`, else its kind's."""
    entry = _entry(entry_or_key)
    own = entry.get("input_extensions")
    return tuple(str(e).lower() for e in own) if own else input_kind(entry).extensions


def samplesheet_format(entry_or_key: Mapping[str, Any] | str | None) -> str:
    fmt = _entry(entry_or_key).get("samplesheet_format") or "csv"
    if fmt not in SAMPLESHEET_FORMATS:
        raise ValueError(f"unknown samplesheet_format {fmt!r}; known: {sorted(SAMPLESHEET_FORMATS)}")
    return fmt


def samplesheet_filename(entry_or_key: Mapping[str, Any] | str | None) -> str:
    """The sheet's file name, locally and in the Luria run folder: samplesheet.csv, or
    samplesheet.tsv for a `samplesheet_format: tsv` entry."""
    return f"samplesheet.{samplesheet_format(entry_or_key)}"


def delimiter_for(path: str | Path) -> str:
    """The delimiter a samplesheet file was written with, read off its name."""
    return "\t" if str(path).lower().endswith(".tsv") else ","


def read_samplesheet(path: str | Path) -> list[dict]:
    """Rows of a samplesheet written by the emitter, CSV or TSV."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter_for(path)))


def file_from_meta(meta: Mapping[str, Any], extensions: Sequence[str]) -> str:
    """The first metadata VALUE that is a path to a file with one of `extensions`.

    Value-driven, like the emitter's FASTQ and alignment resolvers: every field is
    scanned for a value with a '/' that ends in a wanted extension, whatever the field is
    called. Checksum fields, which carry the same basename, are skipped, and a local
    absolute path is preferred over a remote URL, because the run happens on Luria. ''
    when none is found (an archive record whose File_PrimaryData is a bare name).
    """
    wanted = tuple(e.lower() for e in extensions)
    found: list[str] = []
    for key, val in (meta or {}).items():
        if any(h in str(key).lower() for h in _NON_PATH_NAME_HINTS):
            continue
        for part in str(val or "").split(";"):
            p = part.strip()
            if "/" in p and p.lower().endswith(wanted):
                found.append(p)
    found.sort(key=lambda p: 0 if p.startswith("/") else 1)
    return found[0] if found else ""


def check_rows(entry_or_key: Mapping[str, Any] | str | None, rows: Iterable[Mapping]) -> list[str]:
    """Errors that stop write_samplesheet before anything is emitted. Only the single-file
    kind has one today: it takes exactly one FASTA, and NExtSEEK does not combine several
    samples' FASTAs into one (that step is not built)."""
    kind = input_kind(entry_or_key)
    rows = list(rows)
    if not kind.samplesheet and len(rows) != 1:
        return [f"This pipeline takes ONE FASTA file holding every genome as its input, so the "
                f"build needs exactly one sample whose file is that combined FASTA; got {len(rows)}. "
                "Combining several samples' FASTAs into one is not supported yet: ask the user "
                "for the one sample that holds the combined file."]
    return []


#: What may follow `--input` in run.sh: a relative file name or an absolute path, word
#: characters, dots, dashes and slashes only, no `..` segment, never starting with '-'.
_INPUT_ARG_RE = re.compile(r"(?:/|[A-Za-z0-9_])[A-Za-z0-9_./-]{0,511}")


def validate_input_arg(value: str) -> str:
    text = str(value or "")
    if not _INPUT_ARG_RE.fullmatch(text) or ".." in text.split("/"):
        raise ValueError(f"invalid --input value {value!r}")
    return text


def run_input(entry_or_key: Mapping[str, Any] | str | None, sheet_path: str | Path,
              column: str | None = None) -> str:
    """What run.sh passes as `--input`. A samplesheet kind: the sheet's file name (it is
    staged beside run.sh under that name). The single-file kind: the path in the sheet's one
    row, under `column` (the path column after the pipeline's alias), validated for the shell.
    Raises ValueError when that row or path is missing."""
    entry = _entry(entry_or_key)
    kind = input_kind(entry)
    if kind.samplesheet:
        return validate_input_arg(samplesheet_filename(entry))
    col = column or kind.path_column or ""
    rows = read_samplesheet(sheet_path)
    paths = [str(r.get(col) or "").strip() for r in rows]
    if len(rows) != 1 or not paths[0]:
        raise ValueError(f"{kind.name} input needs one row with a {col!r} path; the sheet has "
                         f"{len(rows)} row(s) and {sum(1 for p in paths if p)} path(s)")
    if not paths[0].lower().endswith(input_extensions(entry)):
        raise ValueError(f"{paths[0]!r} does not end in one of {list(input_extensions(entry))}")
    return validate_input_arg(paths[0])


def kinds_with_files() -> dict[str, InputKind]:
    """The kinds whose rows carry one file in a path column (the non-read inputs)."""
    return {name: kind for name, kind in INPUT_KINDS.items() if kind.path_column}
