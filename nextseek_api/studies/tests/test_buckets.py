"""The bucket rule (tool spec T5): an investigation's holding study is its one study whose title ends in
"Unpublished", case and surrounding whitespace aside."""
from unittest.mock import MagicMock

import pytest

from nextseek_api.studies import buckets


@pytest.mark.parametrize("title", ["Alpha Unpublished", "  alpha UNPUBLISHED", "Gamma - Unpublished",
                                   "Delta Unpublished ", "unpublished"])
def test_bucket_titles_ignore_case_and_surrounding_whitespace(title):
    assert buckets.is_bucket_title(title)


@pytest.mark.parametrize("title", ["Unpublished data of Alpha", "Alpha Published", "", None])
def test_other_titles_are_not_buckets(title):
    assert not buckets.is_bucket_title(title)


def test_rows_give_one_bucket_per_investigation_and_the_refusals():
    found = buckets.buckets_from_rows([(20, 7, "Alpha Unpublished"), (21, 7, "Paper One"),
                                       (30, 8, "Beta Unpublished"), (31, 8, "Beta unpublished "),
                                       (40, 9, "Gamma Paper"), (50, None, "Orphan Unpublished")])
    assert dict(found.by_investigation) == {7: 20}
    assert dict(found.several) == {8: (30, 31)}
    assert found.study_ids == frozenset({20, 30, 31, 50})
    assert found.refusal(7) is None and found.bucket_of(7) == 20
    assert found.refusal(8) == buckets.SEVERAL_BUCKETS and found.bucket_of(8) is None
    assert found.refusal(9) == buckets.NO_BUCKET
    assert found.refusal(1234) == buckets.NO_BUCKET


def test_bucket_study_ids_reads_every_study_of_seek(settings):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(20, 7, "Alpha Unpublished"), (21, 7, "Paper One")]
    found = buckets.bucket_study_ids(conn)
    sql = str(conn.execute.call_args.args[0])
    assert "SELECT id, investigation_id, title FROM" in sql and ".studies" in sql
    assert dict(found.by_investigation) == {7: 20}
