"""granular._run_checksum: bounded md5 of a caller-named set of settled
primary-data files under a finished Luria run.

Separate from run-harvest on purpose (see the op's docstring): which files
become File_PrimaryData is only known after sample types are assigned, and
hashing multi-GB BAMs during harvest would blow the harvest step's wall clock.

Because ``--paths`` is CALLER-supplied (not a glob match, unlike run-harvest's
GENERIC_GLOBS), an escaping path is a HARD validation error here, not a
silent skip folded into the result's "skipped" list -- an explicit request
to read outside run_dir is treated as adversarial, not incidental. "skipped"
is reserved for a legitimate miss (the file does not exist / is not a
regular file).

The remote-side symlink/hardlink/escape checks mirror run-harvest's
_STAGE_SCRIPT guards (see granular.py, task-9 review): a symlink or
hardlink inside run_dir has no honest local (string-only) test, since the
confinement question can only be answered by stat-ing the real file on the
cluster. Tests below run the exact script we ship (g._CHECKSUM_SCRIPT) via a
local subprocess against a tmp_path tree, exactly as it would run over SSH,
by monkeypatching ``ssh.ssh_run``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g

pytestmark = pytest.mark.django_db


class _Cfg:
    LURIA_ENV = {"working_path": "/net/cluster", "key": "/dev/null",
                 "user": "user", "host": "cluster.example.edu"}


def _cfg_for(working_path):
    """A _Cfg whose runs root is under `working_path` -- needed by the
    filesystem-backed guard tests below, which use a real tmp_path tree as
    run_dir (unlike the pure path-string tests above, which never touch a
    real filesystem and so can use the fixed fake `_Cfg.LURIA_ENV`)."""
    class _TmpCfg:
        LURIA_ENV = {"working_path": str(working_path), "key": "/dev/null",
                     "user": "user", "host": "cluster.example.edu"}
    return _TmpCfg()


def _dispatch(op: str, args: dict, config=None, session=None, write_gate=None,
              neo4j_exec=None, outputs_dir=None):
    """The brief's sketch calls a `granular.dispatch(...)` that does not exist
    (the real entry point is `run_op`, keyword-only past `args`; see
    `NessieAI/tests/ns/reingest/test_run_harvest_op.py`'s identical note).
    This wrapper keeps the brief's positional call shape at the test call
    sites below while calling the real function correctly."""
    return g.run_op(op, args, config=config, session=session, write_gate=write_gate,
                     neo4j_exec=neo4j_exec, outputs_dir=outputs_dir)


def _run_checksum_script_locally(run_dir: str, rels: list[str]) -> str:
    """Stand-in for ssh_run: runs the exact remote script we ship
    (g._CHECKSUM_SCRIPT) via a local subprocess against `run_dir`, exactly as
    it would run on the cluster host over SSH. Returns the decoded stdout
    text (one line of JSON), matching what ssh_run itself returns."""
    proc = subprocess.run(
        [sys.executable, "-c", g._CHECKSUM_SCRIPT, run_dir, *rels],
        capture_output=True, text=True, check=True)
    return proc.stdout


# ---------------------------------------------------------------------------
# Step 1 (brief)
# ---------------------------------------------------------------------------

def test_refuses_a_path_that_escapes_the_run_dir():
    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum",
                   {"run_dir": "/net/cluster/runs/r", "paths": "../../etc/passwd"},
                   _Cfg(), None, None, None, None)
    assert "outside" in str(err.value).lower()


def test_refuses_more_paths_than_the_cap():
    too_many = ",".join(f"f{i}.bam" for i in range(g._CHECKSUM_MAX_FILES + 1))
    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum",
                   {"run_dir": "/net/cluster/runs/r", "paths": too_many},
                   _Cfg(), None, None, None, None)
    assert "too many" in str(err.value).lower()


def test_requires_at_least_one_path():
    with pytest.raises(g.OpValidationError):
        _dispatch("run-checksum", {"run_dir": "/net/cluster/runs/r", "paths": ""},
                   _Cfg(), None, None, None, None)


def test_run_checksum_is_registered_and_takes_no_write_gate():
    assert "run-checksum" in g._HANDLERS
    from NessieAI.ns import write_gate
    assert "run-checksum" not in write_gate.SIDECAR_OPS


# ---------------------------------------------------------------------------
# Security guards on the caller-supplied path list. Unlike run-harvest's
# glob matches, every one of these is a HARD validation error: the caller
# named the path explicitly, so an escape is an adversarial request, not an
# incidental miss.
# ---------------------------------------------------------------------------

def test_refuses_a_symlink_inside_run_dir_pointing_outside(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside_secret.bam"
    outside.write_text("SECRET,DO,NOT,SHIP\n")
    link = run_dir / "sample.bam"
    link.symlink_to(outside)

    monkeypatch.setattr(ssh, "ssh_run",
                         lambda env, cmd, *, key_path: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"},
                   _cfg_for(tmp_path), None, None, None, None)
    assert "sample.bam" in str(err.value)
    assert "symlink" in str(err.value).lower()


def test_refuses_a_hardlink_to_a_file_outside_run_dir(tmp_path, monkeypatch):
    """A hardlink has no separate target path to resolve away from
    (is_symlink() is False, resolve() returns itself), so only the st_nlink
    check catches it -- see granular.py's _STAGE_SCRIPT note on the same
    class of escape in run-harvest."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside_secret.bam"
    outside.write_text("SECRET,DO,NOT,SHIP\n")
    link = run_dir / "sample.bam"
    os.link(str(outside), str(link))

    monkeypatch.setattr(ssh, "ssh_run",
                         lambda env, cmd, *, key_path: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"},
                   _cfg_for(tmp_path), None, None, None, None)
    assert "sample.bam" in str(err.value)
    assert "hardlink" in str(err.value).lower()


def test_refuses_a_file_reached_through_a_symlinked_ancestor_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    outside_file = outside_dir / "sample.bam"
    outside_file.write_text("SECRET")
    (run_dir / "aligned").symlink_to(outside_dir)

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path: _run_checksum_script_locally(str(run_dir), ["aligned/sample.bam"]))

    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "aligned/sample.bam"},
                   _cfg_for(tmp_path), None, None, None, None)
    assert "outside" in str(err.value).lower()


def test_a_missing_file_is_a_reported_skip_not_a_silent_omission(tmp_path, monkeypatch):
    """Unlike an escape, a legitimately absent file is not adversarial --
    surfaced in the result's `skipped` list rather than raised."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    present = run_dir / "present.bam"
    present.write_bytes(b"hello")

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path: _run_checksum_script_locally(
            str(run_dir), ["present.bam", "missing.bam"]))

    result = _dispatch("run-checksum",
                        {"run_dir": str(run_dir), "paths": "present.bam,missing.bam"},
                        _cfg_for(tmp_path), None, None, None, None)

    assert "present.bam" in result["checksums"]
    assert any(item["path"] == "missing.bam" for item in result["skipped"])


def test_computes_the_real_md5_of_an_allowed_file(tmp_path, monkeypatch):
    import hashlib
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    target = run_dir / "sample.bam"
    target.write_bytes(b"hello world")
    expected = hashlib.md5(b"hello world").hexdigest()

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    result = _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"},
                        _cfg_for(tmp_path), None, None, None, None)

    assert result["checksums"]["sample.bam"] == expected
    assert result["run_dir"] == str(run_dir)


def test_checksum_script_output_is_valid_json():
    """Direct sanity check on the remote script itself, independent of the
    op wrapper -- protects against a future edit that breaks its stdout
    contract (JSON on stdout, nothing else)."""
    with __import__("tempfile").TemporaryDirectory() as d:
        run_dir = Path(d)
        (run_dir / "a.txt").write_text("x")
        out = _run_checksum_script_locally(str(run_dir), ["a.txt"])
    payload = json.loads(out)
    assert set(payload) >= {"checksums", "skipped", "escaped"}
