"""A GET missing a required query parameter answers 400 naming it, never a 500 (MultiValueDictKeyError).

Hermetic: the view's collaborators are stubbed; a bare request must not reach them.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

import seek.views.samples  # noqa: F401
import seek.views.search  # noqa: F401

MEMBER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="m", pk=7)

# (module, view, a query that names every parameter but one, the parameter left out)
CASES = [
    ("samples", "getOperators", {"attribute": "a"}, "sampletype_id"),
    ("samples", "sampleDownload", {"allids": "[]", "sampletype_id": "1"}, "includeSampleTree"),
    ("samples", "sampleExport", {"sampletype_id": "1"}, "allids"),
    ("samples", "getSampleType", {"attribute": "a"}, "sampletype_id"),
    ("search", "sampleSearching", {"sampletype_id": "1"}, "attribute"),
    ("search", "searchingAdvanced", {"sampletype_id": "1", "attribute": "a"}, "filter_searchText"),
    ("search", "searchingUIDs", {}, "filter_searchUIDs"),
]


@pytest.mark.parametrize("module,view,query,missing", CASES)
def test_a_missing_required_parameter_is_a_400_that_names_it(module, view, query, missing):
    request = RequestFactory().get("/", query)
    request.user = MEMBER
    request.session = {}
    mod = getattr(seek.views, module)
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True, "username": "m", "password": "x", "server": "s"}
    with patch(f"seek.views.{module}.SeekDB", return_value=seekdb), \
            patch(f"seek.views.{module}.DBtable_sample", return_value=MagicMock()):
        resp = getattr(mod, view)(request)
    assert resp.status_code == 400
    assert missing in resp.content.decode()
