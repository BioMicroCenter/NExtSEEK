# HiBayes runtime-reliability analysis

This package estimates the **posterior probability of runtime success** for the
DMAC headless agent, broken down by `task_family`. It consumes the per-row CSV
produced by `tools/hibayes/exporter.py` from a headless-batch evidence bundle,
fits a hierarchical Bayesian model using
[HiBayes](https://github.com/UKGovernmentBEIS/hibayes), and renders a
self-contained HTML report with Chart.js visualisations and an embedded
`hibayes-manifest` JSON payload for downstream tooling.

This README is the in-tree user-facing documentation for the pipeline. The
authoritative architectural source is the plan at
`.claude/plans/hibayes-runtime-reliability-2026-05-09.md` (DD-01 through DD-13,
Decision Log DL-001..DL-008, Section 0 "Out of scope", Section 11 Q1/Q2).

---

## What this answers

This pipeline estimates the **posterior probability of runtime success** for
each `task_family` in a headless-batch run. "Runtime success" is defined by the
`runtime_success` column of `data/hibayes_eval_rows.csv` — a binary 0/1 flag
emitted by `tools/hibayes/exporter.py` that is `1` when the headless agent
process completed without crashing, timing out, or returning no answer, and `0`
otherwise. See `tools/hibayes/README.md` for the upstream column semantics.

Per `task_family`, the report shows:

- `posterior_mean` — Bayesian point estimate of the family's true success rate.
- `posterior_median` — equivalent point estimate; preferred under skew.
- `hdi_low` / `hdi_high` — the 94% **highest-density interval** (HDI).
- `hdi_80_low` / `hdi_80_high` — the 80% HDI, when present in
  `diagnostics_summary["hdi_80"]`.
- `p_success_lt_strong` — `P(success < 0.90)`; the posterior probability the
  true rate is below the configurable strong-reliability floor.
- `p_success_lt_acceptable` — `P(success < 0.80)`; below the configurable
  acceptable floor.
- A `ReliabilityBand` classification (`Reliable` / `Watch` / `Brittle` /
  `TooUncertain`), defined in §"Interpreting the report" below.

### Worked example

Take family `alpha` with `n_total=20`, `n_success=19`. The model returns
`posterior_mean = 0.96`, 94% HDI `[0.91, 0.99]`, `P(success<0.90) = 0.05`,
`P(success<0.80) = 0.01`, band `Reliable`. Plain reading: "given the 19/20 we
observed and the model's hierarchical prior, our best single-number guess for
alpha's true success rate is 96%; we are 94% sure the true rate lies in
`[0.91, 0.99]`; and the chance the true rate is below 0.90 is roughly 5%."

(These are illustrative example values from the renderer test fixture, not real
MCMC outputs from any production run. The actual values in
`out/hibayes_runtime_reliability/posterior_task_family_reliability.csv` will
differ.)

**Design source**: plan DD-01 ("Reuse the existing row schema, do not
duplicate") and plan Section 0 ("Out of scope (v1)") pin the framing —
**Runtime-success only — NOT answer correctness.**

---

## What this does NOT answer

This analysis does **not** score answer **correctness**, **accuracy**,
helpfulness, or whether the agent was **useful**. Nothing in this report
distinguishes "agent ran to completion and produced a wrong answer" from "agent
ran to completion and produced a correct answer" — both increment the
`runtime_success = 1` count.

The following columns exist on every `RuntimeEvalRow` (and in the CSV) but are
**deliberately not modelled as covariates in v1** (per DD-11 and Section 0
"Out of scope (v1)"):

- `tool_calls_total` — observable per row, not modelled in v1; future row-level
  predictor.
- `artifact_count` — observable per row, not modelled in v1; future row-level
  predictor. **Important caveat**: per the `tools/hibayes/` `artifact_count`
  semantics note in `.claude/CLAUDE.md`, this column is the count of
  user-facing deliverable files (allowlisted extensions only — `.xlsx`,
  `.html`, `.pdf`, `.png`, etc.; `.json` / `.txt` / `.log` are filtered out)
  promoted into `artifacts/<query_id>/` by `tools/e2e/run_batch.py`. It is
  zero across an entire report when the upstream batch ran in
  per-query-tempdir mode (no `--scratch-dir` + `--output-dir`). Do **not**
  read it as "everything the agent wrote" or as a clean proxy for "agent
  productivity".
- `cost_usd` — observable, not modelled. May be `None`; aggregate-only.
- `latency_seconds` — observable, not modelled. Aggregate-only.
- `is_opus` — preserved on every row per DD-11; not a covariate in v1; future
  row-level predictor with a worked example in §"Adding predictors later".
- `image` — observable, not modelled.

The report performs **no LLM judging**, **no semantic comparison** against
expected answers, **no calibration** of model temperature, and **no
across-model A/B inference** (an `is_opus` covariate would be the v1+1 step
toward this).

The omission is deliberate, not accidental. The plan closes scope explicitly
in Section 0 "Out of scope (v1)" and DD-11; this section is not flagging a
deferred work item — it is documenting that v1 is intentionally narrow.

---

## How to run

This section covers host-side prerequisites and CSV generation. The actual
analysis run lives in the next section (Docker).

### Prerequisites

- Python 3.12 (see repo `.python-version`).
- `uv` installed.
- Repo cloned.
- **Docker daemon running** (Docker Desktop on macOS; `dockerd` on Linux). The
  jax-dependent steps (HiBayes / numpyro / arviz / matplotlib / jinja2) all
  run inside the `hibayes-runtime-reliability:dev` image per Amendment 1 +
  DD-13 host-clean refinement; the host venv carries none of those packages.
- **No LLM credentials. No AWS. No NExtSEEK.** Per plan §9
  "Credentials / secrets — None." This pipeline is offline at runtime.

### One-time host install (bridge venv)

```bash
uv sync
```

> **Do NOT run `uv add --group eval` on the host.** The `eval` group does not
> exist in the host `pyproject.toml` — eval dependencies live only inside the
> Docker image per Amendment 1 + DD-13 refinement. (Plan §9's pre-Amendment-1
> `uv sync --group eval` instruction is superseded.)

### One-time image build

```bash
make hibayes-eval-build
```

Equivalent direct invocation:

```bash
docker build --platform linux/amd64 \
    --build-arg HIBAYES_SHA=$(git ls-remote \
        https://github.com/UKGovernmentBEIS/hibayes.git HEAD | awk '{print $1}') \
    -f Dockerfile.hibayes-eval \
    -t hibayes-runtime-reliability:dev .
```

This pulls HiBayes from a git URL (DD-08 as superseded by DD-13; the resolved
sha is recorded at DL-004 of the plan file) and installs ~100MB of HiBayes
transitive dependencies (`dvc`, `dvc-s3`, `pyarrow`, `fastparquet`, `textual`,
`plotext`, `dill`, `krippendorff`) per plan Section 11 R-DV-04 (advisory) —
that is expected. First build is roughly 5–10 minutes cold; subsequent
rebuilds reuse the layer cache.

### Generate the input CSV (host-side, pure-Python)

```bash
uv run python -m tools.hibayes.exporter evidence/headless/<RUN_ID>/report.html
```

This is one-time per evidence bundle; it runs on the host venv with no Docker
dependency. The resolved CSV SHA-256 for the 224850Z reference bundle is
recorded at DL-005 of the plan file for reproducibility. T02 (already merged)
seeded `data/hibayes_eval_rows.csv` from that reference; re-run this command
when pointing at a different bundle.

### Outputs

Per plan §5, the analysis emits these six paths under
`out/hibayes_runtime_reliability/` on the host (mounted at
`/work/out/hibayes_runtime_reliability/` inside the container):

- `report.html` — self-contained HTML report.
- `task_family_aggregates.csv` — per-family aggregate counts.
- `posterior_task_family_reliability.csv` — per-family posterior summaries +
  band classification.
- `diagnostics.json` — HiBayes checker outputs (r_hat, ESS, divergences, LOO,
  WAIC, predictive plots).
- `config.resolved.yaml` — fully-resolved configuration the run actually used.
- `analysis_state/` — HiBayes `ModelAnalysisState` directory (DD-05).

The directory is overwritten on each run (DD-09: single fixed dir,
deterministic filenames). It is gitignored.

### Offline / network statement

The analysis itself requires **no network egress at runtime**. Chart.js v4
loads from `https://cdn.jsdelivr.net/npm/chart.js@4` **in your browser** when
you open the rendered `report.html` — viewing the report on an air-gapped
machine will show the report skeleton without charts. The build step does NOT
fetch the CDN. The image build is the only step that requires network access
(to fetch HiBayes from GitHub and resolve PyPI deps).

### Exit codes

Per plan §3 T07:

- `0` — success.
- `1` — input-validation failure.
- `2` — HiBayes failure.

---

## Running the analysis (Docker)

The actual analysis runs inside the `hibayes-runtime-reliability:dev` image
because the eval group's `jax` / `jaxlib` dependencies do not ship
`macosx_x86_64` wheels (this is the entire reason for the host-clean +
container-run split — see the Amendment Log in the plan file). The existing
`dmac-assistant` bridge image is unrelated and is not modified by this
pipeline.

### Canonical invocation — Makefile

The recommended path for production runs:

```bash
make hibayes-eval
# or with overrides:
make hibayes-eval INPUT=data/hibayes_eval_rows.csv OUT=out/hibayes_runtime_reliability
```

Defaults are `INPUT=data/hibayes_eval_rows.csv` and
`OUT=out/hibayes_runtime_reliability`. The target shells out to
`scripts/run_hibayes_eval.sh`.

### Wrapper — `scripts/run_hibayes_eval.sh`

Direct path, useful for ad-hoc test invocations:

```bash
scripts/run_hibayes_eval.sh python -m dmac_assistant.eval.hibayes_runtime_reliability.run_hibayes \
    --input data/hibayes_eval_rows.csv \
    --out out/hibayes_runtime_reliability
```

The wrapper forwards all arguments to `uv run` inside the container with the
canonical mount layout shown below. Override the image with `IMAGE=...` or the
repo root with `REPO=...` if needed.

### Default mount paths

Every `/work/...` path inside the container maps back to a host path. Users
should know this so they can see where outputs land and what is read-only.

| Container path  | Host path                                                                       | Mode | Purpose                                                                                                                  |
|-----------------|---------------------------------------------------------------------------------|------|--------------------------------------------------------------------------------------------------------------------------|
| `/work/src`     | `${REPO}/src`                                                                   | `ro` | Python package source (mounted live so code edits take effect without rebuild).                                          |
| `/work/tests`   | `${REPO}/tests`                                                                 | `ro` | Pytest test sources (same — for executing the in-container test suite).                                                  |
| `/work/data`    | `${REPO}/data`                                                                  | `ro` | Input CSV (`hibayes_eval_rows.csv` and any future inputs).                                                               |
| `/work/out`     | `${REPO}/out`                                                                   | `rw` | Pipeline outputs: `hibayes_runtime_reliability/` artifacts AND `.coverage` data.                                         |
| `/work/config`  | `${REPO}/src/dmac_assistant/eval/hibayes_runtime_reliability/config`            | `ro` | Packaged YAML config (`hibayes_runtime_reliability.yaml`) — the default location `run_hibayes.py` reads when `--config` is omitted. |
| `/work/templates` | `${REPO}/src/dmac_assistant/eval/hibayes_runtime_reliability/templates`       | `ro` | Jinja2 template directory (`hibayes_runtime_report.html.j2`).                                                            |
| `/work/tools`   | `${REPO}/tools`                                                                 | `ro` | `tools/` package (hibayes exporter, FailureMode) — required at import time by `models.py` (Amendment 3, commit `4a01ce4`). |

The wrapper sets `--platform linux/amd64` unconditionally so the image runs
the same way on Intel and Apple-Silicon Macs (with QEMU emulation on the
latter — slower but functional).

### Running just the tests

For developer / CI ad-hoc invocations:

```bash
scripts/run_hibayes_eval.sh pytest tests/unit/eval -q
scripts/run_hibayes_eval.sh pytest tests/integration/test_hibayes_pipeline.py -v
```

### Troubleshooting

- *`Unable to find image 'hibayes-runtime-reliability:dev' locally`* → run
  `make hibayes-eval-build`.
- *`Cannot connect to the Docker daemon`* → start Docker Desktop or
  `systemctl start docker`.
- *Slow first run on M-series Mac* → `--platform linux/amd64` triggers QEMU
  emulation; the sampler may take 2–3× as long as on a native linux/amd64
  host. Acceptable for v1; native arm64 builds are future work.
- *`out/` files owned by root after first run* (Linux host issue) → `docker
  run` defaults to running as root inside the container, so output files
  inherit root ownership. Post-hoc fix: `chown -R $(id -u):$(id -g) out/`.
  Adding `--user "$(id -u):$(id -g)"` to the wrapper's `docker run` command is
  a follow-up improvement; not v1.

---

## Interpreting the report

### Posterior interpretation primer

Quantities that appear on the report and in the embedded manifest, defined in
plain language. The example numbers below use the renderer's in-memory
3-family pytest fixture (`alpha`, `bravo`, `charlie`) so they match what
T06's tests produce — they are NOT real MCMC outputs from a production run.

- **`posterior_mean`** — Bayesian point estimate of the family's true success
  probability, computed by combining the observed counts (`n_success` /
  `n_total`) with the model's hierarchical prior. Example: `alpha` has
  `posterior_mean = 0.96` — our best single-number guess for alpha's true
  success rate is 96%.
- **`posterior_median`** — equivalent point estimate; preferred when the
  posterior is skewed. For Beta-shaped posteriors at high `n`, mean and median
  converge.
- **`hdi_low` / `hdi_high` (94% HDI)** — the **highest-density interval**
  containing 94% of posterior mass. Plain reading: "given this data and the
  prior, we are 94% sure the true success rate lies between `hdi_low` and
  `hdi_high`." Example: `alpha`'s `[0.91, 0.99]` is narrow → confident
  estimate. `charlie`'s `[0.10, 0.90]` is nearly the entire unit interval →
  no information; the data is too sparse (`n_total = 2`).
- **`p_success_lt_strong`** (`P(success < 0.90)`) — posterior probability the
  family's true success rate is below the strong-reliability floor (default
  0.90; configurable as `strong_floor` in the YAML config). Plain reading:
  "what fraction of the posterior says this family is *not* strongly
  reliable?" Lower is better.
- **`p_success_lt_acceptable`** (`P(success < 0.80)`) — posterior probability
  the true rate is below the acceptable floor (default 0.80; configurable as
  `acceptable_floor`). Lower is better. The `riskChart` plots this column
  sorted descending — bars at the top are the families most likely to be
  unacceptable; act on those first.

**Cautionary note**: HDIs are NOT confidence intervals. The Bayesian
credibility reading — "94% sure the rate is in this range, given the data and
the prior" — is what users want, but the prior choice
(`two_level_group_binomial(prior_sigma_group_scale=2.0)` per Amendment 6 —
the HiBayes default `sgs=0.1` over-shrinks small-`n` families) noticeably
influences narrow-`n` families. Single-row families produce wide HDIs by design — that
is why the `min_n_for_classification = 3` guard exists.

### Reliability bands (DD-06)

The `ReliabilityBand` for each family is one of four values. Defaults are
locked in T03 (`ReliabilityThresholds`) and overridable per-run via the YAML
config.

| Band            | Default rule                                                                              | YAML key(s)                                              | Pill class (T06 DL-T06-4) |
|-----------------|-------------------------------------------------------------------------------------------|----------------------------------------------------------|---------------------------|
| `Reliable`      | `posterior_mean ≥ 0.95` AND `P(<0.90) < 0.20`                                             | `reliable_mean_floor`, `reliable_p_lt_strong_max`        | `pill ok` (green)         |
| `Watch`         | `posterior_mean ≥ 0.80` AND `P(<0.80) < 0.30`                                             | `watch_mean_floor`, `watch_p_lt_acceptable_max`          | `pill warn` (yellow)      |
| `Brittle`       | `P(<0.80) ≥ 0.50` (checked BEFORE Watch — a high mean with a disastrous lower tail still classifies Brittle) | `brittle_p_lt_acceptable_min`                            | `pill fail` (red)         |
| `TooUncertain`  | none of the above OR `n_total < min_n_for_classification` (default 3)                     | `min_n_for_classification`                               | `pill muted` (grey)       |

When `n_total < min_n_for_classification` (default 3) the family is classified
`TooUncertain` and a `small_n` flag is set on the row (per OQ-4 resolution).

`strong_floor` (default 0.90) and `acceptable_floor` (default 0.80) — the two
`P(success < x)` thresholds — are independently tunable.

#### YAML override example

Drop overrides into
`src/dmac_assistant/eval/hibayes_runtime_reliability/config/hibayes_runtime_reliability.yaml`
(or pass a different YAML via `--config`). The example below tightens
`Reliable`, loosens `Watch`, raises the `Brittle` threshold (more aggressive
labelling — fewer families graduate to Brittle), and raises the small-n guard:

```yaml
# src/dmac_assistant/eval/hibayes_runtime_reliability/config/hibayes_runtime_reliability.yaml
reliable_mean_floor: 0.97              # tightened from default 0.95
reliable_p_lt_strong_max: 0.10         # tightened from default 0.20
watch_mean_floor: 0.75                 # loosened from default 0.80
watch_p_lt_acceptable_max: 0.40        # loosened from default 0.30
brittle_p_lt_acceptable_min: 0.55      # tightened from default 0.50 (fewer Brittle labels)
strong_floor: 0.90                     # unchanged from default
acceptable_floor: 0.80                 # unchanged from default
min_n_for_classification: 5            # raised from default 3
```

Overrides take effect on the next run with no code changes (DD-06 guarantee).

### The four Chart.js charts

Canvas IDs are locked in T06 DL-T06-3.

- **`posteriorMeanChart`** — bar chart, posterior mean per family, with 94%
  HDI as error bars. Read: "best estimate ± uncertainty band, by family."
- **`observedVsPosteriorChart`** — scatter; x = `observed_success_rate` (T04),
  y = `posterior_mean`. A diagonal reference line is drawn in JS. Read: "how
  much did the prior pull each family away from its raw observed rate?
  Distance from the diagonal = pooling effect."
- **`failureModeChart`** — stacked bar of `n_error` / `n_timeout` /
  `n_no_answer` per family. Read: "when this family fails at runtime, what
  does the failure look like?" (NOT "what's the wrong answer" — see §"What
  this does NOT answer".)
- **`riskChart`** — bar of `p_success_lt_acceptable` per family, sorted
  descending. Read: "highest bars = act now." This is the chart most
  operationally actionable for triage.

### Diagnostics banner (DD-10)

If any HiBayes checker (`r_hat`, `divergences`, `ess_bulk`, `ess_tail`, `loo`,
`waic`, `prior_predictive_plot`, `posterior_predictive_plot`) returns
`status: "fail"`, the report renders a top-of-report warning banner naming
the failed checkers and their reasons. A failed checker does NOT invalidate
the report (failures are non-fatal per DD-10) but should reduce confidence in
the posterior summaries — investigate before acting.

### Embedded `hibayes-manifest`

Every rendered `report.html` embeds a
`<script type="application/json" id="hibayes-manifest">` block with a stable
v1 schema (locked in T06 DL-T06-5). Tooling consumers (downstream dashboards,
post-hoc analyses) MAY rely on:

- The `id` attribute value `hibayes-manifest` is stable.
- The top-level keys `schema_version`, `generated_at`, `n_families`,
  `thresholds`, `task_family_results`, `diagnostics_summary` are stable.
- Per-family record keys (`task_family`, `band`, `n_total`, `n_success`,
  `n_failure`, `observed_success_rate`, `posterior_mean`, `posterior_median`,
  `hdi_low`, `hdi_high`, `p_success_lt_strong`, `p_success_lt_acceptable`,
  `avg_cost_usd`, `avg_latency_seconds`, `avg_tool_calls_total`, `n_error`,
  `n_timeout`, `n_no_answer`, `hdi_80_low`, `hdi_80_high`) are stable in v1.

Schema additions are non-breaking; renames or removals are breaking and
require a `schema_version` bump. The authoritative schema definition lives in
`.claude/plans/hibayes-runtime-reliability-2026-05-09-tasks/task-06-html-renderer.md`
§3 (DL-T06-5) — this README intentionally does NOT inline-duplicate the schema
to preserve a single source of truth.

---

## Adding predictors later

v1 deliberately omits row-level predictors per DD-11 and Section 0 "Out of
scope (v1)". This section is **future work**, not a deferred work item — the
omission is the design.

### Conceptual primer

The v1 model is HiBayes's `two_level_group_binomial`, which consumes group
counts (`n_success`, `n_total` per family) plus a group-index. To bring
`is_opus` (or any row-level signal) into the model, switch to a HiBayes
built-in that accepts row-level features, or register a custom NumPyro model.

Per plan Section 11 Q1 (verified against
<https://github.com/UKGovernmentBEIS/hibayes/blob/main/src/hibayes/model/models.py>),
HiBayes ships **six** built-in group-binomial models:
`simplified_group_binomial_exponential`, `two_level_group_binomial`,
`three_level_group_binomial`, `three_level_group_binomial_exponential`,
`linear_group_binomial`, and `ordered_logistic_model`. The
`linear_group_binomial` is the natural next step for one row-level covariate.

### What changes — Features dict in `run_hibayes.py`

The `Features` dict that `_build_features` constructs (per DD-05 / DL-006)
currently has this shape:

```python
# === In src/dmac_assistant/eval/hibayes_runtime_reliability/run_hibayes.py ===
# Inside _build_features(...) — current shape, locked at DD-05 / DL-006:
features: Features = {
    "obs":         np.array([n_success_per_family]),          # shape (G,)
    "num_group":   G,
    "group_index": np.arange(G),
    "n_total":     np.array([n_total_per_family]),            # shape (G,)
}

# v1+1 shape adding `is_opus` as a binary covariate (illustrative;
# the exact key names depend on the chosen HiBayes built-in's contract):
features: Features = {
    "obs":         np.array([n_success_per_family_per_opus]), # shape (G, 2)
    "num_group":   G,
    "group_index": np.arange(G),
    "n_total":     np.array([n_total_per_family_per_opus]),   # shape (G, 2)
    "covariate":   np.array([0, 1]),                          # is_opus level per slice
}
```

This change has a **paired upstream requirement** in T04's aggregator:
`process_runtime_reliability.py`'s `aggregate_by_task_family` must be
rewritten (or a sibling function added) to produce per-`(family, is_opus)`
aggregate cells instead of per-`family` cells, so the `(G, 2)`-shaped arrays
above have well-defined contents. **Two files change**:
`process_runtime_reliability.py` (new aggregator shape) and `run_hibayes.py`
(`_build_features` consumes the new shape). Don't change just one.

### What changes — model swap in `run_hibayes.py`

```python
# v1
from hibayes.model.models import two_level_group_binomial
model_builder = two_level_group_binomial(prior_sigma_group_scale=2.0)  # Amendment 6

# v1+1: a built-in that accepts a row-level covariate
from hibayes.model.models import linear_group_binomial
model_builder = linear_group_binomial()
```

### Or: register a custom NumPyro model

If no built-in fits, register a custom model using HiBayes's public `@model`
decorator. Per Section 11 Q1, the decorator is exported at
<https://github.com/UKGovernmentBEIS/hibayes/blob/main/src/hibayes/model/_model.py>
and re-exported as `from hibayes.model import model`:

```python
from hibayes.model import model     # public decorator surface (Section 11 Q1)
from typing import Callable
import numpyro
import numpyro.distributions as dist

@model
def two_level_group_binomial_with_opus() -> Callable:
    def builder(features) -> None:
        # Define priors, a link function over `features["covariate"]`,
        # and the binomial likelihood. See HiBayes's two_level_group_binomial
        # source for the canonical pattern to extend.
        ...
    return builder
```

Use the **public** `from hibayes.model import model` import only; avoid
`from hibayes.model._model import Model` in user code (private module path).
The `Model` type annotation is illustrative and can be inferred from the
decorator. The relevant `ModelAnalysisState` reference for understanding the
state object returned by a run is
<https://github.com/UKGovernmentBEIS/hibayes/blob/main/src/hibayes/analysis_state.py>.

### What this README warns against

Do **not** add `is_opus` (or any row-level covariate) to `RuntimeEvalRow` as
a new field — DD-01 + R-08 forbid extending the schema. The covariate already
exists on the row; the work is in the aggregator + the model, not in the row
shape.

### Closing note

These are illustrative diffs, not a v1+1 task spec. A real extension needs
its own `/ultraplan` cycle covering aggregator changes, model selection,
posterior shape changes, and band-classification adjustments.

## Related axes (2026-05 evaluator expansion)

This module is the load-bearing reference for the broader HiBayes evaluator expansion:

- `src/dmac_assistant/eval/hibayes_artifact_validity/` — artifact-validity axis (T3.1)
- `src/dmac_assistant/eval/hibayes_functional_usefulness/` — functional-usefulness axis (T3.2)
- `src/dmac_assistant/eval/hibayes_combined_report/` — combined HTML report renderer (T4.1)

All three new modules mirror this axis's posterior shape (`PosteriorTaskFamilyReliability`) and model family (`two_level_group_binomial`). The `posterior_json_adapter.py` in THIS module emits a `posterior.json` matching the same nested wrapper schema (DD-41) so the combined renderer can consume all four axes uniformly.

See `.claude/plans/hibayes-evaluator-expansion-build-2026-05-15.md` for the locked build plan.
