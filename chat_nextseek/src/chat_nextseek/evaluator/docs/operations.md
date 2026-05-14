# Operations

## Env vars

| Var | Required | Purpose |
|---|---|---|
| `NEXTSEEK_BASE_URL` / `API_USER` / `API_PASS` | yes | NExtSEEK REST API |
| `GCP_API_KEY` (or Anthropic / OpenAI equivalents) | yes (>=1) | LLM provider |
| `CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1` | no | Skip baml regen (CI) |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | optional | Enables graph-mode evaluation |

<a id="baml-regeneration"></a>
## BAML regeneration

The evaluator auto-regenerates `src/baml_client/` on CLI entry when it's
missing or older than any `.baml` source file (see section `baml-regeneration`).
If auto-regen fails, run it manually:

```bash
uv run baml-cli generate --from src/chat_nextseek/evaluator/baml_src
```

### When to regenerate manually

- You edited a `.baml` file and auto-regen didn't trigger.
- `uv` is not on PATH; the CLI will tell you exactly what to run.
- CI sets `CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP=1` and runs regen separately.

## Failure buckets

| Bucket | Precedence | Meaning |
|---|---|---|
| `queries_exceptions` | 1 (highest) | Uncaught exception in the runner for this query. |
| `infra_failures` | 2 | Environment/provider fault caught and recorded structurally: Neo4j unreachable, 5xx after retries, missing env var. |
| `queries_with_errors` | 3 | Per-query error caught and recorded without becoming an exception bucket. |
| `queries_unsupported` | 4 | Parser returned `unsupported`, or judgment flagged the prompt as unsupported. |
| `queries_failed` | 5 | Final verdict `FAIL` or `PARTIAL`, including unresolved `RETRY` rows that never produced a passing retry outcome. |
| `queries_retried` | 6 | First attempt != PASS, retry returned PASS. |
| `queries_passed` | 7 | PASS on first attempt. |

`run_status`: `crashed` if any `queries_exceptions`; else `completed_with_failures`
if any of {failed, unsupported, with_errors, infra}; else `completed`.

## Troubleshooting

- **`RuntimeError: baml-cli generate failed`**: check stderr output. Usually
  means a `.baml` syntax error. Fix the file and rerun.
- **`ImportError: baml_client`**: the auto-bootstrap was skipped via env var
  or failed before generation completed. Rerun with
  `CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP` unset.
- **Neo4j-related infra_failures**: verify `NEO4J_URI` is reachable before rerunning.
- **Stale dashboard numbers after a classifier change**: rerun the batch.
  Classifier updates do not rewrite past reports.
