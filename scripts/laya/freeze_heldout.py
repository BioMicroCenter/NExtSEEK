"""Freeze the held-out file (JevLevROUTING, unit U4). OPERATOR-GATED: run only on the operator's go.

Usage: python -m scripts.laya.freeze_heldout DRAFT.jsonl [OUT_DIR]   (default ~/code/dmac/jevlev-heldout)
Writes heldout-v1.jsonl and heldout-v1.manifest.jsonl (no text) mode 0444, refuses to overwrite, prints the sha256.
A change is a new version number, never an edit.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys

from scripts.laya import draft_heldout as dh

MANIFEST_KEYS = ("hash", "split", "family", "entity", "route", "truth_kind")


def freeze(draft, out_dir="~/code/dmac/jevlev-heldout", version="v1") -> str:
    dh.assert_outside_git(out_dir)
    out = pathlib.Path(out_dir).expanduser()
    jl, man = out / f"heldout-{version}.jsonl", out / f"heldout-{version}.manifest.jsonl"
    if jl.exists() or man.exists():
        raise FileExistsError(f"{jl} exists; a change is a new version, never an edit")
    rows = [json.loads(line) for line in open(draft) if line.strip()]
    for r in rows:
        if r["route"] not in dh.ROUTES:
            raise ValueError(f"bad route {r['route']!r}")
        r["hash"], r["split"] = dh.norm_text_hash(r["query"]), "heldout"
    body = "".join(json.dumps(r) + "\n" for r in rows).encode()
    out.mkdir(parents=True, exist_ok=True)
    mbody = "".join(json.dumps({k: r[k] for k in MANIFEST_KEYS}) + "\n" for r in rows).encode()
    for path, data in ((jl, body), (man, mbody)):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    sha = hashlib.sha256(body).hexdigest()
    print(f"froze {len(rows)} rows: {jl}\nsha256 {sha}  (record this in PROGRESS.md)")
    return sha


if __name__ == "__main__":
    freeze(*sys.argv[1:3])
