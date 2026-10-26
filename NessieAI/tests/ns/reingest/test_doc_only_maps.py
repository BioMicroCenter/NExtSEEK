"""Maps written from a release's pinned docs/output.md alone, with no run fixture.

nf-core/atacseq 2.1.2 and nf-core/chipseq 2.1.0 have no nf-test snapshot and no
usable published results, so the owner waived G-7's fixture for them: their file
rules come from the pinned output docs (committed beside the other nf-core docs
under NessieAI/tests/chat_nextseek/fixtures/nfcore/), and stay unverified until a
real run. test_fixture_runs.py cannot catch a typo'd folder in such a map, and a
rule whose folder is wrong matches nothing on a real run, silently. This file is
the check that is left: every folder a rule's patterns name is a folder the
release's output.md documents.

A pattern's folder is compared with the documented folders segment by segment,
after brace expansion. A documented placeholder segment (`<ALIGNER>`,
`<PEAK_TYPE>`, `<ANTIBODY>`) accepts any segment; every other segment must be
spelled exactly as documented, so a wildcard where the docs name a folder fails
too. Candidates and companions must name a documented folder exactly; an
`unmapped_files` glob may stop above one (its `*` crosses `/`).
"""
import fnmatch
import re
from pathlib import Path

import pytest

from NessieAI.ns.reingest import maps

DOCS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "fixtures" / "nfcore"
README = maps.MAPS_DIR / "README.md"

#: pipeline -> its pinned output docs. Remove an entry when the pipeline's run fixture lands.
DOC_ONLY = {"atacseq": "atacseq.output.md", "chipseq": "chipseq.output.md"}

#: Folders an output.md documents only by saying they mirror another, as {pipeline:
#: {folder: (its documented twin, the sentence of the docs that says so, the subfolders
#: conf/modules.config publishes under it)}}. A pattern's folder under one of those
#: subfolders is checked as the twin's; any other subfolder stays undocumented, so a
#: rule cannot name a merged_replicate/ folder the release never writes. atacseq's
#: "Merged replicate-level analysis" section lists no files of its own; its
#: modules.config publishes these under merged_replicate/ (no preseq/, deeptools/,
#: ataqv/ or sizeFactors/ there).
TWINS = {"atacseq": {"merged_replicate": (
    "merged_library",
    "The analysis steps and directory structure for `<ALIGNER>/merged_library/` and "
    "`<ALIGNER>/merged_replicate/` are almost identical.",
    ("", "bigwig", "bigwig/scale", "picard_metrics", "samtools_stats", "macs2/*",
     "macs2/*/qc", "macs2/*/consensus", "macs2/*/consensus/deseq2"))}}

_PLACEHOLDER = re.compile(r"<[A-Z_]+>")


def documented_folders(text: str) -> list[list[str]]:
    """Every backticked path ending in "/" in an output.md, as segments."""
    return [path.strip("/").split("/") for path in re.findall(r"`([^`\s]+/)`", text)]


def _as_twins(pattern: str, pipeline: str) -> str:
    """`pattern` (brace-expanded already) with a twin folder read as the folder it
    mirrors, when the pattern's folder below it is one the release publishes there."""
    for folder, (twin, _, published) in TWINS.get(pipeline, {}).items():
        match = re.search(rf"(^|/){folder}/", pattern)
        if not match:
            continue
        below = pattern[match.end():].rsplit("/", 1)[0] if "/" in pattern[match.end():] else ""
        if any(fnmatch.fnmatchcase(below, p) and below.count("/") == p.count("/")
               for p in published):
            pattern = pattern[:match.start()] + match.group(1) + twin + "/" + pattern[match.end():]
    return pattern


def _folder(pattern: str) -> list[str]:
    return pattern.rsplit("/", 1)[0].split("/") if "/" in pattern else []


def _segment_ok(ours: str, theirs: str) -> bool:
    return bool(_PLACEHOLDER.fullmatch(theirs)) or ours == theirs


def undocumented(pattern: str, folders: list[list[str]], *, prefix: bool = False) -> list[str]:
    """The brace expansions of `pattern` whose folder no documented folder matches."""
    missing = []
    for expanded in maps.expand_braces(pattern):
        ours = _folder(expanded)
        if not ours:
            continue
        if not any((len(theirs) >= len(ours) if prefix else len(theirs) == len(ours))
                   and all(_segment_ok(o, t) for o, t in zip(ours, theirs))
                   for theirs in folders):
            missing.append(expanded)
    return missing


def _patterns(m: maps.PipelineMap) -> list[str]:
    rules = list(m.outputs) + [r for o in m.versions for r in o.outputs]
    return [p for r in rules for p in [*r.candidates, *(c.glob for c in r.companions)]]


def test_every_doc_only_map_and_its_docs_exist():
    for pipeline, doc in DOC_ONLY.items():
        assert pipeline in maps.available(), f"no committed map for {pipeline}"
        assert (DOCS / doc).is_file(), f"no pinned output docs at {DOCS / doc}"


@pytest.mark.parametrize("pipeline", sorted(DOC_ONLY))
def test_every_rule_folder_is_documented(pipeline):
    folders = documented_folders((DOCS / DOC_ONLY[pipeline]).read_text())
    assert folders, f"{pipeline}: no documented folders read from {DOC_ONLY[pipeline]}"
    m = maps.load(pipeline)
    assert m.outputs, f"{pipeline}: a doc-only map with no output rules checks nothing"
    missing = [p for pattern in _patterns(m) for expanded in maps.expand_braces(pattern)
               for p in undocumented(_as_twins(expanded, pipeline), folders)]
    assert missing == [], f"{pipeline}: folders its output.md does not document: {missing}"


@pytest.mark.parametrize("pipeline", sorted(DOC_ONLY))
def test_every_unmapped_files_folder_is_documented(pipeline):
    folders = documented_folders((DOCS / DOC_ONLY[pipeline]).read_text())
    missing = [p for ruling in maps.load(pipeline).unmapped_files
               for expanded in maps.expand_braces(ruling.glob)
               for p in undocumented(_as_twins(expanded, pipeline), folders, prefix=True)]
    assert missing == [], f"{pipeline}: ruled-out folders its output.md does not document: {missing}"


@pytest.mark.parametrize("pipeline", sorted(TWINS))
def test_every_twin_folder_is_one_the_docs_say_mirrors_another(pipeline):
    text = " ".join((DOCS / DOC_ONLY[pipeline]).read_text().split())
    for folder, (twin, sentence, _) in TWINS[pipeline].items():
        assert sentence in text, f"{pipeline}: the docs no longer say {folder}/ mirrors {twin}/"


@pytest.mark.parametrize("pipeline", sorted(DOC_ONLY))
def test_the_readme_says_the_map_is_unverified(pipeline):
    rows = [line for line in README.read_text().splitlines()
            if line.startswith(f"| {pipeline} |") and "pinned docs/output.md only" in line]
    assert rows, f"README's doc-only map table has no row for {pipeline}"


def test_the_check_catches_a_typo_and_a_wildcard_where_a_folder_is_named():
    folders = documented_folders(
        "- `<ALIGNER>/merged_library/macs2/<PEAK_TYPE>/`\n- `fastqc/zips/`\n")
    assert undocumented("*/merged_library/macs2/narrow_peak/*.narrowPeak", folders) == []
    assert undocumented("*/merged_libary/macs2/narrow_peak/*.narrowPeak", folders) != []
    assert undocumented("*/merged_library/*/narrow_peak/*.narrowPeak", folders) != []
    assert undocumented("*/merged_library/{macs2,macs3}/broad_peak/*.broadPeak", folders) \
        == ["*/merged_library/macs3/broad_peak/*.broadPeak"]
    assert undocumented("*/merged_library/*.bam", folders) != []
    assert undocumented("*/merged_library/*", folders, prefix=True) == []
    assert undocumented("fastqc/*", folders, prefix=True) == []
    assert undocumented(_as_twins("*/merged_replicate/macs2/broad_peak/*.broadPeak", "atacseq"),
                        folders) == []
    assert _as_twins("*/merged_replicate/x", "chipseq") == "*/merged_replicate/x"
    assert _as_twins("*/merged_replicate/preseq/*", "atacseq") == "*/merged_replicate/preseq/*"
    assert _as_twins("*/merged_replicate/*.bam", "atacseq") == "*/merged_library/*.bam"
