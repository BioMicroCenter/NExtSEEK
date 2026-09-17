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
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g
from NessieAI.ns.reingest import harvest, manifest as manifest_schema
from nextseek_api.assistant.models_db import PipelineRun

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

def _run_stage_script_locally(run_dir: str, patterns, *, runs_root=None, max_file_bytes=None,
                               max_total_bytes=None, max_files=None, inventory_patterns=(),
                               max_inventory_files=None) -> bytes:
    """Stand-in for ssh_run_bytes: runs the exact remote script we ship
    (g._STAGE_SCRIPT) via a local subprocess against `run_dir`, exactly as it
    would run on the cluster host over SSH. Returns the tar bytes it writes
    to stdout (including the trailing __nextseek_stage_report__.json entry).

    ``runs_root`` defaults to ``run_dir``'s parent, matching every call site
    below's ``tmp_path / "runs" / "a_run"`` layout -- i.e. the confinement
    anchor a real ``_validate_run_dir`` call would have computed. A test
    exercising the Important-1 run_dir-is-itself-a-symlink guard overrides
    it explicitly to the real runs root, distinct from run_dir's (symlinked)
    location.

    The cap kwargs default to the real harvest.MAX_* caps, matching what
    `_stage_run_dir` passes in production; a test overrides one to exercise
    the remote-side cap enforcement without needing a multi-MB fixture file.

    ``inventory_patterns`` defaults to empty -- most tests here exercise
    only the staging half (``patterns``) and don't care about the inventory
    half at all; a test that does passes its own ``harvest.INVENTORY_GLOBS``
    or a narrower synthetic set."""
    proc = subprocess.run(
        [sys.executable, "-c", g._STAGE_SCRIPT, run_dir,
         runs_root if runs_root is not None else str(Path(run_dir).parent),
         str(max_file_bytes if max_file_bytes is not None else harvest.MAX_FILE_BYTES),
         str(max_total_bytes if max_total_bytes is not None else harvest.MAX_TOTAL_BYTES),
         str(max_files if max_files is not None else harvest.MAX_FILES),
         str(max_inventory_files if max_inventory_files is not None else harvest.MAX_INVENTORY_FILES),
         g._STAGE_REPORT_NAME, str(len(patterns)), *patterns, *inventory_patterns],
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
                         lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(str(run_dir), harvest.GENERIC_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

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


def test_stage_run_dir_skips_a_symlink_that_escapes_run_dir_and_reports_it(tmp_path, monkeypatch):
    """Important 1 (2026-09-16 review): a symlink inside an otherwise-valid
    run_dir, named to match a GENERIC_GLOBS pattern and pointing outside
    run_dir, must never be dereferenced and shipped -- the run_dir
    confinement exists because the SSH account is shared, and the read
    already happens on the cluster side, before any local extract step."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside_secret.csv"
    outside.write_text("SECRET,DO,NOT,SHIP\n")
    link = run_dir / "samplesheet.csv"  # matches _SAMPLESHEET_GLOB ("*.csv")
    link.symlink_to(outside)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(str(run_dir), harvest.GENERIC_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, _inventory = g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert not (staged / "samplesheet.csv").exists()
    assert not any("SECRET" in p.read_text() for p in staged.rglob("*") if p.is_file())
    assert any(item["path"] == "samplesheet.csv" and "symlink" in item["reason"] for item in skipped), skipped


def test_stage_run_dir_refuses_a_run_dir_that_is_itself_a_symlink_out_of_the_runs_root(tmp_path, monkeypatch):
    """The fifth containment escape (Important 1, 2026-09-16 whole-branch
    review): the escape above is a symlink INSIDE run_dir. This one is
    run_dir ITSELF: `_validate_run_dir`'s check is purely lexical (see its
    docstring), so a symlink at `<runs_root>/a_run` pointing at
    `/elsewhere` still looks like an ordinary subpath of the runs root and
    passes it. The bug this closes: _STAGE_SCRIPT used to anchor its
    containment check on `run_dir.resolve()`, which for a symlinked
    run_dir IS `/elsewhere` -- so every file glob-matched through the
    symlink trivially "resolved inside run_dir" and shipped. Confining
    against `runs_root.resolve()` instead means run_dir escaping is
    detected and refused outright, never silently degraded into an
    all-skipped-but-successful harvest."""
    runs_root_dir = tmp_path / "runs"
    runs_root_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    secret = outside / "params.json"  # matches a GENERIC_GLOBS pattern
    secret.write_text('{"secret": true}')
    run_dir = runs_root_dir / "a_run"
    run_dir.symlink_to(outside)  # run_dir ITSELF escapes the runs root

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), harvest.GENERIC_GLOBS, runs_root=str(runs_root_dir)))

    staged = tmp_path / "staged"
    staged.mkdir()
    with pytest.raises(g.OpValidationError) as err:
        g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(runs_root_dir), str(staged), "/dev/null")
    assert "runs root" in str(err.value).lower()
    assert not any(p.is_file() for p in staged.rglob("*")), "nothing must be staged from an escaping run_dir"


def test_stage_run_dir_refuses_an_oversized_file_remotely_rather_than_transferring_it(tmp_path, monkeypatch):
    """Important 2 (2026-09-16 review): the byte ceiling must be enforced on
    the cluster side, before the tar stream is built, not only after the
    whole tree is already transferred and extracted (ssh_run_bytes buffers
    the entire tar in memory; harvest_local's caps apply only after that).
    Reuses harvest.MAX_FILE_BYTES's mechanism via an override so the test
    does not need a real ~108 MB RSeQC-sized fixture file."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    big = run_dir / "big.csv"  # matches _SAMPLESHEET_GLOB ("*.csv")
    big.write_bytes(b"x" * 100)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), harvest.GENERIC_GLOBS, max_file_bytes=50))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, _inventory = g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert not (staged / "big.csv").exists(), "oversized file must never be transferred, not just discarded locally"
    assert any(
        item["path"] == "big.csv" and "exceeds max file bytes" in item["reason"] for item in skipped
    ), skipped


def test_stage_run_dir_skips_a_hardlink_to_a_file_outside_run_dir_and_reports_it(tmp_path, monkeypatch):
    """Important 1 of the follow-up 2026-09-16 adversarial review: a
    hardlink inside run_dir has no separate target path to resolve away
    from -- `is_symlink()` is False and `resolve()` returns itself -- so
    the symlink checks above (test_stage_run_dir_skips_a_symlink_...) do
    NOT catch it. Without the st_nlink check this ships the outside file's
    real content."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside_secret.csv"
    outside.write_text("SECRET,DO,NOT,SHIP\n")
    link = run_dir / "samplesheet.csv"  # matches _SAMPLESHEET_GLOB ("*.csv")
    os.link(str(outside), str(link))

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(str(run_dir), harvest.GENERIC_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, _inventory = g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert not (staged / "samplesheet.csv").exists()
    assert not any("SECRET" in p.read_text() for p in staged.rglob("*") if p.is_file())
    assert any(
        item["path"] == "samplesheet.csv" and "hardlink" in item["reason"] for item in skipped
    ), skipped


def test_stage_run_dir_skips_a_file_reached_through_a_symlinked_ancestor_dir(tmp_path, monkeypatch):
    """Minor 3 (2026-09-16 review): the module comment above _STAGE_SCRIPT
    claims resolve() 'catches a symlinked ancestor directory too', but only
    the direct leaf-symlink case (test_stage_run_dir_skips_a_symlink_...) was
    ever tested. Here the MATCHED FILE ITSELF is a real file, not a symlink
    -- it is reached through a symlinked PARENT directory that points
    outside run_dir -- so path.is_symlink() is False and only the
    resolve()/relative_to() ancestor check can catch it."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    outside_file = outside_dir / "params_x.json"
    outside_file.write_text('{"secret": true}')

    # pipeline_info is a symlink (the ancestor), pointing outside run_dir;
    # the file matched by the glob underneath it is an ordinary file.
    (run_dir / "pipeline_info").symlink_to(outside_dir)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(str(run_dir), harvest.GENERIC_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, _inventory = g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert not (staged / "pipeline_info" / "params_x.json").exists()
    assert not any("secret" in p.read_text() for p in staged.rglob("*") if p.is_file())
    assert any(
        item["path"] == "pipeline_info/params_x.json" and "outside run_dir" in item["reason"]
        for item in skipped
    ), skipped


def test_stage_run_dir_enforces_the_total_byte_cap_across_multiple_files(tmp_path, monkeypatch):
    """Minor 2 (2026-09-16 review): MAX_TOTAL_BYTES accumulates across the
    whole remote loop, but only the single-file MAX_FILE_BYTES case had a
    test. Two files individually under the per-file cap must still trip the
    TOTAL cap once their combined size crosses it, and the second one must
    never be transferred."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    first = run_dir / "a.csv"
    second = run_dir / "b.csv"
    first.write_bytes(b"x" * 60)
    second.write_bytes(b"y" * 60)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), harvest.GENERIC_GLOBS, max_file_bytes=1000, max_total_bytes=100))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, _inventory = g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    staged_names = {p.name for p in staged.rglob("*") if p.is_file()}
    assert "a.csv" in staged_names, staged_names
    assert "b.csv" not in staged_names, staged_names
    assert any(
        item["path"] == "b.csv" and "exceeds total byte cap" in item["reason"] for item in skipped
    ), skipped


def test_stage_run_dir_enforces_the_file_count_cap_across_multiple_files(tmp_path, monkeypatch):
    """Minor 2 (2026-09-16 review): MAX_FILES accumulates across the whole
    remote loop too, and was equally untested. Three files under a cap of 2
    must stage exactly the first two (glob order) and skip the third with a
    file-count reason."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    for name in ("a.csv", "b.csv", "c.csv"):
        (run_dir / name).write_text("x")

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), harvest.GENERIC_GLOBS, max_files=2))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, _inventory = g._stage_run_dir(_Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    staged_names = {p.name for p in staged.rglob("*") if p.is_file()}
    assert staged_names == {"a.csv", "b.csv"}, staged_names
    assert any(
        item["path"] == "c.csv" and "exceeds max file count" in item["reason"] for item in skipped
    ), skipped


@needs_fixture
def test_stage_run_dir_against_the_real_fixture_stages_every_rseqc_and_multiqc_file(tmp_path, monkeypatch):
    """Regression guard for the exact drift harvest.py's own module docstring
    warns about: if a GENERIC_GLOBS pattern the harvester reads were ever
    missing from the allowlist the op stages against, every sample's
    `derived` would silently come back empty in a real run while local tests
    (which read the fixture directly, bypassing staging) kept passing."""
    monkeypatch.setattr(ssh, "ssh_run_bytes",
                         lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(str(FIXTURE), harvest.GENERIC_GLOBS))
    staged = tmp_path / "staged"
    staged.mkdir()
    g._stage_run_dir(_Cfg.LURIA_ENV, str(FIXTURE), str(FIXTURE.parent), str(staged), "/dev/null")

    run_manifest = harvest.harvest_local(str(staged), lookup_by_fastq=lambda p: [])
    assert run_manifest.samples, "expected staged samples"
    for sample in run_manifest.samples:
        assert sample.derived, (
            f"{sample.nfcore_sample}: derived is empty -- an RSeQC or MultiQC "
            "glob pattern was staged incorrectly")


# ---------------------------------------------------------------------------
# Inventory: the SAME remote script also emits a name+size-only listing for
# every INVENTORY_GLOBS match, subject to the SAME containment guards as
# staging (symlink / escape / hardlink) plus its own count cap. Nothing in
# this half is ever staged or transferred -- the tar carries only the
# `patterns` (GENERIC_GLOBS) matches; `inventory` entries are listing-only.
# ---------------------------------------------------------------------------

def test_stage_run_dir_emits_an_inventory_entry_for_each_output_glob_match(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    bam_dir = run_dir / "star_salmon"
    bam_dir.mkdir()
    bam = bam_dir / "CONTROL_REP1.markdup.sorted.bam"
    bam.write_bytes(b"x" * 4096)
    (run_dir / "not_an_output.txt").write_text("not matched")

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), [], inventory_patterns=harvest.INVENTORY_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, inventory = g._stage_run_dir(
        _Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    # Listing-only: the matched file is never staged, unlike a GENERIC_GLOBS
    # match -- the inventory names it and its real size, nothing more.
    assert not (staged / "star_salmon" / "CONTROL_REP1.markdup.sorted.bam").exists()
    assert inventory == [{"path": "star_salmon/CONTROL_REP1.markdup.sorted.bam", "bytes": 4096}]
    assert skipped == []


def test_stage_run_dir_skips_a_symlinked_inventory_candidate_and_reports_it(tmp_path, monkeypatch):
    """The listing gets the SAME containment guard as staging: listing a
    symlink's target name+size is a smaller disclosure than shipping its
    content, but this is still a shared cluster account, and the boundary
    must not have two standards."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside_secret.bam"
    outside.write_bytes(b"SECRET")
    link = run_dir / "CONTROL_REP1.markdup.sorted.bam"
    link.symlink_to(outside)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), [], inventory_patterns=harvest.INVENTORY_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, inventory = g._stage_run_dir(
        _Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert inventory == []
    assert any(
        item["path"] == "CONTROL_REP1.markdup.sorted.bam" and "symlink" in item["reason"]
        for item in skipped
    ), skipped


def test_stage_run_dir_skips_an_inventory_candidate_reached_through_an_escaping_path(tmp_path, monkeypatch):
    """The escape guard (ancestor-symlink / resolves-outside-run_dir) applies
    to inventory candidates too, not only staged ones. This uses a
    non-"**" pattern ("aligner_out/*.bam", not one of the real
    INVENTORY_GLOBS): on current interpreters, confirmed directly against a
    symlinked ancestor, pathlib.Path.glob's "**" component does not recurse
    through it (the exact Python version this became the default is not
    authoritative here -- see
    test_stage_run_dir_skips_the_real_multiqc_report_glob_reached_through_a_symlinked_dir
    below for why the guard does not depend on it either way). A leading
    literal or wildcard glob SEGMENT (like "aligner_out" here, or "multiqc*"
    in the one real INVENTORY_GLOBS entry with a non-"**" leading segment)
    still follows a symlink there normally, so this pattern -- like that
    real one -- does reach the symlinked file, exercising the STAT-LEVEL
    guard inside confined_stat (shared with staging) directly, the same way
    GENERIC_GLOBS' own ancestor-symlink test relies on _PARAMS_GLOB (also
    non-"**")."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    outside_file = outside_dir / "leak.bam"
    outside_file.write_bytes(b"leak")
    (run_dir / "aligner_out").symlink_to(outside_dir)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), [], inventory_patterns=["aligner_out/*.bam"]))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, inventory = g._stage_run_dir(
        _Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert inventory == []
    assert any(
        item["path"] == "aligner_out/leak.bam" and "outside run_dir" in item["reason"]
        for item in skipped
    ), skipped


def test_stage_run_dir_skips_the_real_multiqc_report_glob_reached_through_a_symlinked_dir(tmp_path, monkeypatch):
    """The ONE production INVENTORY_GLOBS entry an ancestor-symlink escape
    can actually reach: "multiqc*/**/multiqc_report.html" is the only
    pattern in the tuple whose leading segment is a literal/wildcard
    ("multiqc*") rather than "**" -- and, per the synthetic-pattern test
    above, only the "**" component declines to follow a symlink; a leading
    wildcard segment still follows one normally. Every other INVENTORY_GLOBS
    entry starts with "**" and so never reaches a file behind a symlinked
    ancestor to begin with, which is why the synthetic "aligner_out/*.bam"
    test above is needed to exercise confined_stat's escape check at all --
    but that leaves the actually-exposed production path itself unverified.
    This test drives `harvest.INVENTORY_GLOBS` directly (no synthetic
    pattern) against a directory literally named "multiqc" -- the shape
    nf-core's own MultiQC step writes -- symlinked to outside run_dir, so a
    regression in either the pattern's leading segment or in confined_stat's
    escape check would show up here."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    outside_dir = tmp_path / "outside_multiqc"
    outside_dir.mkdir()
    outside_report = outside_dir / "multiqc_report.html"
    outside_report.write_text("<html>leaked</html>")
    (run_dir / "multiqc").symlink_to(outside_dir)

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), [], inventory_patterns=harvest.INVENTORY_GLOBS))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, inventory = g._stage_run_dir(
        _Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert inventory == []
    assert any(
        item["path"] == "multiqc/multiqc_report.html" and "outside run_dir" in item["reason"]
        for item in skipped
    ), skipped


def test_stage_run_dir_enforces_the_inventory_file_count_cap(tmp_path, monkeypatch):
    """MAX_INVENTORY_FILES bounds the listing the same way MAX_FILES bounds
    staging -- cap hit reported, never a silent omission."""
    run_dir = tmp_path / "runs" / "a_run"
    run_dir.mkdir(parents=True)
    for i in range(3):
        (run_dir / f"sample{i}.bam").write_bytes(b"x")

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(
            str(run_dir), [], inventory_patterns=harvest.INVENTORY_GLOBS, max_inventory_files=2))

    staged = tmp_path / "staged"
    staged.mkdir()
    skipped, inventory = g._stage_run_dir(
        _Cfg.LURIA_ENV, str(run_dir), str(run_dir.parent), str(staged), "/dev/null")

    assert len(inventory) == 2
    assert any("exceeds max inventory file count" in item["reason"] for item in skipped), skipped


# ---------------------------------------------------------------------------
# _run_harvest: full handler behavior (staging + harvest_local mocked out at
# the boundary so this test is about the handler's own logic, not staging).
# ---------------------------------------------------------------------------

def _patch_harvest(monkeypatch, *, failed=0, tmp_manifest_dir):
    import NessieAI.ns.reingest.harvest as harvest_mod
    from NessieAI.ns.reingest import manifest as manifest_mod

    def fake_harvest_local(root, *, lookup_by_fastq=None, inventory=None, run_dir=None):
        return manifest_mod.RunManifest(
            run_dir=run_dir if run_dir is not None else root,
            execution=manifest_mod.ExecutionInfo(processes=3, failed=failed, non_terminal=0),
        )

    monkeypatch.setattr(harvest_mod, "harvest_local", fake_harvest_local)
    monkeypatch.setattr(g, "_stage_run_dir", lambda *a, **k: ([], []))
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


# ---------------------------------------------------------------------------
# End-to-end: neither Critical from the 2026-09-16 whole-branch review shows
# up in a per-module test, because both live in the SEAM between modules
# built and reviewed separately.
#
# Critical 1: _run_harvest stages into a tempfile.TemporaryDirectory() and
# used to call harvest_local(staged, ...) with no run_dir label, so
# uid_resolve.resolve() looked up PipelineRun by the TEMP path -- which never
# matches the launch record's real (cluster) run_dir. Every sample fell
# through to the fastq fallback, and PipelineInfo.run_name became the temp
# dir's own generated name.
#
# Critical 2: harvest_local's samplesheet glob ("*.csv") picked whichever
# match sorted first. A pipeline run with `--outdir .` writes the fetchngs
# pre-stage's `ids.csv` directly alongside the real `samplesheet.csv`, and
# "ids.csv" < "samplesheet.csv" alphabetically -- so the harvester read the
# wrong file (here, an empty one) and would have returned zero samples with
# no warning at all.
#
# This test drives the full op (ssh_run_bytes stubbed to tar a synthetic run
# tree containing BOTH files, exactly as production staging would) with a
# PipelineRun row present for the CLUSTER path, and fails on either bug on
# its own -- so it is the single test that closes both.
# ---------------------------------------------------------------------------

def test_run_harvest_end_to_end_resolves_via_launch_record_despite_a_decoy_csv(
        monkeypatch, tmp_path):
    run_dir = "/net/cluster/runs/nfcore_end_to_end"
    source = tmp_path / "cluster_run"
    source.mkdir(parents=True)

    (source / "pipeline_info").mkdir()
    (source / "pipeline_info" / "nf_core_rnaseq_software_mqc_versions.yml").write_text(
        "Workflow:\n  nf-core/rnaseq: v3.22.2\n  Nextflow: 25.10.2\n")
    (source / "pipeline_info" / "execution_trace.txt").write_text(
        "task_id\tstatus\n1\tCOMPLETED\n")

    # The fetchngs pre-stage's decoy, written BEFORE the `[ -s ids.csv ]`
    # guard in the common case: empty, and alphabetically ahead of the real
    # samplesheet.
    (source / "ids.csv").write_text("")
    (source / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,strandedness\n"
        "CONTROL_REP1,/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz,,auto\n")

    monkeypatch.setattr(
        ssh, "ssh_run_bytes",
        lambda env, cmd, *, key_path, timeout=None: _run_stage_script_locally(str(source), harvest.GENERIC_GLOBS))
    monkeypatch.setattr(ssh, "prepare_key", lambda k: "/tmp/key")
    from NessieAI.ns.reingest import store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))

    PipelineRun.objects.create(
        run_dir=run_dir, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
                 "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                 "fastq_2": None}])

    result = _dispatch("run-harvest", {"run_dir": run_dir},
                        _Cfg(), None, None, None, None)

    got = result["manifest"]
    assert got["run_dir"] == run_dir, (
        "run_dir must be the cluster path, not the tempfile.TemporaryDirectory "
        "harvest_local actually read from")
    assert got["pipeline"]["run_name"] == "nfcore_end_to_end", (
        "run_name must derive from the cluster run_dir, not the temp staging "
        "dir's generated name")

    samples = got["samples"]
    assert samples, (
        "manifest.samples came back empty -- the empty ids.csv was read as "
        "the samplesheet instead of samplesheet.csv")
    assert {s["nfcore_sample"] for s in samples} == {"CONTROL_REP1"}
    sample = samples[0]
    assert sample["uid_resolution"] == manifest_schema.RESOLUTION_LAUNCH_RECORD, (
        "the PipelineRun launch record for the cluster run_dir must resolve "
        "this sample; it never will if uid_resolve was looked up by the temp "
        "staging path")
    assert sample["d_seq_uid"] == "D.SEQ-EXAMPLE-1"
