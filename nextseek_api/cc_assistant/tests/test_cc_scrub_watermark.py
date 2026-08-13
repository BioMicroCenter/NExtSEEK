"""#76: the transcript scrub must survive the process that ran it dying.

``run_cc_turn`` executes on a ``daemon=True`` thread (services/cc_assistant.py,
``threading.Thread(target=_run, daemon=True).start()``), and the #72 in-place
source scrub runs in that turn's ``finally``. A gunicorn worker recycle, a
``docker restart`` or a SIGKILL kills a daemon thread at interpreter exit
WITHOUT running its ``finally``, so the user's plaintext SEEK password stays in
the session ``.jsonl`` under the ``dmac-cc-users`` volume — a file nothing ever
deletes, because ``--resume`` needs it.

``cc_sweep`` then reads exactly that file raw on a 300 s beat and forwards it to
a third-party summarizer whose output is merged into the ``CLAUDE.md`` mounted
into later agent containers. It runs with no request in scope, so it holds no
credential and cannot scrub at its own read point.

The two halves tested here:

* ``scrub_transcript_store`` records a durable WATERMARK — sha256 of the bytes
  it left behind — outside the agent's mount, and ``cc_sweep`` refuses to
  summarize any transcript whose current bytes are not watermarked. Needs no
  credentials; fail-closed on every error path.
* ``scrub_sibling_transcript_stores`` widens the credential-bearing repair to
  the user's OTHER session stores, so an orphaned transcript is cleaned on that
  user's next CC turn rather than never.

Hermetic: tmp_path + monkeypatch. No Django, no docker, no network.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import pathlib
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine

PW = "hunter2-s3cr3t"
USER = "demo"
ENV = {"NEXTSEEK_USERNAME": USER, "NEXTSEEK_PASSWORD": PW, "API_PASS": PW}
BASIC = base64.b64encode(f"{USER}:{PW}".encode()).decode()

DIRTY = (
    '{"type":"assistant","message":{"content":"checking"}}\n'
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"NEXTSEEK_PASSWORD={PW}\\n"'
    "}]}}\n"
    f'{{"type":"user","message":{{"content":"> Authorization: Basic {BASIC}"}}}}\n'
).encode()

CLEAN = b'{"type":"assistant","message":{"content":"nothing secret here"}}\n'


def _store(cc_state: Path, name: str = "sess.jsonl", body: bytes = DIRTY) -> Path:
    """Plant one transcript in ``<cc_state>/projects/-home-user/<name>``."""
    d = cc_state / "projects" / "-home-user"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(body)
    return f


def _verified(f: Path) -> bool:
    return cc_engine.transcript_is_verified_scrubbed(f, f.read_bytes())


# ---------------------------------------------------------------------------
# Where the watermark lives — the whole isolation argument
# ---------------------------------------------------------------------------


def test_manifest_is_outside_the_dir_the_agent_can_write(tmp_path):
    """The agent mounts ``<...>/cc-state/<session_id>`` READ-WRITE at
    /home/user/.claude. A watermark stored inside it would be forgeable from
    inside the sandbox: an agent could stamp its own unscrubbed transcript as
    clean and have the sweep publish it. It must live in the parent, which is
    mounted into no agent at all."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    manifest = cc_engine._scrub_manifest_path(cc_state)

    assert manifest.parent == cc_state.parent
    assert cc_state not in manifest.parents, (
        "the watermark must not be inside the dir the agent mounts read-write"
    )
    assert manifest.name.startswith("."), (
        "leading dot keeps it out of the globs that walk this tree"
    )


def test_manifest_is_not_picked_up_by_the_transcript_globs(tmp_path):
    cc_state = tmp_path / "cc-state" / "sess-a"
    _store(cc_state)
    cc_engine.scrub_transcript_store(cc_state, ENV)

    assert list((cc_state / "projects").rglob("*.jsonl")) != []
    assert list(cc_state.rglob("*.scrub.json")) == [], (
        "the manifest must not be inside the session store"
    )


# ---------------------------------------------------------------------------
# The watermark itself
# ---------------------------------------------------------------------------


def test_scrubbed_transcript_is_verified(tmp_path):
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state)
    assert PW.encode() in f.read_bytes(), "fixture must start dirty"

    report = cc_engine.scrub_transcript_store(cc_state, ENV)

    assert report == (1, 0)
    assert PW.encode() not in f.read_bytes()
    assert _verified(f) is True


def test_already_clean_transcript_is_also_verified(tmp_path):
    """The easy branch to forget: ``scrub_transcript_store`` ``continue``s
    without rewriting when there was nothing to redact. That file is still
    PROVEN clean by this pass and must be admitted to the sweep, or a store
    with no secrets in it would be skipped forever."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state, body=CLEAN)

    report = cc_engine.scrub_transcript_store(cc_state, ENV)

    assert report == (0, 0)
    assert _verified(f) is True


def test_bytes_appended_after_the_scrub_are_not_verified(tmp_path):
    """"Verified" is a claim about CONTENT, not about a path. The agent goes on
    appending to its transcript, and the tail written after the last scrub has
    never been through it."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state)
    cc_engine.scrub_transcript_store(cc_state, ENV)
    assert _verified(f) is True

    with f.open("ab") as fh:
        fh.write(b'{"type":"user","message":{"content":"' + PW.encode() + b'"}}\n')

    assert _verified(f) is False


def test_never_scrubbed_transcript_is_not_verified(tmp_path):
    """The bug this whole change exists for: the turn died before its finally."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state)

    assert _verified(f) is False


def test_a_skipped_file_is_not_verified(tmp_path, monkeypatch):
    """``ScrubReport.skipped`` counts files the scrub KNOWS it left dirty.
    Recording those as clean would be the worst possible bug in this feature."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    good = _store(cc_state, "good.jsonl")
    bad = _store(cc_state, "bad.jsonl")

    real_read = pathlib.Path.read_bytes

    def flaky(self, *a, **kw):
        if self.name == "bad.jsonl":
            raise OSError("EIO")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_bytes", flaky)
    report = cc_engine.scrub_transcript_store(cc_state, ENV)
    monkeypatch.undo()

    assert report == (1, 1)
    assert _verified(good) is True
    assert _verified(bad) is False


def test_stale_entries_are_pruned(tmp_path):
    """The manifest is replaced wholesale, not merged: a claim about a file that
    has since disappeared (or become unreadable) must not outlive the file."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    a = _store(cc_state, "a.jsonl")
    _store(cc_state, "b.jsonl")
    cc_engine.scrub_transcript_store(cc_state, ENV)
    assert len(cc_engine._read_scrub_manifest(cc_state)) == 2

    a.unlink()
    cc_engine.scrub_transcript_store(cc_state, ENV)

    files = cc_engine._read_scrub_manifest(cc_state)
    assert list(files) == ["projects/-home-user/b.jsonl"]


def test_an_unwritable_manifest_does_not_change_the_counts(tmp_path, monkeypatch):
    """The files ARE scrubbed; only the proof is missing. Reporting them as
    skipped would be a lie, and failing the scrub would be worse. The safe
    consequence is simply that the sweep will not touch them."""
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state)

    def boom(*a, **kw):
        raise OSError("EROFS")

    monkeypatch.setattr(cc_engine, "_write_scrub_manifest", boom)
    report = cc_engine.scrub_transcript_store(cc_state, ENV)
    monkeypatch.undo()

    assert report == (1, 0)
    assert PW.encode() not in f.read_bytes(), "the scrub itself must still happen"
    assert _verified(f) is False, "unrecorded means unverified, which is the safe side"


# ---------------------------------------------------------------------------
# Fail-closed on every unknown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    b"",
    b"not json at all",
    b"[]",
    json.dumps({"version": 99, "files": {}}).encode(),
    json.dumps({"version": 1}).encode(),
    json.dumps({"version": 1, "files": []}).encode(),
    json.dumps({"version": 1, "files": {"projects/-home-user/sess.jsonl": 7}}).encode(),
])
def test_a_malformed_manifest_verifies_nothing(tmp_path, payload):
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state, body=CLEAN)
    cc_engine.scrub_transcript_store(cc_state, ENV)
    assert _verified(f) is True

    cc_engine._scrub_manifest_path(cc_state).write_bytes(payload)

    assert _verified(f) is False


def test_a_manifest_naming_a_different_digest_verifies_nothing(tmp_path):
    cc_state = tmp_path / "cc-state" / "sess-a"
    f = _store(cc_state, body=CLEAN)
    cc_engine.scrub_transcript_store(cc_state, ENV)

    path = cc_engine._scrub_manifest_path(cc_state)
    data = json.loads(path.read_text())
    data["files"]["projects/-home-user/sess.jsonl"] = hashlib.sha256(b"other").hexdigest()
    path.write_text(json.dumps(data))

    assert _verified(f) is False


def test_a_path_with_no_projects_ancestor_verifies_nothing(tmp_path):
    """The manifest is located by walking up to the ``projects`` store root. A
    path that is not in a transcript store at all cannot be located, and must
    not be treated as clean by default."""
    stray = tmp_path / "elsewhere" / "sess.jsonl"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(CLEAN)

    assert cc_engine.transcript_is_verified_scrubbed(stray, CLEAN) is False


def test_a_missing_store_returns_zero_and_writes_no_manifest(tmp_path):
    """Preserved from #72: no ``projects`` dir is not an error."""
    cc_state = tmp_path / "cc-state" / "nope"

    assert cc_engine.scrub_transcript_store(cc_state, ENV) == (0, 0)
    assert not cc_engine._scrub_manifest_path(cc_state).exists()


# ---------------------------------------------------------------------------
# Sibling repair — the credential-bearing half
# ---------------------------------------------------------------------------


def test_sibling_sweep_cleans_the_other_sessions(tmp_path):
    """The orphan case. sess-b's own turn died before its finally ran; sess-a's
    turn is the next time this user's password is in scope anywhere."""
    root = tmp_path / "cc-state"
    a = _store(root / "sess-a")
    b = _store(root / "sess-b")
    c = _store(root / "sess-c")

    report = cc_engine.scrub_sibling_transcript_stores(root, ENV, exclude=root / "sess-a")

    assert report == (2, 0)
    assert PW.encode() in a.read_bytes(), "the excluded (current) session is not touched"
    assert PW.encode() not in b.read_bytes()
    assert PW.encode() not in c.read_bytes()
    assert _verified(b) is True and _verified(c) is True


def test_sibling_sweep_ignores_manifests_symlinks_and_storeless_dirs(tmp_path):
    root = tmp_path / "cc-state"
    b = _store(root / "sess-b")
    cc_engine.scrub_transcript_store(root / "sess-b", ENV)  # leaves a manifest here
    (root / "sess-empty").mkdir()                            # no projects/ child
    outside = tmp_path / "outside"
    _store(outside)
    (root / "sess-link").symlink_to(outside, target_is_directory=True)

    report = cc_engine.scrub_sibling_transcript_stores(root, ENV)

    assert report == (0, 0), "nothing left to do; the symlinked tree is out of scope"
    assert PW.encode() in (outside / "projects" / "-home-user" / "sess.jsonl").read_bytes()
    assert _verified(b) is True


def test_sibling_sweep_on_a_missing_root_is_a_noop(tmp_path):
    assert cc_engine.scrub_sibling_transcript_stores(tmp_path / "nope", ENV) == (0, 0)


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------


def _finally_call_lines(fn_name: str) -> dict[str, int]:
    """First source line at which each name is called in ``fn_name``'s finally.

    Same shape as ``test_cc_transcript_secret_scrub._finally_call_order``, but
    keyed on ``lineno`` rather than statement index because the two scrub calls
    this asserts about sit inside the SAME ``try`` statement.
    """
    tree = ast.parse(Path(cc_engine.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    positions: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        for stmt in node.finalbody:
            for call in sorted((c for c in ast.walk(stmt) if isinstance(c, ast.Call)),
                               key=lambda c: c.lineno):
                f = call.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if name:
                    positions.setdefault(name, call.lineno)
    return positions


def test_turn_finally_scrubs_the_current_and_the_sibling_stores():
    calls = _finally_call_lines("run_cc_turn")

    assert "scrub_transcript_store" in calls
    assert "scrub_sibling_transcript_stores" in calls, (
        "a session whose own turn died before its finally is never revisited "
        "unless a LATER turn by the same user — the only holder of the "
        "credential needed to scrub it — sweeps its siblings"
    )
    assert calls["stop"] < calls["scrub_sibling_transcript_stores"], (
        "stop the agent before scrubbing, for the same reason #72 ordered the "
        "current-session scrub after it"
    )
