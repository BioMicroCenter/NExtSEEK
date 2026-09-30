"""An in-memory Study layer for the studies release's unit tests.

``StudyGraph`` answers the statements of ``nextseek_api/graph_sync/cypher.py`` that read or write Study, Investigation
and Project nodes and the IN_STUDY, IN_INVESTIGATION and Investigation IN_PROJECT edges, the way the Cypher does, from a
few dicts, and fails on any statement it does not know. It is a driver on its own (``execute_query``); another fake
can merge ``handlers()`` into its own table and pass ``is_sample`` so both agree on which sample ids are Sample nodes.
The real Cypher runs only in the graph scope lane (NessieAI/tests/chat_nextseek/graph_scope/test_study_links_lane.py).
"""
from __future__ import annotations

import itertools
from collections import Counter
from types import SimpleNamespace

from neo4j import RoutingControl

from nextseek_api.graph_sync import cypher as q


class StudyGraph:
    def __init__(self, is_sample=None):
        self.studies: dict[str, dict] = {}                # study element id -> its properties
        self.investigations: dict[str, dict] = {}         # investigation element id -> {"id", "title", ...}
        self.in_investigation: dict[str, list[str]] = {}  # study element id -> investigation element ids
        self.inv_projects: dict[str, set] = {}            # investigation element id -> project ids it is IN_PROJECT to
        self.projects: dict[int, dict] = {}               # project id -> its properties
        self.other_rels: dict[str, list[str]] = {}        # study element id -> any other relationship types it holds
        self.sources: dict[str, dict] = {}                # source element id -> {"labels": set, "id": ...}
        self.in_study: dict[str, tuple[str, str]] = {}    # edge element id -> (source element id, study element id)
        self.calls: list = []
        self.before_write = None                          # called with (query, params) before each write
        self._seq = itertools.count(1)
        self._is_sample = is_sample

    # --- building ----------------------------------------------------------------------------------------

    def _eid(self, prefix: str) -> str:
        return f"{prefix}:{next(self._seq)}"

    def add_project(self, project_id, title=None) -> int:
        self.projects[project_id] = {"id": project_id, "title": title}
        return project_id

    def add_investigation(self, inv_id, title, projects=()) -> str:
        eid = self._eid("i")
        self.investigations[eid] = {"id": inv_id, "title": title}
        self.inv_projects[eid] = set(projects)
        return eid

    def add_study(self, investigation=None, **props) -> str:
        eid = self._eid("st")
        self.studies[eid] = {k: v for k, v in props.items() if v is not None}
        if investigation is None:
            self.in_investigation[eid] = []
        elif isinstance(investigation, str):
            self.in_investigation[eid] = [investigation]
        else:
            self.in_investigation[eid] = list(investigation)
        self.other_rels[eid] = []
        return eid

    def add_sample(self, sample_id, label="Sample") -> str:
        eid = ("s:" if label == "Sample" else "o:") + str(sample_id)
        self.sources[eid] = {"labels": {label}, "id": sample_id}
        return eid

    def is_sample_id(self, sample_id) -> bool:
        if self._is_sample is not None:
            return bool(self._is_sample(sample_id))
        return f"s:{sample_id}" in self.sources

    def _sample_source(self, sample_id):
        eid = f"s:{sample_id}"
        if eid not in self.sources and self.is_sample_id(sample_id):
            self.add_sample(sample_id)
        return eid if eid in self.sources else None

    def link(self, source, study_eid) -> str:
        src = source if isinstance(source, str) else self._sample_source(source)
        if src is None:
            raise ValueError(f"no source {source!r}")
        eid = self._eid("e")
        self.in_study[eid] = (src, study_eid)
        return eid

    # --- reading back ------------------------------------------------------------------------------------

    def key_of(self, study_eid) -> tuple:
        props = self.studies[study_eid]
        return ("seek", props["seek_study_id"]) if props.get("seek_study_id") is not None else ("id", props.get("id"))

    def keys_of(self, sample_id, label="Sample") -> set:
        src = ("s:" if label == "Sample" else "o:") + str(sample_id)
        return {self.key_of(st) for s, st in self.in_study.values() if s == src}

    def seek_links(self) -> set:
        return {(self.sources[s]["id"], self.studies[st]["seek_study_id"]) for s, st in self.in_study.values()
                if "Sample" in self.sources[s]["labels"] and self.studies[st].get("seek_study_id") is not None}

    def studies_by_seek(self, seek_study_id) -> list[str]:
        return [e for e, p in self.studies.items() if p.get("seek_study_id") == seek_study_id]

    def investigation_ids_of(self, study_eid) -> list:
        return [self.investigations[i]["id"] for i in self.in_investigation[study_eid]]

    def investigation_by_id(self, inv_id):
        return next((e for e, i in self.investigations.items() if i["id"] == inv_id), None)

    def writes(self) -> list:
        return [c for c in self.calls if not c.read]

    def of(self, query) -> list:
        return [c for c in self.calls if c.query == query]

    # --- the driver surface ------------------------------------------------------------------------------

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        read = kwargs.get("routing_") == RoutingControl.READ
        self.calls.append(SimpleNamespace(query=query, params=params, read=read))
        handler = self.handlers().get(query)
        if handler is None:
            raise AssertionError(f"StudyGraph does not know this statement: {query.strip()[:120]}")
        if not read and self.before_write is not None:
            self.before_write(query, params)
        records = list(handler(params))
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        return SimpleNamespace(records=records, summary=SimpleNamespace(counters=SimpleNamespace()))

    def handlers(self) -> dict:
        return {
            q.MERGE_SEEK_STUDIES: self._follow,
            q.MERGE_INVESTIGATIONS: self._merge_investigations,
            q.MERGE_INVESTIGATION_IN_PROJECT: self._merge_investigation_in_project,
            q.SAMPLE_STUDIES_OF: self._studies_of,
            q.SAMPLE_STUDIES_PAGE: self._studies_page,
            q.REPLACE_SEEK_IN_STUDY: self._replace,
            q.STUDY_SEEK_ID_DUPLICATES: self._duplicates,
            q.ORPHAN_IN_STUDY: self._orphan_count,
        }

    # --- Investigation nodes ------------------------------------------------------------------------------

    def _merge_investigations(self, p):
        for r in p["rows"]:
            eid = self.investigation_by_id(r["id"])
            if eid is None:
                eid = self.add_investigation(r["id"], r.get("title"))
            props = self.investigations[eid]
            for key in ("title", "description", "project_id"):
                if r.get(key) is None:
                    props.pop(key, None)
                else:
                    props[key] = r[key]
        return []

    def _merge_investigation_in_project(self, p):
        linked = 0
        for r in p["rows"]:
            eid = self.investigation_by_id(r["investigation_id"])
            if eid is None or r["project_id"] not in self.projects:
                continue
            self.inv_projects[eid].add(r["project_id"])
            linked += 1
        return [{"linked": linked}]

    # --- IN_STUDY statements -----------------------------------------------------------------------------

    def _links_record(self, src) -> list:
        out = []
        for eid, (s, st) in sorted(self.in_study.items()):
            if s == src:
                props = self.studies[st]
                out.append({"element_id": eid, "seek_study_id": props.get("seek_study_id"), "id": props.get("id"),
                            "investigations": [{"id": self.investigations[i]["id"],
                                                "title": self.investigations[i].get("title")}
                                               for i in self.in_investigation[st]]})
        return out

    def _follow(self, p):
        n = missing = 0
        for r in p["rows"]:
            targets = self.studies_by_seek(r["study_id"]) or [self.add_study(seek_study_id=r["study_id"])]
            for eid in targets:
                props = self.studies[eid]
                for key in ("title", "description"):
                    if r.get(key) is None:
                        props.pop(key, None)
                    else:
                        props[key] = r[key]
                inv_id = r.get("investigation_id")
                keep = [i for i in self.in_investigation[eid]
                        if inv_id is not None and self.investigations[i]["id"] == inv_id]
                target = None if inv_id is None else self.investigation_by_id(inv_id)
                if target is not None and target not in keep:
                    keep.append(target)
                missing += inv_id is not None and target is None
                self.in_investigation[eid] = keep
                n += 1
        return [{"n": n, "investigation_missing": missing}]

    def _studies_of(self, p):
        return [{"id": sid, "studies": self._links_record(f"s:{sid}")} for sid in p["ids"] if self.is_sample_id(sid)]

    def _sample_ids(self) -> list:
        ids = {s["id"] for s in self.sources.values() if "Sample" in s["labels"]}
        return sorted(i for i in ids if isinstance(i, int) and not isinstance(i, bool) and self.is_sample_id(i))

    def _studies_page(self, p):
        ids = [i for i in self._sample_ids() if i > p["after"]][: p["limit"]]
        return [{"id": i, "studies": self._links_record(f"s:{i}")} for i in ids]

    def _is_paper(self, src) -> bool:
        return any(s == src and self.studies[st].get("seek_study_id") is None for s, st in self.in_study.values())

    def _replace(self, p):
        out = {"samples": 0, "removed": 0, "added": 0, "paper_samples": 0, "paper_added": 0, "withheld": 0,
               "studies_missing": 0}
        for r in p["rows"]:
            if not self.is_sample_id(r["sample_id"]):
                continue
            src = self._sample_source(r["sample_id"])
            out["samples"] += 1
            for eid in r["remove"]:
                edge = self.in_study.get(eid)
                if edge is None or edge[0] != src:
                    continue
                target = self.studies[edge[1]].get("seek_study_id")
                if target is not None and target not in r["study_ids"]:
                    del self.in_study[eid]
                    out["removed"] += 1
            paper = self._is_paper(src)
            withhold = list(r["study_ids"]) if paper and not r["paper"] else list(r["withhold"])
            out["paper_samples"] += paper
            out["withheld"] += len(withhold)
            for k in r["study_ids"]:
                if k in withhold:
                    continue
                targets = self.studies_by_seek(k)
                if not targets:
                    out["studies_missing"] += 1
                for st in targets:
                    if not any(s == src and t == st for s, t in self.in_study.values()):
                        self.link(src, st)
                        out["added"] += 1
                        out["paper_added"] += paper
        return [out]

    def _duplicates(self, p):
        counts = Counter(props["seek_study_id"] for props in self.studies.values()
                         if props.get("seek_study_id") is not None)
        return [{"seek_study_id": k, "nodes": n} for k, n in sorted(counts.items()) if n > 1]

    def _orphan_count(self, p):
        return [{"n": sum(1 for s, _ in self.in_study.values() if "Sample" not in self.sources[s]["labels"])}]
