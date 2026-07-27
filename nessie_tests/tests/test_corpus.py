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


def test_merged_is_base_plus_overlay_minus_overrides():
    base, ov = corpus.load_base(), corpus.load_overlay(OVERLAY)
    overrides = corpus.overridden_ids(OVERLAY)
    merged = corpus.merged(OVERLAY)
    assert len(merged) == len(base) + len(ov) - len(overrides)
    assert len({v.id for v in merged}) == len(merged)  # no duplicate ids


def test_overlay_variant_overrides_the_base_variant_of_the_same_id(monkeypatch):
    """An overlay entry may REPLACE a base entry, keeping its id.

    This is how a weak imported expectation gets strengthened without editing
    the vendored catalog: same case, same id, better criteria.
    """
    from e2e.catalog import Variant, Turn
    base = [
        Variant(family="refine_and_recall", id="refrec.x", name="n", tags=["base"],
                turns=[Turn(label="m", query="q",
                            pass_criteria=[{"field": "parser_plan.mode", "op": "eq", "value": "x"}])]),
        Variant(family="other", id="keep.me", name="n", tags=["base"], turns=[Turn(label="m", query="q")]),
    ]
    strong = Variant(family="refine_and_recall", id="refrec.x", name="n", tags=["overlay"],
                     turns=[Turn(label="m", query="q",
                                 pass_criteria=[{"field": "api_ok", "op": "true"}])])
    monkeypatch.setattr(corpus, "load_base", lambda: base)
    monkeypatch.setattr(corpus, "load_overlay", lambda p: [strong])

    merged = corpus.merged(OVERLAY)
    assert [v.id for v in merged] == ["refrec.x", "keep.me"]  # base ordering preserved
    replaced = merged[0]
    assert replaced.turns[0].pass_criteria[0].field == "api_ok"


def test_overridden_refine_and_recall_cases_assert_outcomes_not_labels():
    """Guard against regressing to mode-label-only assertions.

    Every refine/recall case in the 2026-07-24 run passed while answering from
    the wrong result bundle, because the only criterion was parser_plan.mode.
    """
    merged = {v.id: v for v in corpus.merged(OVERLAY)}
    overridden = [merged[i] for i in corpus.overridden_ids(OVERLAY)]
    refrec = [v for v in overridden if v.family == "refine_and_recall"]
    assert refrec, "expected the overlay to strengthen the refine_and_recall cases"
    for v in refrec:
        fields = {c.field for turn in v.turns for c in turn.pass_criteria}
        assert fields - {"parser_plan.mode"}, f"{v.id} still only asserts the classifier label"


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
