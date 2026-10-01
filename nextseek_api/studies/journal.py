"""The studies tool's journal (tool spec 7.1): ``journal.jsonl`` in the run directory, append-only.

One JSON object a line: ``seq``, ``at`` (UTC), ``run_id``, ``step``, ``event`` and the step's fields. ``append``
writes the line, flushes and fsyncs before it returns, and every write of apply and rollback happens only after its
line has returned. Lines are ASCII (other characters escaped), so no cut can split a character and no line separator
of Unicode's can split a line. The reader splits on newline bytes only and ignores a line it cannot decode or parse
(a crash inside a write leaves a torn last line), counting it; a new ``Journal`` on such a file first ends the torn
line, so the next line is whole. A torn line's write never began. The journal carries
the login name and SEEK person id, never a credential: a field named like one is refused.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

JOURNAL_FILE = "journal.jsonl"
FORBIDDEN_FIELDS = frozenset({"password", "secret", "authorization", "credential", "token"})


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_journal(path) -> tuple[list[dict], int]:
    path = Path(path)
    if not path.exists():
        return [], 0
    lines, bad = [], 0
    for raw in path.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:      # UnicodeDecodeError included: a line cut inside a character
            bad += 1
            continue
        if isinstance(obj, dict):
            lines.append(obj)
        else:
            bad += 1
    return lines, bad


class Journal:
    def __init__(self, path, *, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines, _bad = read_journal(self.path)
        self._seq = max((int(l.get("seq", 0)) for l in lines), default=0)
        if self.path.exists() and self.path.stat().st_size:
            with open(self.path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                torn = fh.read(1) != b"\n"
            if torn:
                with open(self.path, "ab") as fh:
                    fh.write(b"\n")
                    fh.flush()
                    os.fsync(fh.fileno())

    def append(self, step: str, event: str, **fields) -> dict:
        bad = sorted(k for k in fields if k.lower() in FORBIDDEN_FIELDS)
        if bad:
            raise ValueError(f"{bad}: a credential is never journaled")
        self._seq += 1
        line = {"seq": self._seq, "at": _utc(), "run_id": self.run_id, "step": step, "event": event, **fields}
        data = (json.dumps(line, sort_keys=True, default=str) + "\n").encode("utf-8")
        with open(self.path, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return line


@dataclass
class JournalState:
    started: bool = False
    studies: dict = field(default_factory=dict)
    clones: dict = field(default_factory=dict)
    map_pairs: Optional[list] = None
    map_rows: Optional[list] = None
    units: dict = field(default_factory=dict)
    pubs_rows: dict = field(default_factory=dict)
    pubs_done: bool = False
    graph_done: set = field(default_factory=set)    # the investigations the graph step finished; None: all
    apply_done: bool = False
    undone_units: set = field(default_factory=set)
    undo_parts: set = field(default_factory=set)
    undone: bool = False


def _outcome(slot: dict, line: dict) -> None:
    if line["event"] == "intent":
        slot["intent"] = line.get("payload")
    elif line["event"] in ("done", "adopted"):
        slot["seek_id"] = line.get("seek_id")
        slot["how"] = line["event"]


def journal_state(lines: list[dict]) -> JournalState:
    st = JournalState()
    for line in lines:
        step, event = line.get("step"), line.get("event")
        if step == "run" and event == "start":
            st.started = True
        elif step == "study":
            _outcome(st.studies.setdefault(line["target_key"], {"intent": None, "seek_id": None, "how": None}), line)
        elif step == "clone":
            key = (line["target_key"], int(line["source_assay_id"]))
            _outcome(st.clones.setdefault(key, {"intent": None, "seek_id": None, "how": None}), line)
        elif step == "map":
            if event == "intent":
                st.map_pairs = (st.map_pairs or []) + list(line.get("pairs") or [])
            elif event == "done":
                st.map_rows = (st.map_rows or []) + list(line.get("rows") or [])
        elif step == "links":
            unit = st.units.setdefault(int(line["unit"]), {"intent": None, "prepared": None, "committed": False,
                                                           "refused": False})
            if event == "intent":
                unit.update(intent=line, prepared=None, refused=False)
            elif event == "prepared":
                unit["prepared"] = line
            elif event == "committed":
                unit["committed"] = True
            elif event == "refused":
                unit["refused"] = True
        elif step == "pubs":
            if event == "intent":
                for sid, old, new in line.get("rows", []):
                    st.pubs_rows[int(sid)] = (old, new)
            elif event == "done":
                st.pubs_done = True
        elif step == "apply" and event == "done":
            st.apply_done = True
        elif step == "graph" and event == "done":
            st.graph_done.add(line.get("investigation"))
        elif step == "undo" and event == "done":
            part = line.get("part")
            st.undo_parts.add(part)
            if part == "unit":
                st.undone_units.add(int(line["unit"]))
            if part == "run":
                st.undone = True
    return st
