"""The study merge (``graph_sync --merge-studies`` and ``--unmerge-studies``; docs/neo4j-schema.md, v1.2 "Study nodes
and IN_STUDY").

A SEEK study X may sit on two Study nodes: a legacy node L, ``Study {id: X}`` with no ``seek_study_id``, written
before schema 1.2, and a node K, ``Study {seek_study_id: X}`` with no ``id``, the 1.2 sync made beside it. The merge
makes L the study's one node: L keeps ``id``, ``title``, ``description`` and a non-empty DOI and PMID, gains
``seek_study_id: X``, takes every incoming IN_STUDY of K and loses a DOI or PMID that is ``""``; K is deleted. A
rekey in place gives L the key when there is no K, or an empty one. The next write of the study's node (the small
tables, a by-id sync, ``--studies``) makes its title, description and investigation SEEK's.

- ``read_index`` and ``classify``: which kind each SEEK study id is (``KINDS``, tested in that order). Read-only.
- ``plan``: the report the operator approves from (the dry run). Read-only.
- ``apply``: merge the approved ids, journaling every step to ``study_merge.tsv`` before its write.
- ``undo``: reverse merges from their journals, and re-create the IN_STUDY links ``in_study_removed.tsv`` archives
  hold.

Two tests say that L is study X's own node, not a graph-only paper: the marker (L's DOI and PMID are absent or
``""``, the empty values the pre-1.2 writer left) and the match (L's title is SEEK's title of X, and L's one
Investigation has SEEK's investigation id and title). Titles are compared here, on the values as stored, with
``str.strip()`` (which removes a no-break space, as Cypher's ``trim()`` and MySQL's ``TRIM`` do not) and, for an
Investigation, ``casefold()``. Nothing here takes the graph-write lock or records a run: the command does. Every
statement is in ``cypher.py``.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import sources, study_links, writer
from nextseek_api.graph_sync.writer import _batches, _one, _records, _run
from nextseek_graph import schema

log = logging.getLogger(__name__)

JOURNAL_FILE = "study_merge.tsv"
REPORT_FILE = "study_merge.json"
JOURNAL_HEADER = "study_id\trecord\tpayload\n"
OK, FAILED, DRY_RUN, PARTIAL = "ok", "failed", "dry_run", "partial"

ALREADY_MERGED = "already_merged"
SEEK_ONLY = "seek_only"
NOT_IN_SEEK = "not_in_seek"
DUPLICATE_SEEK_KEY = "duplicate_seek_key"
K_RELATIONSHIP = "k_relationship"
INVESTIGATION_COUNT = "investigation_count"
MERGE = "merge"
MERGE_OTHER_INVESTIGATION = "merge_other_investigation"
REKEY_IN_PLACE = "rekey_in_place"
LEGACY_ONLY = "legacy_only"
OTHER_SEEK_INVESTIGATION = "other_seek_investigation"
INVESTIGATION_TITLE_DIFFERS = "investigation_title_differs"
INVESTIGATION_NOT_SEEKS = "investigation_not_seeks"
ID_COLLISION = "id_collision"
PAPER = "paper"
KINDS = (ALREADY_MERGED, SEEK_ONLY, NOT_IN_SEEK, DUPLICATE_SEEK_KEY, K_RELATIONSHIP, INVESTIGATION_COUNT, MERGE,
         MERGE_OTHER_INVESTIGATION, REKEY_IN_PLACE, LEGACY_ONLY, OTHER_SEEK_INVESTIGATION,
         INVESTIGATION_TITLE_DIFFERS, INVESTIGATION_NOT_SEEKS, ID_COLLISION, PAPER)
ACTING = (MERGE, MERGE_OTHER_INVESTIGATION, REKEY_IN_PLACE)
APPROVABLE = ACTING + (ALREADY_MERGED,)
SPLIT_KINDS = (MERGE, MERGE_OTHER_INVESTIGATION)
MARKER, MATCH = "marker", "match"


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _stripped(value) -> str:
    return (value or "").strip()


def same_title(a, b) -> bool:
    """Equal titles, surrounding whitespace aside (``str.strip()``, as the full sync's rekey compares)."""
    return _stripped(a) == _stripped(b)


def same_title_ci(a, b) -> bool:
    """Equal titles, case and surrounding whitespace aside."""
    return _stripped(a).casefold() == _stripped(b).casefold()


@dataclass
class StudyNode:
    element_id: str
    props: dict
    investigations: list
    in_study: int
    sample_in_study: int
    other_relationships: list

    @property
    def id(self):
        return self.props.get("id")

    @property
    def seek_study_id(self):
        return self.props.get("seek_study_id")

    @property
    def title(self):
        return self.props.get("title")


@dataclass
class Index:
    nodes: list
    seek_studies: dict
    seek_investigations: dict


@dataclass
class Selection:
    study_id: int
    kind: str = ""
    test: str | None = None
    reason: str = ""
    legacy: StudyNode | None = None
    seek_keyed: StudyNode | None = None
    seek: dict | None = None
    seek_investigation: dict | None = None


def read_index(driver, db) -> Index:
    """Every Study node (``STUDY_NODES``) and SEEK's studies and investigations. Read-only."""
    nodes = [StudyNode(element_id=r["element_id"], props=dict(r["props"] or {}),
                       investigations=[dict(i) for i in (r["investigations"] or [])],
                       in_study=int(r["in_study"] or 0), sample_in_study=int(r["sample_in_study"] or 0),
                       other_relationships=list(r["other_relationships"] or []))
             for r in _records(_run(driver, db, q.STUDY_NODES, read=True))]
    return Index(nodes=nodes, seek_studies={int(s["id"]): s for s in sources.studies()},
                 seek_investigations={int(i["id"]): i for i in sources.investigations()})


def study_ids(index: Index) -> list[int]:
    """Every int some Study carries as ``id`` or ``seek_study_id`` (what ``all`` means), ascending."""
    return sorted({v for node in index.nodes for v in (node.id, node.seek_study_id) if _is_int(v)})


def _marker(node: StudyNode) -> bool:
    return node.props.get("DOI") in (None, "") and node.props.get("PMID") in (None, "")


def _match(node: StudyNode, seek: dict | None, seek_inv: dict | None) -> bool:
    if seek is None or seek_inv is None or len(node.investigations) != 1:
        return False
    inv = node.investigations[0]
    return (same_title(node.title, seek.get("title")) and inv.get("id") == seek.get("investigation_id")
            and same_title_ci(inv.get("title"), seek_inv.get("title")))


def classify(index: Index, study_id: int) -> Selection:
    """The first kind of ``KINDS`` that applies to SEEK study ``study_id`` (the spec's selection table)."""
    x = int(study_id)
    legacy = [n for n in index.nodes if n.id == x and n.seek_study_id is None]
    keyed = [n for n in index.nodes if n.seek_study_id == x]
    seek = index.seek_studies.get(x)
    inv_id = None if seek is None else seek.get("investigation_id")
    seek_inv = None if inv_id is None else index.seek_investigations.get(inv_id)
    sel = Selection(study_id=x, seek=seek, seek_investigation=seek_inv, legacy=legacy[0] if legacy else None,
                    seek_keyed=keyed[0] if len(keyed) == 1 and keyed[0].id is None else None)

    def done(kind, reason="", test=None):
        sel.kind, sel.reason, sel.test = kind, reason, test
        return sel

    if len(keyed) == 1 and keyed[0].id == x:
        return done(ALREADY_MERGED)
    l = sel.legacy
    if l is None:
        return done(SEEK_ONLY)
    if seek is None:
        return done(NOT_IN_SEEK, "SEEK has no study with this id")
    if len(keyed) > 1 or (keyed and keyed[0].id is not None):
        return done(DUPLICATE_SEEK_KEY, f"{len(keyed)} Study nodes carry seek_study_id {x}, or one also carries "
                                        "another id")
    k = sel.seek_keyed
    if k is not None and k.other_relationships:
        return done(K_RELATIONSHIP, "the seek-keyed node also holds " + ", ".join(sorted(set(k.other_relationships))))
    if len(l.investigations) != 1 or (k is not None and len(k.investigations) != 1):
        return done(INVESTIGATION_COUNT, "the legacy or the seek-keyed node is under no Investigation or several")
    marker, match = _marker(l), _match(l, seek, seek_inv)
    test = MARKER if marker else MATCH if match else None
    l_inv = l.investigations[0]
    k_inv = None if k is None else k.investigations[0]
    same_node = k is not None and l_inv["element_id"] == k_inv["element_id"]
    seek_inv_ids = set(index.seek_investigations)
    if k is not None and same_node and (marker or (match and k.in_study > 0)):
        return done(MERGE, test=test)
    if (marker and k is not None and not same_node and same_title_ci(l_inv.get("title"), k_inv.get("title"))
            and k_inv.get("id") == inv_id and l_inv.get("id") not in seek_inv_ids):
        return done(MERGE_OTHER_INVESTIGATION, "the legacy node's Investigation is one the sync never wrote",
                    test=MARKER)
    if match and (k is None or k.in_study == 0):
        return done(REKEY_IN_PLACE, test=MATCH)
    if marker and k is None:
        return done(LEGACY_ONLY, "nothing is split: its samples stay paper samples", test=MARKER)
    if marker and k is not None and not same_node:
        if not same_title_ci(l_inv.get("title"), k_inv.get("title")):
            return done(INVESTIGATION_TITLE_DIFFERS, "the two nodes' Investigations have different titles",
                        test=MARKER)
        if l_inv.get("id") in seek_inv_ids and k_inv.get("id") in seek_inv_ids:
            return done(OTHER_SEEK_INVESTIGATION, "the two nodes sit under two SEEK investigations of one title",
                        test=MARKER)
    if (marker or match) and k is not None:
        return done(INVESTIGATION_NOT_SEEKS, "the seek-keyed node's Investigation is not SEEK's investigation of "
                                             "this study", test=test)
    if k is not None:
        return done(ID_COLLISION, "two different studies share this id")
    return done(PAPER, "a graph-only paper: its title or Investigation is not SEEK's")


def _node_view(node: StudyNode | None) -> dict | None:
    if node is None:
        return None
    return {"element_id": node.element_id, "id": node.id, "seek_study_id": node.seek_study_id, "title": node.title,
            "DOI": node.props.get("DOI"), "PMID": node.props.get("PMID"), "in_study": node.in_study,
            "investigations": node.investigations}


def _entry(sel: Selection) -> dict:
    seek = sel.seek or {}
    legacy_description = None if sel.legacy is None else sel.legacy.props.get("description")
    return {"study_id": sel.study_id, "kind": sel.kind, "test": sel.test, "reason": sel.reason,
            "seek_title": seek.get("title"), "seek_investigation_id": seek.get("investigation_id"),
            "legacy": _node_view(sel.legacy), "seek_keyed": _node_view(sel.seek_keyed),
            "description_differs": sel.legacy is not None and (legacy_description or None) != (seek.get(
                "description") or None),
            "seek_description_empty": not _stripped(seek.get("description"))}


def _sources(driver, db, element_id: str) -> list[dict]:
    return [{"element_id": r["element_id"], "labels": list(r["labels"] or []), "id": r["id"]}
            for r in _records(_run(driver, db, q.STUDY_SOURCES, {"element_id": element_id}, read=True))]


def _detail(driver, db, sel: Selection) -> dict:
    """The samples on L, on K and on both, K's other sources, and what ``--studies`` will do to them. Read-only."""
    x = sel.study_id
    on_l = _sources(driver, db, sel.legacy.element_id)
    on_k = [] if sel.seek_keyed is None else _sources(driver, db, sel.seek_keyed.element_id)
    samples_l = {s["id"] for s in on_l if schema.SAMPLE in s["labels"] and _is_int(s["id"])}
    samples_k = {s["id"] for s in on_k if schema.SAMPLE in s["labels"] and _is_int(s["id"])}
    others = Counter(label for s in on_k if schema.SAMPLE not in s["labels"] for label in s["labels"])
    everyone = sorted(samples_l | samples_k)
    seek_of: dict[int, set] = {}
    for link in sources.seek_study_links_for(everyone):
        seek_of.setdefault(int(link["sample_id"]), set()).add(int(link["study_id"]))
    current = writer.sample_studies(driver, db, everyone)
    preview = Counter()
    for sample_id in everyone:
        links = current.get(sample_id, [])
        if any(link["seek_study_id"] is None and link["id"] != x for link in links):
            preview["paper_samples"] += 1
        elif not seek_of.get(sample_id):
            preview["no_seek_study"] += 1
        elif x in seek_of[sample_id]:
            preview["kept"] += 1
        else:
            preview["leaves"] += 1
    return {"samples": {"on_legacy": len(samples_l - samples_k), "on_seek_keyed": len(samples_k - samples_l),
                        "on_both": len(samples_l & samples_k)},
            "seek_keyed_other_sources": dict(others),
            "studies_preview": {key: preview.get(key, 0) for key in ("kept", "leaves", "no_seek_study",
                                                                     "paper_samples")}}


def _left_empty(index: Index, selections) -> list[dict]:
    """The legacy Investigations a merge_other_investigation would leave with no Study."""
    out = []
    for sel in selections:
        if sel.kind != MERGE_OTHER_INVESTIGATION:
            continue
        inv = sel.legacy.investigations[0]
        holders = [n for n in index.nodes if n.element_id != sel.legacy.element_id
                   and any(i["element_id"] == inv["element_id"] for i in n.investigations)]
        if not holders:
            out.append({"study_id": sel.study_id, "investigation": inv})
    return out


def plan(driver, db, ids=None, *, detail: bool = True) -> dict:
    """The merge's dry run: each SEEK study id's kind and the facts the operator approves from (the spec's section
    5.4). ``ids`` None means every id some Study carries; ``detail`` reads each acting id's samples too. Read-only."""
    index = read_index(driver, db)
    wanted = study_ids(index) if ids is None else sorted({int(i) for i in ids})
    selections = [classify(index, x) for x in wanted]
    report = {"ids": wanted, "kinds": {s.study_id: s.kind for s in selections},
              "counts": dict(Counter(s.kind for s in selections)), "studies": []}
    for sel in selections:
        if sel.kind == SEEK_ONLY:
            continue
        entry = _entry(sel)
        if detail and sel.kind in ACTING:
            entry.update(_detail(driver, db, sel))
        report["studies"].append(entry)
    report["approval_line"] = ",".join(str(s.study_id) for s in selections if s.kind in (MERGE, REKEY_IN_PLACE))
    report["merge_other_investigation"] = [s.study_id for s in selections if s.kind == MERGE_OTHER_INVESTIGATION]
    report["id_collisions"] = [s.study_id for s in selections if s.kind == ID_COLLISION]
    report["legacy_only"] = [s.study_id for s in selections if s.kind == LEGACY_ONLY]
    report["investigations_left_empty"] = _left_empty(index, selections)
    return report
