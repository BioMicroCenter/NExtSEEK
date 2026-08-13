"""Test-only generation factory for validation and activation fixtures."""
from __future__ import annotations

from nextseek_api.eval import generation_store
from nextseek_api.eval.generation_store import GenerationManifest


def _publish_generation_for_test(manifest: GenerationManifest, *, actor: str = "local"):
    return generation_store._publish_authenticated_generation(
        manifest,
        capability=generation_store._AUTHENTICATED_HUMAN_PUBLISH_CAPABILITY,
        actor=actor,
    )
