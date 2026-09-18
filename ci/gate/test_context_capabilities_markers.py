"""The committed capabilities.md carries the generated investigation block, exactly.

`scripts/context_gen.py --emit capabilities` rewrites what sits between the
`<!-- BEGIN CONTEXT-GEN:investigations -->` and `<!-- END ... -->` markers. The
markers were placed by hand, and a reversed pair, a second pair or an END placed
after later sections made that rewrite duplicate text or delete whole sections
while exiting 0. `check_capabilities_markers` says what is wrong; this gate runs it
on the committed file, so a malformed pair cannot be merged.

The text between the markers is generated from the investigation rows of
`context/projects.json` (`render_capabilities_text`), so this gate also pins it to
them, byte for byte: a hand edit between the markers, or a new row without a
regeneration, fails here. It is graph-free: whether each name answers is the counts
refusal's, run by the operator before a merge, and drift's at runtime.

Standard library only, like the generator it imports.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import context_gen as cg  # noqa: E402


def _committed() -> str:
    return (ROOT / cg.CAPABILITIES_PATH).read_text(encoding="utf-8")


def test_the_committed_capabilities_markers_are_well_formed():
    text = _committed()
    assert cg.CAPABILITIES_BEGIN in text and cg.CAPABILITIES_END in text
    assert cg.check_capabilities_markers(text) == []


def test_the_check_is_not_vacuous():
    reversed_pair = (f"{cg.DRIFT_SECTION_HEADING}\n\n{cg.CAPABILITIES_END}\nx\n"
                     f"{cg.CAPABILITIES_BEGIN}\n")
    assert cg.check_capabilities_markers(reversed_pair)


def test_the_block_is_exactly_what_the_curated_rows_render():
    text = _committed()
    block = cg.render_capabilities_text(cg.curated_rows("projects"))
    inside = text.split(cg.CAPABILITIES_BEGIN, 1)[1].split(cg.CAPABILITIES_END, 1)[0]
    assert f"{cg.CAPABILITIES_BEGIN}{inside}{cg.CAPABILITIES_END}\n" == block, (
        "capabilities.md's generated block is not what context/projects.json renders; "
        "regenerate it with scripts/context_gen.py --emit capabilities")
    assert cg.replace_capabilities_block(text, block) == text


def test_the_block_lists_exactly_the_investigation_rows():
    rows = cg.curated_rows("projects")
    expected = sorted((str(r["name"]), r.get("present_on") is None)
                      for r in rows if r["entity_type"] == "investigation")
    assert cg.listed_investigations(_committed()) == expected
