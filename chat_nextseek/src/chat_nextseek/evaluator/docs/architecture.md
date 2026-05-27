# Architecture

## Data flow

```
Orchestrator run (results_history bundle)
        |
        v
normalization.py            -> EvaluatorRetryContextResponse
        |
        v
workflow.EvaluatorWorkflow  -> judgment + retry_decision (via BAML)
        |
        v
reports.EvalReport          -> per-query row
        |
        v
reports._classify_report    -> exactly one of 7 buckets (DD-43)
        |
        v
reports.build_batch_report  -> EvalBatchReport (JSON)
        |
        v
dashboard/__init__.py       -> standalone HTML artifact
```

## Key components

- **BAML** (`baml_src/*.baml` -> `src/baml_client/`): declarative LLM
  function specs. Regenerated on CLI entry via `bootstrap._bootstrap_baml_client`
  (DD-40..DD-42). `baml_client/` is gitignored and must be regenerable.
- **EvaluatorWorkflow** (`workflow.py`): single seam for BAML calls so tests
  can swap in `FakeBamlClient`.
- **Classifier** (`reports._classify_report`): precedence-based mapping from
  `EvalReport` to one failure bucket. See DD-43.

## Invariants

1. **DL-010 evaluator file boundary**: all subsystem edits stay inside
   `src/chat_nextseek/evaluator/` and `tests/evaluator/` (one reviewed
   exception for a CLAUDE.md pointer in the broader plan history).
2. **DD-32 generated code boundary**: never edit `src/baml_client/` by hand.
3. **DD-43 classification exclusivity**: every report lands in exactly one
   bucket; `run_status` is derived, not stored independently.
