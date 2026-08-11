"""Django-free filesystem glue for Step 1c memory mounts: atomic rendered-file
write + copy-on-change transcript staging. Kept import-light (pathlib/os/shutil)."""
from __future__ import annotations

import os
import shutil
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


def stage_transcripts(window, staging_dir: Path, scrub=None) -> Path | None:
    """Copy each window session's transcript to staging_dir/<sid>.jsonl (copy-on-
    change by size), prune staged files not in the window. Returns staging_dir if
    any file is staged, else None. `window` items expose .session_id/.transcript_path.

    `scrub` (#72) is an optional ``bytes -> bytes`` filter applied to the content
    on the way in. This dir is mounted READ-ONLY into later agent containers, so
    anything staged here is republished to a different turn's agent. The engine
    now scrubs source transcripts in place after every turn, but a session that
    predates that (or that never runs another turn) still holds plaintext, and
    this is the point where such a file would be handed to a new agent.

    Scrubbing changes content length, so the copy-on-change size comparison is
    made against the scrubbed bytes — otherwise a scrubbed destination would
    look "changed" forever and be rewritten on every single turn.
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
        if scrub is None:
            if not dst.exists() or dst.stat().st_size != src_p.stat().st_size:
                shutil.copyfile(src_p, dst)
        else:
            payload = scrub(src_p.read_bytes())
            if not dst.exists() or dst.stat().st_size != len(payload):
                dst.write_bytes(payload)
        staged += 1
    keep = {f"{sid}.jsonl" for sid in wanted}
    for f in staging_dir.glob("*.jsonl"):
        if f.name not in keep:
            f.unlink()
    return staging_dir if staged else None
