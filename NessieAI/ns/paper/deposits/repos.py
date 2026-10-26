"""Generic repositories (spec ruling 8): Zenodo, figshare and the Stanford Digital
Repository. They have no sample structure, so each file becomes its own record; the
paper decides what it is (piece 2)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.models import DepositRecord, FileRef, Repo


def _project(repo: Repo, rid: str, title: str, fields: dict, submitted) -> DepositRecord:
    return DepositRecord(repo=repo, record_id=rid, kind="project", title=title,
                         fields={k: str(v) for k, v in fields.items() if v}, submitted=submitted)


def _file(repo: Repo, rid: str, ref: FileRef, submitted) -> DepositRecord:
    return DepositRecord(repo=repo, record_id=f"{rid}/{ref.name}", kind="file", title=ref.name,
                         files=[ref], links=[rid], submitted=submitted)


def fetch_zenodo(record_id: str, fetcher: Fetcher) -> list[DepositRecord]:
    rid = str(record_id)
    data = fetcher.get_json(f"https://zenodo.org/api/records/{rid}")
    md = data.get("metadata") or {}
    submitted = parse_date(md.get("publication_date"))
    out = [_project("zenodo", rid, md.get("title", ""),
                    {k: md.get(k) for k in ("title", "description", "publication_date", "version")}, submitted)]
    for f in data.get("files") or []:
        name = f.get("key") or f.get("filename") or ""
        algo, _, digest = (f.get("checksum") or "").partition(":")
        links = f.get("links") or {}
        out.append(_file("zenodo", rid, FileRef(
            name=name, url=links.get("self") or links.get("download") or f"https://zenodo.org/records/{rid}/files/{quote(name)}",
            size=f.get("size"), checksum=digest or None,
            checksum_type="md5" if algo == "md5" and digest else None), submitted))
    return out


def fetch_figshare(article_id: str, fetcher: Fetcher) -> list[DepositRecord]:
    rid = str(article_id)
    data = fetcher.get_json(f"https://api.figshare.com/v2/articles/{rid}")
    submitted = parse_date(data.get("published_date"))
    out = [_project("figshare", rid, data.get("title", ""),
                    {k: data.get(k) for k in ("title", "description", "published_date", "doi")}, submitted)]
    for f in data.get("files") or []:
        md5 = f.get("computed_md5") or f.get("supplied_md5") or None
        out.append(_file("figshare", rid, FileRef(
            name=f.get("name", ""), url=f.get("download_url", ""), size=f.get("size"),
            checksum=md5, checksum_type="md5" if md5 else None), submitted))
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el) -> str:
    return (el.text or "").strip()


def fetch_sdr(druid: str, fetcher: Fetcher) -> list[DepositRecord]:
    root = ET.fromstring(fetcher.get(f"https://purl.stanford.edu/{druid}.xml"))
    title = next((_text(el) for el in root.iter() if _local(el.tag) == "title" and _text(el)), "")
    out = [_project("sdr", druid, title, {"title": title}, None)]
    for el in root.iter():
        if _local(el.tag) != "file" or not el.get("id"):
            continue
        name, size = el.get("id"), el.get("size") or ""
        md5 = next((_text(c) for c in el if _local(c.tag) == "checksum" and c.get("type") == "md5"), None)
        out.append(_file("sdr", druid, FileRef(
            name=name, url=f"https://stacks.stanford.edu/file/druid:{druid}/{quote(name)}",
            size=int(size) if size.isdigit() else None, checksum=md5,
            checksum_type="md5" if md5 else None), None))
    return out
