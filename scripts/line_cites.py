#!/usr/bin/env python3
"""Rewrite `path.py:N` code citations in markdown docs into `path.py` (`symbol`).

    python3 scripts/line_cites.py DOC.md [DOC.md ...]            # --report: counts, writes nothing
    python3 scripts/line_cites.py --apply DOC.md                 # rewrite the doc in place
    python3 scripts/line_cites.py --self-check                   # run the built-in check

Each cited line is resolved as of the commit that last changed the DOC line
(git blame), because that is when the author looked at the code. The innermost
def or class holding that line (or the module-level name assigned on it) becomes
the symbol, and is kept only if the same symbol still exists in the current file.
Anything else is left untouched and listed: non-.py paths, files missing at that
commit, lines with no enclosing symbol, symbols that no longer exist.
A range N-M uses line N. Needs only python3 and git; run from the repo root.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CITE = re.compile(r"`((?:[\w.-]+/)*[\w.-]+\.py):(\d+)(?:-(\d+))?`")
SHA = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")


def git(*args: str, cwd: Path) -> str | None:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def _targets(node) -> list[str]:
    ts = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in ts if isinstance(t, ast.Name)]


def symbols(source: str):
    """(spans, names): spans = [(start, end, qualified name)], names = every qualified name."""
    tree = ast.parse(source)
    spans: list[tuple[int, int, str]] = []

    def walk(body, prefix: str, top: bool):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + n.name
                start = min([n.lineno] + [d.lineno for d in n.decorator_list])
                spans.append((start, n.end_lineno, q))
                walk(n.body, q + ".", False)
            elif top and isinstance(n, (ast.Assign, ast.AnnAssign)):
                for name in _targets(n):
                    spans.append((n.lineno, n.end_lineno, name))

    walk(tree.body, "", True)
    return spans, {q for _, _, q in spans}


def innermost(spans, line: int) -> str | None:
    hits = [s for s in spans if s[0] <= line <= s[1]]
    return min(hits, key=lambda s: s[1] - s[0])[2] if hits else None


class Resolver:
    def __init__(self, repo: Path):
        self.repo, self.cache = repo, {}

    def at(self, commit: str, path: str):
        key = (commit, path)
        if key not in self.cache:
            src = git("show", f"{commit}:{path}", cwd=self.repo)
            try:
                self.cache[key] = symbols(src) if src is not None else None
            except SyntaxError:
                self.cache[key] = None
        return self.cache[key]

    def resolve(self, commit: str, path: str, line: int) -> tuple[str | None, str]:
        old = self.at(commit, path)
        if old is None:
            return None, "file missing or unparsable at that commit"
        sym = innermost(old[0], line)
        if sym is None:
            return None, "no enclosing symbol"
        cur = self.at("HEAD", path)
        if cur is None or sym not in cur[1]:
            return None, f"symbol {sym} no longer exists"
        return sym, ""


def blame(repo: Path, doc: str) -> dict[int, str]:
    out = git("blame", "--porcelain", "--", doc, cwd=repo) or ""
    shas = {}
    for ln in out.splitlines():
        m = SHA.match(ln)
        if m:
            shas[int(m.group(2))] = m.group(1)
    return shas


def convert(repo: Path, doc: str, apply: bool):
    path = repo / doc
    text = path.read_text(encoding="utf-8")
    shas, res = blame(repo, doc), Resolver(repo)
    done, left = [], []
    new_lines = []
    for n, line in enumerate(text.split("\n"), 1):
        zero = set("0")
        commit = shas.get(n, "HEAD")
        if set(commit) == zero:
            commit = "HEAD"  # uncommitted doc line: use the current file

        def sub(m):
            sym, why = res.resolve(commit, m.group(1), int(m.group(2)))
            if sym is None:
                left.append((n, m.group(0), why))
                return m.group(0)
            done.append((n, m.group(0), sym))
            return f"`{m.group(1)}` (`{sym}`)"

        new_lines.append(CITE.sub(sub, line))
    # non-.py citations (path:N) are never matched by CITE, so they stay as written
    if apply and done:
        path.write_text("\n".join(new_lines), encoding="utf-8")
    return done, left


def self_check() -> None:
    src = "X = 1\n\n@deco\nclass A:\n    def m(self):\n        return 1\n\ndef f():\n    pass\n"
    spans, names = symbols(src)
    assert names == {"X", "A", "A.m", "f"}, names
    assert innermost(spans, 6) == "A.m" and innermost(spans, 1) == "X" and innermost(spans, 2) is None
    assert innermost(spans, 3) == "A"  # a decorator line belongs to its class
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
        run("init", "-q")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        (repo / "m.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
        (repo / "d.md").write_text("see `m.py:5` and `m.py:2-3` and `m.py:99` and `x.js:3`\n")
        run("add", ".")
        run("commit", "-q", "-m", "one")
        (repo / "m.py").write_text("import os\n\n\n" + (repo / "m.py").read_text().replace("def b", "def c"))
        run("commit", "-qam", "two")
        done, left = convert(repo, "d.md", apply=False)
        assert [x[2] for x in done] == ["a"], done  # line 2 at the doc's commit is inside a
        assert any("no longer exists" in w for _, _, w in left), left  # b was renamed to c
        assert any(c == "`m.py:99`" for _, c, _ in left)
        convert(repo, "d.md", apply=True)
        out = (repo / "d.md").read_text()
        assert "`m.py` (`a`)" in out and "`m.py:99`" in out and "x.js:3" in out, out
    print("line_cites self-check ok")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("docs", nargs="*")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="counts per doc, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="rewrite resolvable citations in place")
    mode.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        self_check()
        return 0
    repo = Path(".").resolve()
    total = [0, 0]
    for doc in a.docs:
        done, left = convert(repo, doc, a.apply)
        total[0] += len(done)
        total[1] += len(left)
        print(f"{doc}: resolved {len(done)}, unresolved {len(left)}")
        for n, cite, why in left:
            print(f"    {doc}:{n} {cite}: {why}")
    print(f"total: resolved {total[0]}, unresolved {total[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
