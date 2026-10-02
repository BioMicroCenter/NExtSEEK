"""The graph-only paper studies as a source (tool spec 4.3, T27): the migration's input.

Read only, from the live graph: every Study with no ``seek_study_id``, an ``id``, and a DOI or PMID other than blank;
its Investigation; its IN_STUDY samples, whose ``id`` is their SEEK id (checked against SEEK's ``samples``). The
paper's Investigation node is accepted only when it is the one node the paper points at and its ``id`` is a SEEK
investigation whose title equals the node's (case and surrounding whitespace aside); otherwise the whole paper is
``investigation_unknown``. A rerun after the paper moved finds the paper's SEEK study by title (tool spec 4.4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from nextseek_api.graph_sync.writer import _records, _run
from nextseek_api.studies.buckets import title_key
from nextseek_api.studies.models import AssociationSet
from nextseek_api.studies.sources.matching import RawTarget, match_targets

GRAPH_ONLY_PAPERS = """
MATCH (st:Study)
WHERE st.seek_study_id IS NULL AND st.id IS NOT NULL
  AND (trim(coalesce(toString(st.DOI), '')) <> '' OR trim(coalesce(toString(st.PMID), '')) <> '')
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(i:Investigation)
RETURN st.id AS id, st.title AS title, st.description AS description, st.DOI AS doi, st.PMID AS pmid,
       collect(DISTINCT i.id) AS investigation_ids, collect(DISTINCT i.title) AS investigation_titles
ORDER BY id
"""
PAPER_SAMPLES = """
MATCH (s:Sample)-[:IN_STUDY]->(st:Study {id: $paper_id})
WHERE st.seek_study_id IS NULL
RETURN s.id AS id
ORDER BY id
"""


def _seek_investigation(paper: dict, investigations: dict) -> Optional[int]:
    ids, titles = list(paper["investigation_ids"] or []), list(paper["investigation_titles"] or [])
    if len(ids) != 1 or not isinstance(ids[0], int) or isinstance(ids[0], bool):
        return None
    seek_title = investigations.get(ids[0])
    if seek_title is None or title_key(seek_title) != title_key(titles[0] if titles else None):
        return None
    return ids[0]


def graph_only_associations(driver, db, paper_ids, reader, investigation: Optional[int] = None, *,
                            now: Optional[str] = None) -> AssociationSet:
    papers = [p for p in _records(_run(driver, db, GRAPH_ONLY_PAPERS, read=True))
              if isinstance(p["id"], int) and not isinstance(p["id"], bool)]
    if paper_ids != "all":
        wanted = {int(i) for i in paper_ids}
        papers = [p for p in papers if p["id"] in wanted]
    investigations = reader.investigations()
    raws = []
    for paper in papers:
        inv_id = _seek_investigation(paper, investigations)
        if investigation is not None and inv_id != investigation:
            continue
        members = _records(_run(driver, db, PAPER_SAMPLES, {"paper_id": paper["id"]}, read=True))
        raws.append(RawTarget(title=paper["title"] or "", investigation_id=inv_id, key=f"graph_only:{paper['id']}",
                              description=paper["description"], doi=paper["doi"], pmid=paper["pmid"],
                              sample_ids=[(m["id"], f"paper {paper['id']}") for m in members
                                          if isinstance(m["id"], int) and not isinstance(m["id"], bool)]))
    targets, unmatched = match_targets(raws, reader)
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return AssociationSet(source="graph_only", source_ref=f"graph read {stamp}; papers {[p['id'] for p in papers]}",
                          created_at=stamp, targets=targets, unmatched=unmatched)
