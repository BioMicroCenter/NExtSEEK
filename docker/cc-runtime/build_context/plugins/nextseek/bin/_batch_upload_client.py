"""Read-only NExtSEEK batch-upload payload-builder client."""
from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Iterable
from typing import Any

import httpx
import orjson

_API = "/nextseek_api"
_UID_CHUNK_SIZE = 1000
_PAGE_SIZE = 1000


class BatchUploadClient:
    def __init__(
        self,
        base_url: str,
        auth: tuple[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(
        cls, *, transport: httpx.BaseTransport | None = None
    ) -> "BatchUploadClient":
        base = os.environ.get("NEXTSEEK_URL") or os.environ.get("NEXTSEEK_BASE_URL") or ""
        ns_user = os.environ.get("NEXTSEEK_USERNAME") or ""
        ns_password = os.environ.get("NEXTSEEK_PASSWORD") or ""
        legacy_user = os.environ.get("API_USER") or ""
        legacy_password = os.environ.get("API_PASS") or ""
        if ns_user or ns_password:
            user = ns_user
            password = ns_password
        else:
            user = legacy_user
            password = legacy_password
        if not base or not user or not password:
            sys.stderr.write(
                "nextseek-error: CONFIG_MISSING — NEXTSEEK_URL/NEXTSEEK_BASE_URL "
                "and NEXTSEEK_USERNAME/NEXTSEEK_PASSWORD or API_USER/API_PASS not set\n"
            )
            raise SystemExit(2)
        return cls(base_url=base, auth=(user, password), transport=transport)

    def list_sample_types(self) -> list[dict[str, Any]]:
        response = self._client.get(f"{_API}/sample_types/")
        response.raise_for_status()
        body = _json(response)
        return body.get("data", body) if isinstance(body, dict) else body

    def sample_type_attributes(self, type_ref: str) -> dict[str, Any]:
        response = self._client.get(f"{_API}/sample_types/{type_ref}/")
        response.raise_for_status()
        body = _json(response)
        data = body.get("data", {})
        attrs = data.get("attributes", {})
        sample_attrs = attrs.get("sample_attributes", []) if isinstance(attrs, dict) else []
        return {"sample_type": type_ref, "attributes": list(sample_attrs)}

    def current_person(self) -> dict[str, Any]:
        response = self._client.get(f"{_API}/people/current/")
        response.raise_for_status()
        return _json(response)

    def list_projects(self) -> list[dict[str, Any]]:
        return self._paged_get(f"{_API}/projects/")

    def list_assays(self) -> dict[str, list[int]]:
        title_map: dict[str, list[int]] = {}
        for item in self._paged_get(f"{_API}/assays/"):
            title = item.get("attributes", {}).get("title")
            if isinstance(title, str) and title:
                title_map.setdefault(title, []).append(int(item["id"]))
        for ids in title_map.values():
            ids.sort()
        return title_map

    def project_assays(self, project_id: int | str) -> set[int]:
        response = self._client.get(f"{_API}/projects/{project_id}/")
        response.raise_for_status()
        body = _json(response)
        rel = body["data"]["relationships"]["assays"]["data"]
        return {int(item["id"]) for item in rel}

    def resolve_assay_title(
        self,
        title: str,
        title_map: dict[str, list[int]],
        project_assay_ids: set[int],
    ) -> int:
        candidates = [int(item) for item in title_map.get(title, [])]
        if not candidates:
            raise ValueError(f"assay title not accessible: {title}")
        in_project = [item for item in candidates if item in project_assay_ids]
        if len(in_project) != 1:
            raise ValueError(f"ambiguous assay title: {title}")
        return in_project[0]

    def assay_samples(self, assay_ids: Iterable[int]) -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        for assay_id in sorted({int(item) for item in assay_ids}):
            samples: set[str] = set()
            for item in self._paged_relationship_get(f"{_API}/assays/{assay_id}/", "samples"):
                samples.add(str(item["id"]))
            out[assay_id] = samples
        return out

    def resolve_current_assay_titles(
        self,
        titles: list[str],
        *,
        sample_numeric_id: int | str,
        title_map: dict[str, list[int]],
        project_assay_ids: set[int],
    ) -> set[int]:
        resolved: set[int] = set()
        ambiguous: dict[str, list[int]] = {}
        for title in titles:
            candidates = [int(item) for item in title_map.get(title, [])]
            if not candidates:
                raise ValueError(f"current assay title not accessible: {title}")
            narrowed = [item for item in candidates if item in project_assay_ids]
            if not narrowed:
                raise ValueError(f"current assay title not in project: {title}")
            if len(narrowed) == 1:
                resolved.add(narrowed[0])
            else:
                ambiguous[title] = narrowed
        if not ambiguous:
            return resolved

        candidate_ids = {item for ids in ambiguous.values() for item in ids}
        samples_by_assay = self.assay_samples(candidate_ids)
        sample_key = str(sample_numeric_id)
        for title, ids in ambiguous.items():
            matched = {item for item in ids if sample_key in samples_by_assay.get(item, set())}
            if not matched:
                raise ValueError(f"could not resolve current assay title: {title}")
            resolved.update(matched)
        return resolved

    def search_samples_by_uid(
        self,
        uids: list[str],
        *,
        known_assay_titles: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        wanted = {uid for uid in uids if uid}
        known_titles = list(known_assay_titles or [])
        by_uid: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(sorted(wanted), _UID_CHUNK_SIZE):
            page = 1
            while True:
                response = self._client.post(
                    f"{_API}/samples/advanced_search/",
                    params={"page": page, "page_size": _PAGE_SIZE},
                    content=orjson.dumps(
                        {
                            "filter_searchText": chunk,
                            "searchText_logic": "OR",
                            "filter_matchType": "EXACT",
                        }
                    ),
                    headers={"content-type": "application/json"},
                )
                response.raise_for_status()
                body = _json(response)
                rows = body.get("rows", [])
                if not isinstance(rows, list):
                    rows = []
                for row in rows:
                    uid = _metadata_uid(row)
                    if uid in wanted:
                        by_uid[uid] = _normalize_sample_row(
                            row,
                            known_assay_titles=known_titles,
                        )
                total = int(body.get("total") or len(rows))
                if page * _PAGE_SIZE >= total or not rows:
                    break
                page += 1
        return [by_uid[uid] for uid in uids if uid in by_uid]

    def validate_file(
        self,
        path: str | pathlib.Path,
        *,
        project_id: int,
        checks: str,
    ) -> dict[str, Any]:
        with pathlib.Path(path).open("rb") as handle:
            response = self._client.post(
                f"{_API}/batch-upload/validate/",
                data={"project_id": str(project_id), "checks": checks},
                files={
                    "file": (
                        pathlib.Path(path).name,
                        handle,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        response.raise_for_status()
        return _json(response)

    def _paged_get(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._client.get(path, params={"page": page, "page_size": _PAGE_SIZE})
            response.raise_for_status()
            body = _json(response)
            data = body.get("data", [])
            if not isinstance(data, list):
                raise ValueError(f"expected list data from {path}")
            items.extend(data)
            next_link = body.get("links", {}).get("next") if isinstance(body.get("links"), dict) else None
            total = body.get("meta", {}).get("count") if isinstance(body.get("meta"), dict) else None
            if not next_link and (total is None or len(items) >= int(total)):
                break
            if not data:
                break
            page += 1
        return items

    def _paged_relationship_get(self, path: str, relationship: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._client.get(path, params={"page": page, "page_size": _PAGE_SIZE})
            response.raise_for_status()
            body = _json(response)
            rel_body = body["data"]["relationships"][relationship]
            rel = rel_body["data"]
            if not isinstance(rel, list):
                raise ValueError(f"expected {relationship} relationship list")
            items.extend(rel)
            rel_next = rel_body.get("links", {}).get("next") if isinstance(rel_body.get("links"), dict) else None
            body_next = body.get("links", {}).get("next") if isinstance(body.get("links"), dict) else None
            if not (rel_next or body_next):
                break
            if not rel:
                break
            page += 1
        return items


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


def _json(response: httpx.Response) -> Any:
    return orjson.loads(response.content)


def _metadata_uid(row: dict[str, Any]) -> str:
    metadata = row.get("json_metadata")
    if isinstance(metadata, str):
        metadata = orjson.loads(metadata)
    if not isinstance(metadata, dict):
        return ""
    uid = metadata.get("UID")
    return uid if isinstance(uid, str) else ""


def _normalize_sample_row(
    row: dict[str, Any],
    *,
    known_assay_titles: Iterable[str] | None = None,
) -> dict[str, Any]:
    metadata = row.get("json_metadata")
    if isinstance(metadata, str):
        metadata = orjson.loads(metadata)
    titles = _parse_assay_titles(
        row.get("assays") or "",
        known_assay_titles=known_assay_titles,
    )
    return {
        **row,
        "json_metadata": metadata if isinstance(metadata, dict) else {},
        "assay_titles": titles,
        "numeric_seek_id": row.get("id"),
    }


def _parse_assay_titles(
    raw: str,
    *,
    known_assay_titles: Iterable[str] | None = None,
) -> list[str]:
    if not known_assay_titles:
        return [part.strip() for part in raw.split(",") if part.strip()]

    titles = sorted({title for title in known_assay_titles if title}, key=len, reverse=True)
    parsed: list[str] = []
    pos = 0
    while pos < len(raw):
        while pos < len(raw) and raw[pos] in {",", " "}:
            pos += 1
        if pos >= len(raw):
            break
        match = next((title for title in titles if raw.startswith(title, pos)), None)
        if match is None:
            raise ValueError(f"could not parse assay titles: {raw}")
        end = pos + len(match)
        if end < len(raw) and raw[end] not in {",", " "}:
            raise ValueError(f"could not parse assay titles: {raw}")
        parsed.append(match)
        pos = end
    return parsed
