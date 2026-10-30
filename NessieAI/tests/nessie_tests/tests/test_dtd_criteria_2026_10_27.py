"""Package-only GEO criteria and the flow-file reply, checked against replies from the 2026-10-27 dev run."""
import json
import re
from pathlib import Path

CORPUS = json.loads((Path(__file__).resolve().parents[1] / "corpus.json").read_text(encoding="utf-8"))


def _regexes(case_id):
    stack, found = [CORPUS["families"]], None
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if o.get("id") == case_id and "turns" in o:
                found = o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return [c["value"] for t in found["turns"] for c in t["pass_criteria"] if c["op"] == "matches_re"]

GEO2 = ("GEO submission draft for two paired-end Seq-Well S3 scRNA-seq libraries (NovaSeq 6000). "
        "D.SEQ-221031SHA-65-PUB is the right upper lobe granuloma. D.SEQ-221031SHA-67-PUB is the right lower lobe granuloma. "
        "Generated report payload is available in the Reporter result panel.")
GEO1 = ("GEO draft for D.SEQ-241219BRY-5-PUB (paired-end polyA RNA-seq, prime-seq, NextSeq 500). "
        "Generated report payload is available in the Reporter result panel.")
FLOW = ("NExtSEEK doesn't host data files; it holds metadata that points to where each file lives. "
        "Both point to the same external location, the ImmPort repository, study SDY2268. "
        "D.FLOW-230330BOO-56-PUB: file x.fcs. D.FLOW-230330BOO-57-PUB: file y.fcs.")


def test_geo_submission_needs_both_uids_not_accessions():
    rx = _regexes("report.geo_submission")[0]
    assert re.search(rx, GEO2)
    assert not re.search(rx, GEO2.replace("D.SEQ-221031SHA-67-PUB", "the other one"))
    # both UIDs are in the question, so a reply that only repeats them must stay red
    assert not re.search(rx, "I could not find metadata for D.SEQ-221031SHA-67-PUB or D.SEQ-221031SHA-65-PUB, "
                             "so the GEO draft is empty.")


def test_geo_deposit_needs_the_uid_and_nextseq():
    rx = _regexes("report.create_a_geo_deposit_file_for")[0]
    assert re.search(rx, GEO1)
    assert not re.search(rx, GEO1.replace("D.SEQ-241219BRY-5-PUB", "the sample"))
    assert not re.search(rx, GEO1.replace("NextSeq", "Illumina"))


def test_flow_file_reply_may_give_locations_in_words():
    rx = _regexes("file.flow_file_for_a_patient")[-1]
    assert re.search(rx, FLOW)
    assert not re.search(rx, "Sorry, nothing found for that patient.")
