"""The app container carries a memory cap, and may not swap past it.

On 2026-09-11, 09-12 and 09-14 one gunicorn worker serving a broad search grew
to 6-11 GB with no limit on the `nextseek` service, filled the host's RAM and
swap, and took sshd and every other container on fairdata-dev down with it. A
cap on this container makes the kernel kill the runaway process inside it
instead; gunicorn respawns the worker, and `restart: always` covers the rest.

`memswap_limit` equal to the cap matters as much as the cap: without it Docker
lets the container swap as much again, and swap thrash is what locked the box.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
CAP = "${NEXTSEEK_MEMORY:-16G}"
SEEK_CAP = "${SEEK_MEMORY:-4G}"
NEO4J_CAP = "${NEO4J_MEMORY:-6G}"
# Every remaining long-lived service. On 2026-09-16 the host itself ran out with
# 29.5 GiB resident across 227 tasks: no single container was over its own cap,
# but four services had no cap at all, so nothing bounded the sum.
OTHER_CAPS = {
    "seek_workers": "${SEEK_WORKERS_MEMORY:-3G}",
    "solr": "${SOLR_MEMORY:-2G}",
    "db": "${SEEK_DB_MEMORY:-2G}",
    "nextseek-sidecar": "${SIDECAR_MEMORY:-1G}",
}
LAYA_CAP = "${LAYA_MEMORY:-3500M}"


def _service(name):
    return yaml.safe_load(COMPOSE.read_text())["services"][name]


def _nextseek():
    return _service("nextseek")


def test_nextseek_has_a_memory_cap_an_operator_can_tune():
    limits = _nextseek().get("deploy", {}).get("resources", {}).get("limits", {})
    assert limits.get("memory") == CAP


def test_nextseek_cannot_swap_past_its_cap():
    assert _nextseek().get("memswap_limit") == CAP


def test_seek_has_a_memory_cap_an_operator_can_tune():
    """SEEK's puma workers never give memory back: one reached 10 GiB on the
    operator's workstation on 2026-09-16 and left the host with nothing free."""
    limits = _service("seek").get("deploy", {}).get("resources", {}).get("limits", {})
    assert limits.get("memory") == SEEK_CAP


def test_seek_cannot_swap_past_its_cap():
    assert _service("seek").get("memswap_limit") == SEEK_CAP


def test_neo4j_has_a_memory_cap_an_operator_can_tune():
    """Unbounded, one transaction can grow into the whole host: the first sync at
    schema 1.2 needed more than 512 MiB for a single 5,000-sample write."""
    limits = _service("neo4j").get("deploy", {}).get("resources", {}).get("limits", {})
    assert limits.get("memory") == NEO4J_CAP


def test_neo4j_cannot_swap_past_its_cap():
    assert _service("neo4j").get("memswap_limit") == NEO4J_CAP


def test_neo4j_bounds_one_transaction_and_sizes_its_heap():
    """The heap must be set explicitly, or the JVM sizes it from host RAM and the
    kernel kills the database instead of the query that outgrew the cap."""
    env = _service("neo4j")["environment"]
    assert env.get("NEO4J_db_memory_transaction_max") == "${NEO4J_TRANSACTION_MAX:-1g}"
    assert env.get("NEO4J_server_memory_heap_max__size") == "${NEO4J_HEAP:-2g}"
    assert env.get("NEO4J_server_memory_pagecache_size") == "${NEO4J_PAGECACHE:-2g}"


def test_every_long_lived_service_has_a_cap_and_cannot_swap_past_it():
    """A cap on some services only moves the kill: the 2026-09-16 host OOM had
    `seek` capped and `seek_workers` not, and the two together held 11.2 GiB."""
    for name, cap in OTHER_CAPS.items():
        service = _service(name)
        limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory") == cap, f"{name} has no memory cap"
        assert service.get("memswap_limit") == cap, f"{name} may swap past its cap"


def _compose():
    return yaml.safe_load(COMPOSE.read_text())


def test_laya_router_is_capped_and_cannot_swap_past_its_cap():
    """Sidecar for the laya router (JevLevROUTING): 2.8 GB measured peak, so 3500M."""
    service = _service("laya-router")
    limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
    assert limits.get("memory") == LAYA_CAP
    assert service.get("memswap_limit") == LAYA_CAP
    assert limits.get("cpus") == "${LAYA_CPUS:-4}"
    assert service["environment"]["LAYA_THREADS"] == "4"


def test_laya_router_is_off_by_default_and_has_no_published_port():
    service = _service("laya-router")
    assert service.get("profiles") == ["laya"]
    assert "ports" not in service
    assert "expose" not in service or service["expose"] == ["8080"]
    assert "laya-router" not in _nextseek()["depends_on"]


def test_laya_router_sits_on_the_internal_laya_net_only():
    """No egress: the user's text must not leave the box, and the per-turn CC
    agents (dmac-cc-net) must not reach the sidecar."""
    doc = _compose()
    assert doc["networks"]["laya-net"]["internal"] is True
    assert doc["services"]["laya-router"]["networks"] == ["laya-net"]
    assert doc["services"]["nextseek"]["networks"] == ["default", "laya-net"]
    for name, svc in doc["services"].items():
        if name not in ("laya-router", "nextseek"):
            assert "laya-net" not in (svc.get("networks") or []), name


def test_laya_router_secrets_and_checkpoint():
    """Own env file (only LAYA_API_KEY), never nextseek.env; checkpoint read-only, offline."""
    service = _service("laya-router")
    assert service["env_file"] == ["./docker/laya.env"] or service["env_file"] == "./docker/laya.env"
    assert "nextseek.env" not in str(service)
    assert service["environment"]["HF_HUB_OFFLINE"] == "1"
    mounts = [v for v in service["volumes"] if isinstance(v, str)]
    assert any(m.endswith(":/models:ro") or m.endswith(":/models:ro,z") for m in mounts)
    assert service["restart"] == "unless-stopped"
    assert "healthcheck" in service
