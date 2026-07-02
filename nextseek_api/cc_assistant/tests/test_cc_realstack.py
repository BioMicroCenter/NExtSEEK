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
from nextseek_api.cc_assistant.tests import cc_matrix_gate_harness as gate
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import (
    format_report,
    validate_run,
)
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    format_report as step7_format_report,
    validate_run as step7_validate_run,
)

RUN = os.environ.get("RUN_REALSTACK") == "1"
OPUS = "us.anthropic.claude-opus-4-8"
PROXY_CONTAINER = os.environ.get("DMAC_PROXY_CONTAINER", "dmac-bedrock-proxy")
NET = cc_engine.DEFAULT_NETWORK
BUDGET_CAP = float(os.environ.get("NEXTSEEK_CC_MAX_BUDGET_USD", "2.0"))
EVID_ROOT = Path(settings.BASE_DIR) / "outputs" / "cc_acceptance"

# Task 15 (G7-11) capability-gate evidence bundle root -- the SPEC-7 section 8
# convention (PLAN-7-compose-native-prod-deploy.md): "acceptance_evidence/step7/<run_id>/".
STEP7_EVID_ROOT = (
    Path(settings.BASE_DIR) / "nextseek_api" / "cc_assistant" / "tests" / "acceptance_evidence" / "step7"
)
GATE_NEXTSEEK_CONTAINER = os.environ.get("NEXTSEEK_CONTAINER", "nextseek")
# nginx carries no `container_name:` pin (docker-compose.yml) -- its runtime
# name is compose-project-prefixed. Resolved by substring match at run time
# (gate.resolve_container_by_name_substring), NOT hardcoded to either the
# bare service name or one specific project prefix; overridable for
# non-default topologies.
GATE_NGINX_NAME_SUBSTRING = os.environ.get("NEXTSEEK_NGINX_CONTAINER_SUBSTRING", "nextseek_nginx")


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


@unittest.skipUnless(RUN, "Task 15 capability gate; set RUN_REALSTACK=1 to run")
class CCCapabilityGateMatrix(TestCase):
    """Task 15 (G7-11): proves all 9 ``nextseek-*`` plugin ops live, in a
    DEDICATED gate executor (``dmac-cc-matrix-<run_id>``) -- OUTSIDE the
    180s in-turn cap, which makes a live 9-op matrix structurally
    unachievable inside one forced-CC turn (iter-2 R2-H1/R2-M2).

    Written now (Task 15); EXECUTED only later (Tasks 9/10) on a dev-VM/MBP
    with the user's sign-off -- never in this session. GATED identically to
    ``CCRealStackAcceptance`` above (``RUN_REALSTACK=1``); needs the
    deployed stack (``nextseek-sidecar``/``dmac-bedrock-proxy``/nginx up,
    ``/var/run/docker.sock``, the gate user's own SEEK login with a real
    sandbox project to seed against).

    Writes every SPEC-7 section 8 plugin_ops_matrix.json + companion
    artifact under ``acceptance_evidence/step7/<run_id>/`` and re-checks the
    bundle with ``validate_step7_compose_deploy.validate_run`` (zero
    additional spend -- the checker only reads committed files).
    """

    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ok, detail = cc_engine.cc_runner_available()
        if not ok:
            raise unittest.SkipTest(f"CC runner not available: {detail}")
        cls.run_id = "matrix-" + uuid.uuid4().hex[:12]
        cls.gate_user_id = os.environ.get("NEXTSEEK_CC_GATE_USER_ID", "ccgateuser")
        # Step 3b (iter-1 H-3): the sandbox SEEK project is CREATED by the
        # fixture; the {pid}-{slug} gate_project dirname is DERIVED from the
        # created project's real id (fixture.project) inside the test --
        # never assumed to pre-exist (greenfield works from zero).
        # NEXTSEEK_CC_GATE_PROJECT remains an explicit operator OVERRIDE only.
        cls.gate_project_title = os.environ.get(
            "NEXTSEEK_CC_GATE_PROJECT_TITLE", gate.DEFAULT_GATE_PROJECT_TITLE
        )
        cls.gate_project_override = os.environ.get("NEXTSEEK_CC_GATE_PROJECT")
        cls.api_user, cls.api_pass = _seek_creds()
        cls.paths = cc_config.CCPaths.from_env()
        cls.evid = STEP7_EVID_ROOT / cls.run_id
        cls.evid.mkdir(parents=True, exist_ok=True)

    def _op_kwargs(self, fixture: "gate.SeededFixture") -> dict:
        """One representative, safe, read-mostly invocation per op, targeting
        the seeded fixture for the data-dependent ops (Step 3b). The write op
        defaults to the SAFE unconfirmed leg (pinned Layer-2 exit-5
        WRITE_BLOCKED form) unless the operator has recorded explicit
        sign-off via NEXTSEEK_CC_GATE_CONFIRM_WRITE=1 -- confirming a write
        must never be this harness's own silent default."""
        confirmed = os.environ.get("NEXTSEEK_CC_GATE_CONFIRM_WRITE") == "1"
        # No fabricated-UID fallback: the fixture CREATED these samples
        # (create_seeded_fixture raises FixtureCreationError otherwise), and
        # the caller asserts uids is non-empty before building op kwargs.
        uids = ",".join(fixture.uids)
        # The ops address the sandbox project by its SEEK TITLE (what the
        # server-side agents resolve project references by), not the local
        # {pid}-{slug} CC dirname (which only names the CC user tree).
        project_title = fixture.title
        return {
            "nextseek-entity-extract": {"query": "list sampletypes and assays used in this project"},
            "nextseek-parse": {"query": f"find samples in project {project_title}"},
            "nextseek-graph": {"query": f"show lineage for a sample in project {project_title}"},
            "nextseek-plan": {"query": f"recommend next steps to summarize project {project_title}"},
            "nextseek-query": {"query": f"how many samples exist in project {project_title}?"},
            "nextseek-api-read": {
                "parser_plan": json.dumps({"endpoint": "/nextseek_api/samples/", "method": "GET"}),
            },
            "nextseek-api-write": {
                "parser_plan": json.dumps({
                    "endpoint": "/nextseek_api/samples/", "method": "PATCH",
                    "requestBody": {"notes": f"Task 15 capability gate {self.run_id}"},
                }),
                "confirmed_write": confirmed,
            },
            "nextseek-report": {"mode": "samples", "project": project_title},
            "nextseek-generate-submission": {"submission_type": "GEO", "uids": uids},
        }

    def test_01_seeded_fixture_matrix_sweep_and_companions(self):
        # --- Step 3b: CREATE the seeded fixture, BEFORE the matrix run --------
        # (iter-1 H-3: one sandbox project + sample UIDs, created as the gate
        # user via the authenticated REST API; FixtureCreationError fails the
        # gate loudly -- nothing is faked, greenfield works from zero.)
        base_url = os.environ.get("NEXTSEEK_BASE_URL", "http://nextseek_nginx")
        fixture = gate.create_seeded_fixture(
            assistant_base_url=base_url,
            api_user=self.api_user, api_pass=self.api_pass,
            gate_project_title=self.gate_project_title, run_id=self.run_id,
        )
        self.assertTrue(fixture.uids, "seeded fixture created no sample UIDs")
        gate_project = self.gate_project_override or fixture.project
        gate.write_json(self.evid / "seeded_fixture.json", fixture.to_json())

        # images.json's CC-image key (Task 1's collector normally writes the
        # full images.json; re-record just this key here so the matrix's own
        # provenance check is independently satisfiable).
        cc_image = cc_engine.DEFAULT_IMAGE
        images_path = self.evid / "images.json"
        images_obj = json.loads(images_path.read_text()) if images_path.is_file() else {}
        images_obj["cc-agent"] = cc_image
        gate.write_json(images_path, images_obj)

        # --- Step 3: spawn the DEDICATED gate executor -----------------------
        environment = gate.gate_executor_environment(
            api_user=self.api_user, api_pass=self.api_pass,
            path_mappings={"scratch": self.paths.user_root_mount},
        )
        run_kwargs = gate.build_gate_executor_run_kwargs(
            run_id=self.run_id, image=cc_image, environment=environment,
        )

        import docker  # local import: never required for the hermetic suite
        client = docker.from_env()
        executor = client.containers.run(**run_kwargs)
        container_name = run_kwargs["name"]
        try:
            provenance = gate.docker_inspect_one(container_name)
            self.assertIsNotNone(provenance, "docker inspect of the gate executor failed")
            container_id = provenance["Id"]

            nginx_name = gate.resolve_container_by_name_substring(GATE_NGINX_NAME_SUBSTRING)
            self.assertIsNotNone(nginx_name, f"no running container matches {GATE_NGINX_NAME_SUBSTRING!r}")
            nginx_before = subprocess.run(
                ["docker", "logs", nginx_name], capture_output=True, text=True,
            ).stdout

            op_kwargs = self._op_kwargs(fixture)
            matrix: dict = {}
            for op in gate.BIN_OPS:
                argv = gate.build_op_argv(op, **op_kwargs[op])
                result = gate.docker_exec_op(container_name, argv)
                matrix[op] = gate.make_matrix_row(
                    op, result=result, container_id=container_id, container_name=container_name,
                    image=cc_image, transport=gate.TRANSPORT_FOR_OP[op],
                )
            gate.write_json(self.evid / "plugin_ops_matrix.json", matrix)

            nginx_window = gate.capture_nginx_log_window(nginx_name, before=nginx_before)
            gate.write_text(self.evid / "gate_access_log_window.txt", nginx_window)

            gate.write_text(self.evid / "matrix_env_scan.txt", gate.capture_matrix_env_scan(container_name))

            network_inspect_matrix = gate.docker_network_inspect(NET)
            gate.write_json(self.evid / "network_inspect_matrix.json", network_inspect_matrix)
        finally:
            try:
                executor.remove(force=True)
            except Exception:  # noqa: BLE001 -- best-effort cleanup, never mask the real failure
                pass

        # --- Step 3: trusted sweep, AFTER the matrix --------------------------
        sweep = gate.run_trusted_sweep(
            nextseek_container=GATE_NEXTSEEK_CONTAINER, user_id=self.gate_user_id,
            api_user=self.api_user, project=gate_project,
        )
        gate.write_json(self.evid / "sweep_invocation.json", sweep)

        # Sweep cross-check: patch published_path onto the two rows whose
        # published artifacts the sweep just delivered. Best-effort
        # positional correlation (report, then generate-submission, in the
        # order the sweep's own delivered list reports them) -- Task 9/10
        # should verify this pairing holds against the live sweep output
        # shape before treating a gate run as authoritative (see the Task 15
        # report's "Concerns" section).
        try:
            sweep_body = json.loads(sweep["output_excerpt"])
        except (KeyError, TypeError, ValueError):
            sweep_body = {}
        delivered = sweep_body.get("delivered") or []
        scratch_dir = sweep_body.get("scratch_dir")
        matrix = json.loads((self.evid / "plugin_ops_matrix.json").read_text())
        for op, relpath in zip(gate.PUBLISHED_PATH_OPS, delivered):
            if scratch_dir:
                matrix[op]["published_path"] = f"{scratch_dir}/{relpath}"
        gate.write_json(self.evid / "plugin_ops_matrix.json", matrix)

        post_sweep_scan = gate.capture_post_sweep_user_tree_scan(volume=self.paths.users_volume)
        gate.write_text(self.evid / "post_sweep_user_tree_scan.txt", post_sweep_scan)

        # --- meta.json (best-effort spend estimate, iter-3 L-3) --------------
        total_wall = sum(float(r.get("wall_secs") or 0) for r in matrix.values())
        meta_path = self.evid / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        meta.update({
            "run_id": self.run_id,
            "gate_project": gate_project,
            "gate_user_id": self.gate_user_id,
            "matrix_spend_estimate_usd": round(total_wall * 0.01, 4),
            "matrix_spend_estimate_method": (
                "sum of per-op wall_secs * a flat $0.01/s heuristic; exact "
                "per-op server-side LLM cost is not programmatically available"
            ),
        })
        gate.write_json(meta_path, meta)

        # --- direct assertions + the independent validator re-check ----------
        for op in gate.BIN_OPS:
            row = matrix[op]
            self.assertNotEqual(row["exit_code"], 7, f"{op}: TRANSPORT_ERROR (missing backend)")

        all_ok, checks = step7_validate_run(self.evid)
        print("\n[CC-CAPABILITY-GATE] run=" + self.run_id + "\n" + step7_format_report(all_ok, checks))
        # NOTE: a full green `all_ok` additionally needs the REST of the
        # SPEC-7 section 8 bundle (preflight.json, network_inspect.json from
        # the isolation-scan harness, etc.) -- this test writes only the
        # Task 15 matrix + its companions, so it does not itself assert
        # `all_ok`. Task 9/10 assembles the full bundle from both harnesses
        # before treating the validator's verdict as authoritative.
