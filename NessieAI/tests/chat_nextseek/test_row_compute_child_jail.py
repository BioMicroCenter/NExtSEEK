"""row_compute_child._lock_down: no root, no new fds (so no sockets or files), checked in a fresh process."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHILD = (Path(__file__).resolve().parents[2] / "chat_nextseek/src/chat_nextseek/helpers/tools/row_compute_child.py")

PROBE = r"""
import importlib.util, json, os, socket, sys
spec = importlib.util.spec_from_file_location("child", sys.argv[1])
child = importlib.util.module_from_spec(spec)
spec.loader.exec_module(child)
for fd in (3, 7):  # held open, with 4-6 free: a 'close to the first gap' bug leaves 7 open
    os.dup2(0, fd)
child._lock_down()
out = {"euid": os.geteuid()}
for name, fn in (("socket", lambda: socket.socket()), ("open", lambda: os.open("/dev/null", os.O_RDONLY)),
                 ("pipe", lambda: os.pipe())):
    try:
        fn()
        out[name] = "opened"
    except OSError as exc:
        out[name] = exc.errno
for fd in (3, 7):
    try:
        os.fstat(fd)
        out[f"fd{fd}"] = "open"
    except OSError:
        out[f"fd{fd}"] = "closed"
print(json.dumps(out))
"""


def _probe():
    import json
    proc = subprocess.run([sys.executable, "-I", "-c", PROBE, str(CHILD)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_no_new_descriptors_and_inherited_ones_closed():
    out = _probe()
    import errno
    assert out["socket"] == errno.EMFILE
    assert out["open"] == errno.EMFILE
    assert out["pipe"] == errno.EMFILE
    assert out["fd3"] == "closed" and out["fd7"] == "closed"


@pytest.mark.skipif(os.geteuid() != 0, reason="the root drop only shows when the lane runs as root")
def test_root_is_dropped_to_nobody():
    assert _probe()["euid"] == 65534


WIRING = r"""
import importlib.util, json, os, socket, sys
spec = importlib.util.spec_from_file_location("child", sys.argv[1])
child = importlib.util.module_from_spec(spec)
spec.loader.exec_module(child)

def probe(code, data, timeout_seconds):
    out = {"euid": os.geteuid()}
    try:
        socket.socket()
        out["socket"] = "opened"
    except OSError as exc:
        out["socket"] = exc.errno
    return out

child._executor = lambda kind: probe
print(json.dumps(child._run("memory", 5, 512)))
"""


def test_lock_down_is_wired_into_the_run_before_user_code():
    import errno
    proc = subprocess.run([sys.executable, "-I", "-c", WIRING, str(CHILD)], input=b'{"code": "", "data": null}',
                          capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    import json
    reply = json.loads(proc.stdout)
    assert reply["ok"] and reply["result"]["socket"] == errno.EMFILE
    if os.geteuid() == 0:
        assert reply["result"]["euid"] == 65534
