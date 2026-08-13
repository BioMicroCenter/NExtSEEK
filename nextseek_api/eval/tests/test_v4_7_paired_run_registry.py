"""V4-7 PairedRunRegistry and Lane M negatives."""
from __future__ import annotations

import pytest

from nextseek_api.eval.evidence_kinds import OnlineEvidenceRejected, UnapprovedPairedRun
from nextseek_api.eval.fit.fit_boundary import validate_publish_provenance
from nextseek_api.eval.generation_store import (
    GenerationManifest,
    PublishError,
    create_generation,
    publish_generation,
)
from nextseek_api.eval.paired_run_registry import register_paired_run

pytestmark = pytest.mark.django_db(transaction=True)


def _manifest(suffix: str, **overrides):
    base = {
        "input_hash": f"input-{suffix}",
        "attempt_hash": f"attempt-{suffix}",
        "aggregate_hash": f"aggregate-{suffix}",
        "config_fingerprint": "cfg",
        "decision_status": "activated_all",
        "groups": [
            {
                "name": "sample_search",
                "route": "container_cc",
                "posterior_mean": 0.9,
                "band": "Reliable",
                "n_total": 10,
            }
        ],
        "compatibility_keys": {"taxonomy_version": "v1", "corpus_hash": f"corpus-{suffix}"},
        "counts": {"retained_pairs": 10},
        "source_provenance": {
            "paired_run_id": f"paired-{suffix}",
            "paired_run_content_hash": f"hash-{suffix}",
            "evidence_kind": "paired_experimental",
            "route_source": "forced",
        },
    }
    base.update(overrides)
    return GenerationManifest(**base)


def test_registry_immutable_content_hash():
    register_paired_run(
        paired_run_id="run-immutable",
        schema_version="paired_run/v1",
        content_hash="abc123",
    )
    register_paired_run(
        paired_run_id="run-immutable",
        schema_version="paired_run/v1",
        content_hash="abc123",
    )
    with pytest.raises(ValueError, match="content_hash mismatch"):
        register_paired_run(
            paired_run_id="run-immutable",
            schema_version="paired_run/v1",
            content_hash="different",
        )


def test_publish_refuses_unapproved_paired_run():
    with pytest.raises(PublishError, match="authenticated human-fit publisher"):
        publish_generation(_manifest("unapproved"))


def test_self_registered_arbitrary_manifest_cannot_publish():
    register_paired_run(
        paired_run_id="paired-approved",
        schema_version="paired_run/v1",
        content_hash="hash-approved",
    )
    manifest = _manifest("approved", source_provenance={
        "paired_run_id": "paired-approved",
        "paired_run_content_hash": "hash-approved",
        "evidence_kind": "paired_experimental",
        "route_source": "forced",
    })
    with pytest.raises(PublishError, match="authenticated human-fit publisher"):
        publish_generation(manifest)


def test_arbitrary_manifest_cannot_use_direct_create():
    from nextseek_api.assistant.models_db import PosteriorGeneration

    with pytest.raises(PublishError, match="authenticated human-fit publisher"):
        create_generation(_manifest("direct-create"))
    assert PosteriorGeneration.objects.count() == 0


def test_publish_refuses_registered_run_with_wrong_content_hash():
    register_paired_run(
        paired_run_id="paired-wrong-hash",
        schema_version="paired_run/v1",
        content_hash="registered-hash",
    )
    manifest = _manifest("wrong-hash", source_provenance={
        "paired_run_id": "paired-wrong-hash",
        "paired_run_content_hash": "forged-hash",
        "evidence_kind": "paired_experimental",
        "route_source": "forced",
    })
    with pytest.raises(UnapprovedPairedRun, match="content_hash mismatch"):
        validate_publish_provenance(manifest.source_provenance)


def test_publish_refuses_online_provenance():
    register_paired_run(
        paired_run_id="paired-online-block",
        schema_version="paired_run/v1",
        content_hash="hash-online",
    )
    with pytest.raises(OnlineEvidenceRejected):
        validate_publish_provenance(
            {
                "paired_run_id": "paired-online-block",
                "evidence_kind": "paired_experimental",
                "route_source": "posterior",
            }
        )
