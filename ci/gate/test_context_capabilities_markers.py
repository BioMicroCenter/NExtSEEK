"""The committed capabilities.md carries no malformed CONTEXT-GEN markers.

`scripts/context_gen.py --emit capabilities` rewrites what sits between the
`<!-- BEGIN CONTEXT-GEN:investigations -->` and `<!-- END ... -->` markers. The
markers are placed by hand, and a reversed pair, a second pair or an END placed
after later sections made that rewrite duplicate text or delete whole sections
while exiting 0. `check_capabilities_markers` says what is wrong; this gate runs it
on the committed file, so a malformed pair cannot be merged. A file with no markers
passes: where they go is decided with the investigation rows (plan task 6.15c).

Standard library only, like the generator it imports.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import context_gen as cg  # noqa: E402


def test_the_committed_capabilities_markers_are_well_formed():
    text = (ROOT / cg.CAPABILITIES_PATH).read_text(encoding="utf-8")
    assert cg.check_capabilities_markers(text) == []


def test_the_check_is_not_vacuous():
    reversed_pair = (f"{cg.DRIFT_SECTION_HEADING}\n\n{cg.CAPABILITIES_END}\nx\n"
                     f"{cg.CAPABILITIES_BEGIN}\n")
    assert cg.check_capabilities_markers(reversed_pair)
