"""Retirement removes a variant from the active corpus without losing it.

`DELETE` was rejected as a disposition in the issue-#35 review: a question that
is wrong today may be the right question once the data or the product changes,
and the reason it left is worth keeping next to it. So a retired variant moves
to `retired.json` with its full definition and a reason, and `merged()` drops it.
"""
from pathlib import Path

import pytest

from nessie_tests import corpus

HERE = Path(__file__).resolve().parents[1]
OVERLAY = HERE / "overlay.json"
RETIRED = HERE / "retired.json"


def test_retired_file_exists_and_is_shaped_like_an_overlay():
    ids = corpus.load_retired_ids(RETIRED)
    assert ids, "retired.json should list at least the issue-#35 retirements"
    # every retired id also keeps its full definition, so it can come back
    kept = {v.id for v in corpus.load_overlay(RETIRED)}
    assert ids <= kept, f"retired without keeping the definition: {sorted(ids - kept)}"


def test_every_retirement_records_why():
    import json
    payload = json.loads(RETIRED.read_text(encoding="utf-8"))
    for vid, meta in payload["retired"].items():
        assert meta.get("reason"), f"{vid} retired with no reason"
        assert meta.get("retired_on"), f"{vid} retired with no date"


def test_merged_excludes_retired_variants():
    merged = {v.id for v in corpus.merged(OVERLAY)}
    retired = corpus.load_retired_ids(RETIRED)
    assert retired, "guard: nothing retired means this test proves nothing"
    assert not (merged & retired), f"still active: {sorted(merged & retired)}"


def test_gbm_is_gone_from_the_active_corpus():
    """The whole point of the 26 GBM retirements: no active question names GBM."""
    merged = corpus.merged(OVERLAY)
    named = [v.id for v in merged
             for t in v.turns if "gbm" in (t.query or "").lower()]
    assert named == [], f"GBM questions still active: {named}"


def test_retirement_can_be_reversed_by_emptying_the_list(tmp_path):
    """Reinstating is a data edit, not a code change."""
    import json
    payload = json.loads(RETIRED.read_text(encoding="utf-8"))
    empty = tmp_path / "none-retired.json"
    empty.write_text(json.dumps({**payload, "retired": {}}), encoding="utf-8")
    assert corpus.load_retired_ids(empty) == set()


def test_unknown_retired_id_is_loud():
    """A typo in retired.json must not silently retire nothing."""
    with pytest.raises(ValueError, match="not found"):
        corpus.check_retired_ids({"nope.not_a_case"}, {"graph.assay_flow_protocols"})


def test_every_retired_id_matches_a_real_variant():
    """The typo guard, against the real corpus.

    It lives here rather than inside merged() because merged() is also called
    with a monkeypatched base in unit tests, where a real retired id correctly
    matches nothing. Running it every test run is what makes silence safe.
    """
    known = {v.id for v in corpus.load_base()} | {v.id for v in corpus.load_overlay(OVERLAY)}
    corpus.check_retired_ids(corpus.load_retired_ids(RETIRED), known)
