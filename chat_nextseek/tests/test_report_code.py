import pytest

from chat_nextseek.helpers.tools.report_code import (
    execute_report_code,
    ReportCodeSafetyError,
    ReportCodeTimeoutError,
)

SAMPLE_DATA = {
    "data": {
        "data": [
            {"sample_type": "D.SEQ", "samples": [
                {"metadata": {"UID": "D.SEQ-1", "Notes": "Sex: M; Treatment: NDMA"}},
                {"metadata": {"UID": "D.SEQ-2", "Notes": "Sex: F; Treatment: Saline"}},
            ]},
        ]
    }
}


def test_allows_helper_functions_and_builds_rows():
    code = (
        "def kv(note):\n"
        "    out = {}\n"
        "    for part in (note or '').split(';'):\n"
        "        if ':' in part:\n"
        "            k, v = part.split(':', 1)\n"
        "            out[k.strip()] = v.strip()\n"
        "    return out\n"
        "rows = []\n"
        "for group in data['data']['data']:\n"
        "    if group.get('sample_type') == 'D.SEQ':\n"
        "        for s in group.get('samples', []):\n"
        "            md = s.get('metadata') or {}\n"
        "            parsed = kv(md.get('Notes'))\n"
        "            rows.append({'*library name': md.get('UID'), 'treatment': parsed.get('Treatment')})\n"
        "result = {'samples': rows}\n"
    )
    out = execute_report_code(code, SAMPLE_DATA)
    assert len(out["samples"]) == 2
    assert out["samples"][0] == {"*library name": "D.SEQ-1", "treatment": "NDMA"}


def test_blocks_import():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("import os\nresult = {}", SAMPLE_DATA)


def test_blocks_open_and_eval():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("result = open('/etc/passwd').read()", SAMPLE_DATA)
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("result = eval('1+1')", SAMPLE_DATA)


def test_blocks_dunder_access():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("result = {}.__class__", SAMPLE_DATA)


def test_allows_terminating_while_for_lineage_walk():
    # report code needs `while` to walk a lineage parent-chain; a terminating
    # loop must run and produce output.
    code = (
        "by_uid = {}\n"
        "for g in data['data']['data']:\n"
        "    for s in g.get('samples', []):\n"
        "        md = s.get('metadata') or {}\n"
        "        by_uid[md.get('UID')] = md\n"
        "uids = []\n"
        "cur = 'D.SEQ-1'\n"
        "while cur and cur in by_uid:\n"
        "    uids.append(cur)\n"
        "    cur = (by_uid.get(cur) or {}).get('Parent')\n"
        "result = {'samples': uids}\n"
    )
    out = execute_report_code(code, SAMPLE_DATA)
    assert out["samples"] == ["D.SEQ-1"]


def test_while_loop_is_time_bounded():
    # An accidental infinite loop must be cut by the execution timeout, not hang.
    with pytest.raises(ReportCodeTimeoutError):
        execute_report_code("while True:\n    pass\nresult = {}", SAMPLE_DATA, timeout_seconds=1)


def test_requires_result_assignment():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("x = 1", SAMPLE_DATA)


def test_blocks_frame_walking_via_generator_expression():
    code = (
        "holder = []\n"
        "g = (holder[0].gi_frame.f_back for _ in range(1))\n"
        "holder.append(g)\n"
        "result = {'v': 1}\n"
    )
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code(code, SAMPLE_DATA)


def test_blocks_frame_internal_attribute_reads():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("x = [].append\nresult = {'v': x.__self__}", SAMPLE_DATA)
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("def f():\n    return f\nresult = {'v': f().f_globals}", SAMPLE_DATA)


def test_blocks_attribute_read_as_argument():
    # os.system-style: attribute read passed as an argument must be rejected
    code = (
        "def grab(x):\n"
        "    return x\n"
        "result = {'v': grab(data.fromkeys)}\n"
    )
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code(code, SAMPLE_DATA)


def test_blocks_generator_expression():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("x = list(z for z in range(3))\nresult = {}", SAMPLE_DATA)


def test_rejects_helper_shadowing_builtin():
    code = "def sorted():\n    return 1\nresult = {'v': sorted()}\n"
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code(code, SAMPLE_DATA)


def test_blocks_builtins_name_reference():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("__builtins__.pop('len', None)\nresult = {}", SAMPLE_DATA)
