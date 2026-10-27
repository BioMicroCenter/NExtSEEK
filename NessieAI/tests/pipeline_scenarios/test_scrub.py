from NessieAI.tests.pipeline_scenarios import scrub


def test_person_fields_are_redacted_at_any_depth():
    raw = {"data": [{"metadata": {"UID": "D.SEQ-1-PUB", "Scientist": "A Person", "contact_email": "x"},
                     "children": [{"metadata": {"Submitter": "B"}}]}]}
    out = scrub.scrub_metadata(raw)
    assert out["data"][0]["metadata"] == {"UID": "D.SEQ-1-PUB", "Scientist": "REDACTED", "contact_email": "REDACTED"}
    assert out["data"][0]["children"][0]["metadata"]["Submitter"] == "REDACTED"


def test_nextseek_person_fields_are_redacted():
    names = ["Scientist", "CoScientist", "Coscientist", "InstrumentUser", "PrepUser", "QCUser", "Lab",
             "TeamLab", "Contact", "ContactEmail", "Submitter", "Owner", "Creator", "PI",
             "PrincipalInvestigator", "Author", "AuthorList", "Person", "Contributor", "Funder",
             "email", "Email_Address", "created_by", "submitted_by", "SomeUser"]
    out = scrub.scrub_metadata({n: "Hernandez Lab" for n in names})
    assert {n for n, v in out.items() if v != "REDACTED"} == set(), out
    keep = scrub.scrub_metadata({"UID": "D.SEQ-1-PUB", "SequencingType": "RNA", "Name": "S1", "Label": "x"})
    assert keep == {"UID": "D.SEQ-1-PUB", "SequencingType": "RNA", "Name": "S1", "Label": "x"}


def test_protocol_text_keeps_only_the_markers_not_the_lines():
    text = ("Intro paragraph by the lab.\nPrepared by Bernard Okafor in the Hernandez lab with RNA.\n"
            "Libraries were made with Chromium Single Cell 3' v3.\nThanks to X.")
    out = scrub.protocol_markers_found(text, markers={"chromium single cell 3' v3", "RNA", "dna"})
    assert out == "chromium single cell 3' v3\nrna"
    assert "Okafor" not in out and "Hernandez" not in out


def test_protocol_markers_are_the_atlas_ones_by_default():
    assert scrub.protocol_markers_found("nothing here at all, zzz") == ""
    assert scrub.atlas_markers()


def test_refusals_find_emails_paths_and_urls_but_not_test_data():
    assert scrub.refusals("write to a@b.org") and scrub.refusals("/home/x/y") and scrub.refusals("s3://b/k")
    assert scrub.refusals("https://raw.githubusercontent.com/nf-core/test-datasets/sarek/x") == []


def test_refusals_in_sees_an_absolute_path_after_a_newline_or_tab():
    assert scrub.refusals_in({"log": "x:\n/Users/a/b"})
    assert scrub.refusals_in(["ok", {"k": "path:\t/home/x/y"}])
    assert scrub.refusals_in({"a": [{"b": "mail me at a@b.org"}]})
    assert scrub.refusals_in({"/Users/a/b": "key holds the path"})
    assert scrub.refusals_in({"a": ["fine", 3, None, {"b": "REDACTED"}]}) == []


def test_refusals_judge_each_url_piece():
    ok = "https://raw.githubusercontent.com/nf-core/test-datasets/sarek/x"
    assert scrub.refusals(f"{ok},https://private.example/y")
    assert scrub.refusals(ok + "/../../evil")
    assert scrub.refusals(ok + "?token=1")
    assert scrub.refusals_in({"a": f"{ok},{ok}"}) == []
    assert scrub.allowed_url(ok) and not scrub.allowed_url("https://github.com.evil/nf-core/test-datasets/")


def test_glued_urls_are_refused_in_a_tape():
    ok = "https://raw.githubusercontent.com/nf-core/test-datasets/a.fq.gz"
    assert scrub.refusals(ok + "|https://private.example.org/b")
    assert scrub.refusals("[a](" + ok + ")[b](https://private.example.org/y)")
    assert scrub.refusals(ok + "https://private.example.org/b")
    assert not scrub.allowed_url(ok + "|x") and scrub.allowed_url(ok)


# ---- real cohort shapes: public archive URLs are kept, every other URL becomes "<url>" ---------

SRA_ODP = "https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR8615271/SRR8615271"
SRA_SRC = "s3://sra-pub-src-14/SRR8615271/R1.fastq.gz.1"
SRA_RUN = "s3://sra-pub-run-11/SRR8615271/SRR8615271.1"
ENA_FASTQ = "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR861/001/SRR8615271/SRR8615271_1.fastq.gz"
ENA_FTP = "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR861/001/SRR8615271/SRR8615271_2.fastq.gz"
ENA_PAGE = "https://www.ebi.ac.uk/ena/browser/view/PRJNA524989"
TRACE = "https://trace.ncbi.nlm.nih.gov/Traces/sra/?run=SRR8615271"
GEO = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE126030"
SRA_PAGE = "https://www.ncbi.nlm.nih.gov/sra/SRX5422860"
SOP = "https://nextseek.example.org/sops/12"
PUBLIC = (SRA_ODP, SRA_SRC, SRA_RUN, ENA_FASTQ, ENA_FTP, ENA_PAGE, TRACE, GEO, SRA_PAGE)


def test_public_archive_urls_are_allowed_and_nothing_else_on_their_hosts():
    for url in PUBLIC:
        assert scrub.allowed_url(url), url
        assert scrub.refusals(url) == [], url
    for url in (SOP, "https://www.ebi.ac.uk/other/x", "https://www.ncbi.nlm.nih.gov/pubmed/1",
                "https://user@ftp.sra.ebi.ac.uk/vol1/x", "https://ftp.sra.ebi.ac.uk:8443/vol1/x",
                "https://ftp.sra.ebi.ac.uk/vol1/../../etc", "https://sra-pub-run-odp.s3.amazonaws.com.evil/x",
                "s3://sra-pub-other/x", "s3://private-bucket/sra-pub-src-1/x", "http://ftp.sra.ebi.ac.uk/vol1/x",
                ENA_FASTQ + "|https://nextseek.example.org/x", "https://trace.ncbi.nlm.nih.gov/x#frag"):
        assert not scrub.allowed_url(url), url


def test_the_archive_allow_list_lives_in_scrub_only():
    """sanitize_fixture (reingest fixtures) keeps its own, narrower list: nf-core test data only."""
    assert not scrub._sanitize.allowed_url(SRA_ODP) and scrub._sanitize.disallowed_urls(SRA_ODP) == [SRA_ODP]


def test_rewrite_urls_keeps_archive_urls_and_replaces_every_other_one():
    text = f"Protocol {SOP}; reads {ENA_FASTQ},{SOP} and ({SRA_ODP}). See {GEO}."
    out = scrub.rewrite_urls(text)
    assert out == f"Protocol <url>; reads {ENA_FASTQ},<url> and ({SRA_ODP}). See {GEO}."
    assert scrub.rewrite_urls(ENA_FASTQ + "|https://nextseek.example.org/x") == "<url>"
    assert scrub.rewrite_urls("no url here") == "no url here"


def test_rewrite_urls_in_walks_every_string_leaf_and_key():
    value = {"Protocol": SOP, "Link_PrimaryData": SRA_ODP, "runs": [ENA_FASTQ, {"page": SOP}], "n": 3,
             SOP: "a key"}
    out = scrub.rewrite_urls_in(value)
    assert out == {"Protocol": "<url>", "Link_PrimaryData": SRA_ODP, "runs": [ENA_FASTQ, {"page": "<url>"}],
                   "n": 3, "<url>": "a key"}
    assert value["Protocol"] == SOP                       # the input is left as it was


def test_an_error_message_loses_its_bare_request_path_and_host():
    msg = ("HTTPSConnectionPool(host='nextseek.example.org', port=443): Max retries exceeded with url: "
           "/api/v1/samples/D.SEQ-1?x=1 (Caused by NameResolutionError(\"Failed to resolve "
           "'nextseek.example.org' ([Errno -2] Name or service not known)\"))")
    out = scrub.for_tape(msg)
    assert "/api/v1" not in out and "nextseek.example.org" not in out and scrub.refusals(out) == []
    assert "Max retries exceeded" in out


def test_for_tape_rewrites_urls_and_still_finds_paths_and_emails():
    kept = scrub.for_tape({"Protocol": SOP, "Notes": "see\n/home/someone/run/x.fastq.gz", "who": "a@b.org"})
    assert kept["Protocol"] == "<url>"
    found = scrub.refusals_in(kept)
    assert any(f.startswith("path") for f in found) and any(f.startswith("email") for f in found)
