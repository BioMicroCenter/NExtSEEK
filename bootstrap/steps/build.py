"""Build and start the docker compose stack in dependency order."""
from __future__ import annotations

from pathlib import Path

from bootstrap.lib.docker_ops import compose_up


def start_databases(repo_root: Path, env: dict[str, str]) -> None:
    """Start MySQL and Neo4j only. Bootstrap waits for them before seeding."""
    compose_up(services=["db", "neo4j"], project_dir=repo_root, env=env)


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
