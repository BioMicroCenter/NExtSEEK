#!/usr/bin/env python3
"""Mechanically extract Step 7 exercise catalog fields from upstream dmac-assistant.

Reads ``tools/e2e/run_t18_rewire_e2e.py`` PAID_PROJECTIONS + REPORT_PROJECT and
``tools/e2e/run_router_e2e.py`` DISCRIMINATORS, emitting JSON suitable for
``acceptance_evidence/step7/STEP7-UPSTREAM-EXERCISE-CATALOG.json``.

Usage:
  DMAC_ASSISTANT_ROOT=/path/to/dmac-assistant \\
    python extract_step7_upstream_catalog.py [--out PATH] [--uid OVERRIDE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_SHA = "a429f1372a075e5db586a1b6efc8c3b1663e211a"

BIN_OP_MAP = {
    "entity": "nextseek-entity-extract",
    "parse": "nextseek-parse",
    "graph": "nextseek-graph",
    "api-read": "nextseek-api-read",
    "api-write": "nextseek-api-write",
    "report": "nextseek-report",
    "generate-submission": "nextseek-generate-submission",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _extract_paid_projections(t18_text: str) -> list[tuple]:
    """Parse PAID_PROJECTIONS from ``_run_paid_steps`` (may call ``json.dumps``)."""
    start = t18_text.find("PAID_PROJECTIONS = [")
    if start < 0:
        raise ValueError("PAID_PROJECTIONS not found in run_t18_rewire_e2e.py")
    end = t18_text.find("\n    for op, model, projected, args_dict in PAID_PROJECTIONS:", start)
    if end < 0:
        raise ValueError("PAID_PROJECTIONS block end not found")
    block = t18_text[start:end]
    ns: dict = {}
    exec(block, {"json": json}, ns)  # noqa: S102 — mechanical upstream mirror
    paid = ns.get("PAID_PROJECTIONS")
    if not isinstance(paid, list):
        raise ValueError("PAID_PROJECTIONS did not evaluate to a list")
    return paid


def _extract_report_project(t18_text: str) -> str:
    m = re.search(r'^REPORT_PROJECT\s*=\s*"([^"]+)"', t18_text, re.MULTILINE)
    if not m:
        raise ValueError("REPORT_PROJECT not found")
    return m.group(1)


def _extract_search_basic_query(router_text: str) -> str:
    """Corpus query for Search-Basic-1 (fallback if corpus file absent)."""
    corpus = Path(__file__).resolve().parents[4] / ".." / "dmac-assistant" / "tools" / "e2e" / "corpus.json"
    if corpus.is_file():
        data = json.loads(corpus.read_text(encoding="utf-8"))
        for row in data.get("queries") or []:
            if row.get("id") == "Search-Basic-1":
                return str(row.get("query") or row.get("text") or "find mice")
    return "find mice"


def build_catalog(
    upstream_root: Path,
    *,
    geo_uid_override: str | None = None,
) -> dict:
    t18_path = upstream_root / "tools" / "e2e" / "run_t18_rewire_e2e.py"
    router_path = upstream_root / "tools" / "e2e" / "run_router_e2e.py"
    t18_text = t18_path.read_text(encoding="utf-8")
    router_text = router_path.read_text(encoding="utf-8")

    report_project = _extract_report_project(t18_text)
    paid = _extract_paid_projections(t18_text)
    search_query = _extract_search_basic_query(router_text)

    exercises: list[dict] = []
    idx = 0
    for op_key, _model, _proj, args_dict in paid:
        bin_op = BIN_OP_MAP.get(op_key)
        if not bin_op:
            continue
        exercises.append({
            "exercise_id": f"T18-{op_key}-{idx + 1}",
            "bin_op": bin_op,
            "proof_paradigm": "forced_cc_direct",
            "upstream_ref": f"dmac-assistant@a429f13:tools/e2e/run_t18_rewire_e2e.py:PAID_PROJECTIONS[{idx}]",
            "inputs": dict(args_dict),
            "expected_exit_code": 0,
        })
        idx += 1

    # api-write unconfirmed leg (T18 Step2c pattern — parser_plan from api-read)
    api_read = next(e for e in exercises if e["bin_op"] == "nextseek-api-read")
    exercises.append({
        "exercise_id": "T18-api-write-1",
        "bin_op": "nextseek-api-write",
        "proof_paradigm": "forced_cc_direct",
        "upstream_ref": "dmac-assistant@a429f13:tools/e2e/run_t18_rewire_e2e.py:Step2c",
        "inputs": {"parser_plan": api_read["inputs"]["parser_plan"], "confirmed_write": False},
        "expected_exit_code": 5,
        "expected_excerpt_contains": ["WRITE_BLOCKED"],
    })

    exercises.append({
        "exercise_id": "T18-report-1",
        "bin_op": "nextseek-report",
        "proof_paradigm": "forced_cc_direct",
        "upstream_ref": "dmac-assistant@a429f13:tools/e2e/run_t18_rewire_e2e.py:REPORT_PROJECT",
        "inputs": {"mode": "published", "project": report_project},
        "expected_exit_code": 0,
    })

    geo_uid = geo_uid_override or "D.MSP-250319WHI-49-PUB"
    gen = next(e for e in exercises if e["bin_op"] == "nextseek-generate-submission")
    gen["inputs"]["uids"] = geo_uid
    if geo_uid_override:
        gen["mapping_rationale"] = "Instance binding override at extraction time"

    exercises.append({
        "exercise_id": "ROUTER-Search-Basic-1",
        "bin_op": "nextseek-query",
        "proof_paradigm": "forced_cc_nl",
        "upstream_ref": "dmac-assistant@a429f13:tools/e2e/run_router_e2e.py:DISCRIMINATORS[0]",
        "query_id": "Search-Basic-1",
        "task_family": "Search-Basic",
        "expected_route": "nextseek_query",
        "inputs": {"query": search_query},
        "expected_exit_code": 0,
    })
    exercises.append({
        "exercise_id": "CORPUS-plan-1",
        "bin_op": "nextseek-plan",
        "proof_paradigm": "forced_cc_nl",
        "upstream_ref": "dmac-assistant@a429f13:tools/e2e/runbook.md:Write-Create-1-adjacent",
        "task_family": "Write-Create",
        "inputs": {"query": "What steps would you recommend to summarize samples treated with NDMA in this project?"},
        "expected_exit_code": 0,
    })

    return {
        "schema_version": "step7-upstream-exercise-catalog/v1",
        "upstream_sha": UPSTREAM_SHA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_files_sha256": {
            str(t18_path.name): _sha256_file(t18_path),
            str(router_path.name): _sha256_file(router_path),
        },
        "exercises": exercises,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--upstream-root",
        default=None,
        help="dmac-assistant repo root (default: DMAC_ASSISTANT_ROOT or ../dmac-assistant)",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: acceptance_evidence/step7/STEP7-UPSTREAM-EXERCISE-CATALOG.json)",
    )
    p.add_argument("--uid", default=None, help="Override GEO UID for generate-submission")
    args = p.parse_args(argv)

    root = Path(args.upstream_root or __import__("os").environ.get(
        "DMAC_ASSISTANT_ROOT", "/home/taishajo/work/dmac-assistant"
    ))
    if not root.is_dir():
        print(f"upstream root not found: {root}", file=sys.stderr)
        return 2

    catalog = build_catalog(root, geo_uid_override=args.uid)
    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parents[1]
        / "acceptance_evidence" / "step7" / "STEP7-UPSTREAM-EXERCISE-CATALOG.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(catalog['exercises'])} exercises)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
