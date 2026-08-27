from pathlib import Path

from nextseek_api.cc_assistant.cc_upload_list import list_input_files


def test_list_input_files_sorted_basenames(tmp_path: Path):
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.csv").write_text("y")
    (tmp_path / ".hidden").write_text("z")
    (tmp_path / "subdir").mkdir()
    assert list_input_files(str(tmp_path)) == ["a.csv", "b.txt"]


def test_list_input_files_missing_dir_returns_empty(tmp_path: Path):
    # the user's input/ may not exist before the first upload -> empty, not an error
    assert list_input_files(str(tmp_path / "nope")) == []
