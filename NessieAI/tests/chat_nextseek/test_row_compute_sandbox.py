"""Model-written row code runs in a separate, limited process: it gets the rows and nothing else."""
from __future__ import annotations

import ast
import inspect
import threading
import time

import pytest

from chat_nextseek.helpers.tools import row_compute
from chat_nextseek.helpers.tools.memory_code import MemoryCodeSafetyError, _validate_memory_code
from chat_nextseek.helpers.tools.row_compute import run_code_isolated

ROWS = [{"uuid": f"D.SEQ-{i}{'SHA' if i % 7 else 'XYZ'}", "type": "D.SEQ"} for i in range(731)]
DATA = {"data": {"rows": ROWS}}


def _in_thread(fn):
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("out", fn()))
    t.start()
    t.join(30)
    return box["out"]


def test_a_plain_computation_returns_its_result():
    out = run_code_isolated("result = {'count': len([r for r in rows if 'SHA' in r.get('uuid', '')])}", DATA)
    assert out["ok"] is True and out["result"] == {"count": 626} and out["error"] is None


@pytest.mark.parametrize("code", [
    "result = {'x': str(re.purge)}",
    "r = re\nresult = {'x': str(r.purge)}",
    "result = {'x': rows.extra}",
    "json.extra = 1\nresult = {}",
    "result = {'x': rows._x}",
    "import os\nresult = {}",
    "result = {'x': open('/etc/hostname')}",
])
def test_code_outside_the_allowed_subset_is_refused_before_it_runs(code):
    with pytest.raises(MemoryCodeSafetyError):
        _validate_memory_code(ast.parse(code))
    out = run_code_isolated(code, DATA)
    assert out["ok"] is False and "Disallowed" in out["error"]


def test_re_flags_and_the_allowed_calls_still_work():
    code = "result = {'n': len([r for r in rows if re.search('sha', r['uuid'], re.I)]), 'j': json.dumps([1])}"
    _validate_memory_code(ast.parse(code))
    assert run_code_isolated(code, DATA)["result"] == {"n": 626, "j": "[1]"}


def test_a_runaway_loop_stops_off_the_main_thread():
    """Chat turns run in a worker thread."""
    code = "n = 0\nfor i in range(10**12):\n    n += 1\nresult = {'n': n}"
    t0 = time.monotonic()
    out = _in_thread(lambda: run_code_isolated(code, DATA, cpu_s=1, wall_s=3))
    assert out["ok"] is False and "time" in out["error"].lower()
    assert time.monotonic() - t0 < 8


def test_the_wall_clock_limit_holds_off_the_main_thread_on_its_own():
    """The process is ended at wall_s even when its CPU allowance is longer."""
    code = "n = 0\nfor i in range(10**12):\n    n += 1\nresult = {'n': n}"
    t0 = time.monotonic()
    out = _in_thread(lambda: run_code_isolated(code, DATA, cpu_s=60, wall_s=2))
    assert out["ok"] is False and "time" in out["error"].lower()
    assert time.monotonic() - t0 < 8


def test_a_memory_blowup_is_an_error_not_an_outage():
    out = run_code_isolated("x = 'x' * (2 * 10**9)\nresult = {'n': len(x)}", DATA, mem_mb=256)
    assert out["ok"] is False and "memory" in out["error"].lower()


def test_the_child_gets_no_environment_and_no_site_packages(monkeypatch):
    monkeypatch.setenv("ROW_COMPUTE_CANARY", "x")
    seen: dict = {}
    real = row_compute.subprocess.run

    def spy(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return real(cmd, **kw)

    monkeypatch.setattr(row_compute.subprocess, "run", spy)
    assert run_code_isolated("result = {}", DATA)["ok"] is True
    assert seen["env"] == {"LANG": "C.UTF-8"}
    assert {"-I", "-S", "-B"} <= set(seen["cmd"])


def test_the_callers_rows_are_not_changed():
    data = {"data": {"rows": [{"a": 1}]}}
    run_code_isolated("rows.append({'a': 2})\nresult = {'n': len(rows)}", data)
    assert data == {"data": {"rows": [{"a": 1}]}}


def test_the_two_existing_callers_use_the_isolated_runner():
    from chat_nextseek.agents import memory
    from chat_nextseek.agents.planner import tools as planner_tools
    for fn in (memory.memory_agent_answer, planner_tools._plan_tool_coding_filter):
        src = inspect.getsource(fn)
        assert "run_code_isolated(" in src and "execute_memory_code(" not in src


@pytest.mark.parametrize("code", [
    "class Holder:\n    pass\nresult = {}",
    "first, *rest = [1, 2, 3]\nresult = {'n': first}",
    "n: int = 1\nresult = {'n': n}",
    "result = {'n': (m := 1)}",
    "assert rows\nresult = {}",
])
def test_syntax_the_allow_list_does_not_name_is_refused(code):
    with pytest.raises(MemoryCodeSafetyError, match="Disallowed syntax"):
        _validate_memory_code(ast.parse(code))
    assert run_code_isolated(code, DATA)["ok"] is False


def test_a_result_too_large_to_return_is_refused_with_its_size(monkeypatch):
    monkeypatch.setattr(row_compute, "REPLY_MAX_BYTES", 2000)
    out = run_code_isolated("result = {'s': 'x' * 5000}", DATA)
    assert out["ok"] is False and "too large to return" in out["error"] and "2,000" in out["error"]
    assert out["result_bytes"] > 5000


def test_data_too_large_to_hand_over_starts_no_process(monkeypatch):
    started = []
    monkeypatch.setattr(row_compute.subprocess, "run", lambda *a, **k: started.append(a))
    out = run_code_isolated("result = {}", {"data": {"rows": [{"note": "x" * (17 << 20)}]}})
    assert out["ok"] is False and "too large" in out["error"] and started == []


@pytest.mark.parametrize("code", [
    "_hidden = 1\nresult = {'n': _hidden}",
    "for _ in range(2):\n    pass\nresult = {}",
])
def test_a_name_that_starts_with_an_underscore_is_refused(code):
    with pytest.raises(MemoryCodeSafetyError, match="Disallowed name"):
        _validate_memory_code(ast.parse(code))
    assert run_code_isolated(code, DATA)["ok"] is False
