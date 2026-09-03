# Native NExtSEEK Evaluator

Runs evaluator judgments and retry decisions against orchestrator outputs,
produces resumable JSON batch reports, and renders an offline HTML dashboard.

**Read the two landmines at the bottom before the first run.** The CLI's own
`--help` advertises an input file that is not in this repository, and the BAML
auto-bootstrap the quickstart used to rely on cannot succeed here.

## Quickstart

### 1. Author a query file

The evaluator ships no query set, so this step is not optional. The loader
picks a branch on the suffix alone
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:456-459`) and accepts
three shapes:

- any non-`.json` path — one query per line, with blank lines and `#` comments
  skipped (`chat_nextseek/src/chat_nextseek/evaluator/reports.py:408-415`)
- `.json` holding a list — each entry an object with a `question` key
  (`chat_nextseek/src/chat_nextseek/evaluator/reports.py:444-453`)
- `.json` holding an object — `full_test.tests[]`, each entry with a `query`
  key (`chat_nextseek/src/chat_nextseek/evaluator/reports.py:421-437`). This is
  the shape the retired `testing.json` had; see the first landmine.

```bash
cat > eval-queries.txt <<'EOF'
# one query per line
How many samples are in project 2?
EOF
```

If you want real questions rather than invented ones, the E2E catalog carried
elsewhere in this boundary is the only query corpus the repo has: 11 families and
366 variants, whose `turns[].query` strings number 429, 382 of them distinct,
counted 2026-09-03 by parsing `chat_nextseek/e2e/catalog.json`. Extracting them
into one of the shapes above is yours to do: nothing in the evaluator reads that
catalog, established the same day by a `/usr/bin/grep -rn` for `catalog.json`
and `e2e` over every `.py` file under
`chat_nextseek/src/chat_nextseek/evaluator/`, which returned nothing.

### 2. Run the batch

In this repository the evaluator runs inside the stack image over a writable
scratch copy of `chat_nextseek/`, the same lane `chat_nextseek/CLAUDE.md`
prescribes for this package's tests. The image installs the package editable
(`pyproject.toml:134-136`), so the mount is what actually executes:

```bash
docker run --rm -v <scratch-copy>:/app/chat_nextseek:z -w /app/chat_nextseek \
  -e CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1 \
  -e CATALOG_FILE=/app/chat_nextseek/agent_model_catalog.json \
  -e GCP_API_KEY=... -e NEXTSEEK_BASE_URL=... -e API_USER=... -e API_PASS=... \
  nextseek-nextseek:latest \
  /app/.venv/bin/python -m chat_nextseek.evaluator \
      --eval-batch eval-queries.txt --eval-batch-limit 5
```

Two of those env vars are load-bearing beyond the credentials:

- `CATALOG_FILE` (or `AGENT_MODEL_CATALOG`) is required — without it config
  construction raises `Neither AGENT_MODEL_CATALOG nor CATALOG_FILE is set.`
  before any query runs
  (`chat_nextseek/src/chat_nextseek/config.py:262-266`). The value in
  `chat_nextseek/.env.example:2` is the repo-relative `agent_model_catalog.json`.
- `CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1` is what lets the command reach the
  argument parser at all in this repo (`chat_nextseek/src/chat_nextseek/evaluator/bootstrap.py:51-52`).
  See the second landmine.

The provider key the default mode needs is `GCP_API_KEY`; a different `--mode`
requires that provider's key instead, and the check is a hard raise at
`chat_nextseek/src/chat_nextseek/config.py:490-493`.

Outside this monorepo — in the standalone repository this directory is a
vendored snapshot of — the documented route is `uv sync` followed by the same
`python -m chat_nextseek.evaluator` invocation. That route was not exercised
here; every command in this file was run in the container lane above.

### 3. Open the dashboard

```bash
open outputs/evaluator/eval-*.html
```

Reports land under `--eval-batch-out`, default `outputs/evaluator`
(`chat_nextseek/src/chat_nextseek/evaluator/runner.py:270-275`). In the
container lane the working directory is the mount, so they appear inside your
scratch copy, owned by root.

## What you'll see

`outputs/evaluator/` gets `eval-<runid>-<UTC timestamp>.json`
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:346-349`) and a sibling
`.html` rendered from it
(`chat_nextseek/src/chat_nextseek/evaluator/runner.py:226-233`). Every query
lands in exactly one bucket — the classifier returns a single bucket name per
report (`chat_nextseek/src/chat_nextseek/evaluator/reports.py:264-292`) and the
summarizer increments only that one
(`chat_nextseek/src/chat_nextseek/evaluator/reports.py:295-306`):
`queries_passed`, `queries_retried`, `queries_failed`, `queries_unsupported`,
`queries_with_errors`, `queries_exceptions`, or `infra_failures`
(see [docs/operations.md](docs/operations.md#failure-buckets)).

Measured 2026-09-03 in the container lane above with `--network none` and a
placeholder provider key, so every agent call failed at DNS: the run still
produced both artifacts and printed `status: completed_with_failures`,
`infra_failures: 0`, with the single query classified `queries_unsupported`.
That is the artifact plumbing proving itself; it is not an evaluation.

## Landmine — `--eval-batch testing.json` cannot work

No file named `testing.json` exists in this repository. Established
2026-09-03 by two exhaustive searches from the worktree root that returned
nothing: `git ls-files | grep -i 'testing\.json'`, and `find . -name
testing.json -not -path './.git/*'`. `chat_nextseek/CITATIONS.txt:139-142`
records why — the `smart_test.py` / `test.py` / `testing.json` harness was
retired.

This is not only a documentation defect. The CLI itself still names the file, in
its own help text: two epilog examples at
`chat_nextseek/src/chat_nextseek/evaluator/runner.py:247-248` and
`chat_nextseek/src/chat_nextseek/evaluator/runner.py:251`, and the `--eval-batch`
help string at `chat_nextseek/src/chat_nextseek/evaluator/runner.py:269`.
Running `--help` therefore prints two examples that cannot run. The same stale
name is carried in the flag table at [docs/cli.md](docs/cli.md). None of that is
fixed here — this refresh may not change code.

Copying either example verbatim fails after config construction, at the loader
(`chat_nextseek/src/chat_nextseek/evaluator/runner.py:347-348`), and the
top-level handler turns the `FileNotFoundError` into a one-line message and
exit 1 (`chat_nextseek/src/chat_nextseek/evaluator/runner.py:447-449`).
Observed 2026-09-03:

```
[evaluator] Command failed: [Errno 2] No such file or directory: 'testing.json'
```

Substitute your own query file, per step 1.

## Landmine — the BAML bootstrap cannot succeed in this repo

`__main__.py` calls the bootstrap before dispatching to the parser
(`chat_nextseek/src/chat_nextseek/evaluator/__main__.py:15`), and the bootstrap
shells out to `uv run baml-cli generate`
(`chat_nextseek/src/chat_nextseek/evaluator/bootstrap.py:60-63`) whenever
`src/baml_client/` is missing or stale. It is always missing here: the
directory is gitignored at `chat_nextseek/.gitignore:47`, and it is absent from
both the worktree and the built image, checked 2026-09-03 by listing
`chat_nextseek/src/` on the host and `/app/chat_nextseek/src/` in
`nextseek-nextseek:latest` — each holds only `chat_nextseek` and
`chat_nextseek.egg-info`.

The regeneration then fails on a version pin, with the network up. The
generator block pins `version "0.221.0"`
(`chat_nextseek/src/chat_nextseek/evaluator/baml_src/generators.baml:14`) while
the dependency is declared open-ended as `baml-py>=0.221.0`
(`chat_nextseek/pyproject.toml:26`) and both lockfiles resolve it to 0.222.0
(`chat_nextseek/uv.lock:169-170`, `uv.lock:222-223`). Observed 2026-09-03:

```
Version mismatch: BAML GENERATION DISABLED: Generator version (0.221.0) !==
the installed baml package version (0.222.0).
Error: Client generation failed
...
RuntimeError: baml-cli generate failed (exit 4).
```

So the quickstart's old "auto-regenerates on first call" promise does not hold,
and the manual fallback in [docs/operations.md](docs/operations.md#baml-regeneration)
is the identical command and fails identically. Setting
`CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1` skips the attempt
(`chat_nextseek/src/chat_nextseek/evaluator/bootstrap.py:51-52`) and is what
every command in this file uses.

Skipping it gets batches running and artifacts written, but leaves one path
dead: any query whose `path_mode` is not `unsupported` reaches the BAML seam
(`chat_nextseek/src/chat_nextseek/evaluator/workflow.py:254-258`), which
imports the generated client at call time
(`chat_nextseek/src/chat_nextseek/evaluator/client.py:105-106`). Measured
2026-09-03 by calling that function directly in the stack image:
`ModuleNotFoundError: No module named 'baml_client'`. Real judgments need
either a generated client or a double passed to the workflow's `baml_client`
constructor argument
(`chat_nextseek/src/chat_nextseek/evaluator/workflow.py:173-178`), which the
CLI never supplies (`chat_nextseek/src/chat_nextseek/evaluator/runner.py:202-205`).

## Next reads

- [docs/architecture.md](docs/architecture.md) — how the pieces fit together
- [docs/operations.md](docs/operations.md) — env vars, BAML regeneration, troubleshooting
- [docs/cli.md](docs/cli.md) — full flag reference
- [docs/runbook.md](docs/runbook.md) — the full async batch, resume, and what
  the 103-question claim is worth today
- `chat_nextseek/CLAUDE.md` — the container test lane and this package's traps
