"""§4.A: NSTurnContext — pure bundle projection, orjson+pydantic, no LLM."""
import sys

import orjson
import pytest

from NessieAI import paths
from NessieAI.cc import ns_turn_context as ntc


def _bundle(bid=1, rows=None, total=222):
    rows = rows if rows is not None else [
        {"uid": "MUS-1", "genotype": "WT"}, {"uid": "MUS-2", "genotype": "KO"}]
    return {"id": bid, "timestamp": "2026-07-22T00:00:00", "mode": "search",
            "user_query": "mice treated with NDMA",
            "terminal_reply": "found 222", "reply": "found 222",
            "endpoint": "/admin/samples/retrieve", "method": "GET",
            "parser_plan": {"filters": {"treatment": "NDMA"}},
            "api_result_full": {"ok": True, "data": {"total": total, "rows": rows}}}


def test_from_bundle_field_mapping():
    c = ntc.from_bundle(_bundle(), session_id="sid", turn_id=1)
    assert (c.schema_version, c.session_id, c.turn_id, c.bundle_id) == ("nsctx/v1", "sid", 1, 1)
    assert c.route == "ns" and c.ts == "2026-07-22T00:00:00" and c.mode == "search"
    assert c.reply == "found 222" and c.ok is True and c.error is None
    assert c.result.endpoint == "/admin/samples/retrieve" and c.result.method == "GET"
    assert c.result.total == 222 and c.result.row_count == 2
    assert c.result.truncated is True                    # 2 < 222
    assert c.result.columns == ["uid", "genotype"]
    assert c.result.sample_uids == ["MUS-1", "MUS-2"]
    assert c.filters == {"treatment": "NDMA"}
    assert c.full_result_available is True


def test_reply_cap_and_flag():
    b = _bundle()
    b["terminal_reply"] = "x" * 3000
    c = ntc.from_bundle(b, session_id="s", turn_id=1)
    assert len(c.reply) == 2000 and c.reply_truncated is True


def test_sample_uids_capped_at_20():
    rows = [{"uid": f"U{i}"} for i in range(50)]
    c = ntc.from_bundle(_bundle(rows=rows, total=50), session_id="s", turn_id=1)
    assert len(c.result.sample_uids) == 20


def test_error_bundle():
    b = _bundle()
    b["api_result_full"] = {"ok": False, "error": "upstream 500"}
    c = ntc.from_bundle(b, session_id="s", turn_id=1)
    assert c.ok is False and c.error == "upstream 500"
    assert c.full_result_available is False


def test_build_contexts_joins_turn_ids_and_skips_non_ns():
    chat_log = [
        {"turn_id": 1, "mode": "search", "user_query": "q", "assistant_reply": "a",
         "bundle_id": 1},
        {"turn_id": 2, "mode": "cc", "user_query": "q", "assistant_reply": "a",
         "cc_run_id": "u"},
        {"turn_id": 3, "mode": "unrelated", "user_query": "q",
         "router_choice": "unrelated", "status": "completed"},
        {"turn_id": 4, "mode": "search", "user_query": "q", "assistant_reply": "a",
         "bundle_id": 2},
    ]
    ctxs = ntc.build_contexts(chat_log, [_bundle(1), _bundle(2)], session_id="s")
    assert [(c.turn_id, c.bundle_id) for c in ctxs] == [(1, 1), (4, 2)]


def test_build_contexts_skips_malformed_bundle():
    ctxs = ntc.build_contexts(
        [{"turn_id": 1, "mode": "search", "user_query": "q",
          "assistant_reply": "a", "bundle_id": 1}],
        ["not-a-dict"], session_id="s")
    assert ctxs == []


def test_orjson_bulk_validation_roundtrip():
    c = ntc.from_bundle(_bundle(), session_id="s", turn_id=1)
    raw = orjson.dumps([c.model_dump()])
    validated = ntc.NSTurnContextList.validate_python(orjson.loads(raw))
    assert validated[0] == c


@pytest.mark.parametrize("api_full,exp_total,exp_rows", [
    ({"data": {"total": 5, "rows": [{"uid": "a"}]}}, 5, 1),          # wrapped rows
    ({"data": {"total_nodes": 7, "nodes": [{"uid": "a"}, {"uid": "b"}]}}, 7, 2),
    ({"data": {"total_samples": 3, "data": [{"uid": "a"}]}}, 3, 1),  # nested data-list
    ({"total": 9, "rows": [{"uid": "a"}]}, 9, 1),                    # raw shape
    ({"data": {"total": 40}}, 40, 0),                                # AR-17: total, no rows
    ({"data": {}}, None, 0),
    ({}, None, 0),
])
def test_total_and_rows_shapes(api_full, exp_total, exp_rows):
    total, rows, _ = ntc._total_and_rows(api_full)
    assert (total, rows) == (exp_total, exp_rows)


def test_total_and_rows_parity_with_chat_nextseek_helper():
    """The mirror must agree with the real extractor on every shape above."""
    import importlib.util
    import types

    src_root = paths.CHAT_NEXTSEEK_DIR / "src"
    sys.path.insert(0, str(src_root))
    for mod_name in (
        "chat_nextseek",
        "chat_nextseek.config",
        "chat_nextseek.session",
        "chat_nextseek.helpers",
        "chat_nextseek.helpers.tools",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)
    sys.modules["chat_nextseek.config"].ChatConfig = object
    sys.modules["chat_nextseek.session"].SessionState = object
    api_path = src_root / "chat_nextseek" / "helpers" / "tools" / "nextseek_api.py"
    spec = importlib.util.spec_from_file_location(
        "chat_nextseek.helpers.tools.nextseek_api", api_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _extract_total_and_rows = mod._extract_total_and_rows
    shapes = [
        {"data": {"total": 5, "rows": [{"uid": "a"}]}},
        {"data": {"total_nodes": 7, "nodes": [{"uid": "a"}, {"uid": "b"}]}},
        {"data": {"total_samples": 3, "data": [{"uid": "a"}]}},
        {"total": 9, "rows": [{"uid": "a"}]},
        {"data": {"total": 40}},
        {"data": {}},
        {},
    ]
    for s in shapes:
        t1, r1 = _extract_total_and_rows(s)
        t2, r2, _ = ntc._total_and_rows(s)
        assert (t1, r1) == (t2, r2), s


# ---------------------------------------------------- graph turns (CC-RERUN-FINDINGS fix 6)
def _graph_bundle(bid=7, n=585, total=None, truncated=False):
    rows = [{"id": i, "uuid": f"TCGA-{i:04d}", "type": "D.SEQ"} for i in range(n)]
    return {"id": bid, "timestamp": "2026-09-23T00:00:00", "mode": "graph_query",
            "user_query": "LUAD samples", "terminal_reply": f"{n} samples",
            "endpoint": "neo4j", "method": None,
            "parser_plan": {"mode": "graph_query", "filters": {"study": "LUAD"}},
            "graph_plan": {"cypher": "MATCH (s:T_D_SEQ) RETURN s.id AS id, s.uuid AS uuid"},
            "graph_result": {"ok": True, "count": n, "total": n if total is None else total,
                             "truncated": truncated, "data": rows}}


def test_a_graph_turn_reads_its_rows_from_the_graph_result():
    """The digest said rows=0 for every graph turn (it read only the REST result), and
    ``sample_uids`` looked for ``uid`` where graph rows carry ``uuid`` (r6-1228)."""
    c = ntc.from_bundle(_graph_bundle(), session_id="s", turn_id=2)
    assert c.ok is True and c.error is None
    assert c.result.row_count == 585 and c.result.total == 585
    assert c.result.truncated is False
    assert c.result.columns == ["id", "uuid", "type"]
    assert c.result.sample_uids == [f"TCGA-{i:04d}" for i in range(20)]
    assert c.result.endpoint == "neo4j"
    assert c.full_result_available is True


def test_a_capped_graph_turn_is_truncated():
    c = ntc.from_bundle(_graph_bundle(n=50, total=6000, truncated=True), session_id="s", turn_id=2)
    assert (c.result.row_count, c.result.total, c.result.truncated) == (50, 6000, True)


def test_a_failed_graph_turn_is_not_ok():
    b = _graph_bundle(n=0)
    b["graph_result"] = {"ok": False, "error": "refused", "data": []}
    c = ntc.from_bundle(b, session_id="s", turn_id=2)
    assert c.ok is False and c.error == "refused" and c.full_result_available is False


def test_uid_keys_are_read_in_either_spelling():
    rows = [{"uid": "A"}, {"uuid": "B"}, {"UID": "C"}, {"name": "no uid"}]
    c = ntc.from_bundle(_bundle(rows=rows, total=4), session_id="s", turn_id=1)
    assert c.result.sample_uids == ["A", "B", "C"]
