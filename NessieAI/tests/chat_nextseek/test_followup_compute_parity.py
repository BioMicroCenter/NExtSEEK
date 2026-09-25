"""Parity: each thing the stored-result answer (memory_agent_answer) could do has a test that passes on the
follow-up loop's compute tool. Task 21 removes that path only when this file passes. Rows are synthetic, shaped like
the follow-up each test is named for (<run>-<task>); the two REAL_CODE programs are the only memory-coder programs in
the pulled runs, verbatim."""
from __future__ import annotations

import json

from chat_nextseek.agents.followup import _stored_rows, resolve_followup_outcome
from chat_nextseek.agents.followup_compute import compute_over_rows
from chat_nextseek.graph_review import review_compute

REAL_CODE_N0914_1142 = """matched_sample_types = set()
for row in rows:
    md = row.get("json_metadata") or {}
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except Exception:
            md = {}
    
    # Filter by Type == 'Illumina Library' if present, otherwise fallback to all rows in this result set
    t = (md.get("Type") or "").strip().upper()
    if t == "ILLUMINA LIBRARY" or not t:
        st = row.get("sample_type")
        if st:
            matched_sample_types.add(st)

if not matched_sample_types:
    for row in rows:
        st = row.get("sample_type")
        if st:
            matched_sample_types.add(st)

st_list = sorted(list(matched_sample_types))
if len(st_list) == 1:
    ans = f"The sample type for those samples is {st_list[0]}."
else:
    ans = f"The sample types for those samples are: {', '.join(st_list)}."

result = {
    "answer": ans,
    "sample_types": st_list,
    "total_rows_analyzed": len(rows)
}"""
REAL_CODE_N0914_1175 = """matched_samples = []
all_items = rows if rows else data.get("neo4j_output", {}).get("data_preview", [])

for row in all_items:
    uid = strip_html(row.get("uuid") or row.get("uid") or row.get("title") or str(row.get("id") or ""))
    if not uid:
        continue
    
    found = False
    for k, v in row.items():
        if isinstance(v, str):
            v_lower = v.lower()
            if "cd8" in v_lower and "deplet" in v_lower:
                found = True
                break
            if v.startswith("{") or v.startswith("["):
                try:
                    parsed = json.loads(v)
                    parsed_str = json.dumps(parsed).lower()
                    if "cd8" in parsed_str and "deplet" in parsed_str:
                        found = True
                        break
                except Exception:
                    pass
        elif isinstance(v, (dict, list)):
            v_str = json.dumps(v).lower()
            if "cd8" in v_str and "deplet" in v_str:
                found = True
                break
                
    if found:
        matched_samples.append(uid)

result = {
    "answer": f"Found {len(matched_samples)} CD8-depleted NHP samples." if matched_samples else "No CD8-depleted NHP samples found in the current results.",
    "count": len(matched_samples),
    "examples": matched_samples[:5],
    "matched_uids": matched_samples
}"""


def _run(rows, *, total=None, complete=True, question="q", target=1, newest=1, **call):
    total = len(rows) if total is None else total
    payload = compute_over_rows(rows=rows, total=total, complete=complete, where=call.get("where"),
                                group_by=call.get("group_by"), code=call.get("code"))
    review = review_compute(question=question, source_kind="stored", source_total=total,
                            target_bundle_id=target, newest_bundle_id=newest, payload=payload)
    return payload, {c.name for c in review.checks if c.fired}, review


def test_p1_r4_606_filter_then_count_over_a_complete_stored_set():
    rows = [{"uuid": f"D.SEQ-{i}{'SHA' if i % 7 else 'XYZ'}", "type": "D.SEQ"} for i in range(731)]
    p, fired, _ = _run(rows, question="Which of those sequencing samples have SHA in their UID?",
                       where=[{"column": "uuid", "op": "contains", "value": "SHA"}])
    assert p["count"] == 626 and fired == set()


def test_p2_n0914_1142_distinct_values_over_rest_rows():
    rows = [{"uid": f"LIB-{i}", "sample_type": "DNA",
             "json_metadata": json.dumps({"Type": "Illumina Library"}) if i % 2 else {"Type": "Illumina Library"}}
            for i in range(1000)]
    p, _, _ = _run(rows, question="What sample type were those?", group_by=["sample_type"])
    assert p["groups"] == [{"sample_type": "DNA", "n": 1000}]
    p, _, _ = _run(rows, question="What sample type were those?", code=REAL_CODE_N0914_1142)
    assert p["ok"] is True and p["result"]["sample_types"] == ["DNA"]


def test_p3_r1_579_breakdown_over_stored_rows():
    species = ["Macaca mulatta"] * 47 + ["Macaca fascicularis"] * 22 + ["Macaca nemestrina"] * 4
    p, _, _ = _run([{"uuid": f"NHP-{i}", "Species": s} for i, s in enumerate(species)],
                   question="Which species are among those?", group_by=["Species"])
    assert p["groups"] == [{"Species": "Macaca mulatta", "n": 47}, {"Species": "Macaca fascicularis", "n": 22},
                           {"Species": "Macaca nemestrina", "n": 4}]


def test_p4_r1_581_a_recorded_value_and_an_older_referent():
    rows = [{"uuid": f"CEL-{i}", "Treatment": "DMSO" if i < 3 else ""} for i in range(4)]
    p, fired, _ = _run(rows, question="Of those, how many have a treatment recorded?", target=1, newest=3,
                       where=[{"column": "Treatment", "op": "present"}])
    assert p["count"] == 3 and "binding" in fired


def test_p5_a0922_532_every_row_of_the_loops_own_query():
    rows = [{"type": f"T{i}", "n": 100 - i} for i in range(60)]
    p = compute_over_rows(rows=rows, total=60, complete=True, where=None, group_by=None,
                          code="result = {'types': len(rows), 'largest': max([r['n'] for r in rows])}")
    assert p["result"] == {"types": 60, "largest": 100}


def test_p6_n0914_1175_a_column_the_rows_lack_is_not_a_zero():
    rows = [{"uuid": f"NHP-{i}", "type": "NHP", "Species": "Macaca mulatta"} for i in range(60)]
    p, _, _ = _run(rows, question="Which of those monkeys are depleted of CD8?",
                   where=[{"column": "CD8Depletion", "op": "present"}])
    assert p["ok"] is False and p["needs_query"] is True
    p, fired, review = _run(rows, question="Which of those monkeys are depleted of CD8?", code=REAL_CODE_N0914_1175)
    assert p["ok"] is True and p["result"]["count"] == 0
    assert "snapshot_zero" in fired and review.verdict == "note"


def test_p7_a_capped_seed_is_never_counted_from_its_rows():
    rows = [{"uuid": f"TIS-{i}", "Tissue": "liver" if i % 3 == 0 else "lung"} for i in range(5000)]
    p, _, _ = _run(rows, total=36622, complete=False, question="how many from the liver",
                   where=[{"column": "Tissue", "op": "equals", "value": "liver"}])
    assert p["ok"] is False and p["needs_query"] is True and "count" not in p


def test_p8_r4_625_the_users_number_is_checked():
    rows = [{"uuid": f"MUS-{i}", "Genotype": f"CC0{i % 23:02d}"} for i in range(745)]
    _, fired, review = _run(rows, group_by=["Genotype"],
                            question="how many of these 1,206 mouse sample records have transcriptomic data?")
    assert "premise" in fired and "745" in review.disclosure


def test_p9_r3_602_shape_a_negated_value_is_disclosed():
    rows = [{"uuid": f"PAT-{i}", "Classification": c}
            for i, c in enumerate(["Non-converter"] * 57 + ["Converter"] * 32 + ["Reverter"] * 9)]
    p, fired, review = _run(rows, question="Of those, how many converted?",
                            where=[{"column": "Classification", "op": "contains", "value": "convert"}])
    assert p["count"] == 89 and "negated_value" in fired and "Non-converter 57" in review.disclosure


def test_p10_r6_1228_shape_matched_values_are_disclosed():
    statuses = (["Current smoker"] * 122 + ["Current reformed smoker for > 15 years"] * 311
                + ["Lifelong non-smoker"] * 152)
    p, fired, review = _run([{"uuid": f"PAT-{i}", "TobaccoSmokingStatus": s} for i, s in enumerate(statuses)],
                            question="Of those, how many are current smokers?",
                            where=[{"column": "TobaccoSmokingStatus", "op": "contains", "value": "current"}])
    assert p["count"] == 433 and "value_split" in fired and "Current smoker 122" in review.disclosure


def test_p11_a_planner_bundles_rows_are_reachable():
    bundle = {"id": 4, "step_results": {1: {"ok": True, "output": {"data": [{"uid": "A"}, {"uid": "B"}], "count": 2}}}}
    assert _stored_rows(bundle) == [{"uid": "A"}, {"uid": "B"}]


def test_p12_until_task_21_an_unsupported_loop_still_reaches_the_stored_path():
    assert resolve_followup_outcome({"unsupported": True}) == (None, True)
