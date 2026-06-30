"""Hermetic tests for nested scratch -> output artifact publishing."""
from pathlib import Path

from nextseek_api.cc_assistant import cc_engine


def test_publish_artifacts_copies_nested_scratch_changes(tmp_path):
    scratch = tmp_path / "project" / "alice" / "scratch"
    output = tmp_path / "project" / "alice" / "output"
    scratch.mkdir(parents=True)
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "run1").mkdir()
    (scratch / "run1" / "result.txt").write_text("ok")

    published = cc_engine._publish_artifacts(
        scratch,
        output,
        output_host_root="/host/users/42-px/alice/output",
        before=before,
    )

    assert (output / "run1" / "result.txt").read_text() == "ok"
    assert published == ["/host/users/42-px/alice/output/run1/result.txt"]


def test_publish_artifacts_skips_symlinks(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    target = tmp_path / "secret.txt"
    target.write_text("secret")
    (scratch / "leak.txt").symlink_to(target)

    published = cc_engine._publish_artifacts(
        scratch,
        output,
        output_host_root="/host/output",
        before=before,
    )

    assert published == []
    assert not (output / "leak.txt").exists()


def test_safe_relpath_rejects_escape_paths():
    assert not cc_engine._safe_relpath("../x")
    assert not cc_engine._safe_relpath("/abs")
    assert not cc_engine._safe_relpath("")
    assert cc_engine._safe_relpath("run/result.txt")
