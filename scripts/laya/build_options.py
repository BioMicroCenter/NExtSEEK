"""Build NessieAI/router/laya_options.json from what the BAML router reads (SPEC s3).

Option text is generated, never hand-written: the first sentence of description, best_for and not_for of each
route in route_capabilities.json, and for `unrelated` the first sentence of the unrelated paragraph of router.baml
plus the first sentence of its "NOT unrelated" counter-rule. The three options share laya's 192-token head
budget: over budget, not_for then best_for sentences are dropped from the end; a description is never cut, and the
build fails instead.

    python scripts/laya/build_options.py            # write laya_options.json
    python scripts/laya/build_options.py --check    # exit 1 if the checked-in file differs
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from NessieAI.router import laya_common  # noqa: E402

QUESTION_ID = "route"
PROMPT = "Which engine should answer the current message?"
KEYS = ("nextseek_query", "container_cc", "unrelated")
HEAD_BUDGET = 192
OUT = "NessieAI/router/laya_options.json"
SOURCES = ("NessieAI/dmac_assistant/build_context/route_capabilities.json",
           "NessieAI/dmac_assistant/baml_src/router.baml")
_SENT = re.compile(r"(?<=[.!?])(?:;\s*|\s+)(?=[A-Z`\"'])")  # not_for items end ".;"


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    return _SENT.split(text, 1)[0]


def _paragraph(lines: list[str], needle: str) -> str:
    i = next(n for n, ln in enumerate(lines) if needle in ln)
    a = i
    while a > 0 and lines[a - 1].strip():
        a -= 1
    b = i
    while b + 1 < len(lines) and lines[b + 1].strip():
        b += 1
    return " ".join(ln.strip() for ln in lines[a:b + 1])


def estimate_tokens(text: str) -> int:
    """No tokenizer on this host: ~4 characters a token is the usual English figure, NOT a measurement."""
    return math.ceil(len(text) / 4)


def default_counter():
    """The laya (ModernBERT) tokenizer when it is importable offline, else the estimate."""
    try:
        from transformers import AutoTokenizer  # noqa: PLC0415
        tok = AutoTokenizer.from_pretrained("answerdotai/ModernBERT-large", local_files_only=True)
        return (lambda t: len(tok(t, add_special_tokens=False)["input_ids"])), "ModernBERT tokenizer"
    except Exception:
        return estimate_tokens, "estimate (chars/4, not measured)"


def _parts(root: pathlib.Path) -> dict[str, dict[str, list[str]]]:
    caps = json.loads((root / SOURCES[0]).read_text(encoding="utf-8"))
    routes = {r["route_name"]: r for r in caps["routes"]}
    parts = {}
    for key in KEYS[:2]:
        r = routes[key]
        parts[key] = {f: [first_sentence(r[f])] for f in ("description", "best_for", "not_for")}
    lines = (root / SOURCES[1]).read_text(encoding="utf-8").splitlines()
    parts["unrelated"] = {
        "description": [first_sentence(_paragraph(lines, "select `unrelated`")),
                        first_sentence(_paragraph(lines, "A question is NOT `unrelated`"))],
        "best_for": [], "not_for": []}
    return parts


def _render(parts) -> list[dict]:
    return [{"key": k, "text": " ".join(s for f in ("description", "best_for", "not_for") for s in parts[k][f])}
            for k in KEYS]


def build(root: pathlib.Path = REPO, count=None) -> dict:
    count = count or default_counter()[0]
    parts = _parts(root)
    for field in ("not_for", "best_for"):          # last option first, as SPEC s3 drops from the end
        for key in reversed(KEYS):
            if sum(count(o["text"]) for o in _render(parts)) <= HEAD_BUDGET:
                break
            parts[key][field] = []
    used = sum(count(o["text"]) for o in _render(parts))
    if used > HEAD_BUDGET:
        raise SystemExit(f"option descriptions alone take {used} tokens, over the {HEAD_BUDGET} head budget; "
                         "not cutting a description, shorten the source text")
    out = {"question_id": QUESTION_ID, "prompt": PROMPT, "options": _render(parts),
           "source_hashes": {s: hashlib.sha256((root / s).read_bytes()).hexdigest() for s in SOURCES}}
    out["options_hash"] = laya_common.options_hash(out)
    return out


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    count, how = default_counter()
    doc = build(REPO, count)
    path = REPO / OUT
    if "--check" in argv:
        return 0 if path.exists() and path.read_text(encoding="utf-8") == dumps(doc) else 1
    path.write_text(dumps(doc), encoding="utf-8")
    print(f"wrote {OUT}: {sum(count(o['text']) for o in doc['options'])} tokens by {how}, "
          f"budget {HEAD_BUDGET}, options_hash {doc['options_hash'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
