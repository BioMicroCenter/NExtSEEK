"""The one concrete `collect.Sources`.

Everything here drives an INJECTED runner. No test starts a process, opens a
socket, or needs docker, MySQL or the volume -- the same rule the collector's own
suite keeps one level up.
"""
import base64
import io
import json
import pathlib
import tarfile

import pytest

from nessie_tests import collect, sources


# --------------------------------------------------------------------------- #
# the fake transport
# --------------------------------------------------------------------------- #
class FakeRunner:
    """Records every argv and replays canned `Completed`s in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.timeouts = []

    def __call__(self, argv, *, timeout=None):
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
        if not self.results:
            return sources.Completed(0, b"", b"")
        return self.results.pop(0)

    @property
    def script(self):
        """The decoded bash of the LAST call."""
        return _decode_script(self.calls[-1])

    @property
    def python(self):
        """The Python the LAST call pipes into the container's stdin."""
        b64 = self.script.split("printf %s ")[1].split()[0].strip("'")
        return base64.b64decode(b64).decode("utf-8")

    @property
    def request(self):
        """The request object the LAST call carries to the container."""
        b64 = self.python.split('base64.b64decode("')[1].split('"')[0]
        return json.loads(base64.b64decode(b64))


def _decode_script(argv):
    b64 = argv[-1].strip('"').split()[1]
    return base64.b64decode(b64).decode("utf-8")


def _payload(obj, *, before=b"", rc=0, stderr=b""):
    return sources.Completed(
        rc, before + f"{sources.MARKER}\n{json.dumps(obj)}\n".encode(), stderr)


def _tar(entries, root="b"):
    """A `docker cp`-shaped archive: everything under one top-level `root/`."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(root)
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(info)
        for name, body in entries.items():
            item = tarfile.TarInfo(f"{root}/{name}")
            item.size, item.mode = len(body), 0o644
            tf.addfile(item, io.BytesIO(body))
    return buf.getvalue()


def _copy_out(archive=None, status=sources.COPIED):
    payload = base64.b64encode(archive).decode() if archive else ""
    return sources.Completed(
        0, f"{payload}\n@@@COPY:{status}@@@\n".encode(), b"")


# --------------------------------------------------------------------------- #
# the transport itself
# --------------------------------------------------------------------------- #
def test_an_empty_host_goes_to_the_local_docker_daemon_not_to_ssh():
    """`ssh localhost` is not the local path; there is no sshd. Same reason
    `fetch_run.py` grew an empty `--host`, and the same shape."""
    run = FakeRunner(_payload({}))
    sources.DockerSources(run=run).task_rows(["a"])

    argv = run.calls[0]
    assert argv[0] == "bash" and argv[1] == "-c"
    assert "ssh" not in argv


def test_a_host_ssh_es_first_and_a_user_sudo_s_there():
    run = FakeRunner(_payload({}))
    sources.DockerSources(host="fairdata-dev", user="service-account",
                          run=run).task_rows(["a"])

    argv = run.calls[0]
    assert argv[:4] == ["ssh", "-o", "ConnectTimeout=30", "fairdata-dev"]
    assert argv[4:7] == ["sudo", "-n", "-u", "service-account"][:3]
    assert "service-account" in argv


def test_a_host_without_a_user_skips_sudo():
    run = FakeRunner(_payload({}))
    sources.DockerSources(host="box", run=run).task_rows(["a"])

    assert "sudo" not in run.calls[0]


def test_the_script_is_base64_so_ssh_and_sudo_quoting_cannot_mangle_it():
    run = FakeRunner(_payload({}))
    sources.DockerSources(run=run).task_rows(["a"])

    assert "base64 -d" in run.calls[0][-1]
    # It decodes to real bash that names the container.
    assert "docker exec -i nextseek" in run.script


def test_the_container_name_is_configurable_and_reaches_the_command():
    run = FakeRunner(_payload({}))
    sources.DockerSources(container="nextseek-2", run=run).task_rows(["a"])

    assert "docker exec -i nextseek-2" in run.script


def test_nothing_is_written_inside_the_container():
    """This is a READ of a live stack. The script is piped into stdin rather than
    dropped somewhere under /tmp for someone else to find."""
    run = FakeRunner(_payload({}))
    sources.DockerSources(run=run).task_rows(["a"])

    script = run.script
    assert "| base64 -d | docker exec -i" in script
    assert " > /" not in script and "mktemp" not in script


def test_the_timeout_reaches_the_runner():
    run = FakeRunner(_payload({}))
    sources.DockerSources(run=run, timeout=12).task_rows(["a"])

    assert run.timeouts == [12]


# --------------------------------------------------------------------------- #
# task_rows
# --------------------------------------------------------------------------- #
def test_task_rows_returns_the_contract_shape():
    row = {"status": "completed", "progress": [{"event": "e", "data": {}}],
           "result": {"reply": "hi"}}
    run = FakeRunner(_payload({"t-1": row}))

    assert sources.DockerSources(run=run).task_rows(["t-1"]) == {"t-1": row}


def test_an_id_with_no_row_is_absent_rather_than_invented():
    run = FakeRunner(_payload({"t-1": {"status": "completed", "progress": [],
                                       "result": None}}))

    got = sources.DockerSources(run=run).task_rows(["t-1", "t-2"])

    assert "t-2" not in got


def test_the_whole_batch_is_one_round_trip():
    """254 ids is 254 container starts if this is done per id."""
    run = FakeRunner(_payload({}))

    sources.DockerSources(run=run).task_rows([f"t-{i}" for i in range(254)])

    assert len(run.calls) == 1


def test_no_ids_asks_the_container_nothing():
    run = FakeRunner()

    assert sources.DockerSources(run=run).task_rows([]) == {}
    assert run.calls == []


# --------------------------------------------------------------------------- #
# the projection -- the other decision the container runs and this side owns
# --------------------------------------------------------------------------- #
_A = "1d3243fb-eb9b-423a-a06f-70dc79c02b18"
_B = "bb0528b9-d42b-4dd1-accf-f0618fc52434"


def _db_row(tid, **kw):
    return dict({"task_id": tid, "status": "completed", "progress": [],
                 "result": None}, **kw)


def test_an_id_the_database_did_not_answer_for_gets_no_row_invented():
    """A placeholder row is how "the run lost this turn" becomes "the turn
    completed with no events" four steps later, on a page a human is grading."""
    got = sources.project_task_rows([_A, _B], [_db_row(_A)])

    assert list(got) == [_A]


def test_the_projection_is_driven_by_the_rows_not_by_the_ids_asked_for():
    assert sources.project_task_rows([_A], []) == {}
    assert sources.project_task_rows([], [_db_row(_A)]) == {}


def test_the_row_carries_exactly_the_three_fields_QueryTask_stores():
    """`status`, `progress` and `result` are `QueryTask`'s own names, so this is
    a projection and not a derivation -- there is no fourth field to invent."""
    row = sources.project_task_rows(
        [_A], [_db_row(_A, progress=[{"event": "e", "data": {}}],
                       result={"reply": "x"})])[_A]

    assert sorted(row) == ["progress", "result", "status"]
    assert row == {"status": "completed", "progress": [{"event": "e", "data": {}}],
                   "result": {"reply": "x"}}


def test_the_answer_comes_back_under_the_id_the_caller_asked_with():
    """The row's `task_id` is a UUID whose str() is canonical lowercase; keying
    on that loses any id the manifest spelled differently."""
    got = sources.project_task_rows([_A.upper()], [_db_row(_A)])

    assert list(got) == [_A.upper()]


def test_a_malformed_id_does_not_cost_the_others_their_rows():
    """A UUIDField lookup raises on the whole batch; one bad id must not."""
    assert sources.canonical_task_ids(["not-a-uuid", _A, None]) == {_A: [_A]}
    assert sources.project_task_rows(["not-a-uuid", _A], [_db_row(_A)]) == {
        _A: {"status": "completed", "progress": [], "result": None}}


def test_the_projection_the_container_runs_is_the_one_under_test_here():
    script = sources._container_script({"op": "task_rows", "task_ids": []}, "/app")

    assert "def canonical_task_ids(requested):" in script
    assert "def project_task_rows(requested, rows):" in script
    assert "OUT = project_task_rows(" in script


def test_the_request_travels_base64_inside_the_script():
    run = FakeRunner(_payload({}))
    sources.DockerSources(run=run).task_rows(["t-1"])

    assert run.request == {"op": "task_rows", "task_ids": ["t-1"]}


# --------------------------------------------------------------------------- #
# cc_transcript
# --------------------------------------------------------------------------- #
def test_cc_transcript_decodes_the_base64_back_to_raw_bytes():
    """Binary cannot travel over a line-oriented stdout shared with markers."""
    blob = b"\x28\xb5\x2f\xfd\x00\n\r\x00binary"
    run = FakeRunner(_payload(base64.b64encode(blob).decode()))

    assert sources.DockerSources(run=run).cc_transcript("s-1") == blob


def test_no_transcript_is_none_not_empty_bytes():
    run = FakeRunner(_payload(None))

    assert sources.DockerSources(run=run).cc_transcript("s-1") is None


def test_a_blank_session_id_asks_the_container_nothing():
    """`collect` passes "" when no turn carried a session id. The answer is the
    same either way and a round trip cannot change it."""
    run = FakeRunner()

    assert sources.DockerSources(run=run).cc_transcript("") is None
    assert run.calls == []


def test_the_transcript_is_looked_up_by_the_chat_session_not_the_cc_session():
    script = sources._container_script(
        {"op": "cc_transcript", "session_id": "s"}, "/app")

    assert "chat_session_id=sid" in script
    assert "cc_session_id=" not in script


def test_transcript_rows_are_ordered_by_created_at_not_by_turn_id():
    """`turn_id` is `str(query_task.task_id)` -- a random UUID in a CharField, so
    ordering by it is a lexicographic shuffle that silently scrambles a
    multi-turn transcript rather than failing."""
    script = sources._container_script(
        {"op": "cc_transcript", "session_id": "s"}, "/app")

    assert '.order_by("created_at", "id")' in script
    assert 'order_by("turn_id")' not in script


# --------------------------------------------------------------------------- #
# merge_transcripts -- the load-bearing decision, tested on THIS side
# --------------------------------------------------------------------------- #
def test_cumulative_snapshots_are_not_duplicated():
    """Each row holds the whole session `.jsonl` as of that turn: `--resume`
    keeps Claude appending to one file under the per-SESSION cc-state dir and
    `_newest_jsonl_under` re-reads all of it every turn. Verified on all four
    multi-turn sessions in the live database."""
    turn1 = b'{"a":1}\n{"a":2}\n'
    turn2 = turn1 + b'{"a":3}\n'

    assert sources.merge_transcripts([turn1, turn2]) == turn2


def test_disjoint_segments_are_kept_because_dropping_one_loses_a_turn():
    """A wiped cc-state store makes the next turn start a fresh Claude session,
    and then the rows really are separate pieces of the record."""
    a, b = b'{"a":1}\n', b'{"b":1}\n'

    assert sources.merge_transcripts([a, b]) == a + b


def test_a_segment_that_does_not_end_in_a_newline_still_joins_as_jsonl():
    assert sources.merge_transcripts([b'{"a":1}', b'{"b":1}']) == b'{"a":1}\n{"b":1}'


def test_one_row_is_returned_untouched():
    assert sources.merge_transcripts([b'{"a":1}\n']) == b'{"a":1}\n'


def test_no_rows_is_empty():
    assert sources.merge_transcripts([]) == b""


def test_the_merge_the_container_runs_is_the_one_under_test_here():
    """It is embedded as SOURCE, so a fix applied here reaches the container and
    the two cannot drift into disagreeing about a multi-turn transcript."""
    script = sources._container_script({"op": "ping"}, "/app")

    assert "def merge_transcripts(chunks):" in script
    assert "if chunk.startswith(out):" in script


def test_the_container_script_is_valid_python():
    """It is a string, so nothing else would catch a template that stopped
    parsing until a paid run had already finished."""
    for op in ({"op": "ping"},
               {"op": "task_rows", "task_ids": ["t"]},
               {"op": "cc_transcript", "session_id": "s"}):
        compile(sources._container_script(op, "/app"), "<container>", "exec")


def test_the_transcript_is_recompressed_to_one_frame():
    """The rows are separate zstd frames and `collect.decompress_transcript`
    opens a single stream over what it is handed, so shipping them end to end
    can truncate to turn 1 -- and still decompress to valid jsonl."""
    script = sources._container_script(
        {"op": "cc_transcript", "session_id": "s"}, "/app")

    assert "cc_transcript_store.compress(merged)" in script


# --------------------------------------------------------------------------- #
# copy_tree
# --------------------------------------------------------------------------- #
def test_a_copied_tree_lands_under_the_name_the_caller_chose(tmp_path):
    """`docker cp <c>:/a/b -` is rooted at `b/`, but every destination `collect`
    passes is a name IT chose (`run_root`, `cc_scratch/<task_id>`)."""
    run = FakeRunner(_copy_out(_tar({"console.txt": b"trace"}, root="b")))

    ok = sources.DockerSources(run=run).copy_tree(
        "/dmac/users/p/u/scratch/b", tmp_path / "cc_scratch" / "t-1")

    assert ok is True
    assert (tmp_path / "cc_scratch" / "t-1" / "console.txt").read_text() == "trace"


def test_an_absent_source_is_false_not_an_exception(tmp_path):
    run = FakeRunner(_copy_out(status=sources.ABSENT))

    assert sources.DockerSources(run=run).copy_tree("/gone", tmp_path / "x") is False
    assert not (tmp_path / "x").exists()


def test_a_broken_copy_raises_CopyFailed_because_it_is_the_collectors_problem(tmp_path):
    """"cc_sweep reaped the scratch" is a fact about the run; "the copy broke" is
    a fact about the collector, and a hundred of the second must not read as a
    hundred of the first."""
    run = FakeRunner(_copy_out(status=sources.FAILED))

    with pytest.raises(collect.CopyFailed):
        sources.DockerSources(run=run).copy_tree("/there", tmp_path / "x")


def test_an_unreachable_container_never_reports_a_source_as_merely_absent(tmp_path):
    run = FakeRunner(_copy_out(status=sources.UNREACHABLE))

    with pytest.raises(collect.CopyFailed):
        sources.DockerSources(run=run).copy_tree("/there", tmp_path / "x")


def test_no_status_marker_at_all_is_a_failure_not_an_absence(tmp_path):
    run = FakeRunner(sources.Completed(127, b"bash: docker: not found\n", b""))

    with pytest.raises(collect.CopyFailed):
        sources.DockerSources(run=run).copy_tree("/there", tmp_path / "x")


def test_a_success_with_no_archive_is_a_failure(tmp_path):
    run = FakeRunner(_copy_out(status=sources.COPIED))

    with pytest.raises(collect.CopyFailed):
        sources.DockerSources(run=run).copy_tree("/there", tmp_path / "x")


def test_the_outcome_is_not_read_off_an_exit_code_that_conflates_the_cases():
    """`docker exec <missing container> true` and `docker exec <live> test -e
    <missing path>` BOTH exit 1, so an exit code cannot tell "the container is
    gone" from "the scratch was reaped". The probe echoes instead."""
    run = FakeRunner(_copy_out(status=sources.ABSENT))
    src = sources.DockerSources(run=run)
    src.copy_tree("/dmac/users/p/u/scratch/t", pathlib.Path("/nonexistent/x"))

    script = run.script
    assert "echo PRESENT" in script and "echo ABSENT" in script


def test_the_archive_is_streamed_so_a_remote_host_works_like_the_local_one(tmp_path):
    """`docker cp` to a path would leave the collected tree on the far side of
    the ssh hop; to stdout it arrives here either way."""
    run = FakeRunner(_copy_out(_tar({"a": b"1"})))

    sources.DockerSources(host="box", run=run).copy_tree("/s", tmp_path / "d")

    assert "box:/s" not in run.script       # the container, not the ssh host
    assert "docker cp nextseek:/s - " in run.script
    assert "| base64 -w0" in run.script


def test_a_source_path_with_shell_metacharacters_cannot_escape_the_command(tmp_path):
    run = FakeRunner(_copy_out(status=sources.ABSENT))

    sources.DockerSources(run=run).copy_tree("/s; rm -rf /", tmp_path / "d")

    script = run.script
    assert "'nextseek:/s; rm -rf /'" in script
    assert "\n/s; rm" not in script


def test_recollecting_replaces_the_previous_tree_rather_than_merging_into_it(tmp_path):
    """Collection is post-hoc so that it is re-runnable; a stale file left from
    the first pass would be read as evidence from the run."""
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "stale.txt").write_text("old")
    run = FakeRunner(_copy_out(_tar({"fresh.txt": b"new"})))

    sources.DockerSources(run=run).copy_tree("/s", dest)

    assert (dest / "fresh.txt").read_text() == "new"
    assert not (dest / "stale.txt").exists()


def test_an_archive_with_more_than_one_root_is_refused(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in ("a", "b"):
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tf.addfile(info)
    run = FakeRunner(_copy_out(buf.getvalue()))

    with pytest.raises(collect.CopyFailed):
        sources.DockerSources(run=run).copy_tree("/s", tmp_path / "d")


def test_the_staging_dir_is_cleaned_up_even_when_the_move_never_happens(tmp_path):
    dest = tmp_path / "sub" / "d"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in ("a", "b"):
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tf.addfile(info)
    run = FakeRunner(_copy_out(buf.getvalue()))

    with pytest.raises(collect.CopyFailed):
        sources.DockerSources(run=run).copy_tree("/s", dest)

    assert list((tmp_path / "sub").iterdir()) == []


# --------------------------------------------------------------------------- #
# a broken transport is never an empty result
# --------------------------------------------------------------------------- #
def test_an_unreachable_container_raises_rather_than_returning_no_rows():
    """A `task_rows` that answered `{}` would put 254 "no assistant_query_task
    row" misses into collection.json, which reads exactly like a product that
    lost every row."""
    run = FakeRunner(sources.Completed(
        1, b"", b"Error response from daemon: No such container: nextseek\n"))

    with pytest.raises(sources.SourcesUnavailable) as exc:
        sources.DockerSources(run=run).task_rows(["t-1"])

    assert "No such container" in str(exc.value)


def test_a_non_json_payload_after_the_marker_raises():
    run = FakeRunner(sources.Completed(0, f"{sources.MARKER}\nnot json\n".encode(), b""))

    with pytest.raises(sources.SourcesUnavailable):
        sources.DockerSources(run=run).task_rows(["t-1"])


def test_noise_before_the_marker_is_ignored():
    """`uv`, ssh and docker all write to this stream."""
    run = FakeRunner(_payload({"t-1": {"status": "completed", "progress": [],
                                       "result": None}},
                              before=b"warning: something\n"))

    assert "t-1" in sources.DockerSources(run=run).task_rows(["t-1"])


def test_ping_proves_the_database_answered_not_merely_that_django_imported():
    run = FakeRunner(_payload({"query_tasks": 12, "cc_transcripts": 3}))

    assert sources.DockerSources(run=run).ping() == {"query_tasks": 12,
                                                     "cc_transcripts": 3}
    assert "QueryTask.objects.count()" in run.python


def test_ping_raises_when_the_chain_is_broken():
    run = FakeRunner(sources.Completed(1, b"", b"cannot connect to the docker daemon"))

    with pytest.raises(sources.SourcesUnavailable):
        sources.DockerSources(run=run).ping()


# --------------------------------------------------------------------------- #
# it really is a `collect.Sources`
# --------------------------------------------------------------------------- #
def test_it_satisfies_the_protocol_collect_documents():
    for name in ("task_rows", "cc_transcript", "copy_tree"):
        assert callable(getattr(sources.DockerSources, name))
        assert hasattr(collect.Sources, name)


def test_collect_runs_end_to_end_against_it_with_only_the_runner_faked(tmp_path):
    """The whole point: `collect.collect` drives this class unchanged."""
    from nessie_tests.bayes_manifest import BayesManifest, BayesPair
    from nessie_tests.manifest import NessieManifestEntry

    entry = NessieManifestEntry(id="a.one", family="f", tier="full",
                                status="passed", task_ids=["t-cc"])
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", cc=entry)])
    row = {"status": "completed",
           "progress": [{"event": "query_complete",
                         "data": {"session_id": "s-1",
                                  "cc_raw_files": ["/dmac/users/p/u/output/raw/x.jsonl"]}}],
           "result": {"reply": "hi"}}
    run = FakeRunner(
        _payload({"t-cc": row}),                      # task_rows
        _copy_out(status=sources.ABSENT),             # cc_scratch
        _copy_out(_tar({"out.txt": b"x"})),           # cc_artifacts
        _payload(None),                               # cc_transcript
    )

    payload = collect.collect(m, tmp_path, sources.DockerSources(run=run),
                              retry_delay_s=0)

    dest = collect.artifacts_dir(tmp_path) / "a.one" / "cc"
    assert json.loads((dest / "task.json").read_text())["result"] == {"reply": "hi"}
    assert (dest / "cc_artifacts" / "t-cc" / "out.txt").read_text() == "x"
    assert payload["turns_seen"] == 1
    # The scratch that was not there is RECORDED, not silently skipped.
    kinds = {(x["what"], x["kind"]) for x in payload["missing"]}
    assert ("cc_scratch", "absent") in kinds
