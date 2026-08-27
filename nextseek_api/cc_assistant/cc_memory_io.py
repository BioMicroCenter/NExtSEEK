"""Django-free filesystem glue for Step 1c memory mounts: atomic rendered-file
write + copy-on-change transcript staging. Kept import-light (pathlib/os/shutil)."""
from __future__ import annotations

import os
from pathlib import Path


def write_memory_file(dest: Path, markdown: str) -> Path | None:
    """Atomically write `markdown` to `dest` (tmp+rename). Empty markdown removes
    any stale file and returns None (caller then mounts nothing)."""
    dest = Path(dest)
    if not markdown:
        try:
            dest.unlink()
        except FileNotFoundError:
            pass
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    os.replace(tmp, dest)          # atomic on the same filesystem
    try:
        os.chmod(dest, 0o644)
    except OSError:
        pass
    return dest


def _is_stale(dst: Path, payload: bytes) -> bool:
    """True if `dst` does not already hold exactly `payload`.

    Deliberately NOT a size comparison. Both writers of a transcript can change
    its content without changing its length: ``_scrub_secret_bytes`` replaces a
    secret with the 10-byte ``<REDACTED>``, so a 10-character password rewrites
    to the identical size, and ``scrub_transcript_store`` performs that same
    rewrite on the source in place. A size check then reports "unchanged" and
    leaves a STALE PLAINTEXT destination in a directory that is mounted
    read-only into every later agent container — permanently, since nothing else
    ever revisits it.

    Cheap enough for the per-turn window: the size check short-circuits the
    common changed case (transcripts grow), so the destination is read only when
    the sizes already match, and the source has just been read in full anyway.
    """
    try:
        if dst.stat().st_size != len(payload):
            return True
    except OSError:              # missing, or unreadable -> rewrite it
        return True
    try:
        return dst.read_bytes() != payload
    except OSError:
        return True


def stage_transcripts(window, staging_dir: Path, scrub=None) -> Path | None:
    """Copy each window session's transcript to staging_dir/<sid>.jsonl (copy-on-
    change by content), prune staged files not in the window. Returns staging_dir
    if any file is staged, else None. `window` items expose
    .session_id/.transcript_path.

    `scrub` (#72) is an optional ``bytes -> bytes`` filter applied to the content
    on the way in. This dir is mounted READ-ONLY into later agent containers, so
    anything staged here is republished to a different turn's agent. The engine
    now scrubs source transcripts in place after every turn, but a session that
    predates that (or that never runs another turn) still holds plaintext, and
    this is the point where such a file would be handed to a new agent.

    Scrubbing changes content, so the copy-on-change comparison is made against
    the scrubbed bytes — otherwise a scrubbed destination would look "changed"
    forever and be rewritten on every single turn. See ``_is_stale`` for why
    that comparison cannot be made on size.
    """
    staging_dir = Path(staging_dir)
    wanted: dict[str, str] = {
        m.session_id: m.transcript_path for m in window if m.transcript_path
    }
    if not wanted:
        if staging_dir.is_dir():
            for f in staging_dir.glob("*.jsonl"):
                f.unlink()
        return None
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged = 0
    for sid, src in wanted.items():
        src_p = Path(src)
        if not src_p.is_file():
            continue
        dst = staging_dir / f"{sid}.jsonl"
        payload = src_p.read_bytes()
        if scrub is not None:
            payload = scrub(payload)
        if _is_stale(dst, payload):
            dst.write_bytes(payload)
        staged += 1
    keep = {f"{sid}.jsonl" for sid in wanted}
    for f in staging_dir.glob("*.jsonl"):
        if f.name not in keep:
            f.unlink()
    return staging_dir if staged else None
