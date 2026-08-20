"""Hermetic coverage for step7_preflight_collector (imported helper under tests/)."""
from __future__ import annotations

import json

from nextseek_api.cc_assistant.tests.step7_preflight_collector import (
    DockerProbe,
    GitProbe,
    collect_preflight,
    compose_meets_subpath_floor,
    default_docker_probe,
    default_git_probe,
    engine_meets_subpath_floor,
    read_tracker_step3_status,
    resolve_integration_plan_path,
    sha256_file,
)


def test_version_floors_and_sha_helpers(tmp_path):
    assert engine_meets_subpath_floor("Version: 27.0.0\n API version: 1.45") is True
    assert engine_meets_subpath_floor("no versions here") is False
    assert compose_meets_subpath_floor("Docker Compose version v2.30.0") is True
    assert compose_meets_subpath_floor("nope") is False
    missing = tmp_path / "nope"
    assert sha256_file(missing) is None
    f = tmp_path / "a.txt"
    f.write_text("hi")
    assert sha256_file(f) == __import__("hashlib").sha256(b"hi").hexdigest()
    assert resolve_integration_plan_path(tmp_path, env={}).name == "integration-plan.json"
    assert resolve_integration_plan_path(tmp_path, env={"INTEGRATION_PLAN_PATH": str(f)}) == f
    assert read_tracker_step3_status(missing) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert read_tracker_step3_status(bad) is None
    ok = tmp_path / "plan.json"
    ok.write_text(json.dumps({"steps": [{"id": "3", "status": "done"}]}))
    assert read_tracker_step3_status(ok) == "done"
    empty_steps = tmp_path / "empty.json"
    empty_steps.write_text(json.dumps({"steps": []}))
    assert read_tracker_step3_status(empty_steps) is None


def test_collect_preflight_from_tmp_tree(tmp_path):
    (tmp_path / "docker").mkdir()
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  nextseek: {}\nnetworks:\n  dmac-cc-net: {}\n"
    )
    (tmp_path / "docker" / "nextseek.env.example").write_text(
        "NEXTSEEK_CC_IMAGE=x\nDMAC_USER_ROOT_MOUNT=/dmac/users\n"
    )
    docs = tmp_path / "nextseek_api" / "cc_assistant"
    docs.mkdir(parents=True)
    (docs / "DEPLOY.md").write_text("Phase A\ndocker network create foo\n")
    (docs / "SPEC-3-ui-based-io.md").write_text("spec")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"steps": [{"id": "3", "status": "closed"}]}))
    git = GitProbe(
        branch="dev",
        commit="abc1234",
        dirty=True,
        cat_file_size=lambda c, p: 12 if "live_gate" in p else None,
    )
    docker = DockerProbe(
        version_summary="Version: 27.0.0 API version: 1.45",
        info_summary="info",
        compose_version="v2.30.0",
        engine_meets_subpath_floor=True,
        compose_meets_subpath_floor=True,
    )
    payload = collect_preflight(
        repo_root=tmp_path,
        git=git,
        docker=docker,
        env={"INTEGRATION_PLAN_PATH": str(plan)},
        port_source_path="docker-compose.yml",
        port_source_commit="abc1234",
        had_host_bind_data=False,
        pre_step3_snapshot_tag="tag",
        canonical_integration_plan_sha256="deadbeef",
        user_signoff_handoff_path="handoff.md",
    )
    assert payload["branch"] == "dev"
    assert payload["dirty"] is True
    assert "nextseek" in payload["compose_services"]
    assert "dmac-cc-net" in payload["compose_networks"]
    assert "NEXTSEEK_CC_IMAGE" in payload["cc_env_keys"]
    assert payload["deploy_md_has_old_bootstrap"] is True
    assert payload["step3_deploy_gate"]["live_gate_transcript_committed"] is True
    assert payload["step3_deploy_gate"]["tracker_step3_status"] == "closed"


def test_default_probes_with_fake_subprocess(monkeypatch, tmp_path):
    class _Proc:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if "rev-parse" in joined and "abbrev" in joined:
            return _Proc("main\n")
        if "rev-parse" in joined:
            return _Proc("deadbeef\n")
        if "porcelain" in joined:
            return _Proc("")
        if "cat-file" in joined:
            raise __import__("subprocess").CalledProcessError(1, cmd)
        if cmd[:2] == ["docker", "version"]:
            return _Proc("Version: 27.0.0\n API version: 1.46\n")
        if cmd[:2] == ["docker", "info"]:
            return _Proc("info")
        if "compose" in joined:
            return _Proc("Docker Compose version v2.29.0")
        return _Proc("")

    monkeypatch.setattr(
        "nextseek_api.cc_assistant.tests.step7_preflight_collector.subprocess.run",
        fake_run,
    )
    gp = default_git_probe(tmp_path)
    assert gp.branch == "main"
    assert gp.commit == "deadbeef"
    assert gp.dirty is False
    assert gp.cat_file_size("deadbeef", "nope") is None
    dp = default_docker_probe()
    assert dp.engine_meets_subpath_floor is True
    assert "unavailable" not in dp.version_summary

    def boom(*a, **k):
        raise OSError("no docker")

    monkeypatch.setattr(
        "nextseek_api.cc_assistant.tests.step7_preflight_collector.subprocess.run",
        boom,
    )
    dp2 = default_docker_probe()
    assert "unavailable" in dp2.version_summary
