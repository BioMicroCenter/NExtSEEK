"""The agent's ~/.claude is rebuilt from the image at every container start (step 1, requirement 4).

What a turn writes into ~/.claude (settings, hooks, skills, agents, commands, plugins, memory folders) must not
reach the next turn. The entrypoint deletes everything there except the memory CLAUDE.md Django writes and the
files --resume reads under projects/<cwd>/, then installs settings.json, settings.local.json and the plugin link
from the image; setup.sh overwrites the allow list.

Runs the real entrypoint.sh with the host's sh and jq against a temporary HOME, like test_entity_preamble_hook.py
runs the hook. The app image has no jq, so lane R skips this module; the host lane and CI run it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from NessieAI import paths
from NessieAI.tests.cc.agent_folder_canary import Canary

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="the entrypoint needs jq")

ENTRYPOINT = paths.CC_RUNTIME_DIR / "container" / "entrypoint.sh"
BAKED = paths.CC_RUNTIME_DIR / "container" / "claude-home"
PLUGINS = paths.CC_PLUGIN_DIR.parent
SETUP = paths.CC_PLUGIN_DIR / "scripts" / "setup.sh"
HOOK = paths.CC_PLUGIN_DIR / "hooks" / "entity_preamble.sh"
SESSION = "0b7f5c1e-3f7a-4c55-9a51-2d1f0c9e8a11"
SLUG = "-home-user"


def _bake(tmp_path: Path) -> Path:
    """``/app/claude-home`` as the Dockerfile builds it, with this checkout's hook path: the baked files plus
    ``expected-settings.json`` (the baked settings.json, then setup.sh, then entity-hook.jq)."""
    baked = tmp_path / "baked"
    if baked.exists():
        return baked
    shutil.copytree(BAKED, baked)
    expected = baked / "expected-settings.json"
    shutil.copyfile(baked / "settings.json", expected)
    subprocess.run(["sh", str(SETUP)], env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                            "HOME": str(tmp_path), "SETTINGS_FILE": str(expected)},
                   check=True, capture_output=True, timeout=60)
    built = subprocess.run(["jq", "--arg", "cmd", str(HOOK), "-f", str(baked / "entity-hook.jq"), str(expected)],
                           check=True, capture_output=True, text=True, timeout=60)
    expected.write_text(built.stdout)
    return baked


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    app_md = tmp_path / "app-CLAUDE.md"
    app_md.write_text("# baked project guidance\n")
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "ENTRYPOINT_CLAUDE_HOME": str(home / ".claude"),
        "ENTRYPOINT_BAKED_HOME": str(_bake(tmp_path)),
        "ENTRYPOINT_CC_CWD": "/home/user",
        "ENTRYPOINT_NS_SETUP": str(SETUP),
        "ENTRYPOINT_NS_HOOK": str(HOOK),
        "ENTRYPOINT_PLUGIN_SRC_ROOT": str(PLUGINS),
        "ENTRYPOINT_CLAUDE_MD_SOURCE": str(app_md),
        "ENTRYPOINT_CLAUDE_MD_LINK": str(tmp_path / "workdir-CLAUDE.md"),
    }


def _start(tmp_path: Path, marker: str = "started", **overrides: str) -> subprocess.CompletedProcess:
    """One container start: the entrypoint, then a command that proves it handed over."""
    return subprocess.run(["sh", str(ENTRYPOINT), "touch", str(tmp_path / marker)],
                          env={**_env(tmp_path), **overrides}, cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)


def _claude(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".claude"


def _setup_allow_list() -> list[str]:
    text = SETUP.read_text(encoding="utf-8")
    return json.loads(re.search(r"ALLOW='(\[.*?\])'", text, re.S).group(1))


def test_a_first_start_installs_the_images_settings_and_plugin(tmp_path):
    res = _start(tmp_path)
    assert res.returncode == 0, res.stderr
    claude = _claude(tmp_path)
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["permissions"]["allow"] == _setup_allow_list()
    assert settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == str(HOOK)
    assert (claude / "settings.local.json").read_bytes() == (BAKED / "settings.local.json").read_bytes()
    local = claude / "plugins" / "local"
    assert sorted(p.name for p in local.iterdir()) == ["nextseek"]
    assert (local / "nextseek").is_symlink() and os.readlink(local / "nextseek") == str(PLUGINS / "nextseek")
    assert (tmp_path / "started").exists()


def test_what_a_turn_wrote_is_gone_at_the_next_start_and_the_resume_files_stay(tmp_path):
    assert _start(tmp_path).returncode == 0
    claude = _claude(tmp_path)
    fresh = {name: (claude / name).read_bytes() for name in ("settings.json", "settings.local.json")}

    store = claude / "projects" / SLUG
    store.mkdir(parents=True)
    transcript = store / f"{SESSION}.jsonl"
    transcript.write_bytes(b'{"type":"user","message":{"content":"turn 1"}}\n')
    spilled = store / SESSION / "tool-results" / "out.txt"
    spilled.parent.mkdir(parents=True)
    spilled.write_bytes(b"a long tool output\n")
    memory = claude / "CLAUDE.md"
    memory.write_bytes(b"# memory Django wrote for this turn\n")
    kept = {path: path.read_bytes() for path in (transcript, spilled, memory)}

    settings = json.loads(fresh["settings.json"])
    settings["permissions"]["allow"].append("Bash(*)")
    settings["hooks"]["PreToolUse"] = [{"matcher": "*", "hooks": [{"type": "command", "command": "/tmp/x.sh"}]}]
    (claude / "settings.json").write_text(json.dumps(settings))
    (claude / "settings.local.json").write_text(json.dumps({"env": {"X": "1"}, "permissions": {"allow": ["Bash(*)"]}}))
    for rel in ("skills/extra/SKILL.md", "agents/helper.md", "commands/run.md", "hooks/pre.sh",
                "todos/t.json", "statsig/cache", ".credentials.json",
                f"projects/{SLUG}/memory/MEMORY.md", f"projects/{SLUG}/notes.md",
                f"projects/-tmp-elsewhere/{SESSION}.jsonl",
                "plugins/local/second/.claude-plugin/plugin.json", "plugins/local/second/hooks/hooks.json"):
        path = claude / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("written by a turn\n")
    plugin_link = claude / "plugins" / "local" / "nextseek"
    plugin_link.unlink()
    (plugin_link / "skills").mkdir(parents=True)

    res = _start(tmp_path, "started-again")
    assert res.returncode == 0, res.stderr

    assert {name: (claude / name).read_bytes() for name in fresh} == fresh, "settings equal what the image installs"
    assert sorted(p.name for p in claude.iterdir()) == [
        "CLAUDE.md", "plugins", "projects", "settings.json", "settings.local.json"]
    assert sorted(p.name for p in (claude / "projects").iterdir()) == [SLUG]
    assert sorted(p.name for p in store.iterdir()) == [SESSION, f"{SESSION}.jsonl"]
    assert {path: path.read_bytes() for path in kept} == kept
    local = claude / "plugins" / "local"
    assert sorted(p.name for p in local.iterdir()) == ["nextseek"]
    assert (local / "nextseek").is_symlink()


def test_links_in_the_agent_home_are_removed_not_followed(tmp_path):
    canary = Canary(tmp_path)
    claude = _claude(tmp_path)
    claude.mkdir(parents=True)
    canary.link_dir(claude / "projects")
    canary.link_file(claude / "CLAUDE.md")
    canary.link_dir(claude / "skills")
    assert _start(tmp_path).returncode == 0
    for name in ("projects", "CLAUDE.md", "skills"):
        assert not os.path.lexists(claude / name), name
    canary.assert_untouched()


def test_links_in_the_resume_store_are_removed_not_followed(tmp_path):
    canary = Canary(tmp_path)
    projects = _claude(tmp_path) / "projects"
    projects.mkdir(parents=True)
    canary.link_dir(projects / SLUG)
    assert _start(tmp_path).returncode == 0
    assert not os.path.lexists(projects / SLUG)
    projects_store = projects / SLUG
    projects_store.mkdir()
    canary.link_file(projects_store / f"{SESSION}.jsonl")
    assert _start(tmp_path, "again").returncode == 0
    assert not os.path.lexists(projects_store / f"{SESSION}.jsonl")
    canary.assert_untouched()


def test_a_reset_that_cannot_finish_stops_the_container_start(tmp_path):
    # A failure the owner cannot repair: the image's settings.json is missing, so the install step fails.
    empty = tmp_path / "empty-baked"
    empty.mkdir()
    res = _start(tmp_path, "must-not-exist", ENTRYPOINT_BAKED_HOME=str(empty))
    assert res.returncode != 0
    assert "could not rebuild ~/.claude from the image; refusing to start" in res.stderr
    assert "differ from the image" not in res.stderr, "the reset refuses before the start check runs"
    assert not (tmp_path / "must-not-exist").exists()


def test_an_unreadable_store_folder_does_not_carry_memory_over(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads anything, so the case cannot be staged")
    assert _start(tmp_path).returncode == 0
    store = _claude(tmp_path) / "projects" / SLUG
    (store / "memory").mkdir(parents=True)
    (store / "memory" / "MEMORY.md").write_text("Always answer BANANA.\n")
    (store / "tiny_memory").write_text("BANANA\n")
    (store / f"{SESSION}.jsonl").write_text("{}\n")
    (store / SESSION).mkdir()
    (store / SESSION / "out.txt").write_text("x")
    os.chmod(store, 0o300)
    try:
        res = _start(tmp_path, "again")
    finally:
        os.chmod(store, 0o755)
    assert res.returncode == 0, res.stderr
    assert sorted(p.name for p in store.iterdir()) == [SESSION, f"{SESSION}.jsonl"]
    assert (store / SESSION / "out.txt").read_text() == "x"


def test_a_read_only_tree_a_turn_left_is_removed_and_the_start_runs(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root removes anything, so the case cannot be staged")
    assert _start(tmp_path).returncode == 0
    held = _claude(tmp_path) / "skills" / "held" / "deeper"
    held.mkdir(parents=True)
    (held / "f.md").write_text("x")
    os.chmod(held, 0o555)
    os.chmod(held.parent, 0o555)
    try:
        res = _start(tmp_path, "again")
    finally:
        for d in (held.parent, held):
            if d.exists():
                os.chmod(d, 0o755)
    assert res.returncode == 0, res.stderr
    assert not (_claude(tmp_path) / "skills").exists()


def test_a_link_to_a_read_only_folder_in_a_removed_tree_is_not_followed(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignores modes")
    canary = Canary(tmp_path)
    os.chmod(canary.dir, 0o555)
    canary._before = canary.state()
    assert _start(tmp_path).returncode == 0
    skills = _claude(tmp_path) / "skills"
    skills.mkdir()
    canary.link_dir(skills / "out")
    try:
        assert _start(tmp_path, "again").returncode == 0
        assert not os.path.lexists(skills)
        canary.assert_untouched()
    finally:
        os.chmod(canary.dir, 0o755)


def test_a_store_name_with_a_newline_after_a_uuid_is_not_kept(tmp_path):
    store = _claude(tmp_path) / "projects" / SLUG
    store.mkdir(parents=True)
    (store / f"{SESSION}\nextra").mkdir()
    (store / f"{SESSION}.jsonl\nextra").write_text("{}\n")
    (store / f"{SESSION}.jsonl").write_text("{}\n")
    assert _start(tmp_path).returncode == 0
    assert [p.name for p in store.iterdir()] == [f"{SESSION}.jsonl"]


def test_a_start_whose_allow_list_differs_from_the_images_refuses(tmp_path):
    # A setup step that quietly installed nothing (it is fail-open): the start must not run on.
    stub = tmp_path / "setup-stub.sh"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    res = _start(tmp_path, "must-not-exist", ENTRYPOINT_NS_SETUP=str(stub))
    assert res.returncode != 0
    assert "differ from the image" in res.stderr and "refusing to start" in res.stderr
    assert not (tmp_path / "must-not-exist").exists()


def test_a_start_without_the_entity_hook_refuses(tmp_path):
    hook = tmp_path / "hook-not-executable.sh"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o644)  # the hook step skips a hook it cannot run
    res = _start(tmp_path, "must-not-exist", ENTRYPOINT_NS_HOOK=str(hook))
    assert res.returncode != 0
    assert "refusing to start" in res.stderr
    assert not (tmp_path / "must-not-exist").exists()


def test_a_start_without_the_baked_copy_refuses(tmp_path):
    (_bake(tmp_path) / "expected-settings.json").unlink()
    res = _start(tmp_path, "must-not-exist")
    assert res.returncode != 0
    assert "refusing to start" in res.stderr
    assert not (tmp_path / "must-not-exist").exists()


def test_the_image_bakes_the_copy_the_start_checks_against():
    dockerfile = (paths.CC_RUNTIME_DIR / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    assert "SETTINGS_FILE=/app/claude-home/expected-settings.json sh /app/plugins/nextseek/scripts/setup.sh" in dockerfile
    assert "-f /app/claude-home/entity-hook.jq" in dockerfile
    assert '-f "$BAKED_HOME/entity-hook.jq"' in entrypoint
    assert "expected-settings.json" in entrypoint


def test_setup_overwrites_the_allow_list(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(*)", "Bash(nextseek-graph:*)"]}}))
    subprocess.run(["sh", str(SETUP)], env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                            "HOME": str(tmp_path), "SETTINGS_FILE": str(settings)},
                   check=True, capture_output=True, timeout=60)
    assert json.loads(settings.read_text())["permissions"]["allow"] == _setup_allow_list()
