import fcntl
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from chat_nextseek.helpers.tools import report_code, row_compute
from chat_nextseek.helpers.tools.report_code import (
    execute_report_code,
    ReportCodeError,
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


def test_a_runaway_loop_stops_off_the_main_thread():
    """Report code can run off the main thread; its time limit holds there too."""
    box: dict = {}

    def run():
        try:
            execute_report_code("while True:\n    pass\nresult = {}", SAMPLE_DATA, timeout_seconds=1)
        except Exception as exc:  # the assertion below reads what was raised
            box["exc"] = exc

    t0 = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(30)
    assert isinstance(box.get("exc"), ReportCodeTimeoutError)
    assert time.monotonic() - t0 < 10


def test_report_code_runs_in_a_separate_limited_process(monkeypatch):
    monkeypatch.setenv("REPORT_CODE_CANARY", "x")
    seen: dict = {}
    real = row_compute.subprocess.run

    def spy(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return real(cmd, **kw)

    monkeypatch.setattr(row_compute.subprocess, "run", spy)
    out = execute_report_code("result = {'n': len(data['data']['data'][0]['samples'])}", SAMPLE_DATA)
    assert out == {"n": 2}
    assert seen["env"] == {"LANG": "C.UTF-8"}
    assert "-I" in seen["cmd"]


def test_the_callers_metadata_is_not_changed():
    data = {"data": {"data": [{"sample_type": "D.SEQ", "samples": []}]}}
    execute_report_code("data['data']['data'].append({'x': 1})\nresult = {}", data)
    assert data == {"data": {"data": [{"sample_type": "D.SEQ", "samples": []}]}}


def test_a_report_that_uses_too_much_memory_is_an_error():
    with pytest.raises(Exception) as err:
        execute_report_code("x = 'x' * (8 * 10**9)\nresult = {'n': len(x)}", SAMPLE_DATA)
    assert "memory" in str(err.value).lower()


def test_a_report_body_over_its_limit_is_an_error_that_states_its_size(monkeypatch):
    monkeypatch.setattr(report_code, "REPORT_REPLY_MAX_BYTES", 2000)
    with pytest.raises(ReportCodeError, match="too large to return"):
        execute_report_code("result = {'samples': ['x' * 5000]}", SAMPLE_DATA)


def test_metadata_over_the_input_limit_starts_no_process(monkeypatch):
    monkeypatch.setattr(report_code, "REPORT_INPUT_MAX_BYTES", 100)
    started = []
    monkeypatch.setattr(row_compute.subprocess, "run", lambda *a, **k: started.append(a))
    with pytest.raises(ReportCodeError, match="too large"):
        execute_report_code("result = {}", SAMPLE_DATA)
    assert started == []


def test_the_input_limit_fits_in_the_process_memory():
    assert report_code.REPORT_INPUT_MAX_BYTES * 5 <= report_code.REPORT_MEM_MB << 20


@contextmanager
def held_report_lock(path):
    """The report lock, held here as another server process would hold it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def test_a_second_report_waits_briefly_then_does_not_run(monkeypatch, tmp_path):
    monkeypatch.setattr(report_code, "REPORT_LOCK_PATH", str(tmp_path / "report.lock"))
    monkeypatch.setattr(report_code, "REPORT_WAIT_S", 0.1)
    started = []
    monkeypatch.setattr(row_compute.subprocess, "run", lambda *a, **k: started.append(a))
    with held_report_lock(report_code.REPORT_LOCK_PATH):
        t0 = time.monotonic()
        with pytest.raises(ReportCodeError, match="already running"):
            execute_report_code("result = {}", SAMPLE_DATA)
        assert time.monotonic() - t0 < 2 and started == []


def test_the_report_lock_holds_across_processes(monkeypatch, tmp_path):
    """The server runs several worker processes: a report in one keeps a report in another from starting."""
    lock = str(tmp_path / "report.lock")
    monkeypatch.setattr(report_code, "REPORT_LOCK_PATH", lock)
    monkeypatch.setattr(report_code, "REPORT_WAIT_S", 0.1)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import fcntl, os, sys, time\n"
         "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('held', flush=True)\ntime.sleep(30)\n", lock],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(ReportCodeError, match="already running"):
            execute_report_code("result = {}", SAMPLE_DATA)
    finally:
        holder.kill()
        holder.wait()
    assert execute_report_code("result = {'n': 1}", SAMPLE_DATA) == {"n": 1}, "free again once the holder is gone"
