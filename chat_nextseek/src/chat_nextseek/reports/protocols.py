"""Protocol fetching and content extraction helpers used by the reporter
flow. Handles SOP reference discovery, HTTP retrieval from fairdata/fairdomhub,
DOCX/PDF text extraction, sanitization, and blob download.

Moved out of ``helpers.py`` during Phase 2 of the src/ restructure.
"""
from __future__ import annotations

import html
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from zipfile import ZipFile

import requests

from ..artifacts import ArtifactStore
from ..config import ChatConfig
from ..helpers.json_io import estimate_tokens_from_text


def extract_protocol_refs_from_metadata(metadata: dict) -> list[dict[str, str]]:
    """
    Walk metadata dict and collect supported protocol references from any key named 'Protocol'.
    Supported references:
    - fairdata-dev / fairdata URLs pointing at /sops/{id-or-name}
    - fairdomhub URLs pointing at /sops/{id-or-name}
    - direct protocol names beginning with 'P.'
    Unsupported external/vendor URLs are ignored.
    """
    refs: dict[tuple[str, str], dict[str, str]] = {}

    def _add(source: str, value: str, raw_value: str) -> None:
        clean = value.strip()
        if not clean:
            return
        refs[(source, clean)] = {"source": source, "value": clean, "raw": raw_value}

    def _classify(value: Any) -> None:
        if isinstance(value, (int, float)):
            _add("fairdata-dev", str(value), str(value))
            return
        if not isinstance(value, str):
            return

        raw = value.strip()
        if not raw:
            return

        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        sop_match = re.search(r"/sops/([^/?#]+)", path, flags=re.IGNORECASE)

        if host in {"fairdata-dev.mit.edu", "fairdata.mit.edu"} and sop_match:
            _add(host, sop_match.group(1), raw)
            return
        if host == "fairdomhub.org" and sop_match:
            _add(host, sop_match.group(1), raw)
            return
        if re.match(r"^P\.[A-Za-z0-9._-]+$", raw):
            _add("protocol_name", raw, raw)
            return

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() == "protocol":
                    _classify(v)
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(metadata)
    return [refs[key] for key in sorted(refs)]


def _request_protocol_record(config: ChatConfig, base_url: str, protocol_ref: str) -> dict:
    """
    Fetch a protocol record from a specific host using the SOP detail endpoint.
    Accepts numeric ids or protocol names.
    """
    host = (urlparse(base_url).netloc or "").lower()
    if host == "fairdomhub.org":
        url = f"{base_url.rstrip('/')}/sops/{quote(protocol_ref, safe='')}/"
    else:
        url = f"{base_url.rstrip('/')}/nextseek_api/sops/{quote(protocol_ref, safe='')}/"
    auth = None
    headers: dict[str, str] = {}
    auth_mode = "None"

    if host == "fairdomhub.org":
        fdh_api = os.getenv("FDH_API")
        if fdh_api:
            headers["Authorization"] = f"Bearer {fdh_api}"
            headers["Accept"] = "application/json"
            auth_mode = "Bearer(FDH_API)"
    elif config.API_USER and config.API_PASS:
        auth = (config.API_USER, config.API_PASS)
        auth_mode = "Basic"

    print("[DEBUG][API] Request:")
    print("  METHOD: GET")
    print(f"  URL:    {url}")
    print("  PARAMS: {'page_size': 1000}")
    print("  BODY:   {}")
    print(f"  AUTH:   {auth_mode}")
    print("  TIMEOUT:90s")

    try:
        resp = requests.get(url, auth=auth, headers=headers or None, params={"page_size": 1000}, timeout=90)
        print("[DEBUG][API] Response:")
        print(f"  STATUS: {resp.status_code}")
        preview = resp.text[:300].replace("\n", " ")
        print(f"  PREVIEW: {preview!r}")
        try:
            data = resp.json()
        except Exception:
            data = {"_raw": resp.text[:1000]}
        return {
            "ok": resp.ok,
            "url": url,
            "status_code": resp.status_code,
            "method": "GET",
            "query": {"page_size": 1000},
            "body": {},
            "data": data,
            "source_base_url": base_url.rstrip("/"),
            "protocol_ref": protocol_ref,
        }
    except Exception as e:
        print(f"[DEBUG][API] Exception: {repr(e)}")
        return {
            "ok": False,
            "error": repr(e),
            "url": url,
            "method": "GET",
            "source_base_url": base_url.rstrip("/"),
            "protocol_ref": protocol_ref,
        }


def fetch_protocols(config, protocol_refs: list[dict[str, str]]) -> dict:
    """
    Fetch protocol details for classified metadata references.
    fairdata-dev/fairdata hosts are queried directly; fairdomhub uses its own host;
    P.* names are resolved against the configured NExtSEEK API host.
    Returns a mapping keyed by the protocol reference value.
    """
    results: dict[str, dict] = {}
    host_map = {
        "fairdata-dev.mit.edu": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://nextseek-dev.mit.edu",
        "fairdata.mit.edu": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://nextseek-dev.mit.edu",
        "fairdomhub.org": "https://fairdomhub.org",
        "fairdata-dev": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://fairdata-dev.mit.edu",
        "protocol_name": getattr(config, "NEXTSEEK_BASE_URL", "") or "https://fairdata-dev.mit.edu",
    }

    for ref in protocol_refs or []:
        source = ref.get("source", "")
        value = ref.get("value", "")
        if not value:
            continue
        base_url = host_map.get(source)
        if not base_url:
            print("[DEBUG][REPORTER_PROTOCOL] Skipping unsupported protocol reference:", ref)
            continue
        try:
            resp = _request_protocol_record(config, base_url, value)
            resp["protocol_source"] = source
            resp["protocol_raw"] = ref.get("raw")
            results[value] = resp
            print("[DEBUG][REPORTER_PROTOCOL] Fetched protocol", value, "source:", source, "ok:", resp.get("ok"))
        except Exception as e:
            results[value] = {"ok": False, "error": repr(e), "protocol_source": source, "protocol_raw": ref.get("raw")}
            print("[DEBUG][REPORTER_PROTOCOL] Failed to fetch protocol", value, "source:", source, "err:", repr(e))
    return results


def _extract_docx_text(content: bytes) -> str | None:
    """
    Extract plain text from a DOCX binary by reading word/document.xml.
    Strips tags, unescapes entities, and returns None on failure so callers can continue gracefully.
    """
    try:
        with ZipFile(BytesIO(content)) as zf:
            with zf.open("word/document.xml") as f:
                xml = f.read().decode("utf-8", errors="ignore")
        # Strip tags and unescape XML entities
        text = re.sub(r"<[^>]+>", " ", xml)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        print("[DEBUG][REPORTER_PROTOCOL] docx extract failed:", repr(e))
        return None


def _extract_pdf_text(content: bytes) -> str | None:
    """
    Extract text from a PDF binary using PyPDF2 when available.
    Returns None when the library is missing or extraction fails, logging debug hints for diagnostics.
    """
    try:
        import PyPDF2
    except Exception:
        print("[DEBUG][REPORTER_PROTOCOL] PyPDF2 not available; skipping PDF text extraction.")
        return None
    try:
        reader = PyPDF2.PdfReader(BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None
    except Exception as e:
        print("[DEBUG][REPORTER_PROTOCOL] PDF extract failed:", repr(e))
        return None


def sanitize_protocols_for_llm(protocol_payloads: dict) -> dict:
    """
    Sanitize protocol payloads for LLM consumption:
    - Replace localhost URLs with fairdata-dev.mit.edu
    - Remove internal fields not needed by the LLM
    Returns a cleaned copy of the protocol payloads.
    Keeps URLs model-safe while preserving useful content for prompt context.
    """
    if not protocol_payloads:
        return {}

    def fix_url(url: str | None) -> str | None:
        if not url:
            return url
        if "localhost" in url or "127.0.0.1" in url:
            path_match = re.search(r"https?://[^/]+(/.*)", url)
            if path_match:
                return f"https://fairdata-dev.mit.edu{path_match.group(1)}"
        return url

    def sanitize_dict(d: dict) -> dict:
        result = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = sanitize_dict(v)
            elif isinstance(v, list):
                result[k] = [sanitize_dict(i) if isinstance(i, dict) else fix_url(i) if isinstance(i, str) and ("localhost" in i or "127.0.0.1" in i) else i for i in v]
            elif isinstance(v, str) and ("localhost" in v or "127.0.0.1" in v):
                result[k] = fix_url(v)
            else:
                result[k] = v
        return result

    return {pid: sanitize_dict(resp) if isinstance(resp, dict) else resp for pid, resp in protocol_payloads.items()}


def download_and_extract_protocol_blobs(protocol_payloads: dict, base_dir: str | Path, config=None) -> dict:
    """
    For each protocol response, download attached files (content_blobs), save them under base_dir/protocols/files,
    and attempt to extract text (docx/pdf). Returns a mapping id -> list of file metadata with text.
    """
    store = ArtifactStore(base_dir)
    results: dict[str, list[dict]] = {}
    session = requests.Session()

    for pid, resp in (protocol_payloads or {}).items():
        files_out: list[dict] = []
        source_base_url = resp.get("source_base_url") if isinstance(resp, dict) else None
        source_host = (urlparse(source_base_url).netloc or "").lower() if source_base_url else ""
        if source_host == "fairdomhub.org":
            session.auth = None
            session.headers.pop("Authorization", None)
            fdh_api = os.getenv("FDH_API")
            if fdh_api:
                session.headers["Authorization"] = f"Bearer {fdh_api}"
        else:
            session.headers.pop("Authorization", None)
            session.auth = (config.API_USER, config.API_PASS) if config and config.API_USER and config.API_PASS else None
        # Response structure: {"ok": ..., "data": {"data": {"id": ..., "attributes": {"content_blobs": [...]}}}}
        attrs = resp.get("data", {}).get("data", {}).get("attributes", {}) if isinstance(resp, dict) else {}
        blobs = attrs.get("content_blobs") or []
        for idx, blob in enumerate(blobs):
            link = blob.get("link") or blob.get("url")
            # Fix localhost URLs - content blobs are served from fairdata-dev.mit.edu
            if link and ("localhost" in link or "127.0.0.1" in link):
                path_match = re.search(r"https?://[^/]+(/.*)", link)
                if path_match and source_base_url:
                    link = f"{source_base_url}{path_match.group(1)}"
            fname = blob.get("original_filename") or f"{pid}_{idx}"
            entry: dict = {"filename": fname, "content_type": blob.get("content_type"), "link": link}
            if not link:
                files_out.append({"filename": fname, "ok": False, "error": "No link in content_blob"})
                continue
            try:
                content_resp = None
                attempted = []
                for candidate in (f"{link}/download", f"{link}?download=1", link):
                    attempted.append(candidate)
                    r = session.get(candidate, timeout=30)
                    ctype_hdr = (r.headers.get("Content-Type") or "").lower()
                    looks_json = ctype_hdr.startswith("application/vnd.api+json") or r.content[:1] in (b"{", b"[")
                    is_ok = r.status_code == 200 and not looks_json
                    if is_ok:
                        content_resp = r
                        entry["status_code"] = r.status_code
                        entry["response_content_type"] = r.headers.get("Content-Type")
                        entry["download_url_used"] = candidate
                        break
                    entry["last_status"] = r.status_code
                    entry["last_response_content_type"] = r.headers.get("Content-Type")

                if content_resp is None:
                    entry.update({"ok": False, "error": f"Unable to download blob; tried {attempted}"})
                    files_out.append(entry)
                    continue

                artifact_entry = store.write_bytes(
                    key=f"protocol_blob_{pid}_{idx}",
                    label=fname,
                    filename=fname,
                    payload=content_resp.content,
                    kind="protocol",
                    subdir="files",
                    mime=blob.get("content_type"),
                )
                dest_path = artifact_entry["path"] if artifact_entry else None
                text = None
                text_error = None
                ctype = (blob.get("content_type") or "").lower()
                try:
                    if "pdf" in ctype or fname.lower().endswith(".pdf") or content_resp.content[:4] == b"%PDF":
                        text = _extract_pdf_text(content_resp.content)
                    elif "word" in ctype or fname.lower().endswith(".docx"):
                        text = _extract_docx_text(content_resp.content)
                    else:
                        # Fallback: try docx, then pdf
                        text = _extract_docx_text(content_resp.content) or _extract_pdf_text(content_resp.content)
                except Exception as e:
                    text_error = repr(e)

                # Truncate text to ~3000 tokens max
                text_truncated = False
                if text:
                    PROTOCOL_TOKEN_LIMIT = 3000
                    token_count = estimate_tokens_from_text(text)
                    if token_count > PROTOCOL_TOKEN_LIMIT:
                        # Truncate: ~4 chars per token
                        max_chars = PROTOCOL_TOKEN_LIMIT * 4
                        text = text[:max_chars] + "\n\n[... truncated, exceeded 3000 token limit ...]"
                        text_truncated = True

                entry.update(
                    {
                        "path": dest_path,
                        "md5": blob.get("md5sum"),
                        "sha1": blob.get("sha1sum"),
                        "size": blob.get("size"),
                        "ok": True,
                        "text": text,
                        "text_truncated": text_truncated,
                        "text_error": text_error,
                    }
                )
                files_out.append(entry)
            except Exception as e:
                entry.update({"ok": False, "error": repr(e)})
                files_out.append(entry)
        if files_out:
            results[str(pid)] = files_out
    return results
