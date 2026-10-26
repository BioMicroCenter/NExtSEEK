"""Read-only lookups against SEEK's tables for paper reingest."""
from __future__ import annotations


def doi_project_ids_strict(doi: str) -> list[int]:
    """SEEK project ids of samples whose DOI attribute is this DOI (bare or as a doi.org URL).

    A hit puts the run in update mode (spec §6.3). An outage RAISES rather than returning
    [], which would start a duplicate paper in new mode.
    """
    from django.conf import settings
    from django.db import connections

    bare = doi.strip().lower()
    sql = """
        SELECT DISTINCT ps.project_id
          FROM samples AS s
          JOIN projects_samples AS ps ON ps.sample_id = s.id
         WHERE LOWER(JSON_UNQUOTE(JSON_EXTRACT(s.json_metadata, '$.DOI'))) IN (%s, %s)
         ORDER BY ps.project_id
    """
    try:
        with connections[settings.SEEK_DATABASE].cursor() as cur:
            cur.execute(sql, [bare, f"https://doi.org/{bare}"])
            return [int(r[0]) for r in cur.fetchall()]
    except Exception as exc:
        raise RuntimeError(f"sample catalog unreachable for a DOI lookup: {exc}") from exc
