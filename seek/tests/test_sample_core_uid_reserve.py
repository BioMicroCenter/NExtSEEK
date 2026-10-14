"""The legacy single-sample path draws its UID index from the batch path's committed counter."""

import datetime
from unittest.mock import MagicMock

import pytest

from nextseek_api.batch_upload.uid_sequence import reserve_uid_indexes
from seek.sample.core import SampleCore

pytestmark = pytest.mark.django_db


def test_legacy_path_reserves_from_the_same_counter():
    prefix = "NHP-" + datetime.datetime.now().strftime("%y%m%d") + "MIT"
    core = SampleCore.__new__(SampleCore)
    core.getSampleUIDIndex = MagicMock(return_value=8)  # samples hold up to -7, nothing inserted since
    first = reserve_uid_indexes(prefix, 3, 7)  # a batch takes -8, -9, -10
    uid = core._defineUID({"lababbv": "MIT"}, {}, {"sampletype": {"title": "NHP_blood"}})
    assert uid.startswith(prefix + "-")
    assert int(uid.rsplit("-", 1)[1]) not in range(first, first + 3)
