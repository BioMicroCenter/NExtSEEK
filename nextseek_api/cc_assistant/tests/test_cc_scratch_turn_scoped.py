"""#70 (and its duplicate #36): CC scratch must be per-TURN, not per-user.

``cc_provision`` used to hand ``{project}/{user}/scratch`` — the whole user root
— to the agent read-write at ``/data/scratch``. A per-turn dir was created under
it, but nothing pointed the agent at that dir: ``working_dir`` is ``/home/user``
(and must stay there, or the baked plugin is never discovered), so a bare
``/data/scratch/foo.json`` from two different turns resolved to the SAME file.
One turn could therefore read, act on, and clobber whatever another turn left
behind.

The fix mounts ``scratch/<run_id>`` at ``/data/scratch`` and stops mounting the
user root at all. The trap called out in the issue is that ``snapshot_before`` /
``_publish_artifacts`` diff the scratch tree and the sidecar staging sweep writes
into it: re-pointing the mount WITHOUT re-pointing those three silently breaks
artifact publishing, since the agent's files would land outside the diffed tree.
Both halves are asserted here.

Hermetic: a spy docker client aborts the turn at spawn, so everything before
spawn (mkdir provisioning, mounts, path mappings, the pre-turn snapshot) is real
and nothing runs.
"""

import ast
import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs

RUN_ID = "a1"
USER = "alice"
PROJECT = "proj"


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


@pytest.fixture
def spawn(tmp_path, monkeypatch):
    """Run a turn up to the spawn boundary; return (run_kwargs, snapshot_arg)."""
    import docker as docker_mod
    from docker.errors import APIError

    client = _SpyClient(APIError("spawn intercepted by test"))
    monkeypatch.setattr(docker_mod, "from_env", lambda: client)

    seen: list[Path] = []
    real_snapshot = cc_engine.snapshot_before

    def _recording_snapshot(scratch_mount, user_id):
        seen.append(Path(scratch_mount))
        return real_snapshot(scratch_mount, user_id)

    monkeypatch.setattr(cc_engine, "snapshot_before", _recording_snapshot)

    def _go(run_id=RUN_ID):
        cc_engine.run_cc_turn(
            query="q", model_id=None, send_event=lambda e, d: None,
            user_id=USER, project_dirname=PROJECT, run_id=run_id,
            paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
            cc_state_key="sess",
        )
        return client.containers.run_kwargs, seen[-1]

    return _go


def _scratch_mount(run_kwargs):
    return next(m for m in run_kwargs["mounts"] if m["Target"] == "/data/scratch")


# ---------------------------------------------------------------------------
# The mount itself
# ---------------------------------------------------------------------------


def test_agent_scratch_mount_is_the_per_turn_subtree(spawn, tmp_path):
    run_kwargs, _ = spawn()
    subpath = _scratch_mount(run_kwargs)["VolumeOptions"]["Subpath"]

    assert subpath == f"{PROJECT}/{USER}/scratch/{RUN_ID}"
    assert (tmp_path / subpath).is_dir(), "backing dir must exist before spawn"


def test_user_scratch_root_is_not_mounted_into_the_agent(spawn):
    """Not even read-only: reading another turn's leftovers is the bug."""
    run_kwargs, _ = spawn()
    subpaths = {m["VolumeOptions"]["Subpath"] for m in run_kwargs["mounts"]}

    assert f"{PROJECT}/{USER}/scratch" not in subpaths


def test_another_turns_file_is_not_reachable_under_the_mounted_subtree(spawn, tmp_path):
    """The concrete #70 scenario: turn A's file must not resolve inside turn B's
    /data/scratch."""
    stale = tmp_path / PROJECT / USER / "scratch" / "older-turn" / "plan.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text('{"from": "another turn"}')

    run_kwargs, _ = spawn()
    mounted = tmp_path / _scratch_mount(run_kwargs)["VolumeOptions"]["Subpath"]

    assert not (mounted / "plan.json").exists()
    assert stale not in mounted.rglob("*")


def test_two_turns_get_disjoint_scratch_dirs(spawn, tmp_path):
    first, _ = spawn(run_id="0aa1")
    second, _ = spawn(run_id="0bb2")

    a = _scratch_mount(first)["VolumeOptions"]["Subpath"]
    b = _scratch_mount(second)["VolumeOptions"]["Subpath"]
    assert a != b
    assert a == f"{PROJECT}/{USER}/scratch/0aa1"
    assert b == f"{PROJECT}/{USER}/scratch/0bb2"


def test_path_mappings_report_the_per_turn_root(spawn, tmp_path):
    """The agent translates its own paths with this; a user-root logical_root
    would mislabel every artifact it reports."""
    run_kwargs, _ = spawn()
    mappings = json.loads(run_kwargs["environment"]["DMAC_PATH_MAPPINGS"])

    assert mappings["scratch"]["logical_root"] == str(
        tmp_path / PROJECT / USER / "scratch" / RUN_ID
    )


def test_workdir_is_unchanged_so_plugin_discovery_still_works(spawn):
    """Isolation comes from the MOUNT, not from moving working_dir — moving it
    would break the baked CLAUDE.md / nextseek plugin discovery."""
    run_kwargs, _ = spawn()
    assert run_kwargs["working_dir"] == "/home/user"
    assert cc_engine._CONTAINER_WORKDIR == "/home/user"


# ---------------------------------------------------------------------------
# The trap: publishing must follow the mount
# ---------------------------------------------------------------------------


def test_pre_turn_snapshot_diffs_the_same_dir_the_agent_has_mounted(spawn, tmp_path):
    """snapshot_before/_publish_artifacts share one ``scratch_mount`` local, so
    pinning the snapshot's argument pins the publish diff too."""
    run_kwargs, snapshotted = spawn()
    mounted = tmp_path / _scratch_mount(run_kwargs)["VolumeOptions"]["Subpath"]

    assert snapshotted == mounted


def test_pre_turn_snapshot_ignores_other_turns_files(spawn, tmp_path):
    """A user-root snapshot would carry another turn's files into this turn's
    ``before`` set, so publishing would misreport created/modified."""
    stale = tmp_path / PROJECT / USER / "scratch" / "older-turn" / "plan.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}")

    _, snapshotted = spawn()

    assert cc_engine.snapshot_before(snapshotted, USER) == {}


def _engine_ast():
    src = Path(cc_engine.__file__).with_suffix(".py").read_text()
    tree = ast.parse(src)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_cc_turn"
    )


def test_staging_sweep_writes_into_the_per_turn_scratch(spawn):
    """The sidecar staging sweep runs after the container exits and before the
    publish diff, so it is unreachable from the spawn-abort harness — pinned
    structurally instead. Sweeping into the user root would drop report /
    generate-submission downloads outside the diffed tree entirely.
    """
    for node in ast.walk(_engine_ast()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sweep_user_staging"
        ):
            kw = {k.arg: k.value for k in node.keywords}
            assert "scratch_dir" in kw
            assert ast.unparse(kw["scratch_dir"]) == "dirs.run_scratch_mnt", (
                "the in-turn staging sweep must write into THIS turn's scratch"
            )
            return
    pytest.fail("no sweep_user_staging call found in run_cc_turn")


def test_publish_diffs_the_turn_scoped_scratch_mount(spawn):
    fn = _engine_ast()
    assigned = [
        ast.unparse(n.value)
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == "scratch_mount" for t in n.targets)
    ]
    assert assigned == ["Path(dirs.run_scratch_mnt)"], (
        f"scratch_mount must be the per-turn subtree, got {assigned}"
    )

    published = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_publish_artifacts"
    ]
    assert published, "no _publish_artifacts call found"
    assert ast.unparse(published[0].args[0]) == "scratch_mount"


# ---------------------------------------------------------------------------
# build_user_dirs contract
# ---------------------------------------------------------------------------


def _paths():
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def test_run_scratch_is_none_without_a_run_id():
    """Out-of-band callers (the cc_sweep_staging recovery command) legitimately
    have no turn, and keep addressing the user-scoped root."""
    dirs = build_user_dirs(_paths(), PROJECT, USER)

    assert dirs.run_scratch_subpath is None
    assert dirs.run_scratch_mnt is None
    assert dirs.scratch_mnt == f"/dmac/users/{PROJECT}/{USER}/scratch"


def test_run_scratch_nests_under_the_user_scratch_root():
    dirs = build_user_dirs(_paths(), PROJECT, USER, run_id=RUN_ID)

    assert dirs.run_scratch_subpath == f"{PROJECT}/{USER}/scratch/{RUN_ID}"
    assert dirs.run_scratch_mnt == f"/dmac/users/{PROJECT}/{USER}/scratch/{RUN_ID}"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "", "a\x00b", "."])
def test_run_id_is_validated_as_a_path_segment(bad):
    """run_id is interpolated into a VolumeOptions.Subpath — a traversing value
    would mount an arbitrary subtree into the agent."""
    with pytest.raises(ValueError):
        build_user_dirs(_paths(), PROJECT, USER, run_id=bad)
