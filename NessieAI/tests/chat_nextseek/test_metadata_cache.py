"""The fetch shared by selection and resolve_samples, memoised per process."""
import pytest

from chat_nextseek.pipeline import metadata_cache


@pytest.fixture(autouse=True)
def _clean():
    metadata_cache.clear()
    yield
    metadata_cache.clear()


def _put(uids, tag, identity=None):
    metadata_cache.put(uids, identity, raw={"ok": True, "tag": tag}, annotated={"a": tag},
                       summary={"s": tag})


def test_miss_returns_none():
    assert metadata_cache.get(["D.SEQ-1"], None) is None


def test_put_then_get_round_trips():
    _put(["D.SEQ-1", "D.SEQ-2"], "first")
    hit = metadata_cache.get(["D.SEQ-1", "D.SEQ-2"], None)
    assert hit["raw"]["tag"] == "first"
    assert hit["annotated"] == {"a": "first"}
    assert hit["summary"] == {"s": "first"}


def test_order_and_duplicates_do_not_change_the_key():
    _put(["D.SEQ-2", "D.SEQ-1"], "first")
    assert metadata_cache.get(["D.SEQ-1", "D.SEQ-2", "D.SEQ-1"], None) is not None


def test_a_different_uid_set_is_a_different_entry():
    _put(["D.SEQ-1"], "first")
    assert metadata_cache.get(["D.SEQ-1", "D.SEQ-3"], None) is None


def test_a_different_identity_is_a_different_entry_with_the_same_uids():
    """The credential-scoping fix: same UID set, two identities, two entries —
    and neither identity's fetch is served to the other."""
    _put(["D.SEQ-1"], "alice-data", identity="alice")
    _put(["D.SEQ-1"], "bob-data", identity="bob")

    alice_hit = metadata_cache.get(["D.SEQ-1"], "alice")
    bob_hit = metadata_cache.get(["D.SEQ-1"], "bob")

    assert alice_hit["raw"]["tag"] == "alice-data"
    assert bob_hit["raw"]["tag"] == "bob-data"
    # Neither identity's cache entry leaked into the other's.
    assert alice_hit["raw"]["tag"] != bob_hit["raw"]["tag"]
    # A third, uncached identity on the same UIDs is still a miss.
    assert metadata_cache.get(["D.SEQ-1"], "carol") is None


def test_empty_uids_never_caches():
    metadata_cache.put([], None, raw={"ok": True}, annotated={}, summary={})
    assert metadata_cache.get([], None) is None


def test_oldest_entry_is_evicted_past_the_bound():
    for i in range(metadata_cache.MAX_ENTRIES + 1):
        _put([f"D.SEQ-{i}"], str(i))
    assert metadata_cache.get(["D.SEQ-0"], None) is None
    assert metadata_cache.get([f"D.SEQ-{metadata_cache.MAX_ENTRIES}"], None) is not None


def test_reput_refreshes_recency():
    for i in range(metadata_cache.MAX_ENTRIES):
        _put([f"D.SEQ-{i}"], str(i))
    metadata_cache.get(["D.SEQ-0"], None)    # touch the oldest
    _put(["D.SEQ-new"], "new")               # forces one eviction
    assert metadata_cache.get(["D.SEQ-0"], None) is not None
    assert metadata_cache.get(["D.SEQ-1"], None) is None


def test_resolve_samples_reuses_what_selection_fetched(monkeypatch):
    """The whole point of the cache: two tools, one fetch."""
    import json

    from chat_nextseek.pipeline import agent_tools

    fetches = []

    def fake_fetch(config, uids):
        fetches.append(list(uids))
        return {"ok": True, "samples": {}}

    monkeypatch.setattr(agent_tools, "fetch_reporter_metadata", fake_fetch)
    monkeypatch.setattr(agent_tools, "annotate_metadata_with_sampletypes",
                        lambda config, raw: {"annotated": True})
    monkeypatch.setattr(agent_tools, "build_metadata_summary", lambda bundle: {})
    monkeypatch.setattr(agent_tools, "enumerate_lineage_leaves", lambda annotated, accepted_types: [])

    uids = ["D.SEQ-1", "D.SEQ-2"]
    agent_tools._fetch_annotate_summarise(object(), uids)       # selection's fetch
    out = json.loads(agent_tools.tool_resolve_samples(
        object(), {}, {}, {"kind": "explicit_uids", "uids": uids}, "rnaseq"))

    assert out["ok"] is True
    assert len(fetches) == 1


def test_a_failed_fetch_is_not_cached(monkeypatch):
    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "fetch_reporter_metadata",
                        lambda config, uids: {"ok": False, "error": "503"})
    with pytest.raises(RuntimeError, match="503"):
        agent_tools._fetch_annotate_summarise(object(), ["D.SEQ-1"])
    assert metadata_cache.get(["D.SEQ-1"], None) is None
