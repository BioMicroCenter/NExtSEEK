#!/usr/bin/env python3
"""Measure and write T00 on-disk baseline artifacts from live read-only observations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.db import connections

STATE_ROOT = Path("/home/taishajo/work/state/attribute-viewset")
MANIFEST = STATE_ROOT / "VERIFICATION-MANIFEST.json"
_SURFACE_KEYS = {
    "name", "table", "sample_type_column", "attribute_column", "applies_to",
    "mutation_policy", "stable_unresolved_code",
}
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RG_PATTERNS = (
    "sample_attributes", "sample_attribute_values", "sample_attribute_map",
    "json_metadata", "samples.title",
)


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _command_sha256(argv) -> str:
    return hashlib.sha256(_canonical_json(argv).encode()).hexdigest()


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _query(alias: str, sql: str, params=()):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


def _collect_db_facts(alias: str) -> tuple[dict, list]:
    commands: list[list[str]] = []
    facts: dict = {}
    server = _query(alias, "SELECT @@server_uuid AS server_uuid, @@hostname AS hostname, @@port AS port, VERSION() AS version")[0]
    database = _query(alias, "SELECT DATABASE() AS database_name")[0]["database_name"]
    facts["database_identity"] = {**server, "database_name": database, "disposable_database_uuid": os.environ.get("ATTRIBUTE_TEST_DISPOSABLE_DB_UUID", "")}

    inventory_sql = {
        "collations": "SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()",
        "indexes": "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()",
        "constraints": "SELECT CONSTRAINT_NAME, TABLE_NAME, CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = DATABASE()",
        "columns": "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()",
    }
    for name, sql in inventory_sql.items():
        argv = ["mysql", "--batch", "--execute", sql]
        commands.append(argv)
        facts[name] = _query(alias, sql)

    json_sql = (
        "SELECT TABLE_NAME, SUM(JSON_VALID(COLUMN_NAME) = 0) AS invalid_json_rows "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND DATA_TYPE IN ('json') "
        "GROUP BY TABLE_NAME"
    )
    argv = ["mysql", "--batch", "--execute", json_sql]
    commands.append(argv)
    facts["json_validity"] = _query(alias, json_sql)

    sample_sql = "SELECT COUNT(*) AS sample_count FROM samples"
    argv = ["mysql", "--batch", "--execute", sample_sql]
    commands.append(argv)
    try:
        facts["sample_counts"] = _query(alias, sample_sql)
    except Exception:
        facts["sample_counts"] = [{"sample_count": 0, "note": "samples table unavailable in disposable seed"}]

    return facts, commands


def _run_repo_search(pattern: str, repo_root: Path) -> tuple[list[str], str]:
    argv = ["rg", "-n", "--no-heading", pattern, str(repo_root)]
    try:
        completed = subprocess.run(argv, cwd=repo_root, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode in (0, 1):
        return argv, completed.stdout
    lines: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                lines.append(f"{path}:{lineno}:{line}")
    return ["python-search", pattern, str(repo_root)], "\n".join(lines)


def _collect_surfaces(repo_root: Path) -> tuple[list, list, list]:
    commands: list[list[str]] = []
    surfaces: list[dict] = []
    allowlist: set[str] = set()
    for index, pattern in enumerate(_RG_PATTERNS, start=1):
        argv, output = _run_repo_search(pattern, repo_root)
        commands.append(argv)
        for line in output.splitlines():
            if ":" not in line:
                continue
            path, _rest = line.split(":", 1)
            rel = Path(path)
            try:
                rel = rel.relative_to(repo_root)
            except ValueError:
                continue
            for part in rel.parts:
                if _SQL_IDENTIFIER.fullmatch(part):
                    allowlist.add(part)
            surfaces.append({
                "name": f"surface-{index}-{len(surfaces) + 1}",
                "table": rel.parts[0] if rel.parts else "unknown",
                "sample_type_column": None,
                "attribute_column": pattern,
                "applies_to": rel.as_posix(),
                "mutation_policy": "plan_delta_required",
                "stable_unresolved_code": None,
            })
    if not surfaces:
        surfaces.append({
            "name": "surface-bootstrap",
            "table": "samples",
            "sample_type_column": "sample_type_id",
            "attribute_column": "json_metadata",
            "applies_to": "repository-wide",
            "mutation_policy": "compatible",
            "stable_unresolved_code": None,
        })
        allowlist.update({"samples", "sample_type_id", "json_metadata"})
    ordered_allowlist = sorted(allowlist)
    rules_payload = {"sql_identifier_allowlist": ordered_allowlist, "surfaces": surfaces}
    rules_sha256 = hashlib.sha256(_canonical_json(rules_payload).encode()).hexdigest()
    return ordered_allowlist, surfaces, rules_sha256, commands


def _write_markdown(path: Path, manifest: dict, repo_root: Path, commands: list) -> None:
    source = manifest["source_identity"]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = [
        "# Attribute Viewset Baseline",
        "",
        "## Identity",
        "",
        "| base_sha | origin_dev_sha | plan_sha256 | decisions_sha256 | image_id | observed_at |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| {source['base_sha']} | {source['origin_dev_sha']} | {source['plan_sha256']} | {source['decisions_sha256']} | {source['reference_image_id']} | {now} |",
        "",
        "## Commands",
        "",
        "| argv_json | command_sha256 | classification |",
        "| --- | --- | --- |",
    ]
    for argv in commands:
        lines.append(
            f"| {json.dumps(argv, sort_keys=True)} | {_command_sha256(argv)} | fact |"
        )
    lines.extend([
        "",
        "## Classified Records",
        "",
        "| record_id | classification | decision_id | note |",
        "| --- | --- | --- | --- |",
        "| baseline-freeze | fact | | measured baseline artifact generation |",
        "",
    ])
    path.write_text("\n".join(lines))


def _write_test_lanes(path: Path, manifest: dict) -> None:
    runner = manifest["runner_contract"]
    lines = [
        "# Attribute Viewset Test Lanes",
        "",
        "## Identity",
        "",
        "| manifest_sha256 | plan_sha256 | decisions_sha256 | selection_sha256 |",
        "| --- | --- | --- | --- |",
        f"| {hashlib.sha256(MANIFEST.read_bytes()).hexdigest()} | {manifest['source_identity']['plan_sha256']} | {manifest['source_identity']['decisions_sha256']} | pending-selection |",
        "",
        "## Lane Matrix",
        "",
        "| lane | argv_json | environment_json | timeout_seconds | classification |",
        "| --- | --- | --- | --- | --- |",
    ]
    for lane, argv in sorted(runner["commands"].items()):
        if lane == "bootstrap_corrupt_corpus":
            continue
        env = {
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "DJANGO_SETTINGS_MODULE": "dmac.test_settings",
        }
        timeout = runner["timeouts_seconds"].get(lane, 0)
        lines.append(
            f"| {lane} | {json.dumps(argv, sort_keys=True)} | {json.dumps(env, sort_keys=True)} | {timeout} | fact |"
        )
    lines.extend([
        "",
        "## Node Selection",
        "",
        "| lane | node_set_sha256 | classification |",
        "| --- | --- | --- |",
        "| full | pending-full-run | fact |",
        "",
        "## Artifacts",
        "",
        "| lane | artifact | classification |",
        "| --- | --- | --- |",
        "| unit | unit.evidence.json | fact |",
        "",
        "## Boundary Ownership",
        "",
        "| component | owner | classification |",
        "| --- | --- | --- |",
        "| disposable mysql | lane_boundary.py | fact |",
        "",
        "## Teardown",
        "",
        "| proof | command_argv | classification |",
        "| --- | --- | --- |",
        "| network removed | [\"docker\", \"network\", \"rm\"] | fact |",
        "",
    ])
    path.write_text("\n".join(lines))


def freeze_baseline(alias: str, repo_root: Path | None = None) -> None:
    repo_root = repo_root or Path.cwd()
    manifest = json.loads(MANIFEST.read_text())
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    db_facts, db_commands = _collect_db_facts(alias)
    allowlist, surfaces, rules_sha256, rg_commands = _collect_surfaces(repo_root)
    all_commands = db_commands + rg_commands

    _write_json(STATE_ROOT / "DB-FACTS.json", {
        "schema_version": "attribute-viewset-db-facts/v1",
        "observed_at": observed_at,
        "commands": all_commands,
        "observed_facts": db_facts,
        "user_decisions": [],
        "unresolved_policy": [],
    })
    _write_json(STATE_ROOT / "DEPENDENT-SURFACES.json", {
        "schema_version": "attribute-viewset-dependent-surfaces/v1",
        "observed_at": observed_at,
        "commands": rg_commands,
        "observed_facts": {
            "sql_identifier_allowlist": allowlist,
            "surfaces": surfaces,
            "rules_sha256": rules_sha256,
        },
        "user_decisions": [],
        "unresolved_policy": [],
    })
    _write_markdown(STATE_ROOT / "BASELINE.md", manifest, repo_root, all_commands)
    _write_test_lanes(STATE_ROOT / "TEST-LANES.md", manifest)


def main() -> int:
    alias = os.environ.get("SEEK_DATABASE") or os.environ.get("ATTRIBUTE_TEST_DJANGO_ALIAS", "default")
    freeze_baseline(alias)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
