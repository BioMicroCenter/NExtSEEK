"""The reingest probes must assert artifacts and a QA disposition, not just routing.

Both variants previously asserted only `route eq container_cc` and `last_reply
nonempty`, which was previously graded WEAK for asserting nothing about outcomes.

``families`` in these probe files is a dict keyed by family name (see
probe-2026-07-29.json / probe-2026-07-29-retest.json), not a list -- this walks
it accordingly rather than assuming the corpus.json list-of-families shape.

Both guard tests below assert against the ``pass_criteria`` list itself, never
against a serialized dump of the whole turn (which would also match prose
documentation keys like ``_asserts``/``_disposition_check`` -- see the 2026-09-17
review that found the disposition guard was inverted: it passed with the real
"Reingest ready" criterion removed, and failed with it present but the prose key
deleted).
"""
import json
from pathlib import Path

PROBE = (Path(__file__).resolve().parents[1] / "probes" / "probe-2026-07-29.json")
PROBE_RETEST = (Path(__file__).resolve().parents[1] / "probes" / "probe-2026-07-29-retest.json")

# The disposition proxy: render_qa_for_user (NessieAI/ns/reingest/report.py) headers
# its reply "Reingest ready" unless some sample type HARD_REJECTed, so this is the
# closest resolvable proxy the harness's field DSL has for a CLEAN/SOFT_FLAG QA verdict.
_DISPOSITION_CRITERION = {"field": "last_reply", "op": "contains", "value": "Reingest ready"}

# The artifact proxy: a "reingest_" name only appears in the reply when
# _resolve_artifact (report.py) actually matched a rendered workbook for that
# sample type, so this proves a workbook artifact was produced -- in both the
# single- and multi-deliverable case, unlike api_artifact.artifacts.zip (see
# the probes' own _asserts notes for why that field is not asserted here).
_ARTIFACT_CRITERION = {"field": "last_reply", "op": "matches_re", "value": "reingest_"}


def _variants(probe_path: Path):
    data = json.loads(probe_path.read_text())
    families = data.get("families") or {}
    for family in families.values():
        for variant in family.get("variants", []):
            if "reingest" in variant.get("id", ""):
                yield variant


def _pass_criteria(variant: dict) -> list[dict]:
    """Every pass_criteria entry across all turns of `variant` -- never a
    json.dumps of the turn, which would also match prose documentation keys."""
    criteria: list[dict] = []
    for turn in variant.get("turns", []):
        criteria.extend(turn.get("pass_criteria", []))
    return criteria


def test_every_reingest_variant_asserts_more_than_routing():
    found = list(_variants(PROBE))
    assert found, "no reingest variants found in the probe file"
    for variant in found:
        criteria = _pass_criteria(variant)
        assert _ARTIFACT_CRITERION in criteria, \
            f"{variant['id']} still asserts routing only (missing {_ARTIFACT_CRITERION})"


def test_every_reingest_variant_asserts_a_qa_disposition():
    found = list(_variants(PROBE))
    assert found, "no reingest variants found in the probe file"
    for variant in found:
        criteria = _pass_criteria(variant)
        assert _DISPOSITION_CRITERION in criteria, \
            f"{variant['id']} does not check the QA verdict (missing {_DISPOSITION_CRITERION})"


def test_retest_mirror_carries_the_same_upgrade():
    # probe-2026-07-29-retest.json's own description calls itself a "verbatim
    # copy" of the reingest variants in probe-2026-07-29.json -- if one file's
    # criteria are upgraded and the other is not, that claim silently goes
    # false and a retest run would exercise the old, weak assertions.
    main_variants = {v["id"]: v for v in _variants(PROBE)}
    retest_variants = {v["id"]: v for v in _variants(PROBE_RETEST)}
    assert retest_variants, "no reingest variants found in the retest probe file"
    assert set(main_variants) == set(retest_variants)
    for variant_id, main_variant in main_variants.items():
        retest_variant = retest_variants[variant_id]
        main_criteria = main_variant["turns"][0]["pass_criteria"]
        retest_criteria = retest_variant["turns"][0]["pass_criteria"]
        assert retest_criteria == main_criteria, \
            f"{variant_id}: retest criteria have drifted from the main probe"
