"""handler500: JSON for the SEEK proxy API, the site's own page elsewhere (#108)."""
import json
import logging
from unittest.mock import patch

import pytest
import requests
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from dmac import urls as dmac_urls
from dmac.views import api_server_error

rf = RequestFactory()


def _times_out(request):
    raise requests.ReadTimeout("slow")


def _fails(request):
    raise RuntimeError("bug")


# A URLconf for the request-path test below: Django's handler, middleware and logging, with views that raise.
urlpatterns = [
    path("nextseek_api/zz_times_out/", _times_out),
    path("nextseek_api/zz_fails/", _fails),
    path("zz_page/", _fails),
]
handler500 = "dmac.views.api_server_error"


def _handle(method, path, exc):
    """Call the handler the way Django does: while the exception is being handled."""
    request = getattr(rf, method)(path)
    try:
        raise exc
    except Exception:
        return api_server_error(request)


def test_handler500_is_wired():
    assert dmac_urls.handler500 == "dmac.views.api_server_error"


def test_write_that_times_out_is_a_json_504_that_says_the_write_may_have_happened():
    resp = _handle("patch", "/nextseek_api/sops/42/", requests.ReadTimeout("slow"))
    body = json.loads(resp.content)
    assert resp.status_code == 504 and resp["Content-Type"] == "application/json"
    assert "may still have" in body["errors"][0]["detail"]


def test_read_that_times_out_does_not_claim_a_write():
    resp = _handle("get", "/nextseek_api/sample_types/", requests.ReadTimeout("slow"))
    assert resp.status_code == 504
    assert "may still have" not in json.loads(resp.content)["errors"][0]["detail"]


def test_connection_error_is_a_json_502():
    resp = _handle("post", "/nextseek_api/samples/", requests.ConnectionError("down"))
    assert resp.status_code == 502 and resp["Content-Type"] == "application/json"


def test_any_other_error_is_a_json_500():
    resp = _handle("post", "/nextseek_api/samples/", RuntimeError("bug"))
    assert resp.status_code == 500 and resp["Content-Type"] == "application/json"
    assert json.loads(resp.content) == {"errors": [{"title": "Server error"}]}


def test_a_path_outside_the_api_gets_the_sites_page():
    with patch("dmac.views.server_error", return_value=HttpResponse("page", status=500)) as page:
        resp = _handle("get", "/some/page/", RuntimeError("bug"))
    page.assert_called_once()
    assert resp.content == b"page"


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__, DEBUG=False)
def test_through_the_request_path_the_api_answers_json_and_the_site_its_page(caplog):
    """Django still holds the exception when it calls handler500, no middleware answers first, and the traceback
    is still logged."""
    client = Client(raise_request_exception=False)
    with caplog.at_level(logging.ERROR, logger="django.request"):
        resp = client.post("/nextseek_api/zz_times_out/")
    assert resp.status_code == 504 and resp["Content-Type"] == "application/json"
    assert "may still have" in json.loads(resp.content)["errors"][0]["detail"]
    assert any(rec.exc_info for rec in caplog.records)
    resp = client.get("/nextseek_api/zz_fails/")
    assert resp.status_code == 500 and resp["Content-Type"] == "application/json"
    resp = client.get("/zz_page/")
    assert resp.status_code == 500 and "text/html" in resp["Content-Type"]


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_a_sop_patch_that_times_out_answers_json_504_through_the_real_urlconf():
    from nextseek_api.services.sops import SopProxyViewSet
    user = get_user_model().objects.create_user("json500", password="pw-json500")
    client = Client(raise_request_exception=False)
    client.force_login(user)
    body = {"data": {"type": "sops", "id": "42", "attributes": {"title": "t"}}}
    # The caller has a SEEK login of its own, as every real SOP caller does (a permission may require one).
    with patch.object(SopProxyViewSet.client, "update_sop", side_effect=requests.ReadTimeout("slow")), \
         patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42"), \
         patch("nextseek_api.helpers.get_auth", return_value=("json500", "pw-json500")):
        resp = client.patch("/nextseek_api/sops/42/", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 504 and resp["Content-Type"] == "application/json"
