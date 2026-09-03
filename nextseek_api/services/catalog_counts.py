"""Per-sample-type counts for the catalog list, computed in single grouped
queries. Never one query per row. Fail soft to {} so a DB hiccup empties a
column, never the page (the soft-dependency rule the catalog already follows).

Both counted tables (`samples`, `sample_attributes`) live in the SEEK schema,
which the default connection reaches cross-schema, the same way
`dmac.dbtable_sampletypesclades.getAllCounts` does.
"""

import logging

import dmac.settings as _settings
from django.db import connections

logger = logging.getLogger(__name__)


def _seek_schema() -> str:
    return _settings.DATABASES[_settings.SEEK_DATABASE]["NAME"]


def _grouped_counts(sql: str) -> dict:
    with connections["default"].cursor() as cur:
        cur.execute(sql)
        return {int(row[0]): int(row[1]) for row in cur.fetchall()}


def sample_counts_by_type_id() -> dict:
    """{sample_types.id: number of samples of that type}."""
    seekdb = _seek_schema()
    try:
        return _grouped_counts(
            f"SELECT sample_type_id, COUNT(*) FROM {seekdb}.samples "
            f"WHERE sample_type_id IS NOT NULL GROUP BY sample_type_id"
        )
    except Exception:
        logger.exception("sample counts unavailable; catalog counts will be blank")
        return {}


def attribute_counts_by_type_id() -> dict:
    """{sample_types.id: number of attribute definitions on that type}."""
    seekdb = _seek_schema()
    try:
        return _grouped_counts(
            f"SELECT sample_type_id, COUNT(*) FROM {seekdb}.sample_attributes "
            f"WHERE sample_type_id IS NOT NULL GROUP BY sample_type_id"
        )
    except Exception:
        logger.exception("attribute counts unavailable; catalog counts will be blank")
        return {}
