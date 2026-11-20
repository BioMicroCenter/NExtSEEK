"""Q1 (2026-11-20): a Container-CC reply names a handed-over file by its name and says it is in the downloads
under the reply. The server's scratch folder (under /dmac/users, the app container's volume) is not shown: no user
can open it. ``drop_scratch_paths`` runs after ``rewrite_container_paths``. Invented paths only."""
from __future__ import annotations

import pytest

from NessieAI.cc.cc_engine import drop_scratch_paths

ROOT = "/dmac/users/p/u/scratch/run-1"
MAP = {"scratch": {"container_root": "/data/scratch", "logical_root": ROOT},
       "output": {"container_root": "/data/output", "logical_root": "/dmac/users/p/u/output"}}


@pytest.mark.parametrize("before,after", [
    (f"x.csv, in the downloads under this reply (`{ROOT}/`).", "x.csv, in the downloads under this reply."),
    (f"Its path is `{ROOT}/x.png`.", "Its path is `x.png`."),
    (f"(`{ROOT}/sub/y.csv`)", "(`sub/y.csv`)"),
    (f"It's also at `{ROOT}/`.", "It's also at the downloads under this reply."),
])
def test_the_scratch_folder_is_dropped_and_the_file_name_kept(before, after):
    assert drop_scratch_paths(before, MAP) == after


@pytest.mark.parametrize("text", [
    "See /dmac/users/p/u/output/r.xlsx",
    "Another turn's /dmac/users/p/u/scratch/run-2/x.csv",
    "No path here.",
])
def test_other_paths_and_plain_text_are_unchanged(text):
    assert drop_scratch_paths(text, MAP) == text


def test_a_turn_with_no_scratch_mapping_or_a_non_string_is_unchanged():
    assert drop_scratch_paths(f"{ROOT}/x.csv", {"output": MAP["output"]}) == f"{ROOT}/x.csv"
    assert drop_scratch_paths(None, MAP) is None
    assert drop_scratch_paths("x", None) == "x"


# Round 2 F12: a bare folder in bold, quotes, <> or a link's text is still the server's folder.
@pytest.mark.parametrize("text", [f"Saved in **{ROOT}**.", f'Saved in "{ROOT}".', f"Saved in '{ROOT}/'.",
                                  f"Saved in <{ROOT}>.", f"Saved in [{ROOT}].", f"Saved in **`{ROOT}/`**."])
def test_a_bare_folder_in_bold_or_quotes_is_not_shown(text):
    out = drop_scratch_paths(text, MAP)
    assert ROOT not in out and "the downloads under this reply" in out
