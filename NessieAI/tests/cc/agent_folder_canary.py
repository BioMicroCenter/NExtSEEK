"""A canary for the agent-folder tests: files Django must never write, rename over, chmod or read.

Each test_cc_agent_folders_ module plants links from the names Django touches in a folder an agent (or the
sidecar) can write to this canary, calls the real code, and asserts the canary is exactly as it was and its
bytes reached none of Django's sinks. Not a test module (no test_ prefix). Standard library only.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

MARK = b"CANARY-5d0c3a91-agent-folders"


class Canary:
    """``<root>/canary/``: ``secret.jsonl`` and ``sub/deep.jsonl`` carry ``MARK``, plus any ``extra`` files."""

    def __init__(self, root: Path, *, extra: dict[str, bytes] | None = None):
        self.dir = Path(root) / "canary"
        (self.dir / "sub").mkdir(parents=True)
        self.file = self.dir / "secret.jsonl"
        self.file.write_bytes(b'{"type":"user","message":{"content":"' + MARK + b'"}}\n')
        (self.dir / "sub" / "deep.jsonl").write_bytes(
            b'{"type":"user","message":{"content":"deep ' + MARK + b'"}}\n')
        for rel, data in (extra or {}).items():
            path = self.dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        os.chmod(self.file, 0o640)
        self._before = self.state()

    def state(self) -> dict[str, tuple]:
        """Inode, mode, mtime and bytes of every entry: a write, a rename over, a chmod or a new file shows."""
        out: dict[str, tuple] = {}
        st = os.lstat(self.dir)  # the folder itself: a chmod or utime through a link shows
        out["."] = (st.st_ino, st.st_mode, st.st_mtime_ns, None)
        for dirpath, dirnames, filenames in os.walk(self.dir):
            for name in sorted(dirnames + filenames):
                path = Path(dirpath) / name
                st = os.lstat(path)
                body = path.read_bytes() if path.is_file() and not path.is_symlink() else None
                out[path.relative_to(self.dir).as_posix()] = (st.st_ino, st.st_mode, st.st_mtime_ns, body)
        return out

    def assert_untouched(self) -> None:
        after = self.state()
        assert after == self._before, (
            "the canary outside the agent's folders changed (written, renamed over, chmodded, or a file "
            f"added): before={sorted(self._before)} after={sorted(after)}")

    def assert_unread(self, *sinks) -> None:
        for sink in sinks:
            assert MARK not in _as_bytes(sink), "the canary's bytes reached a Django sink"

    def link_file(self, at: Path) -> Path:
        at.parent.mkdir(parents=True, exist_ok=True)
        at.symlink_to(self.file)
        return at

    def link_dir(self, at: Path) -> Path:
        at.parent.mkdir(parents=True, exist_ok=True)
        at.symlink_to(self.dir, target_is_directory=True)
        return at


def _as_bytes(sink) -> bytes:
    if isinstance(sink, bytes):
        return sink
    if isinstance(sink, Path):
        return tree_bytes(sink)
    return repr(sink).encode("utf-8", "replace")


def tree_bytes(root: Path) -> bytes:
    """Every regular file under ``root``, zip members opened, joined. Never follows a link."""
    root = Path(root)
    if not root.exists():
        return b""
    chunks: list[bytes] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            chunks.append(data)
            if name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    chunks.extend(zf.read(member) for member in zf.namelist())
    return b"\n".join(chunks)


# --- fakes for a full run_cc_turn (Task 5 onward) ---------------------------------------------------------

TURN_FRAMES = [
    json.dumps({"type": "system", "subtype": "init", "session_id": "sid-1", "model": "opus"}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}),
    json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                "total_cost_usd": 0.01, "session_id": "sid-1", "num_turns": 1, "duration_ms": 5}),
]


class FakeContainer:
    """A docker container that records wait/stop/remove in ``calls``; the ``*_ok`` knobs make a call raise."""

    def __init__(self, calls, *, wait_ok=True, stop_ok=True, remove_ok=True,
                 wait_gone=False, stop_gone=False):
        self.calls = calls
        self.wait_ok, self.stop_ok, self.remove_ok = wait_ok, stop_ok, remove_ok
        # *_gone: raise docker's NotFound (auto_remove already removed the container).
        self.wait_gone, self.stop_gone = wait_gone, stop_gone
        self.wait_timeouts: list = []

    def attach_socket(self, params=None):
        return object()

    def logs(self, **kwargs):
        return iter(())

    def wait(self, timeout=None):
        self.calls.append("wait")
        self.wait_timeouts.append(timeout)
        if self.wait_gone:
            from docker.errors import NotFound
            raise NotFound("gone")
        if not self.wait_ok:
            raise RuntimeError("wait failed")
        return {"StatusCode": 0}

    def stop(self, timeout=None):
        self.calls.append("stop")
        if self.stop_gone:
            from docker.errors import NotFound
            raise NotFound("gone")
        if not self.stop_ok:
            raise RuntimeError("stop failed")

    def remove(self, force=False):
        self.calls.append("remove")
        if not self.remove_ok:
            raise RuntimeError("remove failed")


class FakeAgent:
    """Calls ``work()`` once on the first read (the agent writing its files), then returns ``frames``."""

    def __init__(self, work=None, frames=None):
        self._work = work
        self._lines = list(TURN_FRAMES if frames is None else frames)

    def send_stdin(self, _data):
        return None

    def close_stdin(self):
        return None

    def read_event_line(self):
        if self._work is not None:
            work, self._work = self._work, None
            work()
        return self._lines.pop(0) if self._lines else None
