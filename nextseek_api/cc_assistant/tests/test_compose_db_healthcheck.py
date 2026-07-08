"""R3b: docker-compose must gate nextseek on db readiness, not just start-order."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _load():
    return yaml.safe_load(COMPOSE.read_text())


def test_db_service_has_healthcheck():
    services = _load()["services"]
    hc = services["db"].get("healthcheck")
    assert hc is not None, "db service must define a healthcheck"
    assert "test" in hc
    joined = " ".join(hc["test"]) if isinstance(hc["test"], list) else str(hc["test"])
    assert "mysqladmin" in joined and "ping" in joined


def test_nextseek_depends_on_db_condition_service_healthy():
    services = _load()["services"]
    dep = services["nextseek"]["depends_on"]
    assert isinstance(dep, dict), "depends_on must be the long (condition) form"
    assert dep["db"]["condition"] == "service_healthy"


def test_db_healthcheck_is_not_trivially_green():
    hc = _load()["services"]["db"]["healthcheck"]
    joined = " ".join(hc["test"]) if isinstance(hc["test"], list) else str(hc["test"])
    for banned in ("|| true", "|| exit 0", "; true", "|| :", "|| /bin/true"):
        assert banned not in joined, f"healthcheck must not be always-green ({banned!r})"


def test_db_healthcheck_is_credential_free():
    hc = _load()["services"]["db"]["healthcheck"]
    joined = " ".join(hc["test"]) if isinstance(hc["test"], list) else str(hc["test"])
    assert "mysqladmin" in joined and "ping" in joined
    assert "-h 127.0.0.1" in joined
    for banned in ("MYSQL_ROOT_PASSWORD", "--password", " -p", "-u root"):
        assert banned not in joined, f"healthcheck must be credential-free ({banned!r})"
