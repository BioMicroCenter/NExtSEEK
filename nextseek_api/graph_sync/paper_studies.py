"""The studies tool's graph writes (the tool spec, 7.6 and 7.7). Statements in cypher.py; nothing here reads MySQL.

- ``retire_paper_links``: the IN_STUDY of the listed samples to one graph-only paper Study, each archived to
  ``in_study_removed.tsv`` (the studies release's archive and header) and flushed before its delete, in batches of
  ``REL_CHUNK``.
- ``delete_empty_paper_study_nodes``: a graph-only paper Study (an ``id``, no ``seek_study_id``) that holds no
  IN_STUDY and nothing but its IN_INVESTIGATION, archived with its properties and investigations first. A SEEK
  study's node is never deleted here: Study nodes of SEEK studies are not deleted (the studies release).
- ``restore_paper_links``: rollback: the archived paper nodes, then the archived paper links, each only where
  missing; limited to ``paper_ids`` and, given ``sample_ids``, to those samples' links and the papers they name.

The caller holds the graph-write lock and has decided which samples may leave their paper (the studies tool's graph
step: those MySQL now places in the paper's SEEK study).
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.study_links import ARCHIVE_FILE as IN_STUDY_REMOVED_FILE
from nextseek_api.graph_sync.writer import (IN_STUDY_ARCHIVE_HEADER, REL_CHUNK, _append_rows, _batches, _one,
                                            _records, _run, _tsv_field)

STUDY_NODES_REMOVED_FILE = "study_nodes_removed.jsonl"
PAPER_PATH = "studies_tool_paper"   # the archive's path column for a link this module removed


def retire_paper_links(driver, db, paper_id: int, sample_ids, archive_path) -> dict:
    wanted = sorted({int(s) for s in sample_ids})
    found: list = []
    for batch in _batches(wanted, REL_CHUNK):
        found.extend(_records(_run(driver, db, q.PAPER_IN_STUDY_OF, {"paper_id": paper_id, "ids": batch}, read=True)))
    if not found:
        return {"paper_links_found": 0, "paper_links_retired": 0}
    _append_rows(str(archive_path), IN_STUDY_ARCHIVE_HEADER,
                 ["\t".join((_tsv_field(r["sample_id"]), "", _tsv_field(paper_id), _tsv_field(r["element_id"]),
                             PAPER_PATH)) + "\n" for r in found])
    retired = 0
    for batch in _batches([r["element_id"] for r in found], REL_CHUNK):
        retired += _one(_run(driver, db, q.DELETE_PAPER_IN_STUDY, {"paper_id": paper_id, "element_ids": batch}),
                        "deleted")
    return {"paper_links_found": len(found), "paper_links_retired": retired}


def _append_json_lines(path, rows) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def delete_empty_paper_study_nodes(driver, db, paper_ids, *, archive_path) -> dict:
    ids = sorted({int(i) for i in paper_ids})
    rows = _records(_run(driver, db, q.EMPTY_PAPER_STUDY_NODES, {"ids": ids}, read=True)) if ids else []
    if not rows:
        return {"study_nodes_empty": 0, "study_nodes_deleted": 0}
    _append_json_lines(str(archive_path), [{"study_id": r["study_id"], "element_id": r["element_id"],
                                            "props": dict(r["props"] or {}),
                                            "investigation_ids": list(r["investigation_ids"] or [])} for r in rows])
    deleted = sum(_one(_run(driver, db, q.DELETE_EMPTY_PAPER_STUDY_NODES, {"element_ids": batch}), "deleted")
                  for batch in _batches([r["element_id"] for r in rows], REL_CHUNK))
    return {"study_nodes_empty": len(rows), "study_nodes_deleted": deleted}


def _node_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _paper_link_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh, delimiter="\t"):
            if rec.get("path") == PAPER_PATH and rec.get("sample_id") and rec.get("study_id"):
                rows.append({"sample_id": int(rec["sample_id"]), "study_id": int(rec["study_id"])})
    return rows


def restore_paper_links(driver, db, graph_dir, *, paper_ids=None, sample_ids=None) -> dict:
    graph_dir = Path(graph_dir)
    rows = _paper_link_rows(graph_dir / IN_STUDY_REMOVED_FILE)
    keep = None if paper_ids is None else {int(i) for i in paper_ids}
    if keep is not None:
        rows = [r for r in rows if r["study_id"] in keep]
    if sample_ids is not None:
        wanted = {int(s) for s in sample_ids}
        rows = [r for r in rows if r["sample_id"] in wanted]
        keep = {r["study_id"] for r in rows}     # a paper none of whose links comes back stays deleted
    papers = [n for n in _node_rows(graph_dir / STUDY_NODES_REMOVED_FILE) if n.get("study_id") is not None
              and (keep is None or int(n["study_id"]) in keep)]
    restored = 0
    for batch in _batches(papers, REL_CHUNK):
        restored += _one(_run(driver, db, q.RESTORE_PAPER_STUDY_NODES, {"rows": batch}), "restored")
    links = 0
    for batch in _batches(rows, REL_CHUNK):
        links += _one(_run(driver, db, q.RESTORE_PAPER_IN_STUDY, {"rows": batch}), "restored")
    return {"study_nodes_restored": restored, "paper_links_restored": links}
