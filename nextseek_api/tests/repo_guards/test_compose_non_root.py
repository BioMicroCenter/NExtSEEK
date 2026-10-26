"""`nextseek` and `web` run as 10001 with no capabilities (W9 part 2, phase 4).

The image side (user, owned mount points) is pinned by test_dockerfile_non_root.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]
APP = ("nextseek", "web")


@pytest.mark.parametrize("name", APP)
def test_app_service_is_non_root_without_capabilities(name):
    svc = SERVICES[name]
    assert svc.get("user") == "10001:10001"
    assert svc.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in svc.get("security_opt", [])
    assert "cap_add" not in svc
    assert not svc.get("privileged")
    # (i) the deploy account's gid, by env reference only (mandatory form: no guessed fallback)
    assert svc.get("group_add") == [
        "${NEXTSEEK_HOST_GID:?is missing from .env: add it with echo NEXTSEEK_HOST_GID=`id -g` >> .env}"]


def test_no_service_adds_the_docker_or_root_group():
    # (ii) group_add never names docker, 994 or 0 (the docker group is root on the host)
    for name, svc in SERVICES.items():
        for g in svc.get("group_add", []):
            assert str(g) not in ("docker", "994", "0", "root"), (name, g)


def test_no_other_service_changed_its_user_by_accident():
    # cc-spawner keeps its own hardened block; no other service gains the app's user.
    spawner = SERVICES["cc-spawner"]
    assert spawner["cap_drop"] == ["ALL"] and "no-new-privileges:true" in spawner["security_opt"]
    assert spawner.get("user") != "10001:10001"
    for name, svc in SERVICES.items():
        if name not in APP:
            assert svc.get("user") != "10001:10001", name
