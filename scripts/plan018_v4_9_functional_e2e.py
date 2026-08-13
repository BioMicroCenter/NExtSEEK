"""Disposable functional E2E for the initial human-grade posterior router.

This harness uses ``dmac.test_settings`` and its in-memory database.  It never
touches the live generation store.  A real classifier call is made only when
``--live-classifier`` is explicit; otherwise the classifier result is injected
for a zero-provider structural replay.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
from unittest import mock


DEFAULT_DELIVERY = Path(
    "/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07"
)


def run(delivery: Path, *, live_classifier: bool) -> dict[str, object]:
    import django

    django.setup()

    from django.conf import settings
    from django.core.management import call_command

    from nextseek_api.assistant.models_db import PosteriorGeneration
    from nextseek_api.cc_assistant import posterior_selector, router
    from nextseek_api.eval.generation_store import EMPTY_ACTIVE_HASH
    from nextseek_api.eval.human_grade_fit import (
        ModelMode,
        activate_human_grade_generation,
        build_human_grade_fit,
        publish_human_grade_fit,
    )

    call_command("migrate", interactive=False, verbosity=0)
    prepared = build_human_grade_fit(
        delivery,
        model_mode=ModelMode.initial_human_grade,
    )
    generation = publish_human_grade_fit(
        prepared,
        allow_initial_release_override=True,
    )
    activate_human_grade_generation(
        generation.generation_hash,
        expected_hash=EMPTY_ACTIVE_HASH,
        activated_by="disposable-e2e",
    )

    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    classifier = (
        nullcontext()
        if live_classifier
        else mock.patch.object(
            router,
            "_classify_query",
            return_value=("sample_search", "baml", "injected structural replay"),
        )
    )
    with classifier:
        decision = router.decide("Find me mice treated with NDMA.")

    if decision.source != "posterior":
        raise AssertionError(f"expected posterior source, got {decision!r}")
    if decision.task_family != "sample_search":
        raise AssertionError(f"expected sample_search, got {decision!r}")
    if decision.route != "container_cc":
        raise AssertionError(f"expected container_cc winner, got {decision!r}")
    if decision.generation_hash != generation.generation_hash:
        raise AssertionError("router did not report the activated generation")

    # A current-store mutation must make the active snapshot unusable.  This
    # directly exercises the fail-open selector without paying for another
    # classifier call.
    PosteriorGeneration.objects.filter(pk=generation.pk).update(
        decision_status="legacy_fallback"
    )
    corrupt_fallback = posterior_selector.select_route("sample_search")
    if corrupt_fallback is not None:
        raise AssertionError("corrupt active generation did not fail open")

    return {
        "schema_version": "plan018-v4-9-functional-e2e/v1",
        "database": "sqlite-memory-disposable",
        "live_classifier": live_classifier,
        "input_arms": prepared.conservation.input_count,
        "retained_pairs": len(prepared.admission.retained_pairs),
        "generation_hash": generation.generation_hash,
        "task_family": decision.task_family,
        "route": decision.route,
        "route_source": decision.source,
        "corrupt_generation_fallback": corrupt_fallback is None,
        "authoritative": False,
        "initial_release_override": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", type=Path, default=DEFAULT_DELIVERY)
    parser.add_argument("--live-classifier", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.delivery, live_classifier=args.live_classifier), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
