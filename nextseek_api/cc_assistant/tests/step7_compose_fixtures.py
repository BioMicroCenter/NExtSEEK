"""Shared Step 7 compose-deploy bundle builders (not pytest tests).

Extracted so collected tests do not import the ignored
``test_step7_compose_deploy.py`` module (which would drag fail-under).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from nextseek_api.cc_assistant.tests.step7_preflight_collector import LIVE_GATE_TRANSCRIPT_REL
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import OPUS as CC_OPUS_MODEL_ID
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import LIVE_EVIDENCE_PATH_LITERAL

PORT_SOURCE_COMMIT = "b" * 40
# Byte-identical allowlist markers (PLAN-3 Task 13 Step 8 / Task 2 brief): the
# migration marker (fresh-migrate stdout form), `cc_assistant.upload`
# (registered-task name), `cc_traces` (Step 6 GET …?include=turns JSON key).
TRANSCRIPT_CONTENT = (
    b"Applying nextseek_api.0007_ccsessiontranscript... OK\n"
    b"cc_assistant.upload\n"
    b'{"cc_traces": [{"turn": 1}]}\n'
)

# --------------------------------------------------------------------------
# Task 2: a comprehensive, all-artifacts-present bundle builder. Task 1's
# `_pass_bundle` only wrote preflight.json (+ a minimal meta.json via
# `_write_meta`) because Task 1 only implemented the preflight/step3_deploy_gate
# checks. Task 2 extends CHECKS with ~30 more checks spanning the rest of the
# SPEC-7 section-8 bundle, so a bundle that should now fully pass
# (`all_ok is True`) must carry every one of those artifacts too.
# --------------------------------------------------------------------------

# Hex-and-hyphen only (matches cc_engine._CONTAINER_NAME_SAFE_RE /
# ^dmac-cc-agent-[0-9a-f-]{1,64}$ -- Task 15's closed-set general-agent
# pattern): real run_ids are Celery task UUIDs.
RUN_ID = "a1b2c3d4-e5f6-0001"
OWN_MARKER = f"OWN_{RUN_ID}"
LIVE_SENTINEL = "LIVE_SENT-feed1234"
FOREIGN_TOKENS = ["SENTINEL_FOREIGN", "otherproj", "bob"]


GATE_PROJECT = "1-sandbox"
GATE_USER_ID = "ccgateuser"
CC_IMAGE_DEFAULT = "dmac-assistant:poc"


def _write_meta_full(bundle_dir: Path, *, run_id: str = RUN_ID, repo_commit: str,
                      host_label: str = "dev-vm", budget_cap_usd: float = 2.0,
                      own_marker: str = OWN_MARKER, live_sentinel: str = LIVE_SENTINEL,
                      foreign_tokens: list[str] | None = None,
                      migration_policy: str | None = None,
                      greenfield_exception: bool = False,
                      greenfield_exception_handoff_path: str | None = None,
                      zero_cost_exception: bool = False,
                      gate_project: str = GATE_PROJECT,
                      gate_user_id: str = GATE_USER_ID,
                      **extra) -> None:
    meta = {
        "run_id": run_id,
        "repo_commit": repo_commit,
        "repo_branch": "cc-step7-compose-native",
        "host_label": host_label,
        "timestamp": "2026-07-01T12:00:00Z",
        "verifier_version": "1",
        "budget_cap_usd": budget_cap_usd,
        "foreign_tokens": FOREIGN_TOKENS if foreign_tokens is None else foreign_tokens,
        "own_marker": own_marker,
        "live_sentinel": live_sentinel,
        "migration_policy": migration_policy,
        "greenfield_exception": greenfield_exception,
        "greenfield_exception_handoff_path": greenfield_exception_handoff_path,
        "zero_cost_exception": zero_cost_exception,
        "gate_project": gate_project,
        "gate_user_id": gate_user_id,
    }
    meta.update(extra)
    (bundle_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _write_cost_ledger(bundle_dir: Path, *, run_id: str = RUN_ID) -> None:
    from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import BIN_OPS

    entries = []
    for op in BIN_OPS:
        if op == "nextseek-api-write":
            entries.append({
                "op": op,
                "source_system": "none",
                "usd": 0.0,
                "call_id": f"{run_id}-{op}-blocked",
                "timestamp": "2026-07-01T12:00:00Z",
                "reconciliation_note": "WRITE_BLOCKED unconfirmed leg",
            })
            continue
        entries.append({
            "op": op,
            "source_system": "llm_client_ledger",
            "usd": 0.01,
            "call_id": f"{run_id}-{op}-1",
            "timestamp": "2026-07-01T12:00:00Z",
            "reconciliation_note": "synthetic Gate 3C.5 fixture",
        })
    (bundle_dir / "cost_ledger.json").write_text(
        json.dumps({"run_id": run_id, "entries": entries, "total_usd": round(0.01 * (len(BIN_OPS) - 1), 4)}),
        encoding="utf-8",
    )


def _write_cost_extraction_evidence(bundle_dir: Path, *, run_id: str = RUN_ID) -> None:
    from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import BIN_OPS

    rows = []
    for op in BIN_OPS:
        if op == "nextseek-api-write":
            continue
        rows.append({
            "op": op,
            "call_id": f"{run_id}-{op}-1",
            "source_system": "llm_client_ledger",
            "ledger_path": "outputs/_ledger.jsonl",
            "ledger_line_start": 0,
            "ledger_line_end": 1,
            "usd": 0.01,
            "price_table_version": "2026-06-granular-realstack",
        })
    (bundle_dir / "cost_extraction_evidence.json").write_text(
        json.dumps({"run_id": run_id, "entries": rows}), encoding="utf-8"
    )


def _network_inspect_json(names: list[str], *, id_prefix: str = "cid") -> dict:
    """Build the FULL `docker network inspect dmac-cc-net` shape (a JSON array
    whose sole element carries `Containers`, keyed by container ID, each with
    a `Name` field) from a plain list of container names -- the format Task
    15 pins for network_inspect.json / network_inspect_matrix.json."""
    return [{
        "Name": "dmac-cc-net",
        "Id": "netid0000000000000000000000000000000000000000000000000000000000",
        "Containers": {
            f"{id_prefix}{i:04d}{'0' * 56}"[:64]: {"Name": nm}
            for i, nm in enumerate(names)
        },
    }]


def _matrix_row(op: str, *, run_id: str, cc_image: str, container_id: str,
                 exit_code: int = 0, excerpt: str | None = None,
                 transport: str | None = None, wall_secs: float = 4.2,
                 published_path: str | None = None) -> dict:
    from nextseek_api.cc_assistant.bin_inventory import is_viewset_op, op_suffix
    from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
        OP_EXCERPT_ALLOWED_FIELDS,
    )
    if transport is None:
        transport = "viewset" if is_viewset_op(op) else "sidecar"
    if excerpt is None:
        suf = op_suffix(op)
        if suf in ("query", "plan"):
            excerpt = json.dumps({"reply": f"[{op} ok]", "debug": {}, "bundle_id": None})
        elif suf == "recall":
            excerpt = json.dumps({
                "turn_id": 1,
                "bundle_id": 42,
                "total": 1,
                "row_count": 1,
                "columns": ["uid"],
                "path": "/data/scratch/recall/turn-1.json",
            })
        else:
            wire_op = suf if suf != "entity-extract" else "entity"
            allowed = OP_EXCERPT_ALLOWED_FIELDS.get(op, frozenset({"op", "result"}))
            body: dict = {"op": wire_op, "result": {}}
            if "download" in allowed:
                body["download"] = None
            excerpt = json.dumps(body)
    row = {
        "op": op,
        "transport": transport,
        "exit_code": exit_code,
        "excerpt": excerpt,
        "container_id": container_id,
        "container_name": f"dmac-cc-matrix-{run_id}",
        "image": cc_image,
        "wall_secs": wall_secs,
        "exercise_id": f"T18-{op.removeprefix('nextseek-')}-1",
        "upstream_ref": f"dmac-assistant@a429f13:fixture:{op}",
        "cost_usd": 0.0 if op_suffix(op) == "api-write" else 0.01,
        "call_id": f"{run_id}-{op}-1",
        "cost_source": "none" if op_suffix(op) == "api-write" else "llm_client_ledger",
    }
    if published_path is not None:
        row["published_path"] = published_path
    return row


def _write_matrix_artifacts(bundle_dir: Path, *, run_id: str = RUN_ID,
                             cc_image: str = CC_IMAGE_DEFAULT,
                             gate_project: str = GATE_PROJECT,
                             gate_user_id: str = GATE_USER_ID,
                             matrix_overrides: dict | None = None,
                             extra_matrix_ops: list[str] | None = None,
                             omit_matrix_ops: list[str] | None = None,
                             network_inspect_matrix_containers: list[str] | None = None,
                             skip: set[str] | None = None) -> None:
    """Write plugin_ops_matrix.json + every Task 15 companion artifact with
    values that make a clean, fully-passing bundle. ``skip`` names artifacts
    to omit entirely (for negative "bundle missing X" tests)."""
    from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import BIN_OPS

    from nextseek_api.cc_assistant.bin_inventory import op_suffix

    skip = skip or set()
    matrix_executor_id = "matrixcid" + "0" * 55
    matrix_executor_id = matrix_executor_id[:64]
    matrix_executor_name = f"dmac-cc-matrix-{run_id}"

    report_path = f"/dmac/users/{gate_project}/{gate_user_id}/scratch/nextseek-artifacts/report.xlsx"
    submission_path = f"/dmac/users/{gate_project}/{gate_user_id}/scratch/nextseek-artifacts/submission.zip"

    matrix: dict[str, dict] = {}
    ops = list(BIN_OPS) + list(extra_matrix_ops or [])
    for op in ops:
        if omit_matrix_ops and op in omit_matrix_ops:
            continue
        kwargs = {"run_id": run_id, "cc_image": cc_image, "container_id": matrix_executor_id}
        if op_suffix(op) == "api-write":
            kwargs.update(exit_code=5, excerpt=json.dumps(
                {"error": {"code": "WRITE_BLOCKED", "message": "nextseek-api-write requires --confirmed-write"}}
            ))
        elif op_suffix(op) == "report":
            kwargs["published_path"] = report_path
        elif op_suffix(op) == "generate-submission":
            kwargs["published_path"] = submission_path
        row = _matrix_row(op, **kwargs)
        if matrix_overrides and op in matrix_overrides:
            row.update(matrix_overrides[op])
        matrix[op] = row

    if "plugin_ops_matrix" not in skip:
        (bundle_dir / "plugin_ops_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")

    if "instance_binding" not in skip and "seeded_fixture" not in skip:
        (bundle_dir / "instance_binding.json").write_text(json.dumps({
            "binding_id": "hermetic-fixture",
            "project_id": 1,
            "project_title": "Published Data",
            "project": gate_project,
            "reference_uids": ["A.ADCD-250312ALT-1-PUB"],
            "uids": ["A.ADCD-250312ALT-1-PUB"],
            "forbidden_actions": ["create_seeded_fixture"],
            "source": "instance_binding.json",
        }), encoding="utf-8")

    if "cost_ledger" not in skip:
        _write_cost_ledger(bundle_dir, run_id=run_id)
        _write_cost_extraction_evidence(bundle_dir, run_id=run_id)

    if "r26_live_probes" not in skip:
        (bundle_dir / "R26-live-probes.json").write_text(json.dumps({
            "run_id": run_id,
            "probes": [
                {"name": "project_binding", "pass": True, "exit_code": 0, "stdout": "1\tPublished Data"},
                {"name": "sample_count", "pass": True, "exit_code": 0, "stdout": "50886"},
                {"name": "sample_type_count", "pass": True, "exit_code": 0, "stdout": "104"},
                {"name": "reference_uid", "pass": True, "exit_code": 0, "stdout": "319625\tA.ADCD-250312ALT-1-PUB"},
                {"name": "mus_count", "pass": True, "exit_code": 0, "stdout": "1179"},
            ],
        }), encoding="utf-8")

    if "gate_access_log_window" not in skip:
        from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
            OP_ASSISTANT_ENDPOINT,
        )
        endpoints = sorted({OP_ASSISTANT_ENDPOINT[op] for op in BIN_OPS})
        lines = [
            f'172.20.0.5 - - [01/Jul/2026:12:05:0{i} +0000] "POST {ep} HTTP/1.1" 200 42 "-" "httpx"'
            for i, ep in enumerate(endpoints)
        ]
        (bundle_dir / "gate_access_log_window.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if "post_sweep_user_tree_scan" not in skip:
        (bundle_dir / "post_sweep_user_tree_scan.txt").write_text(
            f"{report_path}\n{submission_path}\n", encoding="utf-8",
        )

    if "matrix_env_scan" not in skip:
        (bundle_dir / "matrix_env_scan.txt").write_text(
            "NEXTSEEK_SIDECAR_HOST=nextseek-sidecar\nNEXTSEEK_SIDECAR_PORT=8765\nHOME=/home/user\n",
            encoding="utf-8",
        )

    if "sweep_invocation" not in skip:
        (bundle_dir / "sweep_invocation.json").write_text(json.dumps({
            "command": (
                f"docker exec nextseek /app/.venv/bin/python manage.py cc_sweep_staging "
                f"--user-id {gate_user_id} --api-user {gate_user_id} --project {gate_project}"
            ),
            "exit_code": 0,
            "output_excerpt": json.dumps({"user_id": gate_user_id, "delivered_count": 2}),
            "timestamp": "2026-07-01T12:10:00Z",
        }), encoding="utf-8")

    if "network_inspect_matrix" not in skip:
        names = (
            [matrix_executor_name, "dmac-bedrock-proxy"]
            if network_inspect_matrix_containers is None else network_inspect_matrix_containers
        )
        # The matrix executor's peer record MUST key on exactly
        # `matrix_executor_id` (the same id the matrix rows' `container_id`
        # carries) so the validator's row->inspect join actually resolves --
        # never derive it from the generic per-name id formula, which has no
        # reason to coincide.
        containers_map = {}
        for i, nm in enumerate(names):
            cid = matrix_executor_id if nm == matrix_executor_name else f"peercid{i:04d}{'0' * 53}"[:64]
            containers_map[cid] = {"Name": nm}
        (bundle_dir / "network_inspect_matrix.json").write_text(json.dumps([{
            "Name": "dmac-cc-net",
            "Id": "netid0000000000000000000000000000000000000000000000000000000000",
            "Containers": containers_map,
        }]), encoding="utf-8")


def _write_auxiliary_artifacts(bundle_dir: Path, *, run_id: str = RUN_ID,
                                own_marker: str = OWN_MARKER, live_sentinel: str = LIVE_SENTINEL,
                                foreign_tokens: list[str] | None = None,
                                cost: float = 0.05, is_error: bool = False,
                                sentinel: str = LIVE_SENTINEL,
                                pre_bootstrap: bool = False,
                                pre_existing_volume_names: list[str] | None = None,
                                pre_existing_network_names: list[str] | None = None,
                                network_inspect_containers: list[str] | None = None,
                                cc_image: str = CC_IMAGE_DEFAULT,
                                gate_project: str = GATE_PROJECT,
                                gate_user_id: str = GATE_USER_ID,
                                matrix_overrides: dict | None = None,
                                extra_matrix_ops: list[str] | None = None,
                                omit_matrix_ops: list[str] | None = None,
                                network_inspect_matrix_containers: list[str] | None = None,
                                skip_matrix_artifacts: set[str] | None = None,
                                write_matrix_artifacts: bool = True) -> None:
    """Write every SPEC-7 section 8 artifact Task 2/15 checks beyond
    preflight.json + meta.json, with values that make a clean, fully-passing
    bundle."""
    foreign_tokens = FOREIGN_TOKENS if foreign_tokens is None else foreign_tokens
    bundle_dir.mkdir(parents=True, exist_ok=True)

    compose_config = {
        "services": {
            "nextseek": {"image": "nextseek:dev"},
            "nextseek_nginx": {"image": "nginx:1"},
            "bedrock-proxy": {"image": "bedrock-proxy:dev"},
        },
        "networks": {"dmac-cc-net": {"driver": "bridge"}},
        "volumes": {"dmac-cc-users": {"external": True}, "seek-filestore": {"external": True}},
    }
    (bundle_dir / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")
    (bundle_dir / "compose_services.txt").write_text(
        "nextseek       Up 2 minutes\nbedrock-proxy  Up 2 minutes\n", encoding="utf-8"
    )
    (bundle_dir / "docker_ps.txt").write_text(
        "CONTAINER ID   IMAGE           STATUS\nabc123         nextseek:dev    Up 2 minutes\n", encoding="utf-8"
    )
    (bundle_dir / "images.json").write_text(
        json.dumps({
            "nextseek": "nextseek:dev", "bedrock-proxy": "bedrock-proxy:dev", "cc-agent": cc_image,
        }), encoding="utf-8"
    )
    containers = (
        [f"dmac-cc-agent-{run_id}", "dmac-bedrock-proxy"]
        if network_inspect_containers is None else network_inspect_containers
    )
    (bundle_dir / "network_inspect.json").write_text(
        json.dumps(_network_inspect_json(containers)), encoding="utf-8"
    )
    (bundle_dir / "cc_runner_available.json").write_text(json.dumps([True, "ok"]), encoding="utf-8")
    (bundle_dir / "forced_cc_result.json").write_text(json.dumps({
        "run_id": run_id, "is_error": is_error, "sentinel": sentinel, "cost": cost,
    }), encoding="utf-8")
    (bundle_dir / "proxy_log_window.txt").write_text(
        f"[2026-07-01T12:00:00Z] run_id={run_id} "
        # Task 16: proxy_invoke_recorded is pinned to the allowed Opus model
        # id (CC_OPUS_MODEL_ID) -- a clean bundle's proxy log must carry an
        # invoke->200 line for THAT exact model, not an arbitrary one.
        f"POST /model/{CC_OPUS_MODEL_ID}/invoke -> 200\n",
        encoding="utf-8",
    )
    (bundle_dir / "agent_env_scan.txt").write_text(
        "NEXTSEEK_CC_IMAGE=nextseek-cc:dev\nHOME=/home/agent\nPATH=/usr/bin\n", encoding="utf-8"
    )
    (bundle_dir / "pre_turn_seed_scan.txt").write_text(
        "/v/otherproj/bob/input/SENTINEL_FOREIGN\n", encoding="utf-8"
    )
    (bundle_dir / "subpath_isolation_scan.txt").write_text(
        f"/data/input/{own_marker}/foo.txt\n/data/scratch/{live_sentinel}\n", encoding="utf-8"
    )
    (bundle_dir / "secret_scan_report.json").write_text(
        json.dumps({"clean": True, "screenshots": {}}), encoding="utf-8"
    )
    if pre_bootstrap:
        vol_lines = ["DRIVER    VOLUME NAME"] + [f"local     {n}" for n in (pre_existing_volume_names or [])]
        (bundle_dir / "pre_bootstrap_docker_volume_ls.txt").write_text(
            "\n".join(vol_lines) + "\n", encoding="utf-8"
        )
        net_lines = ["NETWORK ID     NAME      DRIVER"] + [
            f"abc123         {n}       bridge" for n in (pre_existing_network_names or [])
        ]
        (bundle_dir / "pre_bootstrap_docker_network_ls.txt").write_text(
            "\n".join(net_lines) + "\n", encoding="utf-8"
        )
    if write_matrix_artifacts:
        _write_matrix_artifacts(
            bundle_dir, run_id=run_id, cc_image=cc_image, gate_project=gate_project,
            gate_user_id=gate_user_id, matrix_overrides=matrix_overrides,
            extra_matrix_ops=extra_matrix_ops, omit_matrix_ops=omit_matrix_ops,
            network_inspect_matrix_containers=network_inspect_matrix_containers,
            skip=skip_matrix_artifacts,
        )


def _full_bundle(bundle_dir: Path, tracker_path: Path, deploy_commit: str,
                  *, host_label: str = "dev-vm", run_id: str = RUN_ID,
                  had_host_bind_data: bool = False, migration_policy: str | None = None,
                  gate_overrides: dict | None = None, meta_overrides: dict | None = None,
                  aux_overrides: dict | None = None) -> dict:
    """The one-stop "clean bundle" builder for Task 2: preflight.json (via
    `_pass_bundle`, with a realistic multi-line docker_version_summary so the
    independent Engine/API floor parse passes) + meta.json + every auxiliary
    section-8 artifact. Returns the written preflight dict."""
    gate_overrides = dict(gate_overrides or {})
    gate_overrides.setdefault("had_host_bind_data", had_host_bind_data)
    # `deploy_commit` is also a positional param of this function (used below
    # for meta.json.repo_commit and the bundle's real git commit); allow
    # gate_overrides to independently override just the *recorded* gate value
    # (e.g. to test a malformed deploy_commit) without a duplicate-keyword
    # crash against the positional arg.
    pass_bundle_deploy_commit = gate_overrides.pop("deploy_commit", deploy_commit)
    preflight = _pass_bundle(bundle_dir, tracker_path, pass_bundle_deploy_commit, **gate_overrides)
    preflight["docker_version_summary"] = (
        "Docker version 27.0.0, build abc123\n"
        "Engine:\n Version: 27.0.0\n API version: 1.47 (minimum version 1.24)\n"
    )
    (bundle_dir / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    meta_kwargs = {"run_id": run_id, "repo_commit": deploy_commit, "host_label": host_label,
                   "migration_policy": migration_policy}
    meta_kwargs.update(meta_overrides or {})
    _write_meta_full(bundle_dir, **meta_kwargs)
    _write_auxiliary_artifacts(bundle_dir, run_id=run_id, **(aux_overrides or {}))
    return preflight


# --------------------------------------------------------------------------
# Real throwaway git repos (tmp_path; no network, no global config needed)
# --------------------------------------------------------------------------

def _git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir),
         "-c", "user.email=test@test", "-c", "user.name=test",
         *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _make_git_repo(root: Path, files: dict[str, bytes]) -> tuple[Path, str]:
    """git init a repo at root/gitrepo with `files` committed; return (repo, HEAD sha)."""
    repo = root / "gitrepo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    return repo, _git(repo, "rev-parse", "HEAD")


def _repo_with_transcript(tmp_path: Path, content: bytes = TRANSCRIPT_CONTENT) -> tuple[Path, str]:
    return _make_git_repo(tmp_path, {LIVE_GATE_TRANSCRIPT_REL: content})


# --------------------------------------------------------------------------
# Synthetic bundle builders
# --------------------------------------------------------------------------

def _mk_tracker(status: str = "done") -> dict:
    return {"steps": [{"id": "3", "name": "UI-based I/O", "status": status, "substeps": []}]}


def _write_tracker(path: Path, status: str = "done") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_mk_tracker(status)), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pass_bundle(bundle_dir: Path, tracker_path: Path, deploy_commit: str,
                 **gate_overrides) -> dict:
    """Write a synthetic clean preflight.json bundle. Returns the dict written."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    tracker_sha = _write_tracker(tracker_path, "done")

    gate = {
        "integration_plan_path": str(tracker_path),
        "tracker_step3_status": "done",
        "integration_plan_sha256": tracker_sha,
        "canonical_integration_plan_sha256": tracker_sha,
        "live_gate_transcript_committed": True,
        "deploy_commit": deploy_commit,
        # None by default: check_supplementary_handoff_valid treats an absent
        # handoff as trivially fine (transcript commit is the hard gate; the
        # handoff is supplementary). Tests exercising a *recorded* handoff
        # path set this explicitly.
        "user_signoff_handoff_path": None,
        "live_evidence_path": LIVE_EVIDENCE_PATH_LITERAL,
        "pre_step3_snapshot_tag": "nextseek-nextseek:pre-step3",
        "docker_engine_meets_subpath_floor": True,
        "docker_compose_meets_subpath_floor": True,
        "port_source_path": "/home/x/work/dmac-assistant",
        "port_source_commit": PORT_SOURCE_COMMIT,
        "had_host_bind_data": False,
    }
    gate.update(gate_overrides)

    preflight = {
        "branch": "cc-step7-compose-native",
        "commit": deploy_commit,
        "dirty": False,
        "file_hashes": {
            "docker-compose.yml": {"path": "docker-compose.yml", "exists": True, "sha256": "1" * 64},
            "docker/nextseek.env.example": {
                "path": "docker/nextseek.env.example", "exists": True, "sha256": "2" * 64,
            },
            "DEPLOY.md": {"path": "nextseek_api/cc_assistant/DEPLOY.md", "exists": True, "sha256": "3" * 64},
        },
        "compose_services": ["nextseek", "nextseek_nginx", "db", "neo4j"],
        "compose_networks": [],
        "cc_env_keys": [],
        "deploy_md_has_old_bootstrap": True,
        "docker_version_summary": "Docker version 27.0.0, build abc123",
        "docker_info_summary": "Server Version: 27.0.0",
        "docker_compose_version": "Docker Compose version v2.29.1",
        "step3_deploy_gate": gate,
    }
    (bundle_dir / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    return preflight


def _write_meta(bundle_dir: Path, host_label: str = "linux-dev-vm") -> None:
    (bundle_dir / "meta.json").write_text(json.dumps({"host_label": host_label}), encoding="utf-8")


def _names(checks):
    return {name: ok for name, ok, _ in checks}


