# CLI reference

`python -m chat_nextseek.evaluator [flags]`

## Runtime mode

| Flag | Default | Purpose |
|---|---|---|
| `--prod` | off | Use production API + graph credentials |
| `--planner` | off | Use planner pipeline (`run_query_plan`) |
| `--mode <name>` | `gcp` | Evaluator model profile |

## Batch evaluation

| Flag | Default | Purpose |
|---|---|---|
| `--eval-batch <path>` | — | Input: `.txt` (one query per line) or `testing.json` |
| `--eval-batch-out <dir>` | `outputs/evaluator` | Output directory |
| `--eval-batch-limit <n>` | none | Cap queries evaluated |
| `--eval-batch-async` | off | Bounded-concurrency async run with resume state |
| `--eval-batch-concurrency <n>` | 5 | Async concurrency limit |
| `--eval-batch-resume <path>` | — | Resume from persisted `batch-*.json` state |

## Demo server

| Flag | Default | Purpose |
|---|---|---|
| `--eval-demo` | off | Serve the dashboard HTML over HTTP |
| `--eval-demo-port <n>` | 8080 | Listen port |
| `--eval-demo-html <path>` | latest | HTML file to serve |

## Single-source evaluation

| Flag | Default | Purpose |
|---|---|---|
| `--eval-source <ref>` | — | `bundle:<session>:<bundle_id>` reference |
| `--eval-context` | off | Print normalized evaluator context and exit |
| `--eval-run` | off | Evaluate without executing retry |
| `--eval-retry` | off | Evaluate and execute retry |

## Run management

| Flag | Default | Purpose |
|---|---|---|
| `--eval-list` | off | List persisted evaluator runs |
| `--eval-status <s>` | — | Filter `--eval-list` by status |
| `--eval-limit <n>` | 20 | Max rows returned |
| `--eval-has-bundle` | off | Only show runs with a bundle source |

Built-in: `--help`.
