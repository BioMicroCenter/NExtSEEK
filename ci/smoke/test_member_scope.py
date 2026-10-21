"""A member (the smoke account, not a superuser) stays inside their own projects and out of the paid lanes.

Never starts a paid turn or an ingest:
  * the chat probes are a `{}` body to the force-Container-CC route, which answers 403 at the superuser check before
    the body is read (a broken check answers 422 at validation, still no turn); on prod only that one is sent;
  * the plan-mode and bad-mode probes post to the routed turn route and are local and dev only, because a broken
    plan-mode check there WOULD start a turn (cents, on a throwaway box). The bad mode fails validation first;
  * validate is called without rows or files, so it ends at 403 (not a member) or 400 (member, nothing to check)
    before the pipeline runs;
  * `batch-upload/start/` is not probed: past its gate it ingests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.member_scope import parse_my_project_ids, pick_non_member

VALIDATE = "/nextseek_api/batch-upload/validate/"
FORCE_CC = "/nextseek_api/cc-assistant/cc/query/async/"
TURN = "/nextseek_api/cc-assistant/query/async/"


@pytest.fixture(scope="module")
def scope(web, api, base_url):
    """(the member's project ids, a project they are not in)."""
    page = web.get(f"{base_url}/seek/samples/upload/", timeout=240)
    assert page.status_code == 200, page.status_code
    mine = parse_my_project_ids(page.text)
    assert mine is not None, "the smoke account is offered every project: it is a superuser"
    assert mine, "the upload page offers the member no project"
    listing = api.get(f"{base_url}/nextseek_api/projects/", timeout=60)
    assert listing.status_code == 200, listing.status_code
    other = pick_non_member([p["id"] for p in listing.json().get("data", [])], mine)
    return mine, other


def test_upload_page_offers_only_the_members_projects(scope, api, base_url):
    mine, other = scope
    if other is None:
        pytest.skip("the member belongs to every project listed; nothing to compare")
    assert other not in mine


def test_validate_refuses_a_project_the_member_is_not_in(scope, api, base_url):
    mine, other = scope
    if other is None:
        pytest.skip("no project outside the member's")
    r = api.post(f"{base_url}{VALIDATE}", json={"project_id": other}, timeout=60)
    assert r.status_code == 403, r.status_code


def test_validate_lets_the_member_past_the_gate_on_their_own_project(scope, api, base_url):
    mine, _ = scope
    r = api.post(f"{base_url}{VALIDATE}", json={"project_id": mine[0]}, timeout=60)
    assert r.status_code == 400, r.status_code  # past the membership check; no rows or files, so nothing runs


def test_forcing_container_cc_is_refused_before_any_turn(api, base_url):
    r = api.post(f"{base_url}{FORCE_CC}", json={}, timeout=60)
    assert r.status_code == 403, r.status_code  # 422 here would mean the superuser check no longer runs first


@pytest.fixture
def not_prod(profile):
    if profile == "prod":
        pytest.skip("a broken check here would start a paid turn: local and dev only")


@pytest.mark.usefixtures("not_prod")
def test_plan_mode_is_refused_for_a_member(api, base_url):
    r = api.post(f"{base_url}{TURN}", json={"query": "ci member scope probe", "mode": "plan"}, timeout=60)
    assert r.status_code == 403, r.status_code


@pytest.mark.usefixtures("not_prod")
def test_an_unknown_mode_fails_validation(api, base_url):
    r = api.post(f"{base_url}{TURN}", json={"query": "ci member scope probe", "mode": "no-such-mode"}, timeout=60)
    assert r.status_code == 422, r.status_code
