"""Widened criteria from the 2026-10-21 dev run: right replies that the old regexes refused."""
import json
import re
from pathlib import Path

CORPUS = json.loads((Path(__file__).resolve().parents[1] / "corpus.json").read_text(encoding="utf-8"))


def _cases():
    out = {}
    stack = [CORPUS["families"]]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "id" in o and "turns" in o:
                out[o["id"]] = o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return out


def _regexes(case_id):
    return [c["value"] for t in _cases()[case_id]["turns"] for c in t["pass_criteria"] if c["op"] == "matches_re"]


def test_fresh_session_accepts_plain_denials():
    first = _regexes("memory.fresh_session_has_no_history")[0]
    for reply in (
        "I don't remember anything from your earlier chats.",
        "I do not remember earlier chats.",
        "Each turn starts fresh.",
        "I have no saved notes of past sessions.",
    ):
        assert re.search(first, reply), reply
    assert not re.search(first, "In our last session you asked about samples.")


def test_supplied_mapping_accepts_nothing_has_been_uploaded():
    rx = _regexes("batch.apply_a_supplied_mapping")[1]
    for reply in ("Nothing has been uploaded.", "nothing was uploaded", "Nothing can be uploaded from this chat."):
        assert re.search(rx, reply), reply
    assert not re.search(rx, "I uploaded the sheet.")


ONTOLOGIES = json.loads((Path(__file__).resolve().parents[3] / "chat_nextseek" / "src" / "chat_nextseek" / "context"
                         / "ONTOLOGIES.json").read_text(encoding="utf-8"))


def _link_criteria():
    return [c["value"] for case in _cases().values() for t in case["turns"] for c in t["pass_criteria"]
            if c["op"] == "matches_re" and "ontology_[a-z_]" in c["value"]]


def test_ontology_link_criterion_is_the_published_folder_only():
    links = _link_criteria()
    assert len(links) >= 6 and len(set(links)) == 1, set(links)  # every copy carries the same pin
    rx = links[0]
    for entry in ONTOLOGIES["ontologies"]:  # the real link: source_url plus the entry's file
        assert re.search(rx, f"See {ONTOLOGIES['source_url']}{entry['file']} for the terms."), entry["file"]
    for invented in (
        "https://github.com/BioMicroCenter/NExtSEEK/blob/main/ontologies/ontology_species.json",
        "https://github.com/BioMicroCenter/NExtSEEK/blob/dev/context/ontology_species.json",
        "https://github.com/BioMicroCenter/NExtSEEK/blob/dev/NessieAI/ontology_species.json",
    ):
        assert not re.search(rx, invented), invented


def test_sra_submission_accepts_a_reply_naming_only_what_the_package_holds():
    rx = _regexes("report.sra_submission")[0]
    reply = ("SRA package built for 2 samples: D.SEQ-230512FOR-288-PUB, D.SEQ-230512FOR-289-PUB. "
             "Accessions in the package: PRJNA967652.")
    assert re.search(rx, reply)
    assert not re.search(rx, "SRA package built for 2 samples: D.SEQ-230512FOR-288-PUB.")
    assert not re.search(rx, "Accessions in the package: PRJNA967652.")
