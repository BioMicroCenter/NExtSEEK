"""nessie has FORKED chat_nextseek/e2e/catalog.json. This makes the fork explicit.

catalog.json still has ten other readers (chat_nextseek's own e2e suite, cli.py,
schema_helper.py, dmac_assistant/config.py), and startup/scripts/sync_chat_nextseek.sh
can replace it wholesale on the next vendored snapshot. When that happens, nessie's
corpus does not follow automatically and should not: adopting an upstream change is
a deliberate edit.

What this test buys is that the divergence is never SILENT.
"""
import json
import pathlib

from nessie_tests import corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
UNIFIED = ROOT / "corpus.json"


def test_recorded_catalog_hash_matches_the_vendored_file():
    """Fails the moment the vendored catalog changes at all.

    To resolve: diff the vendored catalog against what corpus.json carries for the
    ids it touches, hand-adopt what you want, then update provenance.catalog_sha256.
    Do NOT update the hash without looking at the diff; that turns this guard into a
    rubber stamp.

    There is no regenerate-and-diff shortcut any more. `scripts/build_corpus.py`,
    `overlay.json` and `retired.json` were deleted on 2026-08-04 with the 28-family
    remap: a generator whose source still carried the old 16-family taxonomy would
    have reverted the remap on every rebuild. corpus.json is hand-owned, so this
    test is now the ONLY thing that notices upstream moving, which is why it must
    stay strict.
    """
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    recorded = payload["provenance"]["catalog_sha256"]
    actual = corpus.sha256_of(corpus._BASE_CATALOG)
    assert recorded == actual, (
        f"chat_nextseek/e2e/catalog.json changed.\n"
        f"  recorded at adoption: {recorded}\n"
        f"  on disk now:          {actual}\n"
        f"See this test's docstring for how to resolve.")


def test_no_upstream_variant_is_missing_from_the_adopted_corpus():
    """A NEW upstream variant is the case the hash check alone reads as noise."""
    adopted = set(corpus.variant_meta(UNIFIED))
    upstream = {v.id for v in corpus.load_base()}
    missing = upstream - adopted
    assert not missing, (
        f"catalog.json has {len(missing)} variant(s) nessie has not adopted: "
        f"{sorted(missing)[:10]}")


def test_every_base_origin_variant_still_exists_upstream():
    """The reverse: a variant marked origin=base that upstream no longer has."""
    meta = corpus.variant_meta(UNIFIED)
    upstream = {v.id for v in corpus.load_base()}
    orphans = [vid for vid, m in meta.items() if m["origin"] == "base" and vid not in upstream]
    assert not orphans, (
        f"marked origin=base but gone from catalog.json: {sorted(orphans)[:10]}. "
        f"Either upstream deleted them (re-mark as origin=overlay to own them) or "
        f"the origin tag is wrong.")
