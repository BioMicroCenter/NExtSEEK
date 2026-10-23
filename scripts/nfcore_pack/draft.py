#!/usr/bin/env python3
"""Draft a pipeline's Nessie files from its pinned nf-core schemas and nf-test snapshots.

Run inside the stack image, from the repo root (it reads public GitHub URLs):
  docker run --rm -v "$PWD":/src -w /src -e PYTHONPATH=/src/NessieAI/chat_nextseek/src \
    nextseek-nextseek:latest /app/.venv/bin/python scripts/nfcore_pack/draft.py sarek 3.10.0
Writes outputs/nfcore_pack/<pipeline>-<revision>/draft.json (outputs/ is untracked) and
prints the traps. Nothing is written into the catalog, the templates or the maps.
"""
import argparse
import json
import sys
from pathlib import Path

import requests

from chat_nextseek.seqera.pack_draft import draft


def _fetch(url: str) -> bytes:
    resp = requests.get(url, timeout=(5, 30), headers={"User-Agent": "nextseek-pack-draft"})
    resp.raise_for_status()
    return resp.content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pipeline")
    parser.add_argument("revision")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = draft(args.pipeline, args.revision, _fetch)
    out = args.out or Path("outputs/nfcore_pack") / f"{args.pipeline}-{args.revision}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "draft.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(f"draft written: {out / 'draft.json'}")
    print(f"snapshots read: {len(result['snapshots'])}")
    for line in result["traps"]:
        print(f"TRAP {line}")
    for line in result["default_check_errors"]:
        print(f"DEFAULT FAILS SCHEMA {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
