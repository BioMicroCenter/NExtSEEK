"""The collector pulls from four sources keyed four different ways.

Every source is injected. A unit test must not need MySQL, docker, or a volume.
"""
import json
import pathlib

import pytest

from nessie_tests import collect
from nessie_tests.bayes_manifest import BayesManifest, BayesPair
from nessie_tests.manifest import NessieManifestEntry


class FakeSources:
    def __init__(self, rows=None, transcript=b"", copyable=()):
        self._rows = rows or {}
        self._transcript = transcript
        self._copyable = set(copyable)
        self.copied = []
        self.fetches = []

    def task_rows(self, task_ids):
        self.fetches.append(list(task_ids))
        return {t: self._rows[t] for t in task_ids if t in self._rows}

    def cc_transcript(self, session_id):
        return self._transcript or None

    def copy_tree(self, src, dest):
        self.copied.append((src, str(dest)))
        if src not in self._copyable:
            return False
        pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(dest) / "console.txt").write_text("trace", encoding="utf-8")
        return True


def _entry(vid, task_id, cost=None, **kw):
    # `task_ids`, not a `task_id=` stuffed into `reason`. The brief's Step 4 called
    # for the field; it is a LIST because an entry is a CASE, and 30 of the 127
    # variants in the paired selection run 2-3 turns.
    ids = kw.pop("task_ids", None)
    if ids is None:
        ids = [task_id] if task_id else []
    kw.setdefault("status", "passed")
    return NessieManifestEntry(id=vid, family="f", tier="full",
                               cost=cost, task_ids=ids, **kw)


def _manifest():
    return BayesManifest(run_meta={"mode": "bayesian"}, pairs=[
        BayesPair(id="a.one", family="f", hibayes_subtype="Search-Basic",
                  ns=_entry("a.one", "t-ns"), cc=_entry("a.one", "t-cc", cost=0.2)),
    ])


def test_layout_is_one_directory_per_variant_per_arm(tmp_path):
    src = FakeSources(rows={"t-ns": {"progress": [], "result": None}})
    collect.collect(_manifest(), tmp_path, src)
    assert (tmp_path / "artifacts" / "a.one" / "ns").is_dir()
    assert (tmp_path / "artifacts" / "a.one" / "cc").is_dir()


def test_task_row_is_written_per_arm(tmp_path):
    rows = {"t-ns": {"progress": [{"event": "query_complete"}], "result": {"reply": "hi"}}}
    collect.collect(_manifest(), tmp_path, FakeSources(rows=rows))
    task = json.loads((tmp_path / "artifacts" / "a.one" / "ns" / "task.json").read_text())
    assert task["result"]["reply"] == "hi"


def test_ns_run_root_is_taken_from_the_event_when_present(tmp_path):
    rows = {"t-ns": {"progress": [
        {"event": "ns_run_root", "data": {"run_root": "/app/outputs/260804_101500_demo"}}],
        "result": None}}
    src = FakeSources(rows=rows, copyable={"/app/outputs/260804_101500_demo"})
    collect.collect(_manifest(), tmp_path, src)
    assert (tmp_path / "artifacts" / "a.one" / "ns" / "run_root" / "console.txt").is_file()


def test_a_missing_artifact_is_recorded_rather_than_silently_absent(tmp_path):
    """An artifact that could not be COLLECTED and one that was never PRODUCED are
    different facts, and the grader has to be able to tell them apart."""
    rows = {"t-ns": {"progress": [
        {"event": "ns_run_root", "data": {"run_root": "/gone"}}], "result": None}}
    out = collect.collect(_manifest(), tmp_path, FakeSources(rows=rows))
    misses = [m for m in out["missing"] if m["what"] == "run_root"]
    assert misses and misses[0]["path"] == "/gone"
    assert misses[0]["reason"]


def test_no_ns_run_root_event_records_the_gap_explicitly(tmp_path):
    out = collect.collect(_manifest(), tmp_path,
                          FakeSources(rows={"t-ns": {"progress": [], "result": None}}))
    assert any(m["what"] == "ns_run_root_event" for m in out["missing"])


def test_cc_transcript_is_decompressed_to_jsonl(tmp_path):
    # importorskip, not a bare import: the prescribed host command is
    # `uv run --no-project --with pytest --with pydantic --with requests
    # --with beautifulsoup4`, and zstandard is in NONE of those. A bare import
    # here is a collection ERROR for the whole module, not a failure of this one
    # test. The wiring this test covers is ALSO covered without the dependency by
    # test_the_transcript_blob_reaches_session_jsonl_without_zstandard below, so
    # a skip here does not leave the path untested.
    zstandard = pytest.importorskip("zstandard")
    raw = b'{"type":"assistant"}\n'
    src = FakeSources(rows={}, transcript=zstandard.ZstdCompressor().compress(raw))
    collect.collect(_manifest(), tmp_path, src)
    assert (tmp_path / "artifacts" / "a.one" / "cc" / "session.jsonl").read_bytes() == raw


def test_collection_json_is_written_and_counts_both_arms(tmp_path):
    out = collect.collect(_manifest(), tmp_path, FakeSources())
    on_disk = json.loads((tmp_path / "collection.json").read_text())
    assert on_disk == out
    assert on_disk["arms_seen"] == 2


# --------------------------------------------------------------------------
# Multi-turn: an entry is a CASE, and a case is up to 3 turns in this selection.
# --------------------------------------------------------------------------

def _multiturn_manifest():
    return BayesManifest(run_meta={"mode": "bayesian"}, pairs=[
        BayesPair(id="a.one", family="refine_and_recall",
                  ns=_entry("a.one", None, task_ids=["t1", "t2"]),
                  cc=_entry("a.one", None, task_ids=["c1", "c2"])),
    ])


def test_task_json_is_the_FINAL_turn_because_that_is_the_answer_being_graded(tmp_path):
    """A follow-up turn's reply is the one under grade. Recording turn 1's answer
    for a refine_and_recall case answers a different question than the one asked."""
    rows = {
        "t1": {"status": "completed", "progress": [], "result": {"reply": "first"}},
        "t2": {"status": "completed", "progress": [], "result": {"reply": "final"}},
    }
    collect.collect(_multiturn_manifest(), tmp_path, FakeSources(rows=rows))
    task = json.loads((tmp_path / "artifacts" / "a.one" / "ns" / "task.json").read_text())
    assert task["result"]["reply"] == "final"


def test_every_turn_is_kept_not_only_the_one_that_carries_the_answer(tmp_path):
    rows = {
        "t1": {"status": "completed", "progress": [], "result": {"reply": "first"}},
        "t2": {"status": "completed", "progress": [], "result": {"reply": "final"}},
        "c1": {"status": "completed", "progress": [], "result": {"reply": "first"}},
        "c2": {"status": "completed", "progress": [], "result": {"reply": "final"}},
    }
    out = collect.collect(_multiturn_manifest(), tmp_path, FakeSources(rows=rows))
    turns = json.loads((tmp_path / "artifacts" / "a.one" / "ns" / "turns.json").read_text())
    assert [t["task_id"] for t in turns] == ["t1", "t2"]
    assert [t["row"]["result"]["reply"] for t in turns] == ["first", "final"]
    assert out["turns_seen"] == 4  # 2 per arm


def test_a_single_turn_arm_writes_no_turns_json(tmp_path):
    """Its absence is the signal that the arm ran exactly one turn. A file that is
    always present says nothing."""
    collect.collect(_manifest(), tmp_path,
                    FakeSources(rows={"t-ns": {"progress": [], "result": None}}))
    assert not (tmp_path / "artifacts" / "a.one" / "ns" / "turns.json").exists()


def test_a_missing_row_is_recorded_per_task_id_not_once_per_arm(tmp_path):
    rows = {"t1": {"status": "completed", "progress": [], "result": None}}
    out = collect.collect(_multiturn_manifest(), tmp_path, FakeSources(rows=rows))
    missed = {m["path"] for m in out["missing"] if m["what"] == "task_row"}
    assert missed == {"t2", "c1", "c2"}


def test_a_missing_row_does_not_forfeit_the_rest_of_the_arm(tmp_path):
    """The brief `continue`d on a missing row, which recorded ONE miss and then
    silently skipped the transcript and the scratch that were still collectable.
    Every miss is recorded; none of them ends the arm."""
    out = collect.collect(_manifest(), tmp_path, FakeSources(rows={}))
    what = {m["what"] for m in out["missing"] if m["arm"] == "cc"}
    assert "task_row" in what and "cc_transcript" in what


def test_an_entry_with_no_task_id_at_all_is_recorded(tmp_path):
    """A manifest written before `task_ids` existed carries none. That is a join
    failure, not an absence of artifacts, and it must not read as the latter."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f",
                                       ns=_entry("a.one", None, task_ids=[]))])
    out = collect.collect(m, tmp_path, FakeSources())
    assert [x["what"] for x in out["missing"] if x["what"] == "task_id"] == ["task_id"]


# --------------------------------------------------------------------------
# De-duplication. run_root is per chat SESSION, not per turn.
# --------------------------------------------------------------------------

def test_one_run_root_shared_by_several_turns_is_copied_once(tmp_path):
    """Proven live in task 1: DictSessionAdapter.save() persists run_root_dir,
    the next turn's adapter reloads it, and _ensure_query_log_dir returns early,
    so turns 2..n APPEND into turn 1's directory. Several task_ids legitimately
    map to one run_root; copying it once per turn is waste, not correctness."""
    ev = [{"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]
    rows = {"t1": {"status": "completed", "progress": ev, "result": None},
            "t2": {"status": "completed", "progress": ev, "result": None}}
    src = FakeSources(rows=rows, copyable={"/app/outputs/x"})
    out = collect.collect(_multiturn_manifest(), tmp_path, src)
    assert [c[0] for c in src.copied] == ["/app/outputs/x"]
    assert out["run_roots_copied"] == 1
    assert not [m for m in out["missing"] if m["what"] in ("run_root", "ns_run_root_event")]
    # De-duplicated in `_run_roots`, BEFORE the cross-arm owner map sees it. The
    # owner map would also stop the second copy, but it would file the arm's own
    # second turn as evidence SHARED with another variant, which is nonsense --
    # and it is the assertion below, not the copy count, that catches that.
    assert out["shared_run_roots"] == []
    assert not (tmp_path / "artifacts" / "a.one" / "ns" / "run_root.shared.json").exists()


def test_a_run_root_shared_across_arms_is_copied_once_and_the_sharing_is_recorded(tmp_path):
    """Each case opens its own session (force_new on turn 0), so this should not
    happen. If it does, two variants' evidence is the same directory and the
    grader must be told rather than handed two identical copies."""
    ev = [{"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]
    m = BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1")),
        BayesPair(id="a.two", family="f", ns=_entry("a.two", "t2")),
    ])
    rows = {"t1": {"status": "completed", "progress": ev, "result": None},
            "t2": {"status": "completed", "progress": ev, "result": None}}
    src = FakeSources(rows=rows, copyable={"/app/outputs/x"})
    out = collect.collect(m, tmp_path, src)
    assert [c[0] for c in src.copied] == ["/app/outputs/x"]
    assert out["shared_run_roots"] == [
        {"run_root": "/app/outputs/x", "copied_under": "a.one/ns", "also_claimed_by": "a.two/ns"}]
    pointer = json.loads(
        (tmp_path / "artifacts" / "a.two" / "ns" / "run_root.shared.json").read_text())
    assert pointer["copied_under"] == "a.one/ns"


def test_two_distinct_run_roots_on_one_arm_are_both_kept(tmp_path):
    """De-duplication is on the PATH, not on the arm. Two different directories
    are two different pieces of evidence."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f",
                                       ns=_entry("a.one", None, task_ids=["t1", "t2"]))])
    rows = {
        "t1": {"status": "completed", "result": None, "progress": [
            {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]},
        "t2": {"status": "completed", "result": None, "progress": [
            {"event": "ns_run_root", "data": {"run_root": "/app/outputs/y"}}]},
    }
    src = FakeSources(rows=rows, copyable={"/app/outputs/x", "/app/outputs/y"})
    out = collect.collect(m, tmp_path, src)
    assert [c[0] for c in src.copied] == ["/app/outputs/x", "/app/outputs/y"]
    assert out["run_roots_copied"] == 2
    assert (tmp_path / "artifacts" / "a.one" / "ns" / "run_root" / "console.txt").is_file()
    assert (tmp_path / "artifacts" / "a.one" / "ns" / "run_root_2" / "console.txt").is_file()


# --------------------------------------------------------------------------
# The one retry. ns_run_root necessarily lands AFTER query_complete.
# --------------------------------------------------------------------------

class LateEventSources(FakeSources):
    """The row goes terminal one save before `ns_run_root` is appended."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = 0

    def task_rows(self, task_ids):
        self.calls += 1
        if self.calls == 1:
            return {t: {"status": "completed", "progress": [], "result": None}
                    for t in task_ids}
        return {t: {"status": "completed", "result": None, "progress": [
            {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]}
            for t in task_ids}


def test_a_late_ns_run_root_is_recovered_by_one_retry(tmp_path):
    """query_complete and ns_run_root are two separate task.save() calls, so a
    collector chained onto the end of a run can read the row between them. The
    timestamp window is NOT the fallback: it is only unambiguous while runs are
    strictly sequential, and a paired run interleaves two engines per question."""
    slept = []
    src = LateEventSources(copyable={"/app/outputs/x"})
    out = collect.collect(BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1"))]),
        tmp_path, src, sleep=slept.append)
    assert src.calls == 2 and slept == [collect.RETRY_DELAY_S]
    assert out["retried_task_ids"] == 1
    assert not out["missing"]
    assert (tmp_path / "artifacts" / "a.one" / "ns" / "run_root" / "console.txt").is_file()


def test_the_retry_is_skipped_when_the_event_already_arrived(tmp_path):
    rows = {"t1": {"status": "completed", "result": None, "progress": [
        {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]}}
    src = FakeSources(rows=rows, copyable={"/app/outputs/x"})
    slept = []
    collect.collect(BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1"))]),
        tmp_path, src, sleep=slept.append)
    assert len(src.fetches) == 1 and slept == []


def test_a_non_terminal_row_is_not_retried(tmp_path):
    """Nothing is late about a turn that has not finished. Retrying it buys a
    second round-trip and the same answer, and would make every unit test sleep."""
    rows = {"t1": {"status": "running", "progress": [], "result": None}}
    src = FakeSources(rows=rows)
    slept = []
    out = collect.collect(BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1"))]),
        tmp_path, src, sleep=slept.append)
    assert len(src.fetches) == 1 and slept == []
    assert any(m["what"] == "ns_run_root_event" for m in out["missing"])


def test_the_retry_happens_at_most_once(tmp_path):
    """`retry once` and nothing more. A collector that keeps polling turns a
    systematic gap into a hang."""
    src = FakeSources(rows={"t1": {"status": "completed", "progress": [], "result": None}})
    slept = []
    out = collect.collect(BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1"))]),
        tmp_path, src, sleep=slept.append)
    assert len(src.fetches) == 2 and len(slept) == 1
    assert any(m["what"] == "ns_run_root_event" for m in out["missing"])


# --------------------------------------------------------------------------
# "nothing there" and "the copy broke" are different outcomes.
# --------------------------------------------------------------------------

class ExplodingCopier(FakeSources):
    def copy_tree(self, src, dest):
        raise collect.CopyFailed("docker: Cannot connect to the Docker daemon")


def test_a_broken_copier_is_distinguished_from_an_absent_source(tmp_path):
    """`copy_tree -> bool` cannot say which happened, and only one of the two is a
    COLLECTION problem. False means the source is not there; CopyFailed means the
    mechanism broke, and a run full of those is a broken collector, not a quiet
    product result."""
    rows = {"t1": {"status": "completed", "result": None, "progress": [
        {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]}}
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1"))])

    absent = collect.collect(m, tmp_path / "a", FakeSources(rows=rows))
    broken = collect.collect(m, tmp_path / "b", ExplodingCopier(rows=rows))

    assert [m_["kind"] for m_ in absent["missing"] if m_["what"] == "run_root"] == ["absent"]
    bad = [m_ for m_ in broken["missing"] if m_["what"] == "run_root"]
    assert [m_["kind"] for m_ in bad] == ["copy_failed"]
    assert "Docker daemon" in bad[0]["reason"]


def test_a_copy_failure_does_not_abort_the_collection(tmp_path):
    rows = {"t1": {"status": "completed", "result": None, "progress": [
        {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}]}}
    m = BayesManifest(pairs=[
        BayesPair(id="a.one", family="f", ns=_entry("a.one", "t1")),
        BayesPair(id="a.two", family="f", ns=_entry("a.two", "t1")),
    ])
    out = collect.collect(m, tmp_path, ExplodingCopier(rows=rows))
    assert out["arms_seen"] == 2
    assert len([x for x in out["missing"] if x["what"] == "run_root"]) == 2


# --------------------------------------------------------------------------
# CC: artifacts, scratch, transcript.
# --------------------------------------------------------------------------

def test_cc_artifacts_are_unioned_across_turns(tmp_path):
    """Each CC turn publishes its own set. The arm produced all of them."""
    rows = {
        "c1": {"status": "completed", "result": None, "progress": [
            {"event": "query_complete", "data": {"artifacts": [{"path": "/a"}],
                                                 "cc_raw_files": ["/raw/1"]}}]},
        "c2": {"status": "completed", "result": None, "progress": [
            {"event": "query_complete", "data": {"artifacts": [{"path": "/a"},
                                                               {"path": "/b"}],
                                                 "cc_raw_files": ["/raw/2"]}}]},
    }
    collect.collect(_multiturn_manifest(), tmp_path, FakeSources(rows=rows))
    got = json.loads(
        (tmp_path / "artifacts" / "a.one" / "cc" / "artifacts.json").read_text())
    assert got["artifacts"] == [{"path": "/a"}, {"path": "/b"}]
    assert got["cc_raw_files"] == ["/raw/1", "/raw/2"]


def test_cc_scratch_has_no_join_key_and_that_is_recorded_every_time(tmp_path):
    """NOTHING emits the scratch path. The brief read `cc_turn_meta.scratch_dir`
    and no such event exists anywhere in the product, so the lookup silently
    returned None and no miss was recorded -- a whole source quietly absent. A
    systematic collection problem must be visible, not look like a product result."""
    rows = {"t-cc": {"status": "completed", "progress": [
        {"event": "query_complete", "data": {}}], "result": None}}
    out = collect.collect(_manifest(), tmp_path, FakeSources(rows=rows))
    miss = [m for m in out["missing"] if m["what"] == "cc_scratch"]
    assert len(miss) == 1 and miss[0]["kind"] == "no_join_key"
    assert "cc_turn_meta" in miss[0]["reason"]


def test_a_scratch_path_is_copied_once_it_is_actually_emitted(tmp_path):
    rows = {"t-cc": {"status": "completed", "result": None, "progress": [
        {"event": "cc_turn_meta", "data": {"scratch_dir": "/data/scratch/u1"}}]}}
    src = FakeSources(rows=rows, copyable={"/data/scratch/u1"})
    out = collect.collect(_manifest(), tmp_path, src)
    assert ("/data/scratch/u1", str(tmp_path / "artifacts" / "a.one" / "cc" / "cc_scratch")
            ) in src.copied
    assert not [m for m in out["missing"] if m["what"] == "cc_scratch"]


def test_the_transcript_blob_reaches_session_jsonl_without_zstandard(tmp_path, monkeypatch):
    """Covers the WIRING (blob -> session.jsonl) in the prescribed host env, which
    has no zstandard. The round-trip against the real codec is
    test_cc_transcript_is_decompressed_to_jsonl, which skips there."""
    monkeypatch.setattr(collect, "decompress_transcript", lambda b: b.upper())
    collect.collect(_manifest(), tmp_path, FakeSources(transcript=b"jsonl"))
    assert (tmp_path / "artifacts" / "a.one" / "cc" / "session.jsonl").read_bytes() == b"JSONL"


def test_an_undecodable_transcript_is_recorded_not_raised(tmp_path):
    def boom(_blob):
        raise ValueError("not a zstd frame")

    m = _manifest()
    out = collect.collect(m, tmp_path, FakeSources(transcript=b"junk"),
                          decompress=boom)
    miss = [x for x in out["missing"] if x["what"] == "cc_transcript"]
    assert len(miss) == 1 and miss[0]["kind"] == "unreadable"
    assert "not a zstd frame" in miss[0]["reason"]


def test_the_chat_session_id_is_what_the_transcript_is_looked_up_by(tmp_path):
    """`make_db_event_callback` setdefaults `session_id` onto query_complete, and
    that is the NExtSEEK ChatSession id -- NOT `cc_session_id`, which is Claude's
    own and is a different value on the same event."""
    seen = []

    class Recording(FakeSources):
        def cc_transcript(self, session_id):
            seen.append(session_id)

    rows = {"t-cc": {"status": "completed", "progress": [
        {"event": "query_complete", "data": {"session_id": "chat-9",
                                             "cc_session_id": "cc-abc"}}],
        "result": {"session_id": "chat-9", "cc_session_id": "cc-abc"}}}
    collect.collect(_manifest(), tmp_path, Recording(rows=rows))
    assert seen == ["chat-9"]


# --------------------------------------------------------------------------
# Outages are excluded, never scored -- so they must be countable.
# --------------------------------------------------------------------------

def test_an_outage_arms_misses_are_tagged_so_they_do_not_read_as_a_collection_gap(tmp_path):
    """An outage arm ran no product code, so it legitimately produced nothing.
    Counting its empty hands among the collection failures is how a real
    systematic gap gets buried in noise."""
    m = BayesManifest(pairs=[BayesPair(
        id="a.one", family="f",
        ns=_entry("a.one", "t1", status="error", outage=True))])
    out = collect.collect(m, tmp_path, FakeSources())
    assert out["arms_outage"] == 1
    assert all(x["outage"] is True for x in out["missing"])


def test_a_normal_arms_misses_are_not_tagged_as_outage(tmp_path):
    out = collect.collect(_manifest(), tmp_path, FakeSources())
    assert out["missing"] and all(x["outage"] is False for x in out["missing"])


# --------------------------------------------------------------------------
# The join key on the entry.
# --------------------------------------------------------------------------

def test_run_case_records_every_turns_task_id():
    """The join must not depend on parsing `reason`, which is prose, and it must
    not stop at turn 0, which is not where a follow-up's answer is.

    Driven through a REAL multi-turn corpus variant, so the turn count comes from
    the corpus rather than from a hand-built double that could agree with a broken
    accumulator."""
    from nessie_tests import corpus, runner

    corpus_path = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"
    v = next(x for x in corpus.merged(corpus_path) if x.id == "refrec.refine_to_cd8")
    assert len(v.turns) > 1, "this test needs a multi-turn variant to mean anything"

    ids = iter([f"task-{i}" for i in range(1, 9)])

    def post_query(_body):
        return {"task_id": next(ids), "session_id": "s1"}

    def get_progress(_task_id):
        return {"status": "completed", "progress": [
            {"event": "query_complete", "data": {"reply": "ok", "session_id": "s1"}}]}

    entry = runner.run_case(v, tier="full", post_query=post_query,
                            get_progress=get_progress, sleep=lambda _s: None)
    assert entry.task_ids == [f"task-{i}" for i in range(1, len(v.turns) + 1)]
