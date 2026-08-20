"""Contracts binding Compose's local images to startup rebuild policy."""
from pathlib import Path

import pytest
import yaml

from startup.lib.rebuild_policy import (
    ATTRIBUTE_RUNTIME_SERVICES,
    BASE_APP_RUNTIME_SERVICES,
    app_runtime_services,
    component_policies,
    registry_images,
    resolve_component,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_every_compose_build_target_is_owned_by_custom_stack() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    build_targets = {
        name for name, service in compose["services"].items() if "build" in service
    }
    policy = component_policies("nextseek")["custom-stack"]
    assert set(policy.build_services) == build_targets


def test_attribute_runtimes_share_app_image_and_are_not_independent_builds() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    app_image = compose["services"]["nextseek"]["image"]
    assert ATTRIBUTE_RUNTIME_SERVICES, 'the attribute runtimes must stay enumerated'
    for service_name in ATTRIBUTE_RUNTIME_SERVICES:
        service = compose["services"][service_name]
        assert service["image"] == app_image
        assert "build" not in service


def test_component_aliases_and_invalid_name() -> None:
    assert resolve_component("nextseek", "demo").name == "app"
    assert resolve_component("agent", "demo").name == "cc-agent"
    assert resolve_component("all", "demo").name == "custom-stack"
    with pytest.raises(ValueError, match="unknown rebuild component"):
        resolve_component("nginx", "demo")


def test_registry_has_one_destination_per_first_party_image() -> None:
    images = registry_images()
    assert len(images) == 4
    assert len({image.local_image for image in images}) == 4
    assert len({image.registry_image for image in images}) == 4


def test_attribute_runtimes_are_not_started_unless_the_profile_is_on(monkeypatch) -> None:
    """A profiles: key alone does not gate these.

    Naming a service explicitly on a docker compose command line starts it even
    when it carries a profile, and startup passes an explicit service list. So
    the gate only holds if startup also stops naming them.
    """
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES
    for name in ATTRIBUTE_RUNTIME_SERVICES:
        assert name not in app_runtime_services()

    monkeypatch.setenv("COMPOSE_PROFILES", "attributes")
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES + ATTRIBUTE_RUNTIME_SERVICES

    monkeypatch.setenv("COMPOSE_PROFILES", "other,attributes , third")
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES + ATTRIBUTE_RUNTIME_SERVICES

    monkeypatch.setenv("COMPOSE_PROFILES", "unrelated")
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES


def test_compose_marks_the_attribute_runtimes_with_the_profile() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    for name in ATTRIBUTE_RUNTIME_SERVICES:
        assert compose["services"][name].get("profiles") == ["attributes"], name
    assert "profiles" not in compose["services"]["nextseek"]
