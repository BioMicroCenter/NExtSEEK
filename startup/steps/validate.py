"""Post-install health checks."""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from startup.lib.instance import load_instance
from startup.steps import seek_settings
from startup.steps.config import read_rendered_seek_public_url
from startup.lib.docker_ops import (
    compose_exec,
    compose_ps_running,
    image_exists,
    DockerOpsError,
)
from startup.lib.rebuild_policy import app_runtime_services, component_policies
from startup.lib.env import read_env
from startup.lib.layout import (
    LEGACY_PROXY_SECRET_ENV,
    PROXY_SECRET_ENV,
    legacy_proxy_secret_env,
    proxy_secret_env,
)


@dataclass
class HealthResult:
    name: str
    ok: bool
    detail: str
    warn: bool = False


def check_http(name: str, url: str, timeout: float = 5.0) -> HealthResult:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.getcode()
        return HealthResult(name=name, ok=200 <= code < 400, detail=f"{url} → {code}")
    except Exception as exc:
        return HealthResult(name=name, ok=False, detail=f"{url} → {exc}")


def run_django_check(repo_root: Path, env: dict[str, str]) -> HealthResult:
    try:
        out = compose_exec(
            service="nextseek",
            command=["uv", "run", "manage.py", "check"],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError as exc:
        return HealthResult(name="django check", ok=False, detail=str(exc))
    return HealthResult(
        name="django check",
        ok=True,
        detail=out.strip().splitlines()[-1] if out.strip() else "exit 0",
    )


def check_prod_overlay_guard(repo_root: Path) -> HealthResult:
    """Flag a stale PROD overlay that the internal transport URL can shadow.

    Review follow-up FU1 (2026-07-07): ChatConfig prefers
    NEXTSEEK_INTERNAL_BASE_URL over NEXTSEEK_BASE_URL. The current
    local_settings template guards its PROD overlay (env pop + config_map),
    but a pre-guard hand-maintained dmac/local_settings.py has neither — on
    such a box, setting NEXTSEEK_INTERNAL_BASE_URL in docker/nextseek.env
    makes the admin PROD toggle silently self-call the DEV backend. Unsafe
    only when the env sets the var AND the overlay carries no guard at all.
    """
    name = "prod overlay guard"
    env_file = repo_root / "docker" / "nextseek.env"
    settings_file = repo_root / "dmac" / "local_settings.py"
    if not env_file.exists() or not settings_file.exists():
        return HealthResult(
            name=name, ok=True, detail="env/local_settings not rendered — nothing to check"
        )

    def _internal_var_set(text: str) -> bool:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() == "NEXTSEEK_INTERNAL_BASE_URL":
                if value.strip().strip("\"'"):
                    return True
        return False

    if not _internal_var_set(env_file.read_text()):
        return HealthResult(
            name=name, ok=True, detail="NEXTSEEK_INTERNAL_BASE_URL not set in nextseek.env"
        )
    settings_text = settings_file.read_text()
    if "_PROD_OVERRIDES" not in settings_text:
        return HealthResult(
            name=name, ok=True, detail="no PROD overlay in local_settings.py"
        )
    # Judge only the overlay block itself (everything from _PROD_OVERRIDES
    # on): a DEV-side ChatConfig(config_map=...) above it — or a comment
    # mentioning config_map — is not a guard. Accept either quote style for
    # a hand-ported pop.
    overlay_text = settings_text[settings_text.index("_PROD_OVERRIDES") :]
    guarded = bool(
        re.search(
            r"os\.environ\.pop\(\s*['\"]NEXTSEEK_INTERNAL_BASE_URL['\"]",
            overlay_text,
        )
        or re.search(r"ChatConfig\(\s*config_map\s*=", overlay_text)
    )
    if guarded:
        return HealthResult(
            name=name, ok=True, detail="PROD overlay carries the internal-URL guard"
        )
    return HealthResult(
        name=name,
        ok=False,
        detail=(
            "NEXTSEEK_INTERNAL_BASE_URL is set but dmac/local_settings.py "
            "predates the internal-URL guard (no env pop, no config_map): the "
            "PROD ChatConfig overlay would self-call the dev backend — "
            "regenerate the file from startup/templates/local_settings.py."
            "template (or port its overlay block) before enabling "
            "_PROD_OVERRIDES"
        ),
    )


def check_proxy_token(repo_root: Path) -> HealthResult:
    secret = proxy_secret_env(repo_root)
    token = read_env(secret).get("AWS_BEARER_TOKEN_BEDROCK", "")
    if token:
        return HealthResult(name="bedrock proxy token", ok=True, detail="token present")
    if legacy_proxy_secret_env(repo_root).exists():
        # The one empty case with a one-line fix: the box kept its token at the
        # pre-NessieAI path, which compose no longer reads.
        fill = (
            f"a pre-move token file is still at {LEGACY_PROXY_SECRET_ENV.as_posix()}: "
            f"move it to {PROXY_SECRET_ENV.parent.as_posix()}/ (mv keeps its mode)"
        )
    else:
        fill = f"Fill {PROXY_SECRET_ENV.as_posix()}"
    return HealthResult(
        name="bedrock proxy token",
        ok=True,
        warn=True,
        detail=(
            f"EMPTY: CC model calls are disabled. {fill}, then "
            "`docker compose up -d --force-recreate bedrock-proxy`"
        ),
    )


# A rendered docker/nextseek.env value that still points into a Nessie unit's
# pre-move location (/app/<unit> for chat_nextseek, dmac_assistant and
# nessie_tests, all now under /app/NessieAI/). The file is gitignored, rebuild never
# re-renders it, and install rewrites it whole (rotating DJANGO_SECRET_KEY), so
# nothing in git ever fixes these lines on a box. A superset of
# ^[A-Z_]+=.*?/app/(chat_nextseek|dmac_assistant|nessie_tests)/ : digits are
# allowed in the key, and the unit name may also end at a quote, a colon,
# whitespace or the end of the line.
STALE_NESSIE_ENV_RE = re.compile(
    r"^([A-Z_][A-Z0-9_]*)=.*?/app/(chat_nextseek|dmac_assistant|nessie_tests)"
    r"(?=/|[\"':\s]|$)"
)

# What to do with each known stale key. The two DMAC_* overrides are deleted,
# not repointed: the package default already resolves inside the editable
# install, and a stale value is silent (every CC turn loses its model id and
# 403s at the proxy; every turn's routing drops to the keyword heuristic).
_STALE_ENV_FIXES = {
    "CATALOG_FILE": 'set it to "/app/NessieAI/chat_nextseek/agent_model_catalog.json"',
    "DMAC_ROUTE_CAPABILITIES_FILE": "delete the line (the package default is correct)",
    "DMAC_ROUTER_MODEL_CLASS_MAP_FILE": "delete the line (the package default is correct)",
}
_STALE_UNIT_HOMES = {
    "chat_nextseek": "/app/NessieAI/chat_nextseek/",
    "dmac_assistant": "/app/NessieAI/dmac_assistant/",
    "nessie_tests": "/app/NessieAI/tests/nessie_tests/",
}


def find_stale_nessie_env_lines(env_path: Path) -> list[tuple[int, str, str]]:
    """``(line number, key, unit)`` for each value naming a pre-move Nessie path.

    Keys and line numbers only: the same file carries the Django secret and the
    LLM keys, and this output goes to a terminal.
    """
    if not env_path.exists():
        return []
    hits: list[tuple[int, str, str]] = []
    for number, line in enumerate(env_path.read_text().splitlines(), start=1):
        match = STALE_NESSIE_ENV_RE.match(line)
        if match:
            hits.append((number, match.group(1), match.group(2)))
    return hits


def check_stale_nessie_env(repo_root: Path) -> HealthResult:
    """Fail on any docker/nextseek.env value that names a pre-NessieAI path.

    ``rebuild`` refuses on this before it builds anything; doctor and install's
    final checks report it.
    """
    name = "nextseek.env paths"
    env_file = repo_root / "docker" / "nextseek.env"
    if not env_file.exists():
        return HealthResult(
            name=name, ok=True, detail="docker/nextseek.env not rendered, nothing to check"
        )
    stale = find_stale_nessie_env_lines(env_file)
    if not stale:
        return HealthResult(
            name=name, ok=True, detail="no value points at a pre-NessieAI path"
        )
    fixes = "; ".join(
        f"line {number} {key}: "
        + _STALE_ENV_FIXES.get(
            key, f"repoint it under {_STALE_UNIT_HOMES[unit]} (was /app/{unit}/)"
        )
        for number, key, unit in stale
    )
    return HealthResult(
        name=name,
        ok=False,
        detail=(
            "docker/nextseek.env still names the pre-NessieAI layout, which a "
            "rebuild would load into nextseek. Edit it by hand (rebuild never "
            "re-renders it, and install rotates DJANGO_SECRET_KEY): "
            f"{fixes}. A rebuild then recreates nextseek, which re-reads the file"
        ),
    )


def check_cc_services(repo_root: Path, env: dict[str, str]) -> HealthResult:
    wanted = ["bedrock-proxy", "nextseek-sidecar"]
    try:
        running = compose_ps_running(wanted, repo_root, env)
    except DockerOpsError as exc:
        return HealthResult(name="cc services", ok=False, detail=str(exc))
    missing = [s for s in wanted if s not in running]
    if missing:
        return HealthResult(
            name="cc services", ok=False, detail=f"not running: {', '.join(missing)}"
        )
    return HealthResult(
        name="cc services", ok=True, detail="bedrock-proxy + nextseek-sidecar running"
    )


#: The container that publishes the instance's port. Every user request and every
#: smoke-suite request arrives through it.
FRONT_DOOR_SERVICE = "nextseek_nginx"


def check_app_runtimes(repo_root: Path, env: dict[str, str]) -> HealthResult:
    """Whether the app and the nginx in front of it both have a running container.

    Running, not healthy: straight after a rebuild the app is still booting and
    its healthcheck says so, which is what the smoke suite's readiness floor
    waits out. A container that is not running at all is a different condition
    that no amount of waiting fixes -- a stopped nginx on 2026-09-10 cost the
    suite its whole five-minute floor before a probe said "connection refused".
    """
    name = "app + front door"
    wanted = [*app_runtime_services(), FRONT_DOOR_SERVICE]
    try:
        running = compose_ps_running(wanted, repo_root, env)
    except DockerOpsError as exc:
        return HealthResult(name=name, ok=False, detail=str(exc))
    missing = [s for s in wanted if s not in running]
    if missing:
        return HealthResult(
            name=name,
            ok=False,
            detail=(f"not running: {', '.join(missing)} -- start it with: "
                    f"docker compose up -d --no-deps {' '.join(missing)}"),
        )
    return HealthResult(name=name, ok=True, detail=f"{' + '.join(wanted)} running")


@dataclass(frozen=True)
class StackHealth:
    """Step 1 of a CI run: what is up before the suite is asked anything.

    ``blocking`` holds the checks without which every smoke test fails the same
    way, so the suite is not started. ``advisory`` holds the ones the suite
    cannot see -- the CC image and services are never requested by it -- which
    are reported, recorded, and make a rebuild exit non-zero, but do not stop
    the run, because its result still says something true about the deploy.
    """
    blocking: tuple[HealthResult, ...]
    advisory: tuple[HealthResult, ...]

    @property
    def results(self) -> tuple[HealthResult, ...]:
        return self.blocking + self.advisory

    @property
    def testable(self) -> bool:
        return all(r.ok for r in self.blocking)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


def stack_health(
    repo_root: Path, env: dict[str, str], compose_project_name: str
) -> StackHealth:
    return StackHealth(
        blocking=(check_app_runtimes(repo_root, env),),
        advisory=(
            check_first_party_images(compose_project_name),
            check_cc_services(repo_root, env),
        ),
    )


def nessie_prerequisites(
    repo_root: Path, env: dict[str, str], compose_project_name: str
) -> tuple[HealthResult, ...]:
    """What the Nessie lane needs before it can pass: a CC turn reaches Bedrock
    only through the proxy, with its token, from a runnable cc-agent image.

    The proxy token is advisory in stack health; for the Nessie lane an empty one
    is a failure (spec decision 6), so its warning is turned into a failure here.
    The token's value never leaves check_proxy_token, which reports presence only.
    """
    token = check_proxy_token(repo_root)
    if token.warn:
        token = HealthResult(name=token.name, ok=False, detail=token.detail)
    return (
        token,
        check_first_party_images(compose_project_name),
        check_cc_services(repo_root, env),
        check_cc_runner(repo_root, env),
    )


def check_first_party_images(compose_project_name: str = "nextseek") -> HealthResult:
    """Whether every image this box builds for itself is actually here.

    Nothing else reports this. ``./startup.sh rebuild`` with no ``--component``
    builds only the app image (``startup/lib/rebuild_policy.py`` -- the ``app``
    policy's build set is ``("nextseek",)``), the smoke suite never requests the
    Container-CC routes (``ci/routes.py`` declares both with ``path=None``), and
    ``cc-agent`` has no container for ``check_cc_services`` or a compose
    healthcheck to watch, by design (``docker-compose.yml``: ``command:
    ["true"]``, ``network_mode: none``). A pruned ``dmac-assistant:poc``
    therefore took Container-CC down on fairdata-dev underneath a fully green
    deploy, and said so only when a user sent a chat turn.
    """
    name = "first-party images"
    # image -> the --component that builds it, so the remediation is pasteable.
    # custom-stack is skipped: it is the union of the other four, not a fifth image.
    owner = {
        image.local_image: policy.name
        for policy in component_policies(compose_project_name).values()
        if policy.name != "custom-stack"
        for image in policy.images
    }
    try:
        missing = [image for image in owner if not image_exists(image)]
    except DockerOpsError as exc:
        # One unreachable daemon, reported once. Probing on regardless would
        # report all four as absent and send the operator rebuilding images
        # that are really there.
        return HealthResult(name=name, ok=False, detail=str(exc))
    if missing:
        fixes = "; ".join(
            f"./startup.sh rebuild --component {owner[image]}" for image in missing
        )
        return HealthResult(
            name=name,
            ok=False,
            detail=f"ABSENT: {', '.join(missing)} -- build with: {fixes}",
        )
    return HealthResult(
        name=name, ok=True, detail=f"all {len(owner)} present ({', '.join(owner)})"
    )


def check_cc_runner(repo_root: Path, env: dict[str, str]) -> HealthResult:
    """DEPLOYMENT.md section 6 step 6, run for the operator rather than by them.

    Host-side image presence is not the same claim. This one runs inside the app
    container, over the docker socket it actually spawns CC turns through, and so
    covers all three legs of ``cc_engine.cc_runner_available()``: the daemon is
    reachable from in there, the image is visible to it, and ``dmac-cc-net``
    exists. It is the exact command the runbook asks for by hand.
    """
    name = "CC runner"
    try:
        out = compose_exec(
            service="nextseek",
            # The app image carries no bare `python` on PATH; `uv run --no-sync`
            # executes in /app/.venv without modifying it (DEPLOYMENT.md 6.6).
            command=[
                "uv", "run", "--no-sync", "python", "-c",
                "from NessieAI.cc import cc_engine; "
                "print(cc_engine.cc_runner_available())",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError as exc:
        return HealthResult(name=name, ok=False, detail=str(exc))
    reported = out.strip().splitlines()[-1] if out.strip() else ""
    if reported.startswith("(True,"):
        return HealthResult(
            name=name,
            ok=True,
            detail=f"{reported} -- image, docker socket and dmac-cc-net all "
                   "reachable from the app container",
        )
    return HealthResult(
        name=name,
        ok=False,
        detail=reported or "cc_runner_available() printed nothing",
    )


# Drift after every app rebuild (the sync design, CI-4). The app container's own
# manage.py answers it: exit 0 no drift, 1 drift, 2 a refusal carrying its reason,
# 3 it could not complete.
GRAPH_DRIFT_SERVICE = "nextseek"
GRAPH_DRIFT_COMMAND = (
    # The app image carries no bare `python` on PATH; `uv run --no-sync` executes
    # in /app/.venv without modifying it, as the CC runner check does above.
    "uv", "run", "--no-sync", "python", "manage.py", "graph_sync", "--drift", "--json",
)
# The check reads every sample in MySQL and every Sample node in the graph, about
# a million of each on the merged snapshot. The ceiling is generous on purpose: it
# exists only so that a wedged read cannot hold a deploy open for ever.
GRAPH_DRIFT_TIMEOUT_S = 1800
# Failing check names to put on one terminal line. The run record keeps them all.
GRAPH_DRIFT_NAMES_SHOWN = 6


def _stream_text(stream) -> str:
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return stream or ""


def _last_line(text: str) -> str:
    """The last non-empty line, clipped. Progress and tracebacks end with the reason."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def _graph_drift_payload(stdout: str) -> dict:
    """The result object ``--json`` printed, or an empty dict.

    Tolerant of a line that is not JSON: an older image, or a library that writes
    to stdout, must cost the report its detail rather than its verdict.
    """
    for text in (stdout, stdout[stdout.find("{"):stdout.rfind("}") + 1]):
        if not text:
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _graph_drift_result(name: str, returncode: int, stdout: str, stderr: str) -> HealthResult:
    """Read the drift command's exit status and its JSON into one health line."""
    payload = _graph_drift_payload(stdout)
    checks = payload.get("checks") or []
    if returncode == 0:
        detail = f"no drift: {len(checks)} checks passed" if checks else "no drift"
        counts = (payload.get("stats") or {}).get("detection") or {}
        if "mysql_samples" in counts and "graph_samples" in counts:
            detail += (f"; {counts['mysql_samples']} samples in MySQL, "
                       f"{counts['graph_samples']} in the graph")
        return HealthResult(name=name, ok=True, detail=detail)
    if returncode == 2:
        # A refusal, which is not a fault of this deploy: until the operator has
        # run the first `graph_sync --full`, every box is on the older schema and
        # there is nothing to compare. Reported, never red.
        reason = (payload.get("reason") or _last_line(stderr)
                  or "the command refused to compare this graph")
        return HealthResult(name=name, ok=True, warn=True, detail=f"skipped: {reason}")
    if returncode == 1:
        failed = [c.get("name", "?") for c in checks if not c.get("pass")]
        if failed:
            shown = ", ".join(failed[:GRAPH_DRIFT_NAMES_SHOWN])
            if len(failed) > GRAPH_DRIFT_NAMES_SHOWN:
                shown += f" and {len(failed) - GRAPH_DRIFT_NAMES_SHOWN} more"
            return HealthResult(
                name=name, ok=False,
                detail=f"DRIFT: {len(failed)} of {len(checks)} checks failed: {shown}",
            )
        return HealthResult(
            name=name, ok=False,
            detail=f"DRIFT (exit 1): {_last_line(stderr) or _last_line(stdout)}",
        )
    return HealthResult(
        name=name, ok=False,
        detail=(f"graph_sync --drift could not complete (exit {returncode}): "
                f"{_last_line(stderr) or _last_line(stdout) or 'no output'}"),
    )


def check_graph_drift(repo_root: Path, env: dict[str, str]) -> HealthResult:
    """Whether the graph the site searches still equals MySQL (the design, CI-4).

    Deliberately not through ``compose_exec``: that raises ``DockerOpsError`` on a
    non-zero exit and keeps only its message, so "the graph has drifted" would
    arrive as an exception with the JSON naming the drifted checks thrown away.
    Here the exit status is the answer and stdout is the detail.

    Advisory by construction. Drift is a failure, which ``rebuild`` exits on at
    the end, after the smoke suite has had its say; a refusal is a pass with a
    warning. Nothing here writes to the graph: ``--drift`` only reads.
    """
    name = "graph drift"
    if not (repo_root / "docker-compose.yml").is_file():
        # No compose project in this tree, so there is no service to exec into
        # and nothing to ask. Said out loud rather than reported as a failure.
        return HealthResult(
            name=name, ok=True, warn=True,
            detail=("skipped: this tree has no docker-compose.yml, so there is no "
                    f"{GRAPH_DRIFT_SERVICE} container to ask"),
        )
    cmd = ["docker", "compose", "exec", "-T", GRAPH_DRIFT_SERVICE, *GRAPH_DRIFT_COMMAND]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env={**os.environ, **env},
            # Empty stdin, never the caller's. `exec -T` reads whatever it is
            # given, and a rebuild is routinely run from a pipe or a hook, where
            # it would swallow the rest of the script that started it.
            input=b"",
            capture_output=True,
            timeout=GRAPH_DRIFT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return HealthResult(
            name=name, ok=False,
            detail=(f"timed out after {GRAPH_DRIFT_TIMEOUT_S}s; run `docker compose exec "
                    f"{GRAPH_DRIFT_SERVICE} {' '.join(GRAPH_DRIFT_COMMAND)}` by hand"),
        )
    except OSError as exc:
        return HealthResult(name=name, ok=False, detail=f"cannot run docker: {exc}")
    return _graph_drift_result(name, result.returncode,
                               _stream_text(result.stdout), _stream_text(result.stderr))


def check_seek_url_consistency(
    repo_root: Path, state, env: dict[str, str]
) -> HealthResult:
    """Flag drift between the two SEEK-URL layers.

    Layer A -- docker/nextseek.env SEEK_PUBLIC_URL -- is how NExtSEEK builds
    browser-facing links TO SEEK. Layer B -- SEEK's DB-backed site_base_host --
    is how SEEK identifies ITSELF (its "SEEK ID", JSON-LD @id, sitemap). install()
    renders both from one stored per-instance value, but they can still diverge
    out of band: an admin changes SEEK's setting in its UI, or someone hand-edits
    the env. That divergence is exactly what produced the original bug -- correct
    links next to localhost identifiers -- so surface it here rather than let it
    be found in a browser.
    """
    name = "SEEK public URL"
    configured = getattr(state, "seek_public_url", "") or None
    rendered = read_rendered_seek_public_url(repo_root)

    try:
        in_seek = seek_settings.read_site_base_host(repo_root, env)
    except Exception as exc:  # DB down / stack not up: doctor still runs
        return HealthResult(
            name=name,
            ok=True,
            warn=True,
            detail=(
                f"could not read SEEK's site_base_host ({exc.__class__.__name__}); "
                f"configured={configured!r}, rendered={rendered!r}. Is the stack up?"
            ),
        )

    if in_seek is None:
        return HealthResult(
            name=name,
            ok=False,
            detail=(
                f"SEEK's site_base_host is not set -- SEEK is publishing identifiers on its "
                f"default http://localhost:3000, while NExtSEEK links to {rendered!r}. "
                "Re-run `./startup.sh install` to apply it."
            ),
        )

    values = {v for v in (configured, rendered, in_seek) if v}
    if len(values) > 1:
        return HealthResult(
            name=name,
            ok=False,
            detail=(
                f"drift: instance={configured!r}, docker/nextseek.env={rendered!r}, "
                f"SEEK site_base_host={in_seek!r}. NExtSEEK's links and SEEK's own "
                "identifiers disagree. Reconcile with "
                "`./startup.sh install --seek-public-url <url>` (SEEK's row is never "
                "overwritten by tooling -- change it in SEEK's admin UI if that is the wrong one)."
            ),
        )

    return HealthResult(name=name, ok=True, detail=f"{in_seek} (env, instance and SEEK agree)")


def run_all_health_checks(
    ports: dict[str, int], repo_root: Path, env: dict[str, str]
) -> list[HealthResult]:
    results = [
        check_http("SEEK", f"http://localhost:{ports.get('seek', 3000)}"),
        check_http("NExtSEEK", f"http://localhost:{ports.get('nextseek', 8000)}"),
        check_http("Neo4j", f"http://localhost:{ports.get('neo4j_http', 7474)}"),
        run_django_check(repo_root, env),
        check_prod_overlay_guard(repo_root),
        check_stale_nessie_env(repo_root),
        check_seek_url_consistency(repo_root, load_instance(repo_root), env),
        check_proxy_token(repo_root),
        check_cc_services(repo_root, env),
        check_first_party_images(env.get("COMPOSE_PROJECT_NAME", "nextseek")),
        check_cc_runner(repo_root, env),
    ]
    return results


def run_app_health_checks(
    ports: dict[str, int], repo_root: Path, env: dict[str, str]
) -> list[HealthResult]:
    """Post-deploy checks for an app-only, disposable deployment cohort.

    This deliberately omits SEEK, Neo4j, and SEEK's DB-backed public-URL check:
    those services are neither rebuilt nor duplicated by an app-only deploy.
    It retains the candidate HTTP/Django checks and the OI-3 peer checks.
    """
    return [
        check_http("NExtSEEK", f"http://localhost:{ports.get('nextseek', 8000)}"),
        run_django_check(repo_root, env),
        check_prod_overlay_guard(repo_root),
        check_stale_nessie_env(repo_root),
        check_proxy_token(repo_root),
        check_cc_services(repo_root, env),
        # An app-only deploy is precisely the cohort that never rebuilds
        # cc-agent, so it is the one that most needs to be told the image is gone.
        check_first_party_images(env.get("COMPOSE_PROJECT_NAME", "nextseek")),
        check_cc_runner(repo_root, env),
    ]
