"""A canary for the agent-folder tests: files Django must never write, rename over, chmod or read.

Each test_cc_agent_folders_ module plants links from the names Django touches in a folder an agent (or the
sidecar) can write to this canary, calls the real code, and asserts the canary is exactly as it was and its
bytes reached none of Django's sinks. Not a test module (no test_ prefix). Standard library only.
"""
from __future__ import annotations

import io
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
