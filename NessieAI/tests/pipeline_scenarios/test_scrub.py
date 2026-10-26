from NessieAI.tests.pipeline_scenarios import scrub


def test_person_fields_are_redacted_at_any_depth():
    raw = {"data": [{"metadata": {"UID": "D.SEQ-1-PUB", "Scientist": "A Person", "contact_email": "x"},
                     "children": [{"metadata": {"Submitter": "B"}}]}]}
    out = scrub.scrub_metadata(raw)
    assert out["data"][0]["metadata"] == {"UID": "D.SEQ-1-PUB", "Scientist": "REDACTED", "contact_email": "REDACTED"}
    assert out["data"][0]["children"][0]["metadata"]["Submitter"] == "REDACTED"


def test_protocol_text_keeps_only_lines_with_a_marker():
    text = "Intro paragraph by the lab.\nLibraries were made with Chromium Single Cell 3' v3.\nThanks to X."
    assert scrub.protocol_marker_lines(text, markers={"chromium single cell 3' v3"}) == \
        "Libraries were made with Chromium Single Cell 3' v3."


def test_refusals_find_emails_paths_and_urls_but_not_test_data():
    assert scrub.refusals("write to a@b.org") and scrub.refusals("/home/x/y") and scrub.refusals("s3://b/k")
    assert scrub.refusals("https://raw.githubusercontent.com/nf-core/test-datasets/sarek/x") == []
