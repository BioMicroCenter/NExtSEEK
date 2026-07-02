"""Hermetic tests for the G7-11 Task 14 user-scoped sidecar staging sweep
(``nextseek_api/cc_assistant/cc_staging.py``) + its management-command entrypoint.

No Docker, no network, no DB (fakes + tmp dirs only). Grounds every layout fact
against the ported upstream contract at
``docker/ns-sidecar/app/staging.py`` (byte-identical to
``/home/taishajo/work/dmac-assistant/sidecar/app/staging.py`` @ a429f13):

    {SIDECAR_STAGING_DIR}/{sha256(api_user)}/{request_id}/<files>
    {SIDECAR_STAGING_DIR}/{sha256(api_user)}/{request_id}.complete

Each locked invariant has a FIRING negative control (a test that goes RED under
the forbidden mutation): fresh artifact surfaces same-turn, non-`.complete` not
swept, older stray not attributed in-turn, symlink escapes refused, delivery
scoped to caller identity (never staged content), foreign identity rejected.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine, cc_staging
from nextseek_api.cc_assistant.cc_provision import project_dirname

# Ported upstream staging source (worktree copy cited by the sweep's parity).
PORTED_STAGING = (
    Path(__file__).resolve().parents[3]
    / "docker" / "ns-sidecar" / "app" / "staging.py"
)


# --------------------------------------------------------------------------
# helpers: build a sidecar-shaped staging tree under a fake dmac-cc-users mount
# --------------------------------------------------------------------------

def _stage(root: Path, api_user: str, request_id: str, files: dict[str, bytes],
           *, complete: bool = True, marker_mtime: float | None = None) -> Path:
    """Write ``files`` (relpath -> bytes) under
    ``{root}/_staging/{sha256(api_user)}/{request_id}/`` and (optionally) the
    sibling ``.complete`` marker — exactly the sidecar's layout."""
    base = root / "_staging" / hashlib.sha256(api_user.encode()).hexdigest()
    req = base / request_id
    for rel, data in files.items():
        dst = req / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    if complete:
        marker = base / f"{request_id}.complete"
        marker.write_text("")
        if marker_mtime is not None:
            os.utime(marker, (marker_mtime, marker_mtime))
    return req


def _scratch(root: Path, project: str, user: str) -> Path:
    d = root / project / user / "scratch"
    d.mkdir(parents=True, exist_ok=True)
    return d


PROJECT = "42-proj"
USER = "alice"
API_USER = "alice@mit.edu"


# --------------------------------------------------------------------------
# INVARIANT: same-turn sweep + artifact surfacing (turn-N staged -> turn-N published)
# --------------------------------------------------------------------------

def test_fresh_complete_artifact_surfaces_in_same_turn_publish_set(tmp_path):
    """Production invariant: a fresh `.complete`-marked staged artifact swept
    pre-publish lands in the user's OWN scratch subtree AND is surfaced by the
    turn's `_publish_artifacts` diff — i.e. appears in turn N's published set."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    _stage(root, API_USER, "11111111-1111-1111-1111-111111111111",
           {"submission.csv": b"col\n1\n"})

    before = cc_engine.snapshot_before(scratch, USER)

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT,
        since_ts=0.0,  # in-turn mode; all real mtimes >= 0 -> treated as this turn
    )
    assert res.delivered == ["nextseek-artifacts/submission.csv"]
    # Landed under the user's own scratch (agent reads /data/scratch/nextseek-artifacts/).
    assert (scratch / "nextseek-artifacts" / "submission.csv").read_bytes() == b"col\n1\n"

    output = root / PROJECT / USER / "output"
    published = cc_engine._publish_artifacts(
        scratch, output, turn_id="turnN",
        output_logical_root=f"/dmac/users/{PROJECT}/{USER}/output", before=before,
    )
    keys = {a["key"] for a in published["artifacts"]}
    assert "turnN/nextseek-artifacts/submission.csv" in keys
    assert (output / "artifacts" / "turnN" / "nextseek-artifacts" / "submission.csv").is_file()


def test_non_complete_dir_is_not_swept(tmp_path):
    """A staged request dir WITHOUT a `.complete` marker (partial / in-flight)
    is never swept. FIRING control: drop the marker check and this would leak a
    half-written artifact into the publish set."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    req = _stage(root, API_USER, "22222222-2222-2222-2222-222222222222",
                 {"partial.bin": b"half"}, complete=False)

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert res.delivered == []
    assert not (scratch / "nextseek-artifacts").exists()
    assert (req / "partial.bin").exists()  # untouched


def test_older_stray_not_attributed_in_turn_but_recovered_standalone(tmp_path):
    """In-turn (since_ts=turn_start): only THIS turn's markers sweep; an OLDER
    stray (crashed earlier turn) is deferred (breadcrumb kept), NOT attributed to
    the current turn. Recovery (since_ts=None) then delivers it.

    FIRING control: if the in-turn sweep ignored `since_ts` (swept everything),
    the stray would be delivered this turn and the `delivered == [fresh]` /
    `stray still on disk` assertions go RED."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    turn_start = time.time()

    _stage(root, API_USER, "33333333-3333-3333-3333-333333333333",
           {"fresh.csv": b"new"})  # marker mtime = now (>= turn_start)
    stray_req = _stage(root, API_USER, "00000000-0000-0000-0000-000000000000",
                       {"stray.csv": b"old"}, marker_mtime=turn_start - 10_000)

    in_turn = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT,
        since_ts=turn_start - 1,
    )
    assert in_turn.delivered == ["nextseek-artifacts/fresh.csv"]
    assert "00000000-0000-0000-0000-000000000000" in in_turn.deferred_markers
    assert stray_req.exists()  # stray NOT swept in-turn
    assert not (scratch / "nextseek-artifacts" / "stray.csv").exists()

    recovery = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT,
        since_ts=None,  # recovery: sweep ALL completed dirs
    )
    assert recovery.delivered == ["nextseek-artifacts/stray.csv"]
    assert (scratch / "nextseek-artifacts" / "stray.csv").read_bytes() == b"old"


# --------------------------------------------------------------------------
# INVARIANT: staged content can never redirect delivery to a foreign subtree
# --------------------------------------------------------------------------

def test_delivery_scoped_to_caller_identity_not_staged_content(tmp_path):
    """The SAME staged bytes, swept under two different caller identities, land in
    each caller's OWN scratch — because the destination is derived from the
    validated (project, user), never from staged content. FIRING control: if the
    sweep derived dest from any on-disk name, this cross-check breaks."""
    root = tmp_path / "users"
    a_scratch = _scratch(root, PROJECT, "alice")
    # Stage identical bytes for two distinct api_users (distinct hash dirs).
    _stage(root, "alice@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", {"r.csv": b"A"})
    _stage(root, "bob@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", {"r.csv": b"B"})

    cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(a_scratch),
        api_user="alice@x", user_id="alice", project_dirname=PROJECT, since_ts=0.0,
    )
    # Alice's sweep reads ONLY sha256("alice@x") -> gets "A", never bob's "B".
    assert (a_scratch / "nextseek-artifacts" / "r.csv").read_bytes() == b"A"

    # A different caller (bob) sweeping bob's hash lands in BOB's own scratch.
    b_scratch = _scratch(root, "9-otherproj", "bob")
    cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(b_scratch),
        api_user="bob@x", user_id="bob", project_dirname="9-otherproj", since_ts=0.0,
    )
    assert (b_scratch / "nextseek-artifacts" / "r.csv").read_bytes() == b"B"
    # Alice's subtree never received bob's bytes.
    assert (a_scratch / "nextseek-artifacts" / "r.csv").read_bytes() == b"A"


def test_foreign_users_staging_invisible_to_this_caller(tmp_path):
    """Sweeping as user B never reads user A's hashed staging dir (one-way hash;
    B's src_base is sha256(B) only) — a foreign user's completed artifacts can't
    be delivered to B."""
    root = tmp_path / "users"
    b_scratch = _scratch(root, "9-otherproj", "bob")
    _stage(root, "alice@x", "aaaaaaaa-0000-0000-0000-000000000000", {"secret.csv": b"A"})

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(b_scratch),
        api_user="bob@x", user_id="bob", project_dirname="9-otherproj", since_ts=0.0,
    )
    assert res.delivered == []
    assert not (b_scratch / "nextseek-artifacts").exists()


@pytest.mark.parametrize("api_user,user_id,project", [
    ("a/../b", USER, PROJECT),               # api_user path separator
    ("..", USER, PROJECT),                    # api_user dotdot
    ("", USER, PROJECT),                      # empty api_user
    (API_USER, "../evil", PROJECT),           # user_id traversal
    (API_USER, "a/b", PROJECT),               # user_id separator
    (API_USER, USER, "../../etc"),            # project traversal
    (API_USER, USER, "a/b"),                  # project separator
    (API_USER, USER, ".."),                   # project dotdot
])
def test_invalid_identity_rejected(tmp_path, api_user, user_id, project):
    """Foreign/traversing project/user/api_user components are rejected before any
    path interpolation — no cross-user path is constructible from a WS request."""
    with pytest.raises(ValueError):
        cc_staging.sweep_user_staging(
            user_root_mount=str(tmp_path), scratch_dir=str(tmp_path / "s"),
            api_user=api_user, user_id=user_id, project_dirname=project, since_ts=0.0,
        )


# --------------------------------------------------------------------------
# INVARIANT: path safety (symlinks / traversal refused, dest stays in subtree)
# --------------------------------------------------------------------------

def test_symlinked_staged_file_is_skipped(tmp_path):
    """A symlink inside a completed request dir is never followed/copied (no
    out-of-tree content leak). FIRING control: drop the symlink skip and the
    linked secret's bytes would be copied into scratch."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    req = _stage(root, API_USER, "44444444-4444-4444-4444-444444444444", {"ok.txt": b"ok"})
    (req / "leak.txt").symlink_to(secret)

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert res.delivered == ["nextseek-artifacts/ok.txt"]
    assert not (scratch / "nextseek-artifacts" / "leak.txt").exists()
    # And the secret's content never appears anywhere under scratch.
    for p in scratch.rglob("*"):
        if p.is_file():
            assert p.read_text() != "TOPSECRET"


def test_symlinked_request_dir_is_refused(tmp_path):
    """A `.complete` marker whose backing request dir is a symlink (pointing
    outside the hashed base) is refused, never walked."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.txt").write_text("evil")
    base = root / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest()
    base.mkdir(parents=True)
    (base / "55555555-5555-5555-5555-555555555555").symlink_to(outside, target_is_directory=True)
    (base / "55555555-5555-5555-5555-555555555555.complete").write_text("")

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert res.delivered == []
    assert not (scratch / "nextseek-artifacts").exists()


def test_safe_rel_and_within_guards():
    assert cc_staging._safe_rel(Path("a/b.txt"))
    assert not cc_staging._safe_rel(Path("../x"))
    assert not cc_staging._safe_rel(Path("a/../../x"))
    assert not cc_staging._safe_rel(Path("/abs"))
    base = Path("/dmac/users/42-proj/alice/scratch/nextseek-artifacts")
    assert cc_staging._is_within(base, base / "sub" / "f.txt")
    assert not cc_staging._is_within(base, base.parent / "escape.txt")
    # Fix round 1: Path.relative_to is purely lexical — (base/"..") "passes" it
    # (yields PurePath("..")), so _is_within must ALSO reject ``..`` tails.
    assert not cc_staging._is_within(base, base / "..")
    assert not cc_staging._is_within(base, base / ".." / "sibling")
    assert not cc_staging._is_within(base, base / "sub" / ".." / ".." / "x")


def test_dotdot_named_request_marker_cannot_escape_hashed_base(tmp_path):
    """FIRING control (fix round 1, reviewer finding): a hostile ``...complete``
    marker (the only filename that can put ``..`` in front of the ``.complete``
    tail) must be refused with the marker LEFT IN PLACE, and a foreign user's
    staged bytes must never reach the caller's scratch.

    Why this fires RED under the unfixed code: ``marker.stem`` semantics are
    pathlib-version-dependent — on Pythons where ``Path("...complete").stem ==
    ".."``, the old ``req_dir = src_base / ".."`` resolved to the WHOLE
    ``_staging`` dir and ``Path.relative_to`` (purely lexical) did not flag it,
    so the sweep would rglob EVERY user's hash dir and deliver foreign bytes
    (the foreign-bytes assertions below catch that); on the runtime Python
    (3.14, where the stem parses as the whole name) the old code instead
    UNLINKED the marker as a "stray" (the marker-preserved assertion below
    catches that). The fixed code refuses any non-canonical-UUID stem before
    any use — version-independent — and ``_is_within`` now independently
    rejects ``..`` tails (unit-tested above)."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    # A FOREIGN user's completed staged artifact elsewhere under _staging.
    foreign_req = _stage(root, "mallory@x", "cccccccc-cccc-cccc-cccc-cccccccccccc",
                         {"foreign.csv": b"FOREIGN"})
    base = root / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest()
    base.mkdir(parents=True, exist_ok=True)
    hostile = base / "...complete"
    hostile.write_text("")

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    # Nothing delivered; the foreign user's bytes never reach the caller's scratch.
    assert res.delivered == [] and res.deferred_markers == []
    assert not (scratch / "nextseek-artifacts").exists()
    for p in scratch.rglob("*"):
        if p.is_file():
            assert p.read_bytes() != b"FOREIGN"
    # Refused marker is preserved (never unlinked / interpolated).
    assert hostile.exists()
    # Foreign staging untouched (not swept, not deleted).
    assert (foreign_req / "foreign.csv").read_bytes() == b"FOREIGN"
    assert (foreign_req.parent / "cccccccc-cccc-cccc-cccc-cccccccccccc.complete").exists()


@pytest.mark.parametrize("bad_stem", [
    "..",                                    # traversal
    "evil",                                  # not a UUID
    "AAAAAAAA-1111-1111-1111-111111111111",  # uppercase hex — not str(UUID(...)) canonical form
    "11111111111111111111111111111111",      # unhyphenated hex
])
def test_non_canonical_request_id_marker_refused(tmp_path, bad_stem):
    """The sweep only ever interpolates canonical `str(UUID(...))` request ids
    (docker/ns-sidecar/app/contract.py:57-64) as path segments — a shape guard
    independent of the sidecar's own canonicalization (defense in depth, fix
    round 1). Refused markers are left in place, never unlinked as strays."""
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    base = root / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest()
    base.mkdir(parents=True, exist_ok=True)
    if bad_stem != "..":  # a dir literally named ".." cannot exist
        req = base / bad_stem
        req.mkdir(parents=True, exist_ok=True)
        (req / "x.csv").write_bytes(b"x")
    (base / f"{bad_stem}.complete").write_text("")

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert res.delivered == [] and res.deferred_markers == []
    assert not (scratch / "nextseek-artifacts").exists()
    # Refused marker is left in place (never unlinked as a "stray").
    assert (base / f"{bad_stem}.complete").exists()


def test_nested_relpath_preserved_within_subtree(tmp_path):
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    _stage(root, API_USER, "66666666-6666-6666-6666-666666666666",
           {"sub/dir/deep.csv": b"deep"})
    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert res.delivered == ["nextseek-artifacts/sub/dir/deep.csv"]
    assert (scratch / "nextseek-artifacts" / "sub" / "dir" / "deep.csv").read_bytes() == b"deep"


# --------------------------------------------------------------------------
# cleanup, collisions, no-op, hash parity, reserved-name safety
# --------------------------------------------------------------------------

def test_cleanup_removes_swept_dir_and_marker(tmp_path):
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    req = _stage(root, API_USER, "77777777-7777-7777-7777-777777777777", {"x.csv": b"x"})
    marker = req.parent / "77777777-7777-7777-7777-777777777777.complete"

    cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert not req.exists()
    assert not marker.exists()


def test_collision_is_disambiguated_never_clobbers(tmp_path):
    root = tmp_path / "users"
    scratch = _scratch(root, PROJECT, USER)
    (scratch / "nextseek-artifacts").mkdir(parents=True)
    (scratch / "nextseek-artifacts" / "r.csv").write_bytes(b"PRIOR")
    _stage(root, API_USER, "88888888-8888-8888-8888-888888888888", {"r.csv": b"NEW"})

    res = cc_staging.sweep_user_staging(
        user_root_mount=str(root), scratch_dir=str(scratch),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    # Prior artifact preserved; new one renamed with the __N pattern.
    assert (scratch / "nextseek-artifacts" / "r.csv").read_bytes() == b"PRIOR"
    assert res.delivered == ["nextseek-artifacts/r__1.csv"]
    assert (scratch / "nextseek-artifacts" / "r__1.csv").read_bytes() == b"NEW"


def test_missing_staging_root_is_noop(tmp_path):
    res = cc_staging.sweep_user_staging(
        user_root_mount=str(tmp_path / "users"), scratch_dir=str(tmp_path / "s"),
        api_user=API_USER, user_id=USER, project_dirname=PROJECT, since_ts=0.0,
    )
    assert res.delivered == [] and res.deferred_markers == []


def test_user_hash_matches_ported_sidecar_contract():
    """Parity with docker/ns-sidecar/app/staging.py::_user_hash — silent drift
    means staged artifacts are never found."""
    assert cc_staging._user_hash("alice@mit.edu") == hashlib.sha256(b"alice@mit.edu").hexdigest()
    text = PORTED_STAGING.read_text(encoding="utf-8")
    assert 'hashlib.sha256(api_user.encode("utf-8")).hexdigest()' in text


def test_reserved_staging_name_never_a_project_dirname():
    """`_staging` (no hyphen) can never equal a `project_dirname()` output
    ({pid}-{slug}, always a hyphen), so it can't collide with a per-project tree."""
    assert cc_staging.STAGING_SUBDIR == "_staging"
    assert "-" not in cc_staging.STAGING_SUBDIR
    assert project_dirname("42", "proj") == "42-proj"
    # project_dirname ALWAYS appends "-{slug}", so its output always contains a
    # hyphen and can never equal the reserved hyphen-free "_staging".
    assert project_dirname("_staging", "") == "_staging-"
    assert project_dirname("_staging", "") != cc_staging.STAGING_SUBDIR


def test_staging_root_for_derivation():
    assert cc_staging.staging_root_for("/dmac/users") == Path("/dmac/users/_staging")
    assert cc_staging.staging_root_for("/dmac/users/") == Path("/dmac/users/_staging")


# --------------------------------------------------------------------------
# management command (recovery / Task 15 gate entrypoint) — SAME code path
# --------------------------------------------------------------------------

def test_management_command_recovery_delivers_all(tmp_path, monkeypatch):
    from django.core.management import call_command
    from io import StringIO

    root = tmp_path / "users"
    _scratch(root, PROJECT, USER)
    _stage(root, API_USER, "99999999-9999-9999-9999-999999999999",
           {"a.csv": b"a"}, marker_mtime=time.time() - 99_999)  # old stray

    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(root))
    monkeypatch.setenv("DMAC_CC_USERS_VOLUME", "dmac-cc-users")

    out = StringIO()
    call_command("cc_sweep_staging", "--user-id", USER, "--api-user", API_USER,
                 "--project", PROJECT, stdout=out)
    payload = json.loads(out.getvalue())
    # Recovery mode (since_ts=None) delivers the old stray too.
    assert payload["delivered"] == ["nextseek-artifacts/a.csv"]
    assert payload["delivered_count"] == 1
    assert (root / PROJECT / USER / "scratch" / "nextseek-artifacts" / "a.csv").read_bytes() == b"a"


def test_management_command_rejects_traversal_identity(tmp_path, monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(tmp_path / "users"))
    with pytest.raises(CommandError):
        call_command("cc_sweep_staging", "--user-id", "../evil",
                     "--api-user", API_USER, "--project", PROJECT)
