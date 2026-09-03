"""Per-sample-type counts for the catalog list: one grouped query each, fail-soft."""

from unittest.mock import patch


class TestCatalogCounts:
    @patch("nextseek_api.services.catalog_counts._grouped_counts",
           return_value={13: 10350, 1: 706})
    def test_sample_counts_are_keyed_by_type_id(self, _q):
        from nextseek_api.services.catalog_counts import sample_counts_by_type_id
        assert sample_counts_by_type_id() == {13: 10350, 1: 706}

    @patch("nextseek_api.services.catalog_counts._grouped_counts",
           return_value={13: 18})
    def test_attribute_counts_are_keyed_by_type_id(self, _q):
        from nextseek_api.services.catalog_counts import attribute_counts_by_type_id
        assert attribute_counts_by_type_id() == {13: 18}

    @patch("nextseek_api.services.catalog_counts._grouped_counts", side_effect=RuntimeError("db down"))
    def test_failure_is_soft(self, _q):
        from nextseek_api.services.catalog_counts import sample_counts_by_type_id
        assert sample_counts_by_type_id() == {}
