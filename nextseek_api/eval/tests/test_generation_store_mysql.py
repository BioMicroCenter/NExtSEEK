import threading

import pytest
from django.db import connection

from nextseek_api.eval.generation_validation import ValidationError
from nextseek_api.eval.generation_store import (
    EMPTY_ACTIVE_HASH,
    ActivationAbort,
    ActivationError,
    GenerationManifest,
    PublishAbort,
    PublishError,
    activate_generation,
    create_generation,
    get_active_snapshot,
    get_current_active_hash,
    publish_generation,
    rollback_generation,
    set_test_abort_activate_after_pointer_mutate,
    set_test_abort_publish_after_generation,
)
from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration

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
    }
    base.update(overrides)
    return GenerationManifest(**base)


def test_mysql_stale_cas_refused():
    gen_a = publish_generation(_manifest("a"))
    gen_b = publish_generation(_manifest("b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    with pytest.raises(ActivationError, match="stale CAS"):
        activate_generation(gen_b, expected_hash=gen_b.generation_hash)


def test_mysql_two_activators_second_loses_race():
    gen_a = publish_generation(_manifest("race-a"))
    gen_b = publish_generation(_manifest("race-b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def _activate(gen, token):
        try:
            barrier.wait(timeout=5)
            activate_generation(gen, expected_hash=token)
            results.append("ok")
        except ActivationError:
            results.append("stale")

    t1 = threading.Thread(target=_activate, args=(gen_b, gen_a.generation_hash))
    t2 = threading.Thread(target=_activate, args=(gen_b, gen_a.generation_hash))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert results.count("ok") == 1
    assert results.count("stale") == 1
    assert get_current_active_hash() == gen_b.generation_hash


def test_mysql_immutable_overwrite_refused():
    manifest = _manifest("immutable")
    create_generation(manifest)
    with pytest.raises(PublishError, match="overwrite refused"):
        create_generation(_manifest("immutable", aggregate_hash="mutated-aggregate"))


def test_mysql_rollback_restores_previous():
    gen_a = publish_generation(_manifest("rb-a"))
    gen_b = publish_generation(_manifest("rb-b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    activate_generation(gen_b, expected_hash=gen_a.generation_hash)
    rollback_generation(expected_hash=gen_b.generation_hash)
    assert get_current_active_hash() == gen_a.generation_hash


def test_mysql_reader_observes_single_generation_hash():
    gen_a = publish_generation(_manifest("reader-a"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    seen = set()

    def _reader():
        connection.close()
        snap = get_active_snapshot()
        if snap is not None:
            seen.add(snap.generation_hash)

    threads = [threading.Thread(target=_reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {gen_a.generation_hash}


def test_mysql_parent_mismatch_refused_on_validation():
    parent = publish_generation(_manifest("parent"))
    child = publish_generation(_manifest("child"))
    child.parent = parent
    child.payload = {**(child.payload or {}), "parent_hash": "deadbeef"}
    child.save(update_fields=["parent", "payload"])
    with pytest.raises(ValidationError, match="parent"):
        activate_generation(child, expected_hash=EMPTY_ACTIVE_HASH)


def test_mysql_corruption_refused_on_activate():
    generation = publish_generation(_manifest("corrupt"))
    before = get_current_active_hash()
    generation.generation_hash = "0" * 64
    generation.save(update_fields=["generation_hash"])
    with pytest.raises(ValidationError, match="hash"):
        activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    assert get_current_active_hash() == before


def test_mysql_taxonomy_corpus_incompat_refused():
    generation = publish_generation(_manifest("incompat", compatibility_keys={}))
    with pytest.raises(ValidationError, match="compatibility"):
        activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)


def test_mysql_partial_publish_refused():
    generation = publish_generation(_manifest("partial"))
    generation.payload = {**(generation.payload or {}), "partial_publish": True}
    generation.save(update_fields=["payload"])
    with pytest.raises(ValidationError, match="partial"):
        activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)


def test_mysql_crash_publish_boundary_leaves_no_incomplete_generation():
    set_test_abort_publish_after_generation(True)
    try:
        with pytest.raises(PublishAbort):
            create_generation(_manifest("crash-pub"))
    finally:
        set_test_abort_publish_after_generation(False)
    assert PosteriorGeneration.objects.filter(input_hash="input-crash-pub").count() == 0
    assert FamilyPosterior.objects.filter(task_family="sample_search").count() == 0


def test_mysql_crash_activation_boundary_leaves_pointer_unchanged():
    generation = publish_generation(_manifest("crash-act"))
    set_test_abort_activate_after_pointer_mutate(True)
    try:
        with pytest.raises(ActivationAbort):
            activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    finally:
        set_test_abort_activate_after_pointer_mutate(False)
    assert get_current_active_hash() == EMPTY_ACTIVE_HASH
    assert get_active_snapshot() is None
