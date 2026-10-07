"""Retirement removes a variant from the active corpus without losing it.

`DELETE` was rejected as a disposition in the issue-#35 review: a question that
is wrong today may be the right question once the data or the product changes,
and the reason it left is worth keeping next to it.

2026-10-07 test-set review (SPEC-2, REVIEW-RULINGS R1): every retired question lives in
`retired.json`, next to `corpus.json`, with its FULL BODY and a `retirement` record
(`decided_by`, `family`, `reason`, `retired_on`, `source`, and `duplicate_of` when a keeper
asks the question now). `corpus.json` holds the active variants only. Before that, 106
retired variants sat in `corpus.json` under `status: "retired"`, and the probe files and
run sets simply deleted their removals.

`retired.json` is a RECORD. Nothing selects cases from it, and no code path may read it to
decide what is active: a `retired.json` existed until 2026-08-04 and was deleted because
the corpus generator re-derived `status` from it and resurrected retirements. The only
readers are the metadata lookups (`variant_meta`, `load_all_definitions`) so that an old run
report naming a retired id still resolves; `test_only_the_metadata_lookups_read_retired_json`
pins that. Reinstating a question is copying its body back into `corpus.json` with
`status: "active"` and `retirement: null`, and deleting the record.
"""
import json
import re
from pathlib import Path

from NessieAI.tests.nessie_tests import corpus

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE / "corpus.json"
RETIRED = HERE / "retired.json"
BATCH_2026_10_07 = "2026-10-07 test-set review"


def _corpus_variants():
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    return [v for fam in payload["families"].values() for v in fam["variants"]]


def _records():
    return json.loads(RETIRED.read_text(encoding="utf-8"))["retired"]


def test_corpus_json_holds_active_variants_only():
    bad = {v["id"]: v["status"] for v in _corpus_variants() if v["status"] != "active"}
    assert bad == {}, f"retired or unrecognised variants left in corpus.json: {sorted(bad)}"


def test_no_active_variant_carries_a_stale_retirement_record():
    """A variant reinstated by hand must lose its record: otherwise the corpus claims a live
    case was retired on a date for a reason."""
    stale = {v["id"]: v["retirement"] for v in _corpus_variants() if v["retirement"] is not None}
    assert stale == {}, f"active variants still carrying a retirement record: {sorted(stale)}"


def test_no_id_is_both_active_and_retired_and_none_is_retired_twice():
    ids = [r["id"] for r in _records()]
    assert len(ids) == len(set(ids)), "an id appears twice in retired.json"
    both = {v["id"] for v in _corpus_variants()} & set(ids)
    assert both == set(), f"ids both active in corpus.json and retired: {sorted(both)}"


def test_every_record_is_complete():
    for r in _records():
        rec = r.get("retirement") or {}
        assert r.get("status") == "retired", f"{r['id']}: status {r.get('status')!r}"
        for key in ("decided_by", "family", "reason", "retired_on", "source"):
            assert rec.get(key), f"{r['id']} retired with no {key}"
        assert r.get("turns") and all(t.get("query") for t in r["turns"]), f"{r['id']}: no body to read back"


def test_this_reviews_removals_name_a_keeper_that_is_not_itself_retired():
    """Every removal of the 2026-10-07 review names the case that asks the question now, and
    there are no chains (a keeper that was itself removed)."""
    records = _records()
    retired_ids = {r["id"] for r in records}
    mine = [r for r in records if r["retirement"]["decided_by"].startswith(BATCH_2026_10_07)]
    assert len(mine) == 145, len(mine)
    for r in mine:
        keeper = r["retirement"].get("duplicate_of")
        assert keeper, f"{r['id']}: no keeper named"
        assert keeper not in retired_ids, f"{r['id']}: its keeper {keeper} is retired too"


def test_merged_excludes_retired_variants():
    merged = {v.id for v in corpus.merged(CORPUS)}
    retired = {r["id"] for r in _records()}
    assert retired, "guard: nothing retired means this test proves nothing"
    assert not (merged & retired), f"still active: {sorted(merged & retired)}"


def test_the_lookups_still_resolve_a_retired_id():
    meta = corpus.variant_meta(CORPUS)
    victim = _records()[0]["id"]
    assert meta[victim]["status"] == "retired"
    assert victim in {v.id for v in corpus.load_all_definitions(CORPUS)}


def test_only_the_metadata_lookups_read_retired_json():
    """No selection path may read retired.json (the 2026-08-04 trap). `_read_retired` is
    defined once in corpus.py and called only by the three lookups (load_all_definitions, variant_meta, hibayes_meta); no other harness module
    opens the file."""
    text = (HERE / "corpus.py").read_text(encoding="utf-8")
    assert len(re.findall(r"_read_retired\(", text)) == 4     # the def and its three callers
    callers = {m.group(1) for m in re.finditer(r"def (\w+)\(", text)
               if "_read_retired(" in text[m.end():].split("\ndef ")[0]} - {"_read_retired"}
    assert callers == {"load_all_definitions", "variant_meta", "hibayes_meta"}, callers
    for p in HERE.glob("*.py"):
        if p.name != "corpus.py":
            body = p.read_text(encoding="utf-8")
            assert "_read_retired" not in body, f"{p.name} reads retired.json"
            assert not re.search(r"open\([^)]*retired\.json|['\"]retired\.json['\"]\)", body), p.name


def test_gbm_is_gone_from_the_active_corpus():
    """The whole point of the 26 GBM retirements: no active question names GBM."""
    merged = corpus.merged(CORPUS)
    named = [v.id for v in merged
             for t in v.turns if "gbm" in (t.query or "").lower()]
    assert named == [], f"GBM questions still active: {named}"


def test_reinstating_is_copying_the_body_back_into_corpus_json(tmp_path):
    """Reinstatement is a data edit, not a code change: put a retired body back in a copy of
    corpus.json as active, with no record, and the loader picks it up.

    Drives the real loader over a copy of the real file, not a hand-built fixture."""
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    victim = next(r for r in _records() if r.get("origin"))        # a body that came out of the corpus
    body = {k: v for k, v in victim.items() if k not in ("lived_in", "was_family")}
    body["status"], body["retirement"] = "active", None
    assert body["id"] not in {v.id for v in corpus.merged(CORPUS)}
    next(iter(payload["families"].values()))["variants"].append(body)
    reinstated = tmp_path / "corpus.json"
    reinstated.write_text(json.dumps(payload), encoding="utf-8")

    before = len(corpus.curated(corpus.load_unified(CORPUS)))
    active = {v.id for v in corpus.curated(corpus.load_unified(reinstated))}
    assert victim["id"] in active
    assert len(active) == before + 1


def test_every_definition_has_a_status_the_loader_recognises():
    """`_to_variants` keeps a definition only when `status == "active"`, so a typo like
    `"retried"` would drop it from every run in silence."""
    bad = {vid: m["status"] for vid, m in corpus.variant_meta(CORPUS).items()
           if m["status"] not in ("active", "retired")}
    assert bad == {}, f"unrecognised status values: {bad}"
