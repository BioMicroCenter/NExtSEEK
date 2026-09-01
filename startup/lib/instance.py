"""Per-install instance state (startup/.instance.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class InstanceState:
    name: str
    prefix: str
    ports: dict[str, int]
    compose_project_name: str
    created: str
    # Browser-reachable SEEK base URL for this instance (dev/prod front SEEK on a
    # real hostname; a laptop reaches it on the published port). Single source of
    # truth: both docker/nextseek.env's SEEK_PUBLIC_URL and SEEK's own DB-backed
    # site_base_host are applied from this, so the two cannot drift apart.
    # Defaulted so .instance.json files written before this field still load.
    seek_public_url: str = ""
    # Which CI profile this box permits. ABSENT MEANS "prod": a machine nobody has
    # configured gets the most restrictive profile, never the least. Defaulted so
    # .instance.json files written before this field still load.
    ci_profile: str = ""

    def compose_env(self) -> dict[str, str]:
        """Return env vars to pass to docker compose for this instance."""
        env = {
            "INSTANCE_PREFIX": self.prefix,
            "COMPOSE_PROJECT_NAME": self.compose_project_name,
        }
        port_env_map = {
            "nextseek": "NEXTSEEK_PORT",
            "seek": "SEEK_PORT",
            "neo4j_http": "NEO4J_HTTP_PORT",
            "neo4j_bolt": "NEO4J_BOLT_PORT",
            "db": "DB_PORT",
        }
        for service_key, env_key in port_env_map.items():
            if service_key in self.ports:
                env[env_key] = str(self.ports[service_key])
        return env


def resolve_instance_name(repo_root: Path, explicit: str | None) -> str:
    """If explicit is given, use it. Otherwise use the repo dir's basename."""
    if explicit:
        return explicit
    return repo_root.name


def _instance_path(repo_root: Path) -> Path:
    return repo_root / "startup" / ".instance.json"


def save_instance(repo_root: Path, state: InstanceState) -> None:
    path = _instance_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n")


def load_instance(repo_root: Path) -> InstanceState | None:
    path = _instance_path(repo_root)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return InstanceState(**data)
