"""Build and start the docker compose stack in dependency order."""
from __future__ import annotations

import time
from pathlib import Path

from bootstrap.lib import ui
from bootstrap.lib.docker_ops import DockerOpsError, compose_exec, compose_up


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


def start_seek_side(repo_root: Path, env: dict[str, str]) -> None:
    """Start SEEK, Solr, and SEEK workers."""
    compose_up(services=["solr", "seek", "seek_workers"], project_dir=repo_root, env=env)


def build_and_start_nextseek(repo_root: Path, env: dict[str, str]) -> None:
    """Build the NExtSEEK image and start nextseek + nginx."""
    compose_up(
        services=["nextseek", "nextseek_nginx"],
        project_dir=repo_root,
        env=env,
        build=True,
    )


def start_full_stack(repo_root: Path, env: dict[str, str]) -> None:
    """Convenience: run all three phases in order."""
    start_databases(repo_root, env)
    start_seek_side(repo_root, env)
    build_and_start_nextseek(repo_root, env)
