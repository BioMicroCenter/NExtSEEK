# Step 2 — Multi-user Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded single-user Dropbox provisioning with project-stratified per-user directory isolation, so the Container-CC agent sees only the requesting user's private dir + their SEEK project's shared dir (both read-only), with absolute cross-user isolation.

**Architecture:** A new host-side provisioning primitive (`cc_provision.py`) resolves the logged-in user's SEEK project (credentialed `SeekDB.getCurrentUser()`) into a typed `ProjectIdentity`, and a single path-builder yields every nested host/mount path under one consolidated `DMAC_USER_ROOT/<project>/<user>/{input,scratch,cc-state,output}` + `<project>/shared/`. `cc_engine` mount construction and the `services/cc_assistant.py` caller are reworked to consume the builder; the four old `CCPaths` roots are removed in a final cleanup so no caller can reproduce the flat shape.

**Tech Stack:** Python 3.12, Django (host process), docker-py (CC sibling containers), pytest (hermetic units via `uv run`), SEEK/FAIRDOM REST (`seek/seekdb.py`, `seek/seekapi.py`).

## Global Constraints

- **Spec of record:** `nextseek_api/cc_assistant/SPEC-2-multi-user-provisioning.md` (locked decisions D1–D5 in §11). Every task below traces to a spec section.
- **TDD-first**, bite-sized steps, frequent commits. Implementation code only after a failing test.
- **Hermetic test command (the box cannot run the Django test-DB runner):**
  `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
  Run from `/home/taishajo/work/NExtSEEK`. No Docker, no DB, no network, no spend.
- **No regression** to 1b `--resume`, 1c memory, the NS route, or **OI-3** (zero-creds agent: SEEK resolution runs host-side in the trusted Django process using the user's own login; **no new credential enters the agent**).
- **Validate-before-interpolate** is a hard precondition: `cc_engine._build_volumes` interpolates path segments without re-validating, so every new path segment (`<project>`, `<user>`, `<session>`) MUST be validated before it reaches a bind source.
- **Credentialed SEEK only:** construct `SeekDB(None, api_user, api_pass)`. The bare `SeekDB(None, None, None)` (`nextseek_api/views.py:596`) is NOT user-scoped — never copy it.
- **D1 container mounts:** RO inputs land at `/data/input` (private) + `/data/shared` (project). `/data/scratch` (RW), Claude home (`/home/user/.claude`), `/data/output` keep their container paths.
- **D4 fallback (hybrid):** confirmed-empty SEEK membership → `personal-<user>` namespace (still isolated, no `shared/` peers); SEEK call failure/outage → **fail-closed reject** (never guess a project).
- **D5 host root:** end state is ONE `DMAC_USER_ROOT` (the four old roots are removed in Task 7). The migration is incremental-then-cleanup, but the committed final shape is single-root.
- **Deadline:** NExtSEEK prod before 2026-07-14.

---

## File Structure

- **Create** `nextseek_api/cc_assistant/cc_provision.py` — the provisioning primitive: `slugify_project()`, `ProjectIdentity`, `resolve_user_project()`, `ProjectResolutionError`, and the path-builder `UserDirs` + `build_user_dirs()`. Pure functions + a SEEK-resolution function that takes an injectable factory so it stays hermetically testable.
- **Modify** `nextseek_api/cc_assistant/cc_config.py` — `CCPaths` collapses from four host roots to one `host_user_root` + `user_root_mount` (D5); remove `projects_for()` + `_DEFAULT_USER_PROJECTS` (Task 7).
- **Modify** `nextseek_api/cc_assistant/cc_engine.py` — `_build_volumes` + `run_cc_turn` consume `build_user_dirs`; add container mount-point constants `_CONTAINER_INPUT` / `_CONTAINER_SHARED`; validate `<project>`; repoint `_publish_artifacts`/`_dropbox_display`.
- **Modify** `nextseek_api/services/cc_assistant.py` — call `resolve_user_project`, thread `project_dirname` into `run_cc_turn`, repoint `_session_metas` + the inline 1c `mem_root`/translation to the nested cc-state path; fail-closed on resolution error.
- **Modify** `seek/seekdb.py` — add a public `getProjectName(projectid)` accessor (the name-mangled `__getProjectName` must not be reached across the boundary).
- **Create** test files under `nextseek_api/cc_assistant/tests/`: `test_cc_provision_slug.py`, `test_cc_provision_resolve.py`, `test_cc_provision_paths.py`, `test_cc_provision_isolation.py`, `test_cc_migration_grep_guard.py`. Extend the existing `test_cc_engine_volumes.py`.
- **Modify** `docker/docker-compose*.yml` + `docker/nextseek.env` (Task 8) — single `DMAC_USER_ROOT` bind + `/data/input` + `/data/shared` binds.

---

### Task 1: `slugify_project()` + `ProjectIdentity`

**Files:**
- Create: `nextseek_api/cc_assistant/cc_provision.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_provision_slug.py`

**Interfaces:**
- Produces: `slugify_project(title: str) -> str`; `ProjectIdentity` (frozen dataclass) with fields `id: str`, `title: str`, `slug: str` and a property `dirname -> str` returning `f"{id}-{slug}"`.

Spec refs: §5 (slug helper, D2), §3 (`{id}-{slug}` dir name), ANN-6 (dedicated helper, never inline).

- [ ] **Step 1: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_provision_slug.py
"""Hermetic tests for the Step-2 slug helper + ProjectIdentity. No Django, no network."""
import pytest

from nextseek_api.cc_assistant.cc_provision import slugify_project, ProjectIdentity


@pytest.mark.parametrize("title,expected", [
    ("Liver Tox (NDMA) study", "liver-tox-ndma-study"),
    ("Already-slugged", "already-slugged"),
    ("   leading/trailing   ", "leading-trailing"),
    ("UPPER_case__Mix", "upper-case-mix"),
    ("a..b//c", "a-b-c"),
    ("café déjà", "caf-dj"),          # non-ascii dropped, runs collapsed
    ("!!!", ""),                       # degenerate -> empty (dir still unique via id prefix)
    ("", ""),
])
def test_slugify_project_rule(title, expected):
    assert slugify_project(title) == expected


def test_slugify_project_is_deterministic():
    assert slugify_project("Liver Tox") == slugify_project("liver   tox")


def test_project_identity_dirname():
    pid = ProjectIdentity(id="42", title="Liver Tox (NDMA) study", slug="liver-tox-ndma-study")
    assert pid.dirname == "42-liver-tox-ndma-study"


def test_project_identity_dirname_with_degenerate_slug():
    pid = ProjectIdentity(id="7", title="!!!", slug="")
    assert pid.dirname == "7-"   # id prefix guarantees uniqueness even when slug is empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_slug.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_provision'`

- [ ] **Step 3: Write the minimal implementation**

```python
# nextseek_api/cc_assistant/cc_provision.py
"""Step 2 — multi-user provisioning primitive: SEEK project resolution + the
nested per-user/per-project directory path-builder.

Runs HOST-side in the trusted Django process. Per OI-3 no credential added here
ever enters the sandboxed agent: SEEK is queried as the logged-in user with their
own login, and only path strings flow on to the CC bind sources.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# D2: strict filesystem-safe slug. Cosmetic only — the dir is "{id}-{slug}", so the
# SEEK id (not the slug) guarantees uniqueness/stability. A degenerate slug is fine.
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_project(title: str) -> str:
    """Lower-case; collapse every run of non-[a-z0-9] to a single '-'; trim '-'.

    Non-ASCII is best-effort transliterated to ASCII then dropped if it has no
    ASCII form. Deterministic and robust to case, spaces, punctuation, unicode.
    """
    if not isinstance(title, str):
        title = str(title or "")
    norm = unicodedata.normalize("NFKD", title)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    return _NON_SLUG_RE.sub("-", ascii_only.lower()).strip("-")


@dataclass(frozen=True)
class ProjectIdentity:
    """A resolved SEEK project (or a synthetic personal namespace). The dir name
    is always ``{id}-{slug}`` so the SEEK id is the stable key."""

    id: str
    title: str
    slug: str

    @property
    def dirname(self) -> str:
        return f"{self.id}-{self.slug}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_slug.py`
Expected: PASS (all parametrized cases + the two identity tests)

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/cc_provision.py nextseek_api/cc_assistant/tests/test_cc_provision_slug.py
git commit -m "feat(cc-step2): slugify_project helper + ProjectIdentity (D2, §5)"
```

---

### Task 2: `resolve_user_project()` + D4 fallback

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_provision.py`
- Modify: `seek/seekdb.py` (add public `getProjectName`)
- Test: `nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py`

**Interfaces:**
- Consumes: `slugify_project`, `ProjectIdentity` (Task 1).
- Produces:
  - `class ProjectResolutionError(Exception)` — raised on SEEK outage/failure (fail-closed).
  - `resolve_user_project(api_user: str, api_pass: str, *, seekdb_factory=None, personal_prefix: str = "personal-") -> ProjectIdentity`
    - Builds a **credentialed** `SeekDB(None, api_user, api_pass)` via `seekdb_factory` (defaults to the real `SeekDB`; injectable for tests).
    - Reads `getCurrentUser()['data']['relationships']['projects']['data']` → resource ids.
    - **Empty membership** → `ProjectIdentity(id=f"{personal_prefix}{api_user}", title=api_user, slug=slugify_project(api_user))` (synthetic personal namespace; D4).
    - **Non-empty** → take `projects[0]`, resolve its title via `seekdb.getProjectName(id)`, return `ProjectIdentity(id, title, slugify_project(title))`.
    - **Any exception from SEEK** (network, non-200, missing keys) → raise `ProjectResolutionError` (D4 fail-closed). Empty membership is NOT an error.

Spec refs: §4 (resolution), §9 (edge cases), D4 (§11). Grounding: `seek/seekdb.py:267 getCurrentUser`, `seek/seekapi.py:187 getCurrentUser` (`/people/current`), `seek/seekdb.py:343-348 __getProjectName`, membership pattern `seek/views.py:1247,1345`.

- [ ] **Step 1: Add the public project-name accessor to SeekDB**

In `seek/seekdb.py`, immediately after the existing private `__getProjectName` (ends at line ~348), add:

```python
    def getProjectName(self, projectid):
        """Public accessor for a project's title (Step-2 provisioning needs this
        across the module boundary; the name-mangled __getProjectName is private)."""
        return self.__getProjectName(projectid)
```

- [ ] **Step 2: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py
"""Hermetic tests for resolve_user_project with a STUBBED SeekDB. No network."""
import pytest

from nextseek_api.cc_assistant.cc_provision import (
    resolve_user_project, ProjectIdentity, ProjectResolutionError,
)


class _StubSeekDB:
    """Records construction args; returns canned membership/title."""
    last_args = None

    def __init__(self, server, username, password, *, membership=None, titles=None, boom=False):
        _StubSeekDB.last_args = (server, username, password)
        self._membership = membership or []
        self._titles = titles or {}
        self._boom = boom

    def getCurrentUser(self):
        if self._boom:
            raise RuntimeError("SEEK unreachable")
        return {"data": {"relationships": {"projects": {"data": self._membership}}}}

    def getProjectName(self, projectid):
        return self._titles[str(projectid)]


def _factory(**kw):
    def make(server, username, password):
        return _StubSeekDB(server, username, password, **kw)
    return make


def test_resolves_first_project_credentialed():
    f = _factory(membership=[{"id": "42"}, {"id": "99"}],
                 titles={"42": "Liver Tox (NDMA) study"})
    pid = resolve_user_project("alice", "pw", seekdb_factory=f)
    assert pid == ProjectIdentity(id="42", title="Liver Tox (NDMA) study",
                                  slug="liver-tox-ndma-study")
    # credentialed construction: server=None, real creds (never SeekDB(None,None,None))
    assert _StubSeekDB.last_args == (None, "alice", "pw")


def test_empty_membership_falls_back_to_personal_namespace():
    pid = resolve_user_project("bob", "pw", seekdb_factory=_factory(membership=[]))
    assert pid == ProjectIdentity(id="personal-bob", title="bob", slug="bob")


def test_seek_outage_fails_closed():
    with pytest.raises(ProjectResolutionError):
        resolve_user_project("carol", "pw", seekdb_factory=_factory(boom=True))


def test_custom_personal_prefix():
    pid = resolve_user_project("dave", "pw", seekdb_factory=_factory(membership=[]),
                               personal_prefix="priv-")
    assert pid.id == "priv-dave"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py`
Expected: FAIL — `ImportError: cannot import name 'resolve_user_project'`

- [ ] **Step 4: Write the minimal implementation**

Append to `nextseek_api/cc_assistant/cc_provision.py`:

```python
class ProjectResolutionError(Exception):
    """SEEK could not be reached / returned an unusable response. Fail-closed: the
    turn is rejected rather than guessing a project (D4)."""


def _default_seekdb_factory():
    # Imported lazily so the pure helpers above stay importable without Django/seek.
    from seek.seekdb import SeekDB
    return lambda server, username, password: SeekDB(server, username, password)


def resolve_user_project(api_user, api_pass, *, seekdb_factory=None,
                         personal_prefix="personal-") -> "ProjectIdentity":
    """Resolve the logged-in user's SEEK project to a ProjectIdentity (D4 fallback).

    Empty membership -> synthetic ``personal-<user>`` namespace (isolated, no peers).
    SEEK outage/failure -> ProjectResolutionError (fail-closed; never guess).
    """
    factory = seekdb_factory or _default_seekdb_factory()
    try:
        seekdb = factory(None, api_user, api_pass)   # credentialed; server=None -> settings.SEEK_URL
        current = seekdb.getCurrentUser()
        projects = current["data"]["relationships"]["projects"]["data"]
    except Exception as exc:  # noqa: BLE001 -- any SEEK failure is fail-closed
        raise ProjectResolutionError(str(exc)) from exc

    if not projects:
        return ProjectIdentity(id=f"{personal_prefix}{api_user}", title=api_user,
                               slug=slugify_project(api_user))

    pid = str(projects[0]["id"])
    try:
        title = seekdb.getProjectName(pid)
    except Exception as exc:  # noqa: BLE001
        raise ProjectResolutionError(str(exc)) from exc
    return ProjectIdentity(id=pid, title=title, slug=slugify_project(title))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/cc_provision.py nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py seek/seekdb.py
git commit -m "feat(cc-step2): resolve_user_project + D4 fallback + public getProjectName (§4)"
```

---

### Task 3: `UserDirs` path-builder + single-root `CCPaths` (additive)

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_provision.py`
- Modify: `nextseek_api/cc_assistant/cc_config.py` (add `host_user_root` + `user_root_mount` to `CCPaths`; keep old fields for now)
- Test: `nextseek_api/cc_assistant/tests/test_cc_provision_paths.py`

**Interfaces:**
- Consumes: `ProjectIdentity` (Task 1), `CCPaths` (extended here).
- Produces:
  - `CCPaths` gains `host_user_root: str` + `user_root_mount: str`, env-loaded from `DMAC_USER_ROOT` / `DMAC_USER_ROOT_MOUNT` in `from_env`. (Old four roots remain until Task 7.)
  - `UserDirs` (frozen dataclass) — all per-turn paths as strings:
    - HOST bind sources: `input_src`, `shared_src`, `scratch_src`, `cc_state_src` (None if no session), `output_src`
    - nextseek-container MOUNT paths (for host-side mkdir/copier/memory render): `scratch_mnt`, `cc_state_mnt` (None if no session), `output_mnt`, `memory_mnt`
  - `build_user_dirs(paths: CCPaths, project_dirname: str, user_id: str, session_id: str | None = None) -> UserDirs`

Layout (spec §3):
```
<root>/<project>/shared/                         -> shared_src
<root>/<project>/<user>/input/                   -> input_src
<root>/<project>/<user>/scratch/                 -> scratch_src
<root>/<project>/<user>/cc-state/<session>/      -> cc_state_src
<root>/<project>/<user>/output/                  -> output_src
<root>/<project>/<user>/_memory/<session>/       -> memory_mnt (mount-side; 1c)
```

Spec refs: §3 (layout, D5), §6 (mount construction), §7 (single path-builder, no caller hardcodes old shape).

- [ ] **Step 1: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_provision_paths.py
"""Hermetic tests for the single path-builder. No Docker, no Django."""
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


def _paths() -> CCPaths:
    return CCPaths(
        host_dropbox_root="/legacy/dropbox", host_scratch_root="/legacy/scratch",
        host_output_root="/legacy/output", scratch_mount="/legacy/m/scratch",
        output_mount="/legacy/m/output", host_cc_state_root="/legacy/ccstate",
        cc_state_mount="/legacy/m/ccstate",
        host_user_root="/host/users", user_root_mount="/dmac/users",
    )


def test_host_sources_are_nested_under_project_and_user():
    d = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id="S1")
    assert d.shared_src == "/host/users/42-liver-tox/shared"
    assert d.input_src == "/host/users/42-liver-tox/alice/input"
    assert d.scratch_src == "/host/users/42-liver-tox/alice/scratch"
    assert d.cc_state_src == "/host/users/42-liver-tox/alice/cc-state/S1"
    assert d.output_src == "/host/users/42-liver-tox/alice/output"


def test_mount_paths_use_the_container_mount_root():
    d = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id="S1")
    assert d.scratch_mnt == "/dmac/users/42-liver-tox/alice/scratch"
    assert d.cc_state_mnt == "/dmac/users/42-liver-tox/alice/cc-state/S1"
    assert d.output_mnt == "/dmac/users/42-liver-tox/alice/output"
    assert d.memory_mnt == "/dmac/users/42-liver-tox/alice/_memory/S1"


def test_cc_state_and_memory_are_none_without_session():
    d = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id=None)
    assert d.cc_state_src is None
    assert d.cc_state_mnt is None
    assert d.memory_mnt is None
    # shared/input/scratch/output are always present
    assert d.scratch_src == "/host/users/42-liver-tox/alice/scratch"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_paths.py`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'host_user_root'`

- [ ] **Step 3: Extend `CCPaths` (additive)**

In `nextseek_api/cc_assistant/cc_config.py`, add the two fields + env defaults. Add near the other defaults:

```python
_DEFAULT_HOST_USER_ROOT = "/Users/taishajoseph/dmac-dev/users"
_DEFAULT_USER_ROOT_MOUNT = "/dmac/users"
```

Add to the `CCPaths` dataclass body (after `cc_state_mount`):

```python
    host_user_root: str = ""   # D5: single consolidated host root (bind sources)
    user_root_mount: str = ""  # D5: its nextseek-container mount (host-side I/O)
```

Add to `from_env` (inside the `cls(...)` call):

```python
            host_user_root=os.environ.get("DMAC_USER_ROOT", _DEFAULT_HOST_USER_ROOT),
            user_root_mount=os.environ.get("DMAC_USER_ROOT_MOUNT", _DEFAULT_USER_ROOT_MOUNT),
```

> NOTE: the two new fields have defaults (`""`) so the existing `CCPaths(...)` call sites and tests that don't pass them still construct. Task 7 removes the old fields and these defaults.

- [ ] **Step 4: Implement `UserDirs` + `build_user_dirs`**

Append to `nextseek_api/cc_assistant/cc_provision.py`:

```python
@dataclass(frozen=True)
class UserDirs:
    """Every per-turn path for one (project, user, session), as strings.

    ``*_src`` are HOST paths used as CC bind SOURCES. ``*_mnt`` are the same dirs
    at their nextseek-container mount points, used host-side for mkdir / the
    post-turn copier / 1c memory rendering. ``cc_state_*`` and ``memory_mnt`` are
    None when no session is given.
    """

    input_src: str
    shared_src: str
    scratch_src: str
    output_src: str
    cc_state_src: str | None
    scratch_mnt: str
    output_mnt: str
    cc_state_mnt: str | None
    memory_mnt: str | None


def build_user_dirs(paths, project_dirname: str, user_id: str,
                    session_id: str | None = None) -> UserDirs:
    """The SINGLE source of truth for the nested Step-2 layout (spec §3).

    No caller should hardcode any of these path shapes; they all come from here so
    the 1b/1c migration moves in lockstep.
    """
    host_root = paths.host_user_root.rstrip("/")
    mnt_root = paths.user_root_mount.rstrip("/")
    proj = f"{host_root}/{project_dirname}"
    proj_mnt = f"{mnt_root}/{project_dirname}"
    user_host = f"{proj}/{user_id}"
    user_mnt = f"{proj_mnt}/{user_id}"
    return UserDirs(
        input_src=f"{user_host}/input",
        shared_src=f"{proj}/shared",
        scratch_src=f"{user_host}/scratch",
        output_src=f"{user_host}/output",
        cc_state_src=(f"{user_host}/cc-state/{session_id}" if session_id else None),
        scratch_mnt=f"{user_mnt}/scratch",
        output_mnt=f"{user_mnt}/output",
        cc_state_mnt=(f"{user_mnt}/cc-state/{session_id}" if session_id else None),
        memory_mnt=(f"{user_mnt}/_memory/{session_id}" if session_id else None),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_paths.py`
Expected: PASS (3 tests). Then run the whole suite to confirm the additive `CCPaths` change broke nothing:
`uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: all prior tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/cc_provision.py nextseek_api/cc_assistant/cc_config.py nextseek_api/cc_assistant/tests/test_cc_provision_paths.py
git commit -m "feat(cc-step2): UserDirs path-builder + single-root CCPaths fields, additive (D5, §3)"
```

---

### Task 4: Rework `_build_volumes` to the nested layout

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_engine.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_engine_volumes.py` (rewrite)

**Interfaces:**
- Consumes: `build_user_dirs` (Task 3); `_validate_user_id`, `_validate_project` (existing).
- Produces (new `_build_volumes` signature):
  `_build_volumes(*, paths, project_dirname, user_id, cc_state_key, user_memory_file=None, transcripts_dir=None) -> dict[str, dict[str, str]]`
  - `input_src` → `/data/input` RO; `shared_src` → `/data/shared` RO
  - `scratch_src` → `/data/scratch` RW
  - `cc_state_src` → `/home/user/.claude` RW (only when `cc_state_key`)
  - `user_memory_file` → `/home/user/.claude/CLAUDE.md` RO (1c, unchanged)
  - `transcripts_dir` → `/home/user/.cc-memory/transcripts` RO (1c, unchanged)
  - The old `projects` loop + flat `host_scratch_root/<user>` / `host_cc_state_root/<user>/<key>` sources are GONE.

Spec refs: §6 (mount construction, D1), §8 (isolation: private RO + shared RO; only scratch RW).

- [ ] **Step 1: Add container mount-point constants**

In `cc_engine.py`, beside `_CONTAINER_SCRATCH` (line 99), add:

```python
_CONTAINER_INPUT = "/data/input"
_CONTAINER_SHARED = "/data/shared"
```

- [ ] **Step 2: Rewrite the volume tests (failing)**

Replace the body of `nextseek_api/cc_assistant/tests/test_cc_engine_volumes.py` with:

```python
"""Hermetic tests for the Step-2 nested bind-mount builder. No Docker, no Django."""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(
        host_dropbox_root="/legacy/dropbox", host_scratch_root="/legacy/scratch",
        host_output_root="/legacy/output", scratch_mount="/legacy/m/scratch",
        output_mount="/legacy/m/output", host_cc_state_root="/legacy/ccstate",
        cc_state_mount="/legacy/m/ccstate",
        host_user_root="/host/users", user_root_mount="/dmac/users",
    )


def test_input_and_shared_mounted_ro():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1")
    assert vols["/host/users/42-px/alice/input"] == {"bind": "/data/input", "mode": "ro"}
    assert vols["/host/users/42-px/shared"] == {"bind": "/data/shared", "mode": "ro"}


def test_scratch_rw_and_cc_state_rw():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1")
    assert vols["/host/users/42-px/alice/scratch"] == {"bind": "/data/scratch", "mode": "rw"}
    assert vols["/host/users/42-px/alice/cc-state/S1"] == {
        "bind": "/home/user/.claude", "mode": "rw"}


def test_cc_state_omitted_without_key():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key=None)
    assert not any(v["bind"] == "/home/user/.claude" for v in vols.values())
    # input/shared/scratch always present
    assert "/host/users/42-px/alice/scratch" in vols


def test_memory_and_transcripts_mounts_ride_along_ro():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1",
        user_memory_file="/host/users/42-px/alice/_memory/S1/CLAUDE.md",
        transcripts_dir="/host/users/42-px/alice/_memory/S1/transcripts")
    assert vols["/host/users/42-px/alice/_memory/S1/CLAUDE.md"] == {
        "bind": "/home/user/.claude/CLAUDE.md", "mode": "ro"}
    assert vols["/host/users/42-px/alice/_memory/S1/transcripts"] == {
        "bind": "/home/user/.cc-memory/transcripts", "mode": "ro"}


def test_no_legacy_flat_sources_emitted():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1")
    assert "/legacy/scratch/alice" not in vols
    assert "/legacy/ccstate/alice/S1" not in vols
    assert not any("/data/projects/" in v["bind"] for v in vols.values())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_engine_volumes.py`
Expected: FAIL — `_build_volumes() got an unexpected keyword argument 'project_dirname'` (and old `projects=` removed).

- [ ] **Step 4: Rewrite `_build_volumes`**

Replace `cc_engine._build_volumes` (lines 372–409) with:

```python
def _build_volumes(
    *,
    paths: CCPaths,
    project_dirname: str,
    user_id: str,
    cc_state_key: str | None,
    user_memory_file: str | None = None,
    transcripts_dir: str | None = None,
) -> dict[str, dict[str, str]]:
    """Bind mounts for the CC sibling (sources are HOST paths), Step-2 nested layout:
    the user's PRIVATE input + the project SHARED dir RO; per-user scratch RW; and —
    when ``cc_state_key`` is given — a per-(user, session) ``.claude`` store RW so the
    transcript persists across the ephemeral per-turn containers for ``--resume``.

    Precondition: ``project_dirname`` / ``user_id`` / ``cc_state_key`` MUST already be
    validated (``_validate_project`` / ``_validate_user_id``) — this interpolates them
    into bind-mount SOURCES without re-validating.
    """
    from .cc_provision import build_user_dirs

    dirs = build_user_dirs(paths, project_dirname, user_id, session_id=cc_state_key)
    volumes: dict[str, dict[str, str]] = {
        dirs.input_src: {"bind": _CONTAINER_INPUT, "mode": "ro"},
        dirs.shared_src: {"bind": _CONTAINER_SHARED, "mode": "ro"},
        dirs.scratch_src: {"bind": _CONTAINER_SCRATCH, "mode": "rw"},
    }
    if cc_state_key and dirs.cc_state_src:
        volumes[dirs.cc_state_src] = {"bind": _CONTAINER_CLAUDE_HOME, "mode": "rw"}
    if user_memory_file:
        volumes[user_memory_file] = {"bind": _CONTAINER_USER_MEMORY, "mode": "ro"}
    if transcripts_dir:
        volumes[transcripts_dir] = {"bind": _CONTAINER_MEMORY_TRANSCRIPTS, "mode": "ro"}
    return volumes
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_engine_volumes.py`
Expected: PASS (5 tests). `run_cc_turn` still calls the old signature — that is fixed in Task 5; the broader suite may fail on `test_cc_engine_memory_mounts.py` / realstack until then. Note any such failures and proceed.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/cc_engine.py nextseek_api/cc_assistant/tests/test_cc_engine_volumes.py
git commit -m "feat(cc-step2): nested _build_volumes (input+shared RO, scratch RW) via path-builder (D1, §6)"
```

---

### Task 5: Rework `run_cc_turn` for nested dirs + `<project>` validation

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_engine.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_engine_memory_mounts.py` (adjust call sites)

**Interfaces:**
- Consumes: `build_user_dirs` (Task 3), reworked `_build_volumes` (Task 4).
- Produces (new `run_cc_turn` parameter): replaces `projects: list[str]` with `project_dirname: str`. Everything else unchanged.
  - Validates `project_dirname` via `_validate_project` (already allows `-`/`_`/spaces, blocks `/`, `..`, NUL).
  - Computes all dirs via `build_user_dirs(paths, project_dirname, user_id, session_id=cc_state_key)`; mkdir the per-run scratch + cc-state at their `*_mnt` paths; `path_mappings` output/scratch host roots come from `dirs.output_src` / `dirs.scratch_src`.

Spec refs: §6 (dir spawn + validation), §7 (1b cc-state repoint), §8 (validation before interpolation).

- [ ] **Step 1: Adjust the existing memory-mounts test to the new signature**

In `nextseek_api/cc_assistant/tests/test_cc_engine_memory_mounts.py`, change every `_build_volumes(...)` / `run_cc_turn(...)` call that passes `projects=[...]` to pass `project_dirname="42-px"` instead, and drop the `projects=` kwarg. (Mechanical; keep assertions, updating any expected source paths to the nested `/host/users/42-px/...` shape using the `_paths()` helper from Task 4.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_engine_memory_mounts.py`
Expected: FAIL — `run_cc_turn() got an unexpected keyword argument 'project_dirname'`.

- [ ] **Step 3: Rewrite the `run_cc_turn` signature + dir-spawn**

In `cc_engine.py`:

(a) Signature — replace `projects: list[str],` (line 419) with `project_dirname: str,`.

(b) Validation block (lines 443–447) — replace the `projects` loop:

```python
    # I-4 (audit B2): validate BEFORE any path interpolation / mkdir / mount.
    _validate_user_id(user_id)
    _validate_user_id(run_id)
    _validate_project(project_dirname)
```

(c) Dir construction (lines 449–485) — replace the scratch + cc-state mkdir block with builder-driven nested paths:

```python
    from .cc_provision import build_user_dirs

    effective_session_id = session_id
    dirs = build_user_dirs(paths, project_dirname, user_id, session_id=cc_state_key)

    # Per-run working dir under the user's nested scratch. Created via the
    # nextseek-container mount so the host dir exists before the CC sibling starts.
    user_scratch = Path(dirs.scratch_mnt)
    (user_scratch / run_id).mkdir(parents=True, exist_ok=True)
    for _p in (user_scratch, user_scratch / run_id):
        try:
            os.chmod(_p, 0o777)
        except OSError:
            pass

    if cc_state_key:
        _validate_user_id(cc_state_key)  # single-segment path guard (UUID chat id)
        cc_state_dir = Path(dirs.cc_state_mnt)
        if session_id and not cc_session.store_has_transcripts(cc_state_dir):
            logger.info("cc: resume id present but store empty; starting fresh")
            effective_session_id = None
        cc_state_dir.mkdir(parents=True, exist_ok=True)
        for _p in (cc_state_dir.parent, cc_state_dir):
            try:
                os.chmod(_p, 0o777)
            except OSError:
                pass
```

(d) `_build_volumes` call (lines 482–485) — pass `project_dirname` instead of `projects`:

```python
    volumes = _build_volumes(
        paths=paths, project_dirname=project_dirname, user_id=user_id,
        cc_state_key=cc_state_key,
        user_memory_file=user_memory_file, transcripts_dir=transcripts_dir,
    )
```

(e) `path_mappings` (lines 489–494) — point at the nested host output/scratch:

```python
    path_mappings = {
        "output": {"container_root": _CONTAINER_OUTPUT, "host_root": dirs.output_src},
        "scratch": {"container_root": _CONTAINER_SCRATCH, "host_root": dirs.scratch_src},
    }
```

> The early `scratch_mount = Path(paths.scratch_mount)` / `output_mount = Path(paths.output_mount)` locals (lines 440–441) feed `snapshot_before` / `_publish_artifacts`; leave them for now — Task 7 repoints publish to the nested output. (Publish still works against the legacy mount until then; no test asserts published paths here.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_engine_memory_mounts.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/cc_engine.py nextseek_api/cc_assistant/tests/test_cc_engine_memory_mounts.py
git commit -m "feat(cc-step2): run_cc_turn takes project_dirname; nested dir-spawn + validation (§6,§7)"
```

---

### Task 6: Wire the caller — resolution + nested 1c paths + fail-closed

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py`
- Test: live (no hermetic test for the threaded `_run` closure; logic is exercised by Task 9). Add a focused unit test for the new resolution-error → `query_error` mapping if feasible (see Step 5).

**Interfaces:**
- Consumes: `resolve_user_project`, `ProjectResolutionError` (Task 2); `build_user_dirs` (Task 3); reworked `run_cc_turn` (Task 5).
- Produces: the CC branch resolves `ProjectIdentity` once, derives `project_dirname`, repoints `_session_metas` + the inline 1c `mem_root`/translation to the nested cc-state path, and passes `project_dirname` to `run_cc_turn`. On `ProjectResolutionError` → `query_error` (fail-closed), turn never runs.

Spec refs: §4 (resolution call site), §7 (1c repoint — regression-critical), §9 (fail-closed).

- [ ] **Step 1: Resolve the project on the CC branch (fail-closed)**

In `services/cc_assistant.py`, inside `_run` on the CC branch (the `else` after the NS route, around line 210, BEFORE `cc_state_key = ...`), add:

```python
                    from nextseek_api.cc_assistant.cc_provision import (
                        resolve_user_project, ProjectResolutionError, build_user_dirs)
                    try:
                        project = resolve_user_project(api_user, api_pass)
                    except ProjectResolutionError as exc:
                        logger.warning("cc-step2: project resolution failed: %s", exc)
                        send_event("query_error", {
                            "error": ("Could not resolve your SEEK project (SEEK may be "
                                      "unavailable). Please try again shortly."),
                            "agent": "container_cc", "session_id": resolved_session_id,
                        })
                        return
                    project_dirname = project.dirname
```

- [ ] **Step 2: Repoint `_session_metas` to the nested cc-state path**

`_session_metas` (line 78) currently builds `Path(paths.cc_state_mount) / user.username / sid / "projects"` and translates with `cc_state_mount`→`host_cc_state_root`. Change its signature to accept `project_dirname` and use the builder. Replace the per-session `store` + `host_path` computation:

```python
def _session_metas(user, current_id, paths, mem_cfg, project_dirname):
    """Build cc_memory.SessionMeta for the user's sessions (own sessions only)."""
    from pathlib import Path
    from nextseek_api.cc_assistant.cc_provision import build_user_dirs

    metas = []
    qs = ChatSession.objects.filter(user=user).order_by("-updated_at")
    for s in qs:
        sid = str(s.session_id)
        d = build_user_dirs(paths, project_dirname, user.username, session_id=sid)
        store = Path(d.cc_state_mnt) / "projects"
        jsonls = sorted(store.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                        reverse=True) if store.is_dir() else []
        transcript_mount_path = str(jsonls[0]) if jsonls else None
        host_path = None
        if transcript_mount_path:
            host_path = transcript_mount_path.replace(
                paths.user_root_mount.rstrip("/"), paths.host_user_root.rstrip("/"), 1)
        # ... (rest of the loop body unchanged: prev_fp, changed, metas.append) ...
```

Keep the remainder of the loop (`es`, `prev_fp`, `changed`, `metas.append(...)`) exactly as-is.

- [ ] **Step 3: Repoint the inline 1c memory block + both `_session_metas` calls**

In `_run`, update the two `_session_metas(...)` calls (lines 245, 264) to pass `project_dirname`, and repoint the `mem_root` + the host/mount string translations (lines 248–284) from the cc-state pair to the user-root pair via the builder:

```python
                        metas = _session_metas(request.user, cc_state_key, paths, mem_cfg, project_dirname)
                        tgt = cc_memory.select_sync_target(metas, current_id=cc_state_key)
                        if tgt is not None and tgt.transcript_path:
                            try:
                                mount_path = tgt.transcript_path.replace(
                                    paths.host_user_root.rstrip("/"),
                                    paths.user_root_mount.rstrip("/"), 1)
                                # ... unchanged: read_bytes, SummaryProvenance, summarize,
                                #     _persist_summary_standalone ...
                                metas = _session_metas(request.user, cc_state_key, paths, mem_cfg, project_dirname)
                            except Exception:
                                logger.exception("cc-1c: sync summarize failed; continuing")

                        window = cc_memory.select_window(
                            metas, current_id=cc_state_key, window_size=mem_cfg.window_size)
                        _dirs = build_user_dirs(paths, project_dirname, request.user.username,
                                                session_id=cc_state_key)
                        mem_root = Path(_dirs.memory_mnt)
                        md = cc_memory.render_memory(
                            window, fresh_session=False,
                            transcripts_mount=cc_engine._CONTAINER_MEMORY_TRANSCRIPTS)
                        written = cc_memory_io.write_memory_file(mem_root / "CLAUDE.md", md)
                        staged = cc_memory_io.stage_transcripts(window, mem_root / "transcripts")
                        if written:
                            user_memory_file = str(written).replace(
                                paths.user_root_mount.rstrip("/"),
                                paths.host_user_root.rstrip("/"), 1)
                        if staged:
                            transcripts_dir = str(staged).replace(
                                paths.user_root_mount.rstrip("/"),
                                paths.host_user_root.rstrip("/"), 1)
```

- [ ] **Step 4: Pass `project_dirname` to `run_cc_turn`**

Replace `projects=cc_config.projects_for(cc_user_id),` (line 290) with:

```python
                        project_dirname=project_dirname,
```

- [ ] **Step 5: Add a focused unit test for fail-closed mapping (optional but recommended)**

If a thin seam is reachable without the Django DB, add `nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py::test_outage_is_distinguishable` asserting `ProjectResolutionError` is raised (already covered in Task 2) — the caller wiring itself is verified live in Task 9. Document here that no hermetic test covers the `_run` closure (DB-bound).

- [ ] **Step 6: Run the full hermetic suite**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). The caller module imports must resolve; if `test_cc_realstack.py` is collected and needs the DB it will be skipped/errored as usual — confirm no NEW failures vs. the Task 5 baseline.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/services/cc_assistant.py
git commit -m "feat(cc-step2): caller resolves SEEK project, nested 1c paths, fail-closed (§4,§7,§9)"
```

---

### Task 7: Cleanup — remove the four legacy roots + `projects_for`, repoint publish, grep-guard

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_config.py` (remove old fields/env/`projects_for`)
- Modify: `nextseek_api/cc_assistant/cc_engine.py` (repoint `_publish_artifacts`/`_dropbox_display`; drop legacy locals)
- Modify: `nextseek_api/cc_assistant/tests/test_cc_config_paths.py` (drop legacy-field assertions)
- Test: `nextseek_api/cc_assistant/tests/test_cc_migration_grep_guard.py`

**Interfaces:**
- Consumes: everything from Tasks 3–6 now uses `host_user_root`/`user_root_mount` only.
- Produces: `CCPaths` has exactly two path fields (`host_user_root`, `user_root_mount`); `projects_for` + `_DEFAULT_USER_PROJECTS` deleted; publish writes to the nested output dir.

Spec refs: §3/§7 (single root, no caller hardcodes old shape), §10 (grep-guard), §13 (Dropbox no longer relied on).

- [ ] **Step 1: Write the grep-guard test (failing)**

```python
# nextseek_api/cc_assistant/tests/test_cc_migration_grep_guard.py
"""Guard: the legacy flat path shape / Dropbox provisioning must not reappear."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]   # nextseek_api/cc_assistant


def _read(*names):
    return "\n".join((SRC / n).read_text() for n in names)


def test_legacy_cc_paths_fields_are_gone():
    cfg = (SRC / "cc_config.py").read_text()
    for dead in ("host_dropbox_root", "host_scratch_root", "host_output_root",
                 "host_cc_state_root", "cc_state_mount", "projects_for",
                 "_DEFAULT_USER_PROJECTS"):
        assert dead not in cfg, f"legacy symbol {dead!r} still in cc_config.py"


def test_engine_and_caller_do_not_reference_legacy_roots():
    blob = _read("cc_engine.py")
    from nextseek_api.services import cc_assistant as svc  # noqa: F401
    svc_src = Path(svc.__file__).read_text()
    for dead in ("host_dropbox_root", "host_scratch_root", "host_cc_state_root",
                 "cc_state_mount", "scratch_mount", "output_mount"):
        assert dead not in blob, f"{dead!r} still referenced in cc_engine.py"
        assert dead not in svc_src, f"{dead!r} still referenced in services/cc_assistant.py"


def test_no_data_projects_mount_constant():
    blob = (SRC / "cc_engine.py").read_text()
    assert "/data/projects" not in blob
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_migration_grep_guard.py`
Expected: FAIL (legacy symbols still present).

- [ ] **Step 3: Slim `CCPaths` + delete `projects_for`**

In `cc_config.py`: delete `_DEFAULT_HOST_DROPBOX_ROOT/_SCRATCH/_OUTPUT/_SCRATCH_MOUNT/_OUTPUT_MOUNT/_CC_STATE_ROOT/_CC_STATE_MOUNT` and `_DEFAULT_USER_PROJECTS`; delete `projects_for`. Reduce `CCPaths` to:

```python
@dataclass(frozen=True)
class CCPaths:
    """Single consolidated host root (CC bind sources) + its nextseek-container mount."""

    host_user_root: str   # D5: host root for all per-project/per-user dirs (bind sources)
    user_root_mount: str  # D5: where host_user_root is mounted into the nextseek container

    @classmethod
    def from_env(cls) -> "CCPaths":
        return cls(
            host_user_root=os.environ.get("DMAC_USER_ROOT", _DEFAULT_HOST_USER_ROOT),
            user_root_mount=os.environ.get("DMAC_USER_ROOT_MOUNT", _DEFAULT_USER_ROOT_MOUNT),
        )
```

- [ ] **Step 4: Repoint publish in `cc_engine.py`**

`run_cc_turn` still has `scratch_mount`/`output_mount` locals (lines 440–441) and passes them to `snapshot_before` + `_publish_artifacts`. Replace those locals + the publish call to use the builder's nested mount paths. After `dirs = build_user_dirs(...)` (Task 5c), set:

```python
    scratch_mount = Path(dirs.scratch_mnt)   # user's nested scratch (mount side)
    output_mount = Path(dirs.output_mnt)     # user's nested output (mount side)
```

and remove the old `paths.scratch_mount` / `paths.output_mount` lines. `snapshot_before(scratch_mount, user_id)` and `_publish_artifacts(scratch_mount, output_mount, user_id, before, paths)` now operate on per-user dirs that already include `<user>`; update those helpers + `_dropbox_display` to stop re-appending `user_id` and stop using `paths.host_*`:

```python
def snapshot_before(scratch_mount: Path, user_id: str) -> dict[str, tuple[int, int]]:
    from dmac_assistant.run_tracker import snapshot_scratch_files
    return snapshot_scratch_files(scratch_mount.parent, scratch_mount.name)
```

> `snapshot_scratch_files(root, user)` joins `root/user`; passing `(scratch_mount.parent, scratch_mount.name)` reproduces `scratch_mount` exactly while keeping the dmac helper's signature. Apply the same `(parent, name)` shim in `_publish_artifacts`'s `snapshot_scratch_files` + `copy_files` calls, and compute the friendly display from `dirs.output_src` instead of `paths.host_output_root`.

Concretely, change `_publish_artifacts` to take the user's `output_src` (host path) for display and use the `(parent, name)` shim:

```python
def _publish_artifacts(
    scratch_mount: Path, output_mount: Path, user_id: str,
    before: dict[str, tuple[int, int]], output_host_root: str,
) -> list[str]:
    """Diff scratch, copy new/changed files to output, return friendly relative paths."""
    try:
        from dmac_assistant.run_tracker import snapshot_scratch_files, diff_files
        from dmac_assistant.copier import copy_files
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC: copier/run_tracker import failed (%s); no publish", type(exc).__name__)
        return []
    after = snapshot_scratch_files(scratch_mount.parent, scratch_mount.name)
    changed = diff_files(before, after)
    if not changed:
        return []
    written = copy_files(output_mount.parent, output_mount.name, scratch_mount.name, changed) \
        if False else copy_files(scratch_mount.parent, output_mount.parent, scratch_mount.name, changed)
    display: list[str] = []
    for dst in written:
        try:
            rel = dst.relative_to(output_mount.parent)
        except ValueError:
            rel = Path(dst.name)
        display.append(str(Path(output_host_root).name + "/" + str(rel)))
    return sorted(display)
```

> IMPORTANT: `copy_files`/`snapshot_scratch_files`/`diff_files` are the imported `dmac_assistant` helpers — confirm their exact signatures (`dmac_assistant/copier.py`, `dmac_assistant/run_tracker.py`) before finalizing this shim; the dev runtime is the source of truth. If the `(parent, name)` shim does not fit cleanly, prefer adding a thin nested-aware wrapper in `cc_engine` over changing the vendored dmac helpers. Update `run_cc_turn`'s `_publish_artifacts(...)` call to pass `output_host_root=dirs.output_src` and drop the `paths` arg. Delete `_dropbox_display` (no longer used) — the grep-guard does not require this but §13 says Dropbox is no longer relied on; keep the "Saved to your Dropbox" reply wording **as-is** (user-facing copy change is Step 3, out of scope) unless the user signs off on rewording.

- [ ] **Step 5: Fix `test_cc_config_paths.py`**

Update `nextseek_api/cc_assistant/tests/test_cc_config_paths.py` to construct `CCPaths(host_user_root=..., user_root_mount=...)` and assert `from_env` reads `DMAC_USER_ROOT` / `DMAC_USER_ROOT_MOUNT`. Remove assertions on the deleted fields/env vars.

- [ ] **Step 6: Run the full hermetic suite + grep-guard**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all, including `test_cc_migration_grep_guard.py`). Fix any straggling reference the grep-guard catches.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/cc_assistant/cc_config.py nextseek_api/cc_assistant/cc_engine.py \
        nextseek_api/cc_assistant/tests/test_cc_config_paths.py \
        nextseek_api/cc_assistant/tests/test_cc_migration_grep_guard.py
git commit -m "refactor(cc-step2): collapse CCPaths to single DMAC_USER_ROOT; drop projects_for + Dropbox flat shape (D5, §3,§7)"
```

---

### Task 8: Cross-user isolation acceptance tests

**Files:**
- Test: `nextseek_api/cc_assistant/tests/test_cc_provision_isolation.py`

**Interfaces:**
- Consumes: `build_user_dirs` (Task 3), `_build_volumes` (Task 4), `_validate_project`/`_validate_user_id` (existing).

Spec refs: §8 (the acceptance bar), §10 (isolation tests). This task adds no production code — it pins the isolation invariants so a future refactor can't silently break them.

- [ ] **Step 1: Write the isolation tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_provision_isolation.py
"""Cross-user isolation invariants (spec §8). Hermetic."""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(host_user_root="/host/users", user_root_mount="/dmac/users")


def _vols(project_dirname, user):
    return cc_engine._build_volumes(
        paths=_paths(), project_dirname=project_dirname, user_id=user, cc_state_key="S")


def test_same_project_shares_shared_but_not_private():
    a = _vols("42-px", "alice")
    b = _vols("42-px", "bob")
    # identical shared source
    assert "/host/users/42-px/shared" in a and "/host/users/42-px/shared" in b
    # disjoint private spaces
    assert "/host/users/42-px/alice/scratch" in a
    assert "/host/users/42-px/alice/scratch" not in b
    assert "/host/users/42-px/bob/input" in b
    assert "/host/users/42-px/bob/input" not in a


def test_different_projects_are_fully_disjoint_including_shared():
    a = _vols("42-px", "alice")
    c = _vols("99-py", "alice")
    assert set(a) & set(c) == set()   # no shared bind source at all


def test_private_and_shared_are_readonly_only_scratch_rw():
    v = _vols("42-px", "alice")
    assert v["/host/users/42-px/alice/input"]["mode"] == "ro"
    assert v["/host/users/42-px/shared"]["mode"] == "ro"
    assert v["/host/users/42-px/alice/scratch"]["mode"] == "rw"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "", "a\x00b"])
def test_malicious_project_rejected_before_interpolation(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_project(bad)


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", ".", "x" * 65])
def test_malicious_user_rejected(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)
```

- [ ] **Step 2: Run to verify**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_isolation.py`
Expected: PASS. (`_validate_project("")` already raises; if a parametrized case unexpectedly passes a malicious value, that is a real isolation bug — fix the validator, do not weaken the test.)

- [ ] **Step 3: Run the FULL suite (regression gate)**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: all PASS (this is the hermetic acceptance baseline for Step 2).

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/cc_assistant/tests/test_cc_provision_isolation.py
git commit -m "test(cc-step2): cross-user isolation invariants (§8)"
```

---

### Task 9: Deployment config — single `DMAC_USER_ROOT` + `/data/input` + `/data/shared`

**Files:**
- Modify: `docker/docker-compose.yml` (and any prod/dev override compose file the project uses)
- Modify: `docker/nextseek.env` (gitignored on the dev host; the **prod host must carry the new bind + env** — it does not ride git)
- Modify: `nextseek_api/cc_assistant/DEPLOY.md` (document the new env + bind)

**Interfaces:** purely deployment; no hermetic test. Verified by Task 10.

Spec refs: §12 (deployment, D5/D1).

- [ ] **Step 1: Add the single host root env + mount**

In `docker/nextseek.env`, replace the old `DMAC_HOST_DROPBOX_ROOT` / `DMAC_HOST_SCRATCH_ROOT` / `DMAC_HOST_OUTPUT_ROOT` / `DMAC_HOST_CC_STATE_ROOT` / `DMAC_SCRATCH_MOUNT` / `DMAC_OUTPUT_MOUNT` / `DMAC_CC_STATE_MOUNT` / `DMAC_CC_USER_PROJECTS` vars with:

```
DMAC_USER_ROOT=/absolute/host/path/to/dmac-users
DMAC_USER_ROOT_MOUNT=/dmac/users
```

(Pick the real host path for the target instance. Optional: `DMAC_CC_PERSONAL_PREFIX=personal-` if the default is overridden.)

- [ ] **Step 2: Mount the host root into the nextseek container**

In the compose service for `nextseek`, replace the old per-root binds with a single bind:

```yaml
    volumes:
      - ${DMAC_USER_ROOT}:/dmac/users
```

(The CC sibling's RO `/data/input` + `/data/shared` binds are constructed at runtime by `_build_volumes` from `DMAC_USER_ROOT` — they are NOT compose binds. Only the nextseek container needs the `/dmac/users` mount so the host-side mkdir/copier/memory render can reach the dirs.)

- [ ] **Step 3: Document in DEPLOY.md**

Add a "Step 2 deployment" note: the single `DMAC_USER_ROOT` host dir must exist and be writable by the nextseek container; on prod, create the env + bind by hand (not in git); the four legacy `DMAC_HOST_*`/`DMAC_*_MOUNT` vars are removed.

- [ ] **Step 4: Commit**

```bash
git add docker/docker-compose.yml docker/nextseek.env nextseek_api/cc_assistant/DEPLOY.md
git commit -m "chore(cc-step2): single DMAC_USER_ROOT bind/env; remove legacy roots (D5, §12)"
```

> NOTE: `docker/nextseek.env` may be gitignored — if `git add` reports it ignored, do NOT force-add secrets; instead document the required keys in DEPLOY.md and set them directly on each host.

---

### Task 10: D3 wipe + live verification (per-change sign-off)

**Files:** none (operational). This is the regression-critical gate (ANN-9): the path migration is not "done" until 1b resume + 1c memory are re-verified live at the new nested paths.

Spec refs: §7 (D3 wipe, mandatory re-verification), §10 (live verification), ANN-9 (risk).

**Pre-req:** explicit per-change sign-off from the user before touching the running instance. Use the Step-0 deploy procedure (snapshot `:pre-step2` → fast-forward the SA build-context clone → rebuild → recreate `--no-deps nextseek` via the SA `docker:cli` helper). Rollback via `/home/taishajo/work/state/rollback.sh` if needed.

- [ ] **Step 1: Snapshot for rollback**

Tag the current live image `nextseek-nextseek:pre-step2` (SA helper). Confirm `rollback.sh` still points at the pristine `:dev-rollback`.

- [ ] **Step 2: D3 — wipe the demo user's legacy flat dirs**

On the host, remove the old flat `scratch/<demo>`, `cc-state/<demo>/*`, output dirs (no migration code; dev instance, no real user data; prior 1b/1c evidence already captured). Create the new `DMAC_USER_ROOT` dir, writable by the nextseek container.

- [ ] **Step 3: Deploy the Step-2 image**

Rebuild + recreate per the Step-0 procedure. Confirm the container comes up gunicorn+celery, site returns 200, and `cc_engine.cc_runner_available() == (True, "ok")` (the `dmac-cc-net` network + bedrock-proxy are up).

- [ ] **Step 4: Verify project resolution + isolation (two logins, same project)**

Per SPEC-2 §10, drive the chat UI with Playwright (see `nextseek-playwright.md`), forced-CC, ≤ $2 cap:
- Log in as the demo user; confirm a CC turn provisions `<root>/<projectID-slug>/demo/...` (check the host dir tree) and mounts only `demo/input` + `<project>/shared` RO.
- Confirm a second distinct login (if available) in the same project shares `shared/` but cannot read the first user's `<user>/` space; two users in different projects are fully disjoint. (If only the demo user exists on dev, verify the personal-namespace path `personal-demo/...` instead and record that two-user isolation is deferred to a multi-user dev fixture.)

- [ ] **Step 5: Re-verify 1b `--resume` (no regression)**

Repeat the 1b live A/B (per `evidence/` 1b doc): a new chat, two turns — turn 1 stores a codeword, turn 2 recalls + transforms it (e.g. "Output: BANANA-42" → "BANANA-84"), no 404; confirm ONE transcript `.jsonl` under the NESTED `…/<project>/demo/cc-state/<session>/projects/` holds both turns.

- [ ] **Step 6: Re-verify 1c cross-session memory (no regression)**

Repeat the 1c live checklist (per `evidence/1c-cross-session-memory-live.md`) at the nested `…/<project>/demo/_memory/<session>/` path: a prior session's summary renders into the new session's mounted `CLAUDE.md`; Summarize runs on `gemini-3.5-flash`, StopReason STOP. Use an on-domain agentic prompt for recall (per ANN-2; the router gates content-free recall by design).

- [ ] **Step 7: Record evidence + flip the tracker**

Write the live evidence under `nextseek_api/cc_assistant/evidence/2-multi-user-live.md` (secret-scan before commit). With the user's OK, set `integration-plan.json` step **2** `status` → `done` (status field ONLY; never add keys). Capture the session with `/handoff`.

---

## Self-Review

**1. Spec coverage:**
- §3 layout / D5 single root → Tasks 3 (builder + fields), 7 (consolidation), 9 (deploy). ✔
- §4 resolution / credentialed SeekDB / D4 fallback → Task 2. ✔
- §5 slug helper / D2 → Task 1. ✔
- §6 mount construction / D1 (`/data/input` + `/data/shared`) → Tasks 4, 5. ✔
- §7 1b/1c path migration (regression-critical) → Tasks 5 (cc-state), 6 (1c memory + `_session_metas`), 10 (re-verify). ✔
- §8 isolation acceptance → Task 8. ✔
- §9 edge cases (no-project / outage / demo / multi-project) → Tasks 2, 6, 10. ✔
- §10 testing (TDD units + live) → every task's tests; Task 10 live. ✔
- §11 D1–D5 → D1 (4,5,9), D2 (1), D3 (10), D4 (2,6), D5 (3,7,9). ✔
- §12 deployment → Task 9; §13 out-of-scope (UI upload/download, raw/artifacts, Dropbox removal, multi-project, shared population) — intentionally excluded. ✔

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every code step carries the actual code. The one explicit verification dependency is the `dmac_assistant.copier`/`run_tracker` signatures in Task 7 Step 4, flagged to confirm against the runtime before finalizing the publish shim.

**3. Type consistency:** `ProjectIdentity{id,title,slug,.dirname}` (Task 1) consumed unchanged in Tasks 2/6. `UserDirs` field names (`input_src/shared_src/scratch_src/output_src/cc_state_src/scratch_mnt/output_mnt/cc_state_mnt/memory_mnt`) defined in Task 3 used identically in Tasks 4/5/6/7/8. `build_user_dirs(paths, project_dirname, user_id, session_id=)` signature stable across all callers. `_build_volumes(*, paths, project_dirname, user_id, cc_state_key, ...)` and `run_cc_turn(..., project_dirname, ...)` consistent between Tasks 4/5 and the Task 6 caller. `CCPaths` carries the legacy fields through Tasks 3–6 (additive) and sheds them in Task 7, so all interim tests construct it with both old + new fields until the Task 7 cleanup.

**Known coupling note:** Tasks 4–6 each leave the *targeted* test file green; the FULL suite is only guaranteed green again at the end of Task 6 (caller wired) and is the explicit gate at Tasks 7 Step 6 and 8 Step 3. This is the documented cost of the CCPaths shape change and is why D5/D3 are sequenced as their own tasks with live re-verification (ANN-9).
