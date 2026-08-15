"""Future-op drop-in proof: real CLIs, three transports, actual audit/check negatives."""
from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.op_registry.models import Backend, GateClass, Transport

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_REL = Path("docker/cc-runtime/build_context/plugins/nextseek")
SKILL_REL = Path(".claude/skills/add-cc-op/SKILL.md")
EXPORT_MOD = "nextseek_api.cc_assistant.op_registry.export"
GEN_MOD = "build_tools.gen_op_surfaces"
AUDIT_REL = Path("nextseek_api/cc_assistant/tests/test_op_registry_audit.py")
OPS_REL = Path("nextseek_api/cc_assistant/op_registry/ops.py")
RUNNER_REL = PLUGIN_REL / "bin" / "_nextseek_runner.py"
BATCH_REL = PLUGIN_REL / "bin" / "_batch_upload_runner.py"
WS_REL = PLUGIN_REL / "bin" / "_ws_contract.py"
GRANULAR_REL = Path("nextseek_api/assistant/granular.py")
WRITE_GATE_REL = Path("nextseek_api/assistant/write_gate.py")
CLAUDE_MD_REL = Path("docker/cc-runtime/container/CLAUDE.md")
DOCKERFILE_REL = Path("docker/cc-runtime/Dockerfile")
ROUTE_CAP_REL = Path("dmac_assistant/build_context/route_capabilities.json")
OPS_JSON_REL = Path("nextseek_api/cc_assistant/op_registry/ops.json")
BAKED_OPS_REL = PLUGIN_REL / "context" / "ops.json"
COMMANDS_REL = PLUGIN_REL / "commands" / "nextseek.md"
SKILL_NEXTSEEK_REL = PLUGIN_REL / "skills" / "nextseek" / "SKILL.md"
SKILL_BATCH_REL = PLUGIN_REL / "skills" / "nextseek-batch-upload" / "SKILL.md"

COPY_PATHS = (
    PLUGIN_REL,
    Path("docker/cc-runtime/Dockerfile"),
    Path("docker/cc-runtime/container/CLAUDE.md"),
    Path("docker-compose.yml"),
    Path("nextseek_api/cc_assistant/op_registry"),
    Path("nextseek_api/cc_assistant/tests/test_op_registry_audit.py"),
    Path("nextseek_api/cc_assistant/__init__.py"),
    Path("nextseek_api/__init__.py"),
    Path("nextseek_api/assistant/granular.py"),
    Path("nextseek_api/assistant/write_gate.py"),
    Path("nextseek_api/assistant/read_safe_endpoints.json"),
    Path("dmac_assistant/build_context/route_capabilities.json"),
    Path("chat_nextseek/src/chat_nextseek/context/capabilities.md"),
    Path("nessie_tests/corpus.json"),
)

STEP_HEADINGS = (
    "Add an executable shim using the common runner contract",
    "Register the exact `_DISPATCH` or `_CMDS` runner_key",
    "Add the NExtSEEK OpSpec row",
    "Hand-authored server enforcement",
    "New plugin: add its manifest-bearing tree",
    "Export `ops.json`",
    "Regenerate mechanical surfaces",
    "Run Audit A, no-write checks, focused tests, and the Task 12 gate",
)


def _interpreter() -> str:
    image_py = Path("/app/.venv/bin/python")
    if image_py.exists():
        return sys.executable
    dmac = Path("/home/taishajo/work/dmac-assistant/.venv/bin/python3")
    if dmac.exists():
        return str(dmac)
    return sys.executable


def _pythonpath(root: Path) -> str:
    parts = [
        str(root),
        str(REPO_ROOT),
        str(REPO_ROOT / "dmac_assistant" / "src"),
        str(REPO_ROOT / "chat_nextseek" / "src"),
        str(REPO_ROOT / "chat_nextseek"),
        str(REPO_ROOT / "dmac_assistant" / "tools" / "e2e"),
    ]
    return os.pathsep.join(parts)


def _env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = str(root / ".tmp")
    env.pop("DJANGO_SETTINGS_MODULE", None)
    (root / ".tmp").mkdir(exist_ok=True)
    return env


def _copy_sandbox(tmp: Path) -> Path:
    root = tmp / "repo"
    for rel in COPY_PATHS:
        src = REPO_ROOT / rel
        if not src.exists():
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dest)
    (root / "nextseek_api" / "cc_assistant" / "tests").mkdir(parents=True, exist_ok=True)
    init = root / "nextseek_api" / "cc_assistant" / "tests" / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    return root


def _run(root: Path, module: str, args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_interpreter(), "-m", module, *args],
        cwd=str(root),
        env=_env(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _insert_before_marker(text: str, marker: str, insertion: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        raise AssertionError(f"marker not found: {marker}")
    return text[:idx] + insertion + text[idx:]


def _write_shim(root: Path, *, bin_name: str, runner_key: str, transport: Transport) -> None:
    path = root / PLUGIN_REL / "bin" / bin_name
    if transport is Transport.local_subcommand:
        body = textwrap.dedent(
            f"""\
            #!/bin/sh
            set -eu
            SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
            . "$SCRIPT_DIR/_nextseek_common.sh"
            for arg in "$@"; do
              case "$arg" in
                --confirmed-write|--start|--upload)
                  nextseek_die 3 "$arg is forbidden: this tool is read-only" ;;
              esac
            done
            exec python "$SCRIPT_DIR/_batch_upload_runner.py" {runner_key} "$@"
            """
        )
    else:
        body = textwrap.dedent(
            f"""\
            #!/bin/sh
            set -eu
            SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
            . "$SCRIPT_DIR/_nextseek_common.sh"
            QUERY=""
            while [ $# -gt 0 ]; do
              case "$1" in
                --query) QUERY="$2"; shift 2 ;;
                --query=*) QUERY="${{1#--query=}}"; shift ;;
                *) nextseek_die 3 "unknown arg: $1" ;;
              esac
            done
            [ -n "$QUERY" ] || nextseek_die 3 "missing --query"
            exec python "$SCRIPT_DIR/_nextseek_runner.py" --agent {runner_key} --query "$QUERY"
            """
        )
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _append_opspec(
    root: Path,
    *,
    op_id: str,
    bin_name: str,
    runner_key: str,
    transport: Transport,
) -> None:
    path = root / OPS_REL
    text = path.read_text(encoding="utf-8")
    if transport is Transport.local_subcommand:
        row = textwrap.dedent(
            f"""\
            _subcmd(
                op_id={op_id!r},
                bin_name={bin_name!r},
                runner_key={runner_key!r},
                argv=[ArgSpec(flag="--query", required=True)],
                skill_name="nextseek-batch-upload",
                skill_row=_row("Temporary drop-in op.", '--query "<text>"', "{{ok}}"),
            ),
            """
        )
    else:
        gate = (
            "GateClass.unrouted"
            if transport is Transport.viewset
            else "GateClass.read"
        )
        per_op = "False" if transport is Transport.viewset else "True"
        endpoint = (
            "_QUERY_ASYNC"
            if transport is Transport.viewset
            else '"/nextseek_api/assistant/entity/"'
        )
        row = textwrap.dedent(
            f"""\
            _dispatch(
                op_id={op_id!r},
                bin_name={bin_name!r},
                runner_key={runner_key!r},
                transport=Transport.{transport.value},
                assistant_endpoint={endpoint},
                gate_class={gate},
                per_op_gate_enabled={per_op},
                argv=[ArgSpec(flag="--query", required=True)],
                skill_name="nextseek",
                skill_row=_row("Temporary drop-in op.", '--query "<text>"', "{{ok}}"),
            ),
            """
        )
    marker = "\n]\n"
    idx = text.rfind(marker)
    if idx < 0:
        if text.rstrip().endswith("]"):
            text = text.rstrip()[:-1] + "\n" + row + "]\n"
            path.write_text(text, encoding="utf-8")
            return
        raise AssertionError("OPS list terminator not found")
    path.write_text(text[:idx] + "\n" + row + "]\n", encoding="utf-8")


def _append_dispatch(root: Path, runner_key: str) -> None:
    handler = f"_dispatch_{runner_key.replace('-', '_')}"
    runner = root / RUNNER_REL
    text = runner.read_text(encoding="utf-8")
    fn = textwrap.dedent(
        f"""\

        def {handler}(args):
            if _dry_run():
                return {{"ok": True, "query": args.query}}
            return {{"ok": True, "query": args.query}}
        """
    )
    text = _insert_before_marker(text, "\n_DISPATCH = {", fn)
    text = text.replace(
        "_DISPATCH = {\n",
        f"_DISPATCH = {{\n    {runner_key!r}: {handler},\n",
        1,
    )
    runner.write_text(text, encoding="utf-8")


def _append_cmds(root: Path, runner_key: str) -> None:
    handler = f"_cmd_{runner_key.replace('-', '_')}"
    batch = root / BATCH_REL
    text = batch.read_text(encoding="utf-8")
    fn = textwrap.dedent(
        f"""\

        def {handler}(argv, *, transport=None):
            return 0
        """
    )
    text = _insert_before_marker(text, "\n_CMDS = {", fn)
    text = text.replace(
        "_CMDS = {\n",
        f"_CMDS = {{\n    {runner_key!r}: {handler},\n",
        1,
    )
    batch.write_text(text, encoding="utf-8")


def _append_sidecar_fixtures(root: Path, runner_key: str) -> None:
    ws = root / WS_REL
    ws_text = ws.read_text(encoding="utf-8")
    ws.write_text(
        ws_text.replace(
            '"build-upload-xlsx"}',
            f'"build-upload-xlsx", {runner_key!r}}}',
            1,
        ),
        encoding="utf-8",
    )
    granular = root / GRANULAR_REL
    gtext = granular.read_text(encoding="utf-8")
    fn_name = f"_{runner_key.replace('-', '_')}_dropin"
    fn = textwrap.dedent(
        f"""\

        def {fn_name}(payload):
            return {{"ok": True}}
        """
    )
    gtext = _insert_before_marker(gtext, "\n_HANDLERS: dict[str, Callable] = {", fn)
    gtext = gtext.replace(
        "_HANDLERS: dict[str, Callable] = {\n",
        f"_HANDLERS: dict[str, Callable] = {{\n    {runner_key!r}: {fn_name},\n",
        1,
    )
    granular.write_text(gtext, encoding="utf-8")
    wg = root / WRITE_GATE_REL
    wtext = wg.read_text(encoding="utf-8")
    wg.write_text(
        wtext.replace(
            '"generate-submission"}',
            f'"generate-submission", {runner_key!r}}}',
            1,
        ),
        encoding="utf-8",
    )


def _patch_viewset_audit(root: Path, runner_key: str) -> None:
    path = root / AUDIT_REL
    text = path.read_text(encoding="utf-8")
    old = '{"query", "plan", "recall", "pipeline"}'
    if old not in text:
        raise AssertionError("viewset membership oracle not found")
    path.write_text(
        text.replace(
            old,
            '{"query", "plan", "recall", "pipeline", %r}' % runner_key,
            1,
        ),
        encoding="utf-8",
    )


def _install_op(root: Path, transport: Transport) -> dict[str, str]:
    token = uuid.uuid4().hex[:12]
    op_id = f"dropin-{token}"
    runner_key = f"dropin-{token}"
    bin_name = f"nextseek-dropin-{token}"
    _write_shim(root, bin_name=bin_name, runner_key=runner_key, transport=transport)
    _append_opspec(
        root,
        op_id=op_id,
        bin_name=bin_name,
        runner_key=runner_key,
        transport=transport,
    )
    if transport is Transport.local_subcommand:
        _append_cmds(root, runner_key)
    else:
        _append_dispatch(root, runner_key)
        if transport is Transport.sidecar:
            _append_sidecar_fixtures(root, runner_key)
        if transport is Transport.viewset:
            _patch_viewset_audit(root, runner_key)
    return {
        "op_id": op_id,
        "bin_name": bin_name,
        "runner_key": runner_key,
        "transport": transport.value,
    }


def _hashes(root: Path, rels: tuple[Path, ...]) -> dict[str, str]:
    import hashlib

    out: dict[str, str] = {}
    for rel in rels:
        path = root / rel
        if path.is_file():
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


GENERATED_RELS = (
    OPS_JSON_REL,
    BAKED_OPS_REL,
    COMMANDS_REL,
    SKILL_NEXTSEEK_REL,
    SKILL_BATCH_REL,
    CLAUDE_MD_REL,
    ROUTE_CAP_REL,
)


def _write_surfaces(root: Path) -> None:
    exported = _run(root, EXPORT_MOD, ["--write", "--root", str(root)])
    assert exported.returncode == 0, exported.stderr
    generated = _run(root, GEN_MOD, ["--write", "--root", str(root)], timeout=240)
    assert generated.returncode in {0, 2}, generated.stderr + generated.stdout


def _check_surfaces(root: Path) -> subprocess.CompletedProcess[str]:
    export = _run(root, EXPORT_MOD, ["--check", "--root", str(root)])
    gen = _run(root, GEN_MOD, ["--check", "--root", str(root)], timeout=240)
    return gen if gen.returncode != 0 else export


def _audit(root: Path, *node_ids: str) -> subprocess.CompletedProcess[str]:
    args = [
        "-m",
        "pytest",
        str(AUDIT_REL),
        "-p",
        "no:cacheprovider",
        "-p",
        "no:django",
        "-q",
        "--tb=line",
    ]
    for node in node_ids:
        args.extend(["-k", node])
    return subprocess.run(
        [_interpreter(), *args],
        cwd=str(root),
        env=_env(root),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize(
    "transport",
    [Transport.viewset, Transport.sidecar, Transport.local_subcommand],
    ids=["viewset", "sidecar", "local_subcommand"],
)
def test_future_op_dropin_real_clis_change_generated_targets(tmp_path: Path, transport: Transport):
    root = _copy_sandbox(tmp_path)
    before = _hashes(root, GENERATED_RELS)
    meta = _install_op(root, transport)
    _write_surfaces(root)
    after = _hashes(root, GENERATED_RELS)
    assert after[OPS_JSON_REL.as_posix()] != before[OPS_JSON_REL.as_posix()]
    assert after[BAKED_OPS_REL.as_posix()] != before[BAKED_OPS_REL.as_posix()]
    assert after[COMMANDS_REL.as_posix()] != before[COMMANDS_REL.as_posix()]
    skill_rel = (
        SKILL_BATCH_REL
        if transport is Transport.local_subcommand
        else SKILL_NEXTSEEK_REL
    )
    assert after[skill_rel.as_posix()] != before.get(skill_rel.as_posix(), "")
    assert after[CLAUDE_MD_REL.as_posix()] != before[CLAUDE_MD_REL.as_posix()]
    ops_payload = json.loads((root / OPS_JSON_REL).read_text(encoding="utf-8"))
    assert any(row["op_id"] == meta["op_id"] for row in ops_payload)
    baked = json.loads((root / BAKED_OPS_REL).read_text(encoding="utf-8"))
    assert any(row["op_id"] == meta["op_id"] for row in baked)
    commands = (root / COMMANDS_REL).read_text(encoding="utf-8")
    assert meta["bin_name"] in commands
    skill_text = (root / skill_rel).read_text(encoding="utf-8")
    assert meta["bin_name"] in skill_text or meta["op_id"] in skill_text
    claude = (root / CLAUDE_MD_REL).read_text(encoding="utf-8")
    assert meta["bin_name"] in claude
    if transport is not Transport.viewset:
        assert after[ROUTE_CAP_REL.as_posix()] != before.get(ROUTE_CAP_REL.as_posix(), "")
    check = _check_surfaces(root)
    assert check.returncode == 0, check.stderr
    mapping = _audit(
        root,
        "test_installed_shims_match_available_ops_bidirectionally"
        " or test_dispatch_runner_keys_match_ops"
        " or test_subcmd_runner_keys_match_ops"
        " or test_dispatch_handlers_match_runner_keys"
        " or test_subcmd_handlers_match_runner_keys"
        " or test_each_shim_runner_key_and_argv_forwarding"
        " or test_sidecar_transport_matches_ws_contract_and_handlers"
        " or test_local_subcommand_transport_matches_batch_runner"
        " or test_viewset_transport_ops_are_query_plan_recall_pipeline"
        " or test_gate_class_matches_enforcement",
    )
    assert mapping.returncode == 0, mapping.stdout + mapping.stderr
    tree = ast.parse((root / OPS_REL).read_text(encoding="utf-8"))
    assert tree.body
    assert meta["transport"] == transport.value
    if transport is Transport.viewset:
        assert GateClass.unrouted.value == "unrouted"
        assert Backend.dispatch.value == "dispatch"


def test_future_op_negatives_fail_actual_audit_and_check(tmp_path: Path):
    root = _copy_sandbox(tmp_path)
    meta = _install_op(root, Transport.sidecar)
    _write_surfaces(root)

    shim = root / PLUGIN_REL / "bin" / meta["bin_name"]
    shim_bytes = shim.read_bytes()
    shim.unlink()
    missing_shim = _audit(root, "test_installed_shims_match_available_ops_bidirectionally")
    assert missing_shim.returncode != 0
    shim.write_bytes(shim_bytes)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    ops_path = root / OPS_REL
    ops_text = ops_path.read_text(encoding="utf-8")
    ops_path.write_text(
        ops_text.replace(f"bin_name={meta['bin_name']!r}", "bin_name='nextseek-missing-op'", 1),
        encoding="utf-8",
    )
    missing_opspec = _audit(root, "test_installed_shims_match_available_ops_bidirectionally")
    assert missing_opspec.returncode != 0
    ops_path.write_text(ops_text, encoding="utf-8")

    runner = root / RUNNER_REL
    runner_text = runner.read_text(encoding="utf-8")
    runner.write_text(
        runner_text.replace(f"{meta['runner_key']!r}:", "'not-the-op':", 1),
        encoding="utf-8",
    )
    wrong_dispatch = _audit(root, "test_dispatch_runner_keys_match_ops")
    assert wrong_dispatch.returncode != 0
    runner.write_text(runner_text, encoding="utf-8")

    ws = root / WS_REL
    ws_text = ws.read_text(encoding="utf-8")
    ws.write_text(ws_text.replace(f", {meta['runner_key']!r}", "", 1), encoding="utf-8")
    missing_contract = _audit(root, "test_sidecar_transport_matches_ws_contract_and_handlers")
    assert missing_contract.returncode != 0
    ws.write_text(ws_text, encoding="utf-8")

    granular = root / GRANULAR_REL
    gtext = granular.read_text(encoding="utf-8")
    granular.write_text(
        gtext.replace(f"    {meta['runner_key']!r}:", "    'not-handler':", 1),
        encoding="utf-8",
    )
    missing_handler = _audit(root, "test_sidecar_transport_matches_ws_contract_and_handlers")
    assert missing_handler.returncode != 0
    granular.write_text(gtext, encoding="utf-8")

    shim.write_text(
        shim.read_text(encoding="utf-8").replace(
            f'--agent {meta["runner_key"]} --query',
            f'--agent {meta["runner_key"]} --mode',
            1,
        ),
        encoding="utf-8",
    )
    wrong_argv = _audit(root, "test_each_shim_runner_key_and_argv_forwarding")
    assert wrong_argv.returncode != 0
    shim.write_bytes(shim_bytes)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    baked = root / BAKED_OPS_REL
    baked_bytes = baked.read_bytes()
    baked.unlink()
    missing_baked = _run(root, EXPORT_MOD, ["--check", "--root", str(root)])
    assert missing_baked.returncode != 0
    baked.write_bytes(baked_bytes)

    claude = root / CLAUDE_MD_REL
    claude_bytes = claude.read_bytes()
    claude.write_text(claude.read_text(encoding="utf-8").replace(meta["bin_name"], "stale-bin"), encoding="utf-8")
    stale_docs = _run(root, GEN_MOD, ["--check", "--root", str(root)], timeout=240)
    assert stale_docs.returncode != 0
    claude.write_bytes(claude_bytes)

    dockerfile = root / DOCKERFILE_REL
    docker_bytes = dockerfile.read_bytes()
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace(
            "COPY build_context/plugins/nextseek/",
            "COPY build_context/plugins/stale/",
            1,
        ),
        encoding="utf-8",
    )
    stale_copy = _run(root, GEN_MOD, ["--check", "--root", str(root)], timeout=240)
    assert stale_copy.returncode != 0
    dockerfile.write_bytes(docker_bytes)
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace(
            'ENV PATH="/app/plugins/nextseek/bin:${PATH}"',
            'ENV PATH="/app/plugins/stale/bin:${PATH}"',
            1,
        ),
        encoding="utf-8",
    )
    stale_path = _run(root, GEN_MOD, ["--check", "--root", str(root)], timeout=240)
    assert stale_path.returncode != 0
    dockerfile.write_bytes(docker_bytes)

    route = root / ROUTE_CAP_REL
    route_bytes = route.read_bytes()
    payload = json.loads(route_bytes)
    for item in payload.get("routes", []):
        if item.get("route_name") == "container_cc":
            item["tools"] = ["stale-tool"]
    route.write_text(json.dumps(payload), encoding="utf-8")
    stale_tools = _run(root, GEN_MOD, ["--check", "--root", str(root)], timeout=240)
    assert stale_tools.returncode != 0
    route.write_bytes(route_bytes)


def test_add_cc_op_skill_is_discoverable():
    skill = REPO_ROOT / SKILL_REL
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    for heading in STEP_HEADINGS:
        assert heading in text
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    needle = ".claude/skills/add-cc-op/SKILL.md"
    assert needle in claude or "/add-cc-op" in claude
    assert needle in agents or "/add-cc-op" in agents
    container = (REPO_ROOT / CLAUDE_MD_REL).read_text(encoding="utf-8").casefold()
    assert "plugin.json" not in container or "source of truth" not in container
    assert "discover_ops" not in container
    assert "registration authority" not in container
    assert "registration soT" not in container
    assert "plugin.json is the registration" not in container
