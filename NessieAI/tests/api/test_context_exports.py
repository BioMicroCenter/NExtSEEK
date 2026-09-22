"""The committed context exports agree with the curated source (`--emit exports`).

`_fetch_context_files_from_db` rewrites these files once per UTC day from the three context
tables, so the app converges on the database by itself and the committed copies are only its
fallback. The cc-agent has no such path: `NessieAI/docker/cc-runtime/Dockerfile` COPYs three of
them out of the checkout at build time (`startup/lib/layout.py::CANONICAL_CONTEXT_FILES`), the
container holds no database connection, and its own MANIFEST.md sends the agent to
`min_sampletypes_db.json` to map "a kind of sample" to its code. So the committed bytes are what
Container-CC reads for the life of the image.

That is how the curated `Tags` went missing where it mattered. The operator added "mice",
"collaborative cross" and "CC" to the Mouse row; `context/sample_types.json` carried them and the
export did not, so the entity step could not resolve a question about CC mice however the prompt
was worded -- and nothing failed, because the stack-health check `cc-agent context` compares the
checkout with the image, and two stale copies of one file read as green.

These tests are the guard: regenerate with `python scripts/context_gen.py --emit exports
--table all` whenever `context/` changes, and apply `--emit update` to the database in the same
change so all three copies say one thing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts import context_gen  # noqa: E402

CONTEXT = REPO_ROOT / context_gen.CONTEXT_EXPORT_DIR


def _committed(name: str):
    return json.loads((CONTEXT / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(context_gen.EXPORTS))
def test_the_committed_export_is_what_the_generator_writes(name):
    """Byte-for-byte, so a hand edit to an export is a failing test and not a silent drift."""
    table = context_gen.EXPORTS[name][0]
    assert (CONTEXT / name).read_text(encoding="utf-8") == context_gen.render_export(
        name, context_gen.rows_for(table)
    ), f"{name} is not `--emit exports` output; regenerate it"


@pytest.mark.parametrize("name", sorted(context_gen.EXPORTS))
def test_every_export_has_one_row_per_curated_row(name):
    table = context_gen.EXPORTS[name][0]
    assert len(_committed(name)) == len(context_gen.rows_for(table))


def test_the_cc_agent_bakes_exports_this_generator_owns():
    """If a canonical file stops being generated, the cc-agent silently keeps a hand-kept copy."""
    sys.path.insert(0, str(REPO_ROOT / "startup"))
    from startup.lib import layout  # noqa: PLC0415

    baked = set(layout.CANONICAL_CONTEXT_FILES)
    generated = set(context_gen.EXPORTS)
    assert set(context_gen.CC_AGENT_EXPORTS) == baked & generated
    # The rest of the baked set is owned elsewhere, and named here so a new one is noticed.
    assert baked - generated == {
        "capabilities.md", "min_api_endpoints.json", "min_api_endpoints_enriched.json",
    }


def test_the_terms_the_failing_questions_used_reach_the_catalog_the_agent_reads():
    """The whole point: `mixed.rnaseq_files_from_mice` and the collaborative-cross questions.

    The prompt (F15) tells the entity step that `Tags` is the alias list. This asserts the
    aliases are in the file the step is handed, not only in the curated source.
    """
    rows = _committed("min_sampletypes_db.json")
    mouse = next(r for r in rows if r["SampleType"] == "MUS")
    tags = {t.strip().lower() for t in str(mouse["Tags"]).split(",")}
    for term in ("mouse", "mice", "murine", "collaborative cross", "cc"):
        assert term in tags, term


def test_a_project_export_row_carries_no_labs_key():
    """`labs` comes from SEEK's institutions over the export's own connection, at runtime.

    Offline there is nothing to read, so the generator must not invent the key: an empty list
    would read as "this project has no labs" rather than "not resolved yet".
    """
    assert all("labs" not in row for row in _committed("projects_db.json"))


def test_the_projects_export_carries_the_projects_the_parser_lists():
    """F3's failing case: a real project the parser now names must resolve in the catalog too."""
    rows = _committed("projects_db.json")
    by_type = {r["entity_type"] for r in rows}
    assert by_type == {"project", "investigation"}
    names = {str(r["name"]).lower() for r in rows if r["entity_type"] == "project"}
    assert "shoulders" in names
