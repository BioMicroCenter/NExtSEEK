"""The attribute write routes accept POST only."""

import pytest
from django.test import Client


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/seek/attribute/save/", "/seek/attribute/delete/"])
def test_get_is_refused(path):
    assert Client().get(path).status_code == 405
