"""The review page is generator output or it is nothing: schema, fingerprint, check_page.

`build_report.py` renders a page ONLY from a data object validated against
`.claude/skills/nessie-run-review/schema/review.schema.json`, embeds that data with a fingerprint,
and `check_page.py` fails any page that did not come out of it. Pages used to be hand-written
around the generator's output (big_merge_rebuild's 2026-10-13 dev review was), and nothing noticed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from NessieAI import paths

SKILL = paths.REPO_ROOT / ".claude" / "skills" / "nessie-run-review"
EXAMPLE_RUN = SKILL / "examples" / "run-2026-07-24"
EXAMPLE_TRIAGE = SKILL / "examples" / "triage.json"


def _load(name):
    sys.path.insert(0, str(SKILL / "scripts"))
    spec = importlib.util.spec_from_file_location(f"_nessie_skill_{name}", SKILL / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rd = _load("review_data")
build_report = _load("build_report")
check_page = _load("check_page")


def _build(tmp_path, *extra):
    out = tmp_path / "report.html"
    argv = ["build_report.py", "--run", str(EXAMPLE_RUN), "--repo", str(paths.REPO_ROOT),
            "--triage", str(EXAMPLE_TRIAGE), "--out", str(out), *extra]
    old, sys.argv = sys.argv, argv
    try:
        build_report.main()
    finally:
        sys.argv = old
    return out.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("page"))


@pytest.fixture
def data(page):
    return rd.extract(page)[0]


# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #

def test_the_example_run_builds_valid_data(data):
    assert rd.problems(data) == []
    assert len(data["cases"]) == 44


def test_the_schema_rejects_an_extra_top_level_field(data):
    data["dev_green_section"] = "<div>custom</div>"
    assert any("dev_green_section" in p for p in rd.problems(data))


def test_the_schema_rejects_a_missing_field(data):
    del data["findings"]
    assert any("findings" in p for p in rd.problems(data))


def test_the_schema_rejects_a_bad_verdict_and_a_bad_finding(data):
    data["cases"][0]["verdict"] = "green"
    data["findings"].append({"severity": "real", "title": "t", "body": [], "extra": 1})
    bad = rd.problems(data)
    assert any(p.startswith("cases.0.verdict") for p in bad)
    assert any(p.startswith("findings.") for p in bad)


def test_build_report_writes_nothing_when_the_data_fails(tmp_path, monkeypatch):
    # a stats tone the page does not style is the kind of drift the schema exists to stop
    bad = json.loads(EXAMPLE_TRIAGE.read_text())
    bad["stats"] = [{"n": 1, "label": "x", "tone": "green"}]
    tri = tmp_path / "t.json"
    tri.write_text(json.dumps(bad))
    out = tmp_path / "r.html"
    monkeypatch.setattr(sys, "argv", ["build_report.py", "--run", str(EXAMPLE_RUN), "--repo",
                                      str(paths.REPO_ROOT), "--triage", str(tri), "--out", str(out)])
    with pytest.raises(SystemExit) as e:
        build_report.main()
    assert e.value.code == 2 and not out.exists()


# --------------------------------------------------------------------------- #
# check_page
# --------------------------------------------------------------------------- #

def test_check_page_passes_generator_output_fragment_and_standalone(page, tmp_path):
    assert check_page.check(page) == []
    assert check_page.check(_build(tmp_path, "--standalone")) == []


def test_check_page_fails_a_hand_written_page():
    errs = check_page.check("<div class='wrap'><h1>Dev review</h1></div><script>const CASES=[];</script>")
    assert errs and "not generator output" in errs[0]


def test_check_page_fails_appended_markup(page):
    assert check_page.check(page + "<section>dev green</section>")


def test_check_page_fails_data_edited_after_the_build(page):
    edited = page.replace('"verdict":"real"', '"verdict":"pass"', 1)
    assert edited != page
    errs = check_page.check(edited)
    assert errs and "fingerprint" in errs[0]


def test_check_page_fails_a_copied_data_block_in_a_hand_written_page(page):
    data_block = rd.DATA_RE.search(page).group(0)
    fp_block = rd.FP_RE.search(page).group(0)
    assert check_page.check(f"<div>my own layout</div>{data_block}{fp_block}")


def test_a_closing_script_tag_in_a_note_cannot_break_the_page(tmp_path):
    bad = json.loads(EXAMPLE_TRIAGE.read_text())
    bad["reframe"] = "x </script><b>y</b>"
    tri = tmp_path / "t.json"
    tri.write_text(json.dumps(bad))
    out = tmp_path / "r.html"
    old, sys.argv = sys.argv, ["build_report.py", "--run", str(EXAMPLE_RUN), "--repo",
                               str(paths.REPO_ROOT), "--triage", str(tri), "--out", str(out)]
    try:
        build_report.main()
    finally:
        sys.argv = old
    html = out.read_text()
    assert check_page.check(html) == []
    assert rd.extract(html)[0]["meta"]["reframe"] == "x </script><b>y</b>"


def test_rehydrate_reads_the_new_page(page, tmp_path):
    rehydrate = _load("rehydrate_report")
    assert [c["id"] for c in rehydrate._literal(page, "CASES")][:1]
    assert rehydrate._literal(page, "META")["headline"].startswith("44 cases")
