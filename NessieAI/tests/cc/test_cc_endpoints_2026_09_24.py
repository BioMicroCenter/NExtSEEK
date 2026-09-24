"""Container-CC's REST surface after the 2026-09-24 endpoint review.

The operator's scope for REST: SEEK records the graph does not hold (the SOP list), downloads by UID,
and the catalog lists; every sample question goes to the graph. Container-CC's api-read allowlist
keeps exactly the eight pairs below. `/nextseek_api/experiments/` never existed in the URL conf, and
the old `admin/samples/retrieve/` alias is replaced by its new name (the server still answers the
alias so saved chats replay). The unenriched catalog CC reads to build a request body lists the same
eight, so CC is told only what it can call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ENFORCED = REPO / "NessieAI" / "ns" / "read_safe_endpoints.json"
BAKED = (REPO / "NessieAI" / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek"
         / "context" / "read_safe_endpoints.json")
CATALOG = REPO / "NessieAI" / "chat_nextseek" / "src" / "chat_nextseek" / "context" / "min_api_endpoints.json"

CC_READ_SET = {
    ("POST", "/nextseek_api/samples/graph_search/"),
    ("GET", "/nextseek_api/projects/"),
    ("GET", "/nextseek_api/sample_types/"),
    ("GET", "/nextseek_api/assays/"),
    ("POST", "/nextseek_api/samples/retrieve/"),
    ("GET", "/nextseek_api/investigations/"),
    ("GET", "/nextseek_api/people/"),
    ("GET", "/nextseek_api/sops/"),
}


def _allowlist_pairs(path: Path) -> set[tuple[str, str]]:
    return {(m.upper(), e["endpoint"]) for e in json.loads(path.read_text()) for m in e["methods"]}


@pytest.mark.parametrize("path", [ENFORCED, BAKED], ids=["enforced", "baked"])
def test_the_cc_allowlist_is_exactly_the_eight_read_pairs(path):
    assert _allowlist_pairs(path) == CC_READ_SET


@pytest.mark.parametrize("path", [ENFORCED, BAKED], ids=["enforced", "baked"])
def test_the_phantom_and_the_old_retrieve_alias_are_gone(path):
    endpoints = {e["endpoint"] for e in json.loads(path.read_text())}
    assert "/nextseek_api/experiments/" not in endpoints
    assert "/nextseek_api/admin/samples/retrieve/" not in endpoints


def test_the_catalog_cc_builds_bodies_from_lists_only_what_it_can_call():
    rows = json.loads(CATALOG.read_text())
    assert {(r["method"].upper(), r["path"]) for r in rows} == CC_READ_SET
