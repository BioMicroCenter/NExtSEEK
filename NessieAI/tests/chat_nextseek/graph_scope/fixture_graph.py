"""
The synthetic graph the lane runs on: three projects, and every shape a scope must survive.

Samples of four types live in project 1 only, project 2 only, projects 1 and 2, project 3 only, projects 1 and 3,
an empty project list, and no project list at all. DERIVED_FROM chains run inside a project, from a visible child to
a foreign parent, and through a foreign sample between two visible ones; orphans (OrphanSample, no Sample label) exist
with and without project_ids. Studies hold visible, foreign and mixed samples, and one investigation holds a study of
another project beside its own. Investigations carry project_id and IN_PROJECT; people are MEMBER_OF projects.
Catalog SampleType and Attribute nodes carry cross-project statistics, and a GraphMeta node carries the catalog hash.
A visible child's parent_titles name its foreign parent.

Every value carries a marker saying who may read it: ``ZQF`` followed by the project ids whose members may see it
(``ZQF12``: a member of project 1 or of project 2), ``ZQFNONE`` for what no scoped caller may see (samples outside
every project, relationships between two projects no caller holds together) and ``ZQFCAT`` for the catalog.
``forbidden_markers`` returns the markers a caller must never read.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 11.2.
"""
from __future__ import annotations

import re
from typing import Any

PROJECTS = [
    {"id": 1, "title": "Project one ZQF1"},
    {"id": 2, "title": "Project two ZQF2"},
    {"id": 3, "title": "Project three ZQF3"},
]

# (key, type code, project_ids or None for "no property", extra attributes)
_SAMPLES: list[tuple[str, str, list[int] | None, dict[str, Any]]] = [
    ("MUS-230101AAA-1", "MUS", [1], {"Strain": "B6 ZQF1", "Scientist": "Ada Lovelace ZQF1", "Vendor": "Acme ZQF1",
                                      "Treatment1": "drug ZQF1", "Concentration": 1.0}),
    ("TIS-230102AAA-2", "TIS", [1], {"Organ": "Lung", "Analyte": "IL-1b ZQF1"}),
    ("SLD-230103AAA-3", "SLD", [1], {"Stain": "H&E", "PercentNecrosis": "45", "CollectionDate": "2021-03-01"}),
    ("MUS-230201BBB-1", "MUS", [2], {"Strain": "C57 ZQF2", "Scientist": "Grace ZQF2", "Treatment2": "drug ZQF2"}),
    ("TIS-230202BBB-2", "TIS", [2], {"Organ": "Liver ZQF2", "Concentration": 2.5}),
    ("SLD-230203BBB-3", "SLD", [2], {"Stain": "PAS ZQF2", "StorageTemperature": "-80"}),
    ("MUS-230301CCC-1", "MUS", [1, 2], {"Strain": "shared", "Vendor": "acme"}),
    ("TIS-230302CCC-2", "TIS", [1, 2], {"Organ": "Lung", "Dechlorinated": "Y"}),
    ("CHM-230401DDD-1", "CHM", [3], {"Name": "Drug ZQF3", "Concentration": 5.0}),
    ("TIS-230402DDD-2", "TIS", [3], {"Organ": "Heart ZQF3"}),
    ("MUS-230501EEE-1", "MUS", [], {"Strain": "Empty ZQFNONE"}),
    ("TIS-230502EEE-2", "TIS", None, {"Organ": "Missing ZQFNONE"}),
    ("SLD-230104AAA-4", "SLD", [1], {"Stain": "H&E", "parent_titles": ["TIS-230202BBB-2 ZQF2"],
                                      "parent_title_hashes": ["hash ZQF2"]}),
    ("TIS-230105AAA-5", "TIS", [1], {"Organ": "Kidney"}),
    ("MUS-230204BBB-4", "MUS", [2], {"Strain": "Between ZQF2"}),
    ("CHM-230106AAA-6", "CHM", [1], {"Name": "drug stock ZQF1", "Concentration": 1.5}),
    ("SLD-230403DDD-3", "SLD", [3], {"Stain": "H&E ZQF3"}),
    ("MUS-230107AAA-7", "MUS", [1, 3], {"Strain": "shared13"}),
    ("SLD-230205BBB-5", "SLD", [2], {"Stain": "Sibling ZQF2"}),
]


def _marker(project_ids: list[int] | None) -> str:
    return "ZQF" + "".join(str(p) for p in sorted(project_ids)) if project_ids else "ZQFNONE"


def samples() -> list[dict[str, Any]]:
    out = []
    for k, (uid, code, project_ids, attrs) in enumerate(_SAMPLES, start=1):
        mark = _marker(project_ids)
        props: dict[str, Any] = {"id": 1000 + k, "uuid": uid, "type": code, "title": f"{uid} {mark}",
                                 "synced_at": "2026-09-18T00:00:00Z", "source_hash": f"src {mark}"}
        if project_ids is not None:
            props["project_ids"] = project_ids
        props.update(attrs)
        props.setdefault("parent_titles", [])
        props.setdefault("parent_title_hashes", [])
        words = " ".join(str(v) for v in attrs.values() if not isinstance(v, list))
        props["search_text"] = "\n".join([uid, words, f"sample alpha mouse {mark}" if code == "MUS" else
                                          f"sample {code.lower()} {mark}"])
        out.append({"key": uid, "label": f"T_{code}", "props": props})
    return out


ORPHANS = [
    {"uuid": "ORPH-1", "title": "orphan one ZQF1", "project_ids": [1]},
    {"uuid": "ORPH-2", "title": "orphan none ZQFNONE"},
]

# (child, parent, assay, protocol): a relationship's marker is who sees both of its ends.
DERIVED_FROM = [
    ("TIS-230102AAA-2", "MUS-230101AAA-1", "Dissection", "P.AAA-230102-dissect ZQF1"),
    ("SLD-230103AAA-3", "TIS-230102AAA-2", "Staining", "P.AAA-230103-stain ZQF1"),
    ("TIS-230202BBB-2", "MUS-230201BBB-1", "Dissection", "P.BBB-230202-dissect ZQF2"),
    ("SLD-230203BBB-3", "TIS-230202BBB-2", "Staining", "P.BBB-230203-stain ZQF2"),
    ("TIS-230302CCC-2", "MUS-230301CCC-1", "Dissection", "P.CCC-230302-dissect ZQF12"),
    ("TIS-230402DDD-2", "CHM-230401DDD-1", "Treatment", "P.DDD-230402-treat ZQF3"),
    ("SLD-230403DDD-3", "TIS-230402DDD-2", "Staining", "P.DDD-230403-stain ZQF3"),
    ("SLD-230104AAA-4", "TIS-230202BBB-2", "Staining", "P.AAA-230104-cross ZQFNONE"),
    ("TIS-230105AAA-5", "MUS-230204BBB-4", "Dissection", "P.AAA-230105-cross ZQFNONE"),
    ("MUS-230204BBB-4", "CHM-230106AAA-6", "Treatment", "P.BBB-230204-cross ZQFNONE"),
    ("MUS-230107AAA-7", "CHM-230401DDD-1", "Treatment", "P.AAA-230107-treat ZQF3"),
    ("TIS-230502EEE-2", "MUS-230501EEE-1", "Dissection", "P.EEE-230502-dissect ZQFNONE"),
]
ORPHAN_LINKS = [
    ("MUS-230101AAA-1", "ORPH-1", "Receipt", "P.AAA-230101-receipt ZQF1"),
    ("MUS-230201BBB-1", "ORPH-2", "Receipt", "P.BBB-230201-receipt ZQFNONE"),
]

# A study's marker: who sees one of its samples. An investigation's: who sees one of its studies, or its project.
STUDIES = [
    {"id": 1, "title": "Study one ZQF1", "DOI": "10.1000/one", "PMID": "1001", "investigation": 1,
     "samples": ["MUS-230101AAA-1", "TIS-230102AAA-2", "SLD-230103AAA-3", "SLD-230104AAA-4", "TIS-230105AAA-5",
                 "CHM-230106AAA-6"]},
    {"id": 2, "title": "Study two ZQF2", "DOI": "", "PMID": "", "investigation": 2,
     "samples": ["MUS-230201BBB-1", "TIS-230202BBB-2", "SLD-230203BBB-3", "MUS-230204BBB-4"]},
    {"id": 3, "title": "Mixed study ZQF12", "DOI": "", "PMID": "", "investigation": 3,
     "samples": ["MUS-230301CCC-1", "TIS-230302CCC-2"]},
    {"id": 4, "title": "Study four ZQF3", "DOI": "10.1000/four", "PMID": "", "investigation": 4,
     "samples": ["CHM-230401DDD-1", "TIS-230402DDD-2", "SLD-230403DDD-3"]},
    {"id": 5, "title": "Empty study ZQFNONE", "DOI": "", "PMID": "", "investigation": 5, "samples": []},
    {"id": 6, "title": "Outside study ZQFNONE", "DOI": "", "PMID": "", "investigation": 5,
     "samples": ["MUS-230501EEE-1", "TIS-230502EEE-2"]},
    {"id": 7, "title": "Shared study ZQF13", "DOI": "", "PMID": "", "investigation": 4,
     "samples": ["MUS-230107AAA-7"]},
    {"id": 8, "title": "Sibling study ZQF2", "DOI": "", "PMID": "", "investigation": 1,
     "samples": ["SLD-230205BBB-5"]},
]
INVESTIGATIONS = [
    {"id": 1, "title": "Investigation one ZQF12", "project_id": 1},
    {"id": 2, "title": "Investigation two ZQF2", "project_id": 2},
    {"id": 3, "title": "Mixed investigation ZQF12", "project_id": 1},
    {"id": 4, "title": "Investigation four ZQF13", "project_id": 3},
    {"id": 5, "title": "Investigation five ZQF2", "project_id": 2},
]
PEOPLE = [
    {"id": 101, "projects": [1]},
    {"id": 102, "projects": [2]},
    {"id": 103, "projects": [1, 2]},
    {"id": 104, "projects": [3]},
]
CATALOG = [
    {"title": "MUS", "sample_count": 6, "description": "mouse ZQFCAT",
     "attributes": [{"title": "Strain", "sample_count": 6, "top_values": ["B6 ZQFCAT", "C57 ZQFCAT"],
                     "top_counts": [1, 1], "declared": True}]},
    {"title": "TIS", "sample_count": 7, "description": "tissue ZQFCAT",
     "attributes": [{"title": "Organ", "sample_count": 7, "top_values": ["Lung ZQFCAT"], "top_counts": [2],
                     "declared": True}]},
    {"title": "SLD", "sample_count": 5, "description": "slide ZQFCAT", "attributes": []},
    {"title": "CHM", "sample_count": 2, "description": "chemical ZQFCAT", "attributes": []},
]
GRAPH_META = {"schema_version": "1.2", "catalog_hash": "hash ZQFCAT", "synced_at": "2026-09-18T00:00:00Z"}

CALLERS: dict[str, tuple[int, ...] | None] = {"projects_1_3": (1, 3), "project_2": (2,), "no_projects": (),
                                              "admin": None}

_MARKER_RE = re.compile(r"ZQF(NONE|CAT|\d+)")


def forbidden_markers(text: str, caller: tuple[int, ...]) -> list[str]:
    """Every marker in `text` a caller limited to `caller` may not read."""
    bad = []
    for m in _MARKER_RE.finditer(text):
        tag = m.group(1)
        if tag in ("NONE", "CAT") or not (set(int(d) for d in tag) & set(caller)):
            bad.append(m.group(0))
    return bad


def load(tx) -> None:
    """Create the whole fixture in the caller's write transaction."""
    tx.run("UNWIND $rows AS row CREATE (p:Project) SET p = row", rows=PROJECTS).consume()
    for sample in samples():
        tx.run(f"CREATE (s:Sample:{sample['label']}) SET s = $props", props=sample["props"]).consume()
    tx.run("UNWIND $rows AS row CREATE (o:OrphanSample) SET o = row", rows=ORPHANS).consume()
    tx.run("UNWIND $rows AS row MATCH (c:Sample {uuid: row[0]}), (p:Sample {uuid: row[1]}) "
           "CREATE (c)-[:DERIVED_FROM {internal_assay_title: row[2], protocol_title: row[3]}]->(p)",
           rows=[list(r) for r in DERIVED_FROM]).consume()
    tx.run("UNWIND $rows AS row MATCH (c:Sample {uuid: row[0]}), (p:OrphanSample {uuid: row[1]}) "
           "CREATE (c)-[:DERIVED_FROM {internal_assay_title: row[2], protocol_title: row[3]}]->(p)",
           rows=[list(r) for r in ORPHAN_LINKS]).consume()
    tx.run("UNWIND $rows AS row MATCH (s:Sample {uuid: row.uuid}) UNWIND row.projects AS pid "
           "MATCH (p:Project {id: pid}) CREATE (s)-[:IN_PROJECT]->(p)",
           rows=[{"uuid": s["key"], "projects": s["props"].get("project_ids") or []} for s in samples()]).consume()
    tx.run("UNWIND $rows AS row CREATE (i:Investigation {id: row.id, title: row.title, project_id: row.project_id, "
           "description: row.title}) WITH i, row MATCH (p:Project {id: row.project_id}) CREATE (i)-[:IN_PROJECT]->(p)",
           rows=INVESTIGATIONS).consume()
    tx.run("UNWIND $rows AS row CREATE (st:Study {id: row.id, title: row.title, description: row.title, "
           "DOI: row.DOI, PMID: row.PMID, seek_study_id: row.id}) WITH st, row "
           "MATCH (i:Investigation {id: row.investigation}) CREATE (st)-[:IN_INVESTIGATION]->(i) WITH st, row "
           "UNWIND row.samples AS uid MATCH (s:Sample {uuid: uid}) CREATE (s)-[:IN_STUDY]->(st)",
           rows=STUDIES).consume()
    tx.run("UNWIND $rows AS row CREATE (per:Person {id: row.id}) WITH per, row UNWIND row.projects AS pid "
           "MATCH (p:Project {id: pid}) CREATE (per)-[:MEMBER_OF]->(p)", rows=PEOPLE).consume()
    tx.run("UNWIND $rows AS row CREATE (t:SampleType {title: row.title, sample_count: row.sample_count, "
           "description: row.description}) WITH t, row UNWIND row.attributes AS a "
           "CREATE (t)-[:HAS_ATTRIBUTE]->(:Attribute {title: a.title, sample_type: row.title, "
           "sample_count: a.sample_count, top_values: a.top_values, top_counts: a.top_counts, declared: a.declared})",
           rows=CATALOG).consume()
    tx.run("MATCH (s:Sample), (t:SampleType) WHERE s.type = t.title CREATE (s)-[:OF_TYPE]->(t)").consume()
    tx.run("CREATE (g:GraphMeta) SET g = $props", props=GRAPH_META).consume()


FULLTEXT_INDEX = ("CREATE FULLTEXT INDEX sample_search_text IF NOT EXISTS FOR (n:Sample) ON EACH [n.search_text]")


def prune_statements(caller: tuple[int, ...]) -> list[tuple[str, dict]]:
    """What the differential oracle deletes for a caller: every sample and orphan it cannot see, every project
    outside its scope."""
    ids = list(caller)
    return [
        ("MATCH (n) WHERE (n:Sample OR n:OrphanSample) AND NOT any(p IN coalesce(n.project_ids, []) WHERE p IN $ids) "
         "DETACH DELETE n", {"ids": ids}),
        ("MATCH (p:Project) WHERE NOT p.id IN $ids DETACH DELETE p", {"ids": ids}),
    ]
