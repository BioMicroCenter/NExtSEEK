"""Contracts binding Compose's local images to startup rebuild policy."""
from pathlib import Path

import pytest
import yaml

from startup.lib.rebuild_policy import (
    ASSAY_REGISTRATION_RUNTIME_SERVICES,
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


def test_the_assay_registration_worker_shares_the_app_image() -> None:
    """It runs `nextseek_api/` code -- runner.py, executor.py, planner.py -- so
    it is rebuilt by the app image or it is not rebuilt at all."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    app_image = compose["services"]["nextseek"]["image"]
    assert ASSAY_REGISTRATION_RUNTIME_SERVICES, "the worker must stay enumerated"
    for service_name in ASSAY_REGISTRATION_RUNTIME_SERVICES:
        service = compose["services"][service_name]
        assert service["image"] == app_image
        assert "build" not in service


def test_compose_marks_the_assay_registration_worker_with_the_profile() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    for name in ASSAY_REGISTRATION_RUNTIME_SERVICES:
        assert compose["services"][name].get("profiles") == ["assay-registration"], name


def test_the_assay_registration_worker_is_not_started_unless_the_profile_is_on(
    monkeypatch,
) -> None:
    """Same gate as the attribute runtimes, and the same reason a profiles: key
    is not enough on its own.

    The consequence of omitting it is worse here than a service that fails to
    start: `./startup.sh rebuild` is the documented deploy path for
    `nextseek_api/` changes, and a worker outside the restart set keeps running
    its OLD container -- old runner.py, old executor.py -- indefinitely, while
    `restart: unless-stopped` makes it look healthy.
    """
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES
    for name in ASSAY_REGISTRATION_RUNTIME_SERVICES:
        assert name not in app_runtime_services()

    monkeypatch.setenv("COMPOSE_PROFILES", "assay-registration")
    assert app_runtime_services() == (
        BASE_APP_RUNTIME_SERVICES + ASSAY_REGISTRATION_RUNTIME_SERVICES
    )

    monkeypatch.setenv("COMPOSE_PROFILES", "other, assay-registration ,third")
    assert app_runtime_services() == (
        BASE_APP_RUNTIME_SERVICES + ASSAY_REGISTRATION_RUNTIME_SERVICES
    )

    monkeypatch.setenv("COMPOSE_PROFILES", "unrelated")
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES

    monkeypatch.setenv("COMPOSE_PROFILES", "attributes")
    for name in ASSAY_REGISTRATION_RUNTIME_SERVICES:
        assert name not in app_runtime_services(), (
            "the attributes profile must not drag in an unrelated worker"
        )


def test_both_profiles_together_start_both_sets(monkeypatch) -> None:
    """The case the per-profile tests cannot see.

    An early return inside `app_runtime_services` reads correctly one profile at
    a time and silently drops a whole set when an operator runs both -- and the
    dropped set is the one that then ships stale forever. This is the only test
    that fails on it.
    """
    monkeypatch.setenv("COMPOSE_PROFILES", "attributes,assay-registration")
    assert app_runtime_services() == (
        BASE_APP_RUNTIME_SERVICES
        + ATTRIBUTE_RUNTIME_SERVICES
        + ASSAY_REGISTRATION_RUNTIME_SERVICES
    )

    # Order of the operator's list must not decide which set survives.
    monkeypatch.setenv("COMPOSE_PROFILES", "assay-registration,attributes")
    assert set(app_runtime_services()) == set(
        BASE_APP_RUNTIME_SERVICES
        + ATTRIBUTE_RUNTIME_SERVICES
        + ASSAY_REGISTRATION_RUNTIME_SERVICES
    )


def test_a_profile_name_that_merely_contains_a_gate_name_does_not_open_it(
    monkeypatch,
) -> None:
    """COMPOSE_PROFILES is a comma-separated LIST, not a haystack.

    A substring test reads correctly against every profile name in use today and
    then opens a gate for any future name that happens to contain one -- and
    both directions are wrong: `assay-registration-staging` would start the
    production worker, and `no-attributes` would start the pipeline it is named
    for turning off. Found by mutation testing; nothing else here distinguishes
    membership from containment.
    """
    monkeypatch.setenv("COMPOSE_PROFILES", "assay-registration-staging")
    for name in ASSAY_REGISTRATION_RUNTIME_SERVICES:
        assert name not in app_runtime_services(), name

    monkeypatch.setenv("COMPOSE_PROFILES", "no-attributes")
    for name in ATTRIBUTE_RUNTIME_SERVICES:
        assert name not in app_runtime_services(), name

    monkeypatch.setenv("COMPOSE_PROFILES", "xattributes,assay-registrationx")
    assert app_runtime_services() == BASE_APP_RUNTIME_SERVICES


def test_the_app_component_restarts_every_runtime_the_profiles_asked_for(
    monkeypatch,
) -> None:
    """`app_runtime_services()` is only load-bearing because the app policy
    reads it. Asserted through `component_policies`, which is what
    `./startup.sh rebuild` actually consumes."""
    monkeypatch.setenv("COMPOSE_PROFILES", "attributes,assay-registration")
    restart = component_policies("nextseek")["app"].restart_services
    for name in ASSAY_REGISTRATION_RUNTIME_SERVICES + ATTRIBUTE_RUNTIME_SERVICES:
        assert name in restart, name

    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    assert component_policies("nextseek")["app"].restart_services == (
        BASE_APP_RUNTIME_SERVICES
    )


def test_the_worker_is_not_an_independent_build_target() -> None:
    """It must not appear in custom-stack's build list: it has no build: key,
    and `test_every_compose_build_target_is_owned_by_custom_stack` would fail if
    it gained one without being registered."""
    policy = component_policies("nextseek")["custom-stack"]
    for name in ASSAY_REGISTRATION_RUNTIME_SERVICES:
        assert name not in policy.build_services
