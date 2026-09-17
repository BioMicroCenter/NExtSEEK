#!/usr/bin/env python3
"""Check the documentation map against the tree.

    python3 ci/docs_map.py            # from the repo root; needs only python3 and git

Exit 0 when clean. Otherwise exit 1 and print one line per problem, naming the
file, the rule and what to add or change. ci/gate/test_docs_map.py runs the same
check inside the blocking gate step of .github/workflows/ci-pytest.yml.

Rules:
  R1  each DOCS-MAP:folders block names exactly the tracked subfolders of its folder
  R2  every tracked SKILL.md has a row in the root DOCS-MAP:skills block
  R3  docs/INDEX.md, docs/archive/INDEX.md, NessieAI/README.md and
      NessieAI/history/INDEX.md list what their folders hold; root .md files are limited
  R4  relative links and backticked repo paths in live docs resolve
  R5  `FILE` §N and `FILE` "Heading" name a real heading; no line anchors in map
      files, and none past the end of a file anywhere else
  R6  every README.md and CLAUDE.md is linked from another live doc, and every
      CLAUDE.md has a README.md beside it
  R7  the root CLAUDE.md: size cap, #N on standing-plan bullets, startup verbs,
      area labels
  R8  literals that guard tests pin in the root docs
  R9  fenced commands in live docs use no retired form
  R10 no emails, personal home paths or tracked session notes

A rule whose subject does not exist yet (a DOCS-MAP block, the NessieAI tree) is
skipped, except that once NessieAI/README.md is tracked the root CLAUDE.md must
carry all three DOCS-MAP blocks.

Not covered: prose that is wrong while every path in it resolves.
"""
from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MASTER = "CLAUDE.md"
MASTER_MAX_LINES = 160
NESSIE_README = "NessieAI/README.md"

# Live docs are tracked *.md files outside these trees. History and archives are
# frozen; vendored, generated and image-baked docs belong to something else; dated
# specs keep the paths they were written with.
EXCLUDED_PREFIXES = (
    "NessieAI/history/",
    "docs/archive/",
    "docs/superpowers/",
    "NessieAI/hibayes/fit/vendor/",
    # Verbatim copies of nf-core's own README/usage/output pages, pinned as test
    # fixtures. Their relative links point into the upstream repo (images/,
    # CITATIONS.md) and must stay byte-identical to what the pipelines publish --
    # "fixing" a link would corrupt the fixture the tests assert against.
    "NessieAI/tests/chat_nextseek/fixtures/nfcore/",
    "NessieAI/docker/cc-runtime/docs/",
    "NessieAI/docker/cc-runtime/build_context/",
    "NessieAI/docker/cc-runtime/container/",
    # The same trees at their locations before the NessieAI move.
    "nextseek_api/eval/fit/vendor/",
    "docker/cc-runtime/docs/",
    "docker/cc-runtime/build_context/",
    "docker/cc-runtime/container/",
    "nextseek_api/cc_assistant/archive/",
    "nextseek_api/cc_assistant/evidence/",
    "nextseek_api/cc_assistant/acceptance_evidence/",
    "nextseek_api/assistant/tests/acceptance_evidence/",
    "evidence/",
    ".superpowers/",
)

# Map files carry no line anchors at all: they are read first and edited most.
MAP_FILES = {
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "docs/INDEX.md",
    "NessieAI/README.md",
    "NessieAI/CLAUDE.md",
}

ROOT_MD_ALLOWED = {"README.md", "CLAUDE.md", "AGENTS.md", "DEPLOYMENT.md", "NExtSTEPS.md"}
# Moves to NessieAI/docs/ with the NessieAI move; allowed at the root until then.
ROOT_MD_ALLOWED_BEFORE_MOVE = {"architecture.md"}

# A CLAUDE.md with no README.md beside it. Everything else needs the pair.
CLAUDE_WITHOUT_README = {"CLAUDE.md"}

# Top-level names that stop existing with the NessieAI move and the archive pass.
# A backticked path starting with one is checked, so a stale path is reported.
RETIRED_TOP_LEVEL = {
    "chat_nextseek",
    "chat_frontend",
    "dmac_assistant",
    "nessie_tests",
    "build_tools",
    "evidence",
    "data",
    "testquestions-2026-08-07",
    "prod_publication_transfer",
    "sample_publication_attributes",
    ".superpowers",
}

# Untracked runtime directories that docs may name.
RUNTIME_DIRS = ("logs/", "outputs/", "schema_rag/")

# Literals that guard tests pin. Change a literal and its test in the same commit:
#   test_issue_conventions_guard.py (nextseek_api/tests/repo_guards/),
#   test_future_op_dropin.py (NessieAI/tests/cc/),
#   nextseek_api/tests/test_viewset_conventions.py,
#   test_deploy_docs_guard.py (NessieAI/tests/cc/).
# Before the NessieAI move all three unqualified files sit in nextseek_api/cc_assistant/tests/.
PINNED = {
    "CLAUDE.md": ("ISSUE-CONVENTIONS.md", "validate_issue.py", "/add-cc-op"),
    "AGENTS.md": (
        "ISSUE-CONVENTIONS.md",
        "validate_issue.py",
        "/add-cc-op",
        "nextseek-viewset",
        "validate_viewset_conventions.py",
    ),
    "DEPLOYMENT.md": ("./startup.sh install",),
}
DEPLOY_MD_BEFORE_MOVE = "nextseek_api/cc_assistant/DEPLOY.md"
DEPLOY_MD_AFTER_MOVE = "NessieAI/cc/DEPLOY.md"
MUST_EXIST = ("README.md",)

# Append-only: (pattern, what to write instead). Checked against fenced code in live docs.
RETIRED_ALWAYS: list[tuple[str, str]] = []
RETIRED_AFTER_MOVE: list[tuple[str, str]] = [
    (r"\bcd chat_frontend\b", "cd NessieAI/chat_frontend"),
    (r"\bpytest nessie_tests/", "pytest NessieAI/tests/nessie_tests/"),
    (r"\bpytest nextseek_api/(cc_assistant|eval)/", "pytest NessieAI/tests/<area>/"),
    (r"python3? -m (nessie_tests|build_tools|nextseek_api\.cc_assistant)\b", "python -m NessieAI.<package>"),
    (r"\buv run e2e\.py\b", "python -m NessieAI.tests.e2e"),
    (r"\bdocker cp nessie_tests ", "docker cp NessieAI/tests/nessie_tests nextseek:/app/NessieAI/tests/"),
    (r"sync_chat_nextseek\.sh", "nothing: chat_nextseek is edited in place"),
]

EMAIL_ALLOWED = {"git@github.com"}
HOME_ALLOWED = {"service-account", "apache", "user"}

FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HEADING_NUM_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)*[a-z]?)[.)]?(?:\s|$)")
CODE_RE = re.compile(r"`([^`\n]+)`")
LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
ANCHOR_RE = re.compile(r"^(?P<path>[^:]+?):(?P<a>\d+)(?:-(?P<b>\d+))?$")
SECTION_RE = re.compile(
    r"`?(?P<file>[A-Za-z0-9_./-]+\.md)`?"
    r"(?P<tail>(?:\s*(?:,|and|or)?\s*§\s*[0-9]+(?:\.[0-9]+)*[a-z]?)+)"
)
SECTION_NUM_RE = re.compile(r"§\s*([0-9]+(?:\.[0-9]+)*[a-z]?)")
QUOTED_RE = re.compile(r"`(?P<file>[A-Za-z0-9_./-]+\.md)`\s+\"(?P<title>[^\"]+)\"")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOME_RE = re.compile(r"/(?:home|Users)/([A-Za-z0-9_.-]+)(?=[/`'\"\s),]|$)")
SESSION_RE = re.compile(r"^##\s+session reports", re.IGNORECASE | re.MULTILINE)
COMMAND_RE = re.compile(r"@app\.command\((?:\s*name\s*=\s*\"([^\"]+)\"\s*)?\)\s*\n\s*def\s+(\w+)")
AREA_RE = re.compile(r"`area: ([a-z0-9_-]+)`")
ISSUE_REF_RE = re.compile(r"#\d+")
PLACEHOLDER_CHARS = set(" <>*{}$|\\\"'()[],;=")
# A line that says its path is gone is describing history, not pointing at a file.
GONE_RE = re.compile(
    r"no longer exists?|does not exist|doesn't exist|is gone|never existed|never committed|was deleted|were deleted",
    re.IGNORECASE,
)
STATUS_BULLETS_WITHOUT_ISSUE = ("- Boards:", "- Commands:", "- Session reports")


class GitUnavailable(RuntimeError):
    """git cannot read this checkout (not installed, not a repo, refused ownership)."""


@dataclass(frozen=True, order=True)
class Failure:
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule} {self.where}: {self.message}"


def _git(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitUnavailable(str(exc)) from exc


class Tree:
    """The tracked files of one checkout, and their text on disk."""

    def __init__(self, root: Path):
        self.root = root
        proc = _git(root, "ls-files", "-z")
        if proc.returncode != 0:
            raise GitUnavailable(proc.stderr.strip() or "git ls-files failed")
        self.files = {p for p in proc.stdout.split("\0") if p}
        self.dirs: set[str] = set()
        for path in self.files:
            parts = path.split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/".join(parts[:i]))
        self.top = {p.split("/")[0] for p in self.files}
        self._text: dict[str, str] = {}
        self._ignored: dict[str, bool] = {}

    def has(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    def text(self, path: str) -> str:
        if path not in self._text:
            try:
                self._text[path] = (self.root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._text[path] = ""
        return self._text[path]

    def line_count(self, path: str) -> int:
        text = self.text(path)
        return text.count("\n") + (1 if text and not text.endswith("\n") else 0)

    def subdirs(self, folder: str) -> set[str]:
        prefix = f"{folder}/" if folder else ""
        out = set()
        for path in self.files:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                if "/" in rest:
                    out.add(rest.split("/")[0])
        return out

    def ignored(self, paths: list[str]) -> set[str]:
        todo = sorted({p for p in paths if p not in self._ignored})
        if todo:
            proc = _git(self.root, "check-ignore", "--no-index", "--stdin", stdin="\n".join(todo) + "\n")
            if proc.returncode in (0, 1):
                hits = set(proc.stdout.splitlines())
                for path in todo:
                    self._ignored[path] = path in hits
            else:
                # One bad path (for example one beyond a symlink) aborts the whole batch; ask singly.
                for path in todo:
                    self._ignored[path] = _git(self.root, "check-ignore", "--no-index", "-q", path).returncode == 0
        return {p for p in paths if self._ignored.get(p)}


def lines(text: str):
    """Yield (line number, line, inside a code fence)."""
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            yield number, line, True
            fenced = not fenced
            continue
        yield number, line, fenced


def headings(text: str) -> tuple[set[str], set[str]]:
    """The section numbers and the lower-cased titles of every heading outside fences."""
    numbers, titles = set(), set()
    for _, line, fenced in lines(text):
        if fenced:
            continue
        match = HEADING_RE.match(line)
        if not match:
            continue
        title = match.group(2).replace("`", "").strip()
        titles.add(title.lower())
        number = HEADING_NUM_RE.match(title)
        if number:
            numbers.add(number.group(1))
            titles.add(title[number.end():].strip().lower())
    return numbers, titles


def section_citations(line: str):
    """Yield (file, section) for every `FILE` §N, including lists like §3, §5 and §6."""
    for match in SECTION_RE.finditer(line):
        for number in SECTION_NUM_RE.findall(match.group("tail")):
            yield match.group("file"), number


def split_row(line: str) -> list[str]:
    """Split a markdown table row on pipes that sit outside backticks."""
    cells, current, in_code = [], [], False
    for char in line.strip().strip("|"):
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def block(text: str, name: str) -> str | None:
    begin = f"<!-- BEGIN DOCS-MAP:{name} -->"
    end = f"<!-- END DOCS-MAP:{name} -->"
    if begin not in text or end not in text:
        return None
    return text.split(begin, 1)[1].split(end, 1)[0]


def table_rows(block_text: str) -> list[list[str]]:
    rows = []
    for line in block_text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows[1:]  # drop the header row


def path_tokens(line: str):
    """Yield (token, anchor, bare) for each backticked token that looks like a path."""
    for match in CODE_RE.finditer(line):
        token = match.group(1).strip()
        if "..." in token or token.startswith(("/", "~", "-", "http:", "https:", "#", "@")):
            continue
        anchor = None
        found = ANCHOR_RE.match(token)
        if found:
            token = found.group("path")
            first = int(found.group("a"))
            anchor = (first, int(found.group("b") or first))
        if ":" in token or any(c in PLACEHOLDER_CHARS for c in token):
            continue
        if token.startswith("./"):
            token = token[2:]
        bare = "/" not in token
        if bare and not (anchor and "." in token):
            continue
        yield token, anchor, bare


def live_docs(tree: Tree) -> list[str]:
    return sorted(p for p in tree.files if p.endswith(".md") and not p.startswith(EXCLUDED_PREFIXES))


def moved(tree: Tree) -> bool:
    return NESSIE_README in tree.files


class Checker:
    def __init__(self, tree: Tree):
        self.tree = tree
        self.failures: list[Failure] = []
        self.references: dict[str, set[str]] = {}
        self.unresolved: list[tuple[str, str]] = []

    def fail(self, rule: str, where: str, message: str) -> None:
        self.failures.append(Failure(rule, where, message))

    # R4, and the reference graph that R6 reads
    def check_paths(self, doc: str) -> None:
        tree = self.tree
        doc_dir = posixpath.dirname(doc)
        for number, line, fenced in lines(tree.text(doc)):
            if fenced:
                continue
            where = f"{doc}:{number}"
            for target in LINK_RE.findall(line):
                if target.startswith(("http:", "https:", "mailto:", "#")):
                    continue
                clean = posixpath.normpath(posixpath.join(doc_dir, target.split("#", 1)[0]))
                if tree.has(clean.rstrip("/")):
                    self.references.setdefault(clean.rstrip("/"), set()).add(doc)
                else:
                    self.fail("R4", where, f"link target does not exist: {target}")
            for token, anchor, bare in path_tokens(line):
                if bare:
                    if token in tree.files:
                        self.references.setdefault(token, set()).add(doc)
                    continue
                clean = token.rstrip("/")
                relative = posixpath.normpath(posixpath.join(doc_dir, clean)) if doc_dir else clean
                if tree.has(clean):
                    self.references.setdefault(clean, set()).add(doc)
                    continue
                if tree.has(relative):
                    self.references.setdefault(relative, set()).add(doc)
                    continue
                first = clean.split("/")[0]
                if first not in tree.top and first not in RETIRED_TOP_LEVEL:
                    continue  # an in-image path, a URL fragment or a module path
                if token.startswith(RUNTIME_DIRS):
                    continue
                if GONE_RE.search(line) or f"branch `{token}`" in line:
                    continue  # the line says the path is gone, or names a branch
                if clean.endswith(".md"):
                    self.fail("R4", where, f"path is not a tracked file: {token}")
                else:
                    self.unresolved.append((where, token))

    def flush_unresolved(self) -> None:
        # Ask for both spellings: a directory-only rule (`dir/`) matches only the second.
        queries = []
        for _, token in self.unresolved:
            queries += [token.rstrip("/"), token.rstrip("/") + "/"]
        ignored = self.tree.ignored(queries)
        for where, token in self.unresolved:
            if not {token.rstrip("/"), token.rstrip("/") + "/"} & ignored:
                self.fail("R4", where, f"path does not exist and is not gitignored: {token}")

    # R5
    def check_anchors(self, doc: str) -> None:
        tree = self.tree
        doc_dir = posixpath.dirname(doc)

        def locate(name: str) -> str | None:
            for candidate in (name, posixpath.normpath(posixpath.join(doc_dir, name))):
                if candidate in tree.files:
                    return candidate
            return None

        for number, line, fenced in lines(tree.text(doc)):
            if fenced:
                continue
            where = f"{doc}:{number}"
            for name, section in section_citations(line):
                target = locate(name)
                if target is None:
                    continue  # R4 reports a missing file
                numbers, _ = headings(tree.text(target))
                if section not in numbers:
                    self.fail("R5", where, f"{name} has no heading numbered {section}")
            for match in QUOTED_RE.finditer(line):
                target = locate(match.group("file"))
                if target is None:
                    continue
                _, titles = headings(tree.text(target))
                if match.group("title").strip().lower() not in titles:
                    self.fail("R5", where, f'{match.group("file")} has no heading "{match.group("title")}"')
            for token, anchor, bare in path_tokens(line):
                if anchor is None:
                    continue
                if doc in MAP_FILES:
                    self.fail("R5", where, f"line anchor in a map file; cite a symbol or a section: {token}")
                    continue
                target = locate(token)
                if target and anchor[1] > tree.line_count(target):
                    self.fail("R5", where, f"{token}:{anchor[1]} is past the end ({tree.line_count(target)} lines)")

    # R9 and R10 for one doc
    def check_hygiene(self, doc: str, retired: list[tuple[str, str]]) -> None:
        for number, line, fenced in lines(self.tree.text(doc)):
            where = f"{doc}:{number}"
            if fenced:
                for pattern, instead in retired:
                    if re.search(pattern, line):
                        self.fail("R9", where, f"retired command form; write {instead}")
            for email in EMAIL_RE.findall(line):
                if email not in EMAIL_ALLOWED:
                    self.fail("R10", where, "an email address in a public doc")
            for name in HOME_RE.findall(line):
                if name not in HOME_ALLOWED:
                    self.fail("R10", where, "a personal home path in a public doc; describe it instead")


def check_folder_blocks(checker: Checker, docs: list[str]) -> None:
    tree = checker.tree
    for doc in docs:
        text = tree.text(doc)
        folders = block(text, "folders")
        if folders is None:
            continue
        folder = posixpath.dirname(doc)
        named = {}
        for cells in table_rows(folders):
            found = CODE_RE.search(cells[0]) if cells else None
            if found:
                named[found.group(1).strip().strip("/")] = cells
        actual = tree.subdirs(folder)
        width = len(table_rows(folders)[0]) if table_rows(folders) else 2
        for missing in sorted(actual - set(named)):
            row = "| `" + missing + "/` |" + " <what it does> |" * (width - 1)
            checker.fail("R1", doc, f"no row for {folder + '/' if folder else ''}{missing}/; add: {row}")
        for extra in sorted(set(named) - actual):
            checker.fail("R1", doc, f"row names {extra}/, which has no tracked files; delete the row")


def check_skills(checker: Checker, master_text: str) -> None:
    tree = checker.tree
    skills = block(master_text, "skills")
    if skills is None:
        return
    for path in sorted(p for p in tree.files if p.endswith("/SKILL.md")):
        if path.startswith(EXCLUDED_PREFIXES) or path.startswith(("NessieAI/docker/", "docker/cc-runtime/")):
            continue
        text = tree.text(path)
        match = re.search(r"^name:\s*(\S+)\s*$", text.split("---", 2)[1] if text.startswith("---") else "", re.M)
        if not match:
            checker.fail("R2", path, "no frontmatter name")
            continue
        name = match.group(1)
        if f"`{name}`" not in skills:
            checker.fail("R2", MASTER, f"no skills row for {name}; add: | `{name}` | <use when> | `{path}` | <auto or by path> |")
        if path.startswith(".claude/skills/") and path.split("/")[2] != name:
            checker.fail("R2", path, f"directory name must equal the frontmatter name {name}")


def check_indexes(checker: Checker) -> None:
    tree = checker.tree
    if "docs/INDEX.md" in tree.files:
        index = tree.text("docs/INDEX.md")
        for path in sorted(tree.files):
            parts = path.split("/")
            if parts[0] != "docs" or len(parts) < 2:
                continue
            if len(parts) == 2 and path.endswith(".md") and parts[1] != "INDEX.md" and parts[1] not in index:
                checker.fail("R3", "docs/INDEX.md", f"no row for {path}")
        for sub in sorted(tree.subdirs("docs")):
            if f"{sub}/" not in index:
                checker.fail("R3", "docs/INDEX.md", f"docs/{sub}/ is not mentioned")
    if "docs/archive/INDEX.md" in tree.files:
        index = tree.text("docs/archive/INDEX.md")
        for path in sorted(p for p in tree.files if p.startswith("docs/archive/") and p != "docs/archive/INDEX.md"):
            rel = path[len("docs/archive/"):]
            parts = rel.split("/")
            ancestors = ["/".join(parts[:i]) + "/" for i in range(2, len(parts))]
            if rel not in index and not any(a in index for a in ancestors):
                checker.fail("R3", "docs/archive/INDEX.md", f"no row for {rel}")
    if moved(tree):
        index = tree.text(NESSIE_README)
        for path in sorted(p for p in tree.files if p.startswith("NessieAI/docs/") and p.endswith(".md")):
            if path not in index:
                checker.fail("R3", NESSIE_README, f"no sub-doc row for {path}")
        for unit in sorted(tree.subdirs("NessieAI")):
            readme = f"NessieAI/{unit}/README.md"
            if readme in tree.files and readme not in index and unit != "history":
                checker.fail("R3", NESSIE_README, f"no sub-doc row for {readme}")
        history = "NessieAI/history/INDEX.md"
        if tree.subdirs("NessieAI/history"):
            if history not in tree.files:
                checker.fail("R3", history, "NessieAI/history/ has no INDEX.md")
            else:
                text = tree.text(history)
                for sub in sorted(tree.subdirs("NessieAI/history")):
                    if f"{sub}/" not in text:
                        checker.fail("R3", history, f"no row for {sub}/")
    allowed = ROOT_MD_ALLOWED | (set() if moved(tree) else ROOT_MD_ALLOWED_BEFORE_MOVE)
    for path in sorted(p for p in tree.files if "/" not in p and p.endswith(".md")):
        if path not in allowed:
            checker.fail("R3", path, "only " + ", ".join(sorted(ROOT_MD_ALLOWED)) + " live at the root; move it")


def check_orphans(checker: Checker, docs: list[str]) -> None:
    tree = checker.tree
    for doc in docs:
        base = posixpath.basename(doc)
        if base not in ("README.md", "CLAUDE.md") or "/" not in doc:
            continue
        linked_from = checker.references.get(doc, set()) - {doc}
        if not linked_from:
            checker.fail("R6", doc, "no other live doc links it; add it to its parent map or sub-doc table")
        if base == "CLAUDE.md" and doc not in CLAUDE_WITHOUT_README:
            if posixpath.join(posixpath.dirname(doc), "README.md") not in tree.files:
                checker.fail("R6", doc, "a CLAUDE.md needs a README.md beside it")


def check_master(checker: Checker, master_text: str) -> None:
    tree = checker.tree
    status = block(master_text, "status")
    if status is None:
        return
    count = tree.line_count(MASTER)
    if count > MASTER_MAX_LINES:
        checker.fail("R7", MASTER, f"{count} lines; the cap is {MASTER_MAX_LINES}. Move detail to the doc that owns it")
    for line in status.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and not stripped.startswith(STATUS_BULLETS_WITHOUT_ISSUE):
            if not ISSUE_REF_RE.search(stripped):
                checker.fail("R7", MASTER, f"standing-plan bullet without an issue number: {stripped[:60]}")
    marker = "`./startup.sh` verbs:"
    verb_lines = [l for l in master_text.splitlines() if marker in l]
    if "startup/cli.py" in tree.files:
        commands = {name or func.replace("_", "-") for name, func in COMMAND_RE.findall(tree.text("startup/cli.py"))}
        if not verb_lines:
            checker.fail("R7", MASTER, f"no line starting {marker}")
        else:
            written = set(re.findall(r"`([a-z][a-z-]*)`", verb_lines[0].split(marker, 1)[1]))
            if written != commands:
                checker.fail(
                    "R7",
                    MASTER,
                    "startup verbs differ from startup/cli.py: missing "
                    + (", ".join(sorted(commands - written)) or "none")
                    + "; extra "
                    + (", ".join(sorted(written - commands)) or "none"),
                )
    folders = block(master_text, "folders")
    conventions = "docs/ISSUE-CONVENTIONS.md"
    if folders and conventions in tree.files:
        labels = set(AREA_RE.findall(tree.text(conventions)))
        for cells in table_rows(folders):
            if len(cells) >= 4:
                for label in CODE_RE.findall(cells[3]):
                    if label not in labels:
                        checker.fail("R7", MASTER, f"area label {label} is not an area label in {conventions}")


def check_pinned(checker: Checker) -> None:
    tree = checker.tree
    for path in MUST_EXIST:
        if path not in tree.files:
            checker.fail("R8", path, "must exist")
    pinned = dict(PINNED)
    deploy = DEPLOY_MD_AFTER_MOVE if moved(tree) else DEPLOY_MD_BEFORE_MOVE
    pinned["DEPLOYMENT.md"] = pinned["DEPLOYMENT.md"] + (deploy,)
    for path, literals in pinned.items():
        text = tree.text(path)
        for literal in literals:
            if literal not in text:
                checker.fail("R8", path, f"must contain {literal!r}: a guard test pins it")


def check_repo_hygiene(checker: Checker) -> None:
    tree = checker.tree
    for path in sorted(p for p in tree.files if posixpath.basename(p) == "CLAUDE.md"):
        if SESSION_RE.search(tree.text(path)):
            checker.fail("R10", path, "a Session reports section in a tracked file; move it to .claude/CLAUDE.md")
    for path in sorted(p for p in tree.files if p.startswith(".claude/") and not p.startswith(".claude/skills/")):
        checker.fail("R10", path, "only .claude/skills/ is tracked; git rm --cached it")
    if not tree.ignored([".claude/CLAUDE.md"]):
        checker.fail("R10", ".gitignore", ".claude/CLAUDE.md must stay ignored")


def run(root: Path) -> list[Failure]:
    tree = Tree(root)
    checker = Checker(tree)
    docs = live_docs(tree)
    retired = RETIRED_ALWAYS + (RETIRED_AFTER_MOVE if moved(tree) else [])
    for doc in docs:
        checker.check_paths(doc)
        checker.check_anchors(doc)
        checker.check_hygiene(doc, retired)
    checker.flush_unresolved()
    master_text = tree.text(MASTER)
    if moved(tree):
        for name in ("folders", "skills", "status"):
            if block(master_text, name) is None:
                checker.fail("R7", MASTER, f"missing the DOCS-MAP:{name} block")
    check_folder_blocks(checker, docs)
    check_skills(checker, master_text)
    check_indexes(checker)
    check_orphans(checker, docs)
    check_master(checker, master_text)
    check_pinned(checker)
    check_repo_hygiene(checker)
    return sorted(set(checker.failures))


def format_failures(failures: list[Failure]) -> str:
    return "\n".join(str(f) for f in failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the documentation map against the tree.")
    parser.add_argument("--root", default=".", help="checkout to check (default: the current directory)")
    args = parser.parse_args(argv)
    try:
        failures = run(Path(args.root).resolve())
    except GitUnavailable as exc:
        print(f"docs_map: git cannot read this checkout: {exc}", file=sys.stderr)
        return 2
    if failures:
        print(format_failures(failures))
        print(f"\n{len(failures)} problem(s).")
        return 1
    print("docs map: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
