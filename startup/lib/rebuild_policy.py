"""Single source of truth for first-party image rebuild behavior."""
from __future__ import annotations

import os
from dataclasses import dataclass


ATTRIBUTES_PROFILE = "attributes"

#: Always-on services that run the app image.
BASE_APP_RUNTIME_SERVICES = ("nextseek",)

#: The async attribute-mutation pipeline. Gated behind the ``attributes`` compose
#: profile, because it is only useful once native attribute mutation is in use.
ATTRIBUTE_RUNTIME_SERVICES = (
    "attribute_mutation_worker",
    "attribute_mutation_dispatcher",
    "attribute_mutation_recovery_scheduler",
)


def attributes_profile_enabled() -> bool:
    """Whether the operator asked for the attribute-mutation pipeline."""
    profiles = os.environ.get("COMPOSE_PROFILES", "")
    return ATTRIBUTES_PROFILE in {p.strip() for p in profiles.split(",") if p.strip()}


def app_runtime_services() -> tuple[str, ...]:
    """Services to start/restart alongside the app image.

    Naming a service explicitly on a ``docker compose`` command line starts it
    **even when it carries a profile**, so a ``profiles:`` key in the compose
    file is not enough on its own: startup must also stop naming these unless
    the profile is on. Otherwise the gate holds for a bare ``docker compose
    up -d`` and silently does nothing for ``./startup.sh install``, which is the
    supported entry point.
    """
    if attributes_profile_enabled():
        return BASE_APP_RUNTIME_SERVICES + ATTRIBUTE_RUNTIME_SERVICES
    return BASE_APP_RUNTIME_SERVICES


#: Backwards-compatible alias. Prefer ``app_runtime_services()``: this constant is
#: evaluated at import time and cannot see a profile set later.
APP_RUNTIME_SERVICES = app_runtime_services()


@dataclass(frozen=True)
class ImagePolicy:
    local_image: str
    registry_image: str


@dataclass(frozen=True)
class RebuildPolicy:
    name: str
    build_services: tuple[str, ...]
    restart_services: tuple[str, ...]
    images: tuple[ImagePolicy, ...]


_REGISTRY_ROOT = "ghcr.io/biomicrocenter"
_ALIASES = {
    "nextseek": "app",
    "agent": "cc-agent",
    "sidecar": "nextseek-sidecar",
    "proxy": "bedrock-proxy",
    "all": "custom-stack",
}


def component_policies(compose_project_name: str) -> dict[str, RebuildPolicy]:
    app = RebuildPolicy(
        name="app",
        build_services=("nextseek",),
        restart_services=app_runtime_services(),
        images=(
            ImagePolicy(
                local_image=f"{compose_project_name}-nextseek:latest",
                registry_image=f"{_REGISTRY_ROOT}/nextseek",
            ),
        ),
    )
    agent = RebuildPolicy(
        name="cc-agent",
        build_services=("cc-agent",),
        restart_services=(),
        images=(
            ImagePolicy(
                local_image="dmac-assistant:poc",
                registry_image=f"{_REGISTRY_ROOT}/nextseek-cc-agent",
            ),
        ),
    )
    sidecar = RebuildPolicy(
        name="nextseek-sidecar",
        build_services=("nextseek-sidecar",),
        restart_services=("nextseek-sidecar",),
        images=(
            ImagePolicy(
                local_image="nextseek-ns-sidecar:latest",
                registry_image=f"{_REGISTRY_ROOT}/nextseek-sidecar",
            ),
        ),
    )
    proxy = RebuildPolicy(
        name="bedrock-proxy",
        build_services=("bedrock-proxy",),
        restart_services=("bedrock-proxy",),
        images=(
            ImagePolicy(
                local_image="nextseek-bedrock-proxy:latest",
                registry_image=f"{_REGISTRY_ROOT}/nextseek-bedrock-proxy",
            ),
        ),
    )
    custom_stack = RebuildPolicy(
        name="custom-stack",
        build_services=tuple(
            service
            for policy in (app, agent, sidecar, proxy)
            for service in policy.build_services
        ),
        restart_services=tuple(
            service
            for policy in (app, sidecar, proxy)
            for service in policy.restart_services
        ),
        images=tuple(
            image
            for policy in (app, agent, sidecar, proxy)
            for image in policy.images
        ),
    )
    return {
        policy.name: policy
        for policy in (app, agent, sidecar, proxy, custom_stack)
    }


def resolve_component(name: str, compose_project_name: str) -> RebuildPolicy:
    normalized = _ALIASES.get(name, name)
    policies = component_policies(compose_project_name)
    try:
        return policies[normalized]
    except KeyError as exc:
        allowed = ", ".join(policies)
        raise ValueError(f"unknown rebuild component '{name}' (choose: {allowed})") from exc


def registry_images(compose_project_name: str = "nextseek") -> tuple[ImagePolicy, ...]:
    """Return every independently-built first-party image exactly once."""
    return component_policies(compose_project_name)["custom-stack"].images
