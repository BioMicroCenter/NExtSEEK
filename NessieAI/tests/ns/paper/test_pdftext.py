import sys
import types

from NessieAI.ns.paper.pdftext import chunk_paragraphs, pdf_to_text, split_sections
from NessieAI.tests.ns.paper.fixtures import PDF_TEXT


def test_split_sections():
    text = split_sections(PDF_TEXT)
    by = {s.slug: [(p.id, p.text) for p in s.paragraphs] for s in text.sections}
    assert by["front"] == [("E:front:p1", "J Test 2024 doi:10.1000/xyz123 Mice and RNA.")]
    assert by["introduction"] == [("E:introduction:p1", "Background here."), ("E:introduction:p2", "More background.")]
    assert by["methods"] == [("E:methods:p1", "Mice were bred.")]
    assert by["data_availability"] == [("E:data_availability:p1", "Reads are at GSE123456.")]
    assert "Smith" not in " ".join(text.all_text())


def test_chunk_paragraphs_splits_long_text_at_sentences():
    long = ("Sentence number one is here. " * 60).strip()
    chunks = chunk_paragraphs([long], limit=200)
    assert all(len(c) <= 200 for c in chunks) and len(chunks) > 1
    assert all(c.endswith(".") for c in chunks)


def test_pdf_to_text_joins_pages(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, stream):
            self.pages = [Page("a"), Page(None), Page("b")]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    assert pdf_to_text(b"%PDF-1.4") == "a\n\nb"
