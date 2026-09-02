"""Contracts binding Compose's local images to startup rebuild policy."""
from pathlib import Path

import pytest
import yaml

from startup.lib.rebuild_policy import (
    BASE_APP_RUNTIME_SERVICES,
    app_runtime_services,
    component_policies,
    registry_images,
    resolve_component,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

#: The four services folded into the app container's entrypoint on 2026-09-02.
#: Named here so their return is a test failure rather than a rediscovery.
FOLDED_SERVICES = (
    "attribute_mutation_worker",
    "attribute_mutation_dispatcher",
    "attribute_mutation_recovery_scheduler",
    "assay_registration_worker",
)


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


def test_every_compose_build_target_is_owned_by_custom_stack() -> None:
    build_targets = {
        name for name, service in _compose()["services"].items() if "build" in service
    }
    policy = component_policies("nextseek")["custom-stack"]
    assert set(policy.build_services) == build_targets


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


def test_every_service_running_the_app_image_is_restarted_by_a_rebuild() -> None:
    """The invariant the `attributes` profile used to violate.

    A service that runs the app image but is not in the restart set keeps its
    OLD container after `./startup.sh rebuild` -- old code, indefinitely, and
    `restart: unless-stopped` makes it look healthy. That is not a property of
    profiles specifically: ANY app-image service outside this set has the bug.
    Asserting the set equality means the next such service fails here on the
    commit that adds it, whatever mechanism it arrives by.
    """
    services = _compose()["services"]
    app_image = services["nextseek"]["image"]
    running_app_code = {
        name for name, service in services.items() if service.get("image") == app_image
    }
    assert running_app_code == set(app_runtime_services())


def test_app_runtime_services_ignores_compose_profiles(monkeypatch) -> None:
    """Nothing persisted COMPOSE_PROFILES, so it silently decided which workers
    a rebuild moved to the new image. The variable now decides nothing here."""
    for value in ("", "attributes", "assay-registration", "attributes,other"):
        monkeypatch.setenv("COMPOSE_PROFILES", value)
        assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES, value

    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES


def test_no_service_declares_a_compose_profile() -> None:
    """A `profiles:` key is how a service leaves the default bring-up, and
    leaving the default bring-up is how it ends up outside the restart set. The
    stack has no profiled services; adding one is the decision this test asks a
    future author to make deliberately."""
    for name, service in _compose()["services"].items():
        assert "profiles" not in service, name


def test_the_folded_services_are_gone_from_compose() -> None:
    services = _compose()["services"]
    for name in FOLDED_SERVICES:
        assert name not in services, (
            f"{name} runs as a process of the app container now; a compose "
            "service by this name would run it twice"
        )


def test_the_app_component_restarts_exactly_the_app_service(monkeypatch) -> None:
    """`app_runtime_services()` is only load-bearing because the app policy
    reads it. Asserted through `component_policies`, which is what
    `./startup.sh rebuild` actually consumes."""
    monkeypatch.setenv("COMPOSE_PROFILES", "attributes,assay-registration")
    assert component_policies("nextseek")["app"].restart_services == (
        BASE_APP_RUNTIME_SERVICES
    )
