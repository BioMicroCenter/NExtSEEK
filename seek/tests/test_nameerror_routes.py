"""Dead routes answer 404 instead of 500; getSampleType takes its filter arguments."""

from unittest.mock import patch

import pytest
from django.test import Client

from seek.sample.table import DBtable_sample


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/seek/url/smoke/", "/seek/remote/"])
def test_removed_routes_do_not_500(path):
    assert Client(raise_request_exception=False).get(path).status_code < 500


def test_get_sample_type_with_an_attribute_uses_the_default_filter():
    table = DBtable_sample.__new__(DBtable_sample)
    rows = {"rows": [], "total": 0}
    with patch("seek.sample.table.DBtable_sampleattribute") as attr, \
            patch.object(DBtable_sample, "_retrieveSamplesInType", return_value=rows), \
            patch.object(DBtable_sample, "_filterSamples", return_value=[]):
        attr.return_value.validateFilters.return_value = ("okay", 1)
        out = table.getSampleType({}, 1, "Some attr")
    assert '"status": 1' in out
    attr.return_value.validateFilters.assert_called_once_with(1, "Some attr", "No Filter", "", "")


def test_the_view_passes_the_query_string_filter_to_get_sample_type():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from django.test import RequestFactory
    import seek.views.samples as views

    request = RequestFactory().get("/", {"sampletype_id": "1", "attribute": "a", "filter_rule": "Between",
                                         "filter_valueFrom": "2", "filter_valueTo": "9"})
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, username="u", pk=1)
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True}
    dbsample = MagicMock()
    dbsample.getSampleType.return_value = "{}"
    with patch.object(views, "SeekDB", return_value=seekdb), patch.object(views, "DBtable_sample", return_value=dbsample), \
            patch.object(views, "_callerProjectIds", return_value=None):
        views.getSampleType(request)
    kwargs = dbsample.getSampleType.call_args.kwargs
    assert (kwargs["filter_rule"], kwargs["filter_valueFrom"], kwargs["filter_valueTo"]) == ("Between", "2", "9")
