"""Seed loader for MySQL + Neo4j gzipped dumps."""
from __future__ import annotations

import gzip
import subprocess
from pathlib import Path

from bootstrap.lib.docker_ops import compose_exec, DockerOpsError

SEED_FILES: dict[str, str] = {
    "dmac": "bootstrap/seed/dmac.sql.gz",
    "seek_production": "bootstrap/seed/seek_production.sql.gz",
    "neo4j": "bootstrap/seed/neo4j.cypher.gz",
}


def seed_files_present(repo_root: Path) -> list[str]:
    """Return a list of basenames missing from bootstrap/seed/. Empty list = all present."""
    missing: list[str] = []
    for key, rel_path in SEED_FILES.items():
        full = repo_root / rel_path
        if not full.exists():
            missing.append(full.name)
    return missing


def mysql_db_is_populated(database: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if the named MySQL database already has tables."""
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql",
                "-uroot",
                f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
                "-N",
                "-e",
                f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{database}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    try:
        return int(out.strip().splitlines()[-1]) > 0
    except (ValueError, IndexError):
        return False


def neo4j_is_populated(neo4j_password: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if Neo4j has any nodes."""
    try:
        out = compose_exec(
            service="neo4j",
            command=[
                "cypher-shell",
                "-u",
                "neo4j",
                "-p",
                neo4j_password,
                "--format",
                "plain",
                "MATCH (n) RETURN count(n);",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    for line in out.strip().splitlines():
        token = line.strip()
        if token.isdigit():
            return int(token) > 0
    return False


def load_mysql_dump(
    gz_path: Path, database: str, repo_root: Path, env: dict[str, str]
) -> None:
    """Stream gz_path through gunzip | mysql into the named DB."""
    decompressed = gzip.decompress(gz_path.read_bytes())
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "mysql",
            "-uroot",
            f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
            database,
        ],
        cwd=str(repo_root),
        env={**env, **{"PATH": env.get("PATH", "/usr/bin:/bin")}} if env else None,
        input=decompressed,
        check=True,
    )


def load_neo4j_dump(
    gz_path: Path, neo4j_password: str, repo_root: Path, env: dict[str, str]
) -> None:
    """Stream gz_path through gunzip | cypher-shell into Neo4j."""
    decompressed = gzip.decompress(gz_path.read_bytes())
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "neo4j",
            "cypher-shell",
            "-u",
            "neo4j",
            "-p",
            neo4j_password,
        ],
        cwd=str(repo_root),
        env={**env, **{"PATH": env.get("PATH", "/usr/bin:/bin")}} if env else None,
        input=decompressed,
        check=True,
    )
