"""
T5 — in-image entrypoint for the JudgeUITranscript BAML function.

Invoked by T3's host-side judge_query via:
    docker run --rm \
      --env GCP_API_KEY \
      --env NEXTSEEK_EVALUATOR_MODE=gcp \
      -v {evidence_dir}:/evidence:ro \
      -v {out_dir}:/out \
      dmac-assistant:e2e-{date} \
      python -m tools.e2e.judge_runner --record /evidence/<file>.json

Outputs a single JSON object on stdout: {"verdict": ..., "reasoning": ..., "model": ...}.
On any failure, exits non-zero and writes a short message to stderr — T3 then
marks the record's judge_verdict='error' (it does NOT trust this script's stdout
on failure).

Loads only environment variables on the OP-4 allowlist
(GCP_API_KEY, NEXTSEEK_EVALUATOR_MODE). Refuses to start if either is missing.
NEVER reads AWS_BEARER_TOKEN_BEDROCK — closes the second Bedrock-token
exfiltration vector per OP-4.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        print(f"missing required env var: {key}", file=sys.stderr)
        raise SystemExit(2)
    return val


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True, help="Path to QueryRecord JSON")
    args = parser.parse_args()

    _require_env("GCP_API_KEY")
    mode = _require_env("NEXTSEEK_EVALUATOR_MODE")
    if mode != "gcp":
        print(
            f"NEXTSEEK_EVALUATOR_MODE must be 'gcp' (got {mode!r})",
            file=sys.stderr,
        )
        return 2

    record_path = Path(args.record)
    if not record_path.is_file():
        print(f"record not found: {record_path}", file=sys.stderr)
        return 2

    record = json.loads(record_path.read_text())
    query_id = record.get("query_id") or ""
    if not query_id:
        print("record missing query_id field", file=sys.stderr)
        return 2
    query = record.get("query_text") or ""

    # Amendment 4 / F-05-03 fix: source pass_criteria from queries.json (T1's
    # output) by query_id lookup. queries.json lives at /evidence/queries.json
    # because T3's docker run mounts evidence_dir:/evidence:ro, and T1 writes
    # queries.json at evidence_dir/queries.json. No new mount required.
    queries_path = record_path.parent / "queries.json"
    if not queries_path.is_file():
        print(f"queries.json not found at {queries_path}", file=sys.stderr)
        return 2
    queries_doc = json.loads(queries_path.read_text())
    queries = queries_doc.get("queries") or queries_doc  # tolerate either shape
    if not isinstance(queries, list):
        print("queries.json: expected a list of query entries", file=sys.stderr)
        return 2
    entry = next((q for q in queries if q.get("id") == query_id), None)
    if entry is None:
        print(f"query_id {query_id!r} not found in queries.json", file=sys.stderr)
        return 2
    pass_criteria_raw = entry.get("pass_criteria")
    if not pass_criteria_raw:
        print(
            f"queries.json entry for {query_id!r} missing pass_criteria",
            file=sys.stderr,
        )
        return 2
    # F4 (Wave 1c post-review): T1's pass_criteria is a list[dict] of
    # {field, op, value} assertions. BAML's SimpleEvaluatorInput.pass_criteria
    # is typed `string`, so serialize as JSON before handoff. Strings pass
    # through unchanged for forward-compat with future T1 schema variants.
    pass_criteria = (
        pass_criteria_raw
        if isinstance(pass_criteria_raw, str)
        else json.dumps(pass_criteria_raw)
    )

    # Amendment 4 / F-05-03 fix: ui_answer is now a canonical field on
    # QueryRecord (T2 spec, added in Amendment 4). T6 writes the assistant's
    # user-visible reply text directly to record["ui_answer"] — no private-key
    # convention. None means crashed-query / no answer rendered → refuse.
    ui_answer = record.get("ui_answer") or ""
    if not ui_answer:
        print("record missing or empty ui_answer field", file=sys.stderr)
        return 2

    # baml_client is generated at image-build time into /app/tools/e2e/baml_client/.
    # WORKDIR is /home/user; PATH includes /opt/dmac-venv/bin; sys.path
    # includes the venv's site-packages. /app must be on sys.path for
    # `tools.e2e.baml_client` to import — handle that here defensively.
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")

    from tools.e2e.baml_client import b  # type: ignore[import-not-found]

    try:
        result = b.JudgeUITranscript(
            input={  # type: ignore[arg-type]
                "query": query,
                "pass_criteria": pass_criteria,
                "ui_answer": ui_answer,
            }
        )
    except Exception as exc:  # noqa: BLE001 — sentinel boundary; T3 marks 'error'
        print(f"BAML judge call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # Normalize verdict to lowercase wire form. The BAML enum uses
    # uppercase identifiers (Passed, Failed, ...) with @alias("lowercase")
    # — str(result.verdict) may render either form depending on baml-py
    # version, so lowercase explicitly to honour the N3 sync-gate contract.
    verdict_raw = str(result.verdict)
    # Strip enum class prefix if present (e.g. "JudgeVerdict.Passed").
    if "." in verdict_raw:
        verdict_raw = verdict_raw.rsplit(".", 1)[-1]
    verdict = verdict_raw.lower()

    payload = {
        "verdict": verdict,
        "reasoning": str(result.reasoning),
        "model": str(getattr(result, "model", "") or "gemini-3.1-pro-preview"),
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
