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
OK, FAILED, DRY_RUN, PARTIAL, REFUSED = "ok", "failed", "dry_run", "partial", "refused"

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


def _detail(driver, db, sel: Selection, acting_ids=frozenset()) -> dict:
    """The samples on L, on K and on both, K's other sources, and what ``--studies`` will do to them. Read-only.

    ``studies_preview`` counts every sample once from SEEK's studies alone: ``kept`` (SEEK files it under X),
    ``leaves`` (SEEK files it under other studies only, so its link to X goes, a paper sample's included) and
    ``no_seek_study`` (SEEK files it under none: kept and reported). ``paper_samples`` counts, besides, the samples
    that are also on a graph-only paper; a link to the legacy node of an id in ``acting_ids`` is not a paper link,
    since that node becomes a SEEK study's."""
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
    not_papers = set(acting_ids) | {x}
    preview = Counter()
    for sample_id in everyone:
        links = current.get(sample_id, [])
        if any(link["seek_study_id"] is None and link["id"] not in not_papers for link in links):
            preview["paper_samples"] += 1
        if not seek_of.get(sample_id):
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


def format_approval(kinds: dict) -> str:
    """The approval line: ``id:kind`` for each id, ascending, comma-separated (``3:merge,6:rekey_in_place``)."""
    return ",".join(f"{int(x)}:{kinds[x]}" for x in sorted(kinds, key=int))


def parse_approval(text: str) -> dict:
    """An approval line read back: ``{id: kind}`` in the order given, a bare id mapping to None. Raises ValueError
    for a part that is not an id or ``id:kind`` with a kind of ``KINDS``, and for one id given two kinds."""
    approved: dict[int, str | None] = {}
    for part in (text or "").split(","):
        study_id, sep, kind = (p.strip() for p in part.strip().partition(":"))
        if not study_id.isdigit():
            raise ValueError(f"not a SEEK study id: {part.strip()!r}")
        if sep and kind not in KINDS:
            raise ValueError(f"not a merge kind: {kind!r} (study {study_id})")
        x, kind = int(study_id), (kind if sep else None)
        if x in approved and approved[x] != kind:
            raise ValueError(f"study {x} is given two kinds: {approved[x]} and {kind}")
        approved[x] = kind
    return approved


def plan(driver, db, ids=None, *, detail: bool = True) -> dict:
    """The merge's dry run: each SEEK study id's kind and the facts the operator approves from (the spec's section
    5.4). ``ids`` None means every id some Study carries; ``detail`` reads each acting id's samples too. Read-only.

    ``approval_line`` is what the operator approves: every id of an acting kind with that kind (``format_approval``),
    a merge_other_investigation included, so approving the line leaves no acting id behind. Those ids are also listed
    apart in ``merge_other_investigation``, with the Investigations they would leave empty."""
    index = read_index(driver, db)
    wanted = study_ids(index) if ids is None else sorted({int(i) for i in ids})
    selections = [classify(index, x) for x in wanted]
    report = {"ids": wanted, "kinds": {s.study_id: s.kind for s in selections},
              "counts": dict(Counter(s.kind for s in selections)), "studies": []}
    acting = frozenset(s.study_id for s in selections if s.kind in ACTING)
    for sel in selections:
        if sel.kind == SEEK_ONLY:
            continue
        entry = _entry(sel)
        if detail and sel.kind in ACTING:
            entry.update(_detail(driver, db, sel, acting))
        report["studies"].append(entry)
    report["approval_line"] = format_approval({s.study_id: s.kind for s in selections if s.kind in ACTING})
    report["merge_other_investigation"] = [s.study_id for s in selections if s.kind == MERGE_OTHER_INVESTIGATION]
    report["id_collisions"] = [s.study_id for s in selections if s.kind == ID_COLLISION]
    report["legacy_only"] = [s.study_id for s in selections if s.kind == LEGACY_ONLY]
    report["investigations_left_empty"] = _left_empty(index, selections)
    return report


# --- apply ---------------------------------------------------------------------------------------------------------

def _journal(path: str, study_id: int, record: str, payloads) -> None:
    """Append one journal line per payload and flush it to disk, before the write it describes."""
    lines = [f"{int(study_id)}\t{record}\t{json.dumps(p, sort_keys=True, ensure_ascii=True, default=str)}\n"
             for p in payloads]
    writer._append_rows(path, JOURNAL_HEADER, lines)


def _journaled_node(node: StudyNode | None) -> dict | None:
    if node is None:
        return None
    return {"element_id": node.element_id, "props": node.props, "investigation": node.investigations[0]}


def _merge_one(driver, db, sel: Selection, journal: str, batch: int) -> None:
    x, legacy, keyed = sel.study_id, sel.legacy, sel.seek_keyed
    # L's own sources go in the plan line, before any write: an undo moves every other source it then finds on L
    # (one that reached study X through the key after the merge) to the re-created K.
    _journal(journal, x, "plan", [{"kind": sel.kind, "test": sel.test, "legacy": _journaled_node(legacy),
                                   "seek_keyed": _journaled_node(keyed),
                                   "legacy_sources": _sources(driver, db, legacy.element_id)}])
    if keyed is not None and sel.kind != REKEY_IN_PLACE:
        while True:
            rows = _records(_run(driver, db, q.STUDY_SOURCES_BATCH,
                                 {"k": keyed.element_id, "l": legacy.element_id, "limit": batch}, read=True))
            if not rows:
                break
            _journal(journal, x, "source", [{"element_id": r["element_id"], "labels": list(r["labels"] or []),
                                             "id": r["id"], "place": "on_both" if r["on_l"] else "only_on_k"}
                                            for r in rows])
            moved = _one(_run(driver, db, q.MOVE_IN_STUDY, {"k": keyed.element_id, "l": legacy.element_id,
                                                            "sources": [r["element_id"] for r in rows]}), "moved")
            if moved != len(rows):
                raise RuntimeError(f"study {x}: {len(rows)} sources read on the seek-keyed node but {moved} moved; "
                                   "a rerun with the same run directory finishes it")
    new_investigation = (keyed.investigations[0]["element_id"]
                         if sel.kind == MERGE_OTHER_INVESTIGATION and keyed is not None else None)
    merged = _one(_run(driver, db, q.FINISH_STUDY_MERGE,
                       {"l": legacy.element_id, "k": None if keyed is None else keyed.element_id, "study_id": x,
                        "new_investigation": new_investigation}), "merged")
    if merged != 1:
        raise RuntimeError(f"study {x}: the last step found the seek-keyed node still holding a relationship, or the "
                           "legacy node changed since it was read; that step wrote nothing")
    _journal(journal, x, "done", [{"kind": sel.kind}])


def _unfinished_kinds(journal: str) -> dict:
    """Per study id, the kind of the first ``plan`` line of an attempt this journal holds no ``done`` line for: the
    kind a rerun into it is held to. A line that cannot be read (a crash cut it short) describes no write; skipped."""
    pending: dict[int, str] = {}
    if not os.path.isfile(journal):
        return pending
    with open(journal, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) != 3 or not parts[0].isdigit():
                continue
            x, record = int(parts[0]), parts[1]
            if record == "done":
                pending.pop(x, None)
            elif record == "plan" and x not in pending:
                try:
                    pending[x] = json.loads(parts[2])["kind"]
                except (ValueError, KeyError, TypeError):
                    continue
    return pending


def apply(driver, db, approved: dict, *, run_dir: str, batch: int = writer.REL_CHUNK) -> dict:
    """Merge or rekey each id of ``approved`` (id to the kind the operator approved from the dry run's approval
    line), in id order, under the caller's hold of the graph-write lock (the spec's section 5.3). Each id's kind is
    read again first: an ``already_merged`` id is counted and not written; an id that reads another kind than its
    approved one stops the run before its first write, ids done before it staying done. A rerun given the same
    ``run_dir`` appends to its journal and is held to the kind that journal recorded for an id it did not finish, so
    a merge by the match that moved every source before a crash, and now reads rekey_in_place, is still finished.
    The status is ``refused`` when the run stopped before writing anything, ``failed`` when it stopped part way.
    Every step is journaled to ``study_merge.tsv`` in ``run_dir`` before its write. Raises RuntimeError when a move
    or the last step does not do what was read."""
    run_dir = os.path.abspath(run_dir)
    journal = os.path.join(run_dir, JOURNAL_FILE)
    journaled = _unfinished_kinds(journal)
    result = {"status": OK, "run_dir": run_dir, "journal": journal, "merged": [], "already_merged": [],
              "stopped_at": None}
    for x in sorted(int(i) for i in approved):
        sel = classify(read_index(driver, db), x)
        if sel.kind == ALREADY_MERGED:
            result["already_merged"].append(x)
            continue
        expected = journaled.get(x, approved[x])
        if expected == MERGE and sel.kind == REKEY_IN_PLACE and sel.seek_keyed is not None:
            # A merge by the match whose every source moved before a crash stopped it: K holds no IN_STUDY now, so
            # the id reads rekey_in_place, whose last step is the merge's own. Finish it as that.
            log.info("study %s: its merge moved every source before it stopped; finishing it", x)
        elif sel.kind != expected or sel.kind not in ACTING:
            source = (f"its journal's {expected}, from a run it did not finish" if x in journaled
                      else f"{expected}, as approved")
            result.update(status=FAILED if result["merged"] else REFUSED, stopped_at=x,
                          problem=f"study {x} reads {sel.kind} now, not {source}; it and every later id are left "
                                  "as they are: run the dry run again and approve what it prints")
            return result
        _merge_one(driver, db, sel, journal, batch)
        result["merged"].append({"study_id": x, "kind": sel.kind})
    return result


# --- undo ----------------------------------------------------------------------------------------------------------

def _opt_int(text: str):
    return int(text) if text not in ("", None) else None


def read_journals(paths) -> tuple[dict, list]:
    """Every journal line per study id across ``paths`` (a journal, or a run directory holding ``study_merge.tsv``
    and/or ``in_study_removed.tsv``), and the archives found. The first ``plan`` line of an id wins (a crash and its
    rerun journal the same nodes); sources, and the legacy node's own sources of every plan line, are merged by
    element id (a source a crashed run moved is in its ``source`` lines, which take precedence in the undo).
    ``legacy_sources_known`` is False when a plan line holds no list of them. Raises ValueError for a path that is
    neither."""
    per_id: dict[int, dict] = {}
    archives: list[str] = []
    for raw in paths:
        path = os.path.abspath(raw)
        journal = None
        if os.path.isdir(path):
            candidate, archive = os.path.join(path, JOURNAL_FILE), os.path.join(path, study_links.ARCHIVE_FILE)
            if os.path.isfile(archive):
                archives.append(archive)
            if os.path.isfile(candidate):
                journal = candidate
            elif not os.path.isfile(archive):
                raise ValueError(f"{path} holds no journal ({JOURNAL_FILE}) and no archive "
                                 f"({study_links.ARCHIVE_FILE})")
        elif os.path.isfile(path):
            journal = path
        else:
            raise ValueError(f"not a journal or a run directory: {path}")
        if journal is None:
            continue
        with open(journal, encoding="utf-8") as fh:
            if fh.readline() != JOURNAL_HEADER:
                raise ValueError(f"{journal} is not a study merge journal")
            for line in fh:
                study_id, record, payload = line.rstrip("\n").split("\t", 2)
                entry = per_id.setdefault(int(study_id), {"plan": None, "sources": {}, "legacy_sources": {},
                                                          "legacy_sources_known": True})
                data = json.loads(payload)
                if record == "plan":
                    if entry["plan"] is None:
                        entry["plan"] = data
                    if "legacy_sources" not in data:
                        entry["legacy_sources_known"] = False
                    for source in data.get("legacy_sources") or []:
                        entry["legacy_sources"].setdefault(source["element_id"], source)
                elif record == "source":
                    entry["sources"].setdefault(data["element_id"], data)
    return per_id, archives


def _read_archives(paths) -> list[dict]:
    rows, seen = [], set()
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            if fh.readline() != writer.IN_STUDY_ARCHIVE_HEADER:
                raise ValueError(f"{path} is not an IN_STUDY archive")
            for line in fh:
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 3 or not fields[0]:
                    continue
                key = (int(fields[0]), _opt_int(fields[1]), _opt_int(fields[2]))
                if key not in seen:
                    seen.add(key)
                    rows.append({"sample_id": key[0], "seek_study_id": key[1], "study_id": key[2]})
    return rows


def _undo_state(index: Index, x: int, plan_payload: dict):
    """("merged", None), ("nodes_restored", K's element id or None), or (None, reason)."""
    legacy_eid = plan_payload["legacy"]["element_id"]
    legacy = next((n for n in index.nodes if n.element_id == legacy_eid), None)
    if legacy is None:
        return None, "the legacy node is gone"
    keyed = [n for n in index.nodes if n.seek_study_id == x]
    if legacy.seek_study_id == x and keyed == [legacy]:
        return "merged", None
    if legacy.seek_study_id is None:
        if plan_payload["seek_keyed"] is None and not keyed:
            return "nodes_restored", None
        if plan_payload["seek_keyed"] is not None and len(keyed) == 1 and keyed[0].id is None:
            return "nodes_restored", keyed[0].element_id
    return None, "the legacy node and the nodes carrying this seek_study_id are not as the merge left them"


def _class_labels(labels) -> list[str]:
    """A node's labels without a sample type's label (``T_...``), which a rename of the type changes."""
    return sorted(label for label in labels or [] if not schema.is_type_label(label))


def _same_node(journaled: dict, now: dict | None) -> bool:
    """Whether the node a journaled element id names now is the journaled node. Neo4j hands a freed element id to a
    new node, so its ``id`` and labels (a type label aside) must match too."""
    return bool(now and now["found"]) and now["id"] == journaled.get("id") and set(
        _class_labels(journaled.get("labels"))) <= set(now["labels"])


def _nodes_now(driver, db, element_ids, batch: int) -> dict:
    """What each element id names now: ``{element id: {"found", "id", "labels"}}``. Read-only."""
    out = {}
    for part in _batches(list(element_ids), batch):
        for r in _records(_run(driver, db, q.UNDO_SOURCE_NODES, {"element_ids": part}, read=True)):
            out[r["element_id"]] = {"found": bool(r["found"]), "id": r["id"], "labels": list(r["labels"] or [])}
    return out


def _identity(element_id: str, journaled: dict) -> dict:
    return {"element_id": element_id, "id": journaled.get("id"), "labels": _class_labels(journaled.get("labels"))}


def _not_restored(study_id: int, node: str, investigation: dict | None) -> dict:
    investigation = investigation or {}
    return {"study_id": study_id, "node": node,
            "investigation": {"id": investigation.get("id"), "title": investigation.get("title")}}


def unlisted_journals(run_root: str, paths, study_ids) -> list[str]:
    """The merge journals under ``run_root`` (``<run root>/<run directory>/study_merge.tsv``) that ``paths`` do not
    name and that hold a ``plan`` line for one of ``study_ids``: a crashed merge and its rerun into another run
    directory, or a later approval of the same id, are undone only together."""
    given = set()
    for raw in paths:
        path = os.path.realpath(raw)
        given.add(os.path.join(path, JOURNAL_FILE) if os.path.isdir(path) else path)
    wanted, found = {int(x) for x in study_ids}, []
    root = os.path.realpath(run_root)
    if not os.path.isdir(root):
        return found
    for name in sorted(os.listdir(root)):
        journal = os.path.join(root, name, JOURNAL_FILE)
        if journal in given or not os.path.isfile(journal):
            continue
        with open(journal, encoding="utf-8", errors="replace") as fh:
            named = {int(p[0]) for p in (line.split("\t", 2) for line in fh)
                     if len(p) == 3 and p[1] == "plan" and p[0].isdigit()}
        if named & wanted:
            found.append(journal)
    return found


def undo(driver, db, paths, *, dry_run: bool = False, batch: int = writer.REL_CHUNK,
         run_root: str | None = None) -> dict:
    """Reverse the merges journaled in ``paths`` and re-create the IN_STUDY links their archives hold (the spec's
    section 5.6), under the caller's hold of the graph-write lock. Per id, in id order: while L is the only node
    carrying the id, L's journaled properties and Investigation come back and K is re-created (a rerun after that step
    finds it done and goes on); then every archived link whose sample and Study still exist is re-created, in the same
    call and before any source moves, so a link ``--studies`` removed from the merged node comes back on L and then
    moves to K with the rest (given in a later call it would stay on L); then each journaled source goes back to K
    ("on both" keeps its link to L too), and every other source on L that is not one of L's own journaled sources
    reached study X through the key after the merge (an upload, a ``--studies`` link) and moves to K too, listed in
    ``arrived_after_merge``; where the journal holds no K (a rekey in place of a node with none) they stay on L, are
    listed in ``arrived_left_on_legacy``, and the status is ``partial``. ``dry_run`` reports each id's state and
    writes nothing. With ``run_root``, a merge journal under it that names one of the ids and is not among ``paths``
    raises ValueError before anything is read from the graph.

    Neo4j hands a freed element id to a new node, and an undo may run days after its merge, so every source and
    Investigation is matched by its journaled element id AND its ``id`` (and a source's labels): a source whose element
    id now names another node is listed in ``sources_replaced`` and never linked, and an Investigation that is gone
    or replaced is named in ``investigation_not_restored`` and makes the status ``partial``."""
    journals, archives = read_journals(paths)
    if run_root is not None:
        missing = unlisted_journals(run_root, paths, journals)
        if missing:
            raise ValueError("other merge journals name the same study ids; give them too, so a crash and its rerun "
                             "(or a later approval) are undone together: " + ", ".join(missing))
    archive_rows = _read_archives(archives)
    index = read_index(driver, db)
    report = {"status": DRY_RUN if dry_run else OK, "studies": [], "refused": [], "archives": archives,
              "archive_rows": len(archive_rows), "archive_restored": 0, "investigation_not_restored": []}
    todo = []
    for x in sorted(journals):
        entry = journals[x]
        if entry["plan"] is None:
            report["refused"].append({"study_id": x, "reason": "no plan line in the journals given"})
            continue
        state_, found = _undo_state(index, x, entry["plan"])
        if state_ is None:
            report["refused"].append({"study_id": x, "reason": found})
            continue
        todo.append([x, entry, state_, found])
        report["studies"].append({"study_id": x, "state": state_, "sources": len(entry["sources"]),
                                  "moved_back": 0, "skipped": 0, "sources_replaced": [], "arrived_after_merge": [],
                                  "arrived_moved": 0, "arrived_left_on_legacy": []})
    if dry_run:
        return report
    for item in todo:
        x, entry, state_, _ = item
        if state_ != "merged":
            continue
        plan_payload = entry["plan"]
        keyed = plan_payload["seek_keyed"]
        l_inv = plan_payload["legacy"]["investigation"] or {}
        k_inv = None if keyed is None else (keyed["investigation"] or {})
        records = _records(_run(driver, db, q.UNMERGE_STUDY_NODES, {
            "l": plan_payload["legacy"]["element_id"], "study_id": x, "l_props": plan_payload["legacy"]["props"],
            "l_investigation": l_inv.get("element_id"), "l_investigation_id": l_inv.get("id"),
            "k_props": None if keyed is None else keyed["props"],
            "k_investigation": None if k_inv is None else k_inv.get("element_id"),
            "k_investigation_id": None if k_inv is None else k_inv.get("id")}))
        if not records:
            report["refused"].append({"study_id": x, "reason": "the legacy node changed between the read and the "
                                                               "write"})
            item[2] = None
            continue
        new_k = list(records[0]["new_k"] or [])
        item[3] = new_k[0] if new_k else None
        if not records[0]["l_investigations"]:
            report["investigation_not_restored"].append(_not_restored(x, "legacy", l_inv))
        if new_k and not records[0]["k_investigations"]:
            report["investigation_not_restored"].append(_not_restored(x, "seek_keyed", k_inv))
    for rows in _batches(archive_rows, batch):
        report["archive_restored"] += _one(_run(driver, db, q.RESTORE_IN_STUDY, {"rows": rows}), "restored")
    by_id = {s["study_id"]: s for s in report["studies"]}
    for x, entry, state_, keyed_eid in todo:
        if state_ is None:
            continue
        if keyed_eid is not None:
            _move_back(driver, db, entry, keyed_eid, by_id[x], batch)
        _move_arrivals(driver, db, entry, keyed_eid, by_id[x], batch)
    refused = {r["study_id"] for r in report["refused"]}
    report["studies"] = [s for s in report["studies"] if s["study_id"] not in refused]
    if (report["refused"] or report["investigation_not_restored"]
            or any(s["arrived_left_on_legacy"] for s in report["studies"])):
        report["status"] = PARTIAL
    return report


def _move_back(driver, db, entry: dict, keyed_eid: str, study: dict, batch: int) -> None:
    """Step 2: each journaled K source that is still the journaled node goes back to K."""
    journaled = entry["sources"]
    now = _nodes_now(driver, db, sorted(journaled), batch)
    same = [eid for eid in sorted(journaled) if _same_node(journaled[eid], now.get(eid))]
    kept = set(same)
    replaced = [_identity(eid, journaled[eid]) for eid in sorted(journaled)
                if eid not in kept and now.get(eid, {}).get("found")]
    rows = [dict(_identity(eid, journaled[eid]), source=eid, on_both=journaled[eid]["place"] == "on_both")
            for eid in same]
    moved = sum(_one(_run(driver, db, q.UNMERGE_MOVE_BACK, {"l": entry["plan"]["legacy"]["element_id"],
                                                            "k": keyed_eid, "rows": part}), "restored")
                for part in _batches(rows, batch))
    study.update(moved_back=moved, skipped=len(journaled) - moved, sources_replaced=replaced)


def _move_arrivals(driver, db, entry: dict, keyed_eid: str | None, study: dict, batch: int) -> None:
    """Every source on L now that is neither a journaled K source nor one of L's own journaled sources (each matched
    by element id, id and labels) reached study X through the key after the merge: it goes to K, or, with no K to
    go to, stays and is listed. A journal from before L's sources were journaled says nothing about arrivals."""
    if not entry["legacy_sources_known"]:
        study["arrived_after_merge"] = None
        return
    legacy_eid = entry["plan"]["legacy"]["element_id"]
    known = (entry["sources"], entry["legacy_sources"])
    arrived = []
    for source in _sources(driver, db, legacy_eid):
        now = {"found": True, "id": source["id"], "labels": source["labels"]}
        if not any(source["element_id"] in j and _same_node(j[source["element_id"]], now) for j in known):
            arrived.append(_identity(source["element_id"], source))
    study["arrived_after_merge"] = arrived
    if not arrived:
        return
    if keyed_eid is None:
        study["arrived_left_on_legacy"] = arrived
        return
    rows = [dict(a, source=a["element_id"], on_both=False) for a in arrived]
    study["arrived_moved"] = sum(
        _one(_run(driver, db, q.UNMERGE_MOVE_BACK, {"l": legacy_eid, "k": keyed_eid, "rows": part}), "restored")
        for part in _batches(rows, batch))
