#!/usr/bin/env python3
"""Post-merge survival assertions (SPEC v2 section 6.5). Exit non-zero on any failure."""
from __future__ import annotations
import ast, re, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
failures: list[str] = []


def check(sid: str, desc: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {sid}: {desc}")
    if not ok:
        failures.append(sid)


def has(path: str, pattern: str) -> bool:
    p = ROOT / path
    # re.MULTILINE so `^`/`$` anchor per line (S4's SEEK_PUBLIC_URL default line is mid-file, not file-start).
    return p.exists() and re.search(pattern, p.read_text(), re.MULTILINE) is not None


def python_imports(path: str, name: str) -> bool:
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == name or alias.name.endswith("." + name) for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == name or mod.endswith("." + name):
                return True
            if any(alias.name == name for alias in node.names):
                return True
    return False


def python_calls_attr(path: str, object_name: str, attr_name: str) -> bool:
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != attr_name:
            continue
        value = node.func.value
        if isinstance(value, ast.Name) and value.id == object_name:
            return True
    return False


check("S1", "portable.py exports generate_report_outputs",
      has("chat_nextseek/src/chat_nextseek/portable.py", r"generate_report_outputs"))
check("S2", "parser.py + planner/tools.py keep the F821 import fixes",
      # the real F821 fix imports _resolve_step_inputs from ..parser (feat tools.py imports it;
      # dev/base use it bare = the F821 defect feat fixes; the auto-merged tree keeps feat's import).
      has("chat_nextseek/src/chat_nextseek/agents/parser.py", r"_TERMINAL_REPLY_TOOLS")
      and python_imports("chat_nextseek/src/chat_nextseek/agents/planner/tools.py", "_resolve_step_inputs"))
check("S3", "config.py keeps _resolve_nextseek_base_url and applies it",
      has("chat_nextseek/src/chat_nextseek/config.py", r"def _resolve_nextseek_base_url")
      and has("chat_nextseek/src/chat_nextseek/config.py", r"_resolve_nextseek_base_url\(\)"))
check("S4", "settings.py registers cc_assistant and has guard-safe SEEK_PUBLIC_URL",
      has("dmac/settings.py", r"cc_assistant\.apps\.CcAssistantConfig")
      and has("dmac/settings.py", r'^SEEK_PUBLIC_URL = os\.getenv\("SEEK_PUBLIC_URL", ""\)'))
compose = (ROOT / "docker-compose.yml").read_text()
compose_yml = yaml.safe_load(compose)
check("S5", "compose keeps the dmac-cc-net name pin",
      re.search(r"dmac-cc-net:\s*\n\s*name:\s*dmac-cc-net", compose) is not None)
check("S6", "CC identities never INSTANCE_PREFIXed",
      compose_yml["services"]["nextseek-sidecar"].get("container_name") == "nextseek-sidecar"
      and compose_yml["services"]["bedrock-proxy"].get("container_name") == "dmac-bedrock-proxy"
      and compose_yml["networks"]["dmac-cc-net"].get("name") == "dmac-cc-net")
check("S7", "INSTANCE_PREFIX present on the 6 sanctioned container_names",
      # compare the prefix-stripped container_name VALUES, not the service dict KEYS.
      sorted(
          str(svc["container_name"]).removeprefix("${INSTANCE_PREFIX:-}")
          for svc in compose_yml["services"].values()
          if str(svc.get("container_name", "")).startswith("${INSTANCE_PREFIX:-}")
      ) == ["neo4j", "nextseek", "seek", "seek-mysql", "seek-solr", "seek-workers"])
check("S8", "orchestrator routes NFCORE -> pipeline_agent.start",
      python_calls_attr("chat_nextseek/src/chat_nextseek/orchestrator.py", "pipeline_agent", "start"))
wizard_imports = [
    p for p in (ROOT / "chat_nextseek" / "src").rglob("*.py")
    if re.search(r"^\s*(from|import)\s+[\w.]*\bwizard\b", p.read_text(), re.M)
]
check("S9", "zero wizard imports remain", not wizard_imports)

# S10 (2026-07-08 amendment): the merged entrypoint.sh must UNION dev's /media mkdir with feat's
# R2/R3/FU4 boot-hardening. A take-theirs (dev) resolution drops feat's fail-fast guards; a take-ours
# (feat) resolution drops dev's /media dirs. Either regression FAILS this check.
entry = "docker/scripts/entrypoint.sh"
check("S10", "entrypoint.sh unions dev /media mkdir with feat R2/R3/FU4 boot-hardening",
      has(entry, r"mkdir -p /media/download")
      and has(entry, r"\[COLLECTSTATIC-FAILED\]")
      and has(entry, r"collectstatic --noinput \|\| \{")
      and has(entry, r"\[DB-UNREACHABLE\]")
      and has(entry, r"DB_WAIT_ATTEMPTS")
      and has(entry, r"\[MIGRATE-FAILED\]")
      and has(entry, r"migrate --noinput \|\| \{"))

# S11 (2026-07-08 post-Phase-A amendment): the merged docker_ops.py must retain feat's superset.
# Phase A Task 2 added compose_ps_running; dev independently added compose_port (byte-identical to feat's).
# Take-feat preserves all four; a take-dev resolution drops compose_ps_running/compose_build/bootstrap_staging_dir.
dops = "startup/lib/docker_ops.py"
check("S11", "docker_ops.py retains feat superset (compose_ps_running/compose_port/compose_build/bootstrap_staging_dir)",
      has(dops, r"def compose_ps_running\(")
      and has(dops, r"def compose_port\(")
      and has(dops, r"def compose_build\(")
      and has(dops, r"def bootstrap_staging_dir\("))

sys.exit(1 if failures else 0)
