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
