"""The docs map check, in the blocking gate step.

Runs ci/docs_map.py over this checkout. Locally it skips when git cannot read the
checkout (the read-only gate container may have no git binary, or git may refuse
the mount's ownership). Under GitHub Actions it never skips.

On the host, `python3 ci/docs_map.py` prints the same list faster.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ci import docs_map  # noqa: E402


def _run_or_skip():
    try:
        return docs_map.run(ROOT)
    except docs_map.GitUnavailable as exc:
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail(f"git must be able to read the checkout under GitHub Actions: {exc}")
        pytest.skip(f"git cannot read this checkout here: {exc}")


def test_docs_map_is_clean():
    failures = _run_or_skip()
    assert not failures, (
        "\n\n" + docs_map.format_failures(failures) + "\n\nRun python3 ci/docs_map.py on the host for the same list.\n"
    )


def test_heading_numbers_are_read_outside_code_fences():
    text = "## 3. Redeploying\n### 3.2 What change\n```\n# 9. not a heading\n```\n### 2b. Neo4j\n"
    numbers, titles = docs_map.headings(text)
    assert numbers == {"3", "3.2", "2b"}
    assert "what change" in titles


def test_section_citations_are_found_in_lists():
    found = list(docs_map.section_citations("see `DEPLOYMENT.md` §3, §5 and §6.2."))
    assert found == [("DEPLOYMENT.md", "3"), ("DEPLOYMENT.md", "5"), ("DEPLOYMENT.md", "6.2")]


def test_placeholders_and_commands_are_not_paths():
    line = "run `docker cp a b`, edit `NessieAI/docker/<name>/`, read `dmac/settings.py:12`"
    assert list(docs_map.path_tokens(line)) == [("dmac/settings.py", (12, 12), False)]


def test_table_pipes_inside_backticks_do_not_split_cells():
    assert docs_map.split_row("| `a|b` | c |") == ["`a|b`", "c"]


def _fixture_checker(files):
    tree = object.__new__(docs_map.Tree)
    tree.files = set(files)
    tree.dirs = {"/".join(p.split("/")[:i]) for p in files for i in range(1, len(p.split("/")))}
    return docs_map.Checker(tree)


def test_r11_real_tree_has_no_readme_gap():
    failures = [f for f in _run_or_skip() if f.rule == "R11"]
    assert not failures, docs_map.format_failures(failures)


def test_r11_flags_a_bare_folder_and_a_stale_exempt_pattern():
    checker = _fixture_checker(["a/README.md", "a/b/x.py", "a/c/README.md", "a/c/y.py", "a/d/z.py"])
    docs_map.check_readmes(checker, exempt=(("a/d", "leaf"), ("gone/*", "stale")))
    got = {(f.where, f.message.split(";")[0]) for f in checker.failures}
    assert ("a/b", "folder has no README.md") in got
    assert any(w == "ci/docs_map.py" and "'gone/*'" in m for w, m in got)
    assert all(w not in ("a", "a/c", "a/d") for w, _ in got)
