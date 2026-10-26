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


CELL_TEXT = (
    "Title line\nSummary\nWe found x.\nReferences\n1. Smith J. A paper.\n2. Doe K. Another.\n"
    "STAR\u2605METHODS\nKey resources table\nAntibody X.\nMethod details\nMice were bred.\n"
    "Data and code availability\nReads are at GSE123456.\n"
)


def test_split_sections_resumes_after_the_reference_list():
    text = split_sections(CELL_TEXT)
    by = {s.slug: " ".join(p.text for p in s.paragraphs) for s in text.sections}
    assert text.has("methods") and text.has("data_availability")
    assert "Mice were bred." in by["methods"]
    assert by["data_availability"] == "Reads are at GSE123456."
    joined = " ".join(text.all_text())
    assert "Smith" not in joined and "Doe" not in joined


def test_split_sections_skips_every_reference_block():
    text = split_sections("Abstract\nA.\nReferences\n1. Smith J.\nMethods\nM.\nReferences\n2. Doe K.\n")
    joined = " ".join(text.all_text())
    assert text.has("methods") and "Smith" not in joined and "Doe" not in joined


def test_star_methods_headings_classify_to_methods_and_availability_keeps_its_slug():
    for variant in ("STAR\u2605METHODS", "STAR*Methods", "STAR Methods", "star methods", "STAR\u2605 METHODS"):
        by = {s.slug for s in split_sections(f"Front\n{variant}\nBody text.\n").sections}
        assert "methods" in by, variant
    text = split_sections(
        "Front\nSTAR Methods\nKey resources table\nAb.\nResource availability\nR.\nLead contact\nL.\n"
        "Materials availability\nM.\nExperimental model and subject details\nE.\n"
        "Quantification and statistical analysis\nQ.\n")
    slugs = [s.slug for s in text.sections]
    assert slugs.count("methods") == 1
    by = {s.slug: " ".join(p.text for p in s.paragraphs) for s in text.sections}
    assert "E." in by["methods"] and "Q." in by["methods"]
    assert {"key_resources_table", "resource_availability", "lead_contact", "materials_availability"} <= set(slugs)


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
