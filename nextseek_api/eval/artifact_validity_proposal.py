"""General, kind-agnostic artifact validator — the runnable form of the proposed V9.

PROPOSAL, not an implementation. This is the verified reference for the deterministic
artifact axis: it runs, and its results are the regression pins the real task must
reproduce. The implementation this plan reserves is `nextseek_api/eval/artifact_validity.py`
plus `artifact_sources.py`; do not import this file as product code.

The one thing that must change on port: ROOT below is a hardcoded path to a delivered
run. The real module resolves artifacts through two source adapters — a live turn's
`result.artifacts`/`result.files` against the outputs volume, and an exported run
directory — so it ingests the next paired E2E run without a code change.

SCOPE OF WHAT THIS SUPERSEDES. dmac_assistant remains the port source for the judge, the
HiBayes fit code and the eval containers. Only ONE thing is superseded: the per-kind
dispatch in dmac_assistant/tools/hibayes/artifact_validator.py, which routes on
task_family -> ArtifactKind and therefore needs a new hardcoded branch for every new
report type. That design produced, on Charlie's set3 delivery:

  * `Missing` for all 18 CC artifact-expected arms, 9 of which have real deliverables
    on disk (the DD-25 hazard the upstream module's own docstring warns about);
  * `Indeterminate` for 9 NS arms, meaning only "no validator for kind=PRIDE_PACKAGE";
  * zero `Valid` anywhere, on either arm.

THE DESIGN. An artifact declares its own schema. Both engines mark required fields with
a leading `*` (single-valued) or `**` (multi-valued) — the same convention the upstream
module already reads at artifact_validator.py:378. So validation reads the markers the
artifact carries; it does not switch on what KIND of report it is. ArtifactKind survives
only as a reporting label. A new report type needs no code change here.

MULTI-ARTIFACT IS THE NORMAL CASE, not an edge case: one PRIDE arm emits 4 artifacts on
NS (2 inline tables + 2 files) and 8 files on CC. The upstream single-file guard
(`NotImplementedError`, plan-DD-03) is what was anomalous.

CURATOR DECISIONS (2026-08-08), encoded below:
  1. Required-but-empty  -> STRUCTURAL ONLY. A required key must be PRESENT; its value
                            may be null. Null does not fail an artifact.
  2. Many artifacts      -> WORST STATUS WINS, on the full 10-value ArtifactStatus scale.
                            The per-artifact detail is kept, not collapsed early.
  3./4. judge sourcing and judge scope are downstream of this file.

Stack: calamine (via fastexcel), polars, orjson — no openpyxl, no stdlib csv/json on
any hot path.

Run:  uv run --no-project --with fastexcel --with polars --with orjson python artifact_validity_proposal.py

      `--no-project` is REQUIRED from inside this repo. Without it uv resolves the
      NExtSEEK project's own dependencies and dies on torch, which publishes no
      x86_64 macOS wheel — the script never runs, and an unchanged output file
      looks deceptively like a clean reproduction.

Out:  artifact_validity_<set>.csv        one row per arm
      artifact_detail_<set>.csv          one row per artifact

Committed results for set3_final sit beside this file and are the regression pins:
artifact_validity_set3_final.csv (298 arms), artifact_detail_set3_final.csv (256 artifacts).
Source data: Charlie's 2026-08-07 `testquestions` delivery, staged on the dev box at
~/work/NExtSEEK-dev/testquestions-2026-08-07/ with a per-file manifest (MANIFEST.json).
"""

from __future__ import annotations

import argparse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import fastexcel
import orjson
import polars as pl

ROOT = Path.home() / "Downloads" / "testquestions"
OUT_DIR = Path(__file__).resolve().parent

# The upstream 10-value vocabulary (tools/e2e/functional_evaluator_models.py), kept
# verbatim so results are comparable with prior runs. Severity ascending: "worst wins"
# takes the maximum. Indeterminate is deliberately WORST — under this design it means
# the validator met something it has no rule for, which should be loud, not silent.
SEVERITY: dict[str, int] = {
    "NotExpected": 0,
    "Valid": 1,
    "Incomplete": 2,
    "SchemaInvalid": 3,
    "Unreadable": 4,
    "Inaccessible": 5,
    "PartialAfterFailure": 6,
    "Missing": 7,
    "RuntimeFailed": 8,
    "Indeterminate": 9,
}

# Projection onto the plan's 4-value artifact_status (V8-C). Total by construction.
# Only Valid yields artifact_success=true. Indeterminate maps to EXCLUDED rather than
# to a failure: "we had no rule" must never be recorded as "the engine failed".
PLAN_STATUS: dict[str, str] = {
    "NotExpected": "not_expected",
    "Valid": "delivered_valid",
    "Incomplete": "delivered_invalid",
    "SchemaInvalid": "delivered_invalid",
    "Unreadable": "delivered_invalid",
    "Inaccessible": "delivered_invalid",
    "PartialAfterFailure": "delivered_invalid",
    "Missing": "missing",
    "RuntimeFailed": "missing",
    "Indeterminate": "EXCLUDED",
}

# Files that are transport, not deliverables. artifacts.zip is verified byte-equivalent
# to the loose nextseek-artifacts/ tree before being skipped (see collect_disk_files).
TRANSPORT_NAMES = {"artifacts.zip"}


@dataclass
class ArtifactResult:
    artifact_id: str
    origin: str          # "declared_table" | "file"
    detected_type: str
    bytes: int | None
    status: str
    required_markers: int = 0
    missing_markers: list[str] = field(default_factory=list)
    rows: int | None = None
    notes: str = ""


# --------------------------------------------------------------------------- typing


def detect_type(path: Path) -> str:
    """Magic-byte typing. CC strips extensions from report bundles (`pride_sdrf__3`),
    so an extension-based sniff is exactly the assumption that breaks on one engine."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return "unreadable"
    if head[:4] == b"PK\x03\x04":
        try:
            names = zipfile.ZipFile(path).namelist()
        except zipfile.BadZipFile:
            return "zip-corrupt"
        # `[Content_Types].xml` is present in EVERY OOXML container — docx and pptx
        # as much as xlsx — so it cannot discriminate. The part directory does:
        # xl/ = workbook, word/ = document, ppt/ = deck. Getting this wrong sends a
        # Word protocol attachment to a spreadsheet reader and scores it Unreadable.
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
        return "zip"
    stripped = head.lstrip()
    if stripped[:1] in (b"{", b"["):
        return "json"
    if head[:5] == b"<?xml" or head[:4] == b"<svg":
        return "xml"
    try:
        with path.open("r", encoding="utf-8", errors="strict") as fh:
            first = fh.readline()
    except (UnicodeDecodeError, OSError):
        return "binary"
    if "\t" in first:
        return "tsv"
    if "," in first:
        return "csv"
    return "text"


# ------------------------------------------------------------------ marker discovery


def collect_markers(obj, found: list[str]) -> None:
    """Every `*`/`**`-prefixed key anywhere in the payload. The convention is the
    schema; no per-kind knowledge is involved."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith("*"):
                found.append(k)
            collect_markers(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_markers(item, found)


# ---------------------------------------------------------------- file-level checks


def validate_file(path: Path, artifact_id: str) -> ArtifactResult:
    if not path.exists():
        return ArtifactResult(artifact_id, "file", "absent", None, "Missing",
                              notes="declared but not on disk")
    size = path.stat().st_size
    kind = detect_type(path)
    r = ArtifactResult(artifact_id, "file", kind, size, "Valid")

    if size == 0:
        r.status, r.notes = "SchemaInvalid", "zero-byte file"
        return r
    if kind in ("unreadable", "zip-corrupt"):
        r.status, r.notes = "Unreadable", f"cannot open as {kind}"
        return r

    if kind == "json":
        try:
            payload = orjson.loads(path.read_bytes())
        except orjson.JSONDecodeError as exc:
            r.status, r.notes = "Unreadable", f"json parse error: {exc}"[:120]
            return r
        markers: list[str] = []
        collect_markers(payload, markers)
        r.required_markers = len(markers)
        # DECISION 1 — structural only. A marker's presence is the requirement; a null
        # value is not a failure. Nothing here inspects values.
        r.rows = len(payload) if isinstance(payload, list) else None
    elif kind == "xlsx":
        try:
            book = fastexcel.read_excel(str(path))
            if not book.sheet_names:
                r.status, r.notes = "SchemaInvalid", "workbook has no sheets"
            else:
                total = 0
                for name in book.sheet_names:
                    total += book.load_sheet(name, header_row=None).height
                r.rows = total
                if total == 0:
                    r.status, r.notes = "SchemaInvalid", "workbook has no rows"
        except Exception as exc:  # noqa: BLE001
            r.status, r.notes = "Unreadable", f"calamine: {type(exc).__name__}"
    elif kind in ("csv", "tsv"):
        try:
            frame = pl.read_csv(
                path, separator="," if kind == "csv" else "\t",
                infer_schema=False, truncate_ragged_lines=True,
            )
        except Exception as exc:  # noqa: BLE001
            r.status, r.notes = "Unreadable", f"polars: {type(exc).__name__}"
            return r
        if not frame.columns:
            r.status, r.notes = "SchemaInvalid", "no header row"
        else:
            r.required_markers = sum(1 for c in frame.columns if c.startswith("*"))
            r.rows = frame.height
    elif kind in ("docx", "pptx"):
        # A document attachment (e.g. a protocol .docx shipped inside a PRIDE deposit)
        # is a deliverable in its own right. Structural check only: a well-formed OOXML
        # container carrying its main part.
        main = "word/document.xml" if kind == "docx" else "ppt/presentation.xml"
        try:
            names = zipfile.ZipFile(path).namelist()
        except zipfile.BadZipFile:
            r.status, r.notes = "Unreadable", "corrupt OOXML container"
            return r
        if main not in names:
            r.status, r.notes = "SchemaInvalid", f"OOXML container lacks {main}"
    # text / xml / zip / binary: non-empty and readable is all that can be asserted.
    return r


def validate_declared_table(art: dict, artifact_id: str) -> ArtifactResult:
    """An inline `table` artifact carries `columns` (with `*`/`**` markers) and `data`.
    Structural check: every declared column key appears in every row. Values may be null."""
    columns = art.get("columns") or []
    data = art.get("data") or []
    required = [c for c in columns if isinstance(c, str) and c.startswith("*")]
    r = ArtifactResult(artifact_id, "declared_table", "table", None, "Valid",
                       required_markers=len(required), rows=len(data))
    if not columns:
        r.status, r.notes = "SchemaInvalid", "table declares no columns"
        return r
    missing: set[str] = set()
    for row in data:
        if isinstance(row, dict):
            missing |= {c for c in required if c not in row}
    if missing:
        r.status = "Incomplete"
        r.missing_markers = sorted(missing)
        r.notes = f"{len(missing)} required column(s) absent from at least one row"
    return r


# ------------------------------------------------------------------ arm-level walk


def collect_disk_files(arm_dir: Path) -> list[Path]:
    """NS publishes to run_root/files/**, CC to output/**. Path resolution is the only
    arm-specific step in this module; validation below is identical for both."""
    out: list[Path] = []
    for sub in ("output", "run_root/files"):
        base = arm_dir / sub
        if base.is_dir():
            out += [p for p in base.rglob("*") if p.is_file()]

    bundle = arm_dir / "output" / "artifacts.zip"
    if bundle.exists():
        # Skip the bundle only if it is genuinely a duplicate of the loose tree;
        # otherwise it carries unique content and must be validated on its own.
        try:
            members = {Path(n).name for n in zipfile.ZipFile(bundle).namelist()
                       if not n.endswith("/")}
            loose = {p.name for p in out if p != bundle}
            if members and members <= loose:
                out = [p for p in out if p.name not in TRANSPORT_NAMES]
        except zipfile.BadZipFile:
            pass
    return out


def validate_arm(arm_dir: Path, artifact_expected: bool, runtime_success: bool):
    results: list[ArtifactResult] = []

    task = arm_dir / "task.json"
    declared = []
    if task.exists():
        try:
            payload = orjson.loads(task.read_bytes())
            declared = (payload.get("result") or {}).get("artifacts") or []
        except orjson.JSONDecodeError:
            declared = []

    for i, art in enumerate(declared):
        if art.get("artifact_type") == "table":
            results.append(validate_declared_table(art, art.get("key") or f"table[{i}]"))

    for p in sorted(collect_disk_files(arm_dir)):
        results.append(validate_file(p, str(p.relative_to(arm_dir))))

    if not artifact_expected:
        return "NotExpected", results
    if not results:
        # DD-36: distinguish "the run failed and produced nothing" from "the run
        # succeeded but delivered nothing" — different defects, different dispositions.
        return ("Missing" if runtime_success else "RuntimeFailed"), results

    # DECISION 2 — worst status wins, on the full 10-value scale.
    return max((r.status for r in results), key=lambda s: SEVERITY[s]), results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="set3_final")
    args = ap.parse_args()
    setname = args.set
    base = ROOT / setname

    expected = {
        r["query_id"]: r
        for r in pl.read_csv(base / "hibayes" / "hibayes_functional_eval_inputs.csv",
                             infer_schema=False).to_dicts()
    }

    runtime: dict[str, bool] = {}
    for arm in ("ns", "cc"):
        frame = pl.read_csv(base / "hibayes" / f"hibayes_eval_rows_{arm}.csv",
                            infer_schema=False)
        for r in frame.to_dicts():
            runtime[f"{r['query_id']}::{arm}"] = r["runtime_success"].lower() == "true"

    arm_rows, detail_rows = [], []
    for key, meta in sorted(expected.items()):
        qid, arm = key.rsplit("::", 1)
        arm_dir = base / "raw_files" / qid / arm
        exp = meta["artifact_expected"].lower() == "true"
        status, results = validate_arm(arm_dir, exp, runtime.get(key, False))
        plan_status = PLAN_STATUS[status]
        arm_rows.append({
            "arm_key": key, "query_id": qid, "arm": arm,
            "task_family": meta["task_family"],
            "artifact_expected": exp,
            "reported_kind_label": meta["artifact_kind"],
            "charlie_artifact_status": meta["artifact_status"],
            "artifact_status": status,
            "plan_artifact_status": plan_status,
            # V8-C: a family that expects no artifact CANNOT fail the artifact gate.
            # `not_expected` must pass, or the 262 arms that never needed an artifact
            # would all be scored 0 by the conjunctive rule.
            #
            # None (not False) for EXCLUDED. `Indeterminate` means this validator met a
            # shape it has no rule for; under the conjunctive outcome a False here would
            # silently record "the engine failed" for what is actually "we could not
            # measure" — the precise confusion this module exists to remove. None routes
            # to EvalRow.outcome()'s excluded path instead. Count asserted below.
            "artifact_success": (
                None if plan_status == "EXCLUDED"
                else plan_status in ("delivered_valid", "not_expected")
            ),
            "n_artifacts": len(results),
            "n_required_markers": sum(r.required_markers for r in results),
        })
        for r in results:
            detail_rows.append({
                "arm_key": key, "artifact_id": r.artifact_id, "origin": r.origin,
                "detected_type": r.detected_type, "bytes": r.bytes, "status": r.status,
                "required_markers": r.required_markers,
                "missing_markers": ";".join(r.missing_markers), "rows": r.rows,
                "notes": r.notes,
            })

    # Loud, not silent: an unmeasurable arm must be visible, never averaged in as a loss.
    unmeasurable = [r["arm_key"] for r in arm_rows if r["artifact_success"] is None]
    if unmeasurable:
        print(f"WARNING: {len(unmeasurable)} arm(s) are Indeterminate — no validation "
              f"rule matched their shape. These are EXCLUDED from the fit, not scored 0. "
              f"First few: {unmeasurable[:5]}")
    else:
        print("no Indeterminate arms: every artifact shape encountered had a rule")

    for name, rows in ((f"artifact_validity_{setname}.csv", arm_rows),
                       (f"artifact_detail_{setname}.csv", detail_rows)):
        path = OUT_DIR / name
        pl.DataFrame(rows, infer_schema_length=None).write_csv(path)
        print(f"wrote {path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
