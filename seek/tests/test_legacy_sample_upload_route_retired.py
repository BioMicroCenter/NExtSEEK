"""The legacy sheet upload's route is retired: it wrote samples by UID with no project check.

Uploads go through ``/nextseek_api/batch-upload/``, which checks the caller's write access. The view and the code path
it drives stay for their tests (``test_media_route_private.py`` calls the view directly).
"""

from django.urls import resolve

from seek import views


def test_the_legacy_sample_upload_route_no_longer_reaches_its_view():
    # The site's catch-all page route takes the path now (a 404 for a page that does not exist).
    match = resolve("/seek/sampleupload/")
    assert match.func is not views.sampleUploadAjax
    assert match.url_name != "sampleUploadAjax"


def test_the_upload_validator_route_still_resolves():
    assert resolve("/seek/samplesvalidate/").url_name == "samplesValidate"
