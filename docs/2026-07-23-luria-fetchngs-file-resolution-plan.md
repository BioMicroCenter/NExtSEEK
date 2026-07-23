# Luria-only file-resolution + fetchngs + reference-bias Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the nf-core pipeline agent Luria-only: resolve each sample's fastqs from a local `/net/bmc-*` path or an SRR accession, fetch SRR-only samples on-cluster via an `nf-core/fetchngs` pre-stage in the run.sh, turn off the ENA and Tower routes (code left in place), and bias reference resolution to local Luria references with honest messaging.

**Architecture:** Host side (the container) decides source per sample and writes a samplesheet where local rows are filled and SRR rows are blank-but-tagged with their accession. Luria side, a fetchngs pre-block in the single `run.sh` fills the blank rows from a shared cache, then the unchanged pipeline block runs against local files. Reference resolution consults `LURIA_GENOMES` as the single source of truth for "has a local ref."

**Tech Stack:** Python 3.14, `uv`, pytest, nf-core/Nextflow, Luria SLURM via ssh+sbatch.

**Design spec:** `docs/2026-07-23-luria-fetchngs-file-resolution-design.md` (read it first).

## Global Constraints

- Package root: `chat_nextseek/src/chat_nextseek/`. Tests: `chat_nextseek/tests/`. **All `uv`/`pytest` commands run from the `chat_nextseek/` subpackage dir** (its own uv project, `uv.lock`).
- Run tests with: `uv run pytest tests/ --ignore=tests/evaluator -q`. Scope a file with `uv run pytest tests/test_x.py -v`.
- **Leave code, do not delete:** `seqera/ena.py`, `tool_submit_to_tower` + `submit_to_tower` schema + the Tower client, `prompts/seqera_agent.txt`. Turn them off / de-mention only.
- **Fail-closed** on every new value that enters a shell script: validate against a regex, raise `ValueError` on mismatch. Accession values are NEVER interpolated into host-rendered shell; the run.sh derives them at runtime from the staged samplesheet via a fixed helper.
- Conventional commits, module scopes: `feat(pipeline): ...`, `fix(luria): ...`, `docs(...)`.
- `build_reference_params(pipeline_key, bundle_key)` returns `tuple[dict, str]` (params, status). Status enum after this work: `local_luria` > `configured` > `igenomes_fallback` > `unconfigured_no_fallback` > `no_bundle` (+ `invalid` upstream).
- fetchngs revision default is the catalog's `NFCORE_PIPELINE_CATALOG["fetchngs"]["default_revision"]` (currently `"1.12.0"`).
- The four genome keys with local Luria refs (in `LURIA_GENOMES`): `GRCh38, GRCm39, Mfas6.0, Mmul_10`.

---

### Task 1: `has_local_luria_ref` predicate + `local_luria` reference status

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/luria/run_script.py` (add two functions after `LURIA_GENOMES`, ~line 103)
- Modify: `chat_nextseek/src/chat_nextseek/seqera/pipeline_params.py:79-109` (`build_reference_params`) + import
- Test: `chat_nextseek/tests/test_luria_run_script.py` (predicate), `chat_nextseek/tests/test_pipeline_params.py` (status)

**Interfaces:**
- Produces: `has_local_luria_ref(genome_key: str | None) -> bool`; `local_luria_ref_files(genome_key: str | None) -> dict[str, str] | None` (keys `fasta`, `gtf`, values are filenames); `build_reference_params(...)` now returns status `"local_luria"` for a bundle whose `igenomes_key` is in `LURIA_GENOMES`.

- [ ] **Step 1: Write the failing predicate tests** in `tests/test_luria_run_script.py` (append):

```python
from chat_nextseek.luria.run_script import has_local_luria_ref, local_luria_ref_files


def test_has_local_luria_ref_true_for_registered_keys():
    for key in ("GRCh38", "GRCm39", "Mfas6.0", "Mmul_10"):
        assert has_local_luria_ref(key) is True


def test_has_local_luria_ref_false_for_unregistered_or_none():
    assert has_local_luria_ref("GRCz11") is False
    assert has_local_luria_ref(None) is False
    assert has_local_luria_ref("") is False


def test_local_luria_ref_files_returns_filenames_or_none():
    files = local_luria_ref_files("Mfas6.0")
    assert files["fasta"].endswith(".fa.gz") and files["gtf"].endswith(".gtf.gz")
    assert local_luria_ref_files("GRCz11") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_luria_run_script.py -k local_luria_ref -v`
Expected: FAIL with `ImportError: cannot import name 'has_local_luria_ref'`

- [ ] **Step 3: Add the predicate + files helper** to `luria/run_script.py`, immediately after the `LURIA_GENOMES` dict (after line 103):

```python
def has_local_luria_ref(genome_key: str | None) -> bool:
    """True when genome_key has a local Luria reference registered in LURIA_GENOMES.

    Single source of truth shared by the host-side reference resolver
    (seqera.pipeline_params.build_reference_params) and the submit-side
    --fasta/--gtf injection, so the two can never disagree about whether a local
    reference exists.
    """
    return str(genome_key or "") in LURIA_GENOMES


def local_luria_ref_files(genome_key: str | None) -> dict[str, str] | None:
    """The {'fasta','gtf'} FILENAMES of the local Luria reference for genome_key,
    or None when there is no local ref. Filenames (not absolute paths) so the host
    side can name them without knowing the Luria refs_root."""
    ref = LURIA_GENOMES.get(str(genome_key or ""))
    return {"fasta": ref["fasta"], "gtf": ref["gtf"]} if ref else None
```

- [ ] **Step 4: Run to verify the predicate tests pass**

Run: `uv run pytest tests/test_luria_run_script.py -k local_luria_ref -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write/flip the status tests** in `tests/test_pipeline_params.py`. Replace `test_build_reference_params_igenomes_fallback_when_store_unconfigured` (lines 72-75) and `test_build_run_params_merges_defaults_bundle_and_overrides` (118-125) with:

```python
def test_build_reference_params_local_luria_for_registered_genome():
    params, status = pp.build_reference_params("rnaseq", "GRCm39")
    assert params == {"genome": "GRCm39"}
    assert status == "local_luria"   # GRCm39 has a local Luria ref -> not an iGenomes fallback


def test_build_reference_params_igenomes_fallback_for_unregistered(monkeypatch):
    bundles = {"store_root": None, "species_to_bundle": {"zeb": "GRCz11"},
               "bundles": {"GRCz11": {"igenomes_key": "GRCz11"}}}   # not in LURIA_GENOMES
    monkeypatch.setattr(pp, "load_reference_bundles", lambda: bundles)
    params, status = pp.build_reference_params("rnaseq", "GRCz11")
    assert params == {"genome": "GRCz11"} and status == "igenomes_fallback"


def test_build_run_params_merges_defaults_bundle_and_overrides():
    merged, errors, status = pp.build_run_params(
        "rnaseq", agent_params={"aligner": "hisat2"}, bundle_key="GRCm39")
    assert errors == []
    assert merged["aligner"] == "hisat2"
    assert merged["pseudo_aligner"] == "salmon"
    assert merged["genome"] == "GRCm39"
    assert status == "local_luria"   # was igenomes_fallback before the local-ref bias
```

- [ ] **Step 6: Run to verify these fail**

Run: `uv run pytest tests/test_pipeline_params.py -k "local_luria or igenomes_fallback_for_unregistered or merges_defaults" -v`
Expected: FAIL (`local_luria` not yet produced; `igenomes_fallback` still returned for GRCm39)

- [ ] **Step 7: Add the `local_luria` branch** to `seqera/pipeline_params.py`. Add the import near the top (after line 12):

```python
from ..luria.run_script import has_local_luria_ref
```

Then in `build_reference_params`, insert the `local_luria` check just after `wanted = set(...)` (line 95), before the `if store_root:` block:

```python
    igenomes_key = bundle.get("igenomes_key")
    # Local Luria reference wins: the submit path injects it as --fasta/--gtf
    # (path > iGenomes), so report it honestly rather than as an iGenomes fallback.
    if igenomes_key and has_local_luria_ref(igenomes_key):
        return {"genome": igenomes_key}, "local_luria"
    if store_root:
```

Also extend the docstring status list (lines 82-86) to include the new top-priority status:

```python
    """Return (reference_params, reference_status) for a pipeline + bundle.

    status: 'local_luria'            -> genome key has a local Luria ref (LURIA_GENOMES) -> {'genome': key}
            'configured'             -> store_root set, explicit resource paths emitted
            'igenomes_fallback'      -> store_root unset, bundle has igenomes_key, NO local ref -> {'genome': key}
            'unconfigured_no_fallback' -> store_root unset and no igenomes_key (e.g. PDX combo)
            'no_bundle'              -> bundle_key is None/unknown
    """
```

- [ ] **Step 8: Run the full pipeline_params + luria run_script suites**

Run: `uv run pytest tests/test_pipeline_params.py tests/test_luria_run_script.py -q`
Expected: PASS (all, including the flipped assertions)

- [ ] **Step 9: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/luria/run_script.py \
        chat_nextseek/src/chat_nextseek/seqera/pipeline_params.py \
        chat_nextseek/tests/test_luria_run_script.py chat_nextseek/tests/test_pipeline_params.py
git commit -m "feat(pipeline): local_luria reference status via shared has_local_luria_ref predicate (#2)"
```

---

### Task 2: `emit_luria_launch_artifacts` (Tower-free launch emit)

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/seqera/emitter.py` (add a new function; place after `emit_launch_artifacts`, ~line 510+)
- Test: `chat_nextseek/tests/test_emitter_launch_split.py`

**Interfaces:**
- Produces: `emit_luria_launch_artifacts(out_dir, *, pipeline, samplesheet_path, launch_plan) -> EmissionResult` with `saved_files["params"]` and `saved_files["launch"]` set, `launch_entry` a dict of `{name, pipeline, revision}`. No `tower_env` argument, no bucket staging.

- [ ] **Step 1: Write the failing test** in `tests/test_emitter_launch_split.py` (append):

```python
import yaml
from pathlib import Path
from chat_nextseek.seqera.emitter import emit_luria_launch_artifacts


def test_emit_luria_launch_artifacts_writes_params_and_minimal_launch(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nD.SEQ-1,/net/bmc-x/1.fastq.gz\n")
    res = emit_luria_launch_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_path=sheet,
        launch_plan={"run_name": "myrun", "pipeline_revision": "3.18.0",
                     "params": {"aligner": "star_salmon", "genome": "GRCm39"}})
    launch = yaml.safe_load(Path(res.saved_files["launch"]).read_text())
    assert launch["launch"][0]["name"] == "myrun"
    assert launch["launch"][0]["pipeline"] == "nf-core/rnaseq"
    assert launch["launch"][0]["revision"] == "3.18.0"
    params = yaml.safe_load(Path(res.saved_files["params"]).read_text())
    assert params["aligner"] == "star_salmon" and params["genome"] == "GRCm39"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_emitter_launch_split.py -k emit_luria -v`
Expected: FAIL with `ImportError: cannot import name 'emit_luria_launch_artifacts'`

- [ ] **Step 3: Add the function** to `seqera/emitter.py` (after `emit_launch_artifacts` ends). It reuses the module's existing `Path`, `EmissionResult`, `get_pipeline_entry`, `_yaml_dump`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_emitter_launch_split.py -k emit_luria -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/seqera/emitter.py chat_nextseek/tests/test_emitter_launch_split.py
git commit -m "feat(pipeline): Tower-free emit_luria_launch_artifacts (minimal launch.yml + params.yml) (#2)"
```

---

### Task 3: Emitter Luria file resolution (local path or blank-tagged SRR)

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/seqera/emitter.py:568-602` (the samplesheet row loop in `emit_nfcore_artifacts`)
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:308-311` (UID-key the curated metadata) and `:430-431` (stop calling ENA)
- Test: `chat_nextseek/tests/test_emitter_launch_split.py`, `chat_nextseek/tests/test_pipeline_agent_tools.py`

**Interfaces:**
- Consumes: `_fastq_from_meta(meta, read_hint)` (unchanged; already prefers a local absolute path over a URL).
- Produces: `emit_nfcore_artifacts` fills a row's `fastq_1`/`fastq_2` from curated metadata looked up by the row's `sample` UID or `accession`; an SRR row with no local path is KEPT with empty fastq columns and its accession preserved (a fetch target), not dropped. The legacy ENA fan-out stays but only fires when `resolutions` are supplied.

- [ ] **Step 1: Write the failing emitter tests** in `tests/test_emitter_launch_split.py` (append):

```python
import csv as _csv
from chat_nextseek.seqera.emitter import emit_nfcore_artifacts


def _read(path):
    with open(path, newline="") as fh:
        return {r["sample"]: r for r in _csv.DictReader(fh)}


def test_luria_local_path_fills_row_without_accession(tmp_path):
    rows = [{"sample": "D.SEQ-1", "strandedness": "auto"}]
    meta = {"D.SEQ-1": {"Link_PrimaryData": "/net/bmc-lab/D.SEQ-1_R1.fastq.gz",
                        "Link_SecondaryData": "/net/bmc-lab/D.SEQ-1_R2.fastq.gz"}}
    res = emit_nfcore_artifacts(tmp_path, pipeline="rnaseq", samplesheet_rows=rows,
                                resolutions=[], accession_metadata=meta, launch_plan=None,
                                tower_env={}, selector_rationale="t")
    out = _read(res.saved_files["samplesheet"])
    assert out["D.SEQ-1"]["fastq_1"] == "/net/bmc-lab/D.SEQ-1_R1.fastq.gz"
    assert out["D.SEQ-1"]["fastq_2"] == "/net/bmc-lab/D.SEQ-1_R2.fastq.gz"
    assert res.samplesheet_row_count == 1


def test_luria_srr_row_kept_blank_and_tagged(tmp_path):
    rows = [{"sample": "D.SEQ-2", "accession": "SRR100", "strandedness": "auto"}]
    res = emit_nfcore_artifacts(tmp_path, pipeline="rnaseq", samplesheet_rows=rows,
                                resolutions=[], accession_metadata={}, launch_plan=None,
                                tower_env={}, selector_rationale="t")
    out = _read(res.saved_files["samplesheet"])
    assert res.samplesheet_row_count == 1              # NOT dropped
    assert out["D.SEQ-2"]["accession"] == "SRR100"
    assert out["D.SEQ-2"]["fastq_1"] == "" and out["D.SEQ-2"]["fastq_2"] == ""


def test_luria_mixed_cohort_local_and_srr(tmp_path):
    rows = [{"sample": "D.SEQ-1", "strandedness": "auto"},
            {"sample": "D.SEQ-2", "accession": "SRR200", "strandedness": "auto"}]
    meta = {"D.SEQ-1": {"Link_PrimaryData": "/net/bmc-x/1_1.fastq.gz",
                        "Link_SecondaryData": "/net/bmc-x/1_2.fastq.gz"}}
    res = emit_nfcore_artifacts(tmp_path, pipeline="rnaseq", samplesheet_rows=rows,
                                resolutions=[], accession_metadata=meta, launch_plan=None,
                                tower_env={}, selector_rationale="t")
    out = _read(res.saved_files["samplesheet"])
    assert out["D.SEQ-1"]["fastq_1"] == "/net/bmc-x/1_1.fastq.gz"
    assert out["D.SEQ-2"]["fastq_1"] == "" and out["D.SEQ-2"]["accession"] == "SRR200"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_emitter_launch_split.py -k "luria_local or luria_srr or luria_mixed" -v`
Expected: FAIL — today the SRR row is dropped (`if not runs: continue`) and the local-only row is passed through without fastq fill.

- [ ] **Step 3: Rewrite the row loop** in `seqera/emitter.py`. Replace the current loop body (lines 572-602, the `for row in samplesheet_rows or []:` block) with:

```python
    for row in samplesheet_rows or []:
        uid = str(row.get("sample") or row.get("Sample") or "")
        acc = row.get("accession") or row.get("Accession") or row.get("ena_accession")
        acc_str = str(acc).strip() if acc else ""
        # Curated local fastq metadata: keyed by leaf UID (path-only samples) or accession.
        sample_meta = (accession_metadata.get(uid)
                       or (accession_metadata.get(acc_str) if acc_str else None) or {})
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
        keep_rows.append(_remap_row_for_pipeline(rewritten, pipeline))
```

- [ ] **Step 4: Run to verify the emitter tests pass**

Run: `uv run pytest tests/test_emitter_launch_split.py -k "luria_local or luria_srr or luria_mixed" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing "ENA not called" test** in `tests/test_pipeline_agent_tools.py` (append):

```python
import json
from pathlib import Path
from chat_nextseek.pipeline import agent_tools as at


class _WSCfg:
    def __init__(self, log_dir):
        self.LOG_DIR = str(log_dir)
        self.TOWER_ENV = {}


def test_write_samplesheet_does_not_resolve_ena(monkeypatch, tmp_path):
    calls = {"n": 0}
    monkeypatch.setattr(at, "resolve_accessions",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or [])
    state = {"resolved": {"uids": ["D.SEQ-2"], "accessions": ["SRR300"]},
             "accession_file_paths": {}}
    tool_input = {"pipeline_key": "rnaseq",
                  "cohorts": [{"label": "c", "rows": [
                      {"sample": "D.SEQ-2", "accession": "SRR300", "strandedness": "auto"}]}]}
    out = json.loads(at.tool_write_samplesheet(_WSCfg(tmp_path), state, tool_input, str(tmp_path)))
    assert out["ok"] is True
    assert calls["n"] == 0                      # ENA route is off
    assert out["total_rows"] == 1               # SRR row kept, not dropped
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_pipeline_agent_tools.py -k does_not_resolve_ena -v`
Expected: FAIL — `resolve_accessions` is still called (`calls["n"] == 1`).

- [ ] **Step 7a: Stop calling ENA** in `pipeline/agent_tools.py`. Replace lines 430-431:

```python
    accs = [r[k] for r in merged_rows for k in _ACC_KEYS if r.get(k)]
    resolutions = resolve_accessions(accs) if accs else []
```

with:

```python
    # ENA route retired: Luria resolves fastqs from a local /net/bmc-* path (filled here)
    # or fetches SRR accessions on-cluster (run.sh fetchngs pre-stage). No ENA URL synthesis.
    # resolve_accessions is left imported but unused for a future ENA re-enable.
    resolutions: list = []
```

- [ ] **Step 7b: UID-key the curated metadata** in `pipeline/agent_tools.py`. Replace lines 308-311:

```python
        _meta = {**(flat if isinstance(flat, dict) else {}), **(leaf.get("metadata") or {})}
        if _meta and accs:
            for _a in accs:
                file_paths_by_acc[str(_a).strip()] = dict(_meta)
```

with:

```python
        _meta = {**(flat if isinstance(flat, dict) else {}), **(leaf.get("metadata") or {})}
        if _meta:
            # Key by leaf UID so a sample with a local /net/bmc-* path but NO accession
            # still reaches the emitter (the samplesheet 'sample' column is the leaf UID),
            # and also by accession for the (dormant) ENA path.
            file_paths_by_acc[str(leaf["uid"])] = dict(_meta)
            for _a in accs:
                file_paths_by_acc[str(_a).strip()] = dict(_meta)
```

- [ ] **Step 8: Run the ENA-off test + the emitter suite**

Run: `uv run pytest tests/test_pipeline_agent_tools.py -k does_not_resolve_ena tests/test_emitter_launch_split.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/seqera/emitter.py \
        chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py \
        chat_nextseek/tests/test_emitter_launch_split.py chat_nextseek/tests/test_pipeline_agent_tools.py
git commit -m "feat(pipeline): Luria file resolution — local path or blank-tagged SRR, UID-keyed meta, ENA off (#2)"
```

---

### Task 4: Rewire `configure_run` to the Luria emit + surface `reference_files`

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:472-535` (`tool_configure_run`) + imports
- Test: `chat_nextseek/tests/test_pipeline_agent_tools.py`

**Interfaces:**
- Consumes: `emit_luria_launch_artifacts` (Task 2), `local_luria_ref_files` (Task 1).
- Produces: `tool_configure_run` no longer needs `TOWER_ENV`; returns `reference_status`, and when it is `"local_luria"` also `"reference_files": {"fasta","gtf"}`. `state["artifacts"]["launch"]` / `["params"]` are set for the Luria submitter.

- [ ] **Step 1: Write the failing test** in `tests/test_pipeline_agent_tools.py` (append):

```python
def test_configure_run_local_luria_without_tower(tmp_path):
    class Cfg:
        LOG_DIR = str(tmp_path)
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nD.SEQ-1,/net/bmc/1.fastq.gz\n")
    state = {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)},
             "bundle_key": "GRCm39", "pipeline_key": "rnaseq"}
    out = json.loads(at.tool_configure_run(
        Cfg(), state, {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["reference_status"] == "local_luria"
    assert out["reference_files"]["fasta"].endswith(".fa.gz")
    assert Path(out["launch_yml"]).exists() and Path(out["params_yml"]).exists()
    assert state["artifacts"]["launch"] and state["artifacts"]["params"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_pipeline_agent_tools.py -k configure_run_local_luria -v`
Expected: FAIL (`reference_files` absent; or an AttributeError reaching Tower staging).

- [ ] **Step 3: Add the import** near the other luria import in `pipeline/agent_tools.py` (line 44 area):

```python
from ..luria.submitter import submit_luria
from ..luria.run_script import local_luria_ref_files
```

- [ ] **Step 4: Rewire the emit + return.** In `tool_configure_run`, replace lines 511-535 (from `tower_env = ...` through the end `return json.dumps({...})`) with:

```python
    result = emit_luria_launch_artifacts(
        base, pipeline=pipeline_key, samplesheet_path=samplesheet,
        launch_plan=plan.model_dump())

    state.setdefault("artifacts", {})
    state["artifacts"]["params"] = result.saved_files.get("params")
    state["artifacts"]["launch"] = result.saved_files.get("launch")
    state["launch_plan"] = plan.model_dump()
    state["pipeline_key"] = pipeline_key

    ref_files = (local_luria_ref_files(merged.get("genome"))
                 if reference_status == "local_luria" else None)
    return json.dumps({
        "ok": True,
        "pipeline_key": pipeline_key,
        "resolved_params": merged,
        "reference_status": reference_status,
        "reference_files": ref_files,
        "bundle_key": bundle_key,
        "params_yml": result.saved_files.get("params"),
        "launch_yml": result.saved_files.get("launch"),
    })
```

Add the import for the new emitter function to the emitter import block (line 33):

```python
from ..seqera.emitter import emit_launch_artifacts, emit_luria_launch_artifacts, emit_nfcore_artifacts
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_pipeline_agent_tools.py -k configure_run_local_luria -v`
Expected: PASS

- [ ] **Step 6: Run the whole pipeline_agent_tools suite** to catch any test that assumed the old Tower return:

Run: `uv run pytest tests/test_pipeline_agent_tools.py -q`
Expected: PASS. If a prior test asserts `reference_status == "igenomes_fallback"` for GRCm39 or `tower_configured`, update it to `local_luria` / remove the `tower_configured` assertion (that key is gone).

- [ ] **Step 7: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py chat_nextseek/tests/test_pipeline_agent_tools.py
git commit -m "feat(pipeline): configure_run emits Luria launch artifacts + surfaces local_luria reference_files (#2)"
```

---

### Task 5: Turn Tower off at the surface (exposure, default mode, prompt, notes)

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:187-199` (`build_pipeline_tool_schemas`)
- Modify: `chat_nextseek/src/chat_nextseek/config.py:36-43` (`detect_pipeline_launch_mode`)
- Modify: `chat_nextseek/src/chat_nextseek/prompts/pipeline_agent.txt` (messaging + strip Tower/Seqera/ENA)
- Modify: `chat_nextseek/src/chat_nextseek/seqera/emitter.py:285-318` (`_build_notes_md`: strip ENA/Tower sections)
- Test: `chat_nextseek/tests/test_pipeline_tool_exposure.py`, a new config test

**Interfaces:**
- Produces: `submit_to_tower` is never exposed by `build_pipeline_tool_schemas`; default `PIPELINE_LAUNCH_MODE` is `"luria"`.

- [ ] **Step 1: Flip the exposure tests** in `tests/test_pipeline_tool_exposure.py`. Replace `test_exposure_tower_only` (16-19) and `test_exposure_both` (27-29) with:

```python
def test_tower_never_exposed_even_when_env_complete():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=True, luria=False)))
    assert "submit_to_tower" not in names          # Tower retired
    assert "submit_to_luria" not in names           # luria env absent here
    assert names == ["resolve_samples", "write_samplesheet", "configure_run", "conclude"]


def test_exposure_luria_only_when_both_env_complete():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=True, luria=True)))
    assert "submit_to_luria" in names and "submit_to_tower" not in names
```

Add a config default test in a new file `tests/test_launch_mode_default.py`:

```python
from chat_nextseek.config import detect_pipeline_launch_mode


def test_default_launch_mode_is_luria():
    assert detect_pipeline_launch_mode({}) == "luria"


def test_invalid_launch_mode_falls_back_to_luria():
    assert detect_pipeline_launch_mode({"PIPELINE_LAUNCH_MODE": "bogus"}) == "luria"


def test_explicit_tower_still_honored():
    assert detect_pipeline_launch_mode({"PIPELINE_LAUNCH_MODE": "tower"}) == "tower"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_pipeline_tool_exposure.py -k "tower_never or luria_only_when_both" tests/test_launch_mode_default.py -v`
Expected: FAIL (submit_to_tower still exposed; default mode still "tower").

- [ ] **Step 3: Drop the exposure** in `pipeline/agent_tools.py`. Replace lines 194-197:

```python
    if getattr(config, "TOWER_ENV_COMPLETE", False):
        tools.append(_SCHEMA_BY_NAME["submit_to_tower"])
    if getattr(config, "LURIA_ENV_COMPLETE", False):
        tools.append(SUBMIT_TO_LURIA_SCHEMA)
```

with:

```python
    # Tower/Seqera retired: Luria is the only exposed launch target. tool_submit_to_tower
    # and its schema stay in place (dormant) for a future re-enable.
    if getattr(config, "LURIA_ENV_COMPLETE", False):
        tools.append(SUBMIT_TO_LURIA_SCHEMA)
```

- [ ] **Step 4: Flip the default mode** in `config.py`. Replace lines 39-42:

```python
    mode = (env.get("PIPELINE_LAUNCH_MODE") or "tower").strip().lower()
    if mode not in _VALID_LAUNCH_MODES:
        print(f"[CONFIG] Invalid PIPELINE_LAUNCH_MODE {mode!r}; defaulting to 'tower'.")
        return "tower"
```

with:

```python
    mode = (env.get("PIPELINE_LAUNCH_MODE") or "luria").strip().lower()
    if mode not in _VALID_LAUNCH_MODES:
        print(f"[CONFIG] Invalid PIPELINE_LAUNCH_MODE {mode!r}; defaulting to 'luria'.")
        return "luria"
```

- [ ] **Step 5: Run the exposure + config tests**

Run: `uv run pytest tests/test_pipeline_tool_exposure.py tests/test_launch_mode_default.py -q`
Expected: PASS. `test_existing_static_schema_unchanged` still passes (the `PIPELINE_TOOL_SCHEMAS` constant is untouched; only the exposure logic changed).

- [ ] **Step 6: Rewrite the prompt messaging** in `prompts/pipeline_agent.txt` (no unit test — verify by inspection; covered later by e2e). Make these exact edits:

Line 17, replace the reference-status sentence

`If reference_status is "igenomes_fallback", say the explicit curated reference bundle isn't configured yet and you're using the iGenomes <genome> key.`

with

`If reference_status is "local_luria", say you're using the local Luria reference for the <genome> genome (name the FASTA/GTF from reference_files). If it is "igenomes_fallback", say no local reference is configured for <genome> yet, so the run will use the iGenomes <genome> key.`

Line 18, replace

`8. On "submit" / "send it": call the appropriate submit tool per the "Choosing a submit tool" rule (submit_to_tower for Tower/Seqera, submit_to_luria for Luria/the cluster), then conclude(outcome="submitted", message=<the result>).`

with

`8. On "submit" / "send it": call submit_to_luria, then conclude(outcome="submitted", message=<the result>).`

Lines 22-31, replace the whole `# Available tools` block (the submit_to_tower bullet and the "Choosing a submit tool" paragraph) with:

```
- submit_to_luria(): submit the built run to MIT's Luria SLURM cluster (ssh + sbatch a
  generated run.sh wrapping `nextflow run`). Only call it AFTER the user confirms they want
  to submit. You may pass SLURM resources (partition/time/cpus/mem) if the user asks. When a
  sample has no local /net/bmc-* fastq path, its SRR accession is fetched on-cluster
  (nf-core/fetchngs) before the pipeline runs — you do not manage that; just build the rows.
```

Line 37, replace `ALWAYS confirm with the user before submit_to_tower or submit_to_luria.` with `ALWAYS confirm with the user before submit_to_luria.`

- [ ] **Step 7: Strip the ENA/Tower sections from notes.md** in `seqera/emitter.py`. In `_build_notes_md`, replace line 285 `lines.append("## Accession resolution (ENA filereport)")` with `lines.append("## Samples")`, and replace the Tower/Seqera block (lines 305-318, from `lines.append("## Tower / Seqera")` through the `else:` branch) with:

```python
    lines.append("## Luria run")
    lines.append("")
    if submitted:
        lines.append("- Submitted to Luria (ssh + sbatch). Run refs:")
        for url in run_urls:
            lines.append(f"  - {url}")
    else:
        lines.append("- Built for Luria submission (submit_to_luria stages run.sh + sbatch).")
```

- [ ] **Step 8: Run the full suite** to confirm nothing else broke:

Run: `uv run pytest tests/ --ignore=tests/evaluator -q`
Expected: PASS (fix any straggler test that asserted the old Tower text/default).

- [ ] **Step 9: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py \
        chat_nextseek/src/chat_nextseek/config.py \
        chat_nextseek/src/chat_nextseek/prompts/pipeline_agent.txt \
        chat_nextseek/src/chat_nextseek/seqera/emitter.py \
        chat_nextseek/tests/test_pipeline_tool_exposure.py chat_nextseek/tests/test_launch_mode_default.py
git commit -m "feat(pipeline): Luria-only surface — drop submit_to_tower exposure, default mode luria, strip Tower/ENA mentions (#2)"
```

---

### Task 6: fetchngs fill/ids helper module

**Files:**
- Create: `chat_nextseek/src/chat_nextseek/luria/fetchngs_helpers.py`
- Test: `chat_nextseek/tests/test_fetchngs_helpers.py`

**Interfaces:**
- Produces: `needs_fetch_accessions(rows: list[dict]) -> list[str]` (bare-id accessions of rows with empty `fastq_1`, deduped, order-preserving); `fill_rows(rows, cache) -> tuple[list[dict], list[str]]` (rows with fastq filled from `<cache>/fastq/<acc>_1.fastq.gz` etc., plus a list of accessions with no fastq found). Runnable as a script: `python3 fetchngs_helpers.py ids` (writes `ids.csv`) and `python3 fetchngs_helpers.py fill <cache>` (rewrites `samplesheet.csv`, exit 1 on any missing).

- [ ] **Step 1: Write the failing tests** `tests/test_fetchngs_helpers.py`:

```python
from pathlib import Path
from chat_nextseek.luria.fetchngs_helpers import needs_fetch_accessions, fill_rows


def test_needs_fetch_accessions_selects_blank_fastq_srr_rows():
    rows = [
        {"sample": "a", "accession": "SRR1", "fastq_1": ""},
        {"sample": "b", "accession": "SRR2", "fastq_1": "/net/bmc/b.fastq.gz"},  # already local
        {"sample": "c", "accession": "", "fastq_1": ""},                          # no accession
        {"sample": "d", "accession": "SRR1", "fastq_1": ""},                      # dup
    ]
    assert needs_fetch_accessions(rows) == ["SRR1"]


def test_needs_fetch_accessions_rejects_non_bare_ids():
    assert needs_fetch_accessions([{"accession": "../evil", "fastq_1": ""}]) == []


def test_fill_rows_paired(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRR1_1.fastq.gz").write_text("x")
    (tmp_path / "fastq" / "SRR1_2.fastq.gz").write_text("x")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR1", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert rows[0]["fastq_1"].endswith("SRR1_1.fastq.gz")
    assert rows[0]["fastq_2"].endswith("SRR1_2.fastq.gz")


def test_fill_rows_single_end(tmp_path):
    (tmp_path / "fastq").mkdir()
    (tmp_path / "fastq" / "SRR9.fastq.gz").write_text("x")
    rows, missing = fill_rows([{"sample": "a", "accession": "SRR9", "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == [] and rows[0]["fastq_1"].endswith("SRR9.fastq.gz") and rows[0]["fastq_2"] == ""


def test_fill_rows_reports_missing(tmp_path):
    (tmp_path / "fastq").mkdir()
    rows, missing = fill_rows([{"sample": "a", "accession": "SRRX", "fastq_1": ""}], str(tmp_path))
    assert missing == ["SRRX"]


def test_fill_rows_leaves_local_rows_untouched(tmp_path):
    rows, missing = fill_rows([{"sample": "a", "fastq_1": "/net/bmc/a.fastq.gz"}], str(tmp_path))
    assert missing == [] and rows[0]["fastq_1"] == "/net/bmc/a.fastq.gz"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_fetchngs_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: chat_nextseek.luria.fetchngs_helpers`

- [ ] **Step 3: Create the module** `luria/fetchngs_helpers.py`:

```python
#!/usr/bin/env python3
"""Fixed, non-interpolated helper STAGED to Luria and invoked from run.sh:

  python3 fetchngs_helpers.py ids           -> write ids.csv from samplesheet.csv
  python3 fetchngs_helpers.py fill <cache>  -> fill fastq_1/2 in samplesheet.csv from <cache>/fastq

Because it is staged verbatim (never string-formatted with run-specific values), it
carries no shell-injection surface. Accessions are validated as bare ids before any
path is constructed, so a malformed accession cannot cause path traversal.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

_ACC_RE = re.compile(r"^[A-Za-z0-9]+$")


def needs_fetch_accessions(rows: list[dict]) -> list[str]:
    """Bare-id accessions of rows with an empty fastq_1, deduped, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        if (row.get("fastq_1") or "").strip():
            continue
        acc = (row.get("accession") or "").strip()
        if acc and _ACC_RE.match(acc) and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def _paths_for(cache: str, acc: str) -> tuple[str | None, str]:
    fq = Path(cache) / "fastq"
    r1, r2 = fq / f"{acc}_1.fastq.gz", fq / f"{acc}_2.fastq.gz"
    se = fq / f"{acc}.fastq.gz"
    if r1.exists():
        return str(r1), (str(r2) if r2.exists() else "")
    if se.exists():
        return str(se), ""
    return None, ""


def fill_rows(rows: list[dict], cache: str) -> tuple[list[dict], list[str]]:
    """Fill fastq_1/fastq_2 for blank SRR rows from the cache; return (rows, missing_accessions)."""
    missing: list[str] = []
    for row in rows:
        if (row.get("fastq_1") or "").strip():
            continue
        acc = (row.get("accession") or "").strip()
        if not (acc and _ACC_RE.match(acc)):
            continue
        f1, f2 = _paths_for(cache, acc)
        if f1 is None:
            missing.append(acc)
            continue
        row["fastq_1"] = f1
        row["fastq_2"] = f2
    return rows, missing


def _main_ids(sheet: str = "samplesheet.csv", ids: str = "ids.csv") -> int:
    with open(sheet, newline="") as fh:
        rows = list(csv.DictReader(fh))
    accs = needs_fetch_accessions(rows)
    Path(ids).write_text("".join(a + "\n" for a in accs), encoding="utf-8")
    return 0


def _main_fill(cache: str, sheet: str = "samplesheet.csv") -> int:
    with open(sheet, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames or []
    rows, missing = fill_rows(rows, cache)
    if missing:
        sys.stderr.write(f"fetchngs fill: no fastqs found in {cache}/fastq for {missing}\n")
        return 1
    with open(sheet, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ids":
        sys.exit(_main_ids())
    if cmd == "fill":
        sys.exit(_main_fill(sys.argv[2]))
    sys.stderr.write("usage: fetchngs_helpers.py ids | fill <cache>\n")
    sys.exit(2)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_fetchngs_helpers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/luria/fetchngs_helpers.py chat_nextseek/tests/test_fetchngs_helpers.py
git commit -m "feat(luria): fetchngs_helpers — ids extraction + cache-path samplesheet fill (#2)"
```

---

### Task 7: Render the fetchngs pre-block into run.sh

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/luria/templates/run.sh.tmpl` (add the `{{FETCHNGS_BLOCK}}` slot after `cd {{RUN_DIR}}`)
- Modify: `chat_nextseek/src/chat_nextseek/luria/run_script.py` (`render_run_script` gains fetch kwargs + a block template + validators)
- Test: `chat_nextseek/tests/test_luria_run_script.py`

**Interfaces:**
- Consumes: `fetchngs_helpers.py` (staged by Task 8; the block just invokes it by relative path).
- Produces: `render_run_script(..., needs_fetch: bool = False, fastq_cache: str | None = None, fetchngs_revision: str = "1.12.0")` renders the fetch pre-block when `needs_fetch`, else empty (run.sh byte-identical to today for pure-local cohorts).

- [ ] **Step 1: Write the failing render tests** in `tests/test_luria_run_script.py` (append):

```python
import pytest
from chat_nextseek.luria.run_script import render_run_script


def _render(**over):
    kw = dict(job_name="j", pipeline="nf-core/rnaseq", revision="3.18.0",
              run_dir="/w/runs/j", work_dir="/w/work/j", singularity_cache="/w/sc",
              genome="GRCm39", resources=None, refs_root="/w/refs")
    kw.update(over)
    return render_run_script(**kw)


def test_render_no_fetch_block_by_default():
    out = _render()
    assert "fetchngs" not in out
    assert "nextflow run nf-core/rnaseq -r 3.18.0" in out   # pipeline block intact


def test_render_includes_fetch_block_when_needed():
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert "nextflow run nf-core/fetchngs -r 1.12.0" in out
    assert "fetchngs_helpers.py ids" in out
    assert "fetchngs_helpers.py fill" in out
    assert "/w/fastq_cache" in out
    assert "nextflow run nf-core/rnaseq -r 3.18.0" in out   # pipeline block STILL intact + unchanged


def test_render_fetch_block_rejects_bad_cache():
    with pytest.raises(ValueError):
        _render(needs_fetch=True, fastq_cache="/w; rm -rf /", fetchngs_revision="1.12.0")


def test_render_fetch_block_rejects_bad_revision():
    with pytest.raises(ValueError):
        _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0; evil")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_luria_run_script.py -k "fetch_block or no_fetch" -v`
Expected: FAIL — `render_run_script` has no `needs_fetch` kwarg (TypeError).

- [ ] **Step 3: Add the slot to the template.** In `luria/templates/run.sh.tmpl`, change line 29 from:

```
cd {{RUN_DIR}}
```

to:

```
cd {{RUN_DIR}}
{{FETCHNGS_BLOCK}}
```

- [ ] **Step 4: Add the block template + render logic** to `luria/run_script.py`. Add a module-level constant near `_TEMPLATE` (line 85):

```python
_FETCHNGS_BLOCK_TMPL = """
# --- fetchngs pre-stage: fetch SRR-only rows to the shared cache, then fill the sheet ---
# Rows with a local /net/bmc-* fastq are already filled and untouched; only rows with an
# empty fastq_1 + an SRA accession are fetched. fetchngs_helpers.py is staged beside run.sh.
CACHE="{FASTQ_CACHE}"
mkdir -p "$CACHE/fastq" "$CACHE/work"
python3 fetchngs_helpers.py ids
if [ -s ids.csv ]; then
  need=0
  while read acc; do ls "$CACHE"/fastq/${{acc}}*.fastq.gz >/dev/null 2>&1 || need=1; done < ids.csv
  if [ "$need" = "1" ]; then
    nextflow run nf-core/fetchngs -r {FETCHNGS_REVISION} -profile singularity \\
      --input ids.csv --outdir "$CACHE" -w "$CACHE/work" -resume
  fi
  python3 fetchngs_helpers.py fill "$CACHE"
fi
"""
```

Change the `render_run_script` signature (line 238-241) to add the fetch kwargs:

```python
def render_run_script(*, job_name: str, pipeline: str, revision: str, run_dir: str,
                      work_dir: str, singularity_cache: str, genome: str,
                      resources: dict | None, refs_root: str | None = None,
                      aligner: str | None = None, working: str | None = None,
                      needs_fetch: bool = False, fastq_cache: str | None = None,
                      fetchngs_revision: str = "1.12.0") -> str:
```

Then, just before the `mapping = {` dict is built (line 261), add:

```python
    fetchngs_block = ""
    if needs_fetch:
        if not fastq_cache or not _REFS_ROOT_RE.fullmatch(str(fastq_cache)):
            raise ValueError(f"invalid fastq_cache {fastq_cache!r}")
        fq_rev = validate_revision(fetchngs_revision)
        fetchngs_block = _FETCHNGS_BLOCK_TMPL.format(
            FASTQ_CACHE=str(fastq_cache).rstrip("/"), FETCHNGS_REVISION=fq_rev)
```

And add the slot to the `mapping` dict (alongside the existing keys):

```python
        "FETCHNGS_BLOCK": fetchngs_block,
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_luria_run_script.py -k "fetch_block or no_fetch" -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the whole luria run_script suite** (the empty-slot case must not perturb existing renders):

Run: `uv run pytest tests/test_luria_run_script.py -q`
Expected: PASS. Existing tests render with `needs_fetch=False`, so `{{FETCHNGS_BLOCK}}` becomes `""` and the only new line is a blank line after `cd` (harmless). If a byte-exact snapshot test exists, update its expected text to include the empty slot line.

- [ ] **Step 7: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/luria/templates/run.sh.tmpl \
        chat_nextseek/src/chat_nextseek/luria/run_script.py \
        chat_nextseek/tests/test_luria_run_script.py
git commit -m "feat(luria): render fetchngs pre-block into run.sh (shared cache, fail-closed slots) (#2)"
```

---

### Task 8: Submitter detects fetch need + stages the helper

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/luria/submitter.py` (`_submit_one`, ~lines 95-169; add a `_sheet_needs_fetch` helper + imports)
- Test: `chat_nextseek/tests/test_luria_submitter.py`

**Interfaces:**
- Consumes: `render_run_script(..., needs_fetch, fastq_cache, fetchngs_revision)` (Task 7), `needs_fetch_accessions` (Task 6), `NFCORE_PIPELINE_CATALOG` (for the fetchngs revision).
- Produces: `_submit_one` passes `needs_fetch=True` + `fastq_cache=<working>/fastq_cache` + the catalog fetchngs revision to `render_run_script` when the staged samplesheet has a blank-fastq SRR row, and scp's `fetchngs_helpers.py` into the run dir in that case.

- [ ] **Step 1: Write the failing test** in `tests/test_luria_submitter.py` (append). It captures the kwargs `render_run_script` receives and asserts the helper is staged:

```python
import csv
from pathlib import Path
import chat_nextseek.luria.submitter as sub


def _write_sheet(path, rows, fields):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def _patch_ssh(monkeypatch, scps):
    monkeypatch.setattr(sub, "prepare_key", lambda key: "/tmp/key")
    monkeypatch.setattr(sub, "ssh_run", lambda env, cmd, **kw: "Submitted batch job 42")
    monkeypatch.setattr(sub, "scp_file",
                        lambda env, src, dst, **kw: scps.append((Path(src).name, dst)))


def test_submit_stages_helper_and_sets_needs_fetch_for_srr_sheet(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(sub, "render_run_script",
                        lambda **kw: captured.update(kw) or "#!/bin/bash\n")
    scps = []
    _patch_ssh(monkeypatch, scps)
    sheet = tmp_path / "samplesheet.csv"
    _write_sheet(sheet, [{"sample": "D.SEQ-2", "accession": "SRR9", "fastq_1": "", "fastq_2": ""}],
                 ["sample", "accession", "fastq_1", "fastq_2"])
    launch = tmp_path / "launch.yml"
    launch.write_text("launch:\n  - {name: r, pipeline: nf-core/rnaseq, revision: 3.18.0}\n")
    env = {"user": "u", "key": "/k", "working_path": "/work", "host": "luria.mit.edu"}
    runs = sub.submit_luria(launch, luria_env=env, samplesheet_local=str(sheet))
    assert runs and runs[0]["job_id"] == "42"
    assert captured["needs_fetch"] is True
    assert captured["fastq_cache"] == "/work/fastq_cache"
    assert any(name == "fetchngs_helpers.py" for name, _ in scps)   # helper staged


def test_submit_no_fetch_for_all_local_sheet(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(sub, "render_run_script",
                        lambda **kw: captured.update(kw) or "#!/bin/bash\n")
    scps = []
    _patch_ssh(monkeypatch, scps)
    sheet = tmp_path / "samplesheet.csv"
    _write_sheet(sheet, [{"sample": "D.SEQ-1", "accession": "", "fastq_1": "/net/bmc/1.fastq.gz", "fastq_2": ""}],
                 ["sample", "accession", "fastq_1", "fastq_2"])
    launch = tmp_path / "launch.yml"
    launch.write_text("launch:\n  - {name: r, pipeline: nf-core/rnaseq, revision: 3.18.0}\n")
    env = {"user": "u", "key": "/k", "working_path": "/work", "host": "luria.mit.edu"}
    sub.submit_luria(launch, luria_env=env, samplesheet_local=str(sheet))
    assert captured["needs_fetch"] is False
    assert not any(name == "fetchngs_helpers.py" for name, _ in scps)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_luria_submitter.py -k "stages_helper or no_fetch_for_all_local" -v`
Expected: FAIL — `render_run_script` is called without `needs_fetch` (KeyError on `captured["needs_fetch"]`).

- [ ] **Step 3: Add imports + a fetch-detection helper** to `luria/submitter.py`. Extend the existing imports (lines 24-25):

```python
from .run_script import render_run_script, render_luria_config, render_process_config, sanitize_job_name
from .fetchngs_helpers import needs_fetch_accessions
from .ssh import prepare_key, ssh_run, scp_file
from ..seqera.catalog import NFCORE_PIPELINE_CATALOG
```

Add a helper above `_submit_one`:

```python
def _sheet_needs_fetch(sheet_path: str) -> bool:
    """True when the staged samplesheet has at least one blank-fastq SRR row to fetch."""
    import csv
    try:
        with open(sheet_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return False
    return bool(needs_fetch_accessions(rows))
```

- [ ] **Step 4: Wire it into `_submit_one`.** After `refs_root = f"{working}/refs"` (line 132), add:

```python
    needs_fetch = _sheet_needs_fetch(local_sheet)
    fastq_cache = f"{working}/fastq_cache"
    fetchngs_rev = NFCORE_PIPELINE_CATALOG.get("fetchngs", {}).get("default_revision", "1.12.0")
```

Change the `render_run_script(...)` call (lines 135-139) to pass the fetch kwargs:

```python
        run_sh = render_run_script(
            job_name=safe, pipeline=pipeline, revision=revision, run_dir=remote_run_dir,
            work_dir=work_dir, singularity_cache=cache_dir, genome=run_genome, resources=resources,
            refs_root=refs_root, aligner=(launch_params or {}).get("aligner"), working=working,
            needs_fetch=needs_fetch, fastq_cache=fastq_cache, fetchngs_revision=fetchngs_rev,
        )
```

After the samplesheet scp line (line 153, `scp_file(luria_env, local_sheet, f"{remote_run_dir}/samplesheet.csv", ...)`), stage the helper when fetching:

```python
        if needs_fetch:
            helper = str(Path(__file__).parent / "fetchngs_helpers.py")
            scp_file(luria_env, helper, f"{remote_run_dir}/fetchngs_helpers.py", key_path=key_path)
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_luria_submitter.py -k "stages_helper or no_fetch_for_all_local" -v`
Expected: PASS

- [ ] **Step 6: Run the full submitter + luria suite**

Run: `uv run pytest tests/test_luria_submitter.py tests/test_luria_run_script.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/luria/submitter.py chat_nextseek/tests/test_luria_submitter.py
git commit -m "feat(luria): submitter detects SRR fetch need, stages fetchngs_helpers, threads cache+revision (#2)"
```

---

## Final verification

- [ ] **Full suite:** from `chat_nextseek/`, run `uv run pytest tests/ --ignore=tests/evaluator -q`. Expected: PASS.
- [ ] **Routing/agent e2e (optional, budget-gated):** `uv run e2e.py --family pipeline` (or the closest family) to confirm the pipeline agent still routes and builds. Reference messaging and the Luria-only submit surface are exercised here.
- [ ] **Manual Luria smoke (owner-driven, out of automated scope):** a mixed cohort (one `/net/bmc-*` sample + one SRR) submitted via the chat UI; confirm run.sh contains the fetchngs pre-block, the shared cache populates under `<WORKING>/fastq_cache/fastq/`, the samplesheet fills, and the pipeline block runs. Validate the real fetchngs `1.12.0` output filename convention (`<SRR>_1.fastq.gz`) against `_paths_for`.

## Notes for the executor

- Do **not** delete `seqera/ena.py`, `tool_submit_to_tower`, the `submit_to_tower` schema, or `prompts/seqera_agent.txt`. Tasks 3-5 only stop calling / stop exposing them.
- `emit_launch_artifacts` (the Tower emitter) is left fully intact; Task 4 routes `configure_run` to the new `emit_luria_launch_artifacts` instead.
- If any test not listed here fails after a task because it hard-coded `igenomes_fallback` for a registered genome, `tower_configured`, or `submit_to_tower` exposure, update it to the new behavior (`local_luria`, no `tower_configured`, no Tower exposure) in the same task and note it in the commit body.
