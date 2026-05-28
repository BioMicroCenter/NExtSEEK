import pytest

from chat_nextseek.helpers.tools.report_code import (
    execute_report_code,
    ReportCodeSafetyError,
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


def test_blocks_while():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("while True:\n    pass\nresult = {}", SAMPLE_DATA)


def test_requires_result_assignment():
    with pytest.raises(ReportCodeSafetyError):
        execute_report_code("x = 1", SAMPLE_DATA)
