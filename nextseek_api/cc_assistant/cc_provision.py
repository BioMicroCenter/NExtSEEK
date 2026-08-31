"""Step 2 multi-user provisioning primitives.

This module runs host-side in the trusted Django process. SEEK credentials are
used only for host-side project resolution; the sandboxed CC agent receives
only paths and its existing per-request NExtSEEK login.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")


def slugify_project(title: str) -> str:
    """Return the strict filesystem-safe project slug required by SPEC-2 D2."""
    if not isinstance(title, str):
        title = str(title or "")
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return _NON_SLUG_RE.sub("-", ascii_only.lower()).strip("-")


def _validate_segment(name: str, value: str | None) -> str:
    if (
        not isinstance(value, str)
        or not _SEGMENT_RE.fullmatch(value)
        or value in (".", "..")
        or "/" in value
        or "\x00" in value
    ):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


def project_dirname(project_id: str, slug: str) -> str:
    """Return a validated Step-2 project directory name."""
    pid = _validate_segment("project id", str(project_id or ""))
    if slug:
        _validate_segment("project slug", slug)
    return f"{pid}-{slug}"


@dataclass(frozen=True)
class ProjectIdentity:
    """Resolved SEEK project identity or a synthetic personal namespace."""

    id: str
    title: str
    slug: str

    @property
    def dirname(self) -> str:
        return project_dirname(self.id, self.slug)


@dataclass(frozen=True)
class UserDirs:
    """All CC paths for one project/user/session (G7-10 named-volume layout).

    ``*_subpath`` values are volume-relative tails under the ``dmac-cc-users``
    volume root — each is the exact ``VolumeOptions.Subpath`` for that CC
    sibling mount (there is no host bind source). ``shared_subpath`` is
    project-scoped (no ``{user_id}`` segment, SPEC-2 D5). ``transcripts_subpath``
    is the ``_memory/<session>`` tail plus a ``/transcripts`` child.

    ``*_mnt`` values are the same directories at their nextseek-container mount
    point (under ``user_root_mount``) for mkdir/chmod, artifact publishing, and
    memory rendering.

    ``scratch_*`` is the USER-scoped scratch root. It is the right scope for
    out-of-band work that is not tied to one turn (the ``cc_sweep_staging``
    recovery command), but it is NOT what a turn's agent may see: #70/#36. The
    per-turn ``run_scratch_*`` pair, populated only when ``run_id`` is given, is
    what gets mounted at ``/data/scratch`` for an agent.
    """

    # Volume-relative subpaths (each is a CC sibling mount's exact Subpath).
    input_subpath: str
    shared_subpath: str
    scratch_subpath: str
    output_subpath: str
    cc_state_subpath: str | None
    memory_subpath: str | None
    transcripts_subpath: str | None
    # nextseek-container mount paths (under user_root_mount).
    input_mnt: str
    scratch_mnt: str
    output_mnt: str
    cc_state_mnt: str | None
    memory_mnt: str | None
    # #70/#36: per-turn scratch, the agent's actual /data/scratch.
    run_scratch_subpath: str | None = None
    run_scratch_mnt: str | None = None


def build_user_dirs(
    paths,
    project_dirname: str,
    user_id: str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> UserDirs:
    """Build the single source of truth for the nested CC volume layout.

    ``run_id`` (#70/#36) adds the per-turn scratch subtree. It is validated as a
    path segment exactly like the others, because it is interpolated into a
    ``VolumeOptions.Subpath``.
    """
    mount_root = paths.user_root_mount.rstrip("/")
    if not paths.users_volume or not mount_root:
        raise ValueError("users_volume and user_root_mount are required")
    _validate_segment("project dirname", project_dirname)
    _validate_segment("user_id", user_id)
    if session_id is not None:
        _validate_segment("session_id", session_id)
    if run_id is not None:
        _validate_segment("run_id", run_id)
    # Volume-relative tails (no leading slash — an absolute Subpath is rejected
    # by the Engine and would defeat per-user isolation).
    project_rel = project_dirname
    user_rel = f"{project_rel}/{user_id}"
    project_mount = f"{mount_root}/{project_rel}"
    user_mount = f"{project_mount}/{user_id}"
    return UserDirs(
        input_subpath=f"{user_rel}/input",
        shared_subpath=f"{project_rel}/shared",  # project-scoped: NO user segment
        scratch_subpath=f"{user_rel}/scratch",
        output_subpath=f"{user_rel}/output",
        cc_state_subpath=f"{user_rel}/cc-state/{session_id}" if session_id else None,
        memory_subpath=f"{user_rel}/_memory/{session_id}" if session_id else None,
        transcripts_subpath=f"{user_rel}/_memory/{session_id}/transcripts" if session_id else None,
        input_mnt=f"{user_mount}/input",
        scratch_mnt=f"{user_mount}/scratch",
        output_mnt=f"{user_mount}/output",
        cc_state_mnt=f"{user_mount}/cc-state/{session_id}" if session_id else None,
        memory_mnt=f"{user_mount}/_memory/{session_id}" if session_id else None,
        run_scratch_subpath=f"{user_rel}/scratch/{run_id}" if run_id else None,
        run_scratch_mnt=f"{user_mount}/scratch/{run_id}" if run_id else None,
    )


class ProjectResolutionError(Exception):
    """SEEK project resolution failed; callers must fail closed."""


def _default_seekdb_factory():
    from seek.seekdb import SeekDB

    return lambda server, username, password, token_provider=None: SeekDB(
        server, username, password, token_provider=token_provider
    )


def _build_seekdb(factory, api_user, api_pass, token_provider):
    """Call the factory, tolerating one that predates token_provider.

    seekdb_factory is an injection point used by the tests, and existing
    doubles take three positional arguments. Passing a keyword they do not
    accept would break them, so the token is only offered when there is one.
    """
    if token_provider is None:
        return factory(None, api_user, api_pass)
    return factory(None, api_user, api_pass, token_provider=token_provider)


def resolve_user_project(
    api_user: str,
    api_pass: str,
    *,
    seekdb_factory=None,
    token_provider=None,
    personal_prefix: str = "personal-",
) -> ProjectIdentity:
    """Resolve the logged-in user's SEEK project using their own credentials.

    Confirmed empty membership maps to an isolated personal namespace. Any
    unresolved/unknown state raises ProjectResolutionError so the CC turn is
    rejected instead of guessed into the wrong project.

    ``token_provider`` (#16, sub-project 3) carries an OAuth caller's SEEK
    credential. Note this is a **SEEK** credential and has nothing to do with
    the NExtSEEK DRF token the same caller uses for the chat API -- this
    function calls ``getCurrentUser()``, which goes to SEEK. ``api_user`` is
    still needed even when a token is supplied: it names the personal namespace
    for a user with no project membership, below.
    """
    factory = seekdb_factory or _default_seekdb_factory()
    try:
        seekdb = _build_seekdb(factory, api_user, api_pass, token_provider)
        current = seekdb.getCurrentUser()
        projects = current["data"]["relationships"]["projects"]["data"]
    except Exception as exc:  # noqa: BLE001
        raise ProjectResolutionError(str(exc)) from exc

    if not isinstance(projects, list):
        raise ProjectResolutionError("malformed SEEK project membership")

    if not projects:
        user = str(api_user or "")
        slug = slugify_project(user)
        return ProjectIdentity(
            id=f"{personal_prefix}{user}",
            title=user,
            slug=slug,
        )

    try:
        project_id = str(projects[0]["id"])
        title = str(seekdb.getProjectName(project_id))
        if not title:
            raise ValueError("missing SEEK project title")
        slug = slugify_project(title)
        project_dirname(project_id, slug)
    except Exception as exc:  # noqa: BLE001
        raise ProjectResolutionError(str(exc)) from exc
    return ProjectIdentity(
        id=project_id,
        title=title,
        slug=slug,
    )
