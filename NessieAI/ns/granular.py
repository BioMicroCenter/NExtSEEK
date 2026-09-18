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


def _validate_run_dir(args: dict, config: Any) -> tuple[str, dict, str]:
    """Shared by run-ls / run-harvest / run-checksum: the run dir must be
    under the cluster runs root. An unvalidated run_dir is an arbitrary read
    of the shared Luria account.

    This check is purely LEXICAL (``normpath`` + a string prefix test) --
    it says nothing about where ``run_dir`` actually resolves on the
    cluster. That is deliberate: this process has no filesystem access to
    Luria, only SSH, so the only place a symlink can be resolved is on the
    remote side. Callers that go on to read files under ``run_dir``
    (run-harvest's ``_STAGE_SCRIPT``, run-checksum's ``_CHECKSUM_SCRIPT``)
    MUST also confine against the returned ``runs_root``, resolved
    REMOTELY -- never against a resolve() of ``run_dir`` itself. See
    Important 1 of the 2026-09-16 whole-branch review: a symlink at
    ``<runs_root>/foo`` pointing to ``/home/someone/else`` passes this
    lexical check (the string still looks like a normal subpath) and then,
    if a remote script anchors its containment check on
    ``run_dir.resolve()``, RE-ANCHORS confinement to the symlink's target --
    every file under that target then trivially "resolves inside run_dir".
    Anchoring on ``runs_root.resolve()`` instead closes this: a file
    reached through the target directory no longer resolves inside the
    runs root, symlinked run_dir or not.
    """
    luria_env = getattr(config, "LURIA_ENV", None) or {}
    working_path = str(luria_env.get("working_path") or "").rstrip("/")
    if not working_path or not luria_env.get("key"):
        raise OpValidationError("Luria is not configured (LURIA_ENV incomplete)")
    runs_root = working_path + "/runs"
    run_dir = os.path.normpath(str(args["run_dir"]))
    if run_dir != runs_root and not run_dir.startswith(runs_root + "/"):
        raise OpValidationError(f"run_dir must be under {runs_root}")
    return run_dir, luria_env, runs_root


def _run_ls(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Read-only recursive listing of a finished Luria run dir (reingest input).

    Validates ``run_dir`` is under ``<LURIA working_path>/runs`` (no traversal),
    then SSHes Luria and runs ``ls -laR``. Returns the tree text (capped). Never
    writes to Luria.

    Unlike run-harvest/run-checksum, this op does NOT need the
    run_dir-is-itself-a-symlink guard (Important 1, 2026-09-16 review):
    ``ls -laR <run_dir>`` never dereferences a symlink named on its own
    command line, and ``-R`` does not follow symlinks it encounters while
    recursing either -- so a symlinked run_dir only ever lists as a single
    symlink entry, never its target's contents.
    """
    import shlex
    run_dir, luria_env, _runs_root = _validate_run_dir(args, config)
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
# 1c. run_dir ITSELF as a symlink. `_validate_run_dir`'s check is lexical
#    only (see its docstring) -- it never asks the cluster whether run_dir
#    resolves anywhere. Earlier, this script anchored every containment
#    check on `run_dir.resolve()`: if run_dir were, say,
#    `<runs_root>/foo` -> `/home/someone/else` (a symlink, not a plain
#    directory), that resolve() call RELOCATES the anchor to
#    `/home/someone/else`, and every file glob-matched through it then
#    trivially "resolves inside run_dir" -- the confinement re-anchors to
#    whatever the caller's symlink points at. The anchor is now
#    `runs_root.resolve()` instead (passed in as its own argument, computed
#    the same way in `_CHECKSUM_SCRIPT`): a directory entry reached through
#    a symlinked run_dir no longer resolves inside the runs root, so it is
#    caught by the same relative_to() check as any other escape. If
#    run_dir itself escapes the runs root this way, every match fails that
#    check -- reported via `run_dir_escapes_runs_root` in the trailing
#    report entry, which `_stage_run_dir` turns into a hard
#    OpValidationError (this is a caller-named top-level path, not an
#    incidental glob hit -- see Important 1 of the 2026-09-16 whole-branch
#    review).
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
#
# 3. The inventory (nfcore-reingest addendum). The same script also walks
#    `harvest.INVENTORY_GLOBS` -- the candidate OUTPUT files (BAMs, merged
#    matrices, h5ad/mtx/rds objects, VCFs, the MultiQC html report,
#    contaminant reports) -- and emits one `{"path", "bytes"}` entry per
#    match into the trailing report, alongside `skipped`. This is a LISTING
#    only: nothing here is added to the tar, nothing is read, nothing is
#    transferred beyond the path string and the integer from `stat()`.
#    Listing a file's name and size is a smaller disclosure than shipping
#    its content, but the account is shared all the same, so every
#    inventory candidate passes through the EXACT SAME containment checks as
#    a staged one -- reject a symlink, reject anything that resolves outside
#    `resolved_runs_root`, reject `st_nlink > 1` -- before it is ever listed.
#    A candidate that fails any of those, or that arrives after
#    `max_inventory_files` matches have already been listed, is skipped and
#    reported exactly like a staging skip, never dropped silently. See
#    `harvest.INVENTORY_GLOBS` and `manifest.RunManifest.outputs` /
#    `.named_outputs` for the two different consumers this feeds.
_STAGE_REPORT_NAME = "__nextseek_stage_report__.json"

# Auto-hash the cheap ones: thresholds for hashing an inventoried output
# during run-harvest's existing staging SSH call -- never a second round
# trip. Deliberately separate module constants from run-checksum's own
# `_CHECKSUM_MAX_FILE_BYTES` / `_CHECKSUM_MAX_TOTAL_BYTES` below: those bound
# an EXPLICIT, caller-requested hash of files the caller already knows are
# worth the wait (up to 10 GB each, 20 GB aggregate); these bound an
# AUTOMATIC, no-one-asked-for-it hash that happens on every run-harvest call,
# so they must be small enough that harvest_local's own
# `_HARVEST_SSH_TIMEOUT_S` (150s, itself under the 180s CC-turn hard cap) is
# never meaningfully at risk from hashing alone -- getting a manifest back at
# all matters far more than getting a checksum for free. At a conservative
# shared-cluster-disk throughput of ~150 MB/s (the same figure run-checksum's
# own comment uses), the 500 MB aggregate ceiling hashes in a bit over 3
# seconds -- negligible next to the tar transfer and local extraction that
# already dominate a harvest call's wall clock. The 100 MB per-file ceiling
# covers a typical MultiQC html report, a small per-sample count matrix, or a
# modest BAM; it deliberately excludes the multi-GB primary alignment files
# this whole feature exists to avoid blocking on -- those still need
# `run-checksum` explicitly.
_HARVEST_CHECKSUM_MAX_FILE_BYTES = int(
    os.environ.get("NEXTSEEK_HARVEST_CHECKSUM_MAX_FILE_BYTES", 100_000_000))
_HARVEST_CHECKSUM_MAX_TOTAL_BYTES = int(
    os.environ.get("NEXTSEEK_HARVEST_CHECKSUM_MAX_TOTAL_BYTES", 500_000_000))

_STAGE_SCRIPT = """\
import hashlib, io, json, pathlib, sys, tarfile
run_dir = pathlib.Path(sys.argv[1])
runs_root = pathlib.Path(sys.argv[2])
resolved_runs_root = runs_root.resolve()
resolved_run_dir = run_dir.resolve()
max_file_bytes = int(sys.argv[3])
max_total_bytes = int(sys.argv[4])
max_files = int(sys.argv[5])
max_inventory_files = int(sys.argv[6])
max_checksum_file_bytes = int(sys.argv[7])
max_checksum_total_bytes = int(sys.argv[8])
report_name = sys.argv[9]
n_patterns = int(sys.argv[10])
patterns = sys.argv[11:11 + n_patterns]
inventory_patterns = sys.argv[11 + n_patterns:]
skipped = []
total_bytes = 0
files_added = 0
tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
run_dir_escapes_runs_root = False
try:
    resolved_run_dir.relative_to(resolved_runs_root)
except ValueError:
    run_dir_escapes_runs_root = True


def confined_stat(path, rel):
    # The SAME containment guard, shared by staging and the inventory
    # listing below: not a symlink, resolves inside the runs root, and no
    # extra hardlink alias. Returns None (already recorded in `skipped`) if
    # rejected, else the path's stat() result. A non-regular-file match (a
    # directory, a fifo, ...) returns None WITHOUT being reported -- that is
    # an ordinary glob miss, not an adversarial one.
    if path.is_symlink():
        skipped.append({"path": rel, "reason": "symlink (run_dir confinement cannot follow it safely)"})
        return None
    if not path.is_file():
        return None
    try:
        path.resolve().relative_to(resolved_runs_root)
    except ValueError:
        skipped.append({"path": rel, "reason": "resolves outside run_dir"})
        return None
    st = path.stat()
    if st.st_nlink > 1:
        # Hardlink: a directory entry inside run_dir sharing an inode with
        # a file elsewhere. No separate target path exists to resolve
        # away from (unlike a symlink), so this can only be caught by the
        # link count itself. Fail closed -- see the "1b. Hardlinks" note
        # above _STAGE_SCRIPT for why false positives here should be rare.
        skipped.append({"path": rel, "reason": "hardlinked; cannot confirm no other path reaches it"})
        return None
    return st


inventory = []
if not run_dir_escapes_runs_root:
    seen = set()
    for pattern in patterns:
        for path in sorted(run_dir.glob(pattern)):
            rel = str(path.relative_to(run_dir))
            if rel in seen:
                continue
            seen.add(rel)
            st = confined_stat(path, rel)
            if st is None:
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

    seen_inventory = set()
    checksum_bytes_used = 0
    for pattern in inventory_patterns:
        for path in sorted(run_dir.glob(pattern)):
            rel = str(path.relative_to(run_dir))
            if rel in seen_inventory:
                continue
            seen_inventory.add(rel)
            st = confined_stat(path, rel)
            if st is None:
                continue
            if len(inventory) >= max_inventory_files:
                skipped.append({"path": rel, "reason": "exceeds max inventory file count (%d)" % max_inventory_files})
                continue
            entry = {"path": rel, "bytes": st.st_size}

            # Checksum_PrimaryData, for free where possible (nfcore-reingest
            # addendum): hash the file itself, bounded twice (a per-file
            # ceiling and a running total budget across the whole run -- see
            # _HARVEST_CHECKSUM_MAX_FILE_BYTES/_HARVEST_CHECKSUM_MAX_TOTAL_BYTES
            # above for why these numbers). Exhausting either bound is a
            # reported `skipped` entry, never a failure -- getting the rest
            # of the manifest back matters more than one more checksum.
            if st.st_size > max_checksum_file_bytes:
                skipped.append({"path": rel, "reason": "exceeds max checksum file bytes (%d > %d); use run-checksum explicitly" % (st.st_size, max_checksum_file_bytes)})
            elif checksum_bytes_used + st.st_size > max_checksum_total_bytes:
                skipped.append({"path": rel, "reason": "exceeds checksum byte budget (%d + %d > %d); use run-checksum explicitly" % (checksum_bytes_used, st.st_size, max_checksum_total_bytes)})
            else:
                h = hashlib.md5()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                entry["checksum"] = h.hexdigest()
                checksum_bytes_used += st.st_size

            inventory.append(entry)

report = json.dumps({
    "skipped": skipped,
    "run_dir_escapes_runs_root": run_dir_escapes_runs_root,
    "inventory": inventory,
}).encode()
info = tarfile.TarInfo(name=report_name)
info.size = len(report)
tar.addfile(info, fileobj=io.BytesIO(report))
tar.close()
"""


def _stage_run_dir(luria_env: dict, run_dir: str, runs_root: str, staged_dir: str,
                    key_path: str) -> tuple[list[dict], list[dict]]:
    """Stage every ``harvest.GENERIC_GLOBS`` match from ``run_dir`` on Luria
    into ``staged_dir``, ready for ``harvest.harvest_local``, and separately
    LIST (never stage) every ``harvest.INVENTORY_GLOBS`` match. See
    ``_STAGE_SCRIPT`` above for why this runs the glob matching remotely, in
    Python, and packs the staged matches into a single tar stream rather than
    an `ls` + per-file `cat`, and for the symlink/cap enforcement it does
    before ever adding a match to that stream or the inventory listing.

    ``runs_root`` (not ``run_dir``) is the confinement anchor the remote
    script resolves against -- see "1c. run_dir ITSELF as a symlink" above
    _STAGE_SCRIPT. If the remote script reports ``run_dir`` itself resolves
    outside ``runs_root``, that is a hard :class:`OpValidationError`, not a
    skip: ``run_dir`` is a caller-named top-level path, not an incidental
    glob hit.

    Returns ``(skipped, inventory)``: ``skipped`` is the list of
    ``{"path", "reason"}`` entries the remote script skipped (symlinks,
    escapes, hardlinks, or cap hits -- from either half) so the caller can
    surface them rather than let the omission pass silently; ``inventory``
    is the list of ``{"path", "bytes"}`` entries -- plus an optional
    ``"checksum"`` key when the file was cheap enough to hash under
    ``_HARVEST_CHECKSUM_MAX_FILE_BYTES``/``_HARVEST_CHECKSUM_MAX_TOTAL_BYTES``
    (see the checksum block in ``_STAGE_SCRIPT`` above) -- for
    ``harvest_local``'s ``inventory`` parameter.
    """
    import io
    import shlex
    import tarfile

    from chat_nextseek.luria.ssh import ssh_run_bytes
    from NessieAI.ns.reingest.harvest import (
        GENERIC_GLOBS, INVENTORY_GLOBS, MAX_FILE_BYTES, MAX_FILES, MAX_INVENTORY_FILES, MAX_TOTAL_BYTES,
    )

    remote_cmd = " ".join([
        "python3", "-c", shlex.quote(_STAGE_SCRIPT), shlex.quote(run_dir), shlex.quote(runs_root),
        shlex.quote(str(MAX_FILE_BYTES)), shlex.quote(str(MAX_TOTAL_BYTES)), shlex.quote(str(MAX_FILES)),
        shlex.quote(str(MAX_INVENTORY_FILES)),
        shlex.quote(str(_HARVEST_CHECKSUM_MAX_FILE_BYTES)), shlex.quote(str(_HARVEST_CHECKSUM_MAX_TOTAL_BYTES)),
        shlex.quote(_STAGE_REPORT_NAME), shlex.quote(str(len(GENERIC_GLOBS))),
        *(shlex.quote(pattern) for pattern in GENERIC_GLOBS),
        *(shlex.quote(pattern) for pattern in INVENTORY_GLOBS),
    ])
    # Bounded, not indefinite: see _HARVEST_SSH_TIMEOUT_S below for why this
    # backstops a stalled shared filesystem rather than an oversized
    # request (the size/count caps enforced remotely, above, are that).
    tar_bytes = ssh_run_bytes(luria_env, remote_cmd, key_path=key_path, timeout=_HARVEST_SSH_TIMEOUT_S)
    skipped: list[dict] = []
    inventory: list[dict] = []
    run_dir_escapes_runs_root = False
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|") as tar:
        for member in tar:
            if member.name == _STAGE_REPORT_NAME:
                report_fileobj = tar.extractfile(member)
                report = json.loads(report_fileobj.read()) if report_fileobj else {}
                skipped = report.get("skipped", [])
                inventory = report.get("inventory", [])
                run_dir_escapes_runs_root = report.get("run_dir_escapes_runs_root", False)
                continue
            if not member.isfile():
                continue
            name = os.path.normpath(member.name)
            if name.startswith("..") or os.path.isabs(name):
                raise OpValidationError(f"staged tar entry escapes run_dir: {member.name!r}")
            tar.extract(member, path=staged_dir)
    if run_dir_escapes_runs_root:
        raise OpValidationError(f"run_dir resolves outside the runs root: {run_dir!r}")
    return skipped, inventory


# Wall-clock backstop for the run-harvest staging SSH call, mirroring
# run-checksum's _CHECKSUM_SSH_TIMEOUT_S (added in an earlier review pass;
# see its docstring for the full reasoning). run-harvest's own byte/count
# ceilings (harvest.MAX_FILE_BYTES / MAX_TOTAL_BYTES / MAX_FILES, enforced
# remotely in _STAGE_SCRIPT before any match is added to the tar) bound the
# LEGITIMATE work; this bounds the illegitimate/unexpected case (a stalled
# shared filesystem) instead. ssh_run_bytes had no timeout kwarg at all
# until this review pass (Important 2, 2026-09-16): a stalled remote glob
# or a wedged network hung the whole CC turn indefinitely, unlike
# run-checksum's ssh_run call, which already had one. Sized the same as
# run-checksum's ceiling (150s), comfortably under the 180s CC turn hard
# cap (NEXTSEEK_CC_TIMEOUT_HARD_MAX, cc_engine.py) with room left for the
# rest of the turn (local tar extraction + harvest_local parsing).
_HARVEST_SSH_TIMEOUT_S = int(os.environ.get("NEXTSEEK_HARVEST_SSH_TIMEOUT_S", 150))


def _run_harvest(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Reingest step 1 — stage the allowlisted files off the cluster and parse
    them into a RunManifest. Read-only; like run-ls it never calls the write
    gate (see nextseek_api/assistant/CONTRACT.md).
    """
    import tempfile

    from NessieAI.ns.reingest import harvest
    from NessieAI.ns.reingest.store import save_manifest

    run_dir, luria_env, runs_root = _validate_run_dir(args, config)
    from chat_nextseek.luria.ssh import prepare_key
    key_path = prepare_key(luria_env["key"])
    try:
        with tempfile.TemporaryDirectory() as staged:
            skipped, inventory = _stage_run_dir(luria_env, run_dir, runs_root, staged, key_path)
            # run_dir (the cluster path) is passed through as harvest_local's
            # provenance/lookup label -- `staged` is only the local read root.
            # Without this, the manifest and PipelineRun lookup are both keyed
            # off the temp staging path, which never matches the launch
            # record's run_dir (see harvest_local's docstring). `inventory` is
            # the remote INVENTORY_GLOBS listing -- harvest_local never sees
            # `run_dir` (the real cluster directory) itself, only `staged`, so
            # it cannot gather that listing on its own.
            run_manifest = harvest.harvest_local(
                staged, run_dir=run_dir, lookup_by_fastq=_d_seq_by_fastq,
                sample_type_lookup=_sample_types_for_uids, inventory=inventory)
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass

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


_CHECKSUM_MAX_FILES = int(os.environ.get("NEXTSEEK_CHECKSUM_MAX_FILES", 200))

# Byte ceilings for run-checksum, deliberately NOT harvest.py's MAX_FILE_BYTES
# / MAX_TOTAL_BYTES (4 MB / 32 MB). Those cap small QC/metadata TEXT files
# (params.json, MultiQC tables, ...) that get fully read into memory and
# parsed into a manifest -- a ceiling sized for that job would reject a
# legitimate BAM/FASTQ outright, which is exactly the primary data this op
# exists to checksum. These ceilings instead bound the one thing that makes
# run-checksum the DoS this op was split out to avoid (see the docstring
# below and Important 1 of the 2026-09-16 review): the remote hashing time.
# Sized so that _CHECKSUM_MAX_FILES (200) files at the per-file ceiling could
# never blow the SSH call's own timeout (_CHECKSUM_SSH_TIMEOUT_S below) even
# at a conservative shared-cluster-disk md5 throughput (~150 MB/s): the
# aggregate ceiling (20 GB) hashes in ~135s at that rate, comfortably inside
# a 150s SSH timeout that itself sits under the default 180s CC turn hard cap
# (NEXTSEEK_CC_TIMEOUT_HARD_MAX, cc_engine.py) with room left for the rest of
# the turn. The per-file ceiling (10 GB) is generous for one sequencing file
# without needing to equal the aggregate.
_CHECKSUM_MAX_FILE_BYTES = int(os.environ.get("NEXTSEEK_CHECKSUM_MAX_FILE_BYTES", 10_000_000_000))
_CHECKSUM_MAX_TOTAL_BYTES = int(os.environ.get("NEXTSEEK_CHECKSUM_MAX_TOTAL_BYTES", 20_000_000_000))
# Wall-clock backstop for the SSH call itself: the byte ceilings above bound
# the LEGITIMATE work to well under this, so this timeout exists to bound the
# illegitimate/unexpected case (a stalled shared filesystem, a wedged
# network) rather than to be the primary defense against an oversized
# request -- that job is the byte ceilings, enforced before any file is
# opened for reading (see _CHECKSUM_SCRIPT).
_CHECKSUM_SSH_TIMEOUT_S = int(os.environ.get("NEXTSEEK_CHECKSUM_SSH_TIMEOUT_S", 150))

# The remote-side half of run-checksum: unlike run-harvest's GENERIC_GLOBS
# matches, ``--paths`` is CALLER-supplied -- an explicit request, not an
# incidental glob hit. So every one of the guards below is surfaced as an
# "escaped" entry that _run_checksum turns into a hard OpValidationError,
# never folded silently into "skipped" (see the module docstring and
# test_run_checksum_op.py's docstring for why that distinction matters).
# The one exception is the byte-ceiling checks added below: per BINDING
# CONSTRAINTS a size refusal may be a skip, but it is never silent -- it is
# always reported by name in "skipped", exactly like a missing file.
#
# The escape guards mirror _STAGE_SCRIPT's exactly, and for the same reason:
# the read already happens on the cluster side, before any byte reaches this
# process, so confinement can only be enforced there.
# 1. Symlinks. A path inside run_dir can be a symlink pointing anywhere on
#    the shared cluster account; is_symlink() is checked before anything
#    else touches the path.
# 2. Escape via a symlinked ancestor directory (or any other resolution
#    mismatch): resolve() must still land inside the resolved run_dir.
# 3. Hardlinks. `os.link(outside, run_dir/x)` makes `x` a directory entry
#    INSIDE run_dir sharing an inode with a file elsewhere -- it is not a
#    symlink and has no separate target path to resolve away from, so only
#    `st_nlink > 1` catches it. Deliberately blunt (also rejects a benign
#    multiply-linked file), same trade-off as run-harvest's staging guard.
# A missing file or a non-regular-file match (a directory, a fifo, ...) is
# NOT an escape -- those are ordinary misses, reported in "skipped".
#
# 4. Size. `st.st_size` is read from the SAME stat() call already made for
#    the hardlink check above -- so the per-file/total ceilings below are
#    enforced BEFORE `open()`/`read()` ever touches the file. A file over the
#    per-file ceiling is skipped without a single byte read; once the
#    running total would cross the aggregate ceiling, every remaining path is
#    skipped the same way -- unhashed. This is what closes Important 1: 200
#    caller-named multi-GB files can no longer make this op read past a
#    bounded amount of data no matter how large the files actually are.
# 5. run_dir ITSELF as a symlink (Important 1, 2026-09-16 whole-branch
#    review). Anchored on `run_dir.resolve()`, every ``rel`` reached through
#    a symlinked run_dir used to "resolve inside run_dir" trivially, for the
#    same reason _STAGE_SCRIPT's equivalent bug did -- see its "1c." note.
#    Anchoring on `runs_root.resolve()` instead (passed in as its own
#    argument) means every ``rel`` fails the relative_to() check the moment
#    run_dir escapes the runs root, landing in ``escaped`` -- which
#    `_run_checksum` already turns into a hard OpValidationError, exactly
#    like any other caller-named escape.
_CHECKSUM_SCRIPT = """\
import hashlib, json, pathlib, sys
run_dir = pathlib.Path(sys.argv[1])
runs_root = pathlib.Path(sys.argv[2])
resolved_runs_root = runs_root.resolve()
resolved_run_dir = run_dir.resolve()
max_file_bytes = int(sys.argv[3])
max_total_bytes = int(sys.argv[4])
rels = sys.argv[5:]
checksums = {}
skipped = []
escaped = []
total_bytes = 0
run_dir_escapes_runs_root = False
try:
    resolved_run_dir.relative_to(resolved_runs_root)
except ValueError:
    run_dir_escapes_runs_root = True


for rel in rels:
    if run_dir_escapes_runs_root:
        escaped.append({"path": rel, "reason": "run_dir resolves outside the runs root"})
        continue
    path = run_dir / rel
    if path.is_symlink():
        escaped.append({"path": rel, "reason": "symlink (run_dir confinement cannot follow it safely)"})
        continue
    if not path.exists():
        skipped.append({"path": rel, "reason": "does not exist"})
        continue
    if not path.is_file():
        skipped.append({"path": rel, "reason": "not a regular file"})
        continue
    try:
        path.resolve().relative_to(resolved_runs_root)
    except ValueError:
        escaped.append({"path": rel, "reason": "resolves outside run_dir"})
        continue
    st = path.stat()
    if st.st_nlink > 1:
        escaped.append({"path": rel, "reason": "hardlinked; cannot confirm no other path reaches it"})
        continue
    size = st.st_size
    if size > max_file_bytes:
        skipped.append({"path": rel, "reason": "exceeds max file bytes (%d > %d)" % (size, max_file_bytes)})
        continue
    if total_bytes + size > max_total_bytes:
        skipped.append({"path": rel, "reason": "exceeds total byte cap (%d + %d > %d)" % (total_bytes, size, max_total_bytes)})
        continue
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    checksums[rel] = h.hexdigest()
    total_bytes += size
print(json.dumps({"checksums": checksums, "skipped": skipped, "escaped": escaped}))
"""


def _run_checksum(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Reingest step 2 — md5 a settled set of primary-data files on the cluster.

    Separate from run-harvest on purpose: which files become File_PrimaryData is
    only known after sample types are assigned, and hashing multi-GB BAMs would
    put the harvest outside the CC turn's wall clock. Read-only; like run-ls and
    run-harvest it never calls the write gate (see
    nextseek_api/assistant/CONTRACT.md).

    ``paths`` is caller-supplied, not a glob match, so every containment guard
    below (traversal, symlink, hardlink, ancestor escape) is a hard
    OpValidationError rather than a silent "skipped" entry -- an explicit
    request to read outside run_dir is adversarial, not incidental. Only a
    genuinely missing/non-regular-file match is a reported skip.

    The file COUNT cap (``_CHECKSUM_MAX_FILES``) alone does not bound how
    long this op can hang: 200 files can still be arbitrarily large, which is
    exactly the wall-clock blowout this op was split out to avoid (see
    above). ``_CHECKSUM_MAX_FILE_BYTES`` / ``_CHECKSUM_MAX_TOTAL_BYTES`` close
    that gap remotely, inside ``_CHECKSUM_SCRIPT``, before a single byte of
    an oversized file is read; a size refusal is reported in "skipped", never
    silently dropped. ``_CHECKSUM_SSH_TIMEOUT_S`` bounds the SSH call itself
    so an unexpected hang (not just an oversized request) is also bounded.

    An optional ``manifest_id`` folds this call's checksums into a manifest
    run-harvest already saved: loaded, merged (``RunManifest.checksums``,
    additive -- an earlier checksum for a path not named this time survives),
    and re-saved. ``store.save_manifest`` derives the id from a content
    digest, so a manifest carrying checksums is a genuinely DIFFERENT,
    immutable manifest -- this never mutates the manifest behind the
    original id, which keeps loading exactly what it always has. The new id
    is returned as ``manifest_id`` for the caller to thread into
    ``build-upload-xlsx`` in place of the original. Omitting ``manifest_id``
    keeps every existing caller's behaviour byte-identical (no such key in
    the result at all), since not every caller of this read-only op has a
    manifest to fold checksums into.
    """
    import shlex

    run_dir, luria_env, runs_root = _validate_run_dir(args, config)
    rels = [p.strip() for p in str(args.get("paths") or "").split(",") if p.strip()]
    if not rels:
        raise OpValidationError("paths must name at least one file")
    if len(rels) > _CHECKSUM_MAX_FILES:
        raise OpValidationError(
            f"too many files: {len(rels)} > {_CHECKSUM_MAX_FILES}; narrow the set")
    for rel in rels:
        joined = os.path.normpath(os.path.join(run_dir, rel))
        if joined != run_dir and not joined.startswith(run_dir + "/"):
            raise OpValidationError(f"path outside the run dir: {rel!r}")

    # Validated -- and the manifest loaded, if named -- before the expensive
    # remote SSH hashing below, the same "fail cheap before failing
    # expensive" ordering _validate_run_dir already applies to run_dir/paths.
    manifest_id = str(args.get("manifest_id") or "").strip()
    run_manifest = None
    if manifest_id:
        if not manifest_id.isalnum():
            raise OpValidationError(f"manifest_id must be alphanumeric, got {manifest_id!r}")
        from NessieAI.ns.reingest.store import load_manifest
        try:
            run_manifest = load_manifest(manifest_id)
        except FileNotFoundError:
            raise OpValidationError(f"no manifest {manifest_id!r}")
        # Both `run_dir` and `manifest_id` are caller-supplied off the same CC
        # turn, and nothing else ties them together: a session that reingests
        # run A then run B, but passes run A's stale manifest_id while hashing
        # run B's files, would otherwise have this call's checksums merge into
        # run A's manifest. Relative output paths collide across nf-core runs
        # by construction (every run has its own "star_salmon/SAMPLE_1...bam"),
        # so `_primary_output` would then match run A's OutputRecord for that
        # identical path and ship run B's digest as run A's measured value --
        # exactly the model-authored-metadata failure the manifest-id design
        # exists to eliminate, just laundered through a caller mismatch
        # instead of a hand-typed number. Cheap (no I/O) and done before the
        # expensive SSH hash, like every other check in this function.
        # `_validate_run_dir` already normalised `run_dir` with `normpath`;
        # normalise the manifest's own `run_dir` the same way before
        # comparing, since `harvest.harvest_local` stores it verbatim from
        # whatever `_run_harvest` passed in (also `_validate_run_dir`'s
        # output, but from that separate call, not necessarily
        # byte-identical as a raw string).
        manifest_run_dir = os.path.normpath(str(run_manifest.run_dir))
        if manifest_run_dir != run_dir:
            raise OpValidationError(
                f"manifest_id {manifest_id!r} was harvested from {manifest_run_dir!r}, "
                f"not the run_dir being hashed ({run_dir!r})")

    from chat_nextseek.luria.ssh import prepare_key, ssh_run
    key_path = prepare_key(luria_env["key"])
    try:
        remote_cmd = " ".join([
            "python3", "-c", shlex.quote(_CHECKSUM_SCRIPT), shlex.quote(run_dir), shlex.quote(runs_root),
            shlex.quote(str(_CHECKSUM_MAX_FILE_BYTES)), shlex.quote(str(_CHECKSUM_MAX_TOTAL_BYTES)),
            *(shlex.quote(rel) for rel in rels),
        ])
        # Bounded, not indefinite: see _CHECKSUM_SSH_TIMEOUT_S above for why
        # the byte ceilings (not this timeout) are the primary defense, and
        # this is the backstop for the unexpected hang (a stalled shared
        # filesystem) rather than the oversized request.
        out = ssh_run(luria_env, remote_cmd, key_path=key_path, timeout=_CHECKSUM_SSH_TIMEOUT_S)
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass

    try:
        payload = json.loads(out)
    except ValueError as exc:
        raise OpValidationError(f"checksum script produced no parseable output: {exc}") from exc

    # A refusal is reported, never a silent omission: every escaped path is
    # named, with its reason, in the raised message -- not dropped, and not
    # quietly merged into "skipped" (see the module note above _CHECKSUM_SCRIPT).
    escaped = payload.get("escaped") or []
    if escaped:
        detail = "; ".join(f"{item['path']!r}: {item['reason']}" for item in escaped)
        raise OpValidationError(f"path outside the run dir or unsafe: {detail}")

    checksums = payload.get("checksums") or {}
    result = {"run_dir": run_dir, "checksums": checksums,
              "skipped": payload.get("skipped") or []}

    if run_manifest is not None:
        from NessieAI.ns.reingest.store import save_manifest
        # Merge keyed by the NORMALISED path, not the caller's raw `--paths`
        # token verbatim: `OutputRecord.path` is harvest-normalised (see
        # harvest.py), so a caller-supplied "./star_salmon/x.bam" or
        # "star_salmon//x.bam" would otherwise land in `checksums` under a
        # key `mapper._primary_output`'s harvested path never matches,
        # silently losing the checksum this call just paid an SSH round trip
        # to compute. `result["checksums"]` (returned to the caller) keeps
        # the raw token as its key -- it is just an echo of what was asked
        # for, not a lookup key into the manifest.
        run_manifest.checksums.update(
            {os.path.normpath(rel): digest for rel, digest in checksums.items()})
        result["manifest_id"] = save_manifest(run_manifest)

    return result


def _d_seq_by_fastq(path: str, types: tuple[str, ...] = ("D.SEQ",)) -> list[str]:
    """UIDs of a sample type in ``types`` whose File_PrimaryData /
    Link_PrimaryData mentions ``path``. Defaults to D.SEQ only, this
    lookup's original scope -- ``harvest.harvest_local`` passes the run's
    own pipeline map's ``accepts_parent_types`` here instead when it has
    one (see maps.PipelineMap.accepts_parent_types)."""
    from nextseek_api.services.reingest_lookups import uids_by_primary_data
    return uids_by_primary_data(path, types=types)


def _sample_types_for_uids(uids: list[str]) -> dict[str, str]:
    """The SampleType title per resolved parent UID, for the QC backfill.

    A parent is no longer necessarily a D.SEQ -- a pipeline whose map declares
    a wider ``accepts_parent_types`` can be launched from an already-analysed
    A.* sample -- so the backfill row must be typed from what the parent
    actually IS. The UID's own prefix is not a safe source for that: measured
    against the live instance, 766 of 51,372 samples do not start with their
    type code, so parsing it would be quietly wrong for those. This asks the
    database instead. A UID it cannot resolve is OMITTED, and the backfill
    writes no row for that sample rather than guessing a type."""
    from nextseek_api.services.reingest_lookups import sample_types_for_uids
    return sample_types_for_uids(uids)


def _build_upload_xlsx(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Reingest step 2 — render reviewable workbooks.

    Two calling conventions:

    * ``args["manifest_id"]`` (current) — the manifest-driven path. CC sends
      only a manifest_id and a mode; the server loads its own copy of the
      harvested manifest (``store.load_manifest``), maps it
      (``mapper.apply``), and derives every row itself. No measured number
      ever round-trips through the model. Writes no NExtSEEK data — proposals
      are RETURNED in the envelope; the service layer
      (``nextseek_api/services/assistant.py``) persists them alongside the
      artifact bundle.
    * ``args["rows"]`` (legacy) — CC-composed rows, kept working verbatim for
      any caller that still builds its own
      ``{"SampleType", "json_metadata", "assay_ids"}`` rows directly.
    """
    if args.get("manifest_id"):
        return _build_upload_xlsx_from_manifest(args, outputs_dir)
    if args.get("rows") is not None:
        return _build_upload_xlsx_from_rows(args, outputs_dir)
    raise OpValidationError("build-upload-xlsx needs either manifest_id or rows")


def _build_upload_xlsx_from_rows(args, outputs_dir):
    """Legacy path: render one 4-sheet upload workbook per A.* sample type from
    CC-composed rows.

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

    from nextseek_api.services.reingest_lookups import attributes_for, known_sample_types

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    # known_sample_types() returning empty means "the catalog could not be read"
    # (context_catalog's house rule: any failure costs the caller an empty
    # catalog, never an exception), not "every type here is unknown". Taking it
    # literally would hard-reject a whole reingest whenever the catalog table is
    # briefly unreachable. So an EMPTY catalog falls back to the old permissive
    # set(by_type) — derived from the rows themselves, so unknown_sampletype
    # can't fire — and only a POPULATED catalog lets that check actually reject.
    #
    # This is DELIBERATELY inconsistent with the manifest-driven path below
    # (_build_upload_xlsx_from_manifest), which raises on the same empty-catalog
    # signal instead of falling back (matching proposals.attribute_exists' own
    # "an entirely empty catalog is an outage, not an answer" rule). The two
    # paths differ because their callers differ: the manifest path is Task 7's
    # DB-backed reingest pipeline, where an outage silently producing an
    # unvalidated workbook is the exact failure the fallback-vs-raise design
    # decision exists to prevent. This legacy rows path is called with
    # CC-composed rows from callers that make no assumption the sample-type
    # table is reachable (or even present) at all, and
    # test_build_upload_xlsx_op.py::test_empty_catalog_falls_back_to_permissive_known_types
    # pins exactly this permissive behaviour, deliberately without django_db, so
    # that it can exercise a genuinely unreachable table. Raising here would
    # hard-reject every legacy call whenever that table is briefly unreachable,
    # which is the regression this fallback was written to avoid in the first
    # place -- so it stays, and this comment is the reconciliation the review
    # asked for rather than a silent, unexplained divergence between the two.
    known = known_sample_types() or set(by_type)
    saved_files: dict[str, str] = {}
    qa: dict[str, dict] = {}
    for st, st_rows in by_type.items():
        attrs = attributes_for(st)
        required = [a["title"] for a in attrs if a.get("required")]
        # a.get("server_required", a.get("required")): an attributes_for
        # entry that never set the new key (an un-updated test double, or a
        # caller mocking the old two-key shape) falls back to that same
        # entry's own "required" -- keeping today's HARD-everything
        # behaviour for anyone who hasn't wired the new flag through, per
        # reingest_qa.qa_rows' own None-vs-explicit-list contract.
        server_required = [a["title"] for a in attrs if a.get("server_required", a.get("required"))]
        report = qa_rows(st_rows, sample_type=st, known_sampletypes=known,
                         required_fields=required, server_required_fields=server_required,
                         existing_parent_uids=existing)
        qa[st] = {"disposition": report.disposition, "hard": report.hard, "soft": report.soft}
        if report.disposition == HARD_REJECT:
            continue
        safe_name = st.replace("/", "_").replace(" ", "_")          # readable filename (keeps the dot)
        # The artifact KEY is the download URL segment, which the route only
        # accepts as [\w]+ — so it must be word-chars only (A.SCXP -> A_SCXP).
        # The file on disk keeps the dot; download serves it by its real name.
        #
        # NessieAI/ns/reingest/report.py's `_resolve_artifact` reconstructs
        # this same key from a bare sample type to look the workbook back up,
        # and duplicates this exact normalisation rather than importing it
        # from here (report.py may not import granular.py). If you change
        # this normalisation, change the copy there too, or a sample type
        # with a hyphen/space/slash silently drops out of the QA report.
        safe_key = safe_name.replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}.xlsx")
        render_upload_workbook(st, st_rows, path)
        saved_files[f"reingest_{safe_key}"] = path
    return {"saved_files": saved_files, "qa": qa}


def _existing_notes(rows) -> dict[str, str]:
    """Current Notes for every UID in ``rows``.

    A UID absent from the result means the fetch failed for it, and QA turns that
    into a hard reject rather than letting a blind write destroy curator text.
    """
    from nextseek_api.services.reingest_lookups import notes_for_uids

    uids = [str((r.get("json_metadata") or {}).get("UID") or "").strip()
            for r in rows]
    return notes_for_uids([u for u in uids if u])


def _build_upload_xlsx_from_manifest(args, outputs_dir):
    """Manifest-driven path: render workbooks from a harvested manifest.

    Takes a manifest_id, not values: CC sends a mode and the server fills every
    number from its own copy of the manifest (``store.load_manifest`` +
    ``mapper.apply``). A measured value therefore never round-trips through
    the model.

    Writes no NExtSEEK data. Attribute proposals are RETURNED; the service
    layer persists them alongside the artifact bundle -- this function must
    never call the recording function itself (see NessieAI/ns/CLAUDE.md's
    "build-upload-xlsx never writes to NExtSEEK" invariant).

    A mapped D.SEQ backfill attribute (map- or approved-origin) that is not
    actually defined on D.SEQ's schema (``proposals.attribute_exists``) is
    parked into that sample's ``Notes`` instead of a normal column (never
    silently invented as a real attribute), and a ``needs_definition``
    proposal is queued for it -- see
    docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md section 8.
    ``attribute_exists`` raises on an outage (an entirely empty catalog) by
    design; that exception is deliberately let through here rather than
    caught and defaulted to False, which would fabricate a schema gap.
    """
    import datetime
    import re

    from NessieAI.ns.reingest import mapper, maps, proposals, report as user_report
    from NessieAI.ns.reingest.notes import compose as compose_notes
    from NessieAI.ns.reingest.store import load_manifest
    from NessieAI.ns.reingest_qa import HARD, HARD_REJECT, Finding, NO_ATTRIBUTES_TO_WRITE, qa_rows
    from NessieAI.ns.upload_workbook import MODE_NEW, MODE_UPDATE, render_upload_workbook
    from nextseek_api.services.reingest_lookups import attributes_for, known_sample_types

    def _slug(name: str) -> str:
        # Artifact keys are word characters only; the download route accepts
        # nothing else -- re.sub(r"\W+", ...) also folds whitespace (unlike
        # the legacy safe_name at :889, which only replaces "/" and " "
        # explicitly), so a pipeline name with a space still produces a
        # route-safe key.
        return re.sub(r"\W+", "_", name.split("/")[-1])

    manifest_id = str(args.get("manifest_id") or "").strip()
    if not manifest_id.isalnum():
        raise OpValidationError(f"manifest_id must be alphanumeric, got {manifest_id!r}")
    mode = str(args.get("mode") or MODE_NEW)
    if mode not in (MODE_NEW, MODE_UPDATE):
        raise OpValidationError(f"mode must be {MODE_NEW!r} or {MODE_UPDATE!r}")

    # manifest_id is user-supplied (comes off the CC turn, not a value this
    # process minted), so an unknown-but-well-formed id must be a caller-visible
    # refusal, not the FileNotFoundError load_manifest's open() raises escaping
    # as an opaque 502.
    try:
        run_manifest = load_manifest(manifest_id)
    except FileNotFoundError:
        raise OpValidationError(f"no manifest {manifest_id!r}")
    pipeline = run_manifest.pipeline.name or "nf-core/rnaseq"
    run_name = run_manifest.pipeline.run_name or manifest_id
    pipeline_map = maps.load(pipeline)
    result = mapper.apply(run_manifest, pipeline_map,
                          approved_rules=proposals.approved_rules(pipeline))

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    saved_files, qa, reports_by_type = {}, {}, {}
    provenance_by_type: dict[str, list] = {}
    rows_by_type: dict[str, list] = {}
    needs_definition: list[dict] = []
    # Surface 2 of the superuser surfaces: a File_PrimaryData the mapper
    # picked among two or more same-basename candidates with no checksum to
    # decisively break the tie (see mapper.py's `MappedAttribute.candidates`
    # docstring). Collected here, not left in `result.rows`, so it can ride
    # into `render_qa_for_user`'s reply the same way `needs_definition`
    # already rides into `proposals` below -- reusing `result.unmapped`
    # would have been wrong: that channel means "a raw metric key nobody
    # claimed", a different fact from "an attribute WAS set, but the pick
    # among ambiguous candidates was not forced by evidence".
    #
    # Keyed on (sample_type, attribute, tuple(candidates)) rather than
    # appended as a flat list: a per_run output rule (e.g. a shared
    # gene-counts matrix) sets the SAME ambiguous attribute, with the SAME
    # candidate set, on every one of a run's sample rows, so a flat list
    # would relay one near-identical entry per sample -- exactly the
    # enumerate-instead-of-count failure report.py's own module docstring
    # (rule 1) exists to prevent, and on a 20-sample run it is ~20 entries
    # for one genuine ambiguity. A true per-sample rule's candidates differ
    # by sample (the harvested path embeds the sample name), so those stay
    # distinct entries here, each affecting exactly the samples that hit it.
    ambiguous_primary_groups: dict[tuple, dict] = {}
    # (sample_type, attribute) -> bool, memoised so a 20-sample backfill does
    # not re-query the schema catalog once per sample for the same attribute.
    exists_cache: dict[tuple[str, str], bool] = {}
    today = datetime.date.today().isoformat()

    def _attribute_exists(sample_type: str, attribute: str) -> bool:
        key = (sample_type, attribute)
        if key not in exists_cache:
            # Deliberately not try/except'd: an outage (RuntimeError) must
            # propagate, never be swallowed into a fabricated False -- see
            # proposals.attribute_exists' own docstring.
            exists_cache[key] = proposals.attribute_exists(sample_type, attribute)
        return exists_cache[key]

    # rows carrying at least one parked value, so their Notes can be composed
    # once existing Notes for their UIDs are known (below, after this loop).
    dseq_parked: list[tuple[dict, dict, dict]] = []

    for row in result.rows:
        want_update = row.sample_type == "D.SEQ"
        if (mode == MODE_UPDATE) != want_update:
            continue

        meta: dict = {}
        provenance_entry: dict = {}
        parked_values: dict = {}
        parked_attrs: dict = {}
        for name, attr in row.attributes.items():
            origin = attr.origin
            if (want_update and name != "Parent"
                    and not _attribute_exists(row.sample_type, name)):
                origin = mapper.ORIGIN_PARKED
                parked_values[name] = attr.value
                parked_attrs[name] = attr
            else:
                meta[name] = attr.value
            provenance_entry[name] = {"origin": origin, "raw_key": attr.raw_key}
            if attr.candidates:
                group_key = (row.sample_type, name, tuple(attr.candidates))
                group = ambiguous_primary_groups.setdefault(group_key, {
                    "sample_type": row.sample_type, "attribute": name,
                    "chosen": attr.source_file, "candidates": attr.candidates,
                    "sample_count": 0,
                })
                group["sample_count"] += 1

        if row.uid:
            meta["UID"] = row.uid

        row_dict = {"json_metadata": meta, "assay_ids": [], "provenance": provenance_entry}
        rows_by_type.setdefault(row.sample_type, []).append(row_dict)
        if parked_values:
            dseq_parked.append((row_dict, parked_values, parked_attrs))

        provenance_by_type.setdefault(row.sample_type, []).extend(
            {"uid": row.uid or "", "attribute": a.attribute, "value": a.value,
             "origin": (mapper.ORIGIN_PARKED if n in parked_values else a.origin),
             "raw_key": a.raw_key, "source_file": a.source_file}
            for n, a in row.attributes.items())

    # Existing Notes for D.SEQ's update-mode rows, fetched AT MOST ONCE: the
    # `want_update` guard above means `rows_by_type` can only ever carry a
    # "D.SEQ" key when mode == MODE_UPDATE (every other sample type is
    # `continue`d past whenever mode == MODE_UPDATE), so the Notes-composition
    # step below (parked values) and qa_rows' own NOTES_WOULD_CLOBBER guard
    # both need Notes for exactly the same row set -- `rows_by_type["D.SEQ"]`.
    # Querying it twice was not just wasted work: a transient blip on the
    # SECOND fetch would drop a UID the first fetch had just proved readable,
    # turning it into a spurious NOTES_WOULD_CLOBBER hard reject for no
    # reason. One fetch, reused both places, closes that window.
    dseq_existing_notes: dict[str, str] | None = None
    if mode == MODE_UPDATE and rows_by_type.get("D.SEQ"):
        dseq_existing_notes = _existing_notes(rows_by_type["D.SEQ"])

    # Compose Notes for every D.SEQ row carrying a parked value, gated by the
    # same existing-Notes fetch the NOTES_WOULD_CLOBBER guard uses: a sample
    # whose Notes could not be fetched gets no note written at all, per the
    # design's clobbering guard (section 8) -- guessing here would risk
    # destroying curator text deep_merge_metadata would overwrite wholesale.
    # The needs_definition proposal is queued regardless: the schema gap is
    # real whether or not THIS run could safely write it into Notes, and a
    # superuser defining the attribute is what stops the parking, not a
    # successful Notes fetch.
    if dseq_parked:
        existing_notes = dseq_existing_notes or {}
        for row_dict, parked_values, parked_attrs in dseq_parked:
            uid = row_dict["json_metadata"].get("UID")
            if uid in existing_notes:
                row_dict["json_metadata"]["Notes"] = compose_notes(
                    existing_notes[uid], run_name, parked_values, today)
            for name, attr in parked_attrs.items():
                needs_definition.append({
                    "raw_key": attr.raw_key, "proposed_attribute": name,
                    # Parking only ever happens on the D.SEQ backfill branch
                    # (the `want_update` guard above), so the target sample
                    # type is always D.SEQ here -- never the stale loop `row`
                    # from the (already-exited) row-construction loop above.
                    "proposed_target": "D.SEQ", "datatype": "string",
                    "example_value": attr.value, "source_file": attr.source_file,
                    "rationale": "mapped by reingest but not defined on D.SEQ (parked in Notes)",
                    "status": "needs_definition",
                })

    # An entirely empty catalog is an outage signal (proposals.attribute_exists'
    # own contract, right above), never an answer -- so this must raise here
    # too, exactly like that function does, rather than quietly substituting a
    # set derived from the very rows it is supposed to validate (which would
    # make UNKNOWN_SAMPLETYPE unfireable for the whole duration of an outage).
    # See NessieAI/ns/granular.py's legacy `_build_upload_xlsx_from_rows` (the
    # `known = known_sample_types() or set(by_type)` line) for the one place
    # in this module that still falls back on purpose, and why.
    known = known_sample_types()
    if not known:
        raise RuntimeError(
            "sample type catalog came back empty; treating this as an "
            "outage rather than validating this reingest run's sample types "
            "against an empty schema"
        )
    # Every d_seq_uid the manifest resolved is, by construction, a legitimate
    # Parent target (mapper.py only ever sets Parent from one of these -- see
    # its _HAS_PARENT set) -- qa_rows' new-mode Parent-resolvability check
    # needs this cohort or every new-mode row's Parent would fail resolution
    # against an empty set and hard-reject the whole batch.
    existing_parent_uids = {s.d_seq_uid for s in run_manifest.samples if s.d_seq_uid}
    for sample_type, type_rows in rows_by_type.items():
        attrs = attributes_for(sample_type)
        required = [a["title"] for a in attrs if a.get("required")]
        # See the legacy-rows path above for why the fallback default is the
        # entry's own "required" rather than False.
        server_required = [a["title"] for a in attrs if a.get("server_required", a.get("required"))]
        built = qa_rows(type_rows, sample_type=sample_type, known_sampletypes=known,
                        required_fields=required, server_required_fields=server_required,
                        mode=mode,
                        existing_parent_uids=existing_parent_uids,
                        existing_notes=dseq_existing_notes if mode == MODE_UPDATE else None,
                        run_name=run_name)
        # Every attribute on this sample type's rows may have been parked into
        # Notes (attribute_exists said none of them are defined on the
        # schema) -- if the Notes fetch above also failed for every one of
        # these UIDs, no row even got a Notes column, and json_metadata is
        # left holding nothing but UID. render_upload_workbook refuses to
        # render that ("rows carry no json_metadata"): catch the same
        # condition here as a QA hard-reject instead of letting that
        # ValueError escape past this op's own VALIDATION/WRITE_BLOCKED/
        # AGENT_FAILED taxonomy as an opaque 502.
        if not any(set(row.get("json_metadata") or {}) - {"UID"} for row in type_rows):
            built.add(Finding(code=NO_ATTRIBUTES_TO_WRITE, severity=HARD,
                              sample_type=sample_type))
            built._finalize()
        reports_by_type[sample_type] = built
        qa[sample_type] = {"disposition": built.disposition, "hard": built.hard, "soft": built.soft}
        if built.disposition == HARD_REJECT:
            continue
        safe_name = sample_type.replace("/", "_").replace(" ", "_")
        suffix = "_update" if mode == MODE_UPDATE else ""
        safe_key = f"reingest_{safe_name}{suffix}".replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}{suffix}.xlsx")
        render_upload_workbook(sample_type, type_rows, path, mode=mode,
                               provenance=provenance_by_type.get(sample_type))
        saved_files[safe_key] = path

    # A HARD_REJECT sample type's own workbook was skipped above (the
    # `continue` two lines up), so asking the reader to confirm a
    # primary-file pick inside a workbook that was never produced would be
    # nonsensical on a blocked run. Every group's `sample_type` is known by
    # now -- `reports_by_type` was just populated for every key
    # `ambiguous_primary_groups` could possibly hold, since both are built
    # from the same `rows_by_type` sample-type set.
    ambiguous_primary = [
        group for group in ambiguous_primary_groups.values()
        if reports_by_type[group["sample_type"]].disposition != HARD_REJECT
    ]

    # Surface 1 of the superuser surfaces (design doc section 10): the
    # genuinely unmapped raw keys ride out in the same artifact bundle as the
    # workbooks, so whoever ran the reingest sees exactly what the mapper
    # could not place, immediately. This must join `saved_files` BEFORE
    # render_qa_for_user runs below, or the verbatim user-facing reply it
    # builds is composed from a saved_files snapshot that does not yet
    # mention this artifact.
    if result.unmapped:
        proposals_path = os.path.join(out_root, f"map_proposals_{_slug(pipeline)}.md")
        with open(proposals_path, "w", encoding="utf-8") as handle:
            handle.write(f"# Proposed sample attributes — {pipeline}\n\n")
            handle.write(f"Run: {run_manifest.run_dir}\n\n")
            handle.write("| Raw key | Example value | Example sample | Source file |\n")
            handle.write("|---|---|---|---|\n")
            for entry in result.unmapped:
                handle.write(f"| `{entry['raw_key']}` | {entry['example_value']} | "
                             f"{entry.get('example_sample', '')} | "
                             f"`{entry['source_file']}` |\n")
        saved_files[f"map_proposals_{_slug(pipeline)}"] = proposals_path

    reply = user_report.render_qa_for_user(reports_by_type, saved_files, run_name,
                                           ambiguous_primary=ambiguous_primary)

    pending = [
        {**entry, "proposed_attribute": "", "proposed_target": "",
         "pipeline": pipeline, "manifest_digest": manifest_id,
         "run_dir": run_manifest.run_dir}
        for entry in result.unmapped
    ] + [
        {**entry, "pipeline": pipeline, "manifest_digest": manifest_id,
         "run_dir": run_manifest.run_dir}
        for entry in needs_definition
    ]

    return {
        "saved_files": saved_files,
        "qa": qa,
        "reply": reply,
        "proposals": pending,
    }


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
    "run-checksum": _run_checksum,
}
