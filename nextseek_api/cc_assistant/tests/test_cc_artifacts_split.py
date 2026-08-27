"""Hermetic hybrid-split + zip tests on a real tmp tree. No Docker, no DB."""
import zipfile
from pathlib import Path

from nextseek_api.cc_assistant.cc_artifacts import (
    partition_changed, build_artifact_zip, RAW_PREFIX,
)


def test_partition_splits_raw_from_artifacts():
    changed = {"report.md", "raw/debug.log", "data/out.csv", "raw/trace/x.txt"}
    artifacts, raw = partition_changed(changed)
    assert artifacts == {"report.md", "data/out.csv"}
    assert raw == {"raw/debug.log", "raw/trace/x.txt"}


def test_raw_prefix_constant():
    assert RAW_PREFIX == "raw/"


def test_build_zip_contains_all_sources(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("AAA")
    b = tmp_path / "sub" / "b.txt"; b.parent.mkdir(); b.write_text("BBB")
    dest = tmp_path / "bundle.zip"
    out = build_artifact_zip([a, b], dest)
    assert out == dest and dest.is_file()
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "a.txt" in names and "sub/b.txt" in names   # relpaths preserved (iter-10)
