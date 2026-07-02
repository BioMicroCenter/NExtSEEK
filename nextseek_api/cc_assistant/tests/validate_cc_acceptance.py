"""Zero-spend, reproducible validator for the Container-CC live acceptance evidence.

The live test (``test_cc_realstack.py``) runs ONE paid Container-CC turn and writes
an evidence bundle under ``outputs/cc_acceptance/<run>/``. This module re-checks
every acceptance criterion by reading ONLY those committed files — no Docker, no
network, no paid call — so anyone can re-verify the proof:

    python -m nextseek_api.cc_assistant.tests.validate_cc_acceptance <run_dir>

It maps 1:1 onto the security-acceptance checklist (AUDIT.md, live items):
  11 real router    routed_route_decided.json: source=="baml", route=="container_cc"
  12 real Opus turn forced_result.json: is_error false, reply echoes the sentinel
  12 proxy live     proxy_log.txt: >=1 `POST /model/<opus-4-8>/invoke[...] -> 200`
  13 token unlogged proxy_log.txt: 0 occurrences of `ABSK` / `Authorization`
  10 agent de-cred  agent_env_scan.txt: none of the 16 shared keys; no `ABSK`/`demopassword`
   9 segmentation   network.json: agent net excludes neo4j/seek-mysql/seek/seek-solr
  16 turn-scoped artifacts forced_result.json: non-empty artifacts with turn-scoped keys
                    (nested/turn-scoped shape -- rejects the pre-Step-2 flat
                    ``{user_id}/...`` copier-scope prefix)
  17 cost ledger    ledger.json: total_cost_usd <= budget_cap_usd

The bundle holds NO secret: a correct agent env has none, so committing
``agent_env_scan.txt`` is safe; the validator proves de-credentialing by the
absence of the shared KEY names (if no shared key is present, no shared value
can be either, by construction) plus the public ``ABSK`` prefix and the
committed-default ``demopassword``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# The 16 shared backend-credential env keys that must NEVER be in the agent.
SHARED_CRED_KEYS = (
    "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_API_KEY",
    "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE",
    "MYSQL_HOST", "MYSQL_HOST_DEV", "MYSQL_HOST_PROD", "MYSQL_PORT",
    "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DEV_PASSWORD",
    "MYSQL_PROD_PASSWORD", "MYSQL_ROOT_PASSWORD", "GCP_API_KEY",
)
# Non-secret markers whose presence in the agent env betrays a leak.
LEAK_MARKERS = ("ABSK", "demopassword")
# Backend-service name STEMS the de-credentialed agent's network must NOT contain.
# Matched on word boundaries so the legitimately-dual-homed ``nextseek_nginx``
# entrypoint (which contains the substring "seek") is NOT a false positive.
FORBIDDEN_NET_PEERS = ("neo4j", "seek", "mysql")
_PEER_RE = {s: re.compile(rf"(^|[-_]){re.escape(s)}([-_]|$)") for s in FORBIDDEN_NET_PEERS}

# --- G7-11 / Task 15: dmac-cc-net CLOSED-SET peer identity primitives -------
# This module is the existing home of the denylist peer-stem rules above
# (``_PEER_RE`` / ``FORBIDDEN_NET_PEERS``), which
# ``validate_step7_compose_deploy.check_network_segmentation_ok`` already
# imports and reuses. Task 15 Step 2 (SPEC-7 section 10 G7-11) hardens
# ``dmac-cc-net`` membership from a denylist into an enforceable CLOSED SET;
# these constants/helper are mirrored HERE (single source of truth) so both
# validators reuse the identical peer identity rule rather than each
# maintaining a drift-prone copy.
CC_AGENT_NAME_RE = re.compile(r"^dmac-cc-agent-[0-9a-f-]{1,64}$")
NGINX_PEER_NAME_RE = re.compile(r"(^|[-_])nextseek_nginx(?:[-_]\d+)?$")
BEDROCK_PROXY_CONTAINER_NAME = "dmac-bedrock-proxy"
SIDECAR_CONTAINER_NAME = "nextseek-sidecar"


def matrix_executor_name(run_id: str) -> str:
    """The reserved Task 15 gate-executor container name for one capability-gate run."""
    return f"dmac-cc-matrix-{run_id}"


def is_dmac_cc_net_closed_set_member(name: str, *, run_id: str | None = None) -> bool:
    """True iff ``name`` is a legitimate ``dmac-cc-net`` peer under the G7-11
    closed-set rule (Task 15 Step 2 / SPEC-7 section 10 G7-11): the nginx
    entrypoint (bare ``nextseek_nginx`` or compose-project-prefixed runtime
    form), the bedrock proxy, the NS sidecar, any general-pattern transient CC
    agent (``dmac-cc-agent-<run>`` — concurrent legitimate turns from other
    users are lawful on a shared dev VM), or -- when ``run_id`` is supplied --
    THIS run's reserved gate-executor name (``dmac-cc-matrix-<run_id>``,
    exact; not a general pattern, unlike agents). The exact literal
    ``"nextseek"`` is NEVER a member: the app container itself must never
    join the de-credentialed agent's segmented network."""
    if name == "nextseek":
        return False
    if NGINX_PEER_NAME_RE.search(name):
        return True
    if name in (BEDROCK_PROXY_CONTAINER_NAME, SIDECAR_CONTAINER_NAME):
        return True
    if CC_AGENT_NAME_RE.match(name):
        return True
    if run_id and name == matrix_executor_name(run_id):
        return True
    return False


OPUS = "us.anthropic.claude-opus-4-8"
_INVOKE_200 = re.compile(
    r"POST\s+/model/" + re.escape(OPUS) + r"/invoke(?:-with-response-stream)?\b[^\n]*?->\s*200"
)


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def validate_run(run_dir: str | Path) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Return (all_passed, [(check_name, ok, detail), ...])."""
    d = Path(run_dir)
    checks: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    meta = _load_json(d / "meta.json") if (d / "meta.json").is_file() else {}
    sentinel = meta.get("sentinel", "")
    cap = float(meta.get("budget_cap_usd", 5.0))

    # 11 — real BAML router decision (not heuristic, not forced)
    try:
        rd = _load_json(d / "routed_route_decided.json")
        ok = rd.get("source") == "baml" and rd.get("route") == "container_cc"
        add("router_is_baml", ok, f"source={rd.get('source')} route={rd.get('route')}")
    except Exception as e:  # noqa: BLE001
        add("router_is_baml", False, f"unreadable: {e}")

    # 12 — real Opus turn: completed, not error, sentinel echoed in the reply
    try:
        res = _load_json(d / "forced_result.json")
        is_err = bool(res.get("is_error") or res.get("error"))
        reply = res.get("reply") or res.get("result") or ""
        add("turn_completed_no_error", not is_err, f"is_error={is_err}")
        add("reply_echoes_sentinel", bool(sentinel) and sentinel in reply,
            f"sentinel={sentinel!r} present={sentinel in reply if sentinel else False}")
    except Exception as e:  # noqa: BLE001
        add("turn_completed_no_error", False, f"unreadable: {e}")
        add("reply_echoes_sentinel", False, "no result")

    # 12/13 — proxy traversed live (>=1 opus-4-8 invoke -> 200) and token never logged
    try:
        log = (d / "proxy_log.txt").read_text(encoding="utf-8", errors="replace")
        n200 = len(_INVOKE_200.findall(log))
        add("proxy_opus_invoke_200", n200 >= 1, f"{n200} opus-4-8 invoke->200 lines")
        leaks = sum(log.count(m) for m in ("ABSK", "Authorization", "authorization"))
        add("proxy_never_logs_token", leaks == 0, f"{leaks} token/authz occurrences")
    except Exception as e:  # noqa: BLE001
        add("proxy_opus_invoke_200", False, f"unreadable: {e}")
        add("proxy_never_logs_token", False, "no proxy log")

    # 10 — agent env de-credentialed (no shared KEY, no leak markers)
    try:
        envtxt = (d / "agent_env_scan.txt").read_text(encoding="utf-8", errors="replace")
        present_keys = [k for k in SHARED_CRED_KEYS if re.search(rf"(^|\W){re.escape(k)}=", envtxt)]
        present_markers = [m for m in LEAK_MARKERS if m in envtxt]
        add("agent_env_no_shared_keys", not present_keys, f"leaked keys: {present_keys}")
        add("agent_env_no_leak_markers", not present_markers, f"markers: {present_markers}")
    except Exception as e:  # noqa: BLE001
        add("agent_env_no_shared_keys", False, f"unreadable: {e}")
        add("agent_env_no_leak_markers", False, "no env scan")

    # 9 — network segmentation: agent net excludes backend services
    try:
        net = _load_json(d / "network.json")
        peers = set(net.get("containers", net if isinstance(net, list) else []))
        bad = sorted(p for p in peers if any(rx.search(p) for rx in _PEER_RE.values()))
        add("network_segmented", not bad, f"forbidden peers on agent net: {bad}")
    except Exception as e:  # noqa: BLE001
        add("network_segmented", False, f"unreadable: {e}")

    # 16 — turn-scoped artifact keys on query_complete (copier-scope oracle).
    # PLAN-7 Task 5 Step 3 publish-scope migration: the pre-Step-2 copier used
    # a FLAT `{user_id}/<relpath>` publish prefix; Step 2's nested per-project
    # per-user dirs (and Task 6's `logical_root`) replaced it with turn-scoped
    # `{turn_id}/<relpath>` keys (see cc_engine._publish_artifacts). A key is
    # only rejected as the stale flat scheme when its leading path segment is
    # LITERALLY the run's own `user_id` (from meta.json) -- that is the
    # concrete, checkable difference between "flat {user_id}/ prefix" and any
    # nested/turn-scoped shape, which by construction never leads with the
    # bare user_id segment.
    try:
        res = _load_json(d / "forced_result.json")
        arts = res.get("artifacts") or []
        user_id = str(meta.get("user_id") or "")

        def _not_flat_user_id_prefixed(key: str) -> bool:
            if not key or "/" not in key:
                return False
            leading = key.split("/", 1)[0]
            return not (user_id and leading == user_id)

        ok = bool(arts) and all(_not_flat_user_id_prefixed(a.get("key") or "") for a in arts)
        add("artifacts_turn_scoped", ok, f"{len(arts)} artifact(s); turn-scoped={ok}")
    except Exception as e:  # noqa: BLE001
        add("artifacts_turn_scoped", False, f"unreadable: {e}")

    # 17 — cost ledger under cap
    try:
        led = _load_json(d / "ledger.json")
        cost = float(led.get("total_cost_usd", led.get("usd", 0.0)))
        add("cost_under_cap", cost <= cap, f"${cost:.4f} <= ${cap:.2f}")
    except Exception as e:  # noqa: BLE001
        add("cost_under_cap", False, f"unreadable: {e}")

    all_ok = all(ok for _, ok, _ in checks)
    return all_ok, checks


def format_report(all_ok: bool, checks: list[tuple[str, bool, str]]) -> str:
    lines = [f"{'PASS' if ok else 'FAIL'}  {name:28s} {detail}" for name, ok, detail in checks]
    lines.append("")
    lines.append(f"{'ALL CHECKS PASSED' if all_ok else 'ACCEPTANCE FAILED'} "
                 f"({sum(ok for _, ok, _ in checks)}/{len(checks)})")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m ...validate_cc_acceptance <run_dir>", file=sys.stderr)
        return 2
    all_ok, checks = validate_run(argv[1])
    print(format_report(all_ok, checks))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
