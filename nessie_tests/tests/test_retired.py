"""Retirement removes a variant from the active corpus without losing it.

`DELETE` was rejected as a disposition in the issue-#35 review: a question that
is wrong today may be the right question once the data or the product changes,
and the reason it left is worth keeping next to it. So a retired variant keeps
its full definition in `corpus.json` under `status: "retired"`, next to a
`retirement` record saying why and when — and `merged()` drops it.

Until 2026-08-04 that record lived in a separate `retired.json`, whose `retired`
map listed the ids and whose `families` block held the bodies. Two guards here
existed only because those two halves could disagree: a typo'd id retired
nothing, and a kept body with no matching id read as gone when it was not. Both
are structurally impossible now — the status IS on the definition — so they are
replaced by the one failure the new shape does admit, an unrecognised status.
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


def test_a_retired_variant_keeps_its_full_definition():
    ids = _retired_ids()
    assert ids, "corpus.json should mark at least the issue-#35 retirements retired"
    # every retired id also keeps its full definition, so it can come back
    kept = {v.id for v in corpus.load_all_definitions(CORPUS)}
    assert ids <= kept, f"retired without keeping the definition: {sorted(ids - kept)}"


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
    """Reinstating is a data edit, not a code change.

    Drives the real loader over a copy of the real file with ONE status flipped,
    so what is proved is that the harness picks the variant back up — not that a
    hand-built fixture round-trips.
    """
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    victim = next(v for fam in payload["families"].values()
                  for v in fam["variants"] if v["status"] == "retired")
    assert victim["id"] not in {v.id for v in corpus.merged(CORPUS)}

    victim["status"] = "active"
    reinstated = tmp_path / "corpus.json"
    reinstated.write_text(json.dumps(payload), encoding="utf-8")

    active = {v.id for v in corpus.load_unified(reinstated)}
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
