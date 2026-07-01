"""REAL-STACK PAID acceptance for the Container-CC (dmac_assistant) route.

Proves the dmac_assistant integration ACTUALLY runs end-to-end on this server:

  * the REAL BAML router (Gemini/Bedrock) decides ``container_cc`` (not heuristic),
  * a REAL ``dmac-assistant:poc`` agent runs a REAL Claude **Opus** turn THROUGH
    the Bedrock auth-proxy — the agent container holds ZERO AWS creds and none of
    the shared backend creds (OI-3),
  * on a DEDICATED network the agent shares only with the proxy + nginx (the agent
    cannot reach neo4j/mysql),
  * and the host-side copier publishes the artifact into the user's output dir.

It writes a committed evidence bundle under ``outputs/cc_acceptance/<run>/`` that
``validate_cc_acceptance.py`` re-checks with ZERO spend, so the proof is
reproducible and verifiable.

GATED: only runs when ``RUN_REALSTACK=1``. Needs the deployed stack
(``dmac-assistant:poc`` image, ``dmac-cc-net`` + ``dmac-bedrock-proxy`` up,
``/var/run/docker.sock``). Run inside the nextseek container:

  docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek \\
    sh -lc 'cd /app && uv run python manage.py test \\
    nextseek_api.cc_assistant.tests.test_cc_realstack \\
    --settings=dmac.test_settings_realstack --noinput'
"""
import json
import os
import re
import subprocess
import threading
import time
import unittest
import uuid
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from nextseek_api.cc_assistant import cc_config, cc_engine
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import (
    format_report,
    validate_run,
)

RUN = os.environ.get("RUN_REALSTACK") == "1"
OPUS = "us.anthropic.claude-opus-4-8"
PROXY_CONTAINER = os.environ.get("DMAC_PROXY_CONTAINER", "dmac-bedrock-proxy")
NET = cc_engine.DEFAULT_NETWORK
BUDGET_CAP = float(os.environ.get("NEXTSEEK_CC_MAX_BUDGET_USD", "2.0"))
EVID_ROOT = Path(settings.BASE_DIR) / "outputs" / "cc_acceptance"


def _docker(*args, timeout=30):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _seek_creds():
    u, p = os.environ.get("SEEK_TEST_USER"), os.environ.get("SEEK_TEST_PASS")
    if u and p:
        return u, p
    cfg = (getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
           or getattr(settings, "NEXTSEEK_CHAT_CONFIG", None))
    return getattr(cfg, "API_USER", None), getattr(cfg, "API_PASS", None)


@unittest.skipUnless(RUN, "real-stack paid acceptance; set RUN_REALSTACK=1 to run")
class CCRealStackAcceptance(TestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ok, detail = cc_engine.cc_runner_available()
        if not ok:
            raise unittest.SkipTest(f"CC runner not available: {detail}")
        r = _docker("inspect", "-f", "{{.State.Running}}", PROXY_CONTAINER)
        if r.returncode != 0 or "true" not in (r.stdout or ""):
            raise unittest.SkipTest(f"{PROXY_CONTAINER} is not running: {r.stdout or r.stderr}")
        cls.run_id = "cc-" + uuid.uuid4().hex[:12]
        cls.user_id = "ccacc"  # passes the user_id charset; scoped-access default
        cls.sentinel = "NSCC-" + uuid.uuid4().hex[:10].upper()
        cls.api_user, cls.api_pass = _seek_creds()
        cls.evid = EVID_ROOT / cls.run_id
        cls.evid.mkdir(parents=True, exist_ok=True)

    # -- helpers ---------------------------------------------------------------

    def _capture_agent_env(self, run_id, deadline_s=120):
        """Poll for the live sibling by label and capture its Config.Env."""
        end = time.time() + deadline_s
        while time.time() < end:
            ps = _docker("ps", "-q", "--filter", f"label=nextseek.cc.run={run_id}")
            cid = (ps.stdout or "").strip().split("\n")[0]
            if cid:
                ins = _docker("inspect", "-f", "{{json .Config.Env}}", cid)
                if ins.returncode == 0 and ins.stdout.strip():
                    try:
                        env_list = json.loads(ins.stdout)
                        return "\n".join(env_list)
                    except json.JSONDecodeError:
                        return ins.stdout
            time.sleep(0.5)
        return ""

    def _net_containers(self, net):
        r = _docker("network", "inspect", net, "-f",
                    "{{range .Containers}}{{.Name}} {{end}}")
        return [c for c in (r.stdout or "").split() if c]

    # -- tests -----------------------------------------------------------------

    def test_01_real_baml_router_decides_container_cc(self):
        """The REAL router (not the heuristic fallback) classifies an agentic
        task as container_cc and pins Opus."""
        # Lab-related AND agentic (code execution + file I/O on NExtSEEK data), so
        # it routes to container_cc rather than the deterministic nextseek_query
        # pipeline — and is on-topic, so the router's Unrelated guard won't reject it.
        query = ("Write and run a Python script that pulls the published samples "
                 "from NExtSEEK and saves to a file the UIDs of any that are missing "
                 "an 'organism' value, then tell me the output file path.")
        decision = cc_router.decide(query)
        (self.evid / "routed_route_decided.json").write_text(json.dumps({
            "route": decision.route, "source": decision.source,
            "model_class": decision.model_class, "model_id": decision.model_id,
            "reasoning": decision.reasoning,
        }))
        self.assertEqual(decision.source, "baml",
                         f"router fell back to {decision.source!r} — the BAML/LLM "
                         f"router is not actually running ({decision.reasoning}).")
        self.assertEqual(decision.route, cc_router.ROUTE_CC,
                         f"router did not choose container_cc ({decision.reasoning}).")
        self.assertEqual(decision.model_id, OPUS, "CC model must be Opus (proxy allowlist).")

    def test_02_cc_turn_runs_real_opus_via_proxy_and_publishes(self):
        paths = cc_config.CCPaths.from_env()
        events: list = []
        errbox: dict = {}

        def send_event(ev, data):
            events.append((ev, dict(data)))

        before = (_docker("logs", PROXY_CONTAINER).stdout or "") + \
                 (_docker("logs", PROXY_CONTAINER).stderr or "")

        query = (
            f"Create a file at /data/scratch/result.txt whose entire contents are "
            f"exactly this token: {self.sentinel}\n"
            f"Then, in your final reply, include the exact token {self.sentinel}."
        )

        def _target():
            try:
                cc_engine.run_cc_turn(
                    query=query, model_id=OPUS, send_event=send_event,
                    user_id=self.user_id, project_dirname="personal-ccacc-ccacc",
                    run_id=self.run_id,
                    paths=paths, api_user=self.api_user, api_pass=self.api_pass,
                )
            except Exception as exc:  # noqa: BLE001
                errbox["err"] = repr(exc)
                send_event("query_error", {"error": repr(exc)})

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        agent_env = self._capture_agent_env(self.run_id, deadline_s=150)
        t.join(timeout=210)
        self.assertFalse(t.is_alive(), "CC turn did not finish within 210s")

        after = (_docker("logs", PROXY_CONTAINER).stdout or "") + \
                (_docker("logs", PROXY_CONTAINER).stderr or "")
        # Window: only the bytes appended during this run.
        proxy_window = after[len(before):] if after.startswith(before[:200]) else after
        (self.evid / "proxy_log.txt").write_text(proxy_window)

        terminal = next(((e, d) for e, d in reversed(events)
                         if e in ("query_complete", "query_error")), None)
        self.assertIsNotNone(terminal, f"no terminal event; got {[e for e, _ in events]} "
                                       f"err={errbox.get('err')}")
        ev, data = terminal
        (self.evid / "forced_result.json").write_text(json.dumps({
            "event": ev, "is_error": ev == "query_error",
            "reply": data.get("reply", ""), "error": data.get("error"),
            "total_cost_usd": data.get("total_cost_usd"),
            "artifacts": data.get("artifacts") or [],
        }))

        (self.evid / "agent_env_scan.txt").write_text(agent_env)
        net_containers = self._net_containers(NET)
        (self.evid / "network.json").write_text(json.dumps({"containers": net_containers}))
        artifacts = data.get("artifacts") or []
        cost = data.get("total_cost_usd")
        (self.evid / "ledger.json").write_text(json.dumps({"total_cost_usd": cost or 0.0}))
        (self.evid / "meta.json").write_text(json.dumps({
            "run_id": self.run_id, "user_id": self.user_id, "sentinel": self.sentinel,
            "model_id": OPUS, "budget_cap_usd": BUDGET_CAP,
        }))

        # --- direct assertions (the validator re-checks the committed bundle) ---
        self.assertEqual(ev, "query_complete", f"CC turn errored: {data.get('error')}")
        self.assertIn(self.sentinel, data.get("reply", ""),
                      "reply did not echo the per-run sentinel (no real turn?)")
        self.assertTrue(agent_env.strip(), "failed to capture the live agent env")
        for key in ("AWS_BEARER_TOKEN_BEDROCK", "NEO4J_PASSWORD", "MYSQL_PASSWORD", "GCP_API_KEY"):
            self.assertNotRegex(agent_env, rf"(^|\W){key}=", f"{key} leaked into the agent")
        self.assertNotIn("ABSK", agent_env, "AWS bearer token prefix in the agent env")
        self.assertRegex(proxy_window, re.escape(OPUS), "no opus-4-8 invoke in the proxy log")
        self.assertNotIn("ABSK", proxy_window, "proxy logged the bearer token")
        backend = [c for c in net_containers
                   if re.search(r"(^|[-_])(neo4j|seek|mysql)([-_]|$)", c)]
        self.assertEqual(backend, [], f"backend service on the agent network: {backend}")
        self.assertTrue(artifacts, "query_complete missing artifacts")
        for a in artifacts:
            key = a.get("key", "")
            self.assertIn("/", key, f"artifact key not turn-scoped: {key!r}")

        all_ok, checks = validate_run(self.evid)
        print("\n[CC-ACCEPTANCE] run=" + self.run_id + "\n" + format_report(all_ok, checks))
        self.assertTrue(all_ok, f"validator failed: {[c for c in checks if not c[1]]}")
