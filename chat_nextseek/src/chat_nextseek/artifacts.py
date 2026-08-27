from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def guess_file_mime(filename: str) -> str:
    """Infer a file MIME type from its extension, defaulting to binary."""
    ext = Path(filename).suffix.lower()
    return {
        ".json": "application/json",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".tsv": "text/tab-separated-values",
    }.get(ext, "application/octet-stream")


def build_file_manifest_entry(
    key: str,
    label: str,
    path: str | Path | None,
    filename: str | None = None,
    mime: str | None = None,
    *,
    kind: str | None = None,
    bundle_id: int | None = None,
    step_id: int | None = None,
) -> dict[str, Any] | None:
    """Return a standard file-manifest entry when the path exists on disk."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    name = filename or p.name
    entry: dict[str, Any] = {
        "key": key,
        "label": label,
        "path": str(p),
        "filename": name,
        "mime": mime or guess_file_mime(name),
    }
    if kind:
        entry["kind"] = kind
    if bundle_id is not None:
        entry["bundle_id"] = bundle_id
    if step_id is not None:
        entry["step_id"] = step_id
    return entry


def _drop_none_values(value: dict[str, Any]) -> dict[str, Any]:
    """Keep metadata compact while preserving false/zero/empty-list values."""
    return {k: v for k, v in value.items() if v is not None}


def build_metadata_bundle(
    *,
    bundle_id: int,
    mode: str,
    user_query: str,
    parser_plan: dict[str, Any] | None = None,
    api_plan: dict[str, Any] | None = None,
    api_result_full: dict[str, Any] | None = None,
    api_result_slim: dict[str, Any] | None = None,
    graph_plan: dict[str, Any] | None = None,
    graph_result: dict[str, Any] | None = None,
    reporter_plan: dict[str, Any] | None = None,
    reporter_result: dict[str, Any] | None = None,
    report_writer_output: dict[str, Any] | None = None,
    report_saved_files: dict[str, Any] | None = None,
    planner_output: dict[str, Any] | None = None,
    multi_parser_plan: dict[str, Any] | None = None,
    step_results: dict[Any, Any] | None = None,
    terminal_reply: str | None = None,
    provisional_reply: str | None = None,
    memory_payload: dict[str, Any] | None = None,
    search_context: dict[str, Any] | None = None,
    files: list[dict[str, Any]] | None = None,
    paths: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the canonical session metadata bundle used by both standard and planner runs.

    The top-level aliases keep compatibility with existing memory/refine/evaluator
    code. The `model_outputs` block is the stable home for structured outputs from
    parser, planner, tool, reporter, graph, and chatter stages.
    """
    paths = paths or {}
    model_outputs = _drop_none_values(
        {
            "parser_plan": parser_plan,
            "api_plan": api_plan,
            "api_result_full": api_result_full,
            "api_result_slim": api_result_slim,
            "graph_plan": graph_plan,
            "graph_result": graph_result,
            "reporter_plan": reporter_plan,
            "reporter_result": reporter_result,
            "report_writer_output": report_writer_output,
            "planner_output": planner_output,
            "multi_parser_plan": multi_parser_plan,
            "step_results": step_results,
            "terminal_reply": terminal_reply,
            "provisional_reply": provisional_reply,
            "memory_payload": memory_payload,
            "search_context": search_context,
        }
    )
    endpoint = None
    method = None
    request_body = {}
    query_params = {}
    if isinstance(api_plan, dict):
        endpoint = api_plan.get("endpoint")
        method = api_plan.get("method")
        request_body = api_plan.get("requestBody") or {}
        query_params = api_plan.get("queryParameters") or {}
    if isinstance(search_context, dict):
        endpoint = search_context.get("endpoint") or endpoint
        method = search_context.get("method") or method
        request_body = search_context.get("request_body") or request_body
        query_params = search_context.get("query_params") or query_params

    bundle = {
        "id": bundle_id,
        "timestamp": datetime.now().isoformat(),
        "user_query": user_query,
        "mode": mode,
        "model_outputs": model_outputs,
        "memory_payload": memory_payload,
        "search_context": search_context or {},
        "terminal_reply": terminal_reply,
        "reply": terminal_reply,
        "provisional_reply": provisional_reply,
        "parser_plan": parser_plan,
        "api_plan": api_plan,
        "endpoint": endpoint,
        "method": method,
        "request_body": request_body,
        "query_params": query_params,
        "api_result_full": api_result_full,
        "api_result_slim": api_result_slim,
        "raw_result_path": paths.get("raw_result_path"),
        "graph_plan": graph_plan,
        "graph_result": graph_result,
        "graph_debug_path": paths.get("graph_debug_path"),
        "reporter_plan": reporter_plan,
        "reporter_result": reporter_result,
        "report_writer_output": report_writer_output,
        "report_saved_files": report_saved_files or {},
        "plan": planner_output,
        "multi_parser_plan": multi_parser_plan,
        "step_results": step_results or {},
        "raw_result_paths": paths.get("raw_result_paths") or {},
        "graph_debug_paths": paths.get("graph_debug_paths") or {},
        "plan_debug_path": paths.get("plan_debug_path"),
        "paths": paths,
        "files": files or [],
    }
    if extra:
        bundle.update(extra)
    return bundle


class ArtifactStore:
    """Persist artifacts under a run log directory and emit standard manifest entries."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _artifact_dir(self, kind: str | None = None, subdir: str | None = None) -> Path:
        path = self.base_dir
        if kind:
            path = path / kind
        if subdir:
            path = path / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(
        self,
        *,
        key: str,
        label: str,
        filename: str,
        payload: Any,
        kind: str,
        subdir: str | None = None,
        bundle_id: int | None = None,
        step_id: int | None = None,
    ) -> dict[str, Any] | None:
        path = self._artifact_dir(kind, subdir) / filename
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return build_file_manifest_entry(
            key,
            label,
            path,
            kind=kind,
            bundle_id=bundle_id,
            step_id=step_id,
        )

    def write_text(
        self,
        *,
        key: str,
        label: str,
        filename: str,
        payload: str,
        kind: str,
        subdir: str | None = None,
        bundle_id: int | None = None,
        step_id: int | None = None,
        mime: str | None = None,
    ) -> dict[str, Any] | None:
        path = self._artifact_dir(kind, subdir) / filename
        path.write_text(payload, encoding="utf-8")
        return build_file_manifest_entry(
            key,
            label,
            path,
            mime=mime,
            kind=kind,
            bundle_id=bundle_id,
            step_id=step_id,
        )

    def write_bytes(
        self,
        *,
        key: str,
        label: str,
        filename: str,
        payload: bytes,
        kind: str,
        subdir: str | None = None,
        bundle_id: int | None = None,
        step_id: int | None = None,
        mime: str | None = None,
    ) -> dict[str, Any] | None:
        path = self._artifact_dir(kind, subdir) / filename
        path.write_bytes(payload)
        return build_file_manifest_entry(
            key,
            label,
            path,
            mime=mime,
            kind=kind,
            bundle_id=bundle_id,
            step_id=step_id,
        )

    def register_path(
        self,
        *,
        key: str,
        label: str,
        path: str | Path | None,
        kind: str,
        filename: str | None = None,
        mime: str | None = None,
        bundle_id: int | None = None,
        step_id: int | None = None,
    ) -> dict[str, Any] | None:
        return build_file_manifest_entry(
            key,
            label,
            path,
            filename=filename,
            mime=mime,
            kind=kind,
            bundle_id=bundle_id,
            step_id=step_id,
        )


def build_saved_report_file_manifest(
    saved_files: dict[str, Any] | None,
    *,
    key_prefix: str = "",
) -> list[dict[str, Any]]:
    """Normalize saved report artifacts into the standard file manifest."""
    reporter_file_labels: dict[str, str] = {
        "reporter_result": "Reporter result JSON",
        "samples_report": "Samples report JSON",
        "protocols_report": "Protocols report JSON",
        "published_report": "Published report JSON",
        "uuid_report_file": "UUID list",
        "merged_report": "Merged report JSON",
        "metadata": "Metadata JSON",
        "protocols": "Protocols JSON",
        "protocol_files": "Protocol files JSON",
        "geo_seq_workbooks": "GEO submission workbook",
        "sra_submission_workbooks": "SRA submission workbook",
        "sra_biosample_workbooks": "SRA BioSample workbook",
        "pride_submission_px": "PRIDE submission.px",
        "pride_sdrf": "PRIDE SDRF (experimental design)",
        "nfcore_csv_files": "nf-core CSV",
    }
    reporter_file_kinds: dict[str, str] = {
        "reporter_result": "report",
        "samples_report": "report",
        "protocols_report": "report",
        "published_report": "report",
        "uuid_report_file": "report",
        "merged_report": "report",
        "metadata": "report",
        "protocols": "protocol",
        "protocol_files": "protocol",
        "geo_seq_workbooks": "export",
        "sra_submission_workbooks": "export",
        "sra_biosample_workbooks": "export",
        "pride_submission_px": "export",
        "pride_sdrf": "export",
        "nfcore_csv_files": "export",
    }

    def _prefixed(key: str) -> str:
        return f"{key_prefix}{key}" if key_prefix else key

    result_files: list[dict[str, Any]] = []
    for key, path_value in (saved_files or {}).items():
        if isinstance(path_value, (list, tuple)):
            base_label = reporter_file_labels.get(key) or key.replace("_", " ")
            for i, item_path in enumerate(path_value):
                entry = build_file_manifest_entry(
                    _prefixed(f"{key}_{i}"),
                    f"{base_label} {i + 1}",
                    item_path,
                    kind=reporter_file_kinds.get(key, "report"),
                )
                if entry:
                    result_files.append(entry)
            continue

        label = reporter_file_labels.get(key) or (
            f"Report writer output ({key.replace('report_writer_output_', '')})"
            if key.startswith("report_writer_output_")
            else key
        )
        entry = build_file_manifest_entry(
            _prefixed(key),
            label,
            path_value,
            kind=reporter_file_kinds.get(
                key,
                "report_writer_output" if key.startswith("report_writer_output_") else "report",
            ),
        )
        if entry:
            result_files.append(entry)

    return result_files
