"""Generate nextseek_api/services/controlled_vocabularies.json from the ontology files.

The ontology files in NessieAI/chat_nextseek/src/chat_nextseek/context/ are the source of
truth (ONTOLOGIES.json names them). Each entry with an ``upload_vocabulary`` feeds one
upload-sheet dropdown: the vocabulary is its TERMs in file order, and each exact column name
in its attribute patterns maps to it. Stdlib only, no Django.

    python -m NessieAI.build_tools.gen_house_vocabularies          # write
    python -m NessieAI.build_tools.gen_house_vocabularies --check  # exit 1 when stale
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / "NessieAI/chat_nextseek/src/chat_nextseek/context"
TARGET = ROOT / "nextseek_api/services/controlled_vocabularies.json"
COMMAND = "python -m NessieAI.build_tools.gen_house_vocabularies"


def build() -> dict:
    index = json.loads((CONTEXT / "ONTOLOGIES.json").read_text(encoding="utf-8"))
    field_map: dict[str, str] = {}
    vocabularies: dict[str, list[str]] = {}
    for entry in index["ontologies"]:
        name = entry.get("upload_vocabulary")
        if not name:
            continue
        records = json.loads((CONTEXT / entry["file"]).read_text(encoding="utf-8"))
        vocabularies[name] = [r["TERM"] for r in records]
        for pattern in entry["attributes"]:
            column = pattern.rsplit(".", 1)[1]
            if "*" not in column:
                field_map[column] = name
    return {
        "_source": (
            "Generated from the ontology files in NessieAI/chat_nextseek/src/chat_nextseek/context/ "
            f"(ONTOLOGIES.json lists them); do not edit by hand. Regenerate with `{COMMAND}`. "
            "Each vocabulary is the TERM list of one ontology file that names an upload_vocabulary."
        ),
        "_field_map": "Which sample column each vocabulary governs.",
        "field_map": field_map,
        "vocabularies": vocabularies,
    }


def render() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


def check() -> str | None:
    """None when the committed file is current, else a one-line reason."""
    if not TARGET.exists():
        return f"{TARGET.relative_to(ROOT)} is missing; run `{COMMAND}`"
    if TARGET.read_text(encoding="utf-8") != render():
        return f"{TARGET.relative_to(ROOT)} is stale; run `{COMMAND}`"
    return None


def main(argv: list[str]) -> int:
    if "--check" in argv:
        reason = check()
        if reason:
            print(reason)
            return 1
        return 0
    TARGET.write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
