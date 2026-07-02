"""Hermetic tests for nested scratch -> output artifact publishing.

G7-10: ``_publish_artifacts`` reports display paths under the mount-relative
``logical_root`` (``output_logical_root`` = ``dirs.output_mnt``, an in-container
path under ``user_root_mount``) — never a host bind string (the old
``output_host_root`` / ``output_src`` are retired).
"""
from pathlib import Path

from nextseek_api.cc_assistant import cc_engine


def test_publish_artifacts_copies_nested_scratch_changes(tmp_path):
    scratch = tmp_path / "project" / "alice" / "scratch"
    output = tmp_path / "project" / "alice" / "output"
    scratch.mkdir(parents=True)
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "run1").mkdir()
    (scratch / "run1" / "result.txt").write_text("ok")

    result = cc_engine._publish_artifacts(
        scratch,
        output,
        turn_id="run1",
        output_logical_root="/dmac/users/42-px/alice/output",
        before=before,
    )

    # single deliverable -> turn-scoped artifact dict (no zip), copied under
    # output/artifacts/<turn_id>/ (the scratch "run1/" subdir nests under it).
    assert (output / "artifacts" / "run1" / "run1" / "result.txt").read_text() == "ok"
    assert isinstance(result, dict)
    assert result["artifacts"] == [{
        "artifact_type": "file", "key": "run1/run1/result.txt",
        "label": "result.txt", "file_format": "txt",
    }]
    assert result["raw"] == [] and result["raw_zip"] is None
    assert result["files_created"] == ["run1/result.txt"]
    assert result["files_modified"] == []


def test_publish_artifacts_skips_symlinks(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    target = tmp_path / "secret.txt"
    target.write_text("secret")
    (scratch / "leak.txt").symlink_to(target)

    result = cc_engine._publish_artifacts(
        scratch,
        output,
        turn_id="run1",
        output_logical_root="/dmac/users/42-px/alice/output",
        before=before,
    )

    # _snapshot_tree skips symlinks -> nothing changed -> empty-result dict; no leak.
    assert result == {"artifacts": [], "raw": [], "raw_zip": None,
                      "files_created": [], "files_modified": []}
    assert not (output / "leak.txt").exists()
    assert not (output / "artifacts").exists()


def test_publish_artifacts_zips_multiple_and_splits_raw(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "a.txt").write_text("AAA")
    (scratch / "b.txt").write_text("BBB")
    (scratch / "raw").mkdir()
    (scratch / "raw" / "debug.log").write_text("noise")

    result = cc_engine._publish_artifacts(
        scratch,
        output,
        turn_id="run9",
        output_logical_root="/dmac/users/42-px/alice/output",
        before=before,
    )

    # >1 deliverable -> ONE turn-scoped zip artifact (key = "<turn_id>/artifacts.zip").
    assert result["artifacts"] == [{
        "artifact_type": "file", "key": "run9/artifacts.zip",
        "label": "artifacts.zip", "file_format": "zip",
    }]
    assert (output / "artifacts" / "run9" / "artifacts.zip").is_file()
    # scratch/raw/ is split off (prefix stripped), copied to output/raw/, not
    # bundled — reported under the LOGICAL root (mount-relative display path,
    # concrete expected string), never a /srv or /host bind path.
    assert (output / "raw" / "debug.log").read_text() == "noise"
    assert result["raw"] == ["/dmac/users/42-px/alice/output/raw/debug.log"]
    assert result["raw_zip"] is None
    assert result["files_created"] == ["a.txt", "b.txt", "raw/debug.log"]
    assert result["files_modified"] == []


def test_publish_raw_paths_never_use_host_bind_roots(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "raw").mkdir()
    (scratch / "raw" / "x.log").write_text("n")

    result = cc_engine._publish_artifacts(
        scratch, output, turn_id="t1",
        output_logical_root="/dmac/users/42-px/alice/output", before=before,
    )

    for p in result["raw"]:
        assert p.startswith("/dmac/users/")
        assert "/srv/dmac" not in p and "/host/" not in p


def test_safe_relpath_rejects_escape_paths():
    assert not cc_engine._safe_relpath("../x")
    assert not cc_engine._safe_relpath("/abs")
    assert not cc_engine._safe_relpath("")
    assert cc_engine._safe_relpath("run/result.txt")
