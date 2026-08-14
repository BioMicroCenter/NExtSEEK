"""Contracts binding Compose's local images to startup rebuild policy."""
from pathlib import Path

import pytest
import yaml

from startup.lib.rebuild_policy import (
    APP_RUNTIME_SERVICES,
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
    for service_name in APP_RUNTIME_SERVICES[1:]:
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
