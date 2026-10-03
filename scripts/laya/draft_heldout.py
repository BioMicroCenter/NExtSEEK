"""Build the held-out DRAFT from a text pool (JevLevROUTING, unit U4). Writes nothing in the repo.

Pool = jsonl rows {chat_id, query, history, family, entity, route, truth_kind, prompt_seen[, entities]},
built outside the repo. Usage: python -m scripts.laya.draft_heldout POOL.jsonl OUT_DIR   (OUT_DIR outside any git repo)
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

from NessieAI.router.laya_common import norm_text_hash  # U3's; tests stub it
from scripts.laya.split import heldout_bucket

ROUTES = ("nextseek_query", "container_cc", "either", "unrelated")
TARGETS = {"nextseek_query": 150, "container_cc": 40, "either": 15, "unrelated": 25}  # DATA.md s12, decision 1


def assert_outside_git(path) -> None:
    p = pathlib.Path(path).expanduser().resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            raise ValueError(f"{p} is inside a git repo ({d}); held-out text must live outside every repo")


def draft(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (held, rest). A chat is held when any of its rows is held (it travels together).
    Prompt-seen rows are in neither. Rows that only share a secondary entity with a held primary entity are dropped."""
    held_chats = {r["chat_id"] for r in rows
                  if not r.get("prompt_seen") and heldout_bucket(r["family"], r["entity"])}
    held_ents = {r["entity"] for r in rows if heldout_bucket("none", r["entity"])}
    held, rest = [], []
    for r in rows:
        if r.get("prompt_seen"):
            continue
        if r["chat_id"] in held_chats:
            held.append({**r, "split": "heldout", "hash": norm_text_hash(r["query"])})
        elif not held_ents & set(r.get("entities") or [r["entity"]]):
            rest.append(r)
    return held, rest


def topup(held: list[dict]) -> dict:
    """What the operator still has to write, counts only (never text)."""
    have = collections.Counter(r["route"] for r in held)
    return {"targets": TARGETS, "have": dict(have), "need": {k: max(0, v - have[k]) for k, v in TARGETS.items()},
            "also": "a few negation questions (don't run anything, just tell me how); written by a human, never by an agent that trains"}


def main(pool, out_dir) -> None:
    assert_outside_git(out_dir)
    rows = [json.loads(line) for line in open(pool) if line.strip()]
    held, rest = draft(rows)
    out = pathlib.Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "draft.jsonl").write_text("".join(json.dumps(r) + "\n" for r in held))
    (out / "topup.json").write_text(json.dumps(topup(held), indent=1))
    print(f"held {len(held)} rows, rest {len(rest)}; wrote draft.jsonl and topup.json in {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
