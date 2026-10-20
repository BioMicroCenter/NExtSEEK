"""The app image runs as a worker (`nextseek`) and a swappable `web` service.

SPEC-PLAN sections 1 and 3 (compose_swap, W9 part 1). Each assertion is a way the
gap-free swap or the spawner boundary breaks silently:

* `web` with a `container_name` cannot run twice, so `--scale web=2` fails.
* `web` without a healthcheck gives the swap nothing to wait on.
* A grace period shorter than the drain cap makes Docker kill the container while
  it is still finishing chat turns.
* The worker holding the Docker socket, or sitting on the spawner network, undoes
  the point of splitting the chat turn out of it.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
SOCK = "/var/run/docker.sock"


def _mounts(service: dict) -> list[str]:
    out = []
    for v in service.get("volumes", []):
        out.append(v if isinstance(v, str) else f"{v.get('source')}:{v.get('target')}")
    return out


def _seconds(value: str) -> int:
    m = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", value)
    assert m and value, value
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def _drain_default() -> int:
    env = SERVICES["web"]["environment"]["NEXTSEEK_DRAIN_SECONDS"]
    m = re.fullmatch(r"\$\{NEXTSEEK_DRAIN_SECONDS:-(\d+)\}", env)
    assert m, env
    return int(m.group(1))


def test_web_can_run_twice_and_reuses_the_worker_image():
    web = SERVICES["web"]
    assert "container_name" not in web, "a fixed name stops `--scale web=2`"
    assert "build" not in web
    assert web["image"] == SERVICES["nextseek"]["image"]
    assert web["command"][-1] == "web"
    assert SERVICES["nextseek"]["command"][-1] == "worker"
    assert SERVICES["nextseek"]["container_name"] == "${INSTANCE_PREFIX:-}nextseek"


def test_web_has_a_login_healthcheck_the_swap_can_wait_on():
    hc = SERVICES["web"]["healthcheck"]
    assert "http://127.0.0.1:8000/login/" in " ".join(hc["test"])
    assert hc["start_interval"] == "2s"
    # Above the entrypoint's wait for the worker's migrations (NEXTSEEK_MIGRATE_WAIT_SECONDS, 300 s).
    assert int(hc["start_period"].rstrip("s")) > 300


def test_web_grace_period_covers_the_drain_cap_plus_gunicorn():
    assert _drain_default() == 240
    grace = _seconds(SERVICES["web"]["stop_grace_period"])
    assert grace >= _drain_default() + 30


def test_only_web_and_the_spawner_mount_the_docker_socket():
    holders = {n for n, s in SERVICES.items() if any(m.startswith(SOCK + ":") for m in _mounts(s))}
    assert holders == {"web", "cc-spawner"}


def test_the_worker_has_no_socket_no_laya_and_no_spawner_network():
    worker = SERVICES["nextseek"]
    assert not [m for m in _mounts(worker) if SOCK in m]
    nets = worker.get("networks") or ["default"]
    assert "laya-net" not in nets and "dmac-spawner-net" not in nets
    assert not [m for m in _mounts(worker) if "luria" in m or m.endswith("/app/.ssh")]


def test_the_worker_holds_no_spawner_token_and_no_spawner_url():
    """The worker loads docker/nextseek.env, where install writes the spawner token. `environment:` beats
    `env_file:`, so both keys are set empty here: the worker can neither present the token nor be pointed at the
    spawner, from this part on."""
    worker = SERVICES["nextseek"]
    assert "./docker/nextseek.env" in worker["env_file"]
    assert worker["environment"]["NEXTSEEK_SPAWNER_TOKEN"] == ""
    assert worker["environment"]["NEXTSEEK_SPAWNER_URL"] == ""


def test_media_is_one_shared_volume_and_the_worker_gets_the_internal_url():
    for name in ("nextseek", "web"):
        assert "nextseek-media:/media" in _mounts(SERVICES[name]), name
        assert SERVICES[name]["environment"]["NEXTSEEK_LABS_FILE"] == "/media/context/labs_db.json"
    assert SERVICES["nextseek"]["environment"]["NEXTSEEK_INTERNAL_BASE_URL"] == "http://nextseek_nginx"
    assert "nextseek-media" in COMPOSE["volumes"]
    assert "external" not in COMPOSE["volumes"]["nextseek-media"]


def test_web_reaches_the_spawner_by_default_and_the_switch_is_unset_vs_empty():
    web = SERVICES["web"]
    assert web["environment"]["NEXTSEEK_SPAWNER_URL"] == "${NEXTSEEK_SPAWNER_URL-http://cc-spawner:8080}"
    assert set(web["networks"]) == {"default", "laya-net", "dmac-spawner-net"}
    assert web["restart"] == "always"
    assert {"db", "seek"} <= set(web["depends_on"])


def test_the_spawner_is_hardened_and_only_on_its_own_network():
    sp = SERVICES["cc-spawner"]
    assert sp["build"]["context"] == "./docker/cc-spawner"
    assert sp["image"] == "nextseek-cc-spawner:latest"
    assert "container_name" not in sp   # reached by the service name; the prefixed names are a pinned set
    assert sp["read_only"] is True
    assert sp["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in sp["security_opt"]
    assert "ports" not in sp and "expose" not in sp
    assert sp["networks"] == ["dmac-spawner-net"]
    assert sp["env_file"] == [{"path": "./docker/cc-spawner.env", "required": False}]
    assert "memory" in sp["deploy"]["resources"]["limits"]
    assert sp["healthcheck"] and "/health" in " ".join(sp["healthcheck"]["test"])
    assert sp["restart"] == "unless-stopped"
    assert set(sp["environment"]) == {
        "DMAC_CC_USERS_VOLUME", "NEXTSEEK_CC_IMAGE", "NEXTSEEK_CC_NETWORK", "SEEK_CONTAINER",
        "NEXTSEEK_CC_MAX_TURNS", "NEXTSEEK_CC_MEM_LIMIT", "NEXTSEEK_CC_NANO_CPUS",
        "NEXTSEEK_CC_PIDS_LIMIT", "NEXTSEEK_CC_FSIZE_BYTES", "NEXTSEEK_INTERNAL_BASE_URL",
        "NEO4J_URI", "NEXTSEEK_CC_AUTOMODE_GCP"}
    assert "nextseek.env" not in str(sp["env_file"]), "the spawner must not hold the app's secrets"
    assert "NEXTSEEK_SPAWNER_TOKEN" not in sp["environment"] and "NEXTSEEK_SPAWNER_URL" not in sp["environment"]
    members = [n for n, s in SERVICES.items() if "dmac-spawner-net" in (s.get("networks") or [])]
    assert sorted(members) == ["cc-spawner", "web"]
    assert COMPOSE["networks"]["dmac-spawner-net"] == {"name": "dmac-spawner-net", "internal": True}


def test_nginx_pins_through_a_generated_upstream_directory():
    nginx_conf = (REPO_ROOT / "docker" / "nginx.conf").read_text()
    assert 'set $upstream "web";' in nginx_conf
    assert "include /etc/nginx/upstream/*.conf;" in nginx_conf
    assert nginx_conf.index("include /etc/nginx/upstream") < nginx_conf.index("proxy_pass http://$upstream:8000")
    assert "./docker/nginx-upstream:/etc/nginx/upstream:ro,z" in _mounts(SERVICES["nextseek_nginx"])
    ignore = (REPO_ROOT / "docker" / "nginx-upstream" / ".gitignore").read_text().split()
    assert "*" in ignore and "!.gitignore" in ignore
