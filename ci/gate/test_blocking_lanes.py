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


def test_the_sample_search_page_tests_block():
    assert "seek/tests/test_sample_search_*.py" in blocking_lanes.BLOCKING_GLOBS
    paths = blocking_lanes.expand(ROOT)
    assert "seek/tests/test_sample_search_js.py" in paths
    assert "seek/tests/test_sample_search_page.py" in paths


def test_the_agent_folder_tests_block():
    assert "NessieAI/tests/cc/test_cc_agent_folders_*.py" in blocking_lanes.BLOCKING_GLOBS
    paths = blocking_lanes.expand(ROOT)
    assert "NessieAI/tests/cc/test_cc_agent_folders_every_folder.py" in paths
    assert "NessieAI/tests/cc/test_cc_agent_folders_entrypoint.py" in paths


def test_the_studies_tool_tests_block():
    assert "nextseek_api/studies/tests/test_*.py" in blocking_lanes.BLOCKING_GLOBS
    paths = blocking_lanes.expand(ROOT)
    assert "nextseek_api/studies/tests/test_buckets.py" in paths
    assert "nextseek_api/studies/tests/test_models.py" in paths


def test_the_entity_tree_tests_block():
    assert "nextseek_api/tests/test_services_entity_tree.py" in blocking_lanes.BLOCKING_GLOBS
    assert "nextseek_api/tests/test_entity_tree_read_routing.py" in blocking_lanes.BLOCKING_GLOBS
    paths = blocking_lanes.expand(ROOT)
    assert "nextseek_api/tests/test_services_entity_tree.py" in paths
    assert "nextseek_api/tests/test_entity_tree_read_routing.py" in paths


def test_the_transcript_recover_tests_block():
    assert "nextseek_api/tests/test_services_cc_transcript_recover.py" in blocking_lanes.BLOCKING_GLOBS
    assert "nextseek_api/tests/test_services_cc_transcript_recover.py" in blocking_lanes.expand(ROOT)


def test_the_assistant_route_tests_block():
    assert "nextseek_api/tests/test_services_cc_assistant_routes.py" in blocking_lanes.expand(ROOT)


def test_the_attributes_tests_block():
    assert "nextseek_api/attributes/tests/test_*.py" in blocking_lanes.BLOCKING_GLOBS
    assert "nextseek_api/attributes/tests/test_views.py" in blocking_lanes.expand(ROOT)


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
    assert "seek/tests/test_sample_search_*.py" in err.getvalue()


def test_the_blocking_job_checks_for_missing_migrations():
    """A model changed without its migration must fail the job, which it could not until task C1.

    Scoped to nextseek_api on purpose. Mezzanine's four apps propose migrations that would be
    written into site-packages, so they can never be clean here, and seek is excluded pending a
    ruling on two out-of-band models declared managed. Both are recorded in ci/CLAUDE.md and in
    docs/superpowers/plans/2026-09-16-ci-coverage-gaps-findings.md.
    """
    raw = (ROOT / ".github" / "workflows" / "ci-pytest.yml").read_text(encoding="utf-8")
    # Comment lines are stripped first: the file's own note explaining why no check used to run
    # contains this exact command, so a bare substring search passes on prose.
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    assert "makemigrations --check --dry-run" in body, (
        "the blocking job runs no migration check, so a model changed without its migration is "
        "invisible to CI"
    )
    assert "--skip-checks" in body, (
        "the migration check must pass --skip-checks: without it Django stops on the empty "
        "CSRF_TRUSTED_ORIGINS system check before it reaches the migration state"
    )


def test_the_blocking_job_runs_the_plugin_bin_dispatch_tests():
    """The Container-CC plugin's shim tests (upload-reingest's write forward among them) run in no
    other lane: the lanes step leaves NessieAI/docker out, and they need the cc-runtime environment.

    A failing one sat unseen until this step was added. The step must run them without letting a
    failure pass: no continue-on-error on it.
    """
    raw = (ROOT / ".github" / "workflows" / "ci-pytest.yml").read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(lines)
    assert "pytest build_context/plugins/nextseek/bin/tests" in body, (
        "no ci-pytest.yml step runs NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/tests"
    )
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "- name: Blocking plugin bin/ dispatch tests")
    step = []
    for line in lines[start + 1:]:
        if line.lstrip().startswith("- "):
            break
        step.append(line)
    assert "working-directory: NessieAI/docker/cc-runtime" in "\n".join(step)
    assert "continue-on-error" not in "\n".join(step)
