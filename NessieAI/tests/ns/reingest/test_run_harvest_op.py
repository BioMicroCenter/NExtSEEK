"""granular._run_harvest: runs-root path guard, registration/write-gate exemption,
and the SSH staging method (a remote tar built from Python's own pathlib glob,
not the remote shell's globbing).

The staging tests are the load-bearing ones: GENERIC_GLOBS' MultiQC pattern
("multiqc*/**/*_data/multiqc_*.txt") is a multi-segment "**" pattern, which
pathlib.Path.glob treats as zero-or-more intervening directories. A plain
POSIX shell `ls -1d <pattern>` does NOT match that -- confirmed directly (see
task-9-report.md): it silently returns nothing for a run whose MultiQC
directory has zero or two intervening directories (only a coincidental single
intervening directory, as in the checked-in fixture, happens to match). That
failure is silent (no error, just an empty listing), which is exactly the
failure class this staging method exists to eliminate.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g
from NessieAI.ns.reingest import harvest

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nfcore_rnaseq_run"
needs_fixture = pytest.mark.skipif(
    not FIXTURE.is_dir(),
    reason="nf-core run fixture is local-only; see the fixture README")


class _Cfg:
    LURIA_ENV = {"working_path": "/net/cluster", "key": "/dev/null",
                 "user": "user", "host": "cluster.example.edu"}


def _dispatch(op: str, args: dict, config=None, session=None, write_gate=None,
              neo4j_exec=None, outputs_dir=None):
    """The brief's sketch calls a `granular.dispatch(...)` that does not exist
    (verified: the real entry point is `run_op`, keyword-only past `args`,
    per `NessieAI.ns.granular.run_op` and every existing caller, e.g.
    `NessieAI/tests/ns/test_granular_dispatch.py`). This wrapper keeps the
    brief's positional call shape at the test call sites below while calling
    the real function correctly."""
    return g.run_op(op, args, config=config, session=session, write_gate=write_gate,
                     neo4j_exec=neo4j_exec, outputs_dir=outputs_dir)


# ---------------------------------------------------------------------------
# Step 1 (brief)
# ---------------------------------------------------------------------------

def test_run_harvest_refuses_a_dir_outside_the_runs_root():
    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-harvest", {"run_dir": "/etc"}, _Cfg(), None, None, None, None)
    assert "runs" in str(err.value)


def test_run_harvest_refuses_traversal_out_of_the_runs_root():
    with pytest.raises(g.OpValidationError):
        _dispatch("run-harvest", {"run_dir": "/net/cluster/runs/../../etc"},
                  _Cfg(), None, None, None, None)


def test_run_harvest_is_registered_and_takes_no_write_gate():
    assert "run-harvest" in g._HANDLERS
    from NessieAI.ns import write_gate
    assert "run-harvest" not in write_gate.SIDECAR_OPS


# ---------------------------------------------------------------------------
# Staging: prove the remote-Python-glob + single-tar method actually handles
# every GENERIC_GLOBS pattern, INCLUDING directory depths a plain shell glob
# (without globstar, or on a shell that never supports "**") would miss.
# ---------------------------------------------------------------------------

def _run_stage_script_locally(run_dir: str, patterns) -> bytes:
    """Stand-in for ssh_run_bytes: runs the exact remote script we ship
    (g._STAGE_SCRIPT) via a local subprocess against `run_dir`, exactly as it
    would run on the cluster host over SSH. Returns the tar bytes it writes
    to stdout."""
    proc = subprocess.run(
        [sys.executable, "-c", g._STAGE_SCRIPT, run_dir, *patterns],
        capture_output=True, check=True)
    return proc.stdout


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_stage_run_dir_matches_pathlib_at_every_multiqc_directory_depth(tmp_path, monkeypatch, depth):
    """The MultiQC glob's "**" must behave like pathlib's zero-or-more-dirs,
    at 0, 1 (the fixture's own depth) and 2 intervening directories. A plain
    `ls -1d` shell glob (no globstar) only "works" at depth 1 by coincidence
    -- confirmed to return nothing at depths 0 and 2."""
    run_dir = tmp_path / "runs" / "a_run"
    middle = "/".join(f"level{i}" for i in range(depth))
    data_dir = run_dir / "multiqc" / middle / "star_salmon_data" if middle else \
        run_dir / "multiqc" / "star_salmon_data"
    data_dir.mkdir(parents=True)
    target = data_dir / "multiqc_general_stats.txt"
    target.write_text("Sample\tstar-x\nA\t1\n")
    (run_dir / "other.txt").write_text("not matched")  # must NOT be staged

    monkeypatch.setattr(ssh, "ssh_run_bytes",
                         lambda env, cmd, *, key_path: _run_stage_script_locally(str(run_dir), harvest.GENERIC_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(staged), "/dev/null")

    expected_rel = target.relative_to(run_dir)
    staged_file = staged / expected_rel
    assert staged_file.is_file(), (
        f"depth={depth}: expected {expected_rel} to be staged; got "
        f"{sorted(p.relative_to(staged) for p in staged.rglob('*') if p.is_file())}")
    assert staged_file.read_text() == target.read_text()
    assert not (staged / "other.txt").exists()


def test_shell_ls_glob_would_have_silently_missed_depth_zero_and_two():
    """Documents exactly the failure the tar/pathlib method avoids: a plain
    `ls -1d` (no globstar) against the MultiQC "**" pattern matches only at
    one intervening directory, and returns NOTHING (not an error) at zero or
    two -- the silent-empty-result class this op exists to prevent."""
    import shutil
    if shutil.which("sh") is None:
        pytest.skip("no /bin/sh available")
    for depth, should_match in ((0, False), (1, True), (2, False)):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            middle = "/".join(f"level{i}" for i in range(depth))
            data_dir = base / "multiqc_test" / middle / "some_data" if middle else \
                base / "multiqc_test" / "some_data"
            data_dir.mkdir(parents=True)
            (data_dir / "multiqc_x.txt").write_text("x")
            proc = subprocess.run(
                "ls -1d multiqc*/**/*_data/multiqc_*.txt 2>/dev/null || true",
                shell=True, cwd=str(base), capture_output=True, text=True, executable="/bin/sh")
            matched = bool(proc.stdout.strip())
            assert matched == should_match, (
                f"depth={depth}: shell glob match={matched}, expected {should_match}")


@needs_fixture
def test_stage_run_dir_against_the_real_fixture_stages_every_rseqc_and_multiqc_file(tmp_path, monkeypatch):
    """Regression guard for the exact drift harvest.py's own module docstring
    warns about: if a GENERIC_GLOBS pattern the harvester reads were ever
    missing from the allowlist the op stages against, every sample's
    `derived` would silently come back empty in a real run while local tests
    (which read the fixture directly, bypassing staging) kept passing."""
    monkeypatch.setattr(ssh, "ssh_run_bytes",
                         lambda env, cmd, *, key_path: _run_stage_script_locally(str(FIXTURE), harvest.GENERIC_GLOBS))
    staged = tmp_path / "staged"
    staged.mkdir()
    g._stage_run_dir(_Cfg.LURIA_ENV, str(FIXTURE), str(staged), "/dev/null")

    run_manifest = harvest.harvest_local(str(staged), lookup_by_fastq=lambda p: [])
    assert run_manifest.samples, "expected staged samples"
    for sample in run_manifest.samples:
        assert sample.derived, (
            f"{sample.nfcore_sample}: derived is empty -- an RSeQC or MultiQC "
            "glob pattern was staged incorrectly")


# ---------------------------------------------------------------------------
# _run_harvest: full handler behavior (staging + harvest_local mocked out at
# the boundary so this test is about the handler's own logic, not staging).
# ---------------------------------------------------------------------------

def _patch_harvest(monkeypatch, *, failed=0, tmp_manifest_dir):
    import NessieAI.ns.reingest.harvest as harvest_mod
    from NessieAI.ns.reingest import manifest as manifest_mod

    def fake_harvest_local(root, *, lookup_by_fastq=None, extra_globs=None):
        return manifest_mod.RunManifest(
            run_dir=root,
            execution=manifest_mod.ExecutionInfo(processes=3, failed=failed, non_terminal=0),
        )

    monkeypatch.setattr(harvest_mod, "harvest_local", fake_harvest_local)
    monkeypatch.setattr(g, "_stage_run_dir", lambda *a, **k: None)
    monkeypatch.setattr(ssh, "prepare_key", lambda k: "/tmp/key")
    # store._ROOT is resolved once at module-import time from an env var; a
    # module already imported by an earlier test would ignore a later
    # monkeypatch.setenv, so patch the module attribute directly instead.
    from NessieAI.ns.reingest import store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_manifest_dir))


def test_run_harvest_refuses_a_failed_run_without_the_override(monkeypatch, tmp_path):
    _patch_harvest(monkeypatch, failed=1, tmp_manifest_dir=tmp_path)
    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-harvest", {"run_dir": "/net/cluster/runs/a"},
                  _Cfg(), None, None, None, None)
    assert "allow-failed-run" in str(err.value) or "allow_failed_run" in str(err.value)


def test_run_harvest_allows_a_failed_run_with_the_override(monkeypatch, tmp_path):
    _patch_harvest(monkeypatch, failed=1, tmp_manifest_dir=tmp_path)
    result = _dispatch(
        "run-harvest", {"run_dir": "/net/cluster/runs/a", "allow_failed_run": True},
        _Cfg(), None, None, None, None)
    assert result["run_dir"] == "/net/cluster/runs/a"
    assert result["manifest"]["execution"]["failed"] == 1
    assert result["manifest_id"]


def test_run_harvest_succeeds_and_returns_manifest_id(monkeypatch, tmp_path):
    _patch_harvest(monkeypatch, failed=0, tmp_manifest_dir=tmp_path)
    result = _dispatch("run-harvest", {"run_dir": "/net/cluster/runs/a"},
                       _Cfg(), None, None, None, None)
    assert result["run_dir"] == "/net/cluster/runs/a"
    assert result["manifest_id"]
    from NessieAI.ns.reingest.store import load_manifest
    loaded = load_manifest(result["manifest_id"])
    assert loaded.run_dir == "/net/cluster/runs/a"
