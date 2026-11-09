"""Write the owner's rulings from the review page into the story files.

  python -m NessieAI.tests.pipeline_scenarios.apply_rulings <rulings.json> <yyyy-mm-dd>

<rulings.json> maps story id to {"state": "accepted"} or {"state": "changed", "note": str,
"set": {field: value}}; Claude writes it from the review page's store. Unknown ids raise;
"set" may change any story field except id, family and ruling; any other key raises.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from NessieAI.tests.pipeline_scenarios.stories import STORIES_DIR, Story

_FROZEN = {"id", "family", "ruling"}
_FIELDS = set(Story.__dataclass_fields__)


def apply_rulings(rulings: dict[str, dict], today: str, directory: Path = STORIES_DIR) -> list[str]:
    files = {p: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*.json"))}
    where = {s["id"]: (p, i) for p, payload in files.items() for i, s in enumerate(payload["stories"])}
    unknown = sorted(set(rulings) - set(where))
    if unknown:
        raise KeyError(f"no story with id {unknown}")
    changed = []
    for story_id, ruling in rulings.items():
        if ruling.get("state") not in ("accepted", "changed"):
            raise ValueError(f"{story_id}: state must be accepted or changed")
        bad = set(ruling.get("set") or {}) & _FROZEN
        if bad:
            raise ValueError(f"{story_id}: cannot set {sorted(bad)}")
        unknown_fields = set(ruling.get("set") or {}) - _FIELDS
        if unknown_fields:
            raise ValueError(f"{story_id}: {sorted(unknown_fields)} is not a story field")
        path, index = where[story_id]
        row = files[path]["stories"][index]
        row.update(ruling.get("set") or {})
        row["ruling"] = {"state": ruling["state"], "date": today, "note": str(ruling.get("note") or "")}
        changed.append(story_id)
    for path, payload in files.items():
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sorted(changed)


if __name__ == "__main__":
    print(apply_rulings(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2]))
