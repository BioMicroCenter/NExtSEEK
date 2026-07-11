from __future__ import annotations

import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path("build_context/plugins/nextseek/bin")))
import _batch_upload_extract as ex


def _fake_pdfplumber(monkeypatch):
    calls = {"count": 0}
    fake = types.ModuleType("pdfplumber")

    class _Page:
        def extract_text(self):
            return "fallback text"

    class _Pdf:
        pages = [_Page()]

    def open_pdf(path):
        calls["count"] += 1
        return _Pdf()

    fake.open = open_pdf
    monkeypatch.setitem(sys.modules, "pdfplumber", fake)
    return calls


def _fake_markitdown(monkeypatch, converter):
    fake = types.ModuleType("markitdown")
    fake.MarkItDown = lambda *args, **kwargs: converter
    monkeypatch.setitem(sys.modules, "markitdown", fake)


def _fake_docx(monkeypatch, texts):
    calls = {"count": 0}
    fake = types.ModuleType("docx")

    class _Paragraph:
        def __init__(self, text):
            self.text = text

    class _Document:
        def __init__(self, path):
            calls["count"] += 1
            self.paragraphs = [_Paragraph(text) for text in texts]

    fake.Document = _Document
    monkeypatch.setitem(sys.modules, "docx", fake)
    return calls


def test_extract_text_uses_markitdown(monkeypatch, tmp_path):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")
    calls = {"path": None}

    class _Result:
        text_content = "Subject: A123\nSex: M"

    class _MID:
        def convert(self, path):
            calls["path"] = path
            return _Result()

    _fake_markitdown(monkeypatch, _MID())

    assert "Subject: A123" in ex.extract_text(str(source))
    assert calls["path"] == str(source)


def test_never_silently_substitute(monkeypatch, tmp_path, capsys):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")
    fallback_calls = _fake_pdfplumber(monkeypatch)
    missing = types.ModuleType("markitdown")
    monkeypatch.setitem(sys.modules, "markitdown", missing)

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source))

    assert exc.value.code != 0
    assert fallback_calls["count"] == 0
    assert "pdfplumber" in capsys.readouterr().err


def test_docx_import_error_names_python_docx(monkeypatch, tmp_path, capsys):
    source = tmp_path / "protocol.docx"
    source.write_text("dummy")
    missing = types.ModuleType("markitdown")
    monkeypatch.setitem(sys.modules, "markitdown", missing)

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source))

    assert exc.value.code != 0
    assert "python-docx" in capsys.readouterr().err


def test_unknown_file_import_error_names_both_fallbacks(monkeypatch, tmp_path, capsys):
    source = tmp_path / "protocol.xyz"
    source.write_text("dummy")
    missing = types.ModuleType("markitdown")
    monkeypatch.setitem(sys.modules, "markitdown", missing)

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source))

    assert exc.value.code != 0
    assert "pdfplumber/python-docx" in capsys.readouterr().err


def test_markitdown_runtime_error_no_fallback(monkeypatch, tmp_path):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")
    fallback_calls = _fake_pdfplumber(monkeypatch)

    class _MID:
        def convert(self, path):
            raise RuntimeError("parse failed")

    _fake_markitdown(monkeypatch, _MID())

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source))

    assert exc.value.code != 0
    assert fallback_calls["count"] == 0


def test_markitdown_empty_text_no_fallback(monkeypatch, tmp_path):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")
    fallback_calls = _fake_pdfplumber(monkeypatch)

    class _Result:
        text_content = "   "

    class _MID:
        def convert(self, path):
            return _Result()

    _fake_markitdown(monkeypatch, _MID())

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source))

    assert exc.value.code != 0
    assert fallback_calls["count"] == 0


def test_markitdown_rejects_filetype_no_fallback(monkeypatch, tmp_path):
    source = tmp_path / "protocol.xyz"
    source.write_text("dummy")
    fallback_calls = _fake_pdfplumber(monkeypatch)

    class _MID:
        def convert(self, path):
            raise ValueError("unsupported file type")

    _fake_markitdown(monkeypatch, _MID())

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source))

    assert exc.value.code != 0
    assert fallback_calls["count"] == 0


def test_explicit_pdfplumber_fallback_invoked_only_when_selected(monkeypatch, tmp_path):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")
    fallback_calls = _fake_pdfplumber(monkeypatch)

    assert ex.extract_text(str(source), fallback="pdfplumber") == "fallback text"
    assert fallback_calls["count"] == 1


def test_explicit_python_docx_fallback(monkeypatch, tmp_path):
    source = tmp_path / "protocol.docx"
    source.write_text("dummy")
    calls = _fake_docx(monkeypatch, ["first", "second"])

    assert ex.extract_text(str(source), fallback="python-docx") == "first\nsecond"
    assert calls["count"] == 1


def test_explicit_fallback_missing_dependency_refuses(monkeypatch, tmp_path):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")
    monkeypatch.setitem(
        ex._FALLBACKS,
        "missing-lib",
        ("definitely_missing_nextseek_module", "Missing", "pdf"),
    )

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source), fallback="missing-lib")

    assert exc.value.code != 0


def test_explicit_fallback_empty_text_refuses(monkeypatch, tmp_path):
    source = tmp_path / "protocol.docx"
    source.write_text("dummy")
    _fake_docx(monkeypatch, ["", "   "])

    with pytest.raises(SystemExit) as exc:
        ex.extract_text(str(source), fallback="python-docx")

    assert exc.value.code != 0


def test_unknown_fallback_refuses(tmp_path):
    source = tmp_path / "protocol.pdf"
    source.write_text("dummy")

    with pytest.raises(ValueError, match="unknown fallback"):
        ex.extract_text(str(source), fallback="magic")


def test_private_fallback_reader_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unsupported fallback kind"):
        ex._fallback_reader("spreadsheet", object())
