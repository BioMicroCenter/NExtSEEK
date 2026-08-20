# Plan 018 V4-0 — exact port set + reviewed provenance

**Recorded:** 2026-08-11  
**Implementation base:** `origin/dev@6881b6a870d68a6efaeb483b111cb9244488c5f9`  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018`  
**Authority:** V4-0 (“Enumerate every selected commit/file and review its diff onto the exact base”)

This records the **exact port set** and provenance review. It does **not** perform the V4-2 port.

## Bound sources (user-approved)

| Role | Identity |
|---|---|
| Eval vendor | `/home/taishajo/work/dmac-assistant` @ `dcca50c187890dc93659e5594810179793bb94eb` (`origin/main`) |
| Nessie paired/ordinary harness tip | `origin/dev-v4-merge@3fe71670b954b78573795ac087caedec5eac2b97` |
| set3_final producer SHA (run metadata) | `26609bdba100793ee33e1bc6e050a7a373a41419` |
| Historical plan-cited reviewed ordinary tip | `origin/dev-v3-merge@0aca6fc46aa086210c3ed238dc6b4adb687f8ac0` (superseded as tip; still the “was reviewed” baseline for delta review) |

## Nessie harness (`nessie_tests/**`)

### Identity

| Ref | Git tree `nessie_tests` | Non-bytecode file count |
|---|---|---:|
| producer `26609bd` | `c1f468de3d52284d05438a95a35ed051d64cf571` | 92 |
| `origin/dev-v3-merge` tip `bac32335` | **same** `c1f468de…` | 92 |
| approved tip `3fe71670` | **same** `c1f468de…` | 92 |
| historical `0aca6fc` | `136a59c2401ede1c60ed7689494a2bfad2aad113` | (ordinary-only generation; see delta) |
| implementation base `6881b6a8` | **absent** (0 files) | 0 |

Machine TSV (every tip file vs base): `evidence/plan018-v4-0-nessie-port-file-enum.tsv`  
→ **92/92** = `absent_on_base_add` (entire tree is additive onto `6881b6a8`).

### Diff onto exact base (`6881b6a8`)

Because the base has **no** `nessie_tests/`, the reviewed base-diff is:

- **Add** the entire 92-file tree from tip `3fe71670` (git tree `c1f468de…`).
- **Companion add:** `nextseek_api/management/commands/nessie.py` (present on tip; **absent** on base). Existing `management/__init__.py` and `commands/__init__.py` already exist on base (no content change required for package markers).

No file may be copied from the live `/app` tree or the SA clone; only these git objects.

### Newer-source review vs plan’s `0aca6fc`

V4-0 requires any newer source than the historically reviewed `0aca6fc` ordinary harness to receive the same review. Tip `3fe71670` **is** newer and includes the paired/Bayesian generation.

| Delta class | Count | Artifact |
|---|---:|---|
| Added since `0aca6fc` | 36 | `evidence/plan018-v4-0-nessie-added-since-0aca6fc.txt` |
| Removed since `0aca6fc` | 2 | `evidence/plan018-v4-0-nessie-removed-since-0aca6fc.txt` |
| Content-changed (same path) | 30 | `evidence/plan018-v4-0-nessie-changed-since-0aca6fc.txt` |
| `git diff --stat` | 68 files, +49129/−4877 | tip vs `0aca6fc` on `nessie_tests` |

**Material additions since `0aca6fc` (port-critical):** paired producer surfaces including `bayesian.py`, `bayes_manifest.py`, `preflight.py`, `collect.py`, `export.py`, `corpus.json`, `FAMILIES.json`, plus supporting tests/docs under `nessie_tests/`.

**Review verdict for V4-0 provenance (not V4-2 DONE):** tip is authorized; tree is bit-identical to the set3 producer and current v3 tip; onto-base effect is a clean additive import of that exact tree + `nessie` management command. V4-2 still owns hermetic port/proof and must not treat live `/app` as source.

### Explicit non-ports (nessie lane)

- Live container `/app/nessie_tests` and SA clone trees — evidence only.
- Replacing/rerunning `set3_final` — void (V13-B).
- Renaming ordinary `manifest.json` as Bayesian evidence — void (V4-2).

## Eval vendor (`dmac-assistant@dcca50c`)

File enum: `evidence/plan018-v4-0-eval-vendor-file-enum.txt` (**55** paths).

| Surface | Paths | Destination owner |
|---|---|---|
| Judge + models | `tools/e2e/functional_evaluator.py`, `functional_evaluator_models.py` | Task 6 → `nextseek_api/eval/` |
| HiBayes fit packages | `src/dmac_assistant/eval/hibayes_*` | Task 6 |
| Exporter / enums / expected_behavior / functional_inputs | `tools/hibayes/*` except validator | Task 6 |
| **Do not port** | `tools/hibayes/artifact_validator.py` (+ its tests) | V9-A → fresh `artifact_validity.py` (Task 7b) |
| Eval image | `Dockerfile.hibayes-eval` | Task 6 → `docker/eval/Dockerfile` |

**Diff onto base:** base has no vendored `nextseek_api/eval/` product tree for these packages (only plan-018 reference artifacts on `origin/dev`). Port is additive under Task 6 after Phase-2 gate; import-path rewrites only.

## Provenance completeness for V4-0 DONE

| Requirement | Status |
|---|---|
| Exact approved base SHA recorded | yes (`6881b6a8`) |
| Exact port sources recorded | yes (bindings + this doc) |
| Every selected nessie file enumerated vs base | yes (92 adds) |
| Newer tip reviewed vs historical `0aca6fc` | yes (36 add / 2 del / 30 change) |
| Eval vendor file set enumerated | yes (55 paths; validator carved out) |
| Actual tree copy / product port | **not** this gate — **V4-2 / Task 6** |
