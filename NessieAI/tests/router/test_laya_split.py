import hashlib, json

from scripts.laya import split


def _h(salt, s):
    return int(hashlib.sha256((salt + s).encode()).hexdigest()[:8], 16) % 100


def test_heldout_bucket_is_deterministic_and_uses_the_spec_salt():
    for fam, ent in [("engine_routing", "PIPELINE"), ("catalog_browse", "NHP"), ("x", "y")]:
        want = _h("jevlev-routing-heldout-v1", fam) < split.FAMILY_PCT or _h(
            "jevlev-routing-heldout-v1", ent) < split.ENTITY_PCT
        assert split.heldout_bucket(fam, ent) is want
        assert split.heldout_bucket(fam, ent) is split.heldout_bucket(fam, ent)


def test_heldout_bucket_family_or_entity_and_unlabelled_never_held():
    fams = [f"f{i}" for i in range(200)]
    held_f = [f for f in fams if _h("jevlev-routing-heldout-v1", f) < split.FAMILY_PCT][0]
    free_f = [f for f in fams if _h("jevlev-routing-heldout-v1", f) >= split.FAMILY_PCT][0]
    held_e = [e for e in fams if _h("jevlev-routing-heldout-v1", e) < split.ENTITY_PCT][0]
    free_e = [e for e in fams if _h("jevlev-routing-heldout-v1", e) >= split.ENTITY_PCT][0]
    assert split.heldout_bucket(held_f, free_e)
    assert split.heldout_bucket(free_f, held_e)
    assert not split.heldout_bucket(free_f, free_e)
    assert not split.heldout_bucket("unlabelled", "none")


def test_calib_bucket_is_15_percent_with_the_spec_salt():
    ids = [f"chat-{i}" for i in range(2000)]
    got = [split.calib_bucket(i) for i in ids]
    assert got == [_h("jevlev-routing-calib-v1", i) < 15 for i in ids]
    assert 0.11 < sum(got) / len(got) < 0.19


def test_load_manifest_reads_hashes_only(tmp_path):
    p = tmp_path / "m.jsonl"
    rows = [{"hash": "a" * 64, "split": "heldout", "family": "f", "entity": "e", "route": "container_cc",
             "truth_kind": "human"}, {"hash": "b" * 64, "split": "heldout", "family": "f", "entity": "e",
                                      "route": "either", "truth_kind": "family"}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert split.load_manifest(p) == {"a" * 64, "b" * 64}
