"""Emit nf-core artifacts to disk: samplesheet.csv, params.yml, launch.yml, notes.md."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # pyyaml is already a transitive dependency via streamlit/pydantic stack
    import yaml as _yaml
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    _yaml = None
    _HAVE_YAML = False

from .catalog import get_pipeline_entry
from .ena import ENAResolution, ENARun
from .tower_client import TowerAPIError, TowerClient
from .tower_datasets import upload_samplesheet_as_dataset


# Per-pipeline column-name normalization. The LLM (and our deterministic row
# synthesis) uses standard names: sample, fastq_1, fastq_2. Some nf-core
# pipelines require different names — map them here so the final samplesheet
# matches the pipeline's expected schema.
PIPELINE_COLUMN_ALIASES: dict[str, dict[str, str]] = {
    "ampliseq": {"sample": "sampleID", "fastq_1": "forwardReads", "fastq_2": "reverseReads"},
    # mag names the short-read columns short_reads_1/2 and additionally requires a
    # `group` column (co-assembly grouping), which the row builder supplies.
    "mag": {"fastq_1": "short_reads_1", "fastq_2": "short_reads_2"},
    # bamtofastq consumes alignments, so it has no fastq columns at all: sample_id,
    # mapped, index, file_type. Only the sample rename is an alias — mapped/index/
    # file_type are synthesised by the bam branch below, already correctly named.
    "bamtofastq": {"sample": "sample_id"},
    # bacass predates the sample/fastq_1/fastq_2 convention entirely.
    "bacass": {"sample": "ID", "fastq_1": "R1", "fastq_2": "R2"},
    # pacvar is bam-input like bamtofastq, but names the columns bam/pbi. The bam
    # branch's `file_type` rides along as an extra column, which pacvar's
    # schema_input.json permits (it sets no additionalProperties: false).
    "pacvar": {"mapped": "bam", "index": "pbi"},
    "detaxizer": {"fastq_1": "short_reads_fastq_1", "fastq_2": "short_reads_fastq_2"},
    "pathogensurveillance": {"sample": "sample_id", "fastq_1": "path", "fastq_2": "path_2"},
}


# --- platform-dependent columns ------------------------------------------------
#
# A few pipelines name the read column after the SEQUENCING PLATFORM rather than
# after the read number: genomeassembler wants `ontreads` for Nanopore and
# `hifireads` for PacBio, because a long-read assembler has to know which error
# profile it is dealing with.
#
# That cannot be an entry in PIPELINE_COLUMN_ALIASES, which is static per pipeline,
# and it cannot be an elicited value either: write_samplesheet runs BEFORE
# configure_run, so nothing the user answers is available yet. It can, however, be
# read off the sample's own metadata — the same value-driven approach the FASTQ and
# alignment resolvers use — which is both earlier and more reliable than asking.
_PLATFORM_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Order matters: check the specific instrument families before the vendor words.
    ("pacbio", ("pacbio", "hifi", "revio", "sequel")),
    ("nanopore", ("nanopore", "minion", "promethion", "gridion", "\bont\b")),
    ("illumina", ("illumina", "novaseq", "miseq", "nextseq", "hiseq", "iseq", "singular")),
)

# pipeline -> platform -> {standard column: the column that platform actually uses}
PIPELINE_PLATFORM_COLUMNS: dict[str, dict[str, dict[str, str]]] = {
    "genomeassembler": {
        "nanopore": {"fastq_1": "ontreads"},
        "hifi": {"fastq_1": "hifireads"},
        "pacbio": {"fastq_1": "hifireads"},
        # Deliberately absent: illumina. genomeassembler assembles LONG reads; short
        # reads only polish. Leaving Illumina unmapped means the read lands in no
        # recognised column and the gap is visible in the sheet, rather than a
        # short-read cohort being quietly fed to a long-read assembler.
    },
}

# pipeline -> the column whose VALUE is the detected platform name.
PIPELINE_PLATFORM_VALUE_COLUMN: dict[str, str] = {
    # pathogensurveillance enumerates illumina|nanopore|pacbio|bgiseq and uses it to
    # pick per-sample tooling. It is in the metadata, so it should never be asked for.
    "pathogensurveillance": "sequence_type",
}


def _platform_from_meta(meta: Mapping[str, Any]) -> str:
    """Best-effort sequencing platform from a sample's metadata, or ''.

    Scans values rather than trusting a field name, because the platform shows up
    under Sequencer, Platform, Instrument and SequencingType depending on who
    curated the row.
    """
    blob = " ".join(str(v) for v in (meta or {}).values() if v).lower()
    for platform, hints in _PLATFORM_HINTS:
        if any(re.search(h, blob) if h.startswith("\\b") else h in blob for h in hints):
            return platform
    return ""


def _apply_platform_columns(row: dict[str, Any], pipeline: str, meta: Mapping[str, Any]) -> dict[str, Any]:
    """Rename read columns and/or stamp a platform column, per this row's metadata."""
    value_col = PIPELINE_PLATFORM_VALUE_COLUMN.get(pipeline)
    table = PIPELINE_PLATFORM_COLUMNS.get(pipeline)
    if not value_col and not table:
        return row
    platform = _platform_from_meta(meta)
    if value_col:
        # Never overwrite a value the agent set deliberately.
        row.setdefault(value_col, platform)
        if not row.get(value_col):
            row[value_col] = platform
    if table and platform:
        for standard, actual in (table.get(platform) or {}).items():
            if standard in row:
                row[actual] = row.pop(standard)
    return row


def _upload_to_tower_dataset(
    *,
    samplesheet_path: Path,
    tower_env: Mapping[str, Any],
    run_name: str,
    cohort_label: str | None,
    kind: str,
    result: "EmissionResult",
    fallback_rel_dir: str,
) -> str:
    """Upload a samplesheet to Tower as a managed dataset; return its URL.

    Used when chat_nextseek's host can't write a staging file to a path the
    Tower compute env can read (no shared mount). Same TOWER_ACCESS_TOKEN.
    On failure logs a warning and returns a relative-path fallback (which
    will not actually work at launch time but lets emission complete).
    """
    token = tower_env.get("access_token") if isinstance(tower_env, Mapping) else None
    workspace = tower_env.get("workspace") if isinstance(tower_env, Mapping) else None
    api_endpoint = tower_env.get("api_endpoint") if isinstance(tower_env, Mapping) else None
    fallback = (
        f"{fallback_rel_dir}/{samplesheet_path.name}"
        if fallback_rel_dir != "." else f"./{samplesheet_path.name}"
    )
    if not token or not workspace:
        print(
            f"[SEQERA][EMITTER] cannot upload Tower dataset (missing token/workspace); "
            f"using fallback path {fallback!r}"
        )
        return fallback
    try:
        client = TowerClient(token=str(token), endpoint=str(api_endpoint) if api_endpoint else None)
        ws = client.resolve_workspace(str(workspace))
        ws_id = ws["workspaceId"]
        dataset_name_parts = ["chat-nextseek", run_name]
        if cohort_label:
            dataset_name_parts.append(cohort_label)
        if kind != "samplesheet":
            dataset_name_parts.append(kind)
        dataset_name = "_".join(dataset_name_parts)
        info = upload_samplesheet_as_dataset(
            client,
            ws_id,
            samplesheet_path,
            name=dataset_name,
            description=f"chat_nextseek emit: {kind} for run {run_name}",
        )
        url = info["url"]
        result.saved_files[f"tower_dataset_{kind}"] = url
        print(
            f"[SEQERA][EMITTER] uploaded {kind} as Tower dataset "
            f"{info.get('name')!r} v{info.get('version')} → {url}"
        )
        return url
    except TowerAPIError as e:
        print(
            f"[SEQERA][EMITTER] Tower dataset upload failed for {samplesheet_path.name}: {e}; "
            f"using fallback path {fallback!r}"
        )
        return fallback
    except Exception as e:  # pragma: no cover
        print(
            f"[SEQERA][EMITTER] Tower dataset upload errored unexpectedly: {e!r}; "
            f"using fallback path {fallback!r}"
        )
        return fallback


def _remap_row_for_pipeline(row: Mapping[str, Any], pipeline: str) -> dict[str, Any]:
    aliases = PIPELINE_COLUMN_ALIASES.get(pipeline)
    if not aliases:
        return dict(row)
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[aliases.get(k, k)] = v
    return out


@dataclass
class EmissionResult:
    out_dir: str
    saved_files: dict[str, str] = field(default_factory=dict)
    samplesheet_row_count: int = 0
    excluded_accessions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    launch_entry: dict[str, Any] | None = None
    fetchngs_launch_entry: dict[str, Any] | None = None


def _yaml_dump(data: Any) -> str:
    if _HAVE_YAML:
        return _yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    # Fallback: hand-roll a minimal YAML for our flat-ish structures.
    return _fallback_yaml(data)


def _fallback_yaml(data: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return "{}\n"
        out = []
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.append(_fallback_yaml(v, indent + 1).rstrip("\n"))
            else:
                out.append(f"{pad}{k}: {_scalar(v)}")
        return "\n".join(out) + "\n"
    if isinstance(data, list):
        if not data:
            return "[]\n"
        out = []
        for item in data:
            if isinstance(item, dict):
                first_key = True
                for k, v in item.items():
                    prefix = f"{pad}- " if first_key else f"{pad}  "
                    if isinstance(v, (dict, list)) and v:
                        out.append(f"{prefix}{k}:")
                        out.append(_fallback_yaml(v, indent + 2).rstrip("\n"))
                    else:
                        out.append(f"{prefix}{k}: {_scalar(v)}")
                    first_key = False
            else:
                out.append(f"{pad}- {_scalar(item)}")
        return "\n".join(out) + "\n"
    return f"{pad}{_scalar(data)}\n"


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "<", ">", "=", "!", "%", "@", "`")) or s.strip() != s or s == "":
        return json.dumps(s)
    return s


def _ensure_columns(rows: Sequence[Mapping[str, Any]], required: Sequence[str],
                    suggested: Sequence[str]) -> list[str]:
    """Build the CSV column order driven by what the rows actually contain.

    - Required columns are always included (in declared order), even if rows
      don't have them — pipeline validation needs the headers.
    - Suggested columns (catalog enrichment list) are used only as ORDERING
      hints: when a suggested column is present in any row, it's placed right
      after the required block in catalog order. They are NOT force-included.
    - Any other keys present on rows follow in insertion order.

    This lets the report_writer LLM decide which enrichment columns are
    meaningful for this specific study; we just write what it gave us.
    """
    seen: set[str] = set()
    columns: list[str] = []
    for c in required:
        if c not in seen:
            columns.append(c)
            seen.add(c)
    row_keys: list[str] = []
    row_key_set: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in row_key_set:
                row_keys.append(k)
                row_key_set.add(k)
    # Suggested cols first (catalog order), but only if the LLM actually used them
    for c in suggested:
        if c in row_key_set and c not in seen:
            columns.append(c)
            seen.add(c)
    # Any remaining row keys in insertion order
    for k in row_keys:
        if k not in seen:
            columns.append(k)
            seen.add(k)
    return columns


def _coerce_csv(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


_R1_NAME = ("primary", "_r1", "read1"); _R1_FILE = ("_1.", "_r1")
_R2_NAME = ("secondary", "_r2", "read2"); _R2_FILE = ("_2.", "_r2")


def _fastq_from_meta(meta: Mapping[str, Any], read_hint: str) -> str:
    """Pick a read's fastq path from the sample metadata, field-name-agnostically.

    `read_hint` is 'primary' (R1) or 'secondary' (R2). Scans EVERY field for a VALUE that
    is actually a fastq path/URL (has a '/', ends in .fastq.gz/.fq.gz), then assigns it to
    this read by either the field NAME (…primary…/…secondary…, r1/r2) or the _1/_2 marker in
    the filename. So it finds the path wherever it lives (Link_PrimaryData / File_* / any
    name), skips bare-accession (File_PrimaryData='SRR…') and checksum fields, and prefers a
    local absolute path over a remote URL. '' when nothing usable is found.
    """
    name_hints, file_hints = (_R1_NAME, _R1_FILE) if read_hint == "primary" else (_R2_NAME, _R2_FILE)
    by_name: list[str] = []
    by_file: list[str] = []
    for key, val in (meta or {}).items():
        kl = str(key).lower()
        for part in str(val or "").split(";"):  # some fields pack R1;R2 or checksum pairs
            p = part.strip()
            pl = p.lower()
            if not ("/" in p and (pl.endswith(".fastq.gz") or pl.endswith(".fq.gz"))):
                continue
            base = pl.rsplit("/", 1)[-1]
            if any(h in kl for h in name_hints):
                by_name.append(p)
            if any(h in base for h in file_hints):
                by_file.append(p)
    pool = by_name or by_file            # trust the field name first, else the filename marker
    local = [c for c in pool if c.startswith("/")]
    return (local or pool or [""])[0]


_ALIGNMENT_EXTS = (".bam", ".cram")
# Checksum fields pack the same basename as the data field, so a field-name check is
# not enough on its own; these are the names that are never a usable file path.
_NON_PATH_NAME_HINTS = ("checksum", "md5", "sha")


def _paths_with_ext(meta: Mapping[str, Any], exts: tuple[str, ...]) -> list[str]:
    """Every value in the metadata that looks like a path to a file with one of `exts`.

    Same value-driven approach as ``_fastq_from_meta``: scan every field for a VALUE
    that is a path (has a '/') ending in a wanted extension, rather than trusting a
    field name. Skips checksum-ish fields, which carry the same basename. Local
    absolute paths sort ahead of remote URLs — the run happens on Luria.
    """
    found: list[str] = []
    for key, val in (meta or {}).items():
        if any(h in str(key).lower() for h in _NON_PATH_NAME_HINTS):
            continue
        for part in str(val or "").split(";"):
            p = part.strip()
            if "/" in p and p.lower().endswith(exts):
                found.append(p)
    return sorted(found, key=lambda p: 0 if p.startswith("/") else 1)


def _alignment_from_meta(meta: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return (mapped_path, file_type, index_path) for an alignment sample.

    `file_type` is derived from the extension of the path we found, never guessed —
    bamtofastq's schema enums it to bam|cram and mis-declaring it fails the run. All
    three are '' when the metadata carries no usable alignment path, which is the
    common case for archive-hosted records whose File_PrimaryData is a bare basename
    (e.g. '8205.1.consensus.bam'); the row is still emitted so the gap is visible in
    the samplesheet rather than silently dropped.
    """
    mapped = next(iter(_paths_with_ext(meta, _ALIGNMENT_EXTS)), "")
    if not mapped:
        return "", "", ""
    file_type = "cram" if mapped.lower().endswith(".cram") else "bam"
    wanted_index = (".crai",) if file_type == "cram" else (".bai",)
    index = next(iter(_paths_with_ext(meta, wanted_index)), "")
    return mapped, file_type, index


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        # lineterminator="\n": csv defaults to CRLF, which leaves a stray \r on the last
        # column; nf-core samplesheets want plain LF.
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _coerce_csv(row.get(c)) for c in columns})


def _build_notes_md(
    *,
    pipeline: str,
    selector_rationale: str,
    resolutions: Sequence[ENAResolution],
    excluded_accessions: Sequence[str],
    tower_env_complete: bool,
    submitted: bool,
    run_urls: Sequence[str],
    extra_notes: Sequence[str],
) -> str:
    lines: list[str] = []
    lines.append(f"# nf-core/{pipeline} run notes")
    lines.append("")
    if selector_rationale:
        lines.append(f"**Pipeline selection rationale:** {selector_rationale}")
        lines.append("")
    lines.append("## Samples")
    lines.append("")
    if not resolutions:
        lines.append("- No accessions discovered in NExtSEEK metadata.")
    else:
        for r in resolutions:
            if r.missing:
                lines.append(f"- ❌ `{r.accession}` — {r.reason}")
            else:
                run_summary = ", ".join(
                    f"{run.run_accession} ({run.layout or '?'})" for run in r.runs
                )
                lines.append(f"- ✅ `{r.accession}` → {run_summary}")
    lines.append("")
    if excluded_accessions:
        lines.append("## Excluded from samplesheet (ENA missing)")
        lines.append("")
        for acc in excluded_accessions:
            lines.append(f"- `{acc}` — see fetchngs_samplesheet.csv to backfill via nf-core/fetchngs")
        lines.append("")
    lines.append("## Luria run")
    lines.append("")
    if submitted:
        lines.append("- Submitted to Luria (ssh + sbatch). Run refs:")
        for url in run_urls:
            lines.append(f"  - {url}")
    else:
        lines.append("- Built for Luria submission (submit_to_luria stages run.sh + sbatch).")
    if extra_notes:
        lines.append("")
        lines.append("## Other notes")
        lines.append("")
        for n in extra_notes:
            lines.append(f"- {n}")
    return "\n".join(lines) + "\n"


def write_combined_launch_yml(parent_dir: str | Path, launch_entries: Sequence[Mapping[str, Any]]) -> str | None:
    """Write a top-level launch.yml at parent_dir containing all cohort entries.
    Returns the written path, or None if there are no entries."""
    entries = [dict(e) for e in launch_entries if e]
    if not entries:
        return None
    p = Path(parent_dir) / "launch.yml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_yaml_dump({"launch": entries}), encoding="utf-8")
    return str(p)


def emit_launch_artifacts(
    out_dir: str | Path,
    *,
    pipeline: str,
    samplesheet_path: str | Path,
    launch_plan: Mapping[str, Any],
    tower_env: Mapping[str, Any] | None,
    excluded: Sequence[str] = (),
    samplesheet_relative_dir: str = ".",
    write_launch_yml: bool = True,
) -> EmissionResult:
    """Stage the samplesheet and write params.yml + launch.yml (+ fetchngs fallback).

    Only acts when tower_env is complete; otherwise returns a result noting that.
    Extracted from emit_nfcore_artifacts so configure_run can call it directly.
    """
    out_path = Path(out_dir)
    samplesheet_path = Path(samplesheet_path)
    result = EmissionResult(out_dir=str(out_path))
    result.excluded_accessions = list(excluded)
    entry = get_pipeline_entry(pipeline)
    tower_complete = bool(tower_env and all(
        tower_env.get(k) for k in ("access_token", "workspace", "compute_env", "work_bucket")
    ))
    if not (tower_complete and launch_plan):
        result.notes.append("Tower env incomplete (samplesheet-only mode)")
        return result

    # --- fetchngs fallback samplesheet
    if excluded:
        fetchngs_path = out_path / "fetchngs_samplesheet.csv"
        _write_csv(
            fetchngs_path,
            [{"accession": acc} for acc in excluded],
            ["accession"],
        )
        result.saved_files["fetchngs_samplesheet"] = str(fetchngs_path)

    run_name = (launch_plan.get("run_name") or f"{pipeline}-run").strip() or f"{pipeline}-run"
    outdir_suffix = launch_plan.get("outdir_suffix") or run_name
    work_dir_suffix = launch_plan.get("work_dir_suffix") or run_name

    # Resolve where Tower's compute env will read the samplesheet from.
    # Three modes:
    #  1. SEQERA_INPUT_DIR set → stage to <input_dir>/<run_name>/, use absolute path.
    #  2. SEQERA_WORK_BUCKET looks like a local-fs path → stage to <work_bucket>/inputs/<run_name>/.
    #  3. work_bucket is a remote URI (s3://, gs://, az://) and no input_dir → fall back to
    #     relative path; caller must arrange staging or use Tower datasets.
    input_dir_root = (tower_env.get("input_dir") or "").strip()
    work_bucket_str = str(tower_env.get("work_bucket") or "").rstrip("/")
    is_remote_bucket = work_bucket_str.startswith(("s3://", "gs://", "az://"))
    if not input_dir_root and not is_remote_bucket and work_bucket_str:
        input_dir_root = f"{work_bucket_str}/inputs"
    rel_dir = (samplesheet_relative_dir or ".").strip()

    # Track staged paths so the fetchngs fallback can reuse them.
    staged_sheet_path: str | None = None
    staged_fetchngs_path: str | None = None
    # Local-FS staging first, fall through to Tower dataset upload.
    if input_dir_root:
        staged_dir = Path(input_dir_root) / run_name
        staged_sheet = staged_dir / "samplesheet.csv"
        try:
            staged_dir.mkdir(parents=True, exist_ok=True)
            staged_sheet.write_text(samplesheet_path.read_text(encoding="utf-8"), encoding="utf-8")
            staged_sheet_path = str(staged_sheet)
            sheet_ref = staged_sheet_path
            result.saved_files["staged_samplesheet"] = staged_sheet_path
            if excluded:
                fetchngs_local = out_path / "fetchngs_samplesheet.csv"
                if fetchngs_local.exists():
                    staged_fetchngs = staged_dir / "fetchngs_samplesheet.csv"
                    staged_fetchngs.write_text(
                        fetchngs_local.read_text(encoding="utf-8"), encoding="utf-8"
                    )
                    staged_fetchngs_path = str(staged_fetchngs)
                    result.saved_files["staged_fetchngs_samplesheet"] = staged_fetchngs_path
            print(f"[SEQERA][EMITTER] staged samplesheet at {staged_sheet_path}")
        except OSError as e:
            # Local mount not writable (e.g., chat_nextseek host lacks /orcd).
            # Fall through to Tower dataset upload.
            print(
                f"[SEQERA][EMITTER] local staging to {staged_dir} failed ({e!r}); "
                "falling back to Tower dataset upload."
            )
            staged_sheet_path = None

    if not staged_sheet_path:
        # Tower dataset upload — works regardless of filesystem topology.
        sheet_ref = _upload_to_tower_dataset(
            samplesheet_path=samplesheet_path,
            tower_env=tower_env,
            run_name=run_name,
            cohort_label=samplesheet_relative_dir if samplesheet_relative_dir != "." else None,
            kind="samplesheet",
            result=result,
            fallback_rel_dir=rel_dir,
        )
        if excluded:
            fetchngs_local = out_path / "fetchngs_samplesheet.csv"
            if fetchngs_local.exists():
                staged_fetchngs_path = _upload_to_tower_dataset(
                    samplesheet_path=fetchngs_local,
                    tower_env=tower_env,
                    run_name=run_name,
                    cohort_label=samplesheet_relative_dir if samplesheet_relative_dir != "." else None,
                    kind="fetchngs",
                    result=result,
                    fallback_rel_dir=rel_dir,
                )

    params: dict[str, Any] = dict(launch_plan.get("params") or {})
    params.setdefault("input", sheet_ref)
    params.setdefault("outdir", f"{work_bucket_str}/results/{outdir_suffix}")
    # NOTE: genome/reference params are owned entirely by the caller (configure_run's
    # species->bundle resolution). We deliberately do NOT fall back to the catalog's
    # default_genome here — doing so silently stamped a wrong-species genome (human
    # GRCh38) on non-human runs when no bundle was resolved.

    params_path = out_path / "params.yml"
    params_path.write_text(_yaml_dump(params), encoding="utf-8")
    result.saved_files["params"] = str(params_path)

    params_file_ref = f"{rel_dir}/params.yml" if rel_dir != "." else "./params.yml"
    # Profile precedence: explicit launch_plan > deployment env override > catalog default > "docker"
    resolved_profile = (
        launch_plan.get("profile")
        or tower_env.get("default_profile")
        or entry.get("default_profile")
        or "docker"
    )
    primary_entry = {
        "name": run_name,
        "workspace": tower_env["workspace"],
        "compute-env": tower_env["compute_env"],
        "pipeline": entry["repo"],
        "revision": launch_plan.get("pipeline_revision") or entry.get("default_revision"),
        "profile": resolved_profile,
        "work-dir": f"{tower_env['work_bucket'].rstrip('/')}/work/{work_dir_suffix}",
        "params-file": params_file_ref,
    }
    result.launch_entry = primary_entry

    if excluded:
        fetchngs_entry_meta = get_pipeline_entry("fetchngs")
        fetchngs_params_path = out_path / "fetchngs_params.yml"
        fetchngs_sheet_ref = staged_fetchngs_path or (
            f"{rel_dir}/fetchngs_samplesheet.csv"
            if rel_dir != "." else "./fetchngs_samplesheet.csv"
        )
        fetchngs_params_ref = (
            f"{rel_dir}/fetchngs_params.yml" if rel_dir != "." else "./fetchngs_params.yml"
        )
        fetchngs_params = {
            "input": fetchngs_sheet_ref,
            "outdir": f"{work_bucket_str}/results/{outdir_suffix}-fetchngs",
            "nf_core_pipeline": pipeline,
        }
        fetchngs_params_path.write_text(_yaml_dump(fetchngs_params), encoding="utf-8")
        result.saved_files["fetchngs_params"] = str(fetchngs_params_path)
        result.fetchngs_launch_entry = {
            "name": f"{run_name}-fetchngs-fallback",
            "workspace": tower_env["workspace"],
            "compute-env": tower_env["compute_env"],
            "pipeline": fetchngs_entry_meta["repo"],
            "revision": fetchngs_entry_meta.get("default_revision"),
            "profile": resolved_profile,
            "work-dir": f"{tower_env['work_bucket'].rstrip('/')}/work/{work_dir_suffix}-fetchngs",
            "params-file": fetchngs_params_ref,
        }

    if write_launch_yml:
        launch_entries = [primary_entry]
        if result.fetchngs_launch_entry:
            launch_entries.append(result.fetchngs_launch_entry)
        launch_path = out_path / "launch.yml"
        launch_path.write_text(_yaml_dump({"launch": launch_entries}), encoding="utf-8")
        result.saved_files["launch"] = str(launch_path)

    return result


def emit_luria_launch_artifacts(
    out_dir: str | Path, *, pipeline: str, samplesheet_path: str | Path,
    launch_plan: Mapping[str, Any],
) -> EmissionResult:
    """Write params.yml + a minimal launch.yml for a Luria run, with NO Tower env.

    The Luria submitter consumes only name/pipeline/revision from each launch entry
    and rebuilds params.yml on-cluster, so this deliberately does not stage to any
    bucket or upload to Tower. This is the Luria path that severs the Tower-
    completeness gate emit_launch_artifacts imposes (that function is left intact
    for a future Tower re-enable).
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    result = EmissionResult(out_dir=str(out_path))
    entry = get_pipeline_entry(pipeline)
    params: dict[str, Any] = dict(launch_plan.get("params") or {})
    params.setdefault("input", "./samplesheet.csv")
    params.setdefault("outdir", ".")
    params_path = out_path / "params.yml"
    params_path.write_text(_yaml_dump(params), encoding="utf-8")
    result.saved_files["params"] = str(params_path)
    run_name = (launch_plan.get("run_name") or f"{pipeline}-run").strip() or f"{pipeline}-run"
    launch_entry = {
        "name": run_name,
        "pipeline": entry["repo"],
        "revision": launch_plan.get("pipeline_revision") or entry.get("default_revision"),
    }
    result.launch_entry = launch_entry
    launch_path = out_path / "launch.yml"
    launch_path.write_text(_yaml_dump({"launch": [launch_entry]}), encoding="utf-8")
    result.saved_files["launch"] = str(launch_path)
    return result


def emit_nfcore_artifacts(
    out_dir: str | Path,
    *,
    pipeline: str,
    samplesheet_rows: Sequence[Mapping[str, Any]],
    resolutions: Sequence[ENAResolution],
    launch_plan: Mapping[str, Any] | None,
    tower_env: Mapping[str, Any] | None,
    selector_rationale: str = "",
    enrichment_fields: Sequence[str] | None = None,
    accession_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    extra_notes: Sequence[str] | None = None,
    samplesheet_relative_dir: str = ".",
    write_launch_yml: bool = True,
) -> EmissionResult:
    """Write all nf-core artifacts. Always writes samplesheet.csv + notes.md.
    Writes params.yml + launch.yml + (optionally) fetchngs_samplesheet.csv only
    when `tower_env` is complete (caller decides; pass None or {} to skip).

    `launch_plan` should be a SeqeraLaunchPlan model_dump() or equivalent dict;
    only consulted when emitting params.yml + launch.yml.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    result = EmissionResult(out_dir=str(out_path))

    entry = get_pipeline_entry(pipeline)
    required = entry.get("required_columns") or []
    enrichment = list(enrichment_fields or [])
    # "bam" pipelines (bamtofastq) take alignments, not reads: different columns,
    # a different resolver, and no ENA/fetchngs fallback — you cannot download a
    # FASTQ to stand in for the BAM you were asked to convert.
    is_alignment_input = entry.get("samplesheet_input_kind") == "bam"

    # Build a deterministic accession → ENA URL map. Run accessions are 1:1.
    # For experiment/study/biosample/biosample-prefixed accessions that resolve
    # to multiple runs, we emit one row per run (LLM-side "accession" maps to
    # the run accession too — see _walk_runs below).
    acc_to_runs: dict[str, list[ENARun]] = {}
    for r in resolutions:
        if r.missing:
            continue
        acc_to_runs[r.accession] = list(r.runs)
        # Also index by the run_accession so a row whose accession is the SRR
        # itself can map directly even if the resolution was queried by SRX/PRJ.
        for run in r.runs:
            if run.run_accession and run.run_accession not in acc_to_runs:
                acc_to_runs[run.run_accession] = [run]

    excluded: list[str] = [r.accession for r in resolutions if r.missing]

    # --- samplesheet.csv: rewrite fastq_1/fastq_2 from ENA + stamp enrichment values ---
    accession_metadata = dict(accession_metadata or {})
    keep_rows: list[dict[str, Any]] = []
    for row in samplesheet_rows or []:
        uid = str(row.get("sample") or row.get("Sample") or "")
        acc = row.get("accession") or row.get("Accession") or row.get("ena_accession")
        acc_str = str(acc).strip() if acc else ""
        # Curated local fastq metadata: keyed by leaf UID (path-only samples) or accession.
        sample_meta = (accession_metadata.get(uid)
                       or (accession_metadata.get(acc_str) if acc_str else None) or {})
        if is_alignment_input:
            # No ENA fan-out and no fetch fallback: the input IS the alignment. An
            # accession here identifies the archived run, not a substitute for the BAM.
            rewritten = dict(row)
            rewritten.pop("fastq_1", None)
            rewritten.pop("fastq_2", None)
            mapped, file_type, index = _alignment_from_meta(sample_meta)
            # Never overwrite a path the agent supplied explicitly (e.g. a Luria path
            # the user gave for a BAM that predates registration) with a resolver miss.
            rewritten["mapped"] = str(rewritten.get("mapped") or "") or mapped
            rewritten["file_type"] = str(rewritten.get("file_type") or "") or file_type
            if index or rewritten.get("index"):
                rewritten["index"] = str(rewritten.get("index") or "") or index
            for field in enrichment:
                value = sample_meta.get(field)
                rewritten[field] = "" if value is None else value
            keep_rows.append(_remap_row_for_pipeline(rewritten, pipeline))
            continue
        runs = acc_to_runs.get(acc_str) if acc_str else None
        if runs:
            # Legacy ENA fan-out — dormant on the Luria path (resolutions=[] -> acc_to_runs empty),
            # kept intact for a future ENA re-enable.
            curated_1 = _fastq_from_meta(sample_meta, "primary") if len(runs) == 1 else ""
            curated_2 = _fastq_from_meta(sample_meta, "secondary") if len(runs) == 1 else ""
            for run in runs:
                rewritten = dict(row)
                rewritten["accession"] = acc_str
                rewritten["run_accession"] = run.run_accession
                rewritten["fastq_1"] = curated_1 or run.fastq_1 or ""
                rewritten["fastq_2"] = curated_2 or run.fastq_2 or ""
                if run.layout and "library_layout" not in rewritten:
                    rewritten["library_layout"] = run.layout
                for field in enrichment:
                    value = sample_meta.get(field)
                    rewritten[field] = "" if value is None else value
                keep_rows.append(_remap_row_for_pipeline(rewritten, pipeline))
            continue
        # Luria path (default): fill fastq from a local /net/bmc-* path when the sample's
        # metadata carries one; otherwise leave fastq_1/fastq_2 empty and keep the accession
        # as a fetch target for the run.sh fetchngs pre-stage. Never drop the row.
        rewritten = dict(row)
        if acc_str:
            rewritten["accession"] = acc_str
        rewritten["fastq_1"] = _fastq_from_meta(sample_meta, "primary")
        rewritten["fastq_2"] = _fastq_from_meta(sample_meta, "secondary")
        for field in enrichment:
            value = sample_meta.get(field)
            rewritten[field] = "" if value is None else value
        # Platform-dependent columns come BEFORE the static alias map: the alias map
        # is keyed on the standard names, and this may have renamed them away.
        rewritten = _apply_platform_columns(rewritten, pipeline, sample_meta)
        keep_rows.append(_remap_row_for_pipeline(rewritten, pipeline))

    columns = _ensure_columns(keep_rows, required, enrichment)
    samplesheet_path = out_path / "samplesheet.csv"
    _write_csv(samplesheet_path, keep_rows, columns)
    result.saved_files["samplesheet"] = str(samplesheet_path)
    result.samplesheet_row_count = len(keep_rows)
    result.excluded_accessions = excluded

    tower_complete = bool(tower_env and all(
        tower_env.get(k) for k in ("access_token", "workspace", "compute_env", "work_bucket")
    ))

    if tower_complete and launch_plan:
        launch_res = emit_launch_artifacts(
            out_path,
            pipeline=pipeline,
            samplesheet_path=samplesheet_path,
            launch_plan=launch_plan,
            tower_env=tower_env,
            excluded=excluded,
            samplesheet_relative_dir=samplesheet_relative_dir,
            write_launch_yml=write_launch_yml,
        )
        result.saved_files.update(launch_res.saved_files)
        result.launch_entry = launch_res.launch_entry
        result.fetchngs_launch_entry = launch_res.fetchngs_launch_entry

    notes_md = _build_notes_md(
        pipeline=pipeline,
        selector_rationale=selector_rationale,
        resolutions=resolutions,
        excluded_accessions=excluded,
        tower_env_complete=tower_complete,
        submitted=False,
        run_urls=[],
        extra_notes=extra_notes or [],
    )
    notes_path = out_path / "notes.md"
    notes_path.write_text(notes_md, encoding="utf-8")
    result.saved_files["notes"] = str(notes_path)
    result.notes.append(
        "Tower env complete" if tower_complete else "Tower env incomplete (samplesheet-only mode)"
    )
    return result
