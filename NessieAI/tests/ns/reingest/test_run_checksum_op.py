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
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g
from NessieAI.ns.reingest import manifest as manifest_mod
from NessieAI.ns.reingest import store as store_mod

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


def _run_checksum_script_locally(
    run_dir: str, rels: list[str], *,
    runs_root: str | None = None,
    max_file_bytes: int = g._CHECKSUM_MAX_FILE_BYTES,
    max_total_bytes: int = g._CHECKSUM_MAX_TOTAL_BYTES,
) -> str:
    """Stand-in for ssh_run: runs the exact remote script we ship
    (g._CHECKSUM_SCRIPT) via a local subprocess against `run_dir`, exactly as
    it would run on the cluster host over SSH. Returns the decoded stdout
    text (one line of JSON), matching what ssh_run itself returns.

    `max_file_bytes`/`max_total_bytes` default to the op's real ceilings so
    existing callers of this helper (written before the ceilings existed)
    keep exercising real ambient values rather than some arbitrarily large
    stand-in that would mask a ceiling regression.

    `runs_root` defaults to `run_dir`'s parent, matching every call site
    below's `tmp_path / "runs" / "a_run"` layout -- i.e. the confinement
    anchor a real `_validate_run_dir` call would have computed. The
    run_dir-is-itself-a-symlink guard test overrides it explicitly to the
    real runs root, distinct from run_dir's (symlinked) location."""
    proc = subprocess.run(
        [sys.executable, "-c", g._CHECKSUM_SCRIPT, run_dir,
         runs_root if runs_root is not None else str(Path(run_dir).parent),
         str(max_file_bytes), str(max_total_bytes), *rels],
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
                         lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

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
                         lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

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
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["aligned/sample.bam"]))

    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "aligned/sample.bam"},
                   _cfg_for(tmp_path), None, None, None, None)
    assert "outside" in str(err.value).lower()


def test_refuses_a_run_dir_that_is_itself_a_symlink_out_of_the_runs_root(tmp_path, monkeypatch):
    """The fifth containment escape (Important 1, 2026-09-16 whole-branch
    review). The escape above is a symlink INSIDE run_dir; this one is
    run_dir ITSELF: `_validate_run_dir`'s check is purely lexical, so a
    symlink at `<runs_root>/a_run` pointing at `/elsewhere` still looks
    like an ordinary subpath of the runs root and passes it. The bug this
    closes: `_CHECKSUM_SCRIPT` used to anchor its containment check on
    `run_dir.resolve()`, which for a symlinked run_dir IS `/elsewhere` --
    so every caller-named path reached through the symlink trivially
    "resolved inside run_dir" instead of being refused. Anchoring on
    `runs_root.resolve()` instead makes every such path -- and so the
    whole request -- a hard OpValidationError, exactly like any other
    caller-named escape."""
    runs_root_dir = tmp_path / "runs"
    runs_root_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    secret = outside / "sample.bam"
    secret.write_text("SECRET,DO,NOT,SHIP\n")
    run_dir = runs_root_dir / "a_run"
    run_dir.symlink_to(outside)  # run_dir ITSELF escapes the runs root

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(
            str(run_dir), ["sample.bam"], runs_root=str(runs_root_dir)))

    with pytest.raises(g.OpValidationError) as err:
        _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"},
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
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(
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
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    result = _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"},
                        _cfg_for(tmp_path), None, None, None, None)

    assert result["checksums"]["sample.bam"] == expected
    assert result["run_dir"] == str(run_dir)


# ---------------------------------------------------------------------------
# Byte ceilings (Important 1 of the 2026-09-16 review): the file COUNT cap
# alone does not bound how long run-checksum can hang -- 200 files can still
# be arbitrarily large, which is exactly the wall-clock blowout this op was
# split out to avoid. A size refusal is a reported "skipped" entry, never a
# hard OpValidationError -- BINDING CONSTRAINTS: "Size refusals may be
# skips, but must be reported."
# ---------------------------------------------------------------------------

def test_refuses_a_single_file_over_the_per_file_byte_ceiling(tmp_path, monkeypatch):
    """A file over the per-file ceiling is skipped -- and, because the
    ceiling is checked via stat() before open()/read() in _CHECKSUM_SCRIPT,
    never hashed."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    big = run_dir / "big.bam"
    big.write_bytes(b"x" * 1000)

    monkeypatch.setattr(g, "_CHECKSUM_MAX_FILE_BYTES", 100)
    monkeypatch.setattr(g, "_CHECKSUM_MAX_TOTAL_BYTES", 1_000_000)
    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(
            str(run_dir), ["big.bam"], max_file_bytes=100, max_total_bytes=1_000_000))

    result = _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "big.bam"},
                        _cfg_for(tmp_path), None, None, None, None)

    assert result["checksums"] == {}
    skip = next(item for item in result["skipped"] if item["path"] == "big.bam")
    assert "exceeds max file bytes" in skip["reason"]


def test_refuses_a_set_whose_total_exceeds_the_aggregate_byte_ceiling(tmp_path, monkeypatch):
    """Each individual file is under the per-file ceiling, but the set's
    total is over the aggregate ceiling: everything that would push the
    running total over the cap is skipped -- and never hashed, since the
    total is checked (from stat() sizes) before the file is opened."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    for name in ("a.bam", "b.bam", "c.bam"):
        (run_dir / name).write_bytes(b"x" * 100)

    monkeypatch.setattr(g, "_CHECKSUM_MAX_FILE_BYTES", 1_000)
    monkeypatch.setattr(g, "_CHECKSUM_MAX_TOTAL_BYTES", 250)
    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(
            str(run_dir), ["a.bam", "b.bam", "c.bam"],
            max_file_bytes=1_000, max_total_bytes=250))

    result = _dispatch("run-checksum",
                        {"run_dir": str(run_dir), "paths": "a.bam,b.bam,c.bam"},
                        _cfg_for(tmp_path), None, None, None, None)

    # a.bam (100) and b.bam (100) fit under the 250 total; c.bam (100) would
    # push the running total to 300 > 250, so it is skipped, unhashed.
    assert "a.bam" in result["checksums"]
    assert "b.bam" in result["checksums"]
    assert "c.bam" not in result["checksums"]
    skip = next(item for item in result["skipped"] if item["path"] == "c.bam")
    assert "exceeds total byte cap" in skip["reason"]


# ---------------------------------------------------------------------------
# Important 2 of the 2026-09-16 review: every existing test above
# monkeypatches ssh.ssh_run and ignores its `cmd` argument, so the actual
# `remote_cmd` string _run_checksum builds is never exercised. This test
# captures the real remote_cmd and drives a hostile path through it.
# ---------------------------------------------------------------------------

def test_remote_cmd_safely_quotes_a_hostile_path(monkeypatch):
    """A caller-named path containing shell metacharacters (a quote-breakout
    attempt followed by a command separator, plus a $() substitution) must
    survive shlex.quote-ing into the real remote_cmd string as ONE opaque
    argv token -- never as separate shell-interpretable words. Asserted on
    the parsed structure (shlex.split, which mirrors POSIX shell word
    splitting) rather than a substring match, per the review's ask."""
    captured = {}

    def _capture(env, cmd, *, key_path, timeout=None):
        captured["cmd"] = cmd
        return json.dumps({"checksums": {}, "skipped": [], "escaped": []})

    monkeypatch.setattr(ssh, "ssh_run", _capture)

    hostile = "a'; touch /tmp/pwned; echo '$(id)"
    _dispatch("run-checksum", {"run_dir": "/net/cluster/runs/r", "paths": hostile},
              _Cfg(), None, None, None, None)

    cmd = captured["cmd"]
    argv = shlex.split(cmd)
    # If a real shell parsed `cmd`, the hostile string must reconstitute as
    # exactly one word -- proving the embedded "'", ";", and "$(...)" were
    # neutralized by quoting rather than left live for the shell to act on.
    assert hostile in argv
    assert argv[-1] == hostile
    # And nothing decomposed it into extra commands: a naive/unquoted build
    # would have split this into several argv words (an unquoted ";" ends a
    # command).
    assert argv.count(hostile) == 1


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


# ---------------------------------------------------------------------------
# --manifest-id: fold this call's checksums into a run-harvest manifest and
# return the NEW id (manifests are content-addressed -- see store.py). Not
# passing it must leave every existing caller's result shape untouched.
# ---------------------------------------------------------------------------

def _save_manifest(tmp_path, monkeypatch, run_dir="/net/cluster/runs/r1"):
    # store._ROOT is resolved once at module-import time from an env var, so
    # a monkeypatch.setenv after the module is already imported would be
    # ignored -- patch the module attribute directly instead (same pattern
    # as test_build_upload_manifest.py's _save_manifest).
    #
    # `run_dir` defaults to a value unrelated to any real tmp_path tree
    # (fine for a caller that never actually dispatches run-checksum against
    # a real run_dir), but a caller that DOES must pass the SAME run_dir it
    # will hash against: the op now refuses a manifest whose own run_dir
    # does not match the run_dir being hashed (Critical 2, 2026-09-17
    # review) -- see test_manifest_id_round_trip_yields_a_new_id_with_the_
    # checksums_merged_in below for why this parameter exists at all.
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    run_manifest = manifest_mod.RunManifest(
        run_dir=run_dir,
        pipeline=manifest_mod.PipelineInfo(name="nf-core/rnaseq", run_name="r1"))
    return store_mod.save_manifest(run_manifest)


def test_without_manifest_id_the_result_shape_is_unchanged(tmp_path, monkeypatch):
    """No `manifest_id` key at all when the caller does not ask for one --
    every existing caller of this read-only op keeps today's exact shape."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    (run_dir / "sample.bam").write_bytes(b"hello")

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    result = _dispatch("run-checksum", {"run_dir": str(run_dir), "paths": "sample.bam"},
                        _cfg_for(tmp_path), None, None, None, None)

    assert set(result) == {"run_dir", "checksums", "skipped"}


def test_manifest_id_round_trip_yields_a_new_id_with_the_checksums_merged_in(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "a_run"
    original_id = _save_manifest(tmp_path, monkeypatch, run_dir=str(run_dir))

    run_dir.mkdir(parents=True)
    (run_dir / "sample.bam").write_bytes(b"hello world")

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    result = _dispatch(
        "run-checksum",
        {"run_dir": str(run_dir), "paths": "sample.bam", "manifest_id": original_id},
        _cfg_for(tmp_path), None, None, None, None)

    new_id = result["manifest_id"]
    assert new_id != original_id, "a manifest carrying checksums is content-different"

    updated = store_mod.load_manifest(new_id)
    assert updated.checksums == {"sample.bam": result["checksums"]["sample.bam"]}

    # Content-addressing preserved: the ORIGINAL id still loads the original,
    # checksum-less manifest -- this call must not have mutated it in place.
    original = store_mod.load_manifest(original_id)
    assert original.checksums == {}


def test_manifest_id_checksums_are_additive_not_replacing(tmp_path, monkeypatch):
    """A second run-checksum call for a different file must not drop the
    first call's checksum -- RunManifest.checksums accumulates."""
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    run_dir = tmp_path / "runs" / "a_run"
    run_manifest = manifest_mod.RunManifest(
        run_dir=str(run_dir),
        pipeline=manifest_mod.PipelineInfo(name="nf-core/rnaseq", run_name="r1"),
        checksums={"already/hashed.bam": "existing123"})
    first_id = store_mod.save_manifest(run_manifest)

    run_dir.mkdir(parents=True)
    (run_dir / "sample.bam").write_bytes(b"hello world")
    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    result = _dispatch(
        "run-checksum",
        {"run_dir": str(run_dir), "paths": "sample.bam", "manifest_id": first_id},
        _cfg_for(tmp_path), None, None, None, None)

    updated = store_mod.load_manifest(result["manifest_id"])
    assert updated.checksums["already/hashed.bam"] == "existing123"
    assert updated.checksums["sample.bam"] == result["checksums"]["sample.bam"]


# ---------------------------------------------------------------------------
# manifest_id must be cross-checked against run_dir (Critical 2, 2026-09-17
# review). Both are caller-supplied off the same CC turn; without this, a
# session that reingests run A then run B, but passes run A's stale
# manifest_id while hashing run B's files, ships run B's digest as run A's
# measured value -- relative output paths collide across nf-core runs by
# construction, so `_primary_output` matches run A's own OutputRecord for
# the identical path.
# ---------------------------------------------------------------------------

def test_a_manifest_from_a_different_run_dir_is_rejected(tmp_path, monkeypatch):
    run_a_dir = tmp_path / "runs" / "run_a"
    run_b_dir = tmp_path / "runs" / "run_b"
    run_b_dir.mkdir(parents=True)
    (run_b_dir / "sample.bam").write_bytes(b"hello world")

    # A manifest genuinely harvested from run A ...
    manifest_id = _save_manifest(tmp_path, monkeypatch, run_dir=str(run_a_dir))

    def _boom(*a, **k):
        raise AssertionError("ssh_run must not be called when run_dir mismatches the manifest")

    monkeypatch.setattr(ssh, "ssh_run", _boom)

    # ... must not be accepted while hashing a file under run B, even though
    # run B is itself a valid, existing run_dir.
    with pytest.raises(g.OpValidationError, match="not the run_dir being hashed"):
        _dispatch(
            "run-checksum",
            {"run_dir": str(run_b_dir), "paths": "sample.bam", "manifest_id": manifest_id},
            _cfg_for(tmp_path), None, None, None, None)


def test_a_manifest_from_the_matching_run_dir_is_accepted(tmp_path, monkeypatch):
    """Positive control for the check above: the ordinary, correct case (the
    manifest and the file being hashed agree on run_dir) must not be
    collaterally rejected."""
    run_dir = tmp_path / "runs" / "run_a"
    run_dir.mkdir(parents=True)
    (run_dir / "sample.bam").write_bytes(b"hello world")

    manifest_id = _save_manifest(tmp_path, monkeypatch, run_dir=str(run_dir))

    monkeypatch.setattr(
        ssh, "ssh_run",
        lambda env, cmd, *, key_path, timeout=None: _run_checksum_script_locally(str(run_dir), ["sample.bam"]))

    result = _dispatch(
        "run-checksum",
        {"run_dir": str(run_dir), "paths": "sample.bam", "manifest_id": manifest_id},
        _cfg_for(tmp_path), None, None, None, None)

    assert "manifest_id" in result
    assert result["checksums"]["sample.bam"]


def test_a_malformed_manifest_id_is_rejected():
    # match= pins this to the manifest_id check specifically: both this
    # run_dir and this manifest_id would also fail `_validate_run_dir`'s own
    # check (an un-configured Luria env, via the bare `_Cfg()`), so a bare
    # `pytest.raises(OpValidationError)` would stay green even if a future
    # change made run_dir validation run first and shadow this one entirely.
    with pytest.raises(g.OpValidationError, match="manifest_id must be alphanumeric"):
        _dispatch("run-checksum",
                   {"run_dir": "/net/cluster/runs/r", "paths": "f.bam",
                    "manifest_id": "../../etc/passwd"},
                   _Cfg(), None, None, None, None)


def test_an_unknown_manifest_id_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    # match= for the same reason as the malformed-id test above: this must
    # fail on the unknown manifest_id, not on run_dir validation happening to
    # run first and rejecting for an unrelated reason.
    with pytest.raises(g.OpValidationError, match="no manifest"):
        _dispatch("run-checksum",
                   {"run_dir": "/net/cluster/runs/r", "paths": "f.bam",
                    "manifest_id": "deadbeefdeadbeef"},
                   _Cfg(), None, None, None, None)


def test_manifest_id_is_validated_before_the_expensive_ssh_call(tmp_path, monkeypatch):
    """A bad manifest_id must fail before the remote hashing call runs at
    all -- the same 'fail cheap before failing expensive' ordering the path
    validation above already gets."""
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))

    def _boom(*a, **k):
        raise AssertionError("ssh_run must not be called for an unknown manifest_id")

    monkeypatch.setattr(ssh, "ssh_run", _boom)
    with pytest.raises(g.OpValidationError):
        _dispatch("run-checksum",
                   {"run_dir": "/net/cluster/runs/r", "paths": "f.bam",
                    "manifest_id": "deadbeefdeadbeef"},
                   _cfg_for(tmp_path), None, None, None, None)
