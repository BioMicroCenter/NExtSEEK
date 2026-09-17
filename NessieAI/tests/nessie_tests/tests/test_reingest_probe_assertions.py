"""The reingest probes must assert artifacts and a QA disposition, not just routing.

Both variants previously asserted only `route eq container_cc` and `last_reply
nonempty`, which the suite's own coverage sheet
(docs/nessie-harmonization-reingest-questions.xlsx) graded WEAK.

``families`` in these probe files is a dict keyed by family name (see
probe-2026-07-29.json / probe-2026-07-29-retest.json), not a list -- this walks
it accordingly rather than assuming the corpus.json list-of-families shape.
"""
import json
from pathlib import Path

PROBE = (Path(__file__).resolve().parents[1] / "probes" / "probe-2026-07-29.json")
PROBE_RETEST = (Path(__file__).resolve().parents[1] / "probes" / "probe-2026-07-29-retest.json")


def _variants(probe_path: Path):
    data = json.loads(probe_path.read_text())
    families = data.get("families") or {}
    for family in families.values():
        for variant in family.get("variants", []):
            if "reingest" in variant.get("id", ""):
                yield variant


def test_every_reingest_variant_asserts_more_than_routing():
    found = list(_variants(PROBE))
    assert found, "no reingest variants found in the probe file"
    for variant in found:
        asserts = json.dumps(variant.get("turns", variant))
        assert "saved_files" in asserts or "artifact" in asserts, \
            f"{variant['id']} still asserts routing only"


def test_every_reingest_variant_asserts_a_qa_disposition():
    found = list(_variants(PROBE))
    assert found, "no reingest variants found in the probe file"
    for variant in found:
        asserts = json.dumps(variant.get("turns", variant))
        assert "disposition" in asserts, \
            f"{variant['id']} does not check the QA verdict"


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
