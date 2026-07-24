from pathlib import Path
from nessie_tests import corpus

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_base_loads_and_is_tagged():
    base = corpus.load_base()
    assert len(base) >= 300
    assert all("base" in v.tags for v in base)


def test_overlay_loads_and_is_tagged():
    ov = corpus.load_overlay(OVERLAY)
    assert all("overlay" in v.tags for v in ov)


def test_merged_is_base_plus_overlay():
    base, ov = corpus.load_base(), corpus.load_overlay(OVERLAY)
    assert len(corpus.merged(OVERLAY)) == len(base) + len(ov)


def test_select_scope_specific_keeps_route_gate():
    merged = corpus.merged(OVERLAY)
    specific = corpus.select(merged, scope="specific")
    assert specific and all("route_gate" in v.tags for v in specific)


def test_select_by_family_and_variant():
    merged = corpus.merged(OVERLAY)
    fam = corpus.select(merged, family="search_advanced")
    assert fam and all(v.family == "search_advanced" for v in fam)
    one = corpus.select(merged, variant_id="advanced.basic_ndma")
    assert len(one) == 1 and one[0].id == "advanced.basic_ndma"
