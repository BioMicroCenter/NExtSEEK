#!/usr/bin/env python3
"""Gate 3D live bundle assembler — container-side live work only (option C).

Runs inside the live ``nextseek`` container after sidecar deploy and
``STEP7_LLM_LEDGER=1``.  Preflight git probes and ``validate_run`` are
**host-side** — see ``step7_gate3d_host_finalize.py`` after ``docker cp``.

  docker cp /home/taishajo/work/state/integration-plan.json nextseek:/app/integration-plan.json

  docker exec -e RUN_REALSTACK=1 \\
    -e SEEK_TEST_USER=demo -e SEEK_TEST_PASS=demopassword \\
    -e NEXTSEEK_CC_GATE_USER_ID=demo \\
    -e NEXTSEEK_CC_MAX_BUDGET_USD=10 \\
    -e STEP7_GATE3D_RUN_ID=<run_id> \\
    -e STEP7_REPO_COMMIT=<40-char-sha> \\
    -e STEP7_REPO_BRANCH=cc-step7-compose-native \\
    -e INTEGRATION_PLAN_PATH=/app/integration-plan.json \\
    nextseek sh -lc 'cd /app && uv run python nextseek_api/cc_assistant/scripts/step7_gate3d_live.py'
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django  # noqa: E402

django.setup()

from nextseek_api.cc_assistant import cc_config, cc_engine  # noqa: E402
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (  # noqa: E402
    CANONICAL_FOREIGN_TOKENS,
)
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import OPUS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
EVID_ROOT = REPO_ROOT / "nextseek_api" / "cc_assistant" / "tests" / "acceptance_evidence" / "step7"
PROXY_CONTAINER = os.environ.get("DMAC_PROXY_CONTAINER", "dmac-bedrock-proxy")
NET = cc_engine.DEFAULT_NETWORK
BUDGET_CAP = float(os.environ.get("NEXTSEEK_CC_MAX_BUDGET_USD", "10"))
USERS_VOLUME = cc_config.CCPaths.from_env().users_volume
_CC_RUN_ID_RE = re.compile(r"^[0-9a-f-]{1,64}$")


def _cc_run_id(run_id: str) -> str:
    """Docker container names allow only ``[0-9a-f-]``; derive from the gate run_id."""
    if _CC_RUN_ID_RE.fullmatch(run_id or ""):
        return run_id
    derived = re.sub(r"[^0-9a-f]", "", (run_id or "").lower())[:64]
    if derived:
        return derived
    return uuid.uuid4().hex


def _run(cmd: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _capture_compose_artifacts(bundle: Path, *, repo_root: Path) -> None:
    cfg = _run(["docker", "compose", "-f", str(repo_root / "docker-compose.yml"), "config", "--format", "json"])
    if cfg.returncode == 0 and cfg.stdout.strip():
        _write_json(bundle / "compose_config.json", json.loads(cfg.stdout))
    ps = _run(["docker", "compose", "ps"])
    (bundle / "compose_services.txt").write_text(ps.stdout or ps.stderr, encoding="utf-8")
    dps = _run(["docker", "ps", "--format", "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"])
    (bundle / "docker_ps.txt").write_text(dps.stdout or "", encoding="utf-8")
    images: dict[str, str] = {}
    for svc, key in (("nextseek", "nextseek"), ("bedrock-proxy", "bedrock-proxy")):
        ins = _run(["docker", "inspect", "-f", "{{.Config.Image}}", svc if svc != "bedrock-proxy" else PROXY_CONTAINER])
        if ins.returncode == 0:
            images[key] = ins.stdout.strip()
    ins = _run(["docker", "inspect", "-f", "{{.Config.Image}}", "nextseek-sidecar"])
    if ins.returncode == 0:
        images["nextseek-sidecar"] = ins.stdout.strip()
    images["cc-agent"] = cc_engine.DEFAULT_IMAGE
    _write_json(bundle / "images.json", images)
    net = _run(["docker", "network", "inspect", NET])
    if net.returncode == 0:
        (bundle / "network_inspect.json").write_text(net.stdout, encoding="utf-8")
    ok, detail = cc_engine.cc_runner_available()
    _write_json(bundle / "cc_runner_available.json", [ok, detail])


def _seed_foreign_and_pre_scan(bundle: Path) -> str:
    seed_cmd = [
        "docker", "run", "--rm", "-v", f"{USERS_VOLUME}:/v", "alpine",
        "sh", "-c",
        "mkdir -p /v/otherproj/bob/input/SENTINEL_FOREIGN && find /v -maxdepth 4",
    ]
    proc = _run(seed_cmd, timeout=60)
    text = proc.stdout or proc.stderr or ""
    (bundle / "pre_turn_seed_scan.txt").write_text(text, encoding="utf-8")
    return text


def _forced_cc(bundle: Path, *, run_id: str, sentinel: str, own_marker: str, live_sentinel: str,
               api_user: str, api_pass: str, gate_user_id: str, gate_project: str) -> float:
    paths = cc_config.CCPaths.from_env()
    events: list = []
    errbox: dict = {}
    before = (_run(["docker", "logs", PROXY_CONTAINER]).stdout or "")

    query = (
        f"Create a file at /data/scratch/{live_sentinel} whose entire contents are exactly: {sentinel}\n"
        f"Also create /data/input/{own_marker}/gate.txt containing exactly: {own_marker}\n"
        f"In your final reply include the token {sentinel}."
    )

    def _target():
        try:
            cc_engine.run_cc_turn(
                query=query, model_id=OPUS, send_event=lambda e, d: events.append((e, dict(d))),
                user_id=gate_user_id, project_dirname=gate_project, run_id=run_id,
                paths=paths, api_user=api_user, api_pass=api_pass,
            )
        except Exception as exc:  # noqa: BLE001
            errbox["err"] = repr(exc)
            events.append(("query_error", {"error": repr(exc)}))

    isolation_lines: list[str] = []
    network_capture: dict | None = None

    def _poll_isolation():
        nonlocal network_capture
        end = time.time() + 150
        while time.time() < end:
            ps = _run(["docker", "ps", "-q", "--filter", f"label=nextseek.cc.run={run_id}"])
            cid = (ps.stdout or "").strip().split("\n")[0]
            if cid:
                ins = _run(["docker", "exec", cid, "find", "/data/input", "/data/scratch", "-maxdepth", "4"])
                if ins.stdout:
                    isolation_lines.append(ins.stdout)
                if network_capture is None:
                    net = _run(["docker", "network", "inspect", NET])
                    if net.returncode == 0:
                        network_capture = net.stdout
            time.sleep(2)

    t = threading.Thread(target=_target, daemon=True)
    scan_t = threading.Thread(target=_poll_isolation, daemon=True)
    t.start()
    scan_t.start()
    t.join(timeout=210)
    scan_t.join(timeout=5)

    after = (_run(["docker", "logs", PROXY_CONTAINER]).stdout or "")
    proxy_window = after[len(before):] if after.startswith(before[:200]) else after
    (bundle / "proxy_log_window.txt").write_text(
        f"# run_id={run_id}\n{proxy_window}", encoding="utf-8",
    )

    terminal = next(((e, d) for e, d in reversed(events) if e in ("query_complete", "query_error")), None)
    if terminal is None:
        raise RuntimeError(f"forced CC produced no terminal event: err={errbox}")
    ev, data = terminal
    cost = float(data.get("total_cost_usd") or 0.0)
    result: dict = {
        "run_id": run_id, "is_error": ev == "query_error", "sentinel": sentinel, "cost": cost,
    }
    if ev == "query_error":
        # Persist the failure detail — a bare is_error/$0 result is undiagnosable
        # after the in-memory events list is gone (2026-07-04 proxy-alias outage).
        result["error"] = data.get("error")
        result["error_reason"] = data.get("reason")
        if errbox:
            result["raised"] = errbox.get("err")
    _write_json(bundle / "forced_cc_result.json", result)

    ps = _run(["docker", "ps", "-q", "--filter", f"label=nextseek.cc.run={run_id}"])
    cid = (ps.stdout or "").strip().split("\n")[0]
    agent_env = ""
    if cid:
        ins = _run(["docker", "inspect", "-f", "{{json .Config.Env}}", cid])
        if ins.returncode == 0:
            agent_env = ins.stdout
    (bundle / "agent_env_scan.txt").write_text(agent_env, encoding="utf-8")
    (bundle / "subpath_isolation_scan.txt").write_text("".join(isolation_lines), encoding="utf-8")
    if network_capture:
        (bundle / "network_inspect.json").write_text(network_capture, encoding="utf-8")
    return cost


def _r26_probes(bundle: Path, *, run_id: str) -> None:
    pw_proc = _run(["docker", "exec", "seek-mysql", "printenv", "MYSQL_ROOT_PASSWORD"], timeout=15)
    mysql_pw = (pw_proc.stdout or "").strip() or "demopassword"
    probes = []
    specs = [
        ("project_binding", [
            "docker", "exec", "seek-mysql", "mysql", f"-uroot", f"-p{mysql_pw}", "-N", "-e",
            "SELECT id, title FROM seek_production.projects WHERE id=1;",
        ]),
        ("sample_count", [
            "docker", "exec", "seek-mysql", "mysql", f"-uroot", f"-p{mysql_pw}", "-N", "-e",
            "SELECT COUNT(*) FROM seek_production.samples;",
        ]),
        ("sample_type_count", [
            "docker", "exec", "seek-mysql", "mysql", f"-uroot", f"-p{mysql_pw}", "-N", "-e",
            "SELECT COUNT(*) FROM seek_production.sample_types;",
        ]),
        ("reference_uid", [
            "docker", "exec", "seek-mysql", "mysql", f"-uroot", f"-p{mysql_pw}", "-N", "-e",
            "SELECT id, uuid FROM seek_production.samples WHERE uuid='A.ADCD-250312ALT-1-PUB' LIMIT 1;",
        ]),
        ("mus_count", [
            "docker", "exec", "seek-mysql", "mysql", f"-uroot", f"-p{mysql_pw}", "-N", "-e",
            "SELECT COUNT(*) FROM seek_production.samples s "
            "JOIN seek_production.sample_types st ON st.id=s.sample_type_id WHERE st.title='MUS';",
        ]),
    ]
    for name, cmd in specs:
        proc = _run(cmd, timeout=30)
        stdout = (proc.stdout or proc.stderr or "").strip()
        passed = proc.returncode == 0 and bool(stdout)
        if name == "reference_uid":
            passed = proc.returncode == 0 and "A.ADCD-250312ALT-1-PUB" in stdout
        if name == "mus_count" and stdout.isdigit():
            passed = int(stdout) >= 1
        probes.append({
            "name": name, "command": " ".join(cmd), "exit_code": proc.returncode,
            "stdout": stdout, "pass": passed, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    _write_json(bundle / "R26-live-probes.json", {"run_id": run_id, "probes": probes})


def _r1_sidecar_proof(bundle: Path, *, run_id: str) -> None:
    ins = _run(["docker", "inspect", "nextseek-sidecar", "--format", "{{json .}}"])
    listen = _run(["docker", "exec", "nextseek-sidecar", "sh", "-lc",
                   "ss -ltn 2>/dev/null | grep 8765 || netstat -ltn 2>/dev/null | grep 8765"])
    reach = _run(["docker", "run", "--rm", "--network", NET, "alpine", "sh", "-lc",
                  "wget -q -O- http://nextseek-sidecar:8765/ 2>&1 | head -1 || nc -zv nextseek-sidecar 8765 2>&1"])
    sidecar_obj = json.loads(ins.stdout) if ins.returncode == 0 else {}
    _write_json(bundle / "R1-sidecar-live-proof.json", {
        "run_id": run_id,
        "container_id": sidecar_obj.get("Id"),
        "image": (sidecar_obj.get("Config") or {}).get("Image"),
        "internal_port_8765_listen": listen.stdout.strip(),
        "dmac_cc_net_reachability": reach.stdout.strip() or reach.stderr.strip(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def _secret_scan(bundle: Path) -> None:
    hits: list[dict] = []
    patterns = [
        re.compile(r"AWS_BEARER_TOKEN_BEDROCK\s*="),
        re.compile(r"GCP_API_KEY\s*="),
        re.compile(r"MYSQL_PASSWORD\s*="),
        re.compile(r"NEO4J_PASSWORD\s*="),
        re.compile(r"Authorization:\s*Bearer"),
        re.compile(r"ABSK"),
        re.compile(r"demopassword"),
    ]
    for path in bundle.rglob("*"):
        if path.is_dir() or path.suffix == ".png":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat in patterns:
            if pat.search(text):
                hits.append({"file": str(path.relative_to(bundle)), "pattern": pat.pattern})
    _write_json(bundle / "secret_scan_report.json", {
        "clean": not hits,
        "hits": hits,
        "scanner": "step7_gate3d_live.secret_scan",
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "screenshots": {},
    })


def _run_matrix(bundle: Path, *, run_id: str) -> None:
    env = os.environ.copy()
    env["RUN_REALSTACK"] = "1"
    env["STEP7_GATE3D_RUN_ID"] = run_id
    env["STEP7_GATE3D_BUNDLE_DIR"] = str(bundle)
    env["NEXTSEEK_STEP7_INSTANCE_BINDING"] = "1"
    proc = subprocess.run(
        [
            "uv", "run", "python", "manage.py", "test",
            "nextseek_api.cc_assistant.tests.test_cc_realstack.CCCapabilityGateMatrix"
            ".test_01_instance_binding_matrix_sweep_and_companions",
            "--settings=dmac.test_settings_realstack",
            "--noinput",
        ],
        cwd=str(REPO_ROOT), env=env, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"matrix pytest failed exit={proc.returncode}\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    run_id = os.environ.get("STEP7_GATE3D_RUN_ID") or str(uuid.uuid4())
    cc_run_id = _cc_run_id(run_id)
    sentinel = "G3D-" + uuid.uuid4().hex[:10].upper()
    own_marker = f"OWN_{run_id}"
    live_sentinel = f"LIVE_{sentinel}"
    gate_user_id = os.environ.get("NEXTSEEK_CC_GATE_USER_ID", "demo")
    gate_project = os.environ.get("NEXTSEEK_CC_GATE_PROJECT", "1-published-data")
    api_user = os.environ.get("SEEK_TEST_USER", "demo")
    api_pass = os.environ.get("SEEK_TEST_PASS", "demopassword")

    repo_commit = os.environ.get("STEP7_REPO_COMMIT", "").strip()
    repo_branch = os.environ.get("STEP7_REPO_BRANCH", "cc-step7-compose-native").strip()
    if not repo_commit or len(repo_commit) != 40:
        raise RuntimeError(
            "STEP7_REPO_COMMIT must be set to the 40-char host worktree HEAD before live run "
            "(preflight/validate are host-side per option C)"
        )

    bundle = Path(os.environ.get("STEP7_GATE3D_BUNDLE_DIR") or (EVID_ROOT / run_id))
    bundle.mkdir(parents=True, exist_ok=True)

    _write_json(bundle / "meta.json", {
        "run_id": run_id,
        "cc_run_id": cc_run_id,
        "repo_commit": repo_commit,
        "repo_branch": repo_branch,
        "host_label": "dev-vm",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verifier_version": "1",
        "budget_cap_usd": BUDGET_CAP,
        "foreign_tokens": sorted(CANONICAL_FOREIGN_TOKENS),
        "own_marker": own_marker,
        "live_sentinel": live_sentinel,
        "gate_project": gate_project,
        "gate_user_id": gate_user_id,
        "live_only": True,
        "host_finalize": "step7_gate3d_host_finalize.py",
    })

    _capture_compose_artifacts(bundle, repo_root=REPO_ROOT)
    _seed_foreign_and_pre_scan(bundle)
    forced_cost = _forced_cc(
        bundle, run_id=cc_run_id, sentinel=sentinel, own_marker=own_marker,
        live_sentinel=live_sentinel, api_user=api_user, api_pass=api_pass,
        gate_user_id=gate_user_id, gate_project=gate_project,
    )
    if forced_cost > BUDGET_CAP:
        raise RuntimeError(f"forced CC cost ${forced_cost:.4f} exceeds cap ${BUDGET_CAP}")

    _r26_probes(bundle, run_id=run_id)
    _r1_sidecar_proof(bundle, run_id=run_id)
    _run_matrix(bundle, run_id=cc_run_id)
    _secret_scan(bundle)

    manifest_files = sorted(p.name for p in bundle.iterdir() if p.is_file())
    _write_json(bundle / "live_bundle_manifest.json", {
        "run_id": run_id,
        "absolute_path": str(bundle.resolve()),
        "repo_commit": repo_commit,
        "files": manifest_files,
        "live_only": True,
    })

    print(f"\nGate 3D live bundle (container): {bundle}")
    print("Next: docker cp bundle to worktree, then host finalize:")
    print("  cd <worktree> && uv run python nextseek_api/cc_assistant/scripts/step7_gate3d_host_finalize.py \\")
    print(f"    nextseek_api/cc_assistant/tests/acceptance_evidence/step7/{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
