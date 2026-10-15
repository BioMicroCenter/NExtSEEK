"""An NHP's timeline holds only the descendants in the caller's projects, and one caller's view never serves another's."""

import json
from unittest.mock import patch

import pytest
from django.test import override_settings

from seek.timeline.services import nhp_service

NHP = "FLY-1"
ROWS = {  # uuid -> (project, metadata)
    NHP: (5, {"UID": "FLY-1"}),
    "TIS-5": (5, {"UID": "TIS-5"}),
    "TIS-9": (9, {"UID": "TIS-9"}),
}
SETTINGS = dict(
    SEEK_DATABASE="seek",
    DATABASES={"default": {"NAME": "appdb"}, "seek": {"NAME": "seekdb"}},
)


def _execute(query, params):
    """A fake MySQL that honours the projects_samples clause the way the real query states it."""
    if "seek_sample_tree" in query:
        tree = [{"id": NHP, "children": [{"id": "TIS-5", "children": []}, {"id": "TIS-9", "children": []}]}]
        return [{"uuid": NHP, "full": json.dumps(tree)}]
    params = list(params)
    scoped = "projects_samples" in query
    if scoped:
        # layout: uuids..., term, project ids...
        n_uuids = 3
        term, projects = params[n_uuids], {int(p) for p in params[n_uuids + 1:] if p is not None}
        uuids = params[:n_uuids]
        keep = [u for u in uuids if u == term or ROWS[u][0] in projects]
    else:
        keep = params
    return [{"uuid": u, "json_metadata": json.dumps(ROWS[u][1])} for u in keep]


@pytest.fixture(autouse=True)
def _clean():
    nhp_service.cache.clear()
    yield
    nhp_service.cache.clear()


@override_settings(**SETTINGS)
def test_member_sees_only_their_projects_descendants():
    with patch.object(nhp_service, "execute_query", side_effect=_execute):
        member = nhp_service.get_timeline_data(NHP, (5,))
        assert {e["uuid"] for e in member} == {NHP, "TIS-5"}
        # a superuser afterwards sees both: the cache did not serve the member's view
        admin = nhp_service.get_timeline_data(NHP, None)
        assert {e["uuid"] for e in admin} == {NHP, "TIS-5", "TIS-9"}
        # and the member again still gets theirs
        again = nhp_service.get_timeline_data(NHP, (5,))
        assert {e["uuid"] for e in again} == {NHP, "TIS-5"}
