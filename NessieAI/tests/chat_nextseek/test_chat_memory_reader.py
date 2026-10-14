"""The shared bundle reader in chat_memory: grouped retrieve bodies, data.data lists, one UID rule."""
from chat_nextseek.chat_memory import bundle_rows_and_extent, bundle_uids, named_bundle, row_uid


def _retrieve(groups, total):
    return {"api_result_full": {"data": {"total_samples": total, "data": groups}}}


def test_a_retrieve_body_reads_samples_not_groups():
    b = _retrieve([{"sample_type": "A", "samples": [{"uuid": "A-1-PUB"}, {"uuid": "A-2-PUB"}]},
                   {"sample_type": "B", "samples": [{"uuid": "B-1-PUB"}]}], 3)
    rows, total, capped, _ = bundle_rows_and_extent(b)
    assert len(rows) == 3 and total == 3 and not capped
    assert sorted(bundle_uids(b)) == ["A-1-PUB", "A-2-PUB", "B-1-PUB"]


def test_a_list_under_data_data_is_the_rows():
    b = {"api_result_full": {"data": {"data": [{"uid": "D.SEQ-1-PUB"}]}}}
    assert bundle_rows_and_extent(b)[0] == [{"uid": "D.SEQ-1-PUB"}]


def test_row_uid_rule():
    assert row_uid({"uid": "<a>X-1-PUB</a>"}) == "X-1-PUB"
    assert row_uid({"s.uuid": "X-2-PUB"}) == "X-2-PUB"
    assert row_uid({"sample_uid": "X-3-PUB"}) == "X-3-PUB"
    assert row_uid({"s": {"uuid": "X-4-PUB"}}) == "X-4-PUB"
    # a SEEK id and a breakdown label are not UIDs
    assert row_uid({"id": "17", "name": "RNA-seq"}) == ""
    assert row_uid({"label": "X-5-PUB"}) == ""


def test_named_bundle():
    h = [{"id": 1}, {"id": 2}]
    assert named_bundle(h, 1) == {"id": 1}
    assert named_bundle(h, 9) is None and named_bundle(h, None) is None and named_bundle(h, True) is None


def test_chat_log_summary_reads_a_retrieve_body():
    from chat_nextseek.chat_memory import _extract_count, _extract_first_uids

    payload = {"data": {"total_samples": 3, "data": [{"sample_type": "A", "samples": [{"uuid": "A-1-PUB"}]}]}}
    assert _extract_count(payload) == 3
    assert _extract_first_uids(payload) == ["A-1-PUB"]


def test_recent_results_total_is_the_plans_final_step():
    from chat_nextseek.helpers.tools.nextseek_api import _bundle_total

    plan = {"step_results": {1: {"ok": True, "output": {"data": [{}] * 5, "count": 5}},
                             2: {"ok": True, "output": {"data": [{}] * 2, "count": 2}}},
            "graph_result": {"ok": True, "total": 5, "data": [{}] * 5}}
    assert _bundle_total(plan)[0] == 2
