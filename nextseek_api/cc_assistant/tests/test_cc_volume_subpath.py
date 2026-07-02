"""G7-10 Task 6 — named-volume subpath mount hermetic tests.

The dmac-cc-users named volume is mounted once into the nextseek container at
``user_root_mount``; each per-turn CC sibling mounts a *subpath* of that same
volume. The per-mount ``Subpath`` VALUE is the cross-user-leak guard (OI-3 /
risk-register rank 2): an empty / root / wrong Subpath would mount the whole
volume root into an agent. These tests pin the concrete Subpath of every sibling
mount and independently reject empty/root/constant Subpaths per mount.

Pure logic: docker-py (7.1.0) is importable in the harness, but no daemon, no
network, no Django.
"""
import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def _mounts(**kwargs):
    base = dict(paths=_paths(), project_dirname="proj", user_id="alice", cc_state_key="sess")
    base.update(kwargs)
    return cc_engine._build_volumes(**base)


def _by_target(mounts):
    return {m["Target"]: m for m in mounts}


# The full post-cutover spawn set (Step 1 enumeration): target -> exact Subpath.
EXPECTED_SUBPATHS = {
    "/data/input": "proj/alice/input",
    "/data/shared": "proj/shared",            # project-scoped: NO user segment (SPEC-2 D5)
    "/data/scratch": "proj/alice/scratch",
    "/home/user/.claude": "proj/alice/cc-state/sess",
    "/home/user/.cc-memory/transcripts": "proj/alice/_memory/sess/transcripts",
}
EXPECTED_RO = {
    "/data/input": True,
    "/data/shared": True,
    "/data/scratch": False,
    "/home/user/.claude": False,
    "/home/user/.cc-memory/transcripts": True,
}


# --------------------------------------------------------------------------
# docker-py 7.1.0 subpath floor: Engine-API PascalCase serialization
# --------------------------------------------------------------------------

def test_build_volumes_returns_list_of_mounts_not_a_dict():
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    assert isinstance(mounts, list)
    for m in mounts:
        # docker.types.Mount is a dict subclass serialized with PascalCase keys.
        assert set(("Type", "Source", "Target", "VolumeOptions")) <= set(m.keys())
        assert m["Type"] == "volume"
        assert m["Source"] == "dmac-cc-users"
        # never the lowercase hand-rolled shape
        assert "volume_options" not in m
        assert "type" not in m and "source" not in m and "target" not in m


def test_every_mount_uses_nested_pascalcase_subpath_key():
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    for m in mounts:
        assert "VolumeOptions" in m
        assert "Subpath" in m["VolumeOptions"]


# --------------------------------------------------------------------------
# Per-mount Subpath VALUE isolation — exact-string equality per mount
# --------------------------------------------------------------------------

@pytest.mark.parametrize("target,expected", sorted(EXPECTED_SUBPATHS.items()))
def test_each_mount_has_its_own_exact_enumerated_subpath(target, expected):
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    by_target = _by_target(mounts)
    assert target in by_target, f"spawn set missing mount for {target}"
    assert by_target[target]["VolumeOptions"]["Subpath"] == expected


@pytest.mark.parametrize("target,ro", sorted(EXPECTED_RO.items()))
def test_each_mount_has_its_locked_read_only_flag(target, ro):
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    assert _by_target(mounts)[target]["ReadOnly"] is ro


def test_spawn_set_has_no_unenumerated_mount():
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    assert set(_by_target(mounts)) == set(EXPECTED_SUBPATHS)


# --------------------------------------------------------------------------
# Per-mount negative controls (anti-empty / anti-root / anti-constant).
# For EACH enumerated mount independently: an empty, root, volume-root, or any
# constant Subpath != that mount's exact expected value MUST fail the
# exact-equality gate. This is the one-line `Subpath=""` mutation a green
# suite must never let pass — exercised against the REAL spawn set by mutating
# one mount at a time and asserting the gate trips.
# --------------------------------------------------------------------------

_FORBIDDEN_SUBPATHS = ["", "/", ".", "proj", "dmac-cc-users", "other/constant"]


def _assert_subpaths_exact(mounts) -> None:
    """The gate: every spawn-set mount's Subpath equals its enumerated value."""
    by_target = _by_target(mounts)
    assert set(by_target) == set(EXPECTED_SUBPATHS)
    for target, expected in EXPECTED_SUBPATHS.items():
        assert by_target[target]["VolumeOptions"]["Subpath"] == expected, target


def test_the_gate_passes_on_the_real_unmutated_spawn_set():
    _assert_subpaths_exact(_mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts"))


@pytest.mark.parametrize("target,expected", sorted(EXPECTED_SUBPATHS.items()))
@pytest.mark.parametrize("bad", _FORBIDDEN_SUBPATHS)
def test_per_mount_rejects_empty_root_or_constant_subpath(target, expected, bad):
    # Sanity: no forbidden constant coincides with any legit enumerated value
    # (so every mutation below is a genuine violation, never vacuous).
    assert bad != expected
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    for m in mounts:
        if m["Target"] == target:
            m["VolumeOptions"]["Subpath"] = bad  # simulate the one-line mutation
    with pytest.raises(AssertionError):
        _assert_subpaths_exact(mounts)


def test_shared_is_enumerated_and_guarded_even_though_project_scoped():
    # A blanket "must contain {project}/{user}/" rule would FALSELY pass an
    # unguarded shared and reject the legit project-scoped value; shared is
    # enumerated with its own exact value so Subpath="" / "proj" on /data/shared
    # fails the hermetic suite (not only the live find gate).
    mounts = _mounts()
    shared = _by_target(mounts)["/data/shared"]
    assert shared["VolumeOptions"]["Subpath"] == "proj/shared"
    assert shared["VolumeOptions"]["Subpath"] != ""
    assert shared["VolumeOptions"]["Subpath"] != "proj"


# --------------------------------------------------------------------------
# cc-state / transcripts conditionality; no legacy file-bind mount
# --------------------------------------------------------------------------

def test_cc_state_mount_omitted_without_key():
    mounts = _mounts(cc_state_key=None)
    assert "/home/user/.claude" not in _by_target(mounts)
    assert "/data/scratch" in _by_target(mounts)


def test_transcripts_mount_omitted_without_subpath():
    mounts = _mounts()  # no transcripts_subpath
    assert "/home/user/.cc-memory/transcripts" not in _by_target(mounts)


def test_no_claude_md_file_bind_target_exists():
    # G7-10 drops the RO user_memory_file bind; merged CLAUDE.md is byte-copied
    # into the cc-state subpath, never mounted at .claude/CLAUDE.md.
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    assert "/home/user/.claude/CLAUDE.md" not in _by_target(mounts)


def test_no_host_path_sources_only_the_named_volume():
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    for m in mounts:
        assert m["Source"] == "dmac-cc-users"
        assert not m["Source"].startswith("/")  # never a host path


# --------------------------------------------------------------------------
# Cross-user isolation via Subpath (same volume, disjoint tails).
# --------------------------------------------------------------------------

def test_two_users_same_project_share_shared_but_not_private():
    alice = _by_target(_mounts(user_id="alice"))
    bob = _by_target(_mounts(user_id="bob"))
    assert alice["/data/shared"]["VolumeOptions"]["Subpath"] == "proj/shared"
    assert bob["/data/shared"]["VolumeOptions"]["Subpath"] == "proj/shared"
    assert alice["/data/scratch"]["VolumeOptions"]["Subpath"] == "proj/alice/scratch"
    assert bob["/data/scratch"]["VolumeOptions"]["Subpath"] == "proj/bob/scratch"
    assert alice["/data/input"]["VolumeOptions"]["Subpath"] != bob["/data/input"]["VolumeOptions"]["Subpath"]


def test_different_projects_fully_disjoint_including_shared():
    a = {m["VolumeOptions"]["Subpath"] for m in _mounts(project_dirname="42-liver")}
    b = {m["VolumeOptions"]["Subpath"] for m in _mounts(project_dirname="99-heart")}
    assert a.isdisjoint(b)


# --------------------------------------------------------------------------
# _mount_volume_subpath helper (PascalCase shape).
# --------------------------------------------------------------------------

def test_mount_volume_subpath_helper_emits_engine_api_shape():
    m = cc_engine._mount_volume_subpath(
        "dmac-cc-users", "/data/input", "proj/alice/input", read_only=True)
    assert m["Type"] == "volume"
    assert m["Source"] == "dmac-cc-users"
    assert m["Target"] == "/data/input"
    assert m["ReadOnly"] is True
    assert m["VolumeOptions"] == {"Subpath": "proj/alice/input"}


# --------------------------------------------------------------------------
# _run_kwargs cutover: mounts=, never volumes=.
# --------------------------------------------------------------------------

def test_run_kwargs_uses_mounts_not_volumes():
    mounts = _mounts()
    kw = cc_engine._run_kwargs(
        image="img", command=["claude"], environment={"A": "b"},
        mounts=mounts, run_id="r1", user_id="alice",
    )
    assert "mounts" in kw
    assert "volumes" not in kw
    assert kw["mounts"] is mounts or kw["mounts"] == mounts


# --------------------------------------------------------------------------
# Preflight: every mount's backing subpath dir must exist in the volume before
# spawn (Engine VolumeOptions.Subpath fails container start if the exact subdir
# is absent). Missing any one -> preflight error.
# --------------------------------------------------------------------------

def test_preflight_subpath_dirs_ok_when_all_present(tmp_path):
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    for m in mounts:
        (tmp_path / m["VolumeOptions"]["Subpath"]).mkdir(parents=True, exist_ok=True)
    cc_engine._preflight_subpath_dirs(str(tmp_path), mounts)  # must not raise


def test_preflight_subpath_dirs_errors_when_transcripts_child_missing(tmp_path):
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    for m in mounts:
        sub = m["VolumeOptions"]["Subpath"]
        if sub == "proj/alice/_memory/sess/transcripts":
            continue  # deliberately omit the transcripts child
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    with pytest.raises((FileNotFoundError, NotADirectoryError, ValueError)):
        cc_engine._preflight_subpath_dirs(str(tmp_path), mounts)


@pytest.mark.parametrize("omit", list(EXPECTED_SUBPATHS.values()))
def test_preflight_errors_when_any_single_subpath_dir_missing(tmp_path, omit):
    mounts = _mounts(transcripts_subpath="proj/alice/_memory/sess/transcripts")
    for m in mounts:
        sub = m["VolumeOptions"]["Subpath"]
        if sub == omit:
            continue
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    with pytest.raises((FileNotFoundError, NotADirectoryError, ValueError)):
        cc_engine._preflight_subpath_dirs(str(tmp_path), mounts)


def test_preflight_errors_on_empty_subpath_mount():
    with pytest.raises(ValueError):
        cc_engine._preflight_subpath_dirs("/dmac/users", [
            {"Target": "/data/shared", "VolumeOptions": {"Subpath": ""}},
        ])


# --------------------------------------------------------------------------
# Atomic cutover — spawn contract + 1c memory staging (hermetic run_cc_turn).
# A fake docker client spies on containers.run kwargs, then aborts the turn
# with APIError so nothing runs. Everything before spawn (mkdir provisioning,
# CLAUDE.md byte-copy, mounts=, path_mappings) is real.
# --------------------------------------------------------------------------

class _SpyContainers:
    def __init__(self, exc):
        self.run_kwargs = None
        self._exc = exc

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        raise self._exc


class _SpyClient:
    def __init__(self, exc):
        self.containers = _SpyContainers(exc)


def _spy_run_cc_turn(tmp_path, monkeypatch, **overrides):
    import docker as docker_mod
    from docker.errors import APIError

    client = _SpyClient(APIError("spawn intercepted by test"))
    monkeypatch.setattr(docker_mod, "from_env", lambda: client)

    paths = CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path))
    merged = tmp_path / "rendered-CLAUDE.md"
    merged.write_bytes(b"# merged user memory\nUSER_MEMORY_MARKER=G7_10\n")
    events: list[tuple[str, dict]] = []
    kwargs = dict(
        query="q", model_id=None,
        send_event=lambda e, d: events.append((e, d)),
        user_id="alice", project_dirname="proj", run_id="r1",
        paths=paths, cc_state_key="sess",
        memory_claude_md=str(merged),
        transcripts_subpath="proj/alice/_memory/sess/transcripts",
    )
    kwargs.update(overrides)
    cc_engine.run_cc_turn(**kwargs)
    return client, events, merged


def test_containers_run_receives_mounts_list_never_volumes(tmp_path, monkeypatch):
    client, events, _ = _spy_run_cc_turn(tmp_path, monkeypatch)
    kw = client.containers.run_kwargs
    assert kw is not None, f"containers.run never called; events={events}"
    assert "volumes" not in kw
    assert isinstance(kw["mounts"], list) and kw["mounts"]
    for m in kw["mounts"]:
        assert m["Type"] == "volume"
        assert m["Source"] == "dmac-cc-users"
        assert m["VolumeOptions"]["Subpath"]
    subs = {m["Target"]: m["VolumeOptions"]["Subpath"] for m in kw["mounts"]}
    assert subs == EXPECTED_SUBPATHS


def test_run_cc_turn_provisions_every_mount_backing_subpath(tmp_path, monkeypatch):
    _spy_run_cc_turn(tmp_path, monkeypatch)
    for sub in EXPECTED_SUBPATHS.values():
        assert (tmp_path / sub).is_dir(), sub
    # the _memory/<session>/transcripts CHILD specifically (Engine Subpath
    # refuses container start when the exact subdir is absent).
    assert (tmp_path / "proj/alice/_memory/sess/transcripts").is_dir()


def test_merged_claude_md_byte_copied_into_cc_state_subpath(tmp_path, monkeypatch):
    _, _, merged = _spy_run_cc_turn(tmp_path, monkeypatch)
    staged = tmp_path / "proj/alice/cc-state/sess/CLAUDE.md"
    assert staged.is_file()
    assert staged.read_bytes() == merged.read_bytes()


def test_merged_claude_md_never_lands_at_nested_dot_claude_path(tmp_path, monkeypatch):
    # {cc_state_mnt}/.claude/CLAUDE.md would mount to /home/user/.claude/.claude/
    # CLAUDE.md — the wrong nested path the brief forbids.
    _spy_run_cc_turn(tmp_path, monkeypatch)
    assert not (tmp_path / "proj/alice/cc-state/sess/.claude/CLAUDE.md").exists()
    assert not (tmp_path / "proj/alice/cc-state/sess/.claude").exists()


def test_path_mappings_env_uses_locked_logical_root_schema(tmp_path, monkeypatch):
    client, _, _ = _spy_run_cc_turn(tmp_path, monkeypatch)
    env = client.containers.run_kwargs["environment"]
    mappings = json.loads(env["DMAC_PATH_MAPPINGS"])
    root = str(tmp_path)
    assert mappings == {
        "output": {"container_root": "/data/output",
                   "logical_root": f"{root}/proj/alice/output"},
        "scratch": {"container_root": "/data/scratch",
                    "logical_root": f"{root}/proj/alice/scratch"},
    }
    assert "host_root" not in env["DMAC_PATH_MAPPINGS"]


# --------------------------------------------------------------------------
# Grep guards (atomic cutover; the negative controls were demonstrated RED
# against the pre-cutover source at commit aacce0d — see task-6-report.md).
# --------------------------------------------------------------------------

_CC_DIR = Path(__file__).resolve().parents[1]           # nextseek_api/cc_assistant
_SERVICES = _CC_DIR.parent / "services" / "cc_assistant.py"
_REPO_ROOT = _CC_DIR.parents[1]


def test_grep_guard_no_host_user_root_in_any_runtime_module():
    files = sorted(_CC_DIR.glob("*.py")) + [
        _SERVICES, _CC_DIR / "evidence" / "run_1c_claude_md_live_probe.py"]
    for f in files:
        assert "host_user_root" not in f.read_text(), f"host_user_root revived in {f}"


def test_grep_guard_spawn_path_never_passes_volumes():
    src = (_CC_DIR / "cc_engine.py").read_text()
    assert '"volumes": volumes' not in src
    assert "volumes=volumes" not in src
    assert '"mounts": mounts' in src


def test_grep_guard_engine_uses_logical_root_not_host_root():
    src = (_CC_DIR / "cc_engine.py").read_text()
    assert '"logical_root"' in src
    assert '"host_root"' not in src
    assert "output_host_root" not in src


def test_grep_guard_run_kwargs_docstring_names_segmented_net_not_compose_net():
    # The stale claim that the agent "joins the nextseek compose network"
    # misleads a segmentation audit — it defaults to dmac-cc-net.
    src = (_CC_DIR / "cc_engine.py").read_text()
    start = src.index("def _run_kwargs")
    doc = src[start:src.index("def _mount_volume_subpath")]
    assert "the nextseek compose network" not in doc
    assert "dmac-cc-net" in doc


def test_grep_guard_compose_never_reintroduces_srv_dmac_host_bind():
    text = (_REPO_ROOT / "docker-compose.yml").read_text()
    assert "/srv/dmac/users:/dmac/users" not in text
    assert "/srv/dmac/users" not in text
    assert "dmac-cc-users:/dmac/users" in text


def test_compose_declares_dmac_cc_users_external_volume():
    text = (_REPO_ROOT / "docker-compose.yml").read_text()
    idx = text.index("\nvolumes:")
    assert "dmac-cc-users:" in text[idx:]
