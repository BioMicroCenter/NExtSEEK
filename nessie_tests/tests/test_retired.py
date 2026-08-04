"""Retirement removes a variant from the active corpus without losing it.

`DELETE` was rejected as a disposition in the issue-#35 review: a question that
is wrong today may be the right question once the data or the product changes,
and the reason it left is worth keeping next to it. So a retired variant keeps
its full definition in `corpus.json` under `status: "retired"`, next to a
`retirement` record saying why and when — and `merged()` drops it.

Until 2026-08-04 that record lived in a separate `retired.json`, whose `retired`
map listed the ids and whose `families` block held the bodies. THREE guards here
existed only because those two halves could disagree: a typo'd id retired
nothing, a kept body with no matching id read as gone when it was not, and a
retired id could lack a definition entirely. None is expressible now — the
status IS on the definition, so there are no longer two halves to disagree.

They are replaced by the two failures the new shape does admit: an unrecognised
`status` string, and a `status`/`retirement` pair that contradict each other.
Both are live risks now: `corpus.json` is hand-owned, and `scripts/build_corpus.py`,
`overlay.json` and `retired.json` were deleted with the 28-family remap on
2026-08-04. Retirement is therefore SYMMETRIC again -- flipping `status` back to
`active` and clearing `retirement` is the whole reinstatement, with no second file
to keep in step and no rebuild to undo it.
"""
import json
from pathlib import Path

from nessie_tests import corpus

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE / "corpus.json"


def _meta():
    return corpus.variant_meta(CORPUS)


def _retired_ids():
    return {vid for vid, m in _meta().items() if m["status"] == "retired"}


def test_no_active_variant_carries_a_stale_retirement_record():
    """The reinstatement half of the invariant, and the half nothing else checks.

    `test_a_retired_variant_keeps_its_full_definition` stood here until the
    2026-08-04 review, asserting `retired_ids <= {v.id for v in
    load_all_definitions()}`. Both sides iterate the same raw variant list in the
    same file, so a retired id without a definition was not EXPRESSIBLE — the
    reviewer stripped `turns` from all 100 retired bodies and it still ran green.
    It was a survivor of the three-file layout, where the ids and the bodies
    really did live in two blocks that could disagree, rewritten when it should
    have been retired alongside `test_unknown_retired_id_is_loud`. The concept it
    meant to guard is genuinely covered by
    `test_unified_corpus.py::test_every_retired_definition_is_still_loadable`,
    which asserts all 100 bodies have turns and non-empty queries.

    What replaces it is the one direction nothing asserted and the unified shape
    CAN violate. Reinstating is `status: "retired"` -> `"active"`, and the
    `retirement` record sits right beside it: flip one, forget the other, and the
    corpus claims a live case was retired on a date for a reason. `merged()` would
    return it and every report would carry the contradiction. Generated files
    could not drift, because the deleted generator derived both from one source.
    `corpus.json` is hand-owned, which is exactly what makes a half-finished
    reinstatement possible.

    Not a restatement of `test_every_retirement_records_why`: that one checks the
    RETIRED side's record is complete, this one checks the ACTIVE side has none.
    """
    stale = {vid: m["retirement"] for vid, m in _meta().items()
             if m["status"] == "active" and m["retirement"] is not None}
    assert stale == {}, (
        f"active variants still carrying a retirement record: {sorted(stale)}. "
        f"Reinstating means clearing the record as well as flipping the status.")


def test_every_retirement_records_why():
    for vid, meta in _meta().items():
        if meta["status"] != "retired":
            continue
        rec = meta["retirement"] or {}
        assert rec.get("reason"), f"{vid} retired with no reason"
        assert rec.get("retired_on"), f"{vid} retired with no date"


def test_merged_excludes_retired_variants():
    merged = {v.id for v in corpus.merged(CORPUS)}
    retired = _retired_ids()
    assert retired, "guard: nothing retired means this test proves nothing"
    assert not (merged & retired), f"still active: {sorted(merged & retired)}"


def test_gbm_is_gone_from_the_active_corpus():
    """The whole point of the 26 GBM retirements: no active question names GBM."""
    merged = corpus.merged(CORPUS)
    named = [v.id for v in merged
             for t in v.turns if "gbm" in (t.query or "").lower()]
    assert named == [], f"GBM questions still active: {named}"


def test_retirement_can_be_reversed_by_flipping_the_status(tmp_path):
    """Reinstating is a data edit, not a code change — TO THE LOADER.

    Scope: this proves that `corpus.load_unified` picks the variant back up, and
    since 2026-08-04 that is the WHOLE story. Reinstatement used to be a two-file
    edit whose corpus.json half a rebuild would silently revert; with the generator
    and `retired.json` deleted there is nothing left to revert it, so the asymmetric
    pair of rebuild tests that used to sit here went with them.

    Drives the real loader over a copy of the real file, so what is proved is
    that the harness picks the variant back up — not that a hand-built fixture
    round-trips.

    Clears `retirement` as well as flipping `status`, because that is what a
    reinstatement IS: leaving the record behind builds the exact contradiction
    `test_no_active_variant_carries_a_stale_retirement_record` exists to reject,
    and this test would then be modelling the mistake rather than the edit.
    """
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    victim = next(v for fam in payload["families"].values()
                  for v in fam["variants"] if v["status"] == "retired")
    assert victim["id"] not in {v.id for v in corpus.merged(CORPUS)}

    victim["status"] = "active"
    victim["retirement"] = None
    reinstated = tmp_path / "corpus.json"
    reinstated.write_text(json.dumps(payload), encoding="utf-8")

    active = {v.id for v in corpus.curated(corpus.load_unified(reinstated))}
    assert victim["id"] in active
    assert len(active) == 284


def test_every_definition_has_a_status_the_loader_recognises():
    """The one way the unified shape can still lose a variant silently.

    `_to_variants` keeps a definition only when `status == "active"`, so a typo
    like `"retried"` drops it from every run while `variant_meta` reports it as
    neither active nor retired. Nothing else would say a word — the same failure
    the old `check_retired_ids` typo guard existed to prevent, in the only form
    the new layout can express it.
    """
    bad = {vid: m["status"] for vid, m in _meta().items()
           if m["status"] not in ("active", "retired")}
    assert bad == {}, f"unrecognised status values: {bad}"
