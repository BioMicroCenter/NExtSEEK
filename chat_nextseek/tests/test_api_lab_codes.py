from chat_nextseek.agents.api import _apply_lab_codes_to_search


def test_injects_lab_codes_into_searchtext():
    rb = {"filter_searchText": ""}
    out = _apply_lab_codes_to_search(rb, {"lab_codes": ["KAM"]})
    assert "KAM" in out["filter_searchText"]


def test_merges_with_existing_searchtext_no_duplicate():
    rb = {"filter_searchText": "OOC"}
    out = _apply_lab_codes_to_search(rb, {"lab_codes": ["KAM", "KAM"]})
    assert out["filter_searchText"].split().count("KAM") == 1
    assert "OOC" in out["filter_searchText"]


def test_no_lab_codes_leaves_body_unchanged():
    rb = {"filter_searchText": "OOC"}
    out = _apply_lab_codes_to_search(rb, {"lab_codes": []})
    assert out == {"filter_searchText": "OOC"}
