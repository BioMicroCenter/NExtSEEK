"""Task 15 (G7-11) capability-gate harness: DORMANT support code for proving
all 9 ``nextseek-*`` plugin ops live, in a DEDICATED gate executor, on a
real deployed stack.

This module is imported ONLY by ``test_cc_realstack.py``'s RUN_REALSTACK-
gated test class (never by the hermetic suite), so importing it never
touches Docker, the network, or a database. It is written now (Task 15) and
EXECUTED later (Tasks 9/10) on a dev-VM/MBP with the user's sign-off, per the
180 s in-turn cap making a live 9-op matrix structurally unachievable inside
one forced-CC turn (iter-2 R2-H1/R2-M2).

Contract this module fulfils (SPEC-7 section 8's plugin_ops_matrix.json
paragraph + Task 15 brief):

* the matrix runs in a DEDICATED GATE EXECUTOR (``dmac-cc-matrix-<run_id>``),
  never inside a live turn;
* the executor is spawned with the SAME image as ``images.json``'s CC image,
  attached to ``dmac-cc-net``, with env produced by the SAME
  ``cc_engine.build_agent_environment`` code path used for production agents
  (iter-3 M-1 -- never hand-assembled);
* the 9 ops are executed sequentially inside it via ``docker exec`` (per-op
  timeout pinned), with ``docker inspect`` provenance recorded per row;
* the trusted sweep entrypoint (Task 14's ``manage.py cc_sweep_staging``) is
  invoked once, after the matrix, with the invocation + a post-sweep
  recursive scan captured as evidence;
* dual ``docker network inspect dmac-cc-net`` captures (during-turn window
  is Task 13/the isolation-scan harness's job, NOT this module's -- this
  module captures ONLY the matrix window) plus the nginx access-log window
  and the executor's sanitized env scan are written as companion artifacts.

The PURE-LOGIC pieces (argv construction, exec-result -> matrix-row shaping,
artifact-dict assembly) are unit-tested hermetically in
``test_cc_matrix_gate_harness.py`` with no Docker socket. The Docker/HTTP-
touching pieces (spawn, exec, inspect, sweep invocation, seeded-fixture REST
calls) are exercised only when Task 9/10 actually runs this harness with
``RUN_REALSTACK=1``.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import matrix_executor_name
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    BIN_OPS,
    IMAGES_JSON_CC_IMAGE_KEY,
    OP_ASSISTANT_ENDPOINT,
    PUBLISHED_PATH_OPS,
)

# Harness-only safety bound on a single `docker exec` op invocation. This is
# DELIBERATELY independent of the 150s in_turn_viable headroom the validator
# evaluates (capability != latency -- the gate executor is NOT subject to the
# 180s in-turn cap); it exists only so a hung sidecar/LLM call cannot stall
# the gate run forever.
PER_OP_EXEC_TIMEOUT_SECS = 300

DMAC_RUNTIME_MODE_IDLE = {"DMAC_RUNTIME_MODE": "idle"}


# --------------------------------------------------------------------------
# Pure logic: per-op argv construction
# --------------------------------------------------------------------------

def build_op_argv(
    op: str,
    *,
    query: str | None = None,
    parser_plan: str | None = None,
    confirmed_write: bool = False,
    mode: str | None = None,
    project: str | None = None,
    submission_type: str | None = None,
    uids: str | None = None,
) -> list[str]:
    """The exact CLI argv for one bin op, matching each shim's own argument
    contract (docker/cc-runtime/build_context/plugins/nextseek/bin/*).
    ``nextseek-query`` always passes ``--json`` -- its DEFAULT (no --json)
    form prints only the extracted ``.reply`` string, which is not the
    excerpt shape the validator's per-op allowlist expects."""
    if op not in BIN_OPS:
        raise ValueError(f"unknown bin op: {op!r}")
    if op in ("nextseek-entity-extract", "nextseek-parse", "nextseek-graph", "nextseek-plan"):
        if not query:
            raise ValueError(f"{op} requires query")
        return [op, "--query", query]
    if op == "nextseek-query":
        if not query:
            raise ValueError(f"{op} requires query")
        return [op, "--query", query, "--json"]
    if op == "nextseek-api-read":
        if not parser_plan:
            raise ValueError(f"{op} requires parser_plan")
        argv = [op, "--parser-plan", parser_plan]
        if query:
            argv += ["--query", query]
        return argv
    if op == "nextseek-api-write":
        if not parser_plan:
            raise ValueError(f"{op} requires parser_plan")
        argv = [op, "--parser-plan", parser_plan]
        if confirmed_write:
            argv.append("--confirmed-write")
        if query:
            argv += ["--query", query]
        return argv
    if op == "nextseek-report":
        if not mode or not project:
            raise ValueError(f"{op} requires mode and project")
        return [op, "--mode", mode, "--project", project]
    if op == "nextseek-generate-submission":
        if not submission_type or not uids:
            raise ValueError(f"{op} requires submission_type and uids")
        return [op, "--type", submission_type, "--uids", uids]
    raise AssertionError(f"unreachable: {op!r} in BIN_OPS but not dispatched")  # pragma: no cover


# --------------------------------------------------------------------------
# Executor env (iter-3 M-1: MUST go through cc_engine.build_agent_environment)
# --------------------------------------------------------------------------

def gate_executor_environment(
    *, api_user: str, api_pass: str, path_mappings: dict[str, Any] | None = None,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """The executor's COMPLETE env: the production ``build_agent_environment``
    output (so every existing env guard applies by construction -- OI-3, no
    shared creds) plus exactly one additive runtime-mode flag that puts the
    executor into idle mode (``exec sleep infinity`` in the image's
    entrypoint -- docker/cc-runtime/container/entrypoint.sh) so the harness
    can ``docker exec`` each op into it, mirroring the router's own idle-mode
    convention (DD-04) rather than inventing a new one."""
    env = cc_engine.build_agent_environment(
        source=source, api_user=api_user, api_pass=api_pass,
        path_mappings=path_mappings or {},
    )
    env.update(DMAC_RUNTIME_MODE_IDLE)
    return env


def build_gate_executor_run_kwargs(
    *, run_id: str, image: str, environment: dict[str, str],
    network: str = cc_engine.DEFAULT_NETWORK,
) -> dict[str, Any]:
    """docker-py ``containers.run`` kwargs for the dedicated gate executor.

    Deliberately NOT ``cc_engine._run_kwargs`` (that helper is coupled to
    per-turn semantics: the ``claude`` command, per-turn labels, and the
    ``dmac-cc-agent-`` name). The executor never runs ``claude`` at all --
    idle mode (env, above) makes the entrypoint ``exec sleep infinity``
    regardless of ``command``, so ``command`` is left at the image default.
    """
    return {
        "image": image,
        "command": None,
        "environment": environment,
        "working_dir": cc_engine._CONTAINER_WORKDIR,
        "network": network,
        "name": matrix_executor_name(run_id),
        "labels": {"nextseek.cc": "1", "nextseek.cc.matrix": "1", "nextseek.cc.run": run_id},
        "platform": "linux/amd64",
        "detach": True,
        "stdin_open": False,
        "tty": False,
    }


# --------------------------------------------------------------------------
# Exec / inspect / matrix-row shaping
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    wall_secs: float
    timed_out: bool = False


def docker_exec_op(container_name: str, argv: list[str], *,
                    timeout: float = PER_OP_EXEC_TIMEOUT_SECS,
                    docker_bin: str = "docker") -> ExecResult:
    """Run one op via ``docker exec <container> <argv...>``, timing the wall
    clock and capturing stdout/stderr/exit code. A timeout is recorded as a
    (non-zero, non-pinned) failure -- it can never coincide with the pinned
    Layer-2 write form, so it correctly fails the gate rather than being
    silently swallowed."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [docker_bin, "exec", container_name, *argv],
            capture_output=True, text=True, timeout=timeout,
        )
        wall = time.monotonic() - start
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, wall_secs=wall)
    except subprocess.TimeoutExpired as exc:
        wall = time.monotonic() - start
        return ExecResult(
            exit_code=-1,
            stdout=(exc.stdout or ""),
            stderr=(exc.stderr or "") + f"\n[gate harness] docker exec timed out after {timeout}s",
            wall_secs=wall,
            timed_out=True,
        )


def docker_inspect_one(container_name: str, *, docker_bin: str = "docker") -> dict[str, Any] | None:
    """``docker inspect <name>`` -> the container's own record (Id/Name/
    Config.Image/...), or None on any failure (fail-closed for the caller)."""
    try:
        proc = subprocess.run(
            [docker_bin, "inspect", container_name],
            capture_output=True, text=True, timeout=30, check=True,
        )
        parsed = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    return parsed[0] if isinstance(parsed, list) and parsed else None


def build_ps_name_filter_command(name_substring: str, *, docker_bin: str = "docker") -> list[str]:
    """``docker ps --filter name=<substring> --format {{.Names}}`` -- resolves
    the nginx entrypoint's RUNTIME container name, which (per docker-compose.yml)
    carries no ``container_name:`` pin and so is compose-project-prefixed
    (e.g. ``nextseek-nextseek_nginx-1``), not the bare service name."""
    return [docker_bin, "ps", "--filter", f"name={name_substring}", "--format", "{{.Names}}"]


def resolve_container_by_name_substring(name_substring: str, *, docker_bin: str = "docker",
                                         timeout: float = 15.0) -> str | None:
    """The first running container whose name contains ``name_substring``, or
    None if none is running. Used to resolve the nginx entrypoint's actual
    runtime name (bare OR compose-prefixed) without hardcoding either form."""
    proc = subprocess.run(
        build_ps_name_filter_command(name_substring, docker_bin=docker_bin),
        capture_output=True, text=True, timeout=timeout,
    )
    names = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return names[0] if names else None


def docker_network_inspect(network: str, *, docker_bin: str = "docker") -> Any:
    """The FULL ``docker network inspect <network>`` JSON (the shape
    ``validate_step7_compose_deploy._load_network_inspect_containers``
    expects: a one-element array carrying ``Containers`` keyed by ID)."""
    proc = subprocess.run(
        [docker_bin, "network", "inspect", network],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(proc.stdout)


def make_matrix_row(
    op: str, *, result: ExecResult, container_id: str, container_name: str,
    image: str, transport: str, published_path: str | None = None,
) -> dict[str, Any]:
    """Shape one ``plugin_ops_matrix.json`` row from an ExecResult + the
    executor's provenance -- the {op, transport, exit_code, excerpt,
    container_id, container_name, image, wall_secs} schema (+ published_path
    for the two sweep-cross-check ops), matching what
    ``validate_step7_compose_deploy.py`` requires verbatim."""
    # Success (exit 0) rows excerpt from stdout (the shim's JSON result);
    # non-success rows excerpt from stderr (the shim's `{"error": {...}}`
    # envelope -- see docker/cc-runtime/.../_nextseek_runner.py's `_err`).
    excerpt = result.stdout.strip() if result.exit_code == 0 else result.stderr.strip()
    row: dict[str, Any] = {
        "op": op,
        "transport": transport,
        "exit_code": result.exit_code,
        "excerpt": excerpt,
        "container_id": container_id,
        "container_name": container_name,
        "image": image,
        "wall_secs": round(result.wall_secs, 3),
    }
    if published_path is not None:
        row["published_path"] = published_path
    return row


TRANSPORT_FOR_OP = {op: ("viewset" if op in ("nextseek-query", "nextseek-plan") else "sidecar") for op in BIN_OPS}


# --------------------------------------------------------------------------
# Trusted sweep invocation (Task 14 entrypoint, invoked from OUTSIDE the
# container as a docker exec into the trusted `nextseek` process)
# --------------------------------------------------------------------------

def build_sweep_command(*, nextseek_container: str, user_id: str, api_user: str,
                         project: str, python_bin: str = "/app/.venv/bin/python") -> list[str]:
    return [
        "docker", "exec", nextseek_container, python_bin, "manage.py", "cc_sweep_staging",
        "--user-id", user_id, "--api-user", api_user, "--project", project,
    ]


def run_trusted_sweep(*, nextseek_container: str, user_id: str, api_user: str,
                       project: str, timeout: float = 60.0) -> dict[str, Any]:
    """Invoke the Task 14 trusted-sweep management command and shape the
    result into ``sweep_invocation.json``'s {command, exit_code,
    output_excerpt, timestamp} schema."""
    argv = build_sweep_command(
        nextseek_container=nextseek_container, user_id=user_id, api_user=api_user, project=project,
    )
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {
        "command": " ".join(argv),
        "exit_code": proc.returncode,
        "output_excerpt": (proc.stdout or proc.stderr or "").strip(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_post_sweep_scan_command(*, volume: str, docker_bin: str = "docker") -> list[str]:
    """A root-mounted recursive listing of the gate user's own subtree,
    mirroring ``pre_turn_seed_scan.txt``'s capture mechanism (mount the
    volume read-only into a throwaway alpine container and `find`)."""
    return [docker_bin, "run", "--rm", "-v", f"{volume}:/v:ro", "alpine", "find", "/v", "-maxdepth", "6"]


def capture_post_sweep_user_tree_scan(*, volume: str, timeout: float = 30.0) -> str:
    proc = subprocess.run(
        build_post_sweep_scan_command(volume=volume), capture_output=True, text=True, timeout=timeout,
    )
    return proc.stdout


# --------------------------------------------------------------------------
# nginx access-log window + executor env scan
# --------------------------------------------------------------------------

def capture_nginx_log_window(nginx_container: str, *, before: str, timeout: float = 30.0) -> str:
    """The bytes appended to ``docker logs <nginx_container>`` since
    ``before`` (a prior capture of the same command's output) -- the same
    before/after diff pattern ``test_cc_realstack.py`` already uses for the
    bedrock-proxy log window."""
    proc = subprocess.run(
        ["docker", "logs", nginx_container], capture_output=True, text=True, timeout=timeout,
    )
    after = (proc.stdout or "") + (proc.stderr or "")
    if before and after.startswith(before[:200]):
        return after[len(before):]
    return after


def capture_matrix_env_scan(container_name: str, *, docker_bin: str = "docker") -> str:
    """Sanitized env scan of the gate executor -- one line per ``KEY=value``
    pair, validated by the SAME no-shared-creds rules as agent_env_scan.txt
    (``check_matrix_env_scan_no_shared_creds`` reuses
    ``validate_cc_acceptance.SHARED_CRED_KEYS``/``LEAK_MARKERS``)."""
    rec = docker_inspect_one(container_name, docker_bin=docker_bin)
    if rec is None:
        return ""
    env_list = (rec.get("Config") or {}).get("Env") or []
    return "\n".join(env_list)


# --------------------------------------------------------------------------
# Seeded fixture (Step 3b): the gate harness CREATES a minimal sandbox
# fixture as the gate user via the authenticated REST API, BEFORE the matrix
# run -- one sandbox project + a small set of sample UIDs (iter-1 H-3: on a
# greenfield MBP gate there IS no pre-existing data, so reading alone makes
# the 9/9 gate unachievable). Data-dependent ops (report/generate-submission/
# api-read/graph) target this fixture.
#
# GROUNDED WRITE PATHS (both exist in-tree; the hermetic drift-pin tests in
# test_cc_matrix_gate_harness.py verify these facts against the source):
#
# * ``POST /nextseek_api/projects/`` -- ProjectViewSet.create
#   (nextseek_api/services/projects.py:172; request model
#   ProjectCreateRequest, nextseek_api/models.py:578: ``{"data": {"type":
#   "projects", "attributes": {"title": ...}}}``, relationships optional).
# * ``POST /nextseek_api/samples/`` -- SampleViewSet.create
#   (nextseek_api/services/samples.py:175; request model
#   SampleCreateRequest, nextseek_api/models.py:1390: ``{"data": {"type":
#   "samples", "attributes": {"title": ...}, "relationships":
#   {"sample_type": {"data": {"type": "sample_types", "id": ...}},
#   "projects": {"data": [...]}}}}`` -- sample_type is the one REQUIRED
#   relationship, SamplePostRelationships nextseek_api/models.py:1370).
# * ``GET /nextseek_api/projects/`` / ``GET /nextseek_api/sample_types/`` --
#   JSON:API list shapes (``{"data": [{"id", "type", "attributes":
#   {"title"}}, ...]}``) used for the idempotence check and the required
#   sample_type reference.
#
# The payload dicts are built by pure functions here (NOT by importing
# nextseek_api.models -- that module needs configured Django settings, which
# the hermetic suite deliberately runs without); the hermetic tests pin the
# builders' output to the models' declared field/type contract by reading
# the model source, per this repo's cross-file literal-lock pattern.
# --------------------------------------------------------------------------

PROJECTS_ENDPOINT = "/nextseek_api/projects/"
SAMPLES_ENDPOINT = "/nextseek_api/samples/"
SAMPLE_TYPES_ENDPOINT = "/nextseek_api/sample_types/"
DEFAULT_GATE_PROJECT_TITLE = "CC Capability Gate Sandbox"
DEFAULT_FIXTURE_SAMPLE_COUNT = 2


class FixtureCreationError(RuntimeError):
    """Seeded-fixture creation failed. NEVER caught-and-faked: the gate run
    must fail loudly (a fixture the harness could not actually create is a
    fixture the data-dependent ops cannot be proven against)."""


@dataclass
class SeededFixture:
    project: str                      # validated {pid}-{slug} CC project dirname
    uids: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    seek_project_id: str = ""
    title: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "uids": self.uids,
            "requests": self.requests,
            "seek_project_id": self.seek_project_id,
            "title": self.title,
        }


def build_project_create_payload(title: str) -> dict[str, Any]:
    """Matches ProjectCreateRequest/ProjectPostData/ProjectPostAttributes
    (nextseek_api/models.py:548-587; extra='forbid' -- emit EXACTLY the
    declared shape, nothing more): title is the only required attribute,
    relationships are Optional."""
    return {"data": {"type": "projects", "attributes": {"title": title}}}


def build_sample_create_payload(
    title: str, sample_type_id: str, project_id: str | None = None,
) -> dict[str, Any]:
    """Matches SampleCreateRequest/SamplePostData/SamplePostAttributes/
    SamplePostRelationships (nextseek_api/models.py:1359-1393;
    extra='forbid'): title required; relationships.sample_type is the one
    REQUIRED SingleReference; projects is an Optional MultipleReferences."""
    relationships: dict[str, Any] = {
        "sample_type": {"data": {"type": "sample_types", "id": str(sample_type_id)}},
    }
    if project_id:
        relationships["projects"] = {"data": [{"type": "projects", "id": str(project_id)}]}
    return {
        "data": {
            "type": "samples",
            "attributes": {"title": title},
            "relationships": relationships,
        }
    }


def find_project_id_by_title(list_body: Any, title: str) -> str | None:
    """First project id in a JSON:API list body whose attributes.title equals
    ``title`` exactly, or None. The idempotence SECONDARY path: reuse-existing
    only when a prior gate run already created the sandbox project."""
    if not isinstance(list_body, dict):
        return None
    for item in list_body.get("data") or []:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") or {}
        if isinstance(attrs, dict) and attrs.get("title") == title and item.get("id"):
            return str(item["id"])
    return None


def first_sample_type_id(list_body: Any) -> str | None:
    """The first sample_type id in a JSON:API list body, or None."""
    if not isinstance(list_body, dict):
        return None
    for item in list_body.get("data") or []:
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
    return None


def extract_created_id(body: Any) -> str | None:
    """``data.id`` of a JSON:API single-resource response (what both create
    endpoints return on 200/201), or None."""
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return None


def extract_sample_uid(body: Any) -> str | None:
    """The created sample's NExtSEEK UID when the response's attribute_map
    carries one (``UID``/``uid`` key), else the JSON:API ``data.id``."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        attrs = body["data"].get("attributes") or {}
        if isinstance(attrs, dict):
            amap = attrs.get("attribute_map") or {}
            if isinstance(amap, dict):
                for key in ("UID", "uid"):
                    if amap.get(key):
                        return str(amap[key])
    return extract_created_id(body)


def default_fixture_http(base_url: str, auth: tuple[str, str], timeout: float):
    """The real HTTP layer for ``create_seeded_fixture``: a callable
    ``(method, path, json_body=None, params=None) -> (status_code,
    parsed_body)`` over httpx with the gate user's Basic auth. Hermetic
    tests inject a fake with the same signature instead (zero live calls)."""
    import httpx  # local import: never required for the hermetic suite

    base = base_url.rstrip("/")

    def _call(method: str, path: str, json_body: dict | None = None, params: dict | None = None):
        with httpx.Client(auth=auth, timeout=timeout) as client:
            resp = client.request(method, base + path, json=json_body, params=params)
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body

    return _call


def create_seeded_fixture(
    *,
    assistant_base_url: str,
    api_user: str,
    api_pass: str,
    gate_project_title: str = DEFAULT_GATE_PROJECT_TITLE,
    sample_count: int = DEFAULT_FIXTURE_SAMPLE_COUNT,
    run_id: str = "",
    timeout: float = 30.0,
    http=None,
) -> SeededFixture:
    """CREATE the minimal seeded fixture as the gate user via the
    authenticated REST API (Step 3b, iter-1 H-3): one sandbox project +
    ``sample_count`` samples, recorded (ids + the requests used) in the
    returned SeededFixture / ``seeded_fixture.json``.

    Creation is the PRIMARY behavior -- greenfield works from zero. Reuse of
    an existing same-titled sandbox project is the SECONDARY idempotence
    path only (a prior gate run on the same host). Any failure raises
    ``FixtureCreationError`` with specifics; nothing is ever faked.

    ``http`` is the injectable HTTP layer (see ``default_fixture_http``);
    hermetic tests pass a fake, the live gate leaves it None.
    """
    from nextseek_api.cc_assistant.cc_provision import project_dirname, slugify_project

    call = http if http is not None else default_fixture_http(
        assistant_base_url, (api_user, api_pass), timeout,
    )
    requests: list[str] = []

    # 1. Idempotence check (SECONDARY path): does the sandbox project exist?
    status, body = call("GET", PROJECTS_ENDPOINT)
    requests.append(f"GET {PROJECTS_ENDPOINT} as {api_user} -> {status}")
    project_id = find_project_id_by_title(body, gate_project_title) if 200 <= status < 300 else None

    # 2. PRIMARY path: create the sandbox project.
    if project_id is None:
        payload = build_project_create_payload(gate_project_title)
        status, body = call("POST", PROJECTS_ENDPOINT, json_body=payload)
        requests.append(f"POST {PROJECTS_ENDPOINT} title={gate_project_title!r} as {api_user} -> {status}")
        if not (200 <= status < 300):
            raise FixtureCreationError(
                f"project create failed: POST {PROJECTS_ENDPOINT} -> {status} ({body!r})"
            )
        project_id = extract_created_id(body)
        if not project_id:
            raise FixtureCreationError(
                f"project create returned no data.id: POST {PROJECTS_ENDPOINT} -> {status} ({body!r})"
            )

    # 3. Resolve the REQUIRED sample_type reference (SamplePostRelationships).
    status, body = call("GET", SAMPLE_TYPES_ENDPOINT)
    requests.append(f"GET {SAMPLE_TYPES_ENDPOINT} as {api_user} -> {status}")
    sample_type_id = first_sample_type_id(body) if 200 <= status < 300 else None
    if not sample_type_id:
        raise FixtureCreationError(
            f"no sample_type available (GET {SAMPLE_TYPES_ENDPOINT} -> {status}): a SEEK "
            "instance with zero sample types cannot accept a sample create "
            "(relationships.sample_type is required) -- seed at least one sample "
            "type in the instance (environment prerequisite), do not fake the fixture"
        )

    # 4. Create the sandbox samples.
    uids: list[str] = []
    for i in range(sample_count):
        title = f"Task15 gate sample {run_id or 'gate'}-{i + 1}"
        payload = build_sample_create_payload(title, sample_type_id, project_id)
        status, body = call("POST", SAMPLES_ENDPOINT, json_body=payload)
        requests.append(
            f"POST {SAMPLES_ENDPOINT} title={title!r} sample_type={sample_type_id} "
            f"project={project_id} as {api_user} -> {status}"
        )
        if not (200 <= status < 300):
            raise FixtureCreationError(
                f"sample create failed: POST {SAMPLES_ENDPOINT} -> {status} ({body!r})"
            )
        uid = extract_sample_uid(body)
        if not uid:
            raise FixtureCreationError(
                f"sample create returned no id/UID: POST {SAMPLES_ENDPOINT} -> {status} ({body!r})"
            )
        uids.append(uid)

    # 5. The CC project dirname identity ({pid}-{slug}) for this sandbox
    #    project, built by the SAME production helpers the engine uses.
    dirname = project_dirname(str(project_id), slugify_project(gate_project_title))

    return SeededFixture(
        project=dirname, uids=uids, requests=requests,
        seek_project_id=str(project_id), title=gate_project_title,
    )


# --------------------------------------------------------------------------
# Artifact writing
# --------------------------------------------------------------------------

def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, default=str), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def images_json_cc_image(evid_dir: Path) -> str | None:
    """Read the CC image tag the SAME way the validator does -- from the
    bundle's own ``images.json`` (IMAGES_JSON_CC_IMAGE_KEY), never a separate
    hardcoded default, so the executor spawn and the validator's provenance
    check always compare the SAME recorded value."""
    p = evid_dir / "images.json"
    if not p.is_file():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj.get(IMAGES_JSON_CC_IMAGE_KEY) if isinstance(obj, dict) else None


__all__ = [
    "BIN_OPS",
    "OP_ASSISTANT_ENDPOINT",
    "PUBLISHED_PATH_OPS",
    "PER_OP_EXEC_TIMEOUT_SECS",
    "TRANSPORT_FOR_OP",
    "PROJECTS_ENDPOINT",
    "SAMPLES_ENDPOINT",
    "SAMPLE_TYPES_ENDPOINT",
    "DEFAULT_GATE_PROJECT_TITLE",
    "DEFAULT_FIXTURE_SAMPLE_COUNT",
    "ExecResult",
    "FixtureCreationError",
    "SeededFixture",
    "build_op_argv",
    "build_project_create_payload",
    "build_sample_create_payload",
    "find_project_id_by_title",
    "first_sample_type_id",
    "extract_created_id",
    "extract_sample_uid",
    "default_fixture_http",
    "gate_executor_environment",
    "build_gate_executor_run_kwargs",
    "docker_exec_op",
    "docker_inspect_one",
    "build_ps_name_filter_command",
    "resolve_container_by_name_substring",
    "docker_network_inspect",
    "make_matrix_row",
    "build_sweep_command",
    "run_trusted_sweep",
    "build_post_sweep_scan_command",
    "capture_post_sweep_user_tree_scan",
    "capture_nginx_log_window",
    "capture_matrix_env_scan",
    "create_seeded_fixture",
    "write_json",
    "write_text",
    "images_json_cc_image",
]
