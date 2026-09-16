"""The blocking test lanes, in the blocking gate step.

ci/blocking_lanes.py names, by glob, the unit tests whose failure fails
ci-pytest.yml (its "Blocking unit tests (ci/blocking_lanes.py)" step). A glob that
matches nothing would block on nothing and still look green, so every glob must
match at least one file, and an empty expansion is an error, never an empty
argument list: `pytest` with no paths walks the whole tree.

Standard library only, like the module it tests.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ci import blocking_lanes  # noqa: E402


def test_every_glob_matches_at_least_one_file():
    assert blocking_lanes.unmatched(ROOT) == [], (
        "a blocking glob matches no file: rename the tests to fit it or change the glob in ci/blocking_lanes.py"
    )


def test_the_graph_search_page_tests_block():
    assert "seek/tests/test_graph_search_*.py" in blocking_lanes.BLOCKING_GLOBS
    paths = blocking_lanes.expand(ROOT)
    assert "seek/tests/test_graph_search_js.py" in paths
    assert "seek/tests/test_graph_search_page.py" in paths


def test_the_expansion_is_sorted_relative_test_modules():
    paths = blocking_lanes.expand(ROOT)
    assert paths == sorted(set(paths))
    for p in paths:
        assert not p.startswith("/"), p
        assert Path(p).name.startswith("test_") and p.endswith(".py"), p
        assert (ROOT / p).is_file(), p


def test_main_prints_one_path_per_line():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = blocking_lanes.main(ROOT)
    assert rc == 0
    assert out.getvalue().splitlines() == blocking_lanes.expand(ROOT)


def test_main_fails_and_prints_no_path_when_a_glob_matches_nothing():
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as empty:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = blocking_lanes.main(Path(empty))
    assert rc == 1
    assert out.getvalue() == ""
    assert "seek/tests/test_graph_search_*.py" in err.getvalue()
