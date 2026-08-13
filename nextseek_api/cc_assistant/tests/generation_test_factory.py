"""Test-only direct model factory for validation and activation fixtures."""
from __future__ import annotations

from dataclasses import replace

from django.db import transaction
from django.utils import timezone as dj_timezone

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration
from nextseek_api.eval import generation_store
from nextseek_api.eval.generation_store import GenerationManifest, PublishError


def _publish_generation_for_test(
    manifest: GenerationManifest,
    *,
    parent: PosteriorGeneration | None = None,
):
    parent_hash = parent.generation_hash if parent else None
    if parent_hash != manifest.parent_hash:
        manifest = replace(manifest, parent_hash=parent_hash)
    generation_hash = generation_store.generation_content_hash(**manifest.to_hash_kwargs())
    existing = PosteriorGeneration.objects.filter(generation_hash=generation_hash).first()
    if existing is not None:
        return existing
    existing_input = PosteriorGeneration.objects.filter(input_hash=manifest.input_hash).first()
    if existing_input is not None and existing_input.generation_hash != generation_hash:
        raise PublishError("overwrite refused — input_hash already bound to a different generation")
    payload = manifest.to_payload()
    payload["partial_publish"] = False
    payload["_canonical_hash_inputs"] = manifest.to_hash_kwargs()
    with transaction.atomic():
        generation = PosteriorGeneration.objects.create(
            generation_hash=generation_hash,
            input_hash=manifest.input_hash,
            config_fingerprint=manifest.config_fingerprint,
            decision_status=manifest.decision_status,
            payload=payload,
            parent=parent,
        )
        if generation_store._should_abort_publish_after_generation():
            raise generation_store.PublishAbort(
                "test abort after generation row before family posteriors"
            )
        fitted_at = dj_timezone.now()
        for group in manifest.groups:
            FamilyPosterior.objects.create(
                generation=generation,
                task_family=group["name"],
                route=group["route"],
                posterior_mean=float(group["posterior_mean"]),
                band=str(group["band"]),
                n_total=int(group["n_total"]),
                fitted_at=group.get("fitted_at") or fitted_at,
            )
    return generation
