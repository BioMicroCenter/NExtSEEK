"""Drift test: every env var the runtime reads is declared in startup/env_registry.toml (#103).

Reads repo files by path with `ast` and `re` only: no Django, no `ci/` import. A failure
names the rule and lists each key with its `file:line`; fix it by adding or changing the
key's table in startup/env_registry.toml (schema: startup/lib/env_registry.py).

Scope (operator ruling): the runtime code below. scripts/, ci/, startup/ itself, the
Nessie CLI (NessieAI/chat_nextseek/cli.py) and tests are out of scope.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from startup.lib.env_registry import REGISTRY_PATH, load

ROOT = Path(__file__).resolve().parents[2]

RUNTIME_DIRS = (
    "dmac", "seek", "nextseek_api", "NessieAI/chat_nextseek/src", "NessieAI/cc", "NessieAI/ns",
    "NessieAI/router", "NessieAI/dmac_assistant", "NessieAI/schema_rag", "NessieAI/docker",
)
SKIP_DIRS = {"tests", "history", "node_modules", "__pycache__", "migrations"}
SETTINGS_FILE = ROOT / "dmac" / "settings.py"
SHELL_FILES = {"docker-compose.yml": ("root.env", "image"), "docker/scripts/entrypoint.sh": None}

# Template or example -> the registry `files` name it renders. None: checked one way only.
TEMPLATES = {
    "startup/templates/nextseek.env.template": "nextseek.env",
    "docker/nextseek.env.example": "nextseek.env",
    "startup/templates/db.env.template": "db.env",
    ".env.example": "root.env",
    "docker/laya.env.example": "laya.env",
    "NessieAI/docker/bedrock-proxy/proxy-secret.env.example": "proxy-secret.env",
    "NessieAI/chat_nextseek/.env.example": None,
}

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ENV_MAPPINGS = {"src", "source", "env", "environ"}
_ENV_HELPERS = {"_env_int", "_env_bool"}
_TEMPLATE_LINE = re.compile(r"^(#\s?)?([A-Z_][A-Z0-9_]*)\s*=")
_SHELL_REF = re.compile(r"(?<!\$)\$\{([A-Z_][A-Z0-9_]*)(?:(:?[-?])([^}]*))?\}")

# Names that look like a credential but hold none. Anything else matching _SECRET_NAME
# must be `secret = true`.
_SECRET_NAME = re.compile(r"PASSWORD|PASSSWORD|SECRET|TOKEN|_KEY$|_PASS(_|$)|CREDENTIAL|PRIVATE")
NOT_SECRET: frozenset[str] = frozenset()


def _runtime_files() -> list[Path]:
    out = []
    for top in RUNTIME_DIRS:
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT).parts
            if SKIP_DIRS & set(rel) or any(p.startswith(".") for p in rel) or path.name.startswith("test_"):
                continue
            out.append(path)
    return out


def _settings_names() -> set[str]:
    tree = ast.parse(SETTINGS_FILE.read_text())
    return {t.id for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}


def _is_os_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os") or (
        isinstance(node, ast.Name) and node.id == "environ")


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return str(node.value)
    return None


def scan_code() -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]], set[str]]:
    """Return ({key: [file:line]}, {key: {literal default: [file:line]}}, settings keys).

    # ponytail: a key built at run time (an f-string, a loop over a literal tuple as in
    # NessieAI/schema_rag/schema_processor.py, local_settings' _PROD_OVERRIDES loop) is
    # invisible here; declare it by hand with `dynamic = true`. Lower-case names are ignored.
    """
    assigned = _settings_names()
    reads: dict[str, list[str]] = defaultdict(list)
    defaults: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    settings_keys: set[str] = set()
    for path in _runtime_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        consts = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                consts[node.targets[0].id] = node.value.value

        def key_of(arg: ast.AST) -> str | None:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
            elif isinstance(arg, ast.Name) and arg.id in consts:
                value = consts[arg.id]
            else:
                return None
            return value if _KEY.match(value) else None

        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            key = default = None
            is_setting = False
            if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
                key = key_of(node.slice)
            elif isinstance(node, ast.Call) and node.args:
                func, args = node.func, node.args
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "getenv" and (isinstance(func, ast.Name) or (
                        isinstance(func.value, ast.Name) and func.value.id == "os")):
                    key, default = key_of(args[0]), args[1] if len(args) > 1 else None
                elif isinstance(func, ast.Attribute) and name in ("get", "setdefault", "pop") and (
                        _is_os_environ(func.value)
                        or (name == "get" and isinstance(func.value, ast.Name) and func.value.id in _ENV_MAPPINGS)):
                    key, default = key_of(args[0]), args[1] if len(args) > 1 else None
                elif name in _ENV_HELPERS:
                    for i, arg in enumerate(args):
                        key = key_of(arg)
                        if key:
                            default = args[i + 1] if len(args) > i + 1 else None
                            break
                elif (name == "getattr" and isinstance(func, ast.Name) and len(args) >= 2
                      and isinstance(args[0], ast.Name) and args[0].id == "settings"):
                    key = key_of(args[1])
                    if key in assigned:
                        key = None
                    default, is_setting = (args[2] if len(args) > 2 else None), True
            if not key:
                continue
            site = f"{rel}:{node.lineno}"
            reads[key].append(site)
            if is_setting:
                settings_keys.add(key)
            literal = _literal(default)
            if literal is not None:
                defaults[key][literal].append(site)
    return dict(reads), {k: dict(v) for k, v in defaults.items()}, settings_keys


def scan_shell() -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """`${KEY}` references in compose and the entrypoint: ({key: [file:line]}, {key: {default: [file:line]}})."""
    refs: dict[str, list[str]] = defaultdict(list)
    defaults: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for rel in SHELL_FILES:
        for lineno, line in enumerate((ROOT / rel).read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for match in _SHELL_REF.finditer(line):
                key, op, value = match.groups()
                refs[key].append(f"{rel}:{lineno}")
                if op in ("-", ":-"):
                    defaults[key][value].append(f"{rel}:{lineno}")
    return dict(refs), {k: dict(v) for k, v in defaults.items()}


def scan_templates() -> dict[str, list[tuple[str, int, bool]]]:
    """{template path: [(key, line, active)]}; `# KEY=` lines count, with active False."""
    out = {}
    for rel in TEMPLATES:
        rows = []
        for lineno, line in enumerate((ROOT / rel).read_text().splitlines(), 1):
            match = _TEMPLATE_LINE.match(line)
            if match:
                rows.append((match.group(2), lineno, match.group(1) is None))
        out[rel] = rows
    return out


def _fail(rule: str, problems: list[str]) -> None:
    assert not problems, (
        f"{rule}: {len(problems)} problem(s). Add or fix the key's table in startup/env_registry.toml "
        "(schema in startup/lib/env_registry.py):\n  " + "\n  ".join(sorted(problems)))


REGISTRY = load(REGISTRY_PATH)
CODE_READS, CODE_DEFAULTS, SETTINGS_KEYS = scan_code()
SHELL_REFS, SHELL_DEFAULTS = scan_shell()
TEMPLATE_KEYS = scan_templates()


def test_the_scanner_finds_known_reads() -> None:
    # Guards the scanner itself: one key per read shape it claims to follow.
    for key in ("DJANGO_SECRET_KEY",              # os.getenv
                "ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD",  # os.environ.get
                "DMAC_CC_MEMORY_WINDOW",          # _env_int(src, KEY, ...)
                "NEXTSEEK_GRAPH_SYNC_STUDY_LINKS",  # a module constant
                "CC_TRANSCRIPT_MAX_BYTES"):       # getattr(settings, KEY, ...)
        assert key in CODE_READS, key
    assert "CC_TRANSCRIPT_MAX_BYTES" in SETTINGS_KEYS
    assert "NEO4J_PASSWORD" in SHELL_REFS and "SEEK_PUMA_WORKERS" in SHELL_REFS


def test_rule1_code_reads_are_declared() -> None:
    _fail("Rule 1, code reads a key the registry does not declare",
          [f"{k}  read at {', '.join(v[:3])}" for k, v in CODE_READS.items() if k not in REGISTRY])


def test_rule1_settings_knobs_are_local_settings() -> None:
    _fail("Rule 1, a getattr(settings, KEY) knob must list local_settings in files",
          [k for k in SETTINGS_KEYS if k in REGISTRY and "local_settings" not in REGISTRY[k].files])


def test_rule2_compose_and_entrypoint_keys_are_declared() -> None:
    problems = []
    for key, sites in SHELL_REFS.items():
        entry = REGISTRY.get(key)
        if entry is None:
            problems.append(f"{key}  referenced at {', '.join(sites[:3])}")
            continue
        if any(s.startswith("docker-compose.yml") for s in sites) and not {"root.env", "image"} & set(entry.files):
            problems.append(f"{key}  is interpolated by compose, so files needs root.env or image")
    _fail("Rule 2, compose or the entrypoint reads an undeclared key", problems)


def test_rule3_template_keys_are_declared_for_that_file() -> None:
    problems = []
    for rel, rows in TEMPLATE_KEYS.items():
        target = TEMPLATES[rel]
        for key, lineno, _active in rows:
            entry = REGISTRY.get(key)
            if entry is None:
                problems.append(f"{key}  in {rel}:{lineno} has no entry")
            elif target is not None and target not in entry.files:
                problems.append(f"{key}  in {rel}:{lineno}: files lacks {target}")
    _fail("Rule 3, a template or example names a key the registry does not place there", problems)


def test_rule3_required_keys_are_in_every_template_of_their_files() -> None:
    problems = []
    for key, entry in REGISTRY.items():
        if not entry.required:
            continue
        for rel, target in TEMPLATES.items():
            if target in entry.files and (key, True) not in {(k, a) for k, _l, a in TEMPLATE_KEYS[rel]}:
                problems.append(f"{key}  is required but has no active line in {rel}")
    _fail("Rule 3, a required key is missing from a template or example", problems)


def test_rule4_literal_defaults_agree() -> None:
    problems = []
    for key in sorted(set(CODE_DEFAULTS) | set(SHELL_DEFAULTS)):
        seen: dict[str, list[str]] = defaultdict(list)
        for source in (CODE_DEFAULTS, SHELL_DEFAULTS):
            for value, sites in source.get(key, {}).items():
                seen[value] += sites
        entry = REGISTRY.get(key)
        if len(seen) > 1:
            problems.append(f"{key}  has different defaults: " + "; ".join(
                f"{v!r} at {', '.join(s[:2])}" for v, s in seen.items()))
        elif entry is not None and entry.default is not None and entry.default not in seen:
            value, sites = next(iter(seen.items()))
            problems.append(f"{key}  registry default {entry.default!r}, code {value!r} at {sites[0]}")
    _fail("Rule 4, literal defaults disagree", problems)


def test_rule5_no_orphan_entries() -> None:
    found = set(CODE_READS) | set(SHELL_REFS) | {k for rows in TEMPLATE_KEYS.values() for k, _l, _a in rows}
    _fail("Rule 5, a registry key nothing reads or sets (delete it, or mark it dynamic = true and name the reader)",
          [k for k, e in REGISTRY.items() if k not in found and not e.dynamic])


def test_rule5_dynamic_entries_name_their_reader() -> None:
    _fail("Rule 5, a dynamic entry's doc must name the reading file",
          [k for k, e in REGISTRY.items() if e.dynamic and not re.search(r"\w+\.(py|sh|yml)\b", e.doc)])


def test_rule6_credential_names_are_secret() -> None:
    _fail("Rule 6, a key named like a credential is not secret = true (or add it to NOT_SECRET with the reason)",
          [k for k, e in REGISTRY.items() if _SECRET_NAME.search(k) and not e.secret and k not in NOT_SECRET])


def test_docs_table_is_current() -> None:
    from startup.lib import env_registry
    text = (ROOT / "DEPLOYMENT.md").read_text()
    assert env_registry.BEGIN in text and env_registry.END in text, (
        "DEPLOYMENT.md section 8 lost its ENV-REGISTRY markers")
    assert env_registry.replace_block(text, REGISTRY) == text, (
        "The env table in DEPLOYMENT.md section 8 is out of date. Regenerate it from the repo root: "
        "uv run --project startup python -m startup.lib.env_registry --write")
