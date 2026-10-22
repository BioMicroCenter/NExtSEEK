from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.db.models.query import QuerySet

from nextseek_api.batch_upload.uid_sequence import reserve_uid_indexes

pytestmark = pytest.mark.django_db


def _fail_once(exc):
    real, calls = QuerySet.get_or_create, []

    def fake(self, *a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise exc
        return real(self, *a, **kw)

    return fake


def test_deadlock_on_first_create_is_retried_once():
    with patch.object(QuerySet, "get_or_create", autospec=True, side_effect=_fail_once(OperationalError(1213, "deadlock"))):
        assert reserve_uid_indexes("DL-261014MIT", 2, 7) == 8
    assert reserve_uid_indexes("DL-261014MIT", 1, 0) == 10


def test_other_operational_errors_are_not_swallowed():
    with patch.object(QuerySet, "get_or_create", autospec=True, side_effect=_fail_once(OperationalError(2006, "gone"))):
        with pytest.raises(OperationalError):
            reserve_uid_indexes("DL2-261014MIT", 1, 0)
