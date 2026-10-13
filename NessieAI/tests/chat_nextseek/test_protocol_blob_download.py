"""Protocol attachments download from the host the record came from, with the record's credentials (#91).

SEEK writes each content-blob link on its own site_base_host (http://localhost:3000 by default), which the app
container cannot fetch, and the app's internal listener is Django, which has no /sops/ route. The record is fetched
from ChatConfig.NEXTSEEK_BASE_URL (NEXTSEEK_INTERNAL_BASE_URL on a templated install), and that same URL's
POST /nextseek_api/sops/download/ streams the blob from SEEK through the app's SEEK proxy. So the blob request goes to
the record's host with the record's Basic credentials, and the stored record is not rewritten. FairDOMHub records keep
their own host and their own token.
"""
from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from chat_nextseek.config import _resolve_nextseek_base_url
from chat_nextseek.reports import protocols

SEEK_LINK = "http://localhost:3000/sops/13/content_blobs/117"   # SEEK's default site_base_host
FDH_LINK = "https://fairdomhub.org/sops/5/content_blobs/7"
CREDS = ("api-user", "api-pass")

# What startup/templates/nextseek.env.template renders on a fresh install (default port).
TEMPLATE_ENV = {
    "SEEK_HOSTNAME": "http://seek:3000",
    "NEXTSEEK_HOSTNAME": "127.0.0.1:8000",
    "NEXTSEEK_BASE_URL": "http://127.0.0.1:8000",
    "NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000",
}
# A box that hand-set the public app host: the blob must still not go there.
HAND_SET_ENV = {**TEMPLATE_ENV, "NEXTSEEK_HOSTNAME": "nextseek.example.org",
                "NEXTSEEK_BASE_URL": "https://nextseek.example.org"}


def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", f"<w:document><w:t>{text}</w:t></w:document>")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, body=b"", ctype="application/octet-stream", data=None):
        self.status_code, self.ok, self.content, self._data = status, status < 400, body, data
        self.headers = {"Content-Type": ctype}
        self.text = body.decode("latin-1")

    def json(self):
        return self._data


@pytest.fixture
def wire(monkeypatch):
    """Every request the protocol code sends: method, host, url, Basic auth, Authorization header, JSON body."""
    sent = []

    def record(method, url, auth=None, headers=None, body=None):
        sent.append({"method": method, "host": urlparse(url).netloc, "url": url, "auth": auth,
                     "authz": (headers or {}).get("Authorization"), "json": body})

    def fake_get(url, auth=None, headers=None, **_):        # the record fetch
        record("GET", url, auth, headers)
        link = FDH_LINK if urlparse(url).netloc == "fairdomhub.org" else SEEK_LINK
        blobs = [{"link": link, "original_filename": "prep.docx", "content_type": "application/msword"}]
        return _Resp(data={"data": {"id": "13", "attributes": {"content_blobs": blobs}}})

    def blob(url):
        served = url.endswith("/nextseek_api/sops/download/") or url == f"{FDH_LINK}/download"
        return _Resp(body=_docx("TruSeq Stranded mRNA")) if served else _Resp(404, b"<html>404</html>", "text/html")

    def fake_post(url, json=None, auth=None, headers=None, **_):
        record("POST", url, auth, headers, json)
        return blob(url)

    def session_get(self, url, **_):
        record("GET", url, self.auth, self.headers)
        return blob(url)

    def session_post(self, url, json=None, **_):
        record("POST", url, self.auth, self.headers, json)
        return blob(url)

    monkeypatch.setattr(protocols.requests, "get", fake_get)
    monkeypatch.setattr(protocols.requests, "post", fake_post)
    monkeypatch.setattr(protocols.requests.Session, "get", session_get)
    monkeypatch.setattr(protocols.requests.Session, "post", session_post)
    return sent


@pytest.mark.parametrize("env", [TEMPLATE_ENV, HAND_SET_ENV], ids=["templated-install", "hand-set-public-host"])
def test_the_blob_comes_from_the_record_host_with_the_record_credentials(monkeypatch, tmp_path, wire, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = SimpleNamespace(API_USER=CREDS[0], API_PASS=CREDS[1], NEXTSEEK_BASE_URL=_resolve_nextseek_base_url())
    raw = protocols.fetch_protocols(config, [{"source": "fairdata-dev.mit.edu", "value": "13",
                                              "raw": "https://fairdata-dev.mit.edu/sops/13"}])

    files = protocols.download_and_extract_protocol_blobs(raw, tmp_path, config=config)

    assert files["13"][0]["ok"] is True and "TruSeq" in files["13"][0]["text"]
    assert [(r["method"], r["host"], r["auth"]) for r in wire] == [
        ("GET", "127.0.0.1:8000", CREDS), ("POST", "127.0.0.1:8000", CREDS)]
    assert wire[1]["url"] == "http://127.0.0.1:8000/nextseek_api/sops/download/"
    assert wire[1]["json"] == {"uid_or_id": "13", "blob_id": 117}
    assert raw["13"]["data"]["data"]["attributes"]["content_blobs"][0]["link"] == SEEK_LINK


def test_a_fairdomhub_blob_stays_on_fairdomhub_with_its_token_and_no_basic_credentials(monkeypatch, tmp_path, wire):
    for key, value in TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FDH_API", "fdh-token")
    config = SimpleNamespace(API_USER=CREDS[0], API_PASS=CREDS[1], NEXTSEEK_BASE_URL=_resolve_nextseek_base_url())
    raw = protocols.fetch_protocols(config, [{"source": "fairdomhub.org", "value": "5",
                                              "raw": "https://fairdomhub.org/sops/5"}])

    files = protocols.download_and_extract_protocol_blobs(raw, tmp_path, config=config)

    assert files["5"][0]["ok"] is True
    assert [(r["method"], r["host"], r["auth"], r["authz"]) for r in wire] == [
        ("GET", "fairdomhub.org", None, "Bearer fdh-token"), ("GET", "fairdomhub.org", None, "Bearer fdh-token")]
    assert wire[1]["url"] == f"{FDH_LINK}/download"
