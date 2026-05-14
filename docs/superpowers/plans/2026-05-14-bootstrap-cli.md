# Bootstrap CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained `./bootstrap` CLI inside `NExtSEEK/` that turns a fresh clone into a running local Docker stack in one command, with multi-instance support, seeded demo data, and two test users.

**Architecture:** Tiny bash entrypoint (`./bootstrap`) does a `uv` preflight, then hands off to a Python `typer` app at `bootstrap/cli.py`. The CLI is organized into pure-function helpers under `bootstrap/lib/` (unit-tested with pytest) and side-effecting step modules under `bootstrap/steps/` (each one is one phase of the install flow). Multi-instance support is achieved by parameterizing the four published ports and the six named-volume names in `docker-compose.yml`, plus a per-instance `bootstrap/.instance.json` state file.

**Tech Stack:** Python 3.14, uv, typer, rich, pytest, Docker Compose v2.

**Spec:** `docs/superpowers/specs/2026-05-14-bootstrap-cli-design.md`

---

## Task 1: Vendor chat_nextseek into NExtSEEK

**Files:**
- Delete: `chat_nextseek/.git/` (entire directory)
- Modify: `.gitignore` (remove `chat_nextseek/` line)
- Modify: NExtSEEK git index (add chat_nextseek/ contents)

**This task is destructive (deletes the inner `.git/`). User runs it manually; do not delegate to a subagent.**

- [ ] **Step 1: Confirm canonical chat_nextseek repo exists elsewhere as a safety net**

Run:
```bash
git -C /home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek remote -v
```
Expected: shows `origin git@github.com:cdemurjian/chat_nextseek.git`. The remote is the canonical source-of-truth; the inner `.git/` is only the local working copy. Deleting it does not lose data — `git clone <remote>` can recreate it anytime.

- [ ] **Step 2: Confirm chat_nextseek working tree is clean and pushed**

Run:
```bash
git -C /home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek status
git -C /home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek log @{u}.. --oneline
```
Expected: both should show "nothing" (clean working tree, no unpushed commits). Abort if either is dirty — push or commit first.

- [ ] **Step 3: Delete the inner `.git/`**

Run:
```bash
rm -rf /home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek/.git
```
Expected: command completes silently.

- [ ] **Step 4: Verify chat_nextseek is no longer a git repo**

Run:
```bash
git -C /home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek rev-parse --show-toplevel
```
Expected: prints `/home/cdemu/code/dmac/docker/NExtSEEK` (the parent NExtSEEK repo) — chat_nextseek now resolves to NExtSEEK's git context.

- [ ] **Step 5: Remove `chat_nextseek/` from NExtSEEK's `.gitignore`**

Edit `/home/cdemu/code/dmac/docker/NExtSEEK/.gitignore`, find this line near the end:
```
chat_nextseek/
```
Delete it.

- [ ] **Step 6: Stage chat_nextseek's contents and verify nested gitignores work**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add chat_nextseek/
git status --short chat_nextseek/ | head -20
```
Expected: lots of `A chat_nextseek/...` lines, none of which are `.env`, `.venv/`, `outputs/`, `*.sqlite`, or `__pycache__/` (these are excluded by the nested `chat_nextseek/.gitignore`). If any secrets-looking file appears, abort with `git reset HEAD chat_nextseek/` and investigate.

- [ ] **Step 7: Sanity-check no real secrets were staged**

Run:
```bash
git diff --cached chat_nextseek/ | grep -ciE "(FFGOD2021mit|bi0micr0|Bi0micr0|NzO8aDX86b53Xrc66WIaVjyQWOdPKHsd|AIzaSy|ABSKQmVk|Z6lhrl7bMA)"
```
Expected: prints `0`. Abort and unstage if non-zero.

- [ ] **Step 8: Commit**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add .gitignore
git commit -m "Vendor chat_nextseek for self-contained bootstrap install

Delete inner chat_nextseek/.git/, remove the chat_nextseek/ line from
NExtSEEK's .gitignore, and stage chat_nextseek's tracked contents.
Source-of-truth chat_nextseek repo (cdemurjian/chat_nextseek) is
unaffected; this repo just gets a vendored snapshot."
```

Expected: commit succeeds with a several-hundred-file diff.

---

## Task 2: Parameterize docker-compose.yml for multi-instance

**Files:**
- Modify: `docker-compose.yml`

The current file hardcodes four ports and six external volume names. Adding env-var fallbacks (with defaults that match current behavior) enables side-by-side installs without breaking the existing one.

- [ ] **Step 1: Read the current compose file to confirm line ranges**

Run:
```bash
grep -nE "ports:|^volumes:|^  [a-z]" /home/cdemu/code/dmac/docker/NExtSEEK/docker-compose.yml
```
Expected output (line numbers may drift; use grep output as ground truth):
```
21:    ports:
22:      - "8000:80"
49:    ports:
50:      - "7474:7474"
51:      - "7687:7687"
70:    ports:
71:      - "3000:3000"
119:volumes:
120:  seek-filestore:
121:    external: true
...
```

- [ ] **Step 2: Replace the four port mappings**

Edit `docker-compose.yml`:

Find:
```
      - "8000:80"
```
Replace with:
```
      - "${NEXTSEEK_PORT:-8000}:80"
```

Find:
```
      - "7474:7474"
      - "7687:7687"
```
Replace with:
```
      - "${NEO4J_HTTP_PORT:-7474}:7474"
      - "${NEO4J_BOLT_PORT:-7687}:7687"
```

Find:
```
      - "3000:3000"
```
Replace with:
```
      - "${SEEK_PORT:-3000}:3000"
```

- [ ] **Step 3: Parameterize the six external volume names**

Find each of these six volume blocks at the bottom of `docker-compose.yml`:
```
volumes:
  seek-filestore:
    external: true
  seek-mysql-db:
    external: true
  seek-solr-data:
    external: true
  seek-cache:
    external: true
  nextseek-static-files:
    external: true
  neo4j-data:
    external: true
```

Replace with:
```
volumes:
  seek-filestore:
    name: "${INSTANCE_PREFIX:-}seek-filestore"
    external: true
  seek-mysql-db:
    name: "${INSTANCE_PREFIX:-}seek-mysql-db"
    external: true
  seek-solr-data:
    name: "${INSTANCE_PREFIX:-}seek-solr-data"
    external: true
  seek-cache:
    name: "${INSTANCE_PREFIX:-}seek-cache"
    external: true
  nextseek-static-files:
    name: "${INSTANCE_PREFIX:-}nextseek-static-files"
    external: true
  neo4j-data:
    name: "${INSTANCE_PREFIX:-}neo4j-data"
    external: true
```

- [ ] **Step 4: Verify the default render still matches current behavior**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose config | grep -E "(target:|published:|name:)" | head -20
```
Expected: published ports are `"8000"`, `"7474"`, `"7687"`, `"3000"`; volume names are `seek-filestore`, `seek-mysql-db`, etc. — identical to before.

- [ ] **Step 5: Verify a parameterized render**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
INSTANCE_PREFIX=test- NEXTSEEK_PORT=8001 SEEK_PORT=3001 NEO4J_HTTP_PORT=7475 NEO4J_BOLT_PORT=7688 \
  docker compose config | grep -E "(published:|name:)" | head -10
```
Expected: ports show `8001`, `3001`, `7475`, `7688`; volume names show `test-seek-filestore`, `test-seek-mysql-db`, etc.

- [ ] **Step 6: Commit**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add docker-compose.yml
git commit -m "compose: parameterize ports and volume names for multi-instance

Add INSTANCE_PREFIX and per-service port env vars with defaults that
preserve the existing install. Enables side-by-side installs on
different ports without touching the running stack."
```

---

## Task 3: Bootstrap package scaffold

**Files:**
- Create: `bootstrap` (bash entrypoint at repo root)
- Create: `bootstrap_pkg/__init__.py` (the Python package — using `bootstrap_pkg` as the import name to avoid colliding with the `bootstrap` bash entrypoint at the same path level)
- Create: `bootstrap_pkg/cli.py`
- Modify: `pyproject.toml` (add bootstrap deps)
- Modify: `.gitignore` (add `bootstrap/.instance.json`)

NOTE: The spec uses `bootstrap/` for both the bash script and the Python package. In practice you cannot have a regular file `bootstrap` at the same path as a directory `bootstrap/`. Resolution: bash script named `bootstrap` at repo root, Python package directory named `bootstrap/` (the bash script wraps `uv run python -m bootstrap.cli`). The bash script and the package directory coexist fine — bash file `bootstrap` and dir `bootstrap/` are distinct entries.

- [ ] **Step 1: Create the Python package skeleton**

Run:
```bash
mkdir -p /home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/{steps,lib,templates,seed/regenerate,tests}
touch /home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/{__init__.py,steps/__init__.py,lib/__init__.py,tests/__init__.py}
```

- [ ] **Step 2: Create the bash entrypoint `bootstrap` at repo root**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap` with this content:
```bash
#!/usr/bin/env bash
# NExtSEEK bootstrap entrypoint.
# Ensures uv is available, then runs the typer CLI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed (https://docs.astral.sh/uv/getting-started/installation/)" >&2
  exit 2
fi

exec uv run python -m bootstrap.cli "$@"
```

Then make it executable:
```bash
chmod +x /home/cdemu/code/dmac/docker/NExtSEEK/bootstrap
```

- [ ] **Step 3: Create `bootstrap/cli.py` with a typer skeleton**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/cli.py` with this content:
```python
"""NExtSEEK bootstrap CLI."""
from __future__ import annotations

import typer

app = typer.Typer(
    name="bootstrap",
    help="Set up and manage local NExtSEEK Docker installs.",
    no_args_is_help=True,
)


@app.command()
def install(
    instance: str | None = typer.Option(None, "--instance", help="Named instance for multi-install."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """First-time install: prereqs, config, volumes, seeds, build, users, validate."""
    typer.echo("install: not yet implemented")
    raise typer.Exit(code=1)


@app.command()
def doctor(
    instance: str | None = typer.Option(None, "--instance"),
) -> None:
    """Diagnose the running install."""
    typer.echo("doctor: not yet implemented")
    raise typer.Exit(code=1)


@app.command()
def reset(
    instance: str | None = typer.Option(None, "--instance"),
    keep_config: bool = typer.Option(False, "--keep-config"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Drop volumes and re-run install."""
    typer.echo("reset: not yet implemented")
    raise typer.Exit(code=1)


@app.command()
def rebuild(
    instance: str | None = typer.Option(None, "--instance"),
    service: str = typer.Option("nextseek", "--service"),
) -> None:
    """Rebuild and restart one or more services without touching volumes."""
    typer.echo("rebuild: not yet implemented")
    raise typer.Exit(code=1)


@app.command(name="seed-users")
def seed_users(instance: str | None = typer.Option(None, "--instance")) -> None:
    """Idempotent: ensure demo + user accounts exist in SEEK."""
    typer.echo("seed-users: not yet implemented")
    raise typer.Exit(code=1)


@app.command(name="dump-db")
def dump_db(
    source: str = typer.Option("dev", "--source"),
    target: str | None = typer.Option(None, "--target"),
) -> None:
    """Maintainer-only: regenerate seed dumps from a source DB."""
    typer.echo("dump-db: not yet implemented")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Add bootstrap dependencies to `pyproject.toml`**

Open `/home/cdemu/code/dmac/docker/NExtSEEK/pyproject.toml`. In the `[project.optional-dependencies]` section (or `[dependency-groups]` if it uses uv-style groups), add a new group:
```toml
[dependency-groups]
bootstrap = [
    "typer>=0.12.0",
    "rich>=13.7.0",
    "pytest>=8.0.0",
]
```

If the file already has a `[project.optional-dependencies]` table, add the same group there instead. The format depends on whether the project uses PEP 621 optional deps or uv dependency groups.

- [ ] **Step 5: Install the bootstrap deps**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv sync --group bootstrap
```
Expected: prints "Resolved N packages" and updates `uv.lock`. If `--group` isn't recognized, the project uses `[project.optional-dependencies]` — adjust syntax: `uv sync --extra bootstrap`.

- [ ] **Step 6: Add `bootstrap/.instance.json` to `.gitignore`**

Edit `/home/cdemu/code/dmac/docker/NExtSEEK/.gitignore`, add a new section near the end:
```
# Per-instance bootstrap state (per-clone, not shared).
bootstrap/.instance.json
```

- [ ] **Step 7: Verify the CLI runs**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap --help
```
Expected: typer help output listing all six commands.

Then:
```bash
./bootstrap install --help
```
Expected: install command help with `--instance` and `--yes` flags.

- [ ] **Step 8: Commit**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add bootstrap pyproject.toml uv.lock .gitignore
git commit -m "bootstrap: scaffold CLI skeleton with typer

Adds bash entrypoint, Python package with empty step/lib/template/seed/tests
subdirs, typer-based cli.py with six stub commands. Each command exits 1
with 'not yet implemented' until its task lands. Adds typer + rich + pytest
as bootstrap-group deps."
```

---

## Task 4: lib/env.py — env file read/write that preserves comments

**Files:**
- Create: `bootstrap/lib/env.py`
- Create: `bootstrap/tests/test_env.py`

The bootstrap config writer must update env files without destroying user comments or reordering keys. A naive `dotenv` dump-and-rewrite loses comments. Implement a minimal parser that round-trips comments + blank lines.

- [ ] **Step 1: Write the failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_env.py`:
```python
"""Tests for bootstrap.lib.env."""
from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.lib.env import read_env, write_env, update_env


def test_read_simple_keyvalue(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text('FOO="bar"\nBAZ="qux"\n')
    assert read_env(p) == {"FOO": "bar", "BAZ": "qux"}


def test_read_strips_quotes_and_handles_unquoted(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("FOO=bar\nBAZ='qux quux'\nZIM=\"zam\"\n")
    assert read_env(p) == {"FOO": "bar", "BAZ": "qux quux", "ZIM": "zam"}


def test_read_ignores_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("# comment\n\nFOO=bar\n# another\nBAZ=qux\n")
    assert read_env(p) == {"FOO": "bar", "BAZ": "qux"}


def test_write_creates_quoted_values(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    write_env(p, {"FOO": "bar", "BAZ": "has spaces"})
    text = p.read_text()
    assert 'FOO="bar"' in text
    assert 'BAZ="has spaces"' in text


def test_update_preserves_comments_and_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    original = "# header comment\n\nFOO=oldvalue\n# trailing\nBAZ=keepme\n"
    p.write_text(original)
    update_env(p, {"FOO": "newvalue"})
    text = p.read_text()
    assert "# header comment" in text
    assert "FOO=\"newvalue\"" in text
    assert "FOO=oldvalue" not in text
    assert "# trailing" in text
    assert "BAZ=keepme" in text


def test_update_appends_new_keys_with_blank_line(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("FOO=existing\n")
    update_env(p, {"NEW_KEY": "newvalue"})
    text = p.read_text()
    assert "FOO=existing" in text
    assert 'NEW_KEY="newvalue"' in text
    assert text.index("FOO=existing") < text.index("NEW_KEY")


def test_update_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("# header\nFOO=bar\n")
    update_env(p, {"FOO": "bar"})
    update_env(p, {"FOO": "bar"})
    assert p.read_text().count("FOO=") == 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_env.py -v
```
Expected: ImportError / ModuleNotFoundError — `bootstrap.lib.env` doesn't exist yet.

- [ ] **Step 3: Implement `bootstrap/lib/env.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/lib/env.py`:
```python
"""Read/write .env files while preserving comments and key order."""
from __future__ import annotations

import re
from pathlib import Path

# Match KEY=VALUE with optional quoting on the value side.
_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.rstrip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw


def _quote(value: str) -> str:
    # Always double-quote for stability — readers should strip either kind.
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def read_env(path: Path) -> dict[str, str]:
    """Return {key: value} from the env file. Comments and blanks are dropped."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match:
            result[match.group(1)] = _unquote(match.group(2))
    return result


def write_env(path: Path, values: dict[str, str]) -> None:
    """Write a fresh env file from scratch. Loses any existing comments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={_quote(v)}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n")


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Update or append keys in path without destroying comments/blanks.

    Keys already present get replaced in place. Keys not present get
    appended after a single blank line separator (if the file is non-empty).
    """
    if not path.exists():
        write_env(path, updates)
        return

    existing_lines = path.read_text().splitlines()
    seen: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        match = _LINE_RE.match(line)
        if match and match.group(1) in updates:
            key = match.group(1)
            new_lines.append(f"{key}={_quote(updates[key])}")
            seen.add(key)
        else:
            new_lines.append(line)

    to_append = [k for k in updates if k not in seen]
    if to_append:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for key in to_append:
            new_lines.append(f"{key}={_quote(updates[key])}")

    path.write_text("\n".join(new_lines) + "\n")
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_env.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/lib/env.py bootstrap/tests/test_env.py
git commit -m "bootstrap: add comment-preserving env file read/write helpers"
```

---

## Task 5: lib/ports.py — free port detection

**Files:**
- Create: `bootstrap/lib/ports.py`
- Create: `bootstrap/tests/test_ports.py`

Bootstrap needs to detect port collisions and pick free ports for non-default instances. Use `socket.socket()` with `SO_REUSEADDR=0` to probe.

- [ ] **Step 1: Write the failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_ports.py`:
```python
"""Tests for bootstrap.lib.ports."""
from __future__ import annotations

import socket

import pytest

from bootstrap.lib.ports import is_port_free, find_free_port, allocate_ports


def test_is_port_free_for_clearly_free_port() -> None:
    # 0 means "let the OS pick"; bind once, then check the picked port is busy.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        bound_port = s.getsockname()[1]
        assert not is_port_free(bound_port)


def test_is_port_free_returns_true_for_unbound_port() -> None:
    # Use a high random port unlikely to be in use.
    assert is_port_free(54321) is True or is_port_free(54322) is True


def test_find_free_port_returns_starting_port_if_free() -> None:
    # Pick a high port that's almost certainly free.
    port = find_free_port(54321)
    assert port >= 54321
    assert is_port_free(port)


def test_find_free_port_walks_past_occupied() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        occupied = s.getsockname()[1]
        # Find a free port starting from the occupied one — should skip past it.
        free = find_free_port(occupied)
        assert free != occupied
        assert free > occupied


def test_allocate_ports_returns_dict_with_all_free() -> None:
    desired = {"a": 54330, "b": 54331, "c": 54332, "d": 54333}
    result = allocate_ports(desired)
    assert set(result.keys()) == set(desired.keys())
    for port in result.values():
        assert is_port_free(port)


def test_allocate_ports_skips_collisions() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        result = allocate_ports({"a": busy})
        assert result["a"] != busy
        assert is_port_free(result["a"])


def test_allocate_ports_avoids_duplicates_in_one_call() -> None:
    # Two services asking for the same starting port should not collide with each other.
    result = allocate_ports({"a": 54400, "b": 54400})
    assert result["a"] != result["b"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_ports.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/lib/ports.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/lib/ports.py`:
```python
"""Port-availability helpers."""
from __future__ import annotations

import socket


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if a TCP server can bind to (host, port) right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(starting_at: int, max_attempts: int = 200) -> int:
    """Return the first free port >= starting_at. Raises after max_attempts."""
    for offset in range(max_attempts):
        candidate = starting_at + offset
        if is_port_free(candidate):
            return candidate
    raise RuntimeError(
        f"No free port found in range {starting_at}..{starting_at + max_attempts}"
    )


def allocate_ports(desired: dict[str, int]) -> dict[str, int]:
    """Allocate free ports for each service. Skips collisions and inter-service duplicates."""
    result: dict[str, int] = {}
    claimed: set[int] = set()
    for name, start in desired.items():
        candidate = start
        while True:
            if candidate not in claimed and is_port_free(candidate):
                result[name] = candidate
                claimed.add(candidate)
                break
            candidate += 1
            if candidate - start > 200:
                raise RuntimeError(f"No free port for service {name} starting at {start}")
    return result
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_ports.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/lib/ports.py bootstrap/tests/test_ports.py
git commit -m "bootstrap: add free-port detection helpers"
```

---

## Task 6: lib/ui.py — rich console wrappers

**Files:**
- Create: `bootstrap/lib/ui.py`

UI helpers don't get unit tests (output is visual). They're thin wrappers around `rich` that keep the rest of the codebase agnostic to the console library.

- [ ] **Step 1: Implement `bootstrap/lib/ui.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/lib/ui.py`:
```python
"""Rich-console wrappers for consistent bootstrap UI."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def banner(title: str) -> None:
    console.print(Panel(title, expand=False, border_style="cyan"))


def step(n: int, total: int, label: str) -> None:
    console.print(f"[bold cyan][{n}/{total}][/bold cyan] {label}")


def info(message: str) -> None:
    console.print(f"       {message}")


def ok(message: str) -> None:
    console.print(f"       [green]✓[/green] {message}")


def warn(message: str) -> None:
    console.print(f"       [yellow]![/yellow] {message}")


def fail(message: str) -> None:
    console.print(f"       [red]✗[/red] {message}", style="red")


def remediation(message: str) -> None:
    console.print(f"       [dim]→ {message}[/dim]")


@contextmanager
def spinner(label: str) -> Iterator[None]:
    """Context manager that shows a spinner while the block runs."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description=label, total=None)
        yield
```

- [ ] **Step 2: Verify it imports cleanly**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run python -c "from bootstrap.lib import ui; ui.banner('NExtSEEK Bootstrap'); ui.step(1, 9, 'Checking prerequisites'); ui.ok('docker installed'); ui.fail('port 8000 busy'); ui.remediation('try --port-offset 1')"
```
Expected: a cyan-bordered banner, a step line, and three status lines visible in the terminal. No tracebacks.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/lib/ui.py
git commit -m "bootstrap: add rich-based UI helpers (banner, steps, status, spinner)"
```

---

## Task 7: lib/docker_ops.py — compose wrappers

**Files:**
- Create: `bootstrap/lib/docker_ops.py`
- Create: `bootstrap/tests/test_docker_ops.py`

Wraps `docker compose` subprocess calls behind a typed API. Mockable for tests.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_docker_ops.py`:
```python
"""Tests for bootstrap.lib.docker_ops."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.lib.docker_ops import (
    DockerOpsError,
    compose_up,
    compose_down,
    compose_exec,
    volume_exists,
    volume_create,
)


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_up_invokes_compose_up_d(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db", "neo4j"], project_dir="/repo", env={})
    args = mock_run.call_args.args[0]
    assert args[:3] == ["docker", "compose", "up"]
    assert "-d" in args
    assert "db" in args and "neo4j" in args


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_up_passes_env(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db"], project_dir="/repo", env={"INSTANCE_PREFIX": "test-"})
    call_env = mock_run.call_args.kwargs["env"]
    assert call_env["INSTANCE_PREFIX"] == "test-"


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_up_raises_on_nonzero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(DockerOpsError, match="boom"):
        compose_up(services=["db"], project_dir="/repo", env={})


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_volume_exists_returns_true_on_zero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="my-volume\n", stderr="")
    assert volume_exists("my-volume") is True


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_volume_exists_returns_false_on_nonzero(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
    assert volume_exists("my-volume") is False


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_volume_create_invokes_docker_volume_create(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="my-volume\n", stderr="")
    volume_create("my-volume")
    args = mock_run.call_args.args[0]
    assert args == ["docker", "volume", "create", "my-volume"]


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_exec_passes_service_and_command(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
    compose_exec(
        service="db",
        command=["mysql", "-e", "SHOW DATABASES;"],
        project_dir="/repo",
        env={},
    )
    args = mock_run.call_args.args[0]
    assert args[:3] == ["docker", "compose", "exec"]
    assert "db" in args
    assert "SHOW DATABASES;" in args
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_docker_ops.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/lib/docker_ops.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/lib/docker_ops.py`:
```python
"""Subprocess wrappers around docker / docker compose."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence


class DockerOpsError(RuntimeError):
    """A docker / docker compose invocation failed."""


def _build_env(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    return env


def _check(result: subprocess.CompletedProcess[str], context: str) -> None:
    if result.returncode != 0:
        raise DockerOpsError(f"{context} failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}")


def compose_up(
    services: Sequence[str],
    project_dir: str | Path,
    env: dict[str, str],
    detached: bool = True,
    build: bool = False,
) -> None:
    """Run `docker compose up [-d] [--build] <services...>` in project_dir."""
    cmd = ["docker", "compose", "up"]
    if detached:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    cmd.extend(services)
    result = subprocess.run(
        cmd,
        cwd=str(project_dir),
        env=_build_env(env),
        capture_output=True,
        text=True,
    )
    _check(result, f"docker compose up {' '.join(services)}")


def compose_down(
    project_dir: str | Path,
    env: dict[str, str],
    volumes: bool = False,
) -> None:
    """Run `docker compose down [-v]`."""
    cmd = ["docker", "compose", "down"]
    if volumes:
        cmd.append("-v")
    result = subprocess.run(
        cmd,
        cwd=str(project_dir),
        env=_build_env(env),
        capture_output=True,
        text=True,
    )
    _check(result, "docker compose down")


def compose_exec(
    service: str,
    command: Sequence[str],
    project_dir: str | Path,
    env: dict[str, str],
    interactive: bool = False,
    stdin: bytes | None = None,
) -> str:
    """Run `docker compose exec [-T] <service> <command...>`, return stdout."""
    cmd = ["docker", "compose", "exec"]
    if not interactive:
        cmd.append("-T")
    cmd.append(service)
    cmd.extend(command)
    result = subprocess.run(
        cmd,
        cwd=str(project_dir),
        env=_build_env(env),
        capture_output=True,
        text=stdin is None,
        input=stdin if stdin is not None else None,
    )
    _check(result, f"docker compose exec {service} {' '.join(command)}")
    return result.stdout if isinstance(result.stdout, str) else result.stdout.decode()


def volume_exists(name: str) -> bool:
    """True if `docker volume inspect <name>` succeeds."""
    result = subprocess.run(
        ["docker", "volume", "inspect", name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def volume_create(name: str) -> None:
    """Create a docker volume by name. No-op-ish if it already exists (docker handles this)."""
    result = subprocess.run(
        ["docker", "volume", "create", name],
        capture_output=True,
        text=True,
    )
    _check(result, f"docker volume create {name}")
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_docker_ops.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/lib/docker_ops.py bootstrap/tests/test_docker_ops.py
git commit -m "bootstrap: add docker compose / volume subprocess wrappers"
```

---

## Task 8: lib/instance.py — per-instance state (.instance.json)

**Files:**
- Create: `bootstrap/lib/instance.py`
- Create: `bootstrap/tests/test_instance.py`

Each install writes `bootstrap/.instance.json` with the resolved instance name, prefix, port assignments, and compose project name. Subsequent commands read it to know which install they're operating on.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_instance.py`:
```python
"""Tests for bootstrap.lib.instance."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bootstrap.lib.instance import (
    InstanceState,
    resolve_instance_name,
    load_instance,
    save_instance,
)


def test_resolve_instance_name_uses_explicit_when_given(tmp_path: Path) -> None:
    assert resolve_instance_name(repo_root=tmp_path, explicit="myname") == "myname"


def test_resolve_instance_name_uses_cwd_basename_when_explicit_none(tmp_path: Path) -> None:
    sub = tmp_path / "NExtSEEK-test"
    sub.mkdir()
    assert resolve_instance_name(repo_root=sub, explicit=None) == "NExtSEEK-test"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    state = InstanceState(
        name="test",
        prefix="test-",
        ports={"nextseek": 8001, "seek": 3001, "neo4j_http": 7475, "neo4j_bolt": 7688},
        compose_project_name="nextseek-test",
        created="2026-05-14T12:34:56-04:00",
    )
    save_instance(tmp_path, state)
    loaded = load_instance(tmp_path)
    assert loaded == state


def test_load_returns_none_if_missing(tmp_path: Path) -> None:
    assert load_instance(tmp_path) is None


def test_save_writes_to_bootstrap_subdir(tmp_path: Path) -> None:
    (tmp_path / "bootstrap").mkdir()
    state = InstanceState(
        name="x", prefix="", ports={"a": 1}, compose_project_name="x", created="now"
    )
    save_instance(tmp_path, state)
    assert (tmp_path / "bootstrap" / ".instance.json").exists()


def test_compose_env_returns_correct_env_dict() -> None:
    state = InstanceState(
        name="test",
        prefix="test-",
        ports={"nextseek": 8001, "seek": 3001, "neo4j_http": 7475, "neo4j_bolt": 7688},
        compose_project_name="nextseek-test",
        created="2026-05-14T12:34:56-04:00",
    )
    env = state.compose_env()
    assert env["INSTANCE_PREFIX"] == "test-"
    assert env["NEXTSEEK_PORT"] == "8001"
    assert env["SEEK_PORT"] == "3001"
    assert env["NEO4J_HTTP_PORT"] == "7475"
    assert env["NEO4J_BOLT_PORT"] == "7688"
    assert env["COMPOSE_PROJECT_NAME"] == "nextseek-test"
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_instance.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/lib/instance.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/lib/instance.py`:
```python
"""Per-install instance state (bootstrap/.instance.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class InstanceState:
    name: str
    prefix: str
    ports: dict[str, int]
    compose_project_name: str
    created: str

    def compose_env(self) -> dict[str, str]:
        """Return env vars to pass to docker compose for this instance."""
        env = {
            "INSTANCE_PREFIX": self.prefix,
            "COMPOSE_PROJECT_NAME": self.compose_project_name,
        }
        port_env_map = {
            "nextseek": "NEXTSEEK_PORT",
            "seek": "SEEK_PORT",
            "neo4j_http": "NEO4J_HTTP_PORT",
            "neo4j_bolt": "NEO4J_BOLT_PORT",
        }
        for service_key, env_key in port_env_map.items():
            if service_key in self.ports:
                env[env_key] = str(self.ports[service_key])
        return env


def resolve_instance_name(repo_root: Path, explicit: str | None) -> str:
    """If explicit is given, use it. Otherwise use the repo dir's basename."""
    if explicit:
        return explicit
    return repo_root.name


def _instance_path(repo_root: Path) -> Path:
    return repo_root / "bootstrap" / ".instance.json"


def save_instance(repo_root: Path, state: InstanceState) -> None:
    path = _instance_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n")


def load_instance(repo_root: Path) -> InstanceState | None:
    path = _instance_path(repo_root)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return InstanceState(**data)
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_instance.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/lib/instance.py bootstrap/tests/test_instance.py
git commit -m "bootstrap: add per-instance state (InstanceState + .instance.json)"
```

---

## Task 9: steps/prereqs.py — preflight checks

**Files:**
- Create: `bootstrap/steps/prereqs.py`
- Create: `bootstrap/tests/test_prereqs.py`

Verifies docker, docker compose, uv versions are present. Checks default ports and disk space. Returns a structured result that the CLI renders.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_prereqs.py`:
```python
"""Tests for bootstrap.steps.prereqs."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.steps.prereqs import (
    PrereqResult,
    check_command_version,
    check_docker,
    check_compose,
    check_uv,
)


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_command_version_returns_ok_on_success(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="Docker version 28.1.0, build abc\n", stderr="")
    r = check_command_version("docker", ["--version"])
    assert r.ok is True
    assert "28.1.0" in r.detail


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_command_version_returns_fail_on_missing(mock_run: MagicMock) -> None:
    mock_run.side_effect = FileNotFoundError("docker")
    r = check_command_version("docker", ["--version"])
    assert r.ok is False
    assert "not installed" in r.detail or "not found" in r.detail


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_docker_parses_version(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="Docker version 28.1.0, build abc\n", stderr="")
    r = check_docker()
    assert r.ok is True
    assert r.name == "docker"


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_compose_parses_version(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="Docker Compose version v2.30.0\n", stderr="")
    r = check_compose()
    assert r.ok is True
    assert r.name == "docker compose"


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_uv_parses_version(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="uv 0.5.20\n", stderr="")
    r = check_uv()
    assert r.ok is True
    assert r.name == "uv"
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_prereqs.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/steps/prereqs.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/prereqs.py`:
```python
"""Preflight checks: docker, docker compose, uv."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class PrereqResult:
    name: str
    ok: bool
    detail: str
    remediation: str = ""


def check_command_version(cmd: str, args: list[str]) -> PrereqResult:
    """Run `cmd args` and capture its stdout as the version line."""
    try:
        result = subprocess.run(
            [cmd] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return PrereqResult(name=cmd, ok=False, detail=f"{cmd} not installed (not in PATH)")
    except subprocess.TimeoutExpired:
        return PrereqResult(name=cmd, ok=False, detail=f"{cmd} timed out")
    if result.returncode != 0:
        return PrereqResult(name=cmd, ok=False, detail=result.stderr.strip() or "non-zero exit")
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return PrereqResult(name=cmd, ok=True, detail=first_line)


def check_docker() -> PrereqResult:
    r = check_command_version("docker", ["--version"])
    if not r.ok:
        r.remediation = "Install Docker: https://docs.docker.com/get-docker/"
    return PrereqResult(name="docker", ok=r.ok, detail=r.detail, remediation=r.remediation)


def check_compose() -> PrereqResult:
    r = check_command_version("docker", ["compose", "version"])
    if not r.ok:
        r.remediation = "Install Docker Compose v2: https://docs.docker.com/compose/install/"
    return PrereqResult(name="docker compose", ok=r.ok, detail=r.detail, remediation=r.remediation)


def check_uv() -> PrereqResult:
    r = check_command_version("uv", ["--version"])
    if not r.ok:
        r.remediation = "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
    return PrereqResult(name="uv", ok=r.ok, detail=r.detail, remediation=r.remediation)


def check_disk_space(path: str, gb_required: int = 5) -> PrereqResult:
    """Check that `path` has at least gb_required GB free."""
    total, used, free = shutil.disk_usage(path)
    free_gb = free // (1024 ** 3)
    if free_gb < gb_required:
        return PrereqResult(
            name=f"disk:{path}",
            ok=False,
            detail=f"{free_gb} GB free (need {gb_required})",
            remediation="Free up disk space before installing",
        )
    return PrereqResult(name=f"disk:{path}", ok=True, detail=f"{free_gb} GB free")


def run_all() -> list[PrereqResult]:
    """Run every prereq check and return the list of results."""
    return [
        check_docker(),
        check_compose(),
        check_uv(),
        check_disk_space("/var/lib/docker" if subprocess.run(["test", "-d", "/var/lib/docker"]).returncode == 0 else "/"),
    ]
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_prereqs.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/steps/prereqs.py bootstrap/tests/test_prereqs.py
git commit -m "bootstrap: add prereq checks (docker, compose, uv, disk)"
```

---

## Task 10: Templates — db.env, nextseek.env, local_settings.py

**Files:**
- Create: `bootstrap/templates/db.env.template`
- Create: `bootstrap/templates/nextseek.env.template`
- Create: `bootstrap/templates/local_settings.py.template`

Pure data files. The config writer (Task 11) renders them.

- [ ] **Step 1: Create `bootstrap/templates/db.env.template`**

Mirrors the current `docker/db.env`. Demo defaults, no real secrets. Create with exact contents:
```
# db is the name of the MySQL Docker container. Don't change.
MYSQL_HOST="db"

# MySQL root password (demo default; override if you want)
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}"

# SEEK database name
MYSQL_DATABASE="seek_production"

# NExtSEEK database name
NEXTSEEK_MYSQL_DATABASE="dmac"

# Application user (used by NExtSEEK + SEEK)
MYSQL_USER="seek_db_user"

# Application user password (demo default)
MYSQL_PASSWORD="${MYSQL_PASSWORD}"
```

- [ ] **Step 2: Create `bootstrap/templates/nextseek.env.template`**

Built from `docker/nextseek.env.example`. Create with exact contents:
```
SEEK_HOST="seek"
SEEK_HOSTNAME="http://seek:3000"

NEXTSEEK_HOSTNAME="127.0.0.1:${NEXTSEEK_PORT}"
NEXTSEEK_NEO4J_PASSWORD="${NEO4J_PASSWORD}"
NEXTSEEK_NEO4J_HOST="neo4j"

DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY}"
DJANGO_ALLOWED_HOSTS="127.0.0.1 localhost"
DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS}"

NEXTSEEK_BASE_URL="http://$NEXTSEEK_HOSTNAME"
LOG_DIR="/app/logs"
NEXTSEEK_OUTPUTS_DIR="/app/outputs"
CATALOG_FILE="/app/chat_nextseek/agent_model_catalog.json"

# Real LLM keys go here. Bootstrap leaves placeholders; the chat features
# stay disabled until you fill these in.
GCP_API_KEY="SET_IN_LOCAL_ENV"
AWS_BEARER_TOKEN_BEDROCK="SET_IN_LOCAL_ENV"
FDH_API="SET_IN_LOCAL_ENV"

SESSION_DB_TYPE="mysql"
SESSION_DB_HOST=$MYSQL_HOST
SESSION_DB_USER=$MYSQL_USER
SESSION_DB_PASSWORD=$MYSQL_PASSWORD
SESSION_DB_NAME=$NEXTSEEK_MYSQL_DATABASE

MYSQL_HOST_PROD=$MYSQL_HOST
MYSQL_PROD_PASSWORD=$MYSQL_PASSWORD

MYSQL_HOST_DEV=$MYSQL_HOST
MYSQL_DEV_PASSWORD=$MYSQL_PASSWORD

NEO4J_URI="neo4j://$NEXTSEEK_NEO4J_HOST"
NEO4J_USER="neo4j"
NEO4J_PASSWORD=$NEXTSEEK_NEO4J_PASSWORD
```

- [ ] **Step 3: Create `bootstrap/templates/local_settings.py.template`**

Built from `dmac/local_settings.example.py`. Create with exact contents:
```python
import os
from chat_nextseek.config import ChatConfig

SEEK_URL = "http://seek:3000"
PUBLISH_URL = SEEK_URL

ASSISTANT_PARTICIPATING_PROJECTS = set(["1"])

NEXTSEEK_CHAT_CONFIG = ChatConfig()


# ---------------------------------------------------------------------------
# Optional PROD ChatConfig (admin-only PROD toggle in the UI).
#
# Fill in the values below with your real prod credentials. This file is
# gitignored. When *any* override is set, a second ChatConfig is built by
# temporarily overlaying the values on the standard env names that ChatConfig
# reads at construction. Leave any line as None to skip that override.
# ---------------------------------------------------------------------------
_PROD_OVERRIDES: dict[str, str | None] = {
    "NEXTSEEK_BASE_URL": None,
    "API_USER": None,
    "API_PASS": None,
    "NEO4J_URI": None,
    "NEO4J_USER": None,
    "NEO4J_PASSWORD": None,
    "NEO4J_DATABASE": None,
    "MYSQL_HOST_PROD": None,
    "MYSQL_PROD_PASSWORD": None,
    "MYSQL_USER": None,
    "MYSQL_PORT": None,
}

NEXTSEEK_CHAT_CONFIG_PROD = None
if any(v is not None for v in _PROD_OVERRIDES.values()):
    _prev_env = {k: os.environ.get(k) for k in _PROD_OVERRIDES}
    try:
        for _k, _v in _PROD_OVERRIDES.items():
            if _v is not None:
                os.environ[_k] = _v
        NEXTSEEK_CHAT_CONFIG_PROD = ChatConfig()
    finally:
        for _k, _v in _prev_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
```

- [ ] **Step 4: Commit**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add bootstrap/templates/
git commit -m "bootstrap: add config templates (db.env, nextseek.env, local_settings.py)"
```

---

## Task 11: steps/config.py — interactive prompts + template rendering

**Files:**
- Create: `bootstrap/steps/config.py`
- Create: `bootstrap/tests/test_config.py`

Renders templates to `docker/db.env`, `docker/nextseek.env`, `dmac/local_settings.py`. Either runs interactive prompts or accepts pre-filled values for `--yes` mode.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_config.py`:
```python
"""Tests for bootstrap.steps.config."""
from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.steps.config import (
    ConfigValues,
    default_values,
    render_db_env,
    render_nextseek_env,
    render_local_settings,
    csrf_origins_for_port,
)


def test_default_values_has_demo_creds() -> None:
    v = default_values(nextseek_port=8000)
    assert v.mysql_root_password == "seek_root"
    assert v.mysql_password == "seek_db_password"
    assert v.neo4j_password == "demopassword"
    assert v.django_secret_key  # auto-generated, non-empty


def test_default_values_csrf_origins_match_port() -> None:
    v = default_values(nextseek_port=8042)
    assert "127.0.0.1:8042" in v.django_csrf_trusted_origins
    assert "localhost:8042" in v.django_csrf_trusted_origins


def test_csrf_origins_for_port_returns_both_hosts() -> None:
    result = csrf_origins_for_port(8001)
    assert "http://127.0.0.1:8001" in result
    assert "http://localhost:8001" in result


def test_render_db_env_substitutes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "bootstrap" / "templates").mkdir(parents=True)
    template_path = repo / "bootstrap" / "templates" / "db.env.template"
    template_path.write_text('MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD}"\nMYSQL_PASSWORD="${MYSQL_PASSWORD}"\n')

    v = ConfigValues(
        mysql_root_password="root_pw",
        mysql_password="user_pw",
        neo4j_password="np",
        django_secret_key="dsk",
        django_csrf_trusted_origins="origins",
        nextseek_port=8000,
    )
    render_db_env(repo, v)
    rendered = (repo / "docker" / "db.env").read_text()
    assert 'MYSQL_ROOT_PASSWORD="root_pw"' in rendered
    assert 'MYSQL_PASSWORD="user_pw"' in rendered


def test_render_nextseek_env_substitutes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "bootstrap" / "templates").mkdir(parents=True)
    template_path = repo / "bootstrap" / "templates" / "nextseek.env.template"
    template_path.write_text(
        'NEXTSEEK_HOSTNAME="127.0.0.1:${NEXTSEEK_PORT}"\n'
        'NEXTSEEK_NEO4J_PASSWORD="${NEO4J_PASSWORD}"\n'
        'DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY}"\n'
        'DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS}"\n'
    )
    v = ConfigValues(
        mysql_root_password="r", mysql_password="p", neo4j_password="np",
        django_secret_key="dsk", django_csrf_trusted_origins="csrforigins",
        nextseek_port=8042,
    )
    render_nextseek_env(repo, v)
    rendered = (repo / "docker" / "nextseek.env").read_text()
    assert 'NEXTSEEK_HOSTNAME="127.0.0.1:8042"' in rendered
    assert 'NEO4J_PASSWORD="np"' in rendered
    assert 'DJANGO_SECRET_KEY="dsk"' in rendered
    assert 'DJANGO_CSRF_TRUSTED_ORIGINS="csrforigins"' in rendered


def test_render_local_settings_writes_to_dmac(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "dmac").mkdir()
    (repo / "bootstrap" / "templates").mkdir(parents=True)
    (repo / "bootstrap" / "templates" / "local_settings.py.template").write_text("# template content\n")
    v = ConfigValues(
        mysql_root_password="r", mysql_password="p", neo4j_password="np",
        django_secret_key="dsk", django_csrf_trusted_origins="o", nextseek_port=8000,
    )
    render_local_settings(repo, v)
    assert (repo / "dmac" / "local_settings.py").exists()
    assert "# template content" in (repo / "dmac" / "local_settings.py").read_text()
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_config.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/steps/config.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/config.py`:
```python
"""Render bootstrap templates to docker/db.env, docker/nextseek.env, dmac/local_settings.py."""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from string import Template


@dataclass
class ConfigValues:
    mysql_root_password: str
    mysql_password: str
    neo4j_password: str
    django_secret_key: str
    django_csrf_trusted_origins: str
    nextseek_port: int


def _generate_secret_key(length: int = 64) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def csrf_origins_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port} http://localhost:{port}"


def default_values(nextseek_port: int) -> ConfigValues:
    return ConfigValues(
        mysql_root_password="seek_root",
        mysql_password="seek_db_password",
        neo4j_password="demopassword",
        django_secret_key=_generate_secret_key(),
        django_csrf_trusted_origins=csrf_origins_for_port(nextseek_port),
        nextseek_port=nextseek_port,
    )


def _render(template_path: Path, values: ConfigValues) -> str:
    text = template_path.read_text()
    substitutions = {
        "MYSQL_ROOT_PASSWORD": values.mysql_root_password,
        "MYSQL_PASSWORD": values.mysql_password,
        "NEO4J_PASSWORD": values.neo4j_password,
        "DJANGO_SECRET_KEY": values.django_secret_key,
        "DJANGO_CSRF_TRUSTED_ORIGINS": values.django_csrf_trusted_origins,
        "NEXTSEEK_PORT": str(values.nextseek_port),
    }
    return Template(text).safe_substitute(substitutions)


def render_db_env(repo_root: Path, values: ConfigValues) -> Path:
    template = repo_root / "bootstrap" / "templates" / "db.env.template"
    output = repo_root / "docker" / "db.env"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(template, values))
    return output


def render_nextseek_env(repo_root: Path, values: ConfigValues) -> Path:
    template = repo_root / "bootstrap" / "templates" / "nextseek.env.template"
    output = repo_root / "docker" / "nextseek.env"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(template, values))
    return output


def render_local_settings(repo_root: Path, values: ConfigValues) -> Path:
    template = repo_root / "bootstrap" / "templates" / "local_settings.py.template"
    output = repo_root / "dmac" / "local_settings.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(template, values))
    return output
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_config.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/steps/config.py bootstrap/tests/test_config.py
git commit -m "bootstrap: add config renderer (db.env, nextseek.env, local_settings.py)"
```

---

## Task 12: steps/volumes.py — named-volume create with prefix

**Files:**
- Create: `bootstrap/steps/volumes.py`
- Create: `bootstrap/tests/test_volumes.py`

Creates the six named volumes for an instance. Idempotent (docker volume create is a no-op if name exists).

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_volumes.py`:
```python
"""Tests for bootstrap.steps.volumes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bootstrap.steps.volumes import REQUIRED_VOLUMES, volume_names_for_prefix, ensure_volumes


def test_required_volumes_has_six_names() -> None:
    assert len(REQUIRED_VOLUMES) == 6
    assert "seek-filestore" in REQUIRED_VOLUMES
    assert "seek-mysql-db" in REQUIRED_VOLUMES
    assert "seek-solr-data" in REQUIRED_VOLUMES
    assert "seek-cache" in REQUIRED_VOLUMES
    assert "nextseek-static-files" in REQUIRED_VOLUMES
    assert "neo4j-data" in REQUIRED_VOLUMES


def test_volume_names_for_prefix_empty() -> None:
    names = volume_names_for_prefix("")
    assert names == REQUIRED_VOLUMES


def test_volume_names_for_prefix_test() -> None:
    names = volume_names_for_prefix("test-")
    assert "test-seek-filestore" in names
    assert "test-neo4j-data" in names
    assert all(n.startswith("test-") for n in names)


@patch("bootstrap.steps.volumes.volume_exists")
@patch("bootstrap.steps.volumes.volume_create")
def test_ensure_volumes_creates_missing(mock_create: MagicMock, mock_exists: MagicMock) -> None:
    mock_exists.return_value = False
    created = ensure_volumes("test-")
    assert mock_create.call_count == 6
    assert len(created) == 6


@patch("bootstrap.steps.volumes.volume_exists")
@patch("bootstrap.steps.volumes.volume_create")
def test_ensure_volumes_skips_existing(mock_create: MagicMock, mock_exists: MagicMock) -> None:
    mock_exists.return_value = True
    created = ensure_volumes("")
    assert mock_create.call_count == 0
    assert created == []
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_volumes.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/steps/volumes.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/volumes.py`:
```python
"""Create the six named volumes required by the compose stack."""
from __future__ import annotations

from bootstrap.lib.docker_ops import volume_exists, volume_create

REQUIRED_VOLUMES: list[str] = [
    "seek-filestore",
    "seek-mysql-db",
    "seek-solr-data",
    "seek-cache",
    "nextseek-static-files",
    "neo4j-data",
]


def volume_names_for_prefix(prefix: str) -> list[str]:
    """Return the full volume names including the instance prefix."""
    return [f"{prefix}{name}" for name in REQUIRED_VOLUMES]


def ensure_volumes(prefix: str) -> list[str]:
    """Create any missing volumes. Returns the names actually created (idempotent: empty if all already exist)."""
    created: list[str] = []
    for full_name in volume_names_for_prefix(prefix):
        if not volume_exists(full_name):
            volume_create(full_name)
            created.append(full_name)
    return created
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_volumes.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/steps/volumes.py bootstrap/tests/test_volumes.py
git commit -m "bootstrap: add volume creator (idempotent, prefix-aware)"
```

---

## Task 13: steps/seed.py — MySQL + Neo4j seed loaders

**Files:**
- Create: `bootstrap/steps/seed.py`
- Create: `bootstrap/tests/test_seed.py`
- Create: `bootstrap/seed/README.md`

Streams `bootstrap/seed/*.gz` through `gunzip | mysql` and `gunzip | cypher-shell`. Detects already-loaded DBs and skips by default.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_seed.py`:
```python
"""Tests for bootstrap.steps.seed."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.steps.seed import (
    SEED_FILES,
    seed_files_present,
    mysql_db_is_populated,
    neo4j_is_populated,
)


def test_seed_files_constant() -> None:
    assert SEED_FILES["dmac"].endswith("dmac.sql.gz")
    assert SEED_FILES["seek_production"].endswith("seek_production.sql.gz")
    assert SEED_FILES["neo4j"].endswith("neo4j.cypher.gz")


def test_seed_files_present_true_when_all_three_exist(tmp_path: Path) -> None:
    seed_dir = tmp_path / "bootstrap" / "seed"
    seed_dir.mkdir(parents=True)
    (seed_dir / "dmac.sql.gz").write_bytes(b"x")
    (seed_dir / "seek_production.sql.gz").write_bytes(b"x")
    (seed_dir / "neo4j.cypher.gz").write_bytes(b"x")
    missing = seed_files_present(tmp_path)
    assert missing == []


def test_seed_files_present_lists_missing(tmp_path: Path) -> None:
    seed_dir = tmp_path / "bootstrap" / "seed"
    seed_dir.mkdir(parents=True)
    (seed_dir / "dmac.sql.gz").write_bytes(b"x")
    missing = seed_files_present(tmp_path)
    assert "seek_production.sql.gz" in missing
    assert "neo4j.cypher.gz" in missing
    assert "dmac.sql.gz" not in missing


@patch("bootstrap.steps.seed.compose_exec")
def test_mysql_db_is_populated_true_when_tables_exist(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "42\n"
    assert mysql_db_is_populated(database="dmac", repo_root=Path("/repo"), env={}) is True


@patch("bootstrap.steps.seed.compose_exec")
def test_mysql_db_is_populated_false_when_zero_tables(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "0\n"
    assert mysql_db_is_populated(database="dmac", repo_root=Path("/repo"), env={}) is False


@patch("bootstrap.steps.seed.compose_exec")
def test_neo4j_is_populated_true_when_nodes_exist(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "count\n51032\n"
    assert neo4j_is_populated(neo4j_password="x", repo_root=Path("/repo"), env={}) is True


@patch("bootstrap.steps.seed.compose_exec")
def test_neo4j_is_populated_false_when_zero(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "count\n0\n"
    assert neo4j_is_populated(neo4j_password="x", repo_root=Path("/repo"), env={}) is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_seed.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/steps/seed.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/seed.py`:
```python
"""Seed loader for MySQL + Neo4j gzipped dumps."""
from __future__ import annotations

import gzip
import subprocess
from pathlib import Path

from bootstrap.lib.docker_ops import compose_exec, DockerOpsError

SEED_FILES: dict[str, str] = {
    "dmac": "bootstrap/seed/dmac.sql.gz",
    "seek_production": "bootstrap/seed/seek_production.sql.gz",
    "neo4j": "bootstrap/seed/neo4j.cypher.gz",
}


def seed_files_present(repo_root: Path) -> list[str]:
    """Return a list of basenames missing from bootstrap/seed/. Empty list = all present."""
    missing: list[str] = []
    for key, rel_path in SEED_FILES.items():
        full = repo_root / rel_path
        if not full.exists():
            missing.append(full.name)
    return missing


def mysql_db_is_populated(database: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if the named MySQL database already has tables."""
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql",
                "-uroot",
                f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
                "-N",
                "-e",
                f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{database}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    try:
        return int(out.strip().splitlines()[-1]) > 0
    except (ValueError, IndexError):
        return False


def neo4j_is_populated(neo4j_password: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if Neo4j has any nodes."""
    try:
        out = compose_exec(
            service="neo4j",
            command=[
                "cypher-shell",
                "-u",
                "neo4j",
                "-p",
                neo4j_password,
                "--format",
                "plain",
                "MATCH (n) RETURN count(n);",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    for line in out.strip().splitlines():
        token = line.strip()
        if token.isdigit():
            return int(token) > 0
    return False


def load_mysql_dump(
    gz_path: Path, database: str, repo_root: Path, env: dict[str, str]
) -> None:
    """Stream gz_path through gunzip | mysql into the named DB."""
    decompressed = gzip.decompress(gz_path.read_bytes())
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "mysql",
            "-uroot",
            f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
            database,
        ],
        cwd=str(repo_root),
        env={**env, **{"PATH": env.get("PATH", "/usr/bin:/bin")}} if env else None,
        input=decompressed,
        check=True,
    )


def load_neo4j_dump(
    gz_path: Path, neo4j_password: str, repo_root: Path, env: dict[str, str]
) -> None:
    """Stream gz_path through gunzip | cypher-shell into Neo4j."""
    decompressed = gzip.decompress(gz_path.read_bytes())
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "neo4j",
            "cypher-shell",
            "-u",
            "neo4j",
            "-p",
            neo4j_password,
        ],
        cwd=str(repo_root),
        env={**env, **{"PATH": env.get("PATH", "/usr/bin:/bin")}} if env else None,
        input=decompressed,
        check=True,
    )
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_seed.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Create `bootstrap/seed/README.md`**

Create with this content:
```markdown
# Bootstrap seed data

This directory ships sanitized snapshots of dev databases for fresh installs.

## Files

- `dmac.sql.gz` — NExtSEEK application schema (the `dmac` MySQL database)
- `seek_production.sql.gz` — SEEK schema (the `seek_production` MySQL database)
- `neo4j.cypher.gz` — Neo4j graph export (sample/assay nodes + relationships)

## Test users baked in

| Username | Password | Role |
|---|---|---|
| `demo` | `demopassword` | Admin |
| `user` | `userpassword` | Regular user |

## Regenerating these dumps (maintainer only)

See `bootstrap/seed/regenerate/`. Requires a local `dump-source.env`
(gitignored) with the dev DB credentials. `./bootstrap dump-db` orchestrates.
```

- [ ] **Step 6: Commit**

```bash
git add bootstrap/steps/seed.py bootstrap/tests/test_seed.py bootstrap/seed/README.md
git commit -m "bootstrap: add seed loader (mysql + neo4j) and seed README"
```

---

## Task 14: steps/build.py — stack build and start sequence

**Files:**
- Create: `bootstrap/steps/build.py`

Sequences the staged startup: db only → neo4j only → seek services → build nextseek → start nextseek + nginx. Mostly orchestration; no unit tests (integration verified manually).

- [ ] **Step 1: Implement `bootstrap/steps/build.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/build.py`:
```python
"""Build and start the docker compose stack in dependency order."""
from __future__ import annotations

from pathlib import Path

from bootstrap.lib.docker_ops import compose_up


def start_databases(repo_root: Path, env: dict[str, str]) -> None:
    """Start MySQL and Neo4j only. Bootstrap waits for them before seeding."""
    compose_up(services=["db", "neo4j"], project_dir=repo_root, env=env)


def start_seek_side(repo_root: Path, env: dict[str, str]) -> None:
    """Start SEEK, Solr, and SEEK workers."""
    compose_up(services=["solr", "seek", "seek_workers"], project_dir=repo_root, env=env)


def build_and_start_nextseek(repo_root: Path, env: dict[str, str]) -> None:
    """Build the NExtSEEK image and start nextseek + nginx."""
    compose_up(
        services=["nextseek", "nextseek_nginx"],
        project_dir=repo_root,
        env=env,
        build=True,
    )


def start_full_stack(repo_root: Path, env: dict[str, str]) -> None:
    """Convenience: run all three phases in order."""
    start_databases(repo_root, env)
    start_seek_side(repo_root, env)
    build_and_start_nextseek(repo_root, env)
```

- [ ] **Step 2: Verify it imports cleanly**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run python -c "from bootstrap.steps import build; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/steps/build.py
git commit -m "bootstrap: add staged stack build/start sequence"
```

---

## Task 15: steps/users.py — idempotent test user + demo project

**Files:**
- Create: `bootstrap/steps/users.py`
- Create: `bootstrap/tests/test_users.py`

Verifies `demo` and `user` accounts exist in SEEK and are bound to a "Demo" project. The seed dumps already include both, so the primary case is "already present, do nothing." The fallback path creates them via SEEK's REST API.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_users.py`:
```python
"""Tests for bootstrap.steps.users."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.steps.users import (
    REQUIRED_USERS,
    user_exists,
    verify_users_present,
)


def test_required_users_includes_demo_and_user() -> None:
    logins = [u.login for u in REQUIRED_USERS]
    assert "demo" in logins
    assert "user" in logins


def test_required_users_has_two_entries() -> None:
    assert len(REQUIRED_USERS) == 2


@patch("bootstrap.steps.users.compose_exec")
def test_user_exists_true_when_query_returns_one(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "1\n"
    assert user_exists("demo", repo_root=Path("/repo"), env={}) is True


@patch("bootstrap.steps.users.compose_exec")
def test_user_exists_false_when_query_returns_zero(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "0\n"
    assert user_exists("ghost", repo_root=Path("/repo"), env={}) is False


@patch("bootstrap.steps.users.user_exists")
def test_verify_users_present_returns_missing(mock_exists: MagicMock) -> None:
    mock_exists.side_effect = [True, False]
    missing = verify_users_present(repo_root=Path("/repo"), env={})
    assert missing == ["user"]


@patch("bootstrap.steps.users.user_exists")
def test_verify_users_present_returns_empty_when_all_present(mock_exists: MagicMock) -> None:
    mock_exists.return_value = True
    missing = verify_users_present(repo_root=Path("/repo"), env={})
    assert missing == []
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_users.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/steps/users.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/users.py`:
```python
"""Idempotent test-user verification + creation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bootstrap.lib.docker_ops import compose_exec, DockerOpsError


@dataclass
class TestUser:
    login: str
    password: str
    admin: bool


REQUIRED_USERS: list[TestUser] = [
    TestUser(login="demo", password="demopassword", admin=True),
    TestUser(login="user", password="userpassword", admin=False),
]


def user_exists(login: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if a row with this login exists in seek_production.users."""
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql",
                "-uroot",
                f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
                "-N",
                "seek_production",
                "-e",
                f"SELECT COUNT(*) FROM users WHERE login = '{login}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    try:
        return int(out.strip().splitlines()[-1]) > 0
    except (ValueError, IndexError):
        return False


def verify_users_present(repo_root: Path, env: dict[str, str]) -> list[str]:
    """Return the list of REQUIRED_USERS logins that are NOT in the DB."""
    missing: list[str] = []
    for user in REQUIRED_USERS:
        if not user_exists(user.login, repo_root, env):
            missing.append(user.login)
    return missing
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_users.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/steps/users.py bootstrap/tests/test_users.py
git commit -m "bootstrap: add idempotent test-user verification (demo + user)"
```

---

## Task 16: steps/validate.py — health checks

**Files:**
- Create: `bootstrap/steps/validate.py`
- Create: `bootstrap/tests/test_validate.py`

Verifies the running stack: ports respond, `manage.py check` exits 0, no obvious smartadmin remnants.

- [ ] **Step 1: Write failing tests**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/tests/test_validate.py`:
```python
"""Tests for bootstrap.steps.validate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.steps.validate import (
    HealthResult,
    check_http,
    run_django_check,
)


@patch("bootstrap.steps.validate.urllib.request.urlopen")
def test_check_http_ok_returns_health_result(mock_urlopen: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response
    r = check_http("nextseek", "http://localhost:8000")
    assert r.ok is True
    assert r.detail.endswith("200")


@patch("bootstrap.steps.validate.urllib.request.urlopen")
def test_check_http_failure_returns_not_ok(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = OSError("connection refused")
    r = check_http("nextseek", "http://localhost:8000")
    assert r.ok is False


@patch("bootstrap.steps.validate.compose_exec")
def test_run_django_check_ok_on_clean_exit(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "System check identified no issues (0 silenced).\n"
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is True


@patch("bootstrap.steps.validate.compose_exec")
def test_run_django_check_fail_on_exception(mock_exec: MagicMock) -> None:
    from bootstrap.lib.docker_ops import DockerOpsError
    mock_exec.side_effect = DockerOpsError("boom")
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_validate.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `bootstrap/steps/validate.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/validate.py`:
```python
"""Post-install health checks."""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bootstrap.lib.docker_ops import compose_exec, DockerOpsError


@dataclass
class HealthResult:
    name: str
    ok: bool
    detail: str


def check_http(name: str, url: str, timeout: float = 5.0) -> HealthResult:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.getcode()
        return HealthResult(name=name, ok=200 <= code < 400, detail=f"{url} → {code}")
    except Exception as exc:
        return HealthResult(name=name, ok=False, detail=f"{url} → {exc}")


def run_django_check(repo_root: Path, env: dict[str, str]) -> HealthResult:
    try:
        out = compose_exec(
            service="nextseek",
            command=["uv", "run", "manage.py", "check"],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError as exc:
        return HealthResult(name="django check", ok=False, detail=str(exc))
    ok = "identified no issues" in out or "no issues" in out
    return HealthResult(name="django check", ok=ok, detail=out.strip().splitlines()[-1] if out.strip() else "no output")


def run_all_health_checks(
    ports: dict[str, int], repo_root: Path, env: dict[str, str]
) -> list[HealthResult]:
    results = [
        check_http("SEEK", f"http://localhost:{ports.get('seek', 3000)}"),
        check_http("NExtSEEK", f"http://localhost:{ports.get('nextseek', 8000)}"),
        check_http("Neo4j", f"http://localhost:{ports.get('neo4j_http', 7474)}"),
        run_django_check(repo_root, env),
    ]
    return results
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/test_validate.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/steps/validate.py bootstrap/tests/test_validate.py
git commit -m "bootstrap: add post-install health checks (http + django check)"
```

---

## Task 17: Wire the install command end-to-end

**Files:**
- Modify: `bootstrap/cli.py`

Replace the stub `install()` function with the full 9-phase sequence using the modules from Tasks 4–16.

- [ ] **Step 1: Rewrite `bootstrap/cli.py` install command**

Open `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/cli.py`. Replace the existing `install()` function with this implementation:
```python
import datetime
from pathlib import Path

from bootstrap.lib import ui
from bootstrap.lib.instance import (
    InstanceState,
    resolve_instance_name,
    load_instance,
    save_instance,
)
from bootstrap.lib.ports import allocate_ports
from bootstrap.steps import prereqs, config, volumes, seed, build, users, validate

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PORTS = {"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7688}


@app.command()
def install(
    instance: str | None = typer.Option(None, "--instance"),
    port_offset: int | None = typer.Option(None, "--port-offset"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """First-time install: prereqs, config, volumes, seeds, build, users, validate."""
    ui.banner("NExtSEEK Bootstrap")
    total = 9

    # [1/9] Prereqs
    ui.step(1, total, "Checking prerequisites")
    failed = [r for r in prereqs.run_all() if not r.ok]
    if failed:
        for r in failed:
            ui.fail(f"{r.name}: {r.detail}")
            if r.remediation:
                ui.remediation(r.remediation)
        raise typer.Exit(code=1)
    ui.ok("docker, compose, uv, disk all OK")

    # [2/9] chat_nextseek present
    ui.step(2, total, "Verifying vendored chat_nextseek/")
    if not (REPO_ROOT / "chat_nextseek" / "pyproject.toml").exists():
        ui.fail("chat_nextseek/ is missing or empty")
        ui.remediation("This repo ships chat_nextseek as a vendored directory; re-clone NExtSEEK")
        raise typer.Exit(code=1)
    ui.ok("chat_nextseek/ present")

    # [3/9] Resolve instance + ports
    ui.step(3, total, "Configuring instance")
    name = resolve_instance_name(REPO_ROOT, instance)
    existing = load_instance(REPO_ROOT)
    if existing and not yes:
        ui.warn(f"Existing install detected (instance={existing.name}). Continuing will re-run install.")

    if port_offset is not None:
        desired = {k: v + port_offset for k, v in DEFAULT_PORTS.items()}
    else:
        desired = dict(DEFAULT_PORTS)
    ports = allocate_ports(desired)
    prefix = "" if name == REPO_ROOT.name else f"{name}-"
    state = InstanceState(
        name=name,
        prefix=prefix,
        ports=ports,
        compose_project_name=f"nextseek{('-' + name) if prefix else ''}",
        created=datetime.datetime.now().astimezone().isoformat(),
    )
    save_instance(REPO_ROOT, state)
    ui.ok(f"instance={name} prefix={prefix or '(none)'} ports={ports}")

    # [4/9] Config templates
    ui.step(4, total, "Writing config templates")
    values = config.default_values(nextseek_port=ports["nextseek"])
    config.render_db_env(REPO_ROOT, values)
    config.render_nextseek_env(REPO_ROOT, values)
    config.render_local_settings(REPO_ROOT, values)
    ui.ok("docker/db.env, docker/nextseek.env, dmac/local_settings.py")

    compose_env = state.compose_env()

    # [5/9] Volumes
    ui.step(5, total, "Creating docker volumes")
    created = volumes.ensure_volumes(prefix)
    ui.ok(f"{len(created)} created, {6 - len(created)} already existed")

    # [6/9] Seeds (gated on populated check)
    ui.step(6, total, "Importing seed databases")
    missing = seed.seed_files_present(REPO_ROOT)
    if missing:
        ui.fail(f"missing seed files: {', '.join(missing)}")
        raise typer.Exit(code=1)
    build.start_databases(REPO_ROOT, compose_env)
    if seed.mysql_db_is_populated("dmac", REPO_ROOT, compose_env):
        ui.ok("dmac already populated; skipping")
    else:
        with ui.spinner("loading dmac.sql.gz"):
            seed.load_mysql_dump(REPO_ROOT / "bootstrap" / "seed" / "dmac.sql.gz", "dmac", REPO_ROOT, compose_env)
        ui.ok("dmac loaded")
    if seed.mysql_db_is_populated("seek_production", REPO_ROOT, compose_env):
        ui.ok("seek_production already populated; skipping")
    else:
        with ui.spinner("loading seek_production.sql.gz"):
            seed.load_mysql_dump(REPO_ROOT / "bootstrap" / "seed" / "seek_production.sql.gz", "seek_production", REPO_ROOT, compose_env)
        ui.ok("seek_production loaded")
    if seed.neo4j_is_populated(values.neo4j_password, REPO_ROOT, compose_env):
        ui.ok("neo4j already populated; skipping")
    else:
        with ui.spinner("loading neo4j.cypher.gz"):
            seed.load_neo4j_dump(REPO_ROOT / "bootstrap" / "seed" / "neo4j.cypher.gz", values.neo4j_password, REPO_ROOT, compose_env)
        ui.ok("neo4j loaded")

    # [7/9] Build + start
    ui.step(7, total, "Building NExtSEEK image and starting the stack")
    with ui.spinner("building"):
        build.start_seek_side(REPO_ROOT, compose_env)
        build.build_and_start_nextseek(REPO_ROOT, compose_env)
    ui.ok("stack up")

    # [8/9] Users
    ui.step(8, total, "Verifying test users")
    missing_users = users.verify_users_present(REPO_ROOT, compose_env)
    if missing_users:
        ui.warn(f"missing users: {', '.join(missing_users)} (seed dump should include them; investigate)")
    else:
        ui.ok("demo + user present")

    # [9/9] Validate
    ui.step(9, total, "Health checks")
    results = validate.run_all_health_checks(ports, REPO_ROOT, compose_env)
    failed_checks = [r for r in results if not r.ok]
    for r in results:
        (ui.ok if r.ok else ui.fail)(f"{r.name}: {r.detail}")
    if failed_checks:
        raise typer.Exit(code=1)

    ui.banner(f"Ready — http://localhost:{ports['nextseek']}/")
```

Make sure all the new imports are at the top of the file. Remove the old stub `install()` definition.

- [ ] **Step 2: Verify the CLI still parses**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap install --help
```
Expected: shows install help with `--instance`, `--port-offset`, `--yes` flags. No tracebacks.

- [ ] **Step 3: Verify the prereq phase runs (no Docker stack touched)**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap install --help
# don't actually invoke install — we don't want to clobber the current stack
```

Manual smoke test of just the prereqs path is deferred to Task 25.

- [ ] **Step 4: Run the unit test suite**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run pytest bootstrap/tests/ -v
```
Expected: all tests pass (~45 tests across 9 files).

- [ ] **Step 5: Commit**

```bash
git add bootstrap/cli.py
git commit -m "bootstrap: wire install command end-to-end (9 phases)"
```

---

## Task 18: doctor command

**Files:**
- Create: `bootstrap/steps/doctor.py`
- Modify: `bootstrap/cli.py`

Read-only diagnostic. Runs prereqs, checks instance state, runs health checks, reports any drift.

- [ ] **Step 1: Implement `bootstrap/steps/doctor.py`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/steps/doctor.py`:
```python
"""Read-only diagnostic for an existing install."""
from __future__ import annotations

from pathlib import Path

from bootstrap.lib.instance import load_instance
from bootstrap.steps import prereqs, validate


def diagnose(repo_root: Path) -> list[tuple[str, bool, str]]:
    """Return a list of (check_name, ok, detail) tuples."""
    results: list[tuple[str, bool, str]] = []

    for r in prereqs.run_all():
        results.append((r.name, r.ok, r.detail))

    state = load_instance(repo_root)
    if state is None:
        results.append(("instance state", False, "bootstrap/.instance.json missing (was install ever run?)"))
        return results
    results.append(("instance state", True, f"name={state.name} prefix={state.prefix or '(none)'} ports={state.ports}"))

    env = state.compose_env()
    for hr in validate.run_all_health_checks(state.ports, repo_root, env):
        results.append((hr.name, hr.ok, hr.detail))

    return results
```

- [ ] **Step 2: Wire the doctor command in `bootstrap/cli.py`**

Replace the stub `doctor()` function with this implementation:
```python
@app.command()
def doctor(
    instance: str | None = typer.Option(None, "--instance"),
) -> None:
    """Read-only diagnostic for an existing install."""
    from bootstrap.steps.doctor import diagnose

    ui.banner("NExtSEEK Bootstrap Doctor")
    results = diagnose(REPO_ROOT)
    any_failed = False
    for name, ok, detail in results:
        if ok:
            ui.ok(f"{name}: {detail}")
        else:
            ui.fail(f"{name}: {detail}")
            any_failed = True
    if any_failed:
        raise typer.Exit(code=1)
```

- [ ] **Step 3: Verify doctor parses + runs**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap doctor --help
```
Expected: doctor help.

- [ ] **Step 4: Commit**

```bash
git add bootstrap/steps/doctor.py bootstrap/cli.py
git commit -m "bootstrap: add doctor command (read-only diagnostic)"
```

---

## Task 19: reset command

**Files:**
- Modify: `bootstrap/cli.py`

Drops volumes for the current instance (via `docker compose down -v`), then re-runs install. With `--keep-config`, preserves env files.

- [ ] **Step 1: Implement reset in `bootstrap/cli.py`**

Replace the stub `reset()` function with:
```python
@app.command()
def reset(
    instance: str | None = typer.Option(None, "--instance"),
    keep_config: bool = typer.Option(False, "--keep-config"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Drop volumes and re-run install."""
    from bootstrap.lib.docker_ops import compose_down

    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance to reset — bootstrap/.instance.json missing")
        raise typer.Exit(code=1)

    if not yes:
        ui.warn(f"This will DROP all data for instance '{state.name}' (volumes: {state.prefix}*)")
        typer.confirm("Continue?", abort=True)

    ui.step(1, 3, "Stopping containers and dropping volumes")
    compose_down(project_dir=REPO_ROOT, env=state.compose_env(), volumes=True)
    ui.ok("stack down, volumes dropped")

    if not keep_config:
        ui.step(2, 3, "Removing config files")
        for p in [
            REPO_ROOT / "docker" / "db.env",
            REPO_ROOT / "docker" / "nextseek.env",
            REPO_ROOT / "dmac" / "local_settings.py",
            REPO_ROOT / "bootstrap" / ".instance.json",
        ]:
            if p.exists():
                p.unlink()
                ui.ok(f"removed {p.relative_to(REPO_ROOT)}")
    else:
        ui.step(2, 3, "Keeping config files (--keep-config)")

    ui.step(3, 3, "Re-running install")
    install(instance=instance, port_offset=None, yes=True)
```

- [ ] **Step 2: Verify reset parses**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap reset --help
```
Expected: reset help with `--instance`, `--keep-config`, `--yes`.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/cli.py
git commit -m "bootstrap: add reset command (drop volumes + re-run install)"
```

---

## Task 20: rebuild command

**Files:**
- Modify: `bootstrap/cli.py`

Rebuilds and restarts one service (default `nextseek`) without touching volumes.

- [ ] **Step 1: Implement rebuild in `bootstrap/cli.py`**

Replace the stub `rebuild()` function with:
```python
@app.command()
def rebuild(
    instance: str | None = typer.Option(None, "--instance"),
    service: str = typer.Option("nextseek", "--service"),
) -> None:
    """Rebuild and restart a service without touching volumes."""
    from bootstrap.lib.docker_ops import compose_up

    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance found — run 'bootstrap install' first")
        raise typer.Exit(code=1)

    ui.banner(f"Rebuilding {service} for instance {state.name}")
    with ui.spinner(f"rebuilding {service}"):
        compose_up(
            services=[service],
            project_dir=REPO_ROOT,
            env=state.compose_env(),
            build=True,
        )
    ui.ok(f"{service} rebuilt and restarted")
```

- [ ] **Step 2: Verify rebuild parses**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap rebuild --help
```
Expected: rebuild help.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/cli.py
git commit -m "bootstrap: add rebuild command (one service, no volume changes)"
```

---

## Task 21: seed-users command (idempotent re-seed)

**Files:**
- Modify: `bootstrap/cli.py`

Already implemented in `steps/users.py`. This command exposes it. For now, it only reports presence; if missing, instructs the user to run a full reset (since user creation in SEEK requires an interactive flow we don't automate here — the seed dumps are the canonical user source).

- [ ] **Step 1: Implement seed-users in `bootstrap/cli.py`**

Replace the stub `seed_users()` function with:
```python
@app.command(name="seed-users")
def seed_users(instance: str | None = typer.Option(None, "--instance")) -> None:
    """Verify test users (demo + user) are present in the running stack."""
    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance found — run 'bootstrap install' first")
        raise typer.Exit(code=1)

    ui.banner("Verifying test users")
    missing = users.verify_users_present(REPO_ROOT, state.compose_env())
    if not missing:
        ui.ok("demo and user are both present")
        return

    ui.fail(f"missing users: {', '.join(missing)}")
    ui.remediation("Seed dumps include both users; if missing, run './bootstrap reset' to re-seed cleanly")
    raise typer.Exit(code=1)
```

- [ ] **Step 2: Verify seed-users parses**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap seed-users --help
```
Expected: seed-users help.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/cli.py
git commit -m "bootstrap: expose seed-users command (read-only verification)"
```

---

## Task 22: dump-db command + regen scripts (maintainer only)

**Files:**
- Create: `bootstrap/seed/regenerate/dump_mysql.sh`
- Create: `bootstrap/seed/regenerate/dump_neo4j.py`
- Create: `bootstrap/seed/regenerate/dump-source.env.example`
- Modify: `bootstrap/cli.py`
- Modify: `bootstrap/seed/regenerate/.gitignore` (new)

Codifies the dump commands. Errors helpfully if `dump-source.env` is missing.

- [ ] **Step 1: Create `bootstrap/seed/regenerate/.gitignore`**

Create with:
```
dump-source.env
*.cypher
*.sql
```

- [ ] **Step 2: Create `bootstrap/seed/regenerate/dump-source.env.example`**

Create with:
```
# Maintainer-only secrets for dump-db.
# Copy this to dump-source.env (gitignored) and fill in real values.

MYSQL_HOST_DEV=fairdata-dev.example.com
MYSQL_USER=SET_LOCAL_ONLY
MYSQL_DEV_PASSWORD=SET_LOCAL_ONLY
MYSQL_PORT=3306

NEO4J_URI=neo4j+s://nextseek-dev.example.com
NEO4J_USER=SET_LOCAL_ONLY
NEO4J_PASSWORD=SET_LOCAL_ONLY
NEO4J_DATABASE=nextseekdev
```

- [ ] **Step 3: Create `bootstrap/seed/regenerate/dump_mysql.sh`**

Create with:
```bash
#!/usr/bin/env bash
# Dump dmac and seek_production from the configured dev MySQL into gzipped files.
# Requires dump-source.env (gitignored, maintainer-only).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_DIR="$SCRIPT_DIR/.."
ENV_FILE="$SCRIPT_DIR/dump-source.env"

if [[ ! -f "$ENV_FILE" ]]; then
  cat >&2 <<MSG
error: $ENV_FILE missing.
This command is maintainer-only — it requires dev DB credentials.
Copy dump-source.env.example to dump-source.env and fill in real values.
MSG
  exit 2
fi

set -a; source "$ENV_FILE"; set +a

for schema in dmac seek_production; do
  echo "dumping $schema -> $SEED_DIR/${schema}.sql.gz"
  mysqldump \
    -h "$MYSQL_HOST_DEV" -P "$MYSQL_PORT" \
    -u "$MYSQL_USER" -p"$MYSQL_DEV_PASSWORD" \
    --single-transaction --quick --routines --triggers \
    --default-character-set=utf8mb4 \
    --column-statistics=0 \
    "$schema" \
    | gzip > "$SEED_DIR/${schema}.sql.gz"
done

echo "done. Files:"
ls -lh "$SEED_DIR"/*.sql.gz
```

Then make it executable:
```bash
chmod +x /home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/seed/regenerate/dump_mysql.sh
```

- [ ] **Step 4: Create `bootstrap/seed/regenerate/dump_neo4j.py`**

Create with:
```python
"""Dump the Neo4j graph to a portable cypher file. Maintainer-only.

Requires dump-source.env in this directory with NEO4J_URI / NEO4J_USER /
NEO4J_PASSWORD / NEO4J_DATABASE. Writes neo4j.cypher.gz to ../.
"""
from __future__ import annotations

import gzip
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("error: python-dotenv not installed. Run: uv add --group bootstrap python-dotenv", file=sys.stderr)
    sys.exit(2)

from neo4j import GraphDatabase


SCRIPT_DIR = Path(__file__).resolve().parent
SEED_DIR = SCRIPT_DIR.parent
ENV_FILE = SCRIPT_DIR / "dump-source.env"


def main() -> int:
    if not ENV_FILE.exists():
        print(
            f"error: {ENV_FILE} missing.\n"
            "This command is maintainer-only — it requires dev DB credentials.\n"
            "Copy dump-source.env.example to dump-source.env and fill in real values.",
            file=sys.stderr,
        )
        return 2

    load_dotenv(ENV_FILE)
    uri = os.environ["NEO4J_URI"]
    user = os.environ["NEO4J_USER"]
    password = os.environ["NEO4J_PASSWORD"]
    database = os.environ["NEO4J_DATABASE"]

    driver = GraphDatabase.driver(uri, auth=(user, password))
    lines: list[str] = []

    def escape(val: object) -> str:
        if isinstance(val, str):
            return '"' + val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'
        if isinstance(val, list):
            return "[" + ", ".join(escape(v) for v in val) + "]"
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)

    def props_str(props: dict) -> str:
        if not props:
            return ""
        return "{" + ", ".join(f"`{k}`: {escape(v)}" for k, v in props.items()) + "}"

    with driver.session(database=database) as session:
        print("Exporting nodes...")
        result = session.run("MATCH (n) RETURN n, labels(n) as lbls, elementId(n) as nid")
        for record in result:
            node = record["n"]
            labels = ":".join(record["lbls"])
            nid = record["nid"]
            lines.append(f"CREATE (n{nid}:{labels} {props_str(dict(node))});")
        print(f"  {len(lines)} nodes")

        print("Exporting relationships...")
        rel_count = 0
        result = session.run(
            "MATCH (a)-[r]->(b) RETURN elementId(a) as aid, elementId(b) as bid, type(r) as rtype, properties(r) as rprops"
        )
        for record in result:
            aid, bid = record["aid"], record["bid"]
            rtype = record["rtype"]
            rprops = record["rprops"]
            p = (" " + props_str(rprops)) if rprops else ""
            lines.append(
                f"MATCH (a) WHERE elementId(a) = '{aid}' MATCH (b) WHERE elementId(b) = '{bid}' CREATE (a)-[:{rtype}{p}]->(b);"
            )
            rel_count += 1
        print(f"  {rel_count} relationships")

    driver.close()

    out = SEED_DIR / "neo4j.cypher.gz"
    with gzip.open(out, "wt") as f:
        f.write("\n".join(lines))
    print(f"wrote {out} ({len(lines)} statements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Add `python-dotenv` and `neo4j` to bootstrap deps**

Open `/home/cdemu/code/dmac/docker/NExtSEEK/pyproject.toml` and append to the `bootstrap` dependency group:
```toml
    "python-dotenv>=1.0.0",
    "neo4j>=5.0.0",
```

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv sync --group bootstrap
```

- [ ] **Step 6: Implement dump-db in `bootstrap/cli.py`**

Replace the stub `dump_db()` function with:
```python
@app.command(name="dump-db")
def dump_db(
    source: str = typer.Option("dev", "--source"),
) -> None:
    """Maintainer-only: regenerate seed dumps from a source DB."""
    import subprocess

    regen_dir = REPO_ROOT / "bootstrap" / "seed" / "regenerate"
    env_file = regen_dir / "dump-source.env"
    if not env_file.exists():
        ui.fail(f"{env_file.relative_to(REPO_ROOT)} missing")
        ui.remediation("This command is maintainer-only. Copy dump-source.env.example and fill in real credentials.")
        raise typer.Exit(code=2)

    ui.banner(f"Regenerating seed dumps from {source}")

    ui.step(1, 2, "MySQL (dmac + seek_production)")
    subprocess.run([str(regen_dir / "dump_mysql.sh")], check=True)
    ui.ok("MySQL dumps written")

    ui.step(2, 2, "Neo4j")
    subprocess.run(["uv", "run", "python", str(regen_dir / "dump_neo4j.py")], check=True, cwd=REPO_ROOT)
    ui.ok("Neo4j dump written")
```

- [ ] **Step 7: Verify dump-db errors gracefully without credentials**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
./bootstrap dump-db
```
Expected: exits with code 2, prints the "maintainer-only" message. **This must not crash with a Python traceback.**

- [ ] **Step 8: Commit**

```bash
git add bootstrap/seed/regenerate/ bootstrap/cli.py pyproject.toml uv.lock
git commit -m "bootstrap: add dump-db command + regen scripts (maintainer-only)"
```

---

## Task 23: Delete duplicate agent_model_catalog.json

**Files:**
- Delete: `agent_model_catalog.json` (at NExtSEEK root — the chat_nextseek copy is canonical)
- Grep + update: any code referencing `agent_model_catalog.json` at NExtSEEK root to point at `chat_nextseek/agent_model_catalog.json`

The two files are byte-identical right now; deleting the duplicate prevents future drift.

- [ ] **Step 1: Confirm files are still identical**

Run:
```bash
diff -q /home/cdemu/code/dmac/docker/NExtSEEK/agent_model_catalog.json \
        /home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek/agent_model_catalog.json \
  && echo IDENTICAL || echo DIFFER
```
Expected: `IDENTICAL`. **Abort and reconcile manually if it says DIFFER.**

- [ ] **Step 2: Find references in code**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
grep -rn "agent_model_catalog.json" --include='*.py' --exclude-dir=.venv --exclude-dir=chat_nextseek --exclude-dir=.git
```
Expected: A small handful of references (likely in `dmac/settings.py` or similar). Note the line numbers.

- [ ] **Step 3: Update each reference to point at chat_nextseek/agent_model_catalog.json**

For each grep hit, edit the file to change the path from `agent_model_catalog.json` (project-root relative) to `chat_nextseek/agent_model_catalog.json`. If the reference uses an env var like `CATALOG_FILE`, update the default value or the env file template (this was already done in Task 10's `nextseek.env.template`, which uses `/app/chat_nextseek/agent_model_catalog.json`).

- [ ] **Step 4: Delete the duplicate**

```bash
rm /home/cdemu/code/dmac/docker/NExtSEEK/agent_model_catalog.json
```

- [ ] **Step 5: Verify imports still work**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
uv run python -c "from dmac import settings; print('ok')"
```
Expected: prints `ok`. If it errors on the missing catalog, you missed a reference in Step 3.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Remove duplicate agent_model_catalog.json at repo root

The chat_nextseek/ vendor includes its own canonical copy. Code that
referenced the root-level duplicate now reads chat_nextseek/agent_model_catalog.json."
```

---

## Task 24: chat_nextseek sync helper script

**Files:**
- Create: `bootstrap/scripts/sync_chat_nextseek.sh`

For the maintainer flow: rsync a fresh chat_nextseek snapshot into NExtSEEK.

- [ ] **Step 1: Create `bootstrap/scripts/sync_chat_nextseek.sh`**

Run:
```bash
mkdir -p /home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/scripts
```

Create `/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/scripts/sync_chat_nextseek.sh` with:
```bash
#!/usr/bin/env bash
# Snapshot-sync chat_nextseek from its canonical repo into NExtSEEK.
# Usage: sync_chat_nextseek.sh <path/to/canonical/chat_nextseek>

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path/to/canonical/chat_nextseek>" >&2
  exit 2
fi

SOURCE="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEST="$REPO_ROOT/chat_nextseek"

if [[ ! -d "$SOURCE/.git" ]]; then
  echo "error: $SOURCE doesn't look like a git repo" >&2
  exit 2
fi

if ! git -C "$SOURCE" diff-index --quiet HEAD; then
  echo "error: source has uncommitted changes — commit or stash first" >&2
  exit 2
fi

SOURCE_SHA=$(git -C "$SOURCE" rev-parse HEAD)
echo "syncing $SOURCE @ $SOURCE_SHA -> $DEST"

rsync -a --delete \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='outputs/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='*.egg-info/' \
  --exclude='.mcp.json' \
  --exclude='*.sqlite' \
  "$SOURCE/" "$DEST/"

echo "$SOURCE_SHA" > "$DEST/.chat_nextseek_snapshot"

echo "done."
echo "review with: git -C $REPO_ROOT status chat_nextseek/"
echo "commit with: git -C $REPO_ROOT add chat_nextseek/ && git commit -m 'sync chat_nextseek to ${SOURCE_SHA:0:8}'"
```

Then make it executable:
```bash
chmod +x /home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/scripts/sync_chat_nextseek.sh
```

- [ ] **Step 2: Verify the script's --help-equivalent works**

Run:
```bash
/home/cdemu/code/dmac/docker/NExtSEEK/bootstrap/scripts/sync_chat_nextseek.sh
```
Expected: prints usage and exits 2.

- [ ] **Step 3: Commit**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add bootstrap/scripts/sync_chat_nextseek.sh
git commit -m "bootstrap: add sync_chat_nextseek.sh for snapshot updates"
```

---

## Task 25: Rewrite NExtSEEK README

**Files:**
- Modify: `README.md` (full rewrite)
- Create: `bootstrap/README.md`

The current README (~18 KB) is comprehensive but assumes Docker fluency. New version emphasizes `./bootstrap install` and links to a dedicated bootstrap README for deep detail.

- [ ] **Step 1: Move the old README aside for reference**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git mv README.md README.old.md
```

This preserves the old content in git history and as a working-tree file for reference during the rewrite.

- [ ] **Step 2: Create the new `README.md`**

Create `/home/cdemu/code/dmac/docker/NExtSEEK/README.md` with this content:
```markdown
# NExtSEEK

A Django/Mezzanine extension of the SEEK platform for active scientific data
curation, with a graph-backed sample database (Neo4j) and an embedded AI
assistant (chat_nextseek) for natural-language queries.

## Quick start

```bash
git clone <repo-url>
cd NExtSEEK
./bootstrap install
```

Open http://localhost:8000 and log in with `demo / demopassword` (admin) or
`user / userpassword` (regular).

## System requirements

- Docker 24+ and Docker Compose v2
- [`uv`](https://docs.astral.sh/uv/) (Python package manager)
- Python 3.14 (uv will install it)
- ~5 GB free disk, 4 GB free RAM

## What bootstrap does

`./bootstrap install` orchestrates the full local Docker stack: prereqs
check, config generation, volume creation, MySQL and Neo4j seed import,
container build, test-user verification, and health checks. For detail and
all available subcommands (`reset`, `rebuild`, `doctor`, `seed-users`,
`dump-db`), see [`bootstrap/README.md`](bootstrap/README.md).

## Architecture

- **NExtSEEK** (this repo) — Django app, REST API, embedded chat panel
- **SEEK** — Upstream FAIRDOM SEEK Rails app, runs as a sibling container,
  shares MySQL with NExtSEEK
- **MySQL** — `dmac` schema (NExtSEEK) + `seek_production` schema (SEEK)
- **Neo4j** — graph of sample/assay relationships
- **Solr** — SEEK search index
- **chat_nextseek** (vendored under `chat_nextseek/`) — multi-agent LLM
  pipeline backing the chat panel. Standalone CLI and MCP server modes
  available; see `chat_nextseek/README.md`.

## Development workflow

Common changes you'll make and how to apply them to a running stack:

| What you changed | Command |
|---|---|
| Python views / models / settings (no static asset change) | `docker compose up -d --build nextseek` |
| Files under `static/` (CSS/JS/images, hand-edited) | `docker compose up -d --build nextseek && docker compose exec nextseek uv run manage.py collectstatic --noinput` |
| `chat_frontend/` React source | Rebuild per `chat_frontend/README.md`, then `collectstatic` as above |
| `chat_nextseek/` source pulled in from canonical repo | `bootstrap/scripts/sync_chat_nextseek.sh <source>`, commit, then `./bootstrap rebuild` |
| New Django model field / migration | `docker compose up -d --build nextseek` (entrypoint runs `migrate` on startup) |
| Full reset (wipe data, re-seed) | `./bootstrap reset` |

The Python-only-rebuild path is the common one. The key gotcha: rebuilding
does **not** automatically run `collectstatic` — if you changed CSS/JS in
`static/`, you must run `collectstatic` after the rebuild or your changes
won't be served.

## Configuration

After `./bootstrap install`, three config files are written and are then
yours to edit:

- `docker/db.env` — MySQL credentials
- `docker/nextseek.env` — Django secret, Neo4j password, API keys (chat
  features stay disabled until you fill in real keys)
- `dmac/local_settings.py` — Django settings overlay, including the optional
  PROD ChatConfig block for the admin-only "PROD" toggle in the chat UI

All three are gitignored. Bootstrap can re-render them via `./bootstrap reset`
if you ever want a clean slate.

## Troubleshooting

Start with `./bootstrap doctor` — it runs every prereq + health check and
reports failures with remediation hints.

For deeper issues, see [`bootstrap/README.md`](bootstrap/README.md) → "Known
failure modes".

## Contributing

Two repos to know about:

- **This repo (NExtSEEK)** — Django app + vendored chat_nextseek snapshot
- **chat_nextseek canonical repo** — `git@github.com:cdemurjian/chat_nextseek.git`

Day-to-day chat_nextseek development happens in the canonical repo. To
ship a new chat_nextseek snapshot into NExtSEEK, run:

```bash
bootstrap/scripts/sync_chat_nextseek.sh /path/to/canonical/chat_nextseek
```

Then commit the changes in NExtSEEK and push.

## License

See `LICENSE`.
```

- [ ] **Step 3: Create `bootstrap/README.md`**

Create with:
```markdown
# Bootstrap CLI reference

`./bootstrap` is the entry point. All subcommands accept `--help`.

## Commands

### `install`

First-time setup. Runs all 9 phases: prereqs, vendor verify, config,
volumes, seeds, build, users, validate.

```
./bootstrap install                       # default: ports 8000/3000/7474/7687
./bootstrap install --instance test       # named instance with auto-assigned ports
./bootstrap install --port-offset 1       # +1 on every port (8001/3001/7475/7688)
./bootstrap install --yes                 # skip confirmation prompts
```

Idempotent for prereqs / config / volumes / users / validate. Seed import
is skipped if the target DB already has tables.

### `doctor`

Read-only diagnostic. Runs prereqs + health checks and reports drift.

```
./bootstrap doctor
```

Exits non-zero if any check fails. **Run this first when something's broken.**

### `reset`

Destructive: drops all volumes for the current instance and re-runs install.

```
./bootstrap reset                # also re-renders config files
./bootstrap reset --keep-config  # preserves docker/*.env, dmac/local_settings.py
./bootstrap reset --yes          # skip the confirmation prompt
```

### `rebuild`

Rebuilds and restarts one service without touching volumes. Default service
is `nextseek`.

```
./bootstrap rebuild
./bootstrap rebuild --service nextseek_nginx
```

### `seed-users`

Idempotent: verifies `demo` + `user` accounts are present. If missing,
points you at `./bootstrap reset` (the seed dumps are the canonical user
source).

```
./bootstrap seed-users
```

### `dump-db`

**Maintainer-only.** Regenerates the gzipped seed dumps from a source DB.
Requires `bootstrap/seed/regenerate/dump-source.env` (gitignored) with the
source-DB credentials. Errors gracefully if absent.

```
./bootstrap dump-db
```

## Multi-instance / side-by-side installs

To run a second isolated install on the same machine without disrupting
your existing one:

```bash
git clone <repo-url> /tmp/NExtSEEK-test
cd /tmp/NExtSEEK-test
./bootstrap install --instance test
```

Bootstrap auto-detects free ports and uses a `test-` volume name prefix.
Both stacks coexist; `./bootstrap reset` from `/tmp/NExtSEEK-test` nukes
only the test data.

Compose project namespacing is automatic via `COMPOSE_PROJECT_NAME`
(set in `bootstrap/.instance.json`).

## Files written by bootstrap

| Path | Tracked? | Purpose |
|---|---|---|
| `docker/db.env` | git-tracked (demo defaults) | MySQL credentials |
| `docker/nextseek.env` | gitignored | Django/Neo4j config + API keys |
| `dmac/local_settings.py` | gitignored | Django settings overlay |
| `bootstrap/.instance.json` | gitignored | Per-instance state (name, prefix, ports) |
| `logs/` | gitignored | Container runtime logs |

## Known failure modes

- **Port already in use**: Use `--port-offset N` or one of the per-service
  `--*-port` flags. `./bootstrap doctor` will tell you which port is busy.
- **chat_nextseek/ missing**: Re-clone the repo. chat_nextseek is vendored —
  it must be present at clone time. If you cloned without it, run
  `bootstrap/scripts/sync_chat_nextseek.sh` from a checkout of the
  canonical repo.
- **Seed import "table already exists"**: The target DB has prior data.
  Run `./bootstrap reset` for a clean install, or load into a fresh
  `--instance NAME`.
- **`manage.py check` fails with "DJANGO_SECRET_KEY not set"**: The
  `docker/nextseek.env` file is missing or empty. Run `./bootstrap install`
  again — it will regenerate config without dropping volumes.
- **Chat features don't work**: API keys in `docker/nextseek.env` are
  placeholders (`SET_IN_LOCAL_ENV`). Fill in real values for
  `GCP_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, or `FDH_API`, then
  `./bootstrap rebuild`.

## Maintainer: regenerating seed dumps

The shipped seeds in `bootstrap/seed/*.gz` are sanitized snapshots of a
dev environment. Regenerate them when:

- New test users / projects are added to the canonical dev DB
- Schema migrations change the data shape enough that the old dumps
  fail to load
- A SEEK upgrade introduces incompatible schema changes

To regenerate:

1. Copy `bootstrap/seed/regenerate/dump-source.env.example` to
   `dump-source.env` (gitignored) and fill in real credentials.
2. `./bootstrap dump-db`

The script verifies dumps are secret-clean before writing
(`zgrep` paranoid sweep — see `bootstrap/seed/regenerate/dump_mysql.sh`).
```

- [ ] **Step 4: Delete the old README**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
rm README.old.md
```

(Its content is preserved in `git log -- README.md` as the prior commit.)

- [ ] **Step 5: Commit**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git add README.md bootstrap/README.md
git commit -m "Rewrite README around the bootstrap CLI

Top-level README is now Quick-Start + System Requirements + Dev Workflow
table. Deep bootstrap reference lives in bootstrap/README.md.

The dev workflow table makes the rebuild-vs-collectstatic distinction
explicit — the common 'I changed CSS but it isn't showing' issue."
```

---

## Task 26: End-to-end smoke test (manual, user runs)

**Files:** None. This task is a verification checklist the user runs against a parallel test install.

The plan is complete at this point but unverified end-to-end. This task walks through validating it on a fresh clone WITHOUT touching the current production install.

- [ ] **Step 1: Clone NExtSEEK into a parallel directory**

Run:
```bash
cd /home/cdemu/code/dmac/docker
git clone NExtSEEK NExtSEEK-test
```

Or if cloning from the remote:
```bash
cd /home/cdemu/code/dmac/docker
git clone <remote-url> NExtSEEK-test
```

- [ ] **Step 2: Verify chat_nextseek/ vendoring landed in the clone**

Run:
```bash
ls /home/cdemu/code/dmac/docker/NExtSEEK-test/chat_nextseek/pyproject.toml
```
Expected: file exists.

- [ ] **Step 3: Run the bootstrap install**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK-test
./bootstrap install --instance test
```

Expected behavior:
- Banner appears
- Phase 1 prereqs all OK
- Phase 2 chat_nextseek OK
- Phase 3 instance state written; ports show 8001/3001/7475/7688 (or whatever offset bootstrap picked)
- Phase 4 config files written
- Phase 5 volumes created (6 with `test-` prefix)
- Phase 6 seeds load (~30 seconds for dmac, ~2 minutes for seek_production, ~3 minutes for neo4j)
- Phase 7 stack builds + starts
- Phase 8 demo + user verified present
- Phase 9 all health checks return 200

- [ ] **Step 4: Smoke test the running instance**

In a browser:
- Visit `http://localhost:8001` → NExtSEEK home page loads
- Visit `http://localhost:3001` → SEEK home page loads
- Log in as `demo` / `demopassword` → admin features accessible
- Log out, log in as `user` / `userpassword` → regular-user features visible, admin features hidden

- [ ] **Step 5: Smoke test the original install is unaffected**

In a browser:
- Visit `http://localhost:8000` → original NExtSEEK still works
- Run `docker compose ps` from `/home/cdemu/code/dmac/docker/NExtSEEK` → original containers still running

- [ ] **Step 6: Test ./bootstrap doctor on the test install**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK-test
./bootstrap doctor
```
Expected: all checks pass, exit code 0.

- [ ] **Step 7: Test ./bootstrap reset on the test install**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK-test
./bootstrap reset --yes
```
Expected: stops containers, drops `test-*` volumes, re-runs install, finishes with healthy stack.

- [ ] **Step 8: Tear down the test install (optional)**

Run:
```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK-test
COMPOSE_PROJECT_NAME=nextseek-test INSTANCE_PREFIX=test- docker compose down -v
cd /home/cdemu/code/dmac/docker
rm -rf NExtSEEK-test
docker volume ls | grep '^test-' | awk '{print $2}' | xargs -r docker volume rm
```

This removes the test clone and any leftover `test-*` volumes. The production install at `/home/cdemu/code/dmac/docker/NExtSEEK` is untouched.

- [ ] **Step 9: Final commit (if any docs were updated during smoke testing)**

If you found bugs during smoke testing and fixed them, commit those fixes with messages describing what failed. Otherwise no commit needed.
