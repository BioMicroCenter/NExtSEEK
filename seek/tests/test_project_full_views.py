"""Plan C: no duplicate stats, ranked bundles, modal-route helper, the /samples/ route."""

import re
from pathlib import Path

from django.conf import settings


def _css():
    return (Path(settings.BASE_DIR) / "themes" / "NextSeek" / "static" / "css" / "nextseek.css").read_text()


def _js():
    return (Path(settings.BASE_DIR) / "themes" / "NextSeek" / "static" / "js" / "nextseek.js").read_text()


class TestNoDuplicateStats:
    def test_m_stats_cards_flex_is_inside_a_mobile_media_query(self):
        css = _css()
        # The top-level .m-stats-cards rule must NOT force display:flex (that overrode
        # .m-only:none on desktop and rendered the stats twice).
        top = re.search(r"\.m-stats-cards\s*\{[^}]*\}", css).group(0)
        assert "display: flex" not in top and "display:flex" not in top
        # A mobile-scoped rule turns it on.
        assert re.search(
            r"@media[^{]*max-width:\s*767\.98px[^{]*\{[^@]*\.m-stats-cards[^}]*display:\s*flex",
            css, re.S,
        )


class TestBundleRanking:
    def test_bundles_are_ranked_by_edge_volume_desc_with_counts(self):
        from nextseek_api.services.project_connections import _derived_bundles
        rows = (
            [{"internal_assay": "Big", "parent_sample_type": "AB", "child_sample_type": "TIS"}] * 5 +
            [{"internal_assay": "Small", "parent_sample_type": "AB", "child_sample_type": "CEL"}] * 1
        )
        known = {"AB", "TIS", "CEL"}
        out = _derived_bundles(rows, known)
        assert [b["label"] for b in out] == ["Big", "Small"]      # ranked, not alphabetical
        assert out[0]["n_edges"] == 5 and out[1]["n_edges"] == 1
