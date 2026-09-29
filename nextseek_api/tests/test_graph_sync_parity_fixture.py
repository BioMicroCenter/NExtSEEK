"""The frozen batch upload outputs equal what ``batch_upload/neo4j_sync.py`` returns.

``fixtures/graph_sync_batch_upload_parity.json`` holds three outputs of neo4j_sync on the fixture worlds the graph_sync
parity tests build, so those tests can compare graph_sync's rules with batch upload's former rule after neo4j_sync is
deleted:

- ``edge_labels``: ``build_derived_from_payloads_from_db`` on the MySQL world of ``test_graph_sync_labels.py``, the
  14 edges and their seven label keys (read by its ``TestParityWithBatchUpload``);
- ``parent_lists``: ``enrich_parent_titles`` on each case of ``test_graph_sync_projection.py``'s ``_ENRICH_FIXTURES``,
  ``[titles, hashes]``;
- ``resolved_internal_assays``: ``_resolve_internal_assays`` on ``test_graph_sync_sources.py``'s
  ``RESOLVER_JUNCTION``, SEEK assay id to ``[internal id, title]``.

This module recomputes all three and asserts they equal the file. It goes with neo4j_sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nextseek_api.batch_upload.models import InputRowModel, NodeRow
from nextseek_api.batch_upload.neo4j_sync import _resolve_internal_assays, enrich_parent_titles
from nextseek_api.tests import test_graph_sync_labels as labels_world
from nextseek_api.tests import test_graph_sync_projection as projection_world
from nextseek_api.tests import test_graph_sync_sources as sources_world

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_sync_batch_upload_parity.json"


def _frozen() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _enrich(child_meta, batch, external):
    node = NodeRow(sample_id=100, sample_uuid="NHP-260225MIT-50", sample_type="Blood", properties=dict(child_meta))
    models = [InputRowModel(UID="NHP-260225MIT-50", SampleType="Blood", json_metadata=json.dumps(child_meta))]
    models += [InputRowModel(UID=uid, SampleType=st, json_metadata=json.dumps(meta)) for uid, st, meta in batch]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(uuid, json.dumps(meta)) for uuid, meta in external]
    enrich_parent_titles([node], models, sql_conn=conn)
    return [node.parent_titles, node.parent_title_hashes]


def test_the_edge_labels_are_batch_uploads():
    edges = labels_world._batch_upload_labels()
    assert len(edges) == 14
    assert _frozen()["edge_labels"] == [{"child_id": c, "parent_id": p, "labels": edges[(c, p)]}
                                        for c, p in sorted(edges)]


@pytest.mark.parametrize("name", sorted(projection_world._ENRICH_FIXTURES))
def test_the_parent_lists_are_batch_uploads(name):
    assert _frozen()["parent_lists"][name] == _enrich(*projection_world._ENRICH_FIXTURES[name])


def test_every_enrich_case_is_frozen():
    assert sorted(_frozen()["parent_lists"]) == sorted(projection_world._ENRICH_FIXTURES)


def test_the_resolved_internal_assays_are_batch_uploads():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = sources_world.RESOLVER_JUNCTION
    resolved = _resolve_internal_assays(set(sources_world.RESOLVER_ASSAY_IDS), conn)
    assert _frozen()["resolved_internal_assays"] == {str(k): list(v) for k, v in sorted(resolved.items())}
