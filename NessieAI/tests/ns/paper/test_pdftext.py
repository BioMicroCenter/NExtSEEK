import sys
import types

from NessieAI.ns.paper.accessions import find_accessions
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


def test_pdf_to_text_joins_pages_with_a_form_feed(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, stream):
            self.pages = [Page("a"), Page(None), Page("b")]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    text = pdf_to_text(b"%PDF-1.4")
    assert text == "a\f\fb" and text.splitlines() == ["a", "", "b"]


# Line-break hyphenation: pypdf keeps the hyphen a line was broken at, and the line break.

def _para(raw: str) -> str:
    return " ".join(split_sections("Data availability\n" + raw).all_text())


def test_an_accession_broken_across_lines_is_rejoined():
    text = _para("Reads are at GSE1234-\n56 and PXD012-\n345; runs SRR30-\n00001.\n"
                 "Images: https://zenodo.org/records/765-\n4321 and 10.6084/m9.figshare.234-\n56789.\n")
    assert "GSE123456" in text and "PXD012345" in text and "zenodo.org/records/7654321" in text
    assert [(a.repo, a.id) for a in find_accessions([text])] == [
        ("geo", "GSE123456"), ("ena", "SRR3000001"), ("pride", "PXD012345"),
        ("zenodo", "7654321"), ("figshare", "23456789")]


def test_a_doi_broken_at_its_own_hyphen_stays_whole():
    text = _para("Code: doi:10.1038/s41467-024-\n00001-x and https://github.com/lab/my-\ntool here.\n")
    assert "10.1038/s41467-024-00001-x" in text and "github.com/lab/my-tool" in text


def test_hyphenated_words_and_ranges_are_left_as_they_were():
    text = _para("A well-\nknown pre-\nand post-treatment of IL-6-\nand TNF, samples GSM7000001-\n7000010.\n")
    assert text == "A well- known pre- and post-treatment of IL-6- and TNF, samples GSM7000001-7000010."
    assert [a.id for a in find_accessions([text])] == ["GSM7000001"]


def test_a_long_paragraph_with_no_sentence_end_is_cut_between_words():
    chunks = chunk_paragraphs(["GSE123456 PRJNA100001 " * 40], limit=100)
    assert len(chunks) > 1 and all(len(c) <= 100 for c in chunks)
    assert {tok for c in chunks for tok in c.split()} == {"GSE123456", "PRJNA100001"}
