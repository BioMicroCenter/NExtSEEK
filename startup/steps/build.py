"""Build and start the docker compose stack in dependency order."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

from startup.lib import ui
from startup.lib.docker_ops import DockerOpsError, compose_build, compose_exec, compose_up
from startup.lib.rebuild_policy import APP_RUNTIME_SERVICES


def wait_for_nextseek_http(port: int, max_attempts: int = 180, interval: float = 1.0) -> None:
    """Block until the nextseek_nginx → gunicorn pipeline responds with anything
    other than 502 Bad Gateway. The container starts in seconds but gunicorn
    takes another 1–2 minutes to finish collectstatic + migrate before it
    actually binds. Until then nginx returns 502.
    """
    url = f"http://localhost:{port}/"
    for _ in range(max_attempts):
        try:
            with urllib.request.urlopen(url, timeout=5.0) as resp:
                # Anything not 5xx means gunicorn answered (incl. 200, 302, 404).
                if resp.getcode() < 500:
                    return
        except urllib.error.HTTPError as exc:
            # urlopen raises on HTTP error codes; 5xx means still booting,
            # 4xx means alive enough — treat anything < 500 as ready.
            if exc.code < 500:
                return
        except OSError:
            pass  # connection refused — nginx not up yet
        time.sleep(interval)
    raise DockerOpsError(
        f"NExtSEEK did not respond at {url} after {int(max_attempts * interval)}s"
    )


def _wait_for(
    service: str,
    check_command: list[str],
    repo_root: Path,
    env: dict[str, str],
    label: str,
    max_attempts: int = 60,
    interval: float = 1.0,
) -> None:
    """Poll a service until check_command exits cleanly. Raises after max_attempts."""
    for _ in range(max_attempts):
        try:
            compose_exec(
                service=service,
                command=check_command,
                project_dir=repo_root,
                env=env,
            )
            return
        except DockerOpsError:
            time.sleep(interval)
    raise DockerOpsError(
        f"{label} did not become ready after {int(max_attempts * interval)}s"
    )


def wait_for_mysql(
    repo_root: Path, env: dict[str, str], root_password: str = "seek_root"
) -> None:
    """Block until MySQL accepts connections inside the db container."""
    _wait_for(
        service="db",
        check_command=["mysql", "-uroot", f"-p{root_password}", "-e", "SELECT 1"],
        repo_root=repo_root,
        env=env,
        label="MySQL",
    )


def wait_for_neo4j(
    repo_root: Path, env: dict[str, str], password: str = "demopassword"
) -> None:
    """Block until Neo4j accepts Bolt connections inside the neo4j container."""
    _wait_for(
        service="neo4j",
        check_command=["cypher-shell", "-u", "neo4j", "-p", password, "RETURN 1"],
        repo_root=repo_root,
        env=env,
        label="Neo4j",
    )


def start_databases(repo_root: Path, env: dict[str, str]) -> None:
    """Start MySQL and Neo4j, then wait until both accept connections."""
    compose_up(services=["db", "neo4j"], project_dir=repo_root, env=env)
    with ui.spinner("waiting for MySQL to accept connections"):
        wait_for_mysql(repo_root, env)
    with ui.spinner("waiting for Neo4j to accept connections"):
        wait_for_neo4j(repo_root, env)


def wait_for_seek_filestore(repo_root: Path, env: dict[str, str]) -> None:
    """Block until SEEK has created its filestore subdirs (avatars/, etc).

    seek and seek_workers share the filestore volume. SEEK's entrypoint mkdir's
    these on first start; if workers race in parallel they hit 'mkdir: file
    exists' errors. We start seek first, wait for the dirs, then start workers.
    """
    _wait_for(
        service="seek",
        check_command=["sh", "-c", "test -d /seek/filestore/avatars"],
        repo_root=repo_root,
        env=env,
        label="SEEK filestore",
        max_attempts=120,
        interval=1.0,
    )


def start_seek_side(repo_root: Path, env: dict[str, str]) -> None:
    """Start Solr + SEEK, wait for the filestore to initialize, then start workers."""
    compose_up(services=["solr", "seek"], project_dir=repo_root, env=env)
    with ui.spinner("waiting for SEEK to initialize the filestore volume"):
        wait_for_seek_filestore(repo_root, env)
    compose_up(services=["seek_workers"], project_dir=repo_root, env=env)


def build_and_start_nextseek(repo_root: Path, env: dict[str, str]) -> None:
    """Build the shared app image and start every app-code runtime + nginx."""
    compose_build(services=["nextseek"], project_dir=repo_root, env=env)
    compose_up(
        services=[*APP_RUNTIME_SERVICES, "nextseek_nginx"],
        project_dir=repo_root,
        env=env,
        no_deps=True,
    )


def start_cc_stack(repo_root: Path, env: dict[str, str]) -> None:
    """Build the per-turn CC image and start the long-running CC services."""
    compose_build(services=["cc-agent"], project_dir=repo_root, env=env)
    compose_up(
        services=["bedrock-proxy", "nextseek-sidecar"],
        project_dir=repo_root,
        env=env,
        build=True,
        force_recreate=True,
    )
    compose_up(
        services=["nextseek_nginx"],
        project_dir=repo_root,
        env=env,
        force_recreate=True,
    )


def start_full_stack(repo_root: Path, env: dict[str, str]) -> None:
    """Convenience: run all stack phases in order."""
    start_databases(repo_root, env)
    start_seek_side(repo_root, env)
    build_and_start_nextseek(repo_root, env)
    start_cc_stack(repo_root, env)
