"""Small, hand-made stand-ins for repository responses, shaped like the real APIs."""

JATS_XML = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
<front><article-meta><title-group><article-title>Mice and RNA.</article-title></title-group></article-meta></front>
<body>
<sec><title>Introduction</title><p>Intro text.</p></sec>
<sec sec-type="methods"><title>Materials and Methods</title>
  <sec><title>Mice</title><p>All mice were   female.</p>
    <fig id="f1"><label>Figure 1</label><caption><p>Tumour growth.</p></caption></fig>
  </sec>
  <p>RNA was sequenced.</p>
</sec>
</body>
<back>
<sec sec-type="data-availability"><title>Data availability</title><p>Reads are in GEO under GSE123456.</p></sec>
<sec><title>Supplementary Material</title>
  <supplementary-material><media xlink:href="NIHMS1_TableS1.xlsx"/><caption><p>Table S1. Samples.</p></caption></supplementary-material>
</sec>
</back>
</article>"""

EPMC_HIT = {
    "pmid": "38000001", "pmcid": "PMC10000001", "doi": "10.1000/xyz123", "title": "Mice and RNA.",
    "isOpenAccess": "Y", "inEPMC": "Y", "firstPublicationDate": "2024-02-03",
    "journalInfo": {"journal": {"title": "J Test"}},
    "authorList": {"author": [
        {"fullName": "Doe J", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Some Institute"}]}},
        {"fullName": "Roe K", "affiliation": "Other Institute"},
    ]},
}


def epmc_search(hits: list[dict]) -> dict:
    return {"resultList": {"result": hits}}


PDF_TEXT = """J Test 2024 doi:10.1000/xyz123
Mice and RNA.
Abstract
We studied things.
1. Introduction
Background here.

More background.
2 Materials and Methods
Mice were bred.
Data Availability
Reads are at GSE123456.
References
1. Smith J. A paper."""

GEO_SERIES_SOFT = """^SERIES = GSE123456
!Series_title = Mouse tumour RNA-seq
!Series_submission_date = Jan 05 2024
!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA100001
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP400001
"""

GEO_SAMPLES_SOFT = """^SAMPLE = GSM7000001
!Sample_title = tumour_rep1
!Sample_submission_date = Jan 05 2024
!Sample_source_name_ch1 = tumour
!Sample_characteristics_ch1 = strain: C57BL/6
!Sample_characteristics_ch1 = age: 8 weeks
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX2000001
!Sample_supplementary_file_1 = ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSM7000nnn/GSM7000001/suppl/GSM7000001_counts.txt.gz
!Sample_supplementary_file_2 = NONE
^SAMPLE = GSM7000002
!Sample_title = tumour_rep2
!Sample_submission_date = Jan 05 2024
!Sample_characteristics_ch1 = strain: C57BL/6
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX2000002
"""
