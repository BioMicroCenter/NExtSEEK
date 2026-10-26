"""PRIDE archive (ProteomeXchange PXD): the project and every file, paginated.

The files endpoint answers either a plain list or a HAL page (`_embedded.files`); both
are read. Paging ends only on an empty page, never on a short one, so a server that
clamps pageSize cannot truncate the list. More than MAX_PAGES pages raises. PRIDE
checksums are SHA-1.
"""
from __future__ import annotations

from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.models import DepositRecord, FileRef

BASE = "https://www.ebi.ac.uk/pride/ws/archive/v2"
PAGE = 100
MAX_PAGES = 1000
_TEXT_FIELDS = ("title", "projectDescription", "sampleProcessingProtocol", "dataProcessingProtocol",
                "submissionDate", "publicationDate", "submissionType")
_LIST_FIELDS = ("organisms", "organismParts", "diseases", "instruments", "keywords", "quantificationMethods")


def fetch(pxd: str, fetcher: Fetcher) -> list[DepositRecord]:
    proj = fetcher.get_json(f"{BASE}/projects/{pxd}")
    submitted = parse_date(proj.get("submissionDate"))
    fields = {k: str(proj[k]) for k in _TEXT_FIELDS if proj.get(k)}
    for k in _LIST_FIELDS:
        names = [n for n in (_name(v) for v in proj.get(k) or []) if n]
        if names:
            fields[k] = "; ".join(names)
    records = [DepositRecord(repo="pride", record_id=pxd, kind="project", title=proj.get("title", ""),
                             fields=fields, submitted=submitted)]
    for page in range(MAX_PAGES):
        data = fetcher.get_json(f"{BASE}/projects/{pxd}/files", params={"pageSize": PAGE, "page": page})
        batch = data if isinstance(data, list) else ((data or {}).get("_embedded") or {}).get("files") or []
        if not batch:
            return records
        records += [_file(pxd, f, submitted) for f in batch if f.get("fileName")]
    raise RuntimeError(f"PRIDE {pxd}: more than {MAX_PAGES} pages of files")


def _name(value) -> str:
    if isinstance(value, dict):
        return value.get("name") or value.get("value") or ""
    return str(value)


def _file(pxd: str, f: dict, submitted) -> DepositRecord:
    name = f["fileName"]
    category = ((f.get("fileCategory") or {}).get("value") or "").upper()
    locations = f.get("publicFileLocations") or []
    url = next((l.get("value") for l in locations if "FTP" in (l.get("name") or "").upper()), None) \
        or next((l.get("value") for l in locations), "")
    checksum = f.get("checksum") or None
    return DepositRecord(
        repo="pride", record_id=f"{pxd}/{name}", kind="file", title=name,
        fields={"fileCategory": category} if category else {},
        files=[FileRef(name=name, url=url or "", size=f.get("fileSizeBytes"),
                       checksum=checksum, checksum_type="sha1" if checksum else None)],
        links=[pxd], submitted=submitted)
