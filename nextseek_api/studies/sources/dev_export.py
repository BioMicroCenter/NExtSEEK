"""The dev-backed source (tool spec 4.3, T26): ``--mode export`` on the dev box, then a plan anywhere from its file.

The export reads the dev graph only, and only paper studies: a Study node carrying a non-empty DOI or PMID whose title
is not a bucket's. Once dev's IN_STUDY follows SEEK its graph holds every SEEK study, so an unfiltered export would add
samples to any study of a matching title and pull them out of their bucket. ``--study-ids`` exports named studies
instead (a bucket never); a named study whose node carries no DOI or PMID takes the value more than half of its
samples carry.

The adapter maps dev investigation titles to SEEK's through ``dev_investigations.json`` (title pairs only), strips a
trailing ``-PUB`` or ``-PUB<n>`` from each UID (a published variant of the same sample on dev), and matches through
the shared matcher; an unmapped investigation makes the whole study ``investigation_unknown``.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from nextseek_api.graph_sync.writer import _records, _run
from nextseek_api.studies.buckets import is_bucket_title, title_key
from nextseek_api.studies.models import AssociationSet, canonical_json
from nextseek_api.studies.sources.matching import RawTarget, clean, match_targets

EXPORT_VERSION = 1
DEV_INVESTIGATIONS = Path(__file__).with_name("dev_investigations.json")
_PUB = re.compile(r"-PUB\d*$")

EXPORT_STUDIES = """
MATCH (st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(i:Investigation)
RETURN elementId(st) AS element_id, st.id AS id, st.seek_study_id AS seek_study_id, st.title AS title,
       st.description AS description, st.DOI AS doi, st.PMID AS pmid,
       collect(DISTINCT i.title) AS investigation_titles
ORDER BY coalesce(st.id, st.seek_study_id)
"""
EXPORT_STUDY_SAMPLES = """
MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
WHERE elementId(st) = $element_id
RETURN s.uuid AS uid, s.DOI AS doi, s.PMID AS pmid
ORDER BY uid
"""


def canonical_uid(uid: str) -> str:
    return _PUB.sub("", uid)


def load_investigation_map(path=DEV_INVESTIGATIONS) -> dict[str, str]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {title_key(dev): prod for dev, prod in doc["pairs"]}


def _majority(values) -> Optional[str]:
    cleaned = [clean(v) for v in values]
    present = [v for v in cleaned if v]
    if not cleaned or not present:
        return None
    value, count = Counter(present).most_common(1)[0]
    return value if count * 2 > len(cleaned) else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export_dev_graph(driver, db, out, study_ids=None, *, now: Optional[str] = None) -> dict:
    wanted = {int(i) for i in (study_ids or ())}
    studies, skipped = [], []
    for row in _records(_run(driver, db, EXPORT_STUDIES, read=True)):
        dev_id = row["id"] if row["id"] is not None else row["seek_study_id"]
        named = bool(wanted) and (row["id"] in wanted or row["seek_study_id"] in wanted)
        if wanted and not named:
            continue
        doi, pmid = clean(row["doi"]), clean(row["pmid"])
        if is_bucket_title(row["title"]):
            skipped.append({"dev_study_id": dev_id, "reason": "bucket"})
            continue
        if not named and not (doi or pmid):
            continue
        invs = list(row["investigation_titles"] or [])
        if len(invs) != 1:
            skipped.append({"dev_study_id": dev_id, "reason": "investigation_count"})
            continue
        samples = _records(_run(driver, db, EXPORT_STUDY_SAMPLES, {"element_id": row["element_id"]}, read=True))
        if named and not doi and not pmid:
            doi = _majority(s["doi"] for s in samples)
            pmid = _majority(s["pmid"] for s in samples)
        studies.append({"dev_study_id": dev_id, "title": row["title"], "description": row["description"],
                        "doi": doi, "pmid": pmid, "investigation_title": invs[0],
                        "sample_uids": sorted({s["uid"] for s in samples if s["uid"]})})
    doc = {"export_version": EXPORT_VERSION, "exported_at": now or _now(), "studies": studies, "skipped": skipped}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(doc), encoding="utf-8")
    return {"studies": len(studies), "skipped": len(skipped), "samples": sum(len(s["sample_uids"]) for s in studies),
            "out": str(out)}


def dev_associations(path, reader, *, investigation_map=None, now: Optional[str] = None) -> AssociationSet:
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("export_version") != EXPORT_VERSION:
        raise ValueError(f"{path.name}: export_version {doc.get('export_version')!r}, expected {EXPORT_VERSION}")
    mapping = load_investigation_map() if investigation_map is None else {
        title_key(k): v for k, v in investigation_map.items()}
    raws = []
    for study in doc["studies"]:
        uids, submitted = [], {}
        for original in study["sample_uids"]:
            canonical = canonical_uid(original.strip())     # a trailing space would hide the -PUB
            uids.append((canonical, f"dev uid {original}"))
            submitted.setdefault(canonical, original)
        investigation_title = mapping.get(title_key(study["investigation_title"]))
        raws.append(RawTarget(title=study["title"], investigation_title=investigation_title,
                              key=f"dev:{study['dev_study_id']}", description=study.get("description"),
                              doi=study.get("doi"), pmid=study.get("pmid"), uids=uids, submitted=submitted))
    targets, unmatched = match_targets(raws, reader)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return AssociationSet(source="dev_export", source_ref=f"{path.name} sha256:{digest}", created_at=now or _now(),
                          targets=targets, unmatched=unmatched)
