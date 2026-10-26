import pytest

from NessieAI.ns.paper.accessions import find_accessions
from NessieAI.ns.paper.identify import IdentifyError, identify


@pytest.mark.parametrize("raw, kind, value", [
    ("38000001", "pmid", "38000001"),
    ("PMID: 38000001", "pmid", "38000001"),
    ("pmc10000001", "pmcid", "PMC10000001"),
    ("https://pubmed.ncbi.nlm.nih.gov/38000001/", "pmid", "38000001"),
    ("https://europepmc.org/article/MED/38000001", "pmid", "38000001"),
    ("https://pmc.ncbi.nlm.nih.gov/articles/PMC10000001/", "pmcid", "PMC10000001"),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10000001/", "pmcid", "PMC10000001"),
    ("https://doi.org/10.1038/S41467-024-00001-X", "doi", "10.1038/s41467-024-00001-x"),
    ("doi:10.1016/j.cell.2024.01.001.", "doi", "10.1016/j.cell.2024.01.001"),
    ("https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1.full", "doi", "10.1101/2024.01.01.123456v1"),
    ("https://doi.org/10.1038/s41467-024-00001-x?utm_source=x", "doi", "10.1038/s41467-024-00001-x"),
    ("https://doi.org/10.1038/s41467-024-00001-x#sec2", "doi", "10.1038/s41467-024-00001-x"),
    ("https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0001234&type=printable", "doi", "10.1371/journal.pone.0001234"),
    ("https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1.full.pdf", "doi", "10.1101/2024.01.01.123456v1"),
    ("https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1.full.pdf+html", "doi", "10.1101/2024.01.01.123456v1"),
    ("paper.pdf", "file", "paper.pdf"),
])
def test_identify(raw, kind, value):
    got = identify(raw, input_files={"paper.pdf"})
    assert (got.kind, got.value) == (kind, value)


@pytest.mark.parametrize("raw", ["", "missing.pdf", "https://www.nature.com/articles/s41467-024-00001-x", "a nice paper", "https://notncbi.nlm.nih.gov/x/PMC1"])
def test_identify_refuses(raw):
    with pytest.raises(IdentifyError):
        identify(raw, input_files={"paper.pdf"})


def test_find_accessions_dedupes_and_keeps_order():
    found = find_accessions([
        "Reads are in GEO (GSE123456; samples GSM7000001) and SRA PRJNA100001, runs SRR3000001.",
        "Proteomics: PXD012345. Again GSE123456.",
        "Images: https://doi.org/10.5281/zenodo.7654321 and https://zenodo.org/records/1234567",
        "Code: https://figshare.com/articles/dataset/My_data_2024/23456789/2 and 10.6084/m9.figshare.24567890",
        "Maps: https://purl.stanford.edu/BB123CD4567",
    ])
    assert [(a.repo, a.id) for a in found] == [
        ("geo", "GSE123456"), ("geo", "GSM7000001"), ("ena", "PRJNA100001"), ("ena", "SRR3000001"),
        ("pride", "PXD012345"), ("zenodo", "7654321"), ("zenodo", "1234567"),
        ("figshare", "23456789"), ("figshare", "24567890"), ("sdr", "bb123cd4567"),
    ]
