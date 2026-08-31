"""The title-collation query must apply the column's CHARACTER SET, not just its collation.

Regression test for a production 500. `_title_collation` reads the real collation of
`sample_attributes.title` and applies it to both sides of the comparison. The column side is
fine, but the candidate literals are bound in the CONNECTION charset, so on a database whose
column charset differs from the connection's, MySQL rejects the statement:

    (1253, "COLLATION 'utf8mb3_unicode_ci' is not valid for CHARACTER SET 'utf8mb4'")

Production's seek_production is an aged database: 558 utf8mb3 columns and 13 latin1, under a
utf8mb4 connection, because a schema's default charset governs only newly created columns and
MySQL never converts existing ones. A freshly built dev stack or test database has utf8mb4
columns throughout, so it cannot reproduce this -- which is why every mutation 500'd on
production while the suite stayed green.

The gateway is driven with a stubbed `_execute` so this runs without the disposable
attribute database (whose fixture needs an evidence-run boundary that is not reproducible
outside its author's machine).
"""
from __future__ import annotations

import pytest

from nextseek_api.attributes.repository import (
    SeekAttributeGateway,
    TitleCollationRequest,
)


def _gateway_capturing_grouping_sql(collation: str, charset: str):
    """A gateway whose only database contact is stubbed, capturing the grouping SQL."""
    gateway = SeekAttributeGateway.__new__(SeekAttributeGateway)
    gateway.alias = "seek"
    gateway.query_count = 0
    gateway.max_parameters = 0
    gateway.chunk_sizes = []
    gateway._rss_baseline = 0
    gateway.added_peak_rss = 0
    captured: list[str] = []

    def fake_execute(sql: str, params=()):
        if "INFORMATION_SCHEMA" in sql.upper():
            # Column-metadata probe. Ordered collation-first so this stub satisfies the
            # single-column shape as well as the charset-carrying one.
            return [(collation, charset)]
        captured.append(sql)
        return []

    gateway._execute = fake_execute
    gateway._observe_rss = lambda: None
    return gateway, captured


def test_candidate_literals_are_converted_to_the_column_charset():
    """A utf8mb3 column under a utf8mb4 connection must not yield a bare `%s COLLATE`."""
    gateway, captured = _gateway_capturing_grouping_sql("utf8mb3_unicode_ci", "utf8mb3")

    gateway.resolve_title_collation_classes([
        TitleCollationRequest(
            target_index=0, attribute_index=0, phase="create",
            sample_type_id=1, title="TEST-222",
        )
    ])

    assert captured, "no grouping query was issued"
    sql = captured[0]

    # The literal side must be converted into the column's charset. Without this MySQL
    # raises 1253 whenever the column charset differs from the connection charset.
    assert "CONVERT(%s USING utf8mb3)" in sql, (
        "candidate literals are still bound in the connection charset; on production "
        f"this is the 1253 that 500s every mutation. SQL was: {sql}"
    )

    # The column side needs no conversion: the column is already in that charset.
    assert "title COLLATE utf8mb3_unicode_ci" in sql, (
        f"the stored-title side should compare in the column's own collation. SQL was: {sql}"
    )
