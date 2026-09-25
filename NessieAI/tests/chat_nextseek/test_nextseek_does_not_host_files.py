"""What NExtSEEK is, told the same way everywhere: it holds metadata, not the data files.

NExtSEEK never hosts raw data (FASTQ, BAM, FCS, images). Every data and analysis record names its file
(``File_PrimaryData``), says where the file is stored (``Link_PrimaryData``) and usually carries its checksum
(``Checksum_PrimaryData``); records deposited publicly also name the repository and its accession
(``Repository``, ``RepositoryID``). A question about downloading a file must get that answer, plus an offer to
look up where the files are, from whichever agent the question reaches (the chatter excepted: its only offer is the
reviewer's chip, so it says the result holds no locations instead):

* the system agent, for "what is NExtSEEK" and "can I download files here": it answers from the capabilities
  document, so that document says it;
* the parser, which must send a general download question to system_question and a request for the files of
  particular samples to a search for their file-location fields, never to unsupported;
* the chatter, which writes the reply when a search ran;
* the About page, which a person reads, and must name the same fields.

Every file is read by path from this checkout, never through ``chat_nextseek.__file__``, so the test checks the
tree it sits in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

NESSIE = Path(__file__).resolve().parents[2]
PACKAGE = NESSIE / "chat_nextseek" / "src" / "chat_nextseek"
PROMPTS = PACKAGE / "prompts"
CAPABILITIES = PACKAGE / "context" / "capabilities.md"
ABOUT_PAGE = NESSIE / "chat_frontend" / "src" / "components" / "Layout" / "AboutDialog.tsx"

FILE_FIELDS = ("File_PrimaryData", "Link_PrimaryData", "Checksum_PrimaryData")
NOT_HOSTED = re.compile(r"does not host|doesn't host|do not host|don't host", re.IGNORECASE)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """A markdown section of ``text`` from ``heading`` to the next heading of the same level."""
    start = text.index(heading)
    level = heading.split(" ", 1)[0]
    nxt = re.search(rf"^{re.escape(level)} ", text[start + len(heading):], re.MULTILINE)
    return text[start:start + len(heading) + (nxt.start() if nxt else len(text))]


# --- the capabilities document, which the system agent and the Container-CC agent both read -------------------------

def test_the_capabilities_document_says_what_nextseek_is_and_that_it_hosts_no_files():
    section = _section(read(CAPABILITIES), "## What NExtSEEK Is")
    assert NOT_HOSTED.search(section), "the section must say NExtSEEK does not host the data files"
    for field in FILE_FIELDS + ("Repository", "RepositoryID"):
        assert field in section, field
    assert re.search(r"metadata", section, re.IGNORECASE)


def test_the_capabilities_document_tells_the_agent_how_to_answer_a_download_question():
    section = _section(read(CAPABILITIES), "## What NExtSEEK Is")
    answer = re.search(r"(?s)When someone asks to download.*?(?=\n\n|\Z)", section)
    assert answer, "the section needs the answer to give a download question"
    assert NOT_HOSTED.search(answer.group(0))
    assert re.search(r"offer|look up|find", answer.group(0), re.IGNORECASE), "the answer offers to find the locations"


def test_the_section_defines_every_clade_the_catalog_uses():
    """The clades come from the curated catalog, so a new clade fails here until the section explains it."""
    import json

    rows = json.loads(read(NESSIE.parent / "context" / "sample_types.json"))
    clades = {row["clade"] for row in rows if row.get("clade")}
    assert clades, "the catalog carries clades"
    section = _section(read(CAPABILITIES), "## What NExtSEEK Is")
    for clade in sorted(clades):
        assert re.search(rf"\*\*{re.escape(clade)}\*\*", section), f"clade {clade} is not defined in the section"


def test_the_section_explains_that_an_assay_links_a_parent_sample_type_to_the_types_it_produces():
    section = _section(read(CAPABILITIES), "## What NExtSEEK Is")
    chain = re.search(r"(?s)### How samples connect.*?(?=\n### |\n## |\Z)", section)
    assert chain, "the section needs the sample -> assay -> sample type chain"
    text = chain.group(0)
    assert re.search(r"assay", text, re.IGNORECASE) and re.search(r"parent", text, re.IGNORECASE)
    assert "DERIVED_FROM" in text, "the chain names the graph relationship that carries the assay"
    assert re.search(r"Source.*Processed.*Raw.*Analyzed", text, re.DOTALL), "the usual order of the clades"


@pytest.mark.parametrize("claim", [
    r"holds biological samples, experimental data files",
    r"can retrieve the data files",
    r"List all sequencing data files",
])
def test_the_capabilities_document_no_longer_claims_to_hold_or_retrieve_files(claim):
    assert not re.search(claim, read(CAPABILITIES), re.IGNORECASE), claim


# --- the system agent --------------------------------------------------------------------------------------------------

def test_the_system_agent_answers_what_nextseek_is_and_file_downloads_from_that_section():
    prompt = read(PROMPTS / "system_agent.txt")
    assert "What NExtSEEK Is" in prompt, "the prompt must point the agent at the section"
    assert NOT_HOSTED.search(prompt)
    assert re.search(r"download", prompt, re.IGNORECASE)
    assert "Link_PrimaryData" in prompt


# --- the parser --------------------------------------------------------------------------------------------------------

def _path_section(text: str, name: str) -> str:
    start = text.index(f"PATH: {name}\n")
    ends = [i for i in (text.find("\nPATH: ", start + 1), text.find("\nHOW TO CHOOSE", start + 1)) if i != -1]
    return text[start:min(ends)]


ROUTING_CORES = [PROMPTS / "parser_core_routing.txt"]


@pytest.mark.parametrize("core_path", ROUTING_CORES, ids=["default"])
def test_the_parser_sends_a_general_download_question_to_system_question(core_path):
    section = _path_section(read(core_path), "system_question")
    rule = re.search(r"(?s)[^\n]*(?:download|data files)[^\n]*(?:\n[^\n]+)*", section, re.IGNORECASE)
    assert rule, "system_question needs the general file and download questions"
    assert re.search(r"what NExtSEEK is", section, re.IGNORECASE)


@pytest.mark.parametrize("core_path", ROUTING_CORES, ids=["default"])
def test_the_parser_sends_the_files_of_named_samples_to_a_search_for_their_locations(core_path):
    core = read(core_path)
    section = _path_section(core, "system_question")
    assert "Link_PrimaryData" in section and "File_PrimaryData" in section
    assert re.search(r"never unsupported|not unsupported", section, re.IGNORECASE)


@pytest.mark.parametrize("core_path", ROUTING_CORES, ids=["default"])
def test_the_parser_step_about_the_system_itself_covers_what_nextseek_is(core_path):
    core = read(core_path)
    step = core[core.index("3. Is the user asking about the system itself"):]
    step = step[:step.index("\n\n")]
    assert re.search(r"what NExtSEEK is", step, re.IGNORECASE)
    assert re.search(r"download", step, re.IGNORECASE)


# --- the chatter -------------------------------------------------------------------------------------------------------

def test_the_chatter_tells_a_person_who_asked_for_files_where_they_live():
    parts = re.split(r"\n-{10,}\n", read(PROMPTS / "chatter_agent.txt"))
    headers = [i for i, part in enumerate(parts) if part.strip().startswith("FILES AND DOWNLOADS")]
    assert headers, "the chatter needs a FILES AND DOWNLOADS section"
    body = parts[headers[0] + 1]
    assert NOT_HOSTED.search(body)
    for field in ("File_PrimaryData", "Link_PrimaryData"):
        assert field in body, field
    # No prose offer without a chip (Phase F, F-d; operator ruling Q2): with no file fields, it says so and stops.
    assert ("If it carries none of them, say that this result holds no file names or locations for those "
            "samples.") in body
    assert not re.search(r"\boffer", body, re.IGNORECASE)


# --- the About page ----------------------------------------------------------------------------------------------------

def test_the_about_page_names_the_same_file_fields_as_the_agents():
    page = read(ABOUT_PAGE)
    shown = set(re.findall(r"<Field>(\w+_PrimaryData)</Field>", page))
    assert shown, "the About page names the file fields"
    section = _section(read(CAPABILITIES), "## What NExtSEEK Is")
    assert shown <= set(FILE_FIELDS), shown - set(FILE_FIELDS)
    for field in shown:
        assert field in section, field
