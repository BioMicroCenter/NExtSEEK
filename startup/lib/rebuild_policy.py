"""Single source of truth for first-party image rebuild behavior."""
from __future__ import annotations

from dataclasses import dataclass


#: Every service that runs the app image, and therefore every service a rebuild
#: must move to the new one.
#:
#: There is exactly one. Until 2026-09-02 there were five: the attribute-mutation
#: worker, dispatcher and recovery scheduler behind an ``attributes`` compose
#: profile, and the assay-registration worker behind its own. This module read
#: COMPOSE_PROFILES to decide which of them ``./startup.sh rebuild`` restarted,
#: nothing persisted that variable, and a rebuild without it moved the app to the
#: new image while leaving the workers on the old one -- old code, indefinitely,
#: under ``restart: unless-stopped``, which looks healthy. All four now run as
#: background processes of the app container (docker/scripts/entrypoint.sh), so
#: there is no second image to leave behind and no variable to forget.
#:
#: Adding an app-image service here is a decision, not a formality: it re-opens
#: that failure mode unless startup also restarts it.
#: ``startup/tests/test_rebuild_policy.py`` asserts this set against compose.
BASE_APP_RUNTIME_SERVICES = ("nextseek",)


def app_runtime_services() -> tuple[str, ...]:
    """Services to start/restart alongside the app image.

    A function rather than a bare constant because callers were written against
    one that could vary by environment. It no longer does.
    """
    return BASE_APP_RUNTIME_SERVICES


#: Backwards-compatible alias for the constant this function returns.
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
