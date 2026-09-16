"""Native dispatch for the 7 granular assistant ops.

Port of the dmac sidecar's ``sidecar/app/ops.py``: each op calls the same
chat_nextseek portable function, with the same argument order, so behavior is
preserved and the dmac sidecar can be rewired to call these endpoints
mechanically. chat_nextseek imports are lazy (deferred to call time) so the
viewset module stays import-light and unit tests can patch the agents.

The single intentional **superset** of dmac behavior is ``graph``: per the design
decision for this work it ALSO executes the Cypher plan via Neo4j and returns the
rows alongside the plan (dmac returns the plan only).

Error taxonomy (mirrors dmac _ws_contract.ERROR_EXIT):
* :class:`OpValidationError` -> VALIDATION
* :class:`~NessieAI.ns.write_gate.WriteBlockedError` -> WRITE_BLOCKED
Any other exception raised by an agent maps to AGENT_FAILED at the viewset layer.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from NessieAI.ns.write_gate import WriteBlockedError  # noqa: F401 (re-exported)


class OpValidationError(ValueError):
    """Bad/missing op arguments. Maps to the canonical VALIDATION error code."""


def _dump(obj: Any) -> Any:
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _load_parser_plan(args: dict) -> Any:
    """Parse ``args['parser_plan']`` as JSON; malformed input -> OpValidationError
    (mirrors the dmac runner's VALIDATION/exit-3 parity for a bad --parser-plan)."""
    try:
        return json.loads(args["parser_plan"])
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise OpValidationError(f"parser_plan is not valid JSON: {exc}") from exc


def run_op(
    op: str,
    args: dict,
    *,
    config: Any,
    session: Any,
    write_gate: Callable,
    neo4j_exec: Callable | None = None,
    outputs_dir: str | None = None,
) -> dict:
    """Dispatch a granular op to its handler and return its result dict."""
    handler = _HANDLERS.get(op)
    if handler is None:
        raise OpValidationError(f"not a sidecar op: {op!r}")
    return handler(args, config, session, write_gate, neo4j_exec, outputs_dir)


def _entity(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import entity_agent
    return _dump(entity_agent(config, args["query"]))


def _parse(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import entity_agent, parser_agent
    entity_out = entity_agent(config, args["query"])
    return _dump(parser_agent(session, config, args["query"], entity_out))


def _graph(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import entity_agent, graph_agent, parser_agent
    entity_out = entity_agent(config, args["query"])
    # Run the parser and pass its plan to graph_agent, mirroring the NS
    # orchestrator (orchestrator.py:869 graph_agent(config, query, entity, plan)).
    # Without the parser_plan the graph agent gets no PARSER PLAN block and emits
    # unbounded, pathological Cypher that overruns the 60s proxy timeout (#20).
    parser_plan = parser_agent(session, config, args["query"], entity_out)
    plan = graph_agent(config, args["query"], entity_out, parser_plan)
    plan_dump = _dump(plan)
    cypher = plan_dump.get("cypher") if isinstance(plan_dump, dict) else getattr(plan, "cypher", None)
    params = (
        plan_dump.get("parameters") if isinstance(plan_dump, dict) else getattr(plan, "parameters", {})
    ) or {}
    exec_fn = neo4j_exec
    if exec_fn is None:
        from chat_nextseek.helpers import tool_neo4j_query
        exec_fn = tool_neo4j_query
    if cypher:
        result = exec_fn(config, cypher, params)
    else:
        result = {"ok": False, "error": "graph agent produced no cypher", "data": []}
    return {"plan": plan_dump, "result": result}


def _api_read(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request
    plan = api_agent_build_request(config, _load_parser_plan(args))
    endpoint, method = plan.endpoint, (plan.method or "").upper()
    write_gate("api-read", endpoint, method, False)  # raises WriteBlocked if not read-safe
    result = helpers.tool_nextseek_api_request(
        config, endpoint, method, requestBody=plan.requestBody, queryParameters=plan.queryParameters
    )
    return {"endpoint": endpoint, "method": method, "api_plan": _dump(plan), "response": result}


def _api_write(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request
    confirmed = args.get("confirmed_write", False)
    write_gate("api-write", None, None, confirmed)  # raises WriteBlocked unless confirmed is True
    plan = api_agent_build_request(config, _load_parser_plan(args))
    result = helpers.tool_nextseek_api_request(
        config, plan.endpoint, plan.method, requestBody=plan.requestBody,
        queryParameters=plan.queryParameters,
    )
    return {
        "endpoint": plan.endpoint, "method": (plan.method or "").upper(),
        "api_plan": _dump(plan), "response": result,
    }


def _report(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek import helpers
    from chat_nextseek.schemas.chat import ReporterPlan
    mode = args["mode"]
    summary_mode = "RPPR" if mode == "rppr" else mode
    rp = ReporterPlan(project=args["project"], reporter_mode="summary", summary_mode=summary_mode)
    log_dir = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    result, saved, summary = helpers.run_reporter_summary(config, rp, log_dir)
    return {"summary": summary, "saved_files": saved, "rows": result}


def _generate_submission(args, config, session, write_gate, neo4j_exec, outputs_dir):
    # Route through the SAME orchestration the NS run_query report_generation
    # path uses (generate_report_outputs), rather than calling the leaf
    # report_writer_agent directly. That gives the op, for every report type:
    #   * the type-specific template (load_report_template) -> bounded output
    #     (a template-less call free-forms and overruns the writer's output-token
    #     cap, truncating the JSON -> AGENT_FAILED);
    #   * the full reporter_context (metadata hydration, protocols, plans);
    #   * the emitters that persist the REAL submission workbooks under
    #     saved_files (geo_seq_workbooks / sra_* / pride_* / nfcore_* / ...),
    #     which the bundle/download + CC staging then serve.
    # See GitHub issue #21 (reporter port defect / drift).
    from chat_nextseek.portable import generate_report_outputs, report_writer_agent
    from chat_nextseek.schemas.chat import ReporterPlan

    uids = [u.strip() for u in args["uids"].split(",") if u.strip()]
    report_type = args["type"]
    # A non-empty user query is required: some providers (Bedrock/Opus Converse)
    # reject a blank message content block. Fall back to a type-aware default when
    # the caller supplies no query, so the op is robust to query=None / "".
    user_query = (args.get("query") or "").strip() or (
        f"Generate a {report_type} submission report for the provided sample UIDs."
    )
    reporter_plan = ReporterPlan(
        report_type=report_type,
        uids=uids,
        reporter_mode="report_generation",
        reporter_context={"per_sample_reports": False},
    )
    log_dir = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    _reporter_result, report_writer_output, saved_files, _reply = generate_report_outputs(
        config=config,
        user_query=user_query,
        parser_plan={"report_type": report_type},
        reporter_plan=reporter_plan,
        uids=uids,
        log_dir=log_dir,
        report_writer_fn=report_writer_agent,
        per_sample_reports=False,
    )
    # Combined mode wraps the writer output as {"all_samples": <writer output>}.
    # Unwrap to the flat writer dict to preserve the op's existing result shape,
    # and attach the real saved_files so the download bundle + CC staging serve
    # the actual generated report file.
    flat = report_writer_output
    if isinstance(report_writer_output, dict) and "all_samples" in report_writer_output:
        flat = report_writer_output["all_samples"]
    result = dict(flat) if isinstance(flat, dict) else {"report_type": report_type, "report": flat}
    result["saved_files"] = saved_files or {}
    return result



_RUN_LS_CAP = 2_000_000  # bytes of `ls -laR` returned to CC before truncation (well under the 16 MiB WS cap)


def _validate_run_dir(args: dict, config: Any) -> tuple[str, dict]:
    """Shared by run-ls / run-harvest: the run dir must be under the cluster
    runs root. An unvalidated run_dir is an arbitrary read of the shared
    Luria account.
    """
    luria_env = getattr(config, "LURIA_ENV", None) or {}
    working_path = str(luria_env.get("working_path") or "").rstrip("/")
    if not working_path or not luria_env.get("key"):
        raise OpValidationError("Luria is not configured (LURIA_ENV incomplete)")
    runs_root = working_path + "/runs"
    run_dir = os.path.normpath(str(args["run_dir"]))
    if run_dir != runs_root and not run_dir.startswith(runs_root + "/"):
        raise OpValidationError(f"run_dir must be under {runs_root}")
    return run_dir, luria_env


def _run_ls(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Read-only recursive listing of a finished Luria run dir (reingest input).

    Validates ``run_dir`` is under ``<LURIA working_path>/runs`` (no traversal),
    then SSHes Luria and runs ``ls -laR``. Returns the tree text (capped). Never
    writes to Luria.
    """
    import shlex
    run_dir, luria_env = _validate_run_dir(args, config)
    from chat_nextseek.luria.ssh import prepare_key, ssh_run
    key_path = prepare_key(luria_env["key"])
    out = ssh_run(luria_env, f"ls -laR {shlex.quote(run_dir)}", key_path=key_path)
    return {"run_dir": run_dir, "truncated": len(out) > _RUN_LS_CAP, "tree": out[:_RUN_LS_CAP]}


# The remote-side half of the run-harvest op's staging: matches GENERIC_GLOBS
# on the CLUSTER, using Python's own pathlib.Path.glob -- the same call
# harvest_local makes locally -- rather than the remote shell's globbing.
#
# GENERIC_GLOBS' MultiQC pattern ("multiqc*/**/*_data/multiqc_*.txt") is a
# multi-segment "**" pattern, which pathlib treats as zero-or-more
# intervening directories. A POSIX shell only matches "**" that way when the
# `globstar` option is explicitly turned on -- which a non-interactive
# `ssh host cmd` invocation does NOT do by default -- and even then a dash/sh
# login shell (a plausible remote default) does not support "**" at all.
# Confirmed directly: `ls -1d multiqc*/**/*_data/multiqc_*.txt` (no globstar)
# silently returns nothing -- no error, just an empty listing -- for a run
# whose MultiQC directory has zero or two intervening directories, while
# pathlib.Path.glob resolves both correctly. That is exactly the failure this
# staging method exists to avoid: every sample's `metrics`/`derived` would
# come back empty, with nothing to say why.
#
# The remote script tars every match into ONE stream piped back over a
# single ssh round trip, instead of one `ls` + one `cat` per file: a
# `cat`-per-file transfer also runs every file's bytes through this
# process's own text codec (ssh_run decodes stdout as text), which can alter
# a byte before harvest_local ever reads it.
#
# Two things the SSH account's confinement to `run_dir` depends on are
# enforced HERE, on the cluster side, because by the time bytes reach this
# process the read has already happened:
#
# 1. Symlinks. `run_dir` sits on a SHARED filesystem: the pipeline itself, or
#    anyone else with write access to it, can drop a symlink inside an
#    otherwise-valid run_dir that points anywhere on the cluster. Earlier,
#    this script called `path.is_file()` (which dereferences symlinks) and
#    opened the tar with `dereference=True`, on the reasoning that "a local
#    extract can never follow a link outside the staging directory." That
#    reasoning only protects the LOCAL extract step below -- the remote read
#    of the symlink's target has already happened on the cluster by then,
#    which is the side the shared account actually needs confining on. So
#    every match is now checked, before it is added to the tar, for (a) not
#    being a symlink itself and (b) resolving to a path still inside the
#    resolved run_dir (catches a symlinked ancestor directory too). Anything
#    that fails either check is skipped and reported in the trailing
#    `__nextseek_stage_report__.json` tar entry (parsed back out by
#    `_stage_run_dir`) rather than silently dropped -- see Important 1 of the
#    2026-09-16 review.
# 1b. Hardlinks. A follow-up adversarial pass on the same 2026-09-16 review
#    found the symlink checks above miss a hardlink: `os.link(outside, run_dir
#    / "x.csv")` makes `x.csv` a directory entry INSIDE run_dir that shares an
#    inode with a file elsewhere -- it is not a symlink (`is_symlink()` is
#    False) and has no separate target path to resolve away from
#    (`resolve()` returns itself), so neither existing check catches it and
#    the outside file's real content would be tarred and shipped. Every match
#    is now also checked for `st_nlink > 1` and skipped (reported the same
#    way) if so. This is deliberately blunt -- it also rejects a benign
#    multiply-linked file -- but GENERIC_GLOBS is small QC/metadata text
#    (params, versions, samplesheets, MultiQC/RSeQC tables), not the large
#    deduplicated BAM/FASTQ pipeline outputs where multi-link files actually
#    occur, so false positives here should be rare. Residual: on a filesystem
#    without `fs.protected_hardlinks`, an attacker who already has write
#    access to run_dir is the threat this closes -- it does not defend
#    against a write-capable adversary using some OTHER, still-unknown
#    mechanism to make a directory entry alias an outside inode.
# 2. Size/count caps. `harvest_local`'s MAX_FILE_BYTES / MAX_TOTAL_BYTES /
#    MAX_FILES caps used to apply only after the ENTIRE tar had already been
#    transferred and extracted (`ssh_run_bytes` buffers the whole stream in
#    memory). RSeQC writes per-sample files at ~108 MB in real runs, so an
#    oversized tree was fully shipped over the wire before ever being
#    capped. The same three caps (imported from `harvest`, never
#    re-invented, so the two halves cannot drift) are now enforced here,
#    remotely, before a match is added to the tar -- an oversized file or an
#    over-cap tree fails fast and is never transferred. Cap hits are
#    reported the same way as skipped symlinks. See Important 2 of the
#    2026-09-16 review.
_STAGE_REPORT_NAME = "__nextseek_stage_report__.json"

_STAGE_SCRIPT = """\
import io, json, pathlib, sys, tarfile
run_dir = pathlib.Path(sys.argv[1])
resolved_run_dir = run_dir.resolve()
max_file_bytes = int(sys.argv[2])
max_total_bytes = int(sys.argv[3])
max_files = int(sys.argv[4])
report_name = sys.argv[5]
patterns = sys.argv[6:]
seen = set()
skipped = []
total_bytes = 0
files_added = 0
tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
for pattern in patterns:
    for path in sorted(run_dir.glob(pattern)):
        rel = str(path.relative_to(run_dir))
        if rel in seen:
            continue
        seen.add(rel)
        if path.is_symlink():
            skipped.append({"path": rel, "reason": "symlink (run_dir confinement cannot follow it safely)"})
            continue
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(resolved_run_dir)
        except ValueError:
            skipped.append({"path": rel, "reason": "resolves outside run_dir"})
            continue
        st = path.stat()
        if st.st_nlink > 1:
            # Hardlink: a directory entry inside run_dir sharing an inode with
            # a file elsewhere. No separate target path exists to resolve
            # away from (unlike a symlink), so this can only be caught by the
            # link count itself. Fail closed -- see the "1b. Hardlinks" note
            # above _STAGE_SCRIPT for why false positives here should be rare.
            skipped.append({"path": rel, "reason": "hardlinked; cannot confirm no other path reaches it"})
            continue
        size = st.st_size
        if size > max_file_bytes:
            skipped.append({"path": rel, "reason": "exceeds max file bytes (%d > %d)" % (size, max_file_bytes)})
            continue
        if total_bytes + size > max_total_bytes:
            skipped.append({"path": rel, "reason": "exceeds total byte cap (%d + %d > %d)" % (total_bytes, size, max_total_bytes)})
            continue
        if files_added >= max_files:
            skipped.append({"path": rel, "reason": "exceeds max file count (%d)" % max_files})
            continue
        # TOCTOU: the resolve()/stat() checks above and this tar.add() are not
        # atomic -- the path could be swapped between them. This window is
        # currently inert only because (a) tar.add() no longer dereferences
        # symlinks (dereference=True was removed, see "1." above) and (b) the
        # local extraction in _stage_run_dir filters on member.isfile(), so a
        # symlink swapped in here would tar as a symlink member and be
        # dropped on extraction, not followed. A future edit to either of
        # those two behaviours reopens this window -- keep them paired.
        tar.add(str(path), arcname=rel)
        total_bytes += size
        files_added += 1
report = json.dumps({"skipped": skipped}).encode()
info = tarfile.TarInfo(name=report_name)
info.size = len(report)
tar.addfile(info, fileobj=io.BytesIO(report))
tar.close()
"""


def _stage_run_dir(luria_env: dict, run_dir: str, staged_dir: str, key_path: str) -> list[dict]:
    """Stage every ``harvest.GENERIC_GLOBS`` match from ``run_dir`` on Luria
    into ``staged_dir``, ready for ``harvest.harvest_local``. See
    ``_STAGE_SCRIPT`` above for why this runs the glob matching remotely, in
    Python, and packs the matches into a single tar stream rather than an
    `ls` + per-file `cat`, and for the symlink/cap enforcement it does before
    ever adding a match to that stream.

    Returns the list of ``{"path", "reason"}`` entries the remote script
    skipped (symlinks, escapes, or cap hits) so the caller can surface them
    rather than let the omission pass silently.
    """
    import io
    import shlex
    import tarfile

    from chat_nextseek.luria.ssh import ssh_run_bytes
    from NessieAI.ns.reingest.harvest import GENERIC_GLOBS, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES

    remote_cmd = " ".join([
        "python3", "-c", shlex.quote(_STAGE_SCRIPT), shlex.quote(run_dir),
        shlex.quote(str(MAX_FILE_BYTES)), shlex.quote(str(MAX_TOTAL_BYTES)), shlex.quote(str(MAX_FILES)),
        shlex.quote(_STAGE_REPORT_NAME),
        *(shlex.quote(pattern) for pattern in GENERIC_GLOBS),
    ])
    tar_bytes = ssh_run_bytes(luria_env, remote_cmd, key_path=key_path)
    skipped: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|") as tar:
        for member in tar:
            if member.name == _STAGE_REPORT_NAME:
                report_fileobj = tar.extractfile(member)
                skipped = json.loads(report_fileobj.read()).get("skipped", []) if report_fileobj else []
                continue
            if not member.isfile():
                continue
            name = os.path.normpath(member.name)
            if name.startswith("..") or os.path.isabs(name):
                raise OpValidationError(f"staged tar entry escapes run_dir: {member.name!r}")
            tar.extract(member, path=staged_dir)
    return skipped


def _run_harvest(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Reingest step 1 — stage the allowlisted files off the cluster and parse
    them into a RunManifest. Read-only; like run-ls it never calls the write
    gate (see nextseek_api/assistant/CONTRACT.md).
    """
    import tempfile

    from NessieAI.ns.reingest import harvest
    from NessieAI.ns.reingest.store import save_manifest

    run_dir, luria_env = _validate_run_dir(args, config)
    from chat_nextseek.luria.ssh import prepare_key
    key_path = prepare_key(luria_env["key"])
    try:
        with tempfile.TemporaryDirectory() as staged:
            skipped = _stage_run_dir(luria_env, run_dir, staged, key_path)
            run_manifest = harvest.harvest_local(staged, lookup_by_fastq=_d_seq_by_fastq)
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    run_manifest.run_dir = run_dir

    # Files the remote stage skipped (a symlink escaping run_dir, or a
    # size/count cap hit) must stay visible, not vanish quietly -- surfaced
    # both in the manifest's own warnings and in the op result below.
    skipped = skipped or []
    run_manifest.warnings.extend(
        f"staging skipped {item['path']}: {item['reason']}" for item in skipped)

    # A failed run's outputs may be partial or truncated. Registering them into a
    # database of record by default is the wrong choice; proceeding is a decision
    # the user makes explicitly.
    if run_manifest.execution.failed and not args.get("allow_failed_run"):
        raise OpValidationError(
            f"{run_manifest.execution.failed} process(es) failed in this run. "
            f"Re-run with --allow-failed-run to reingest it anyway.")

    manifest_id = save_manifest(run_manifest)
    return {"run_dir": run_dir, "manifest_id": manifest_id,
            "manifest": run_manifest.model_dump(), "skipped": skipped}


def _d_seq_by_fastq(path: str) -> list[str]:
    """D.SEQ UIDs whose File_PrimaryData / Link_PrimaryData mentions ``path``."""
    from nextseek_api.services.reingest_lookups import uids_by_primary_data
    return uids_by_primary_data(path)


def _build_upload_xlsx(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Render one 4-sheet upload workbook per A.* sample type from CC-composed rows.

    args["rows"]: JSON array of {"SampleType", "json_metadata", "assay_ids"}. Runs QA
    per type (a HARD_REJECT type is skipped, its report returned). Returns the rendered
    workbooks under ``saved_files`` plus the per-type QA reports. No NExtSEEK write —
    the user reviews the workbook(s) and uploads them via the batch-upload UI.
    """
    from NessieAI.ns.reingest_qa import HARD_REJECT, qa_rows
    from NessieAI.ns.upload_workbook import render_upload_workbook

    try:
        rows = json.loads(args["rows"])
    except ValueError as exc:
        raise OpValidationError(f"rows is not valid JSON: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise OpValidationError("rows must be a non-empty JSON array")

    existing = {u.strip() for u in str(args.get("existing_parent_uids") or "").split(",") if u.strip()}

    by_type: dict[str, list] = {}
    for row in rows:
        st = str((row or {}).get("SampleType") or "").strip()
        if not st:
            raise OpValidationError("every row needs a SampleType")
        by_type.setdefault(st, []).append(row)

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    known = set(by_type)  # permissive here; the real catalog validates on upload
    saved_files: dict[str, str] = {}
    qa: dict[str, dict] = {}
    for st, st_rows in by_type.items():
        report = qa_rows(st_rows, sample_type=st, known_sampletypes=known,
                         existing_parent_uids=existing)
        qa[st] = {"disposition": report.disposition, "hard": report.hard, "soft": report.soft}
        if report.disposition == HARD_REJECT:
            continue
        safe_name = st.replace("/", "_").replace(" ", "_")          # readable filename (keeps the dot)
        # The artifact KEY is the download URL segment, which the route only
        # accepts as [\w]+ — so it must be word-chars only (A.SCXP -> A_SCXP).
        # The file on disk keeps the dot; download serves it by its real name.
        safe_key = safe_name.replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}.xlsx")
        render_upload_workbook(st, st_rows, path)
        saved_files[f"reingest_{safe_key}"] = path
    return {"saved_files": saved_files, "qa": qa}

_HANDLERS: dict[str, Callable] = {
    "entity": _entity,
    "parse": _parse,
    "graph": _graph,
    "api-read": _api_read,
    "api-write": _api_write,
    "report": _report,
    "generate-submission": _generate_submission,
    "run-ls": _run_ls,
    "build-upload-xlsx": _build_upload_xlsx,
    "run-harvest": _run_harvest,
}
