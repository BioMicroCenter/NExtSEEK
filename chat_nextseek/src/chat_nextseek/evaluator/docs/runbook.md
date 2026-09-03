# Runbook — full async regression batch

## Purpose

Run the evaluator over a whole query file end to end: async, bounded
concurrency, resumable, JSON + HTML artifacts.

**The 103-question batch this runbook was written for cannot be reproduced from
this repository.** It was driven from `testing.json`, and no such file exists
here — established 2026-09-03 by two exhaustive searches from the worktree root
that both returned nothing, `git ls-files | grep -i 'testing\.json'` and `find
. -name testing.json -not -path './.git/*'`. Nor is the question set carried
under another name: a `/usr/bin/grep -rn` for the alternation
`103-question|103 question|103 quer` across the worktree, excluding `.git/`,
`.superpowers/` and `__pycache__/`, matched exactly three files — this
document, [../README.md](../README.md), and one `nessie_tests` note that is
itself a restatement of these docs (`nessie_tests/FAMILIES.json:9897`, whose
entry begins `docs:`). The retirement is recorded at
`chat_nextseek/CITATIONS.txt:139-142`.

So this runbook now documents the mechanism, not that particular corpus. Bring
your own query file, in one of the three shapes
[../README.md](../README.md#quickstart) lists; the size of the run is whatever
you put in it.

## Prerequisites

- The container lane from [../README.md](../README.md#2-run-the-batch). Every
  command below was run in it on 2026-09-03.
- `CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1`. Not optional here — the auto-bootstrap
  fails on a generator/package version mismatch, and the manual fallback is the
  same command. See the second landmine in [../README.md](../README.md).
- `CATALOG_FILE` (or `AGENT_MODEL_CATALOG`) plus NExtSEEK creds and a provider
  key for the mode you pick. Full table in [operations.md](operations.md).
- A query file you authored.
- **Expected cost**: no measured figure exists in this repository. The batch is
  one full orchestrator turn per query plus a judgment and, where the verdict
  is not `PASS`, a retry — so cost scales with the query count and the profile
  `--mode` selects, and `--eval-batch-limit` is the lever that caps it. The
  `$5–$15` and `~20 minutes` figures older copies of this file carried were for
  the 103-question batch above; they survive only in the restatement at
  `nessie_tests/FAMILIES.json:9897` and have no reproducible referent here.

## Step-by-step

### 1. Verify credentials

```bash
docker run --rm --network none -v <scratch-copy>:/app/chat_nextseek:z \
  -w /app/chat_nextseek \
  -e CATALOG_FILE=/app/chat_nextseek/agent_model_catalog.json -e GCP_API_KEY=... \
  nextseek-nextseek:latest \
  /app/.venv/bin/python -c "import chat_nextseek.config as c; c.ChatConfig(); print('config OK')"
```

This is a real gate, and it fires before any query runs. Observed 2026-09-03
with the catalog and key set, `config OK`; with neither,
`RuntimeError: GCP mode selected but GCP_API_KEY is not set.`
(`chat_nextseek/src/chat_nextseek/config.py:490-493`); with the key but no
catalog, `RuntimeError: Neither AGENT_MODEL_CATALOG nor CATALOG_FILE is set.`
(`chat_nextseek/src/chat_nextseek/config.py:262-266`). Note that a bare
`ChatConfig()` checks the default `gcp` mode; the CLI's `--mode` overrides it
(`chat_nextseek/src/chat_nextseek/evaluator/runner.py:163-169`, passed into the
config at `chat_nextseek/src/chat_nextseek/evaluator/runner.py:179`), so re-run
this check with the key that mode needs.

Anything raised here is an `infra_failures` candidate; fix it before proceeding.

### 2. Skip the cold bootstrap

Older copies of this file said the first CLI call auto-regenerates
`src/baml_client/` from `baml_src/`. It tries
(`chat_nextseek/src/chat_nextseek/evaluator/__main__.py:15`) and it fails, in
this repo, with or without a network. Pass
`CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1`
(`chat_nextseek/src/chat_nextseek/evaluator/bootstrap.py:51-52`). The evidence
and the consequence — judgments on supported paths still need a client that
cannot be generated — are in [../README.md](../README.md).

### 3. Run the batch (async, resumable)

```bash
docker run --rm -v <scratch-copy>:/app/chat_nextseek:z -w /app/chat_nextseek \
  -e CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1 \
  -e CATALOG_FILE=/app/chat_nextseek/agent_model_catalog.json \
  -e GCP_API_KEY=... -e NEXTSEEK_BASE_URL=... -e API_USER=... -e API_PASS=... \
  nextseek-nextseek:latest \
  /app/.venv/bin/python -m chat_nextseek.evaluator \
      --eval-batch eval-queries.txt \
      --eval-batch-async \
      --eval-batch-concurrency 5 \
      --eval-batch-out outputs/evaluator/regression-batch
```

On completion it prints five lines — `state:`, `json:`, `html:`, `status:`,
`infra_failures:` (`chat_nextseek/src/chat_nextseek/evaluator/runner.py:375-379`)
— and exits 0 only when `run_status` is `completed`
(`chat_nextseek/src/chat_nextseek/evaluator/runner.py:380`). Three files land in
the output directory: the resume state `batch-<runid>.json`
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:586-587`), the report
`eval-<runid>-<UTC timestamp>.json`
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:346-349`), and its
sibling `.html` (`chat_nextseek/src/chat_nextseek/evaluator/runner.py:226-233`).

### 4. If it gets interrupted, resume

```bash
  /app/.venv/bin/python -m chat_nextseek.evaluator \
      --eval-batch-resume outputs/evaluator/regression-batch/batch-<runid>.json \
      --eval-batch-async
```

`--eval-batch-async` is mandatory on a resume: without it the CLI prints
`[evaluator] --eval-batch-resume requires --eval-batch-async.` and exits 2
(`chat_nextseek/src/chat_nextseek/evaluator/runner.py:382-384`). Verified
2026-09-03 — both the exit 2 and, with the flag, a resume that reused the same
run id and rewrote the same three artifact paths.

### 5. Inspect the artifacts

```bash
open outputs/evaluator/regression-batch/eval-*.html
```

In the container lane the working directory is the mount, so the artifacts
appear inside your scratch copy, owned by root.

## Reading the report

Look at `run_status` first
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:308-316`):

- `completed` -> no query landed in `queries_failed`, `queries_unsupported`,
  `queries_with_errors`, `infra_failures` or `queries_exceptions`; this is also
  the only value that sets `success`
  (`chat_nextseek/src/chat_nextseek/evaluator/reports.py:318`)
- `completed_with_failures` -> at least one of `queries_failed`,
  `queries_unsupported`, `queries_with_errors` or `infra_failures` is non-zero
- `crashed` -> at least one `queries_exceptions` row; inspect those first

See [operations.md#failure-buckets](operations.md#failure-buckets) for bucket
semantics, and note that a report is assigned exactly one bucket
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:264-292`).

## Known shape

No evaluator run is recorded in this repository. Established 2026-09-03 by a
`find` for `eval-*.json` and `batch-*.json` outside `.git/`, which returned
nothing, and by `git ls-files` matching nothing under `outputs/evaluator`. The
5-query smoke checkpoint older copies of this file described has no surviving
artifact, so it is not evidence of anything and has been dropped rather than
restated.

What was measured, 2026-09-03: a two-query async batch in the container lane
with `--network none` and a placeholder provider key. Every agent call died at
DNS resolution, yet the batch completed structurally — all three artifacts
written, the five stdout lines printed, `status: completed_with_failures`,
`infra_failures: 0`, exit 1. That proves the batch, resume and reporting
plumbing; it proves nothing about judgment quality, which needs the BAML client
this repo cannot generate.
