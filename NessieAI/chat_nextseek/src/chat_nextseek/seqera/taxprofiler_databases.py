"""The database sheet nf-core/taxprofiler needs, written by Nessie into the run folder.

nf-core/taxprofiler 2.0.1 will not start without ``--databases``: a CSV with one row per
classification database, naming the classifier (``tool``), a ``db_name`` unique per tool,
the classifier's own ``db_params`` for that database, and the database's ``db_path``
(assets/schema_database.json; ``db_type`` is optional). The pipeline ships no database and
builds none (docs/usage.md "Full database sheet"), and none is provisioned on Luria today.
So the databases come from, in order:

1. the user's answer to the ``databases`` entry of ``required_user_params`` in
   reports/templates/nfcore/taxprofiler.json, or
2. the curated list, ``taxprofiler_databases.databases`` in
   reports/templates/nfcore/reference_bundles.json, once someone stages databases on Luria
   and lists them there. It is empty: nothing is staged.

configure_run writes ``databases.csv`` beside the samplesheet and submit_to_luria stages it
in the run folder, as rnasplice's contrasts.csv (``agent_tools._staged_extra_files``). An
answer that is an absolute path to a ``.csv`` is the user's own sheet on Luria and is passed
through untouched; its classifiers cannot be read from here, so the run's ``run_<tool>``
flags must then be named.

Which classifiers run follows from the sheet: taxprofiler runs a classifier only when its
``--run_<tool>`` flag is set AND the sheet names a database for it (docs/usage.md "General
Usage"). By default every classifier with a database runs; a question that names particular
classifiers sets their flags to true, and then only those run.

Accepted answers (a string, or a list):

- one database per line: ``TOOL PATH [DB_PARAMS]``, e.g. ``kraken2 /net/dbs/k2_std8.tar.gz``
  or ``metaphlan /net/dbs/mpa_vJan25/ --index mpa_vJan25``; ``db_name`` is the path's last
  part, less ``.tar.gz``;
- the sheet itself, pasted, with a ``tool,db_name,...,db_path`` header;
- a list of ``{"tool", "db_path", "db_name"?, "db_params"?, "db_type"?}`` objects.

Standard library only.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

#: The name the written sheet has, locally and in the remote run folder (run.sh starts
#: Nextflow there, so the param names it bare).
DATABASES_FILENAME = "databases.csv"
#: The user param this module serves (taxprofiler's ``--databases``).
DATABASES_PARAM = "databases"

#: The classifiers of assets/schema_database.json at 2.0.1, each with a ``--run_<tool>`` flag.
TOOLS: tuple[str, ...] = (
    "bracken", "centrifuge", "diamond", "ganon", "kaiju", "kmcp", "kraken2", "krakenuniq",
    "malt", "metaphlan", "motus", "sylph", "melon", "metacache")
#: Two-step profilers whose db_params taxprofiler splits on ";" (first step; second step).
#: For every other tool a ";" would reach the task's shell, so it is refused.
_TWO_STEP = frozenset({"bracken", "kmcp", "sylph"})
_DB_TYPES = ("short", "long", "short;long")

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./+-]+$")
#: db_params are spliced into the classifier's command line (``ext.args``), so only plain
#: flags and values: no quotes (the schema's own rule), shell syntax or commas (CSV).
_PARAMS_RE = re.compile(r"^[A-Za-z0-9 _.=:/+-]*$")

_BUNDLES = Path(__file__).resolve().parent.parent / "reports" / "templates" / "nfcore" / "reference_bundles.json"


def is_user_path(value: Any) -> bool:
    """True when the answer is the user's own sheet on Luria."""
    return isinstance(value, str) and value.strip().startswith("/") and "\n" not in value.strip() \
        and value.strip().lower().endswith(".csv")


def curated_databases() -> list[dict[str, str]]:
    """The databases staged on Luria and listed in reference_bundles.json ([] when none)."""
    try:
        doc = json.loads(_BUNDLES.read_text())
    except (OSError, ValueError):
        return []
    rows = ((doc.get("taxprofiler_databases") or {}).get("databases")) or []
    return [dict(r) for r in rows if isinstance(r, dict)]


def _name_from_path(path: str) -> str:
    base = path.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".tar.gz"):
        base = base[: -len(".tar.gz")]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", base)


def _from_line(line: str) -> dict[str, str]:
    parts = line.split(None, 2)
    tool = parts[0].lower() if parts else ""
    path = parts[1] if len(parts) > 1 else ""
    return {"tool": tool, "db_name": _name_from_path(path), "db_params": parts[2].strip() if len(parts) > 2 else "",
            "db_path": path}


def _from_mapping(entry: dict) -> dict[str, str]:
    path = str(entry.get("db_path") or "").strip()
    row = {"tool": str(entry.get("tool") or "").strip().lower(),
           "db_name": str(entry.get("db_name") or "").strip() or _name_from_path(path),
           "db_params": str(entry.get("db_params") or "").strip(),
           "db_path": path}
    if str(entry.get("db_type") or "").strip():
        row["db_type"] = str(entry["db_type"]).strip()
    return row


def _from_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    return [_from_mapping({(k or "").strip().lower(): v for k, v in row.items() if isinstance(k, str)})
            for row in reader if any((v or "").strip() for v in row.values() if isinstance(v, str))]


def parse_databases(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    """The answer -> (sheet rows, problems). Rows come back only when there are none."""
    if isinstance(value, (list, tuple)):
        rows = [_from_mapping(v) if isinstance(v, dict) else _from_line(str(v)) for v in value]
    else:
        text = str(value or "").strip()
        if text.lower().startswith("tool,"):
            rows = _from_csv(text)
        else:
            rows = [_from_line(line.strip()) for line in text.splitlines() if line.strip()]
    rows = [r for r in rows if any(r.values())]
    errors = _row_problems(rows) if rows else ["databases: no database given"]
    if any("db_type" in r for r in rows):
        for row in rows:
            row.setdefault("db_type", "short;long")
    return ([] if errors else rows), errors


def _row_problems(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        tool, name, params, path = row["tool"], row["db_name"], row["db_params"], row["db_path"]
        where = f"databases: {tool or '?'} {path or '?'}"
        if tool not in TOOLS:
            errors.append(f"{where}: {tool!r} is not a classifier taxprofiler knows ({', '.join(TOOLS)})")
        if not _PATH_RE.match(path):
            errors.append(f"{where}: the database path must be an absolute path on Luria with no "
                          "spaces, commas or quotes")
        if not _NAME_RE.match(name):
            errors.append(f"{where}: db_name {name!r} must be letters, digits, '_', '.' or '-'")
        if "'" in params or '"' in params:
            errors.append(f"{where}: db_params may not hold quotes")
        elif not _PARAMS_RE.match(params.replace(";", "", 1 if tool in _TWO_STEP else 0)):
            errors.append(f"{where}: db_params {params!r} has characters other than plain flags and "
                          "values (a ';' only separates the two steps of bracken, kmcp or sylph)")
        if row.get("db_type") and row["db_type"] not in _DB_TYPES:
            errors.append(f"{where}: db_type must be one of {', '.join(_DB_TYPES)}")
        # usage.md: names must be unique per tool, and across kraken2 and bracken together,
        # because a bracken database is also run through kraken2.
        family = "kraken2" if tool in ("kraken2", "bracken") else tool
        if (family, name) in seen:
            errors.append(f"{where}: db_name {name!r} is used twice for {family}; names must be "
                          "unique per classifier (kraken2 and bracken share one set)")
        seen.add((family, name))
    return errors


def write_databases_csv(rows: list[dict[str, str]], out_dir: str | Path) -> Path:
    """Write the sheet into out_dir and return its path."""
    columns = ["tool", "db_name", "db_params"] + (["db_type"] if any("db_type" in r for r in rows) else []) \
        + ["db_path"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path = Path(out_dir) / DATABASES_FILENAME
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def _on(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def _set(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def classifier_flags(rows: list[dict[str, str]] | None, params: dict) -> tuple[dict[str, bool], list[str]]:
    """The ``run_<tool>`` flags to add to the run, and the problems with the ones it has.

    ``rows`` is the sheet (None for the user's own sheet, which cannot be read here). With
    no flag set to true in ``params``, every classifier with a database runs; with some set
    to true, only those. A flag set to false is kept. bracken needs kraken2 (taxprofiler's
    validateInputParameters error()s on --run_bracken without --run_kraken2), and kraken2 runs
    on a bracken database too, so a bracken database counts for kraken2.
    """
    named = {t for t in TOOLS if _on(params.get(f"run_{t}"))}
    refused = {t for t in TOOLS if _set(params.get(f"run_{t}")) and not _on(params.get(f"run_{t}"))}
    if rows is None:
        if not named:
            return {}, ["databases: the sheet is your own file on Luria, so its classifiers cannot be "
                        "read here: set run_<tool> to true for each classifier it holds a database for "
                        f"(run_{', run_'.join(TOOLS)})."]
        chosen = set(named)
    else:
        have = {r["tool"] for r in rows}
        covers = have | ({"kraken2"} if "bracken" in have else set())
        missing = sorted(named - covers)
        if missing:
            return {}, [f"databases: no database in the sheet for {', '.join(missing)}; add one, or "
                        "leave that classifier out"]
        chosen = set(named) if named else (have - refused)
    if "bracken" in chosen:
        if "kraken2" in refused:
            return {}, ["run_bracken needs run_kraken2 (bracken re-estimates kraken2's classification); "
                        "taxprofiler stops at validation with run_kraken2 false"]
        chosen.add("kraken2")
    chosen -= refused
    if not chosen:
        return {}, ["databases: no classifier would run; set run_<tool> for at least one classifier "
                    "with a database in the sheet"]
    return {f"run_{t}": True for t in TOOLS if t in chosen}, []


#: Archive accessions that name one run; any other accession (an experiment, sample or
#: study) may download as several runs of the one sample.
_RUN_ACCESSION = re.compile(r"^(SRR|ERR|DRR)\d+$", re.IGNORECASE)


def needs_run_merging(rows: list[dict]) -> bool:
    """True when a sample can arrive as more than one run: two rows share a sample, or a row
    still to be downloaded names an accession that is not a single run.

    taxprofiler profiles each run apart unless --perform_runmerging is set (docs/usage.md
    "Multiple runs of the same sample"), which would give one sample several profiles."""
    samples = [str(r.get("sample") or "").strip() for r in rows]
    if len(samples) != len(set(samples)):
        return True
    for row in rows:
        acc = str(row.get("accession") or row.get("run_accession") or "").strip()
        if not str(row.get("fastq_1") or "").strip() and acc and not _RUN_ACCESSION.match(acc):
            return True
    return False


def host_removal_problems(params: dict) -> list[str]:
    """taxprofiler error()s when host removal is on with no host reference FASTA."""
    on = _on(params.get("perform_shortread_hostremoval")) or _on(params.get("perform_longread_hostremoval"))
    if on and not str(params.get("hostremoval_reference") or "").strip():
        return ["host removal needs hostremoval_reference, the host genome FASTA on Luria; "
                "taxprofiler stops at validation without it"]
    return []
