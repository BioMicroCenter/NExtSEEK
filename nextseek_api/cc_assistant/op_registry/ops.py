"""Authoritative CC operation inventory grounded in installed shims and runners."""
from __future__ import annotations

import json
from pathlib import Path

from nextseek_api.cc_assistant.op_registry.models import (
    AllowlistSpec,
    ArgSpec,
    Backend,
    GateClass,
    OpSpec,
    ReadSafeEndpoint,
    SkillRow,
    Transport,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READ_SAFE_PATH = _REPO_ROOT / "nextseek_api" / "assistant" / "read_safe_endpoints.json"
_READ_SAFE = [
    ReadSafeEndpoint(**entry)
    for entry in json.loads(_READ_SAFE_PATH.read_text(encoding="utf-8"))
]

_QUERY_ASYNC = "/nextseek_api/assistant/query/async/"
_RECALL_ENDPOINT = "/nextseek_api/assistant/sessions/"
_LOCAL_ENDPOINT = "local:batch-upload"


def _row(purpose: str, input: str, output: str) -> SkillRow:
    return SkillRow(purpose=purpose, input=input, output=output)


def _dispatch(
    *,
    op_id: str,
    bin_name: str,
    runner_key: str | None = None,
    transport: Transport,
    assistant_endpoint: str,
    gate_class: GateClass,
    argv: list[ArgSpec],
    skill_name: str,
    skill_row: SkillRow,
    per_op_gate_enabled: bool = True,
    read_safe_endpoints: list[ReadSafeEndpoint] | None = None,
    response_envelope_fields: list[str] | None = None,
    published_path: bool = False,
    allowlist: AllowlistSpec | None = None,
) -> OpSpec:
    return OpSpec(
        op_id=op_id,
        bin_name=bin_name,
        runner_key=runner_key or op_id,
        backend=Backend.dispatch,
        runner="bin/_nextseek_runner.py",
        transport=transport,
        assistant_endpoint=assistant_endpoint,
        gate_class=gate_class,
        argv=argv,
        per_op_gate_enabled=per_op_gate_enabled,
        read_safe_endpoints=read_safe_endpoints or [],
        response_envelope_fields=response_envelope_fields or [],
        published_path=published_path,
        allowlist=allowlist or AllowlistSpec(),
        skill_name=skill_name,
        skill_row=skill_row,
    )


def _subcmd(
    *,
    op_id: str,
    bin_name: str,
    runner_key: str,
    argv: list[ArgSpec],
    skill_name: str,
    skill_row: SkillRow,
) -> OpSpec:
    return OpSpec(
        op_id=op_id,
        bin_name=bin_name,
        runner_key=runner_key,
        backend=Backend.subcmd,
        runner="bin/_batch_upload_runner.py",
        transport=Transport.local_subcommand,
        assistant_endpoint=_LOCAL_ENDPOINT,
        gate_class=GateClass.read,
        argv=argv,
        per_op_gate_enabled=False,
        skill_name=skill_name,
        skill_row=skill_row,
    )


OPS: list[OpSpec] = [
    _dispatch(
        op_id="query",
        bin_name="nextseek-query",
        transport=Transport.viewset,
        assistant_endpoint=_QUERY_ASYNC,
        gate_class=GateClass.unrouted,
        per_op_gate_enabled=False,
        argv=[
            ArgSpec(flag="--query", required=True),
            ArgSpec(flag="--planner"),
        ],
        response_envelope_fields=["reply", "debug", "bundle_id"],
        skill_name="nextseek",
        skill_row=_row(
            "Single-shot deterministic NS run in the live chat session; materializes scratch manifest when a bundle is present.",
            '--query "<text>"',
            "{reply, debug, bundle_id} (+ scratch manifest path when applicable)",
        ),
    ),
    _dispatch(
        op_id="plan",
        bin_name="nextseek-plan",
        transport=Transport.viewset,
        assistant_endpoint=_QUERY_ASYNC,
        gate_class=GateClass.unrouted,
        argv=[ArgSpec(flag="--query", required=True)],
        response_envelope_fields=["reply", "debug", "bundle_id"],
        skill_name="nextseek",
        skill_row=_row(
            "Multi-step planner advisor (read-only).",
            '--query "<text>"',
            "{plan, recommended_next_actions, ...}",
        ),
    ),
    _dispatch(
        op_id="recall",
        bin_name="nextseek-recall",
        transport=Transport.viewset,
        assistant_endpoint=_RECALL_ENDPOINT,
        gate_class=GateClass.unrouted,
        per_op_gate_enabled=False,
        argv=[ArgSpec(flag="--turn", required=True)],
        response_envelope_fields=[
            "turn_id",
            "bundle_id",
            "total",
            "row_count",
            "columns",
            "path",
        ],
        skill_name="nextseek",
        skill_row=_row(
            "Fetch a prior turn's raw rows by `--turn N` from the digest — never re-query for data a prior turn already returned.",
            "--turn <N>",
            "{turn_id, bundle_id, total, row_count, columns, path}",
        ),
    ),
    _dispatch(
        op_id="pipeline",
        bin_name="nextseek-pipeline",
        transport=Transport.viewset,
        assistant_endpoint=_QUERY_ASYNC,
        gate_class=GateClass.unrouted,
        argv=[ArgSpec(flag="--message", required=True)],
        skill_name="nextseek",
        skill_row=_row(
            "**Launch** an nf-core pipeline on the cluster (Luria/Tower) — hand a composed cohort summary to the pipeline agent, which then runs the interactive launch wizard.",
            '--message "<summary: explicit UIDs + species/genome + metadata + pipeline>"',
            "{reply, debug, bundle_id}",
        ),
    ),
    _dispatch(
        op_id="entity",
        bin_name="nextseek-entity-extract",
        runner_key="entity",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/entity/",
        gate_class=GateClass.read,
        argv=[ArgSpec(flag="--query", required=True)],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "Resolve NL terms to NExtSEEK vocabulary.",
            '--query "<text>"',
            "{sampletypes, assays, keywords, projects}",
        ),
    ),
    _dispatch(
        op_id="parse",
        bin_name="nextseek-parse",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/parse/",
        gate_class=GateClass.read,
        argv=[ArgSpec(flag="--query", required=True)],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "Turn an NL question into a parser plan.",
            '--query "<text>"',
            "parser plan {mode, target_endpoint, filters, ...}",
        ),
    ),
    _dispatch(
        op_id="graph",
        bin_name="nextseek-graph",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/graph/",
        gate_class=GateClass.read,
        argv=[ArgSpec(flag="--query", required=True)],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "Run a Neo4j lineage/graph query from NL.",
            '--query "<text>"',
            "{cypher, result}",
        ),
    ),
    _dispatch(
        op_id="api-read",
        bin_name="nextseek-api-read",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/api-read/",
        gate_class=GateClass.read,
        read_safe_endpoints=_READ_SAFE,
        argv=[
            ArgSpec(flag="--parser-plan", required=True),
            ArgSpec(flag="--query"),
        ],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "Execute a read-safe REST call from a parser plan.",
            "--parser-plan '<json>'",
            "API response",
        ),
    ),
    _dispatch(
        op_id="api-write",
        bin_name="nextseek-api-write",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/api-write/",
        gate_class=GateClass.write_confirm,
        allowlist=AllowlistSpec(auto_runnable=False),
        argv=[
            ArgSpec(flag="--parser-plan", required=True),
            ArgSpec(flag="--confirmed-write", required=True),
            ArgSpec(flag="--query"),
        ],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "Execute a write (POST/PUT/DELETE) from a parser plan.",
            "--parser-plan '<json>' --confirmed-write",
            "API response",
        ),
    ),
    _dispatch(
        op_id="report",
        bin_name="nextseek-report",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/report/",
        gate_class=GateClass.read,
        published_path=True,
        argv=[
            ArgSpec(
                flag="--mode",
                required=True,
                enum=["samples", "protocols", "published", "rppr"],
            ),
            ArgSpec(flag="--project", required=True),
        ],
        response_envelope_fields=["op", "result", "download"],
        skill_name="nextseek",
        skill_row=_row(
            "Project summary report.",
            "--mode {samples,protocols,published,rppr} --project <name>",
            "report {summary, saved_files, rows}",
        ),
    ),
    _dispatch(
        op_id="generate-submission",
        bin_name="nextseek-generate-submission",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/generate-submission/",
        gate_class=GateClass.read,
        published_path=True,
        argv=[
            ArgSpec(
                flag="--type",
                required=True,
                enum=[
                    "GEO",
                    "SRA",
                    "NFCORE_RNASEQ",
                    "NFCORE_SCRNASEQ",
                    "PRIDE",
                ],
            ),
            ArgSpec(flag="--uids", required=True),
        ],
        response_envelope_fields=["op", "result", "download"],
        skill_name="nextseek",
        skill_row=_row(
            "Build a submission **workbook** (samplesheet/metadata **file**) for a UID set. Does NOT run/launch a pipeline.",
            "--type {GEO,SRA,NFCORE_RNASEQ,NFCORE_SCRNASEQ,PRIDE} --uids <csv>",
            "{report, type}",
        ),
    ),
    _dispatch(
        op_id="run-ls",
        bin_name="nextseek-run-ls",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/run-ls/",
        gate_class=GateClass.read,
        argv=[ArgSpec(flag="--run-dir", required=True)],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "**Reingest step 1** — recursive read-only listing (`ls -laR`) of a finished Luria run directory.",
            "--run-dir <abs path under the Luria runs root>",
            "{tree, truncated, run_dir}",
        ),
    ),
    _dispatch(
        op_id="build-upload-xlsx",
        bin_name="nextseek-build-upload-xlsx",
        transport=Transport.sidecar,
        assistant_endpoint="/nextseek_api/assistant/build-upload-xlsx/",
        gate_class=GateClass.read,
        argv=[
            ArgSpec(flag="--rows", required=True),
            ArgSpec(flag="--existing-parent-uids"),
        ],
        response_envelope_fields=["op", "result"],
        skill_name="nextseek",
        skill_row=_row(
            "**Reingest step 2** — render NExtSEEK 4-sheet upload workbook(s) from composed rows (one per sample type) for the user to review + upload. Does NOT write to NExtSEEK.",
            "--rows '<json array>' [--existing-parent-uids <csv>]",
            "{saved_files, qa}",
        ),
    ),
    _subcmd(
        op_id="attrs",
        bin_name="nextseek-sampletype-attrs",
        runner_key="attrs",
        argv=[
            ArgSpec(flag="--type"),
            ArgSpec(flag="--list"),
        ],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Fetch structured sample-type schema.",
            "[--type] [--list]",
            "schema object(s) per SampleType",
        ),
    ),
    _subcmd(
        op_id="extract",
        bin_name="nextseek-extract-text",
        runner_key="extract",
        argv=[ArgSpec(flag="--file", required=True)],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Extract text from a file.",
            "--file <path>",
            "extracted text",
        ),
    ),
    _subcmd(
        op_id="project-resolve",
        bin_name="nextseek-project-resolve",
        runner_key="project-resolve",
        argv=[
            ArgSpec(flag="--project-id"),
            ArgSpec(flag="--name"),
            ArgSpec(flag="--confirmed"),
            ArgSpec(flag="--out", required=True),
        ],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Resolve a project against the live projects API.",
            "--name '<project name>' --out <token path> [--confirmed]",
            "project ids, titles, confirmation token",
        ),
    ),
    _subcmd(
        op_id="assay-resolve",
        bin_name="nextseek-assay-resolve",
        runner_key="assay-resolve",
        argv=[
            ArgSpec(flag="--project-id", required=True),
            ArgSpec(flag="--title", required=True),
        ],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Resolve assay titles against the selected project.",
            "--project-id <id> --title <title>",
            "assay ids",
        ),
    ),
    _subcmd(
        op_id="sample-search",
        bin_name="nextseek-sample-search",
        runner_key="sample-search",
        argv=[ArgSpec(flag="--uid", required=True)],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Retrieve current sample rows by UID.",
            "--uid <UID> [--uid <UID> ...]",
            "current sample rows",
        ),
    ),
    _subcmd(
        op_id="build-payload",
        bin_name="nextseek-build-payload",
        runner_key="build-payload",
        argv=[
            ArgSpec(flag="--rows", required=True),
            ArgSpec(flag="--schema", required=True),
            ArgSpec(flag="--id-to-title", required=True),
            ArgSpec(flag="--resolved-current"),
            ArgSpec(flag="--out"),
        ],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Build staged upload payloads from source rows.",
            "--rows <json> --schema <schema> --id-to-title <map> [--resolved-current] [--out]",
            "staged payload workbook",
        ),
    ),
    _subcmd(
        op_id="build-validate",
        bin_name="nextseek-validate-upload",
        runner_key="build-validate",
        argv=[
            ArgSpec(flag="--rows", required=True),
            ArgSpec(flag="--project-id", required=True),
            ArgSpec(flag="--project-confirmation", required=True),
            ArgSpec(flag="--checks"),
            ArgSpec(flag="--confirm-clear-assays"),
            ArgSpec(flag="--out"),
        ],
        skill_name="nextseek-batch-upload",
        skill_row=_row(
            "Fused build and validate of an upload workbook.",
            "--rows <json> --project-id <id> --project-confirmation <token>",
            "validation verdict and workbook path",
        ),
    ),
]
