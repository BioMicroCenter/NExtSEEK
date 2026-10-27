"""Read-only lookups against SEEK's tables for paper reingest."""
from __future__ import annotations

import re

#: The ways a DOI attribute is written in SEEK besides the bare DOI. The stored value is compared case folded
#: and trimmed, so `DOI:`, `HTTPS://DX.DOI.ORG/` and the like match too.
DOI_PREFIXES = ("doi:", "https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
_PREFIX = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.I)


def doi_forms(doi: str) -> list[str]:
    """The bare DOI (case folded, any prefix it came with removed), then the same DOI under each prefix."""
    bare = _PREFIX.sub("", doi.strip()).strip().lower()
    return [bare, *(prefix + bare for prefix in DOI_PREFIXES)]


def doi_project_ids_strict(doi: str) -> list[int]:
    """SEEK project ids of samples whose DOI attribute is this DOI, bare or under any of DOI_PREFIXES.

    A hit puts the run in update mode (spec §6.3). An outage RAISES rather than returning
    [], which would start a duplicate paper in new mode.

    The DOI sits in each sample's JSON, which has no index: the query reads every row of `samples`,
    as it did with the two forms it matched before.
    """
    from django.conf import settings
    from django.db import connections

    forms = doi_forms(doi)
    placeholders = ", ".join(["%s"] * len(forms))
    sql = f"""
        SELECT DISTINCT ps.project_id
          FROM samples AS s
          JOIN projects_samples AS ps ON ps.sample_id = s.id
         WHERE LOWER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(s.json_metadata, '$.DOI')))) IN ({placeholders})
         ORDER BY ps.project_id
    """
    try:
        with connections[settings.SEEK_DATABASE].cursor() as cur:
            cur.execute(sql, forms)
            return [int(r[0]) for r in cur.fetchall()]
    except Exception as exc:
        raise RuntimeError(f"sample catalog unreachable for a DOI lookup: {exc}") from exc
