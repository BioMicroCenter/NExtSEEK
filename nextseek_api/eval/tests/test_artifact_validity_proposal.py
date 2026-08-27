"""Hermetic structural tests for the kind-agnostic artifact proposal."""
from __future__ import annotations

import sys
import zipfile
from types import SimpleNamespace

import pytest

from nextseek_api.eval import artifact_validity_proposal as av


def _zip(path, *names: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"x")


@pytest.mark.parametrize(
    "content,expected",
    [
        (b'{"x": 1}', "json"), (b"  [1]", "json"),
        (b"<?xml version='1.0'?>", "xml"), (b"<svg></svg>", "xml"),
        (b"a\tb\n1\t2\n", "tsv"), (b"a,b\n1,2\n", "csv"),
        (b"plain\n", "text"), (b"\xff\xfe", "binary"),
    ],
)
def test_detect_type_plain_formats(tmp_path, content, expected):
    path = tmp_path / "artifact"
    path.write_bytes(content)
    assert av.detect_type(path) == expected


def test_detect_type_zip_families_and_failures(tmp_path):
    for name, member, expected in [
        ("sheet", "xl/workbook.xml", "xlsx"),
        ("doc", "word/document.xml", "docx"),
        ("deck", "ppt/presentation.xml", "pptx"),
        ("archive", "payload.txt", "zip"),
    ]:
        path = tmp_path / name
        _zip(path, member)
        assert av.detect_type(path) == expected
    corrupt = tmp_path / "corrupt"
    corrupt.write_bytes(b"PK\x03\x04bad")
    assert av.detect_type(corrupt) == "zip-corrupt"
    assert av.detect_type(tmp_path / "absent") == "unreadable"


def test_collect_markers_walks_nested_dicts_and_lists():
    found = []
    av.collect_markers({"*a": None, "nested": [{"**b": 1}, "scalar"]}, found)
    av.collect_markers("scalar", found)
    assert found == ["*a", "**b"]


def test_validate_file_missing_empty_json_and_unreadable(tmp_path, monkeypatch):
    assert av.validate_file(tmp_path / "missing", "m").status == "Missing"
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert av.validate_file(empty, "e").status == "SchemaInvalid"

    unreadable = tmp_path / "u"
    unreadable.write_text("x")
    monkeypatch.setattr(av, "detect_type", lambda _p: "unreadable")
    assert av.validate_file(unreadable, "u").status == "Unreadable"
    monkeypatch.setattr(av, "detect_type", lambda _p: "zip-corrupt")
    assert av.validate_file(unreadable, "z").status == "Unreadable"

    good = tmp_path / "good.json"
    good.write_bytes(b'[{"*required": null}]')
    monkeypatch.setattr(av, "detect_type", lambda _p: "json")
    result = av.validate_file(good, "j")
    assert (result.status, result.required_markers, result.rows) == ("Valid", 1, 1)
    obj = tmp_path / "object.json"
    obj.write_bytes(b'{"x": 1}')
    assert av.validate_file(obj, "o").rows is None
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{broken")
    assert av.validate_file(bad, "b").status == "Unreadable"


def test_validate_file_spreadsheets_delimited_and_documents(tmp_path, monkeypatch):
    path = tmp_path / "artifact"
    path.write_text("payload")

    monkeypatch.setattr(av, "detect_type", lambda _p: "xlsx")
    monkeypatch.setattr(av.fastexcel, "read_excel", lambda _p: SimpleNamespace(sheet_names=[]))
    assert av.validate_file(path, "empty-book").status == "SchemaInvalid"

    class Book:
        sheet_names = ["a", "b"]
        def __init__(self, heights): self.heights = iter(heights)
        def load_sheet(self, _name, header_row=None):
            assert header_row is None
            return SimpleNamespace(height=next(self.heights))

    monkeypatch.setattr(av.fastexcel, "read_excel", lambda _p: Book([0, 0]))
    assert av.validate_file(path, "zero-rows").status == "SchemaInvalid"
    monkeypatch.setattr(av.fastexcel, "read_excel", lambda _p: Book([1, 2]))
    assert av.validate_file(path, "rows").rows == 3
    monkeypatch.setattr(av.fastexcel, "read_excel", lambda _p: (_ for _ in ()).throw(RuntimeError()))
    assert av.validate_file(path, "bad-book").status == "Unreadable"

    for kind, columns, expected_markers in [("csv", ["*a", "b"], 1), ("tsv", [], 0)]:
        monkeypatch.setattr(av, "detect_type", lambda _p, k=kind: k)
        monkeypatch.setattr(av.pl, "read_csv", lambda *_a, c=columns, **_k: SimpleNamespace(columns=c, height=2))
        result = av.validate_file(path, kind)
        assert result.required_markers == expected_markers
        assert result.status == ("Valid" if columns else "SchemaInvalid")
    monkeypatch.setattr(av.pl, "read_csv", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError()))
    assert av.validate_file(path, "bad-csv").status == "Unreadable"

    for kind, main in [("docx", "word/document.xml"), ("pptx", "ppt/presentation.xml")]:
        monkeypatch.setattr(av, "detect_type", lambda _p, k=kind: k)
        monkeypatch.setattr(av.zipfile, "ZipFile", lambda _p, names=[main]: SimpleNamespace(namelist=lambda: names))
        assert av.validate_file(path, kind).status == "Valid"
        monkeypatch.setattr(av.zipfile, "ZipFile", lambda _p: SimpleNamespace(namelist=lambda: ["other"]))
        assert av.validate_file(path, kind + "-missing").status == "SchemaInvalid"
        monkeypatch.setattr(av.zipfile, "ZipFile", lambda _p: (_ for _ in ()).throw(zipfile.BadZipFile()))
        assert av.validate_file(path, kind + "-bad").status == "Unreadable"


def test_validate_declared_tables_cover_empty_complete_and_missing():
    assert av.validate_declared_table({}, "empty").status == "SchemaInvalid"
    complete = av.validate_declared_table(
        {"columns": ["*id", "value"], "data": [{"*id": None}, "ignored"]}, "ok"
    )
    assert (complete.status, complete.required_markers, complete.rows) == ("Valid", 1, 2)
    missing = av.validate_declared_table(
        {"columns": ["*id", "**name"], "data": [{"*id": 1}]}, "bad"
    )
    assert missing.status == "Incomplete"
    assert missing.missing_markers == ["**name"]


def test_collect_disk_files_handles_duplicate_unique_and_corrupt_bundles(tmp_path):
    arm = tmp_path / "arm"
    output = arm / "output"
    loose = arm / "run_root" / "files"
    output.mkdir(parents=True)
    loose.mkdir(parents=True)
    (loose / "a.txt").write_text("a")
    bundle = output / "artifacts.zip"
    _zip(bundle, "nested/a.txt")
    assert {p.name for p in av.collect_disk_files(arm)} == {"a.txt"}
    _zip(bundle, "unique.txt")
    assert {p.name for p in av.collect_disk_files(arm)} == {"a.txt", "artifacts.zip"}
    bundle.write_bytes(b"not-a-zip")
    assert "artifacts.zip" in {p.name for p in av.collect_disk_files(arm)}


def test_validate_arm_declared_disk_missing_runtime_and_not_expected(tmp_path):
    no_task = tmp_path / "no-task"
    no_task.mkdir()
    assert av.validate_arm(no_task, True, True)[0] == "Missing"
    arm = tmp_path / "arm"
    arm.mkdir()
    (arm / "task.json").write_bytes(b"{broken")
    assert av.validate_arm(arm, True, True)[0] == "Missing"
    assert av.validate_arm(arm, True, False)[0] == "RuntimeFailed"
    assert av.validate_arm(arm, False, True)[0] == "NotExpected"

    (arm / "task.json").write_bytes(av.orjson.dumps({"result": {"artifacts": [
        {"artifact_type": "table", "key": "t", "columns": ["*id"], "data": [{}]},
        {"artifact_type": "text", "key": "ignored"},
    ]}}))
    output = arm / "output"
    output.mkdir()
    (output / "valid.txt").write_text("ok")
    status, results = av.validate_arm(arm, True, True)
    assert status == "Incomplete"
    assert len(results) == 2


def test_main_covers_excluded_warning_and_complete_message(tmp_path, monkeypatch, capsys):
    expected = [{
        "query_id": "q::ns", "task_family": "f", "artifact_expected": "true",
        "artifact_kind": "TABLE", "artifact_status": "old",
    }]
    runtime_ns = [{"query_id": "q", "runtime_success": "true"}]
    runtime_cc = [{"query_id": "q", "runtime_success": "false"}]

    class Frame:
        def __init__(self, rows): self.rows = rows
        def to_dicts(self): return self.rows

    def read_csv(path, **_kwargs):
        name = str(path)
        if "functional_eval_inputs" in name: return Frame(expected)
        return Frame(runtime_ns if name.endswith("_ns.csv") else runtime_cc)

    writes = []
    class OutputFrame:
        def __init__(self, rows, **_kwargs): self.rows = rows
        def write_csv(self, path): writes.append((path.name, len(self.rows)))

    monkeypatch.setattr(av.pl, "read_csv", read_csv)
    monkeypatch.setattr(av.pl, "DataFrame", OutputFrame)
    monkeypatch.setattr(av, "ROOT", tmp_path)
    monkeypatch.setattr(av, "OUT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["artifact_validity", "--set", "tiny"])
    monkeypatch.setattr(av, "validate_arm", lambda *_a: ("Indeterminate", []))
    av.main()
    assert "EXCLUDED" in capsys.readouterr().out
    monkeypatch.setattr(av, "validate_arm", lambda *_a: (
        "Valid", [av.ArtifactResult("a", "file", "text", 1, "Valid")]
    ))
    av.main()
    assert "no Indeterminate" in capsys.readouterr().out
    assert writes == [
        ("artifact_validity_tiny.csv", 1), ("artifact_detail_tiny.csv", 0),
        ("artifact_validity_tiny.csv", 1), ("artifact_detail_tiny.csv", 1),
    ]
