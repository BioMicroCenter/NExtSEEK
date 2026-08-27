"""The fetch shared by selection and resolve_samples, memoised per process."""
import pytest

from chat_nextseek.pipeline import metadata_cache


@pytest.fixture(autouse=True)
def _clean():
    metadata_cache.clear()
    yield
    metadata_cache.clear()


def _put(uids, tag):
    metadata_cache.put(uids, raw={"ok": True, "tag": tag}, annotated={"a": tag},
                       summary={"s": tag})


def test_miss_returns_none():
    assert metadata_cache.get(["D.SEQ-1"]) is None


def test_put_then_get_round_trips():
    _put(["D.SEQ-1", "D.SEQ-2"], "first")
    hit = metadata_cache.get(["D.SEQ-1", "D.SEQ-2"])
    assert hit["raw"]["tag"] == "first"
    assert hit["annotated"] == {"a": "first"}
    assert hit["summary"] == {"s": "first"}


def test_order_and_duplicates_do_not_change_the_key():
    _put(["D.SEQ-2", "D.SEQ-1"], "first")
    assert metadata_cache.get(["D.SEQ-1", "D.SEQ-2", "D.SEQ-1"]) is not None


def test_a_different_uid_set_is_a_different_entry():
    _put(["D.SEQ-1"], "first")
    assert metadata_cache.get(["D.SEQ-1", "D.SEQ-3"]) is None


def test_empty_uids_never_caches():
    metadata_cache.put([], raw={"ok": True}, annotated={}, summary={})
    assert metadata_cache.get([]) is None


def test_oldest_entry_is_evicted_past_the_bound():
    for i in range(metadata_cache.MAX_ENTRIES + 1):
        _put([f"D.SEQ-{i}"], str(i))
    assert metadata_cache.get(["D.SEQ-0"]) is None
    assert metadata_cache.get([f"D.SEQ-{metadata_cache.MAX_ENTRIES}"]) is not None


def test_reput_refreshes_recency():
    for i in range(metadata_cache.MAX_ENTRIES):
        _put([f"D.SEQ-{i}"], str(i))
    metadata_cache.get(["D.SEQ-0"])          # touch the oldest
    _put(["D.SEQ-new"], "new")               # forces one eviction
    assert metadata_cache.get(["D.SEQ-0"]) is not None
    assert metadata_cache.get(["D.SEQ-1"]) is None
