"""Human-grade initial fit: functional labels never replace arm outcomes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.family_labels import corpus_snapshot
from nextseek_api.eval.disposition import OutcomeBucket
from nextseek_api.eval.human_grade_fit import (
    DEFAULT_EVIDENCE_IDENTITY,
    EvidenceIntegrityError,
    ModelMode,
    build_human_grade_fit,
    activate_human_grade_generation,
    manifest_for_combined,
    publish_human_grade_fit,
)
from nextseek_api.eval.publish import FitResult, PublicationEvidenceRequired, publish
from nextseek_api.eval.publish import manifest_for_combined as publication_manifest


DELIVERY = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07")


@pytest.fixture(scope="module")
def prepared():
    return build_human_grade_fit(DELIVERY, model_mode=ModelMode.dev_analytic)


@pytest.fixture(scope="module")
def initial_release():
    return build_human_grade_fit(DELIVERY, model_mode=ModelMode.initial_human_grade)


def test_full_delivery_conserves_all_298_arms_and_149_pairs(prepared):
    assert len(prepared.eval_rows) == 298
    assert prepared.conservation.input_count == 298
    assert prepared.conservation.balanced
    assert len(prepared.admission.retained_pairs) == 149
    assert prepared.admission.excluded_pair_ids == []
    assert prepared.admission.pending_pair_ids == []
    assert len(prepared.pair_rows) == 149


def test_human_grade_is_only_the_functional_axis(prepared):
    by_arm = {arm.arm_id: arm for arm in prepared.arms}

    runtime_fail_with_human_pass = [
        arm for arm in prepared.arms
        if arm.row.functional_success is True and arm.row.runtime_success is False
    ]
    artifact_fail_with_human_pass = [
        arm for arm in prepared.arms
        if arm.row.functional_success is True and arm.row.artifact_success is False
    ]
    assert runtime_fail_with_human_pass
    assert artifact_fail_with_human_pass
    for arm in runtime_fail_with_human_pass + artifact_fail_with_human_pass:
        assert by_arm[arm.arm_id].bucket.bucket is OutcomeBucket.not_desired
        assert arm.combined_success is False

    human_fails_despite_both_machine_axes = [
        arm for arm in prepared.arms
        if arm.row.functional_success is False
        and arm.row.runtime_success is True
        and arm.row.artifact_success is True
    ]
    assert human_fails_despite_both_machine_axes
    assert all(arm.combined_success is False for arm in human_fails_despite_both_machine_axes)


def test_hash_tampering_refused_before_archive_members_are_parsed():
    forged = replace(DEFAULT_EVIDENCE_IDENTITY, archive_sha256="0" * 64)
    with pytest.raises(EvidenceIntegrityError, match="testquestions.zip"):
        build_human_grade_fit(DELIVERY, identity=forged, model_mode=ModelMode.dev_analytic)

    forged_member = replace(
        DEFAULT_EVIDENCE_IDENTITY,
        member_sha256={
            **DEFAULT_EVIDENCE_IDENTITY.member_sha256,
            "set3_final/hibayes/hibayes_eval_rows_ns.csv": "0" * 64,
        },
    )
    with pytest.raises(EvidenceIntegrityError, match="authenticated MANIFEST.json"):
        build_human_grade_fit(
            DELIVERY,
            identity=forged_member,
            model_mode=ModelMode.dev_analytic,
        )


def _patch_authenticated_bayes_manifest(monkeypatch, mutate):
    import nextseek_api.eval.human_grade_fit as human_grade_fit

    original = human_grade_fit._verified_source_bytes

    def authenticated_with_semantic_mutation(delivery, identity):
        members, artifact = original(delivery, identity)
        changed = dict(members)
        manifest = json.loads(changed["set3_final/bayes_manifest.json"])
        mutate(manifest)
        changed["set3_final/bayes_manifest.json"] = json.dumps(manifest).encode()
        return changed, artifact

    monkeypatch.setattr(
        human_grade_fit,
        "_verified_source_bytes",
        authenticated_with_semantic_mutation,
    )


def _patch_current_corpus(monkeypatch, tmp_path, mutate):
    import nextseek_api.eval.human_grade_fit as human_grade_fit

    payload = json.loads(Path(corpus_snapshot().corpus_path).read_text())
    mutate(payload)
    changed_path = tmp_path / "current-corpus.json"
    changed_path.write_text(json.dumps(payload, sort_keys=True))
    changed = corpus_snapshot(changed_path)
    monkeypatch.setattr(human_grade_fit, "corpus_snapshot", lambda: changed)
    return changed


def _first_variant(payload):
    return next(
        variant
        for family in payload["families"].values()
        for variant in family["variants"]
    )


def _remove_first_variant(payload):
    next(iter(payload["families"].values()))["variants"].pop(0)


def _add_variant(payload):
    family_name, family = next(iter(payload["families"].items()))
    family["variants"].append({"id": "compatibility.added", "family": family_name})


def _change_variant_family(payload):
    variant = _first_variant(payload)
    variant["family"] = next(name for name in payload["families"] if name != variant["family"])


def _add_family(payload):
    payload["families"]["compatibility_added_family"] = {"description": "", "variants": []}


def test_current_corpus_annotation_drift_is_compatible(monkeypatch, tmp_path):
    changed = _patch_current_corpus(
        monkeypatch,
        tmp_path,
        lambda payload: payload.__setitem__("_compatibility_annotation", "content drift"),
    )
    result = build_human_grade_fit(DELIVERY)
    provenance = result.publication_evidence.source_provenance
    assert provenance["training_corpus_sha256"] == DEFAULT_EVIDENCE_IDENTITY.member_sha256[
        "corpus/corpus.json"
    ]
    assert provenance["current_compatible_corpus_sha256"] == changed.corpus_sha256
    assert result.publication_evidence.compatibility_keys == {
        "taxonomy_version": changed.taxonomy_version,
        "corpus_hash": changed.corpus_sha256,
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.__setitem__("version", 3), "version drift"),
        (_remove_first_variant, "variant ID drift"),
        (_add_variant, "variant ID drift"),
        (_change_variant_family, "variant family mapping drift"),
        (_add_family, "family set drift"),
    ],
)
def test_current_corpus_semantic_drift_refuses(monkeypatch, tmp_path, mutate, message):
    _patch_current_corpus(monkeypatch, tmp_path, mutate)
    with pytest.raises(EvidenceIntegrityError, match=message):
        build_human_grade_fit(DELIVERY)


def test_authenticated_manifest_corpus_fingerprint_must_match_training(monkeypatch):
    _patch_authenticated_bayes_manifest(
        monkeypatch,
        lambda manifest: manifest["run_meta"].__setitem__("corpus_fingerprint", "0" * 64),
    )
    with pytest.raises(EvidenceIntegrityError, match="corpus_fingerprint"):
        build_human_grade_fit(DELIVERY)


def test_authenticated_manifest_selected_ids_must_exactly_match_pairs(monkeypatch):
    _patch_authenticated_bayes_manifest(
        monkeypatch,
        lambda manifest: manifest["run_meta"]["selected_ids"].reverse(),
    )
    with pytest.raises(EvidenceIntegrityError, match="selected_ids"):
        build_human_grade_fit(DELIVERY)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "ordinary", "run_meta.mode"),
        ("arms", ["cc", "ns"], "run_meta.arms"),
        ("max_usd", -0.01, "run_meta.max_usd"),
        ("resumed", "false", "run_meta.resumed"),
        ("superseded_runs", {}, "run_meta.superseded_runs"),
    ],
)
def test_authenticated_manifest_run_meta_semantics_fail_closed(
    monkeypatch,
    field,
    value,
    message,
):
    _patch_authenticated_bayes_manifest(
        monkeypatch,
        lambda manifest: manifest["run_meta"].__setitem__(field, value),
    )
    with pytest.raises(EvidenceIntegrityError, match=message):
        build_human_grade_fit(DELIVERY)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda meta: meta.__setitem__("max_usd", float("nan")), "run_meta.max_usd"),
        (lambda meta: meta.__setitem__("max_usd", float("inf")), "run_meta.max_usd"),
        (lambda meta: meta.__setitem__("base_url", "localhost:8000"), "run_meta.base_url"),
        (lambda meta: meta.__setitem__("base_url", "http://user:pass@localhost"), "run_meta.base_url"),
        (lambda meta: meta.__setitem__("git_sha", "not-a-git-sha"), "run_meta.git_sha"),
        (lambda meta: meta.__setitem__("superseded_runs", ["not-an-object"]), "superseded_runs.0"),
        (
            lambda meta: meta.__setitem__(
                "superseded_runs",
                [{**meta, "superseded_runs": [], "git_sha": "not-a-git-sha"}],
            ),
            "superseded_runs.0.git_sha",
        ),
    ],
)
def test_authenticated_manifest_run_meta_identity_details_fail_closed(
    monkeypatch,
    mutate,
    message,
):
    _patch_authenticated_bayes_manifest(
        monkeypatch,
        lambda manifest: mutate(manifest["run_meta"]),
    )
    with pytest.raises(EvidenceIntegrityError, match=message):
        build_human_grade_fit(DELIVERY)


def test_dev_dry_run_report_is_deterministic_and_explicitly_non_authoritative(prepared):
    assert prepared.report_json() == prepared.report_json()
    report = prepared.report()
    assert report["model"]["mode"] == "dev_analytic"
    assert report["model"]["authoritative"] is False
    assert report["source"]["judge_calls_used"] == 0
    assert report["source"]["functional_success_source"] == "human_grades"


def test_combined_fit_cannot_publish_without_explicit_evidence(prepared):
    with pytest.raises(PublicationEvidenceRequired):
        publish(prepared.fit)


def test_generic_fit_result_cannot_receive_fabricated_local_defaults():
    with pytest.raises(PublicationEvidenceRequired, match="FitResult publication is disabled"):
        publish(FitResult())


def test_initial_human_grade_publication_requires_explicit_override(initial_release):
    with pytest.raises(PublicationEvidenceRequired, match="explicit initial_human_grade"):
        publication_manifest(
            initial_release.fit,
            initial_release.publication_evidence,
            for_publication=True,
        )
    manifest = publication_manifest(
        initial_release.fit,
        initial_release.publication_evidence,
        for_publication=True,
        allow_initial_release_override=True,
    )
    assert manifest.fit_diagnostics["authoritative"] is False
    assert manifest.fit_diagnostics["initial_release_override"] is True
    assert manifest.source_provenance["initial_release_override"] is True


@pytest.mark.django_db
def test_refused_dev_publication_writes_no_paired_registry(prepared):
    from nextseek_api.assistant.models_db import PairedRunRegistry

    with pytest.raises(PublicationEvidenceRequired):
        publish_human_grade_fit(prepared)
    assert not PairedRunRegistry.objects.filter(
        paired_run_id=prepared.paired_batch.paired_run_id
    ).exists()


def test_publication_manifest_is_current_corpus_compatible_and_honest(prepared):
    manifest = manifest_for_combined(prepared.fit, prepared.publication_evidence)
    current = corpus_snapshot()
    assert manifest.compatibility_keys == {
        "taxonomy_version": current.taxonomy_version,
        "corpus_hash": current.corpus_sha256,
    }
    assert manifest.counts["input_arms"] == 298
    assert manifest.counts["retained_pairs"] == 149
    assert manifest.source_provenance["corpus_version"] == 2
    assert manifest.source_provenance["training_corpus_sha256"] == (
        DEFAULT_EVIDENCE_IDENTITY.member_sha256["corpus/corpus.json"]
    )
    assert manifest.source_provenance["current_compatible_corpus_sha256"] == (
        current.corpus_sha256
    )
    assert manifest.source_provenance["corpus_sha256"] == current.corpus_sha256
    assert manifest.source_provenance["functional_success_source"] == "human_grades"
    assert manifest.source_provenance["judge_calls_used"] == 0
    assert manifest.source_provenance["model_mode"] == "dev_analytic"
    assert manifest.source_provenance["stack_identity_status"] == "legacy_git_sha_only"
    assert manifest.source_provenance["stack_identity_debt"]
    assert manifest.source_provenance["source_git_sha"] == "26609bd"
    assert manifest.source_provenance["stack_image_digests"] == {}
    assert manifest.fit_diagnostics["authoritative"] is False
    assert manifest.input_hash != manifest.attempt_hash
    assert manifest.attempt_hash != manifest.aggregate_hash


@pytest.mark.django_db
def test_nested_paired_batch_mutation_refuses_before_any_durable_write(initial_release):
    from nextseek_api.assistant.models_db import PairedRunRegistry, PosteriorGeneration

    changed = deepcopy(initial_release)
    changed.paired_batch.pairs[0]["family"] = "post-prepare-mutation"
    with pytest.raises(EvidenceIntegrityError, match="paired batch content hash"):
        publish_human_grade_fit(
            changed,
            allow_initial_release_override=True,
        )
    assert PairedRunRegistry.objects.count() == 0
    assert PosteriorGeneration.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "tamper",
    [
        lambda evidence: replace(evidence, aggregate_hash="forged-aggregate"),
        lambda evidence: replace(
            evidence,
            family_retained_pairs={
                **evidence.family_retained_pairs,
                next(iter(evidence.family_retained_pairs)): 999,
            },
        ),
    ],
)
def test_derived_publication_evidence_tampering_refuses_before_write(initial_release, tamper):
    from nextseek_api.assistant.models_db import PairedRunRegistry, PosteriorGeneration

    changed = replace(
        initial_release,
        publication_evidence=tamper(initial_release.publication_evidence),
    )
    with pytest.raises(EvidenceIntegrityError, match="derived publication evidence"):
        publish_human_grade_fit(changed, allow_initial_release_override=True)
    assert PairedRunRegistry.objects.count() == 0
    assert PosteriorGeneration.objects.count() == 0


@pytest.mark.django_db
def test_forged_authority_and_stack_provenance_refuse_before_write(initial_release):
    from nextseek_api.assistant.models_db import PairedRunRegistry, PosteriorGeneration

    provenance = dict(initial_release.publication_evidence.source_provenance)
    provenance.pop("stack_identity_status")
    provenance.update(
        model_mode="authoritative_mcmc",
        initial_release_override=False,
    )
    changed = replace(
        initial_release,
        publication_evidence=replace(
            initial_release.publication_evidence,
            fit_diagnostics={"authoritative": True, "diagnostics_ok": True},
            source_provenance=provenance,
        ),
    )
    with pytest.raises(EvidenceIntegrityError, match="derived publication evidence"):
        publish_human_grade_fit(changed)
    assert PairedRunRegistry.objects.count() == 0
    assert PosteriorGeneration.objects.count() == 0


@pytest.mark.django_db
def test_coordinated_source_hash_and_evidence_forgery_is_reauthenticated(initial_release):
    from nextseek_api.assistant.models_db import PairedRunRegistry, PosteriorGeneration
    from nextseek_api.eval import human_grade_fit

    forged_sources = deepcopy(initial_release.source_hashes)
    forged_sources["archive_sha256"] = "0" * 64
    forged_evidence = human_grade_fit._publication_evidence_from_derived_facts(
        model_mode=initial_release.model_mode,
        fit=initial_release.fit,
        source_hashes=forged_sources,
        paired_batch=initial_release.paired_batch,
        paired_content_hash=initial_release.paired_content_hash,
        arms=initial_release.arms,
        conservation=initial_release.conservation,
        admission=initial_release.admission,
        pair_rows=initial_release.pair_rows,
    )
    changed = replace(
        initial_release,
        source_hashes=forged_sources,
        publication_evidence=forged_evidence,
    )
    with pytest.raises(EvidenceIntegrityError, match="source delivery changed"):
        publish_human_grade_fit(changed, allow_initial_release_override=True)
    assert PairedRunRegistry.objects.count() == 0
    assert PosteriorGeneration.objects.count() == 0


@pytest.mark.django_db
def test_wrong_registered_content_hash_refuses_generation_publication(initial_release):
    from nextseek_api.assistant.models_db import PairedRunRegistry, PosteriorGeneration
    from nextseek_api.eval.evidence_kinds import UnapprovedPairedRun
    from nextseek_api.eval.paired_run_registry import register_paired_run

    register_paired_run(
        paired_run_id=initial_release.paired_batch.paired_run_id,
        schema_version=initial_release.paired_batch.schema_version,
        content_hash="wrong-content-hash",
    )
    with pytest.raises((ValueError, UnapprovedPairedRun), match="content_hash mismatch"):
        publish_human_grade_fit(
            initial_release,
            allow_initial_release_override=True,
        )
    assert PairedRunRegistry.objects.count() == 1
    assert PosteriorGeneration.objects.count() == 0


@pytest.mark.django_db
def test_post_registration_publish_failure_rolls_back_registry(initial_release):
    from nextseek_api.assistant.models_db import PairedRunRegistry, PosteriorGeneration
    from nextseek_api.eval import generation_store

    generation_store.set_test_abort_publish_after_generation(True)
    try:
        with pytest.raises(generation_store.PublishAbort, match="test abort"):
            publish_human_grade_fit(
                initial_release,
                allow_initial_release_override=True,
            )
    finally:
        generation_store.set_test_abort_publish_after_generation(False)
    assert PairedRunRegistry.objects.count() == 0
    assert PosteriorGeneration.objects.count() == 0


def test_exact_four_digest_stack_identity_is_used(monkeypatch):
    digests = {
        "nextseek_image": "sha256:" + "1" * 64,
        "container_agent_image": "sha256:" + "2" * 64,
        "sidecar_image": "sha256:" + "3" * 64,
        "seek_image": "sha256:" + "4" * 64,
    }

    _patch_authenticated_bayes_manifest(
        monkeypatch,
        lambda manifest: manifest["run_meta"].update(digests),
    )
    result = build_human_grade_fit(DELIVERY)
    provenance = result.publication_evidence.source_provenance
    assert provenance["stack_identity_status"] == "exact_four_image_digests"
    assert provenance["stack_identity_debt"] is None
    assert provenance["stack_image_digests"] == digests
    assert all(arm.row.stack_id.startswith("stack-v1:sha256:") for arm in result.arms)


def test_partial_stack_digest_identity_is_refused(monkeypatch):
    _patch_authenticated_bayes_manifest(
        monkeypatch,
        lambda manifest: manifest["run_meta"].__setitem__(
            "nextseek_image", "sha256:" + "1" * 64
        ),
    )
    with pytest.raises(EvidenceIntegrityError, match="partial four-image stack identity"):
        build_human_grade_fit(DELIVERY)


def test_legacy_stack_cannot_claim_authoritative_publication(prepared):
    authoritative_claim = replace(
        prepared.publication_evidence,
        fit_diagnostics={"authoritative": True, "diagnostics_ok": True},
        source_provenance={
            **prepared.publication_evidence.source_provenance,
            "model_mode": "authoritative_mcmc",
            "initial_release_override": False,
        },
    )
    with pytest.raises(PublicationEvidenceRequired, match="legacy git-SHA-only"):
        publication_manifest(prepared.fit, authoritative_claim, for_publication=True)


@pytest.mark.django_db
def test_initial_release_publish_is_immutable_and_activation_is_separate_cas(initial_release):
    from nextseek_api.eval.generation_store import EMPTY_ACTIVE_HASH, get_current_active_hash

    generation = publish_human_grade_fit(
        initial_release,
        allow_initial_release_override=True,
    )
    assert get_current_active_hash() == EMPTY_ACTIVE_HASH
    assert generation.payload["source_provenance"]["functional_success_source"] == "human_grades"
    assert generation.payload["fit_diagnostics"]["authoritative"] is False

    pointer = activate_human_grade_generation(
        generation.generation_hash,
        expected_hash=EMPTY_ACTIVE_HASH,
    )
    assert pointer.active.generation_hash == generation.generation_hash
