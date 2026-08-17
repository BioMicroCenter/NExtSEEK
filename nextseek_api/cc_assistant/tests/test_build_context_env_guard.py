"""Build-context hygiene: rendered env files (and any ad-hoc copies of them)
must never enter the docker build context.

Motivation: .dockerignore used to exclude only the three EXACT filenames
(docker/db.env, docker/nextseek.env, docker/bedrock-proxy/proxy-secret.env).
Operator backup copies like ``docker/nextseek.env.bak.<date>`` matched nothing
and were swept into the image by ``COPY . /app``. These tests evaluate the
actual .dockerignore rules (last-match-wins, ``**`` and ``!`` semantics) against
representative paths, so the protection is functional, not textual.
Hermetic: stdlib only, no git, no docker.

Lanes: everything here reads .dockerignore, which the image ships, so it runs
everywhere -- except ``test_gitignore_covers_env_bak_copies``, which reads
``<repo>/.gitignore``. .dockerignore strips `.gitignore` from the build
context by design, and REPO_ROOT is ``/app`` inside the image, so that one test
is marked ``host_only``: deselected in the image lane, run in the host lane
(DEPLOYMENT.md:446) and in worktree lanes, which bind-mount a real checkout.
The IMAGE_ABSENT_INPUTS guards at the bottom hold that marker to exactly its
justification, in both directions -- the input really is stripped from the
image, and the marked set is exactly the tests that read one (#89).
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
GITIGNORE = REPO_ROOT / ".gitignore"


def _pattern_to_regex(pattern: str) -> re.Pattern:
    # Minimal .dockerignore glob semantics: '**/' spans any number of
    # directories (including zero), '*' and '?' stop at '/'. A trailing '/'
    # (directory pattern) also matches everything beneath it.
    pat = pattern.strip().rstrip("/")
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif pat[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "(/.*)?$")


def _rules():
    rules = []
    for raw in DOCKERIGNORE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        rules.append((negate, _pattern_to_regex(line[1:] if negate else line)))
    assert rules, ".dockerignore parsed to zero rules"
    return rules


def _excluded(path: str) -> bool:
    verdict = False
    for negate, rx in _rules():
        if rx.match(path):
            verdict = not negate
    return verdict


MUST_BE_EXCLUDED = [
    # the three canonical rendered secret files
    "docker/db.env",
    "docker/nextseek.env",
    "docker/bedrock-proxy/proxy-secret.env",
    "dmac/local_settings.py",
    # the ad-hoc operator copies that actually leaked into an image (2026-07-29)
    "docker/nextseek.env.bak-pre-seekpublicurl",
    "docker/nextseek.env.bak.20260727123629",
    "docker/nextseek.env.bak.budget-20260729113017",
    "docker/db.env.bak",
    "docker/db.env.old",
    # root compose-interpolation env and any nested real .env
    ".env",
    "chat_nextseek/.env",
    # bak copies at arbitrary depth / with suffixes
    "some/deep/dir/foo.env.bak2",
    "nextseek.env.bak.rootlevel",
]

MUST_BE_INCLUDED = [
    # committed templates and examples stay in the build context
    "docker/nextseek.env.example",
    "docker/bedrock-proxy/proxy-secret.env.example",
    "startup/templates/nextseek.env.template",
    "startup/templates/db.env.template",
    "chat_frontend/.env.example",
    "chat_nextseek/.env.example",
    # sanity: ordinary source stays included
    "README.md",
    "dmac/settings.py",
]


@pytest.mark.parametrize("path", MUST_BE_EXCLUDED)
def test_secretish_paths_excluded_from_build_context(path):
    assert _excluded(path), f"{path} would enter the docker build context"


@pytest.mark.parametrize("path", MUST_BE_INCLUDED)
def test_templates_and_source_stay_included(path):
    assert not _excluded(path), f"{path} is wrongly excluded from the build context"


# host_only: reads <repo>/.gitignore, which the `.gitignore` rule in
# .dockerignore keeps out of the image -- do not remove (#89).
@pytest.mark.host_only
def test_gitignore_covers_env_bak_copies():
    lines = [
        ln.strip()
        for ln in GITIGNORE.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for required in (
        "*.env.bak*",
        ".env",
        "docker/nextseek.env",
        "docker/db.env",
        "docker/bedrock-proxy/proxy-secret.env",
    ):
        assert required in lines, f".gitignore lost the `{required}` rule"


# ---------------------------------------------------------------------------
# Drift guard for issue #89 -- tests whose inputs the image does not ship.
#
# .dockerignore strips `.gitignore` and `.claude/` from the build context on
# purpose, so a test that reads either one cannot pass in the image lane
# (`-w /app ... -m "not host_only"`): under /app those paths simply do not
# exist. The fix is `@pytest.mark.host_only`, which `pyproject.toml` registers
# for exactly this case: the image lane deselects such a test, the host lane
# (DEPLOYMENT.md:446, which bind-mounts a real checkout at /repo) runs it.
#
# That is already this directory's uniform convention -- four sibling modules
# carry a module-level `pytestmark = pytest.mark.host_only`
# (test_step7_proxy_port.py, test_step7_sidecar_port.py,
# test_step7_cc_runtime_port.py, test_plugin_container_claude_md.py). The three
# tests guarded here were the outliers. Two of those modules also put a
# `skipif(not <guarded file>.exists())` on one test, which is redundant belt
# inside an already-deselected module rather than a rival mechanism; do not
# copy it here. The marker is a claim about the *lane*, skipif is a claim about
# the thing under test -- delete `.gitignore` from the repo and skipif turns a
# real regression into a green skip in every lane, including the host lane
# whose whole job is to catch it.
#
# Keys are repo-relative input paths; values are the pytest node ids that read
# them, as `<filename>.py::<Class>::<method>` / `<filename>.py::<function>`.
# Filenames only: every module named here is a sibling of this file, and every
# one of them IS shipped in the image -- it is their *inputs* that are not.
IMAGE_ABSENT_INPUTS = {
    ".gitignore": ("test_build_context_env_guard.py::test_gitignore_covers_env_bak_copies",),
    ".claude/skills/nextseek-issues/SKILL.md": (
        "test_issue_conventions_guard.py::TestSkillAndPointers::test_skill_exists_with_frontmatter",
        "test_issue_conventions_guard.py::TestSkillAndPointers::test_skill_hard_rules",
    ),
}

TESTS_DIR = Path(__file__).resolve().parent


def test_image_absent_inputs_are_excluded_from_build_context():
    """The premise behind the host_only markers: the image genuinely lacks these.

    Reads only .dockerignore, which the image does ship, so this runs in every
    lane. If a future .dockerignore edit re-admitted `.gitignore` or `.claude/`,
    the markers below would be over-marking and this test says so.
    """
    leaked = sorted(path for path in IMAGE_ABSENT_INPUTS if not _excluded(path))
    assert not leaked, (
        "IMAGE_ABSENT_INPUTS claims .dockerignore keeps these out of the image, "
        "but they would enter the build context: " + ", ".join(leaked)
    )


def _is_host_only_mark(node) -> bool:
    """True for `pytest.mark.host_only` and `pytest.mark.host_only(...)`."""
    if isinstance(node, ast.Call):
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "host_only"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _module_pytestmark_is_host_only(tree) -> bool:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            continue
        value = stmt.value
        if value is None:
            continue
        marks = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        if any(_is_host_only_mark(m) for m in marks):
            return True
    return False


def _is_unittest_testcase(node) -> bool:
    """True for `class X(TestCase)` / `class X(unittest.TestCase)`.

    pytest collects TestCase subclasses regardless of ``python_classes``, so a
    class named anything at all is still live coverage -- and still capable of
    hiding a mark. Without this, an over-marked
    ``@pytest.mark.host_only class SkillChecks(unittest.TestCase)`` would delete
    image-lane coverage with the complement guard below staying green.
    """
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
        if name == "TestCase":
            return True
    return False


def _guarded_filenames() -> list:
    return sorted(
        {nid.split("::", 1)[0] for ids in IMAGE_ABSENT_INPUTS.values() for nid in ids}
    )


def _host_only_by_node_id(filename: str):
    """Map every test node id in a sibling test module to whether it is host_only.

    Handles all three shapes a mark can take: module-level ``pytestmark``, a
    decorator on the class, and a decorator on a plain function *or on a method
    inside a class*. That last shape is the one
    ``scripts/verify_host_only_allowlist.py`` cannot see, and it is the shape
    two of the three nodes guarded here use.

    Names are filtered by pytest's default collection rules
    (``python_classes = Test*``, ``python_functions = test*``) *plus* the
    unittest exception -- a ``TestCase`` subclass is collected whatever it is
    called -- so the map is the set of collectible tests and not the modules'
    private helpers. Known gap, pre-existing and deliberately not closed here:
    a ``Test*`` class nested inside another class is not walked.

    Returns ``None`` if the module file itself is gone, so a renamed or moved
    *module* reaches the callers' "renamed or moved?" branch instead of raising
    FileNotFoundError from inside the guard.
    """
    path = TESTS_DIR / filename
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_marked = _module_pytestmark_is_host_only(tree)
    funcdefs = (ast.FunctionDef, ast.AsyncFunctionDef)
    marked = {}
    for stmt in tree.body:
        if isinstance(stmt, funcdefs) and stmt.name.startswith("test"):
            marked[f"{filename}::{stmt.name}"] = module_marked or any(
                _is_host_only_mark(d) for d in stmt.decorator_list
            )
        elif isinstance(stmt, ast.ClassDef) and (
            stmt.name.startswith("Test") or _is_unittest_testcase(stmt)
        ):
            class_marked = module_marked or any(
                _is_host_only_mark(d) for d in stmt.decorator_list
            )
            for sub in stmt.body:
                if isinstance(sub, funcdefs) and sub.name.startswith("test"):
                    marked[f"{filename}::{stmt.name}::{sub.name}"] = class_marked or any(
                        _is_host_only_mark(d) for d in sub.decorator_list
                    )
    return marked


def test_tests_reading_image_absent_inputs_are_host_only():
    """Every node id in IMAGE_ABSENT_INPUTS must carry @pytest.mark.host_only.

    AST, not import: this package has no ``__init__.py`` (so there is no dotted
    path to the sibling), and ``test_issue_conventions_guard`` exec's
    ``scripts/validate_issue.py`` at import time -- a side effect this
    assertion has no business triggering.
    """
    expected = sorted({nid for ids in IMAGE_ABSENT_INPUTS.values() for nid in ids})
    marked_by_file = {fn: _host_only_by_node_id(fn) for fn in _guarded_filenames()}

    unmarked, unknown = [], []
    for node_id in expected:
        marked = marked_by_file[node_id.split("::", 1)[0]]
        if marked is None or node_id not in marked:
            unknown.append(node_id)
        elif not marked[node_id]:
            unmarked.append(node_id)

    problems = []
    if unmarked:
        problems.append("missing @pytest.mark.host_only: " + ", ".join(unmarked))
    if unknown:
        problems.append("node id not found, renamed or moved?: " + ", ".join(unknown))
    assert not problems, (
        "these tests read inputs .dockerignore strips from the image, so they "
        "fail the image lane unless deselected (#89) -- " + "; ".join(problems)
    )


def test_no_other_test_in_those_modules_is_host_only():
    """The other half of the guard: nothing in those modules may be OVER-marked.

    Per-test marks only -- never a module-level ``pytestmark``, never a mark on
    ``TestSkillAndPointers``. That per-test-not-per-class call is the whole
    judgment of #89: every test in these two modules *except* the three above
    reads only inputs the image ships and passes in the image lane today,
    including ``TestSkillAndPointers::test_pointers_present``, a sibling method
    of two of the three marked nodes. A module- or class-level mark would delete that
    coverage while ``test_tests_reading_image_absent_inputs_are_host_only``
    stayed green, which makes it the most likely way this fix gets silently
    undone. So assert the complement: the marked set is exactly
    IMAGE_ABSENT_INPUTS, no more.
    """
    intended = {nid for ids in IMAGE_ABSENT_INPUTS.values() for nid in ids}

    problems, over = [], []
    for filename in _guarded_filenames():
        marked = _host_only_by_node_id(filename)
        if marked is None:
            problems.append(f"module not found, renamed or moved?: {filename}")
            continue
        over.extend(
            sorted(
                nid
                for nid, is_marked in marked.items()
                if is_marked and nid not in intended
            )
        )
    if over:
        problems.append(
            "marked @pytest.mark.host_only, but their inputs ARE shipped in the "
            "image -- the mark deletes real image-lane coverage instead of "
            "protecting anything: " + ", ".join(over)
        )
    assert not problems, (
        "over-marked host_only in the IMAGE_ABSENT_INPUTS modules (#89) -- "
        + "; ".join(problems)
    )
