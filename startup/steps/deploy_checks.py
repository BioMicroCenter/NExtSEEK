"""Stack-health checks that show a rebuild is live, not only built.

The smoke suite tests the stack over HTTP, so it cannot see most of what an
image carries: the cc-agent's node and Claude Code, the Python packages the CC
agent charts with, the bedrock-proxy's model allow list, or whether the app
container runs the code the checkout holds. Each check here reads what the
running container or the built image carries and compares it with the tree the
images were built from (``checkout``). The expected value always comes from that
tree, never from a constant in this file, so a later pin bump needs no edit here,
and every failure names the value it expected and the rebuild that fixes it.

All four are advisory, like the rest of stack health (``startup/CLAUDE.md``):
none of them changes what the smoke suite requests, so a failure is printed,
recorded in the run record, and makes ``rebuild`` exit non-zero at the end.
None makes a model call. The one that starts a container
(``check_cc_agent_runtime``) replaces the image's entrypoint, so the agent never
runs, and gives it no network and no mounts.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path

from startup.lib.docker_ops import DockerOpsError, compose_exec, image_exists
from startup.steps.validate import HealthResult, _cc_agent_image

# The files the expected values are read from, repo-relative. Pinned to the real
# tree by startup/tests/test_deploy_checks.py.
CC_RUNTIME_DOCKERFILE = Path("NessieAI") / "docker" / "cc-runtime" / "Dockerfile"
CC_RUNTIME_PYPROJECT = Path("NessieAI") / "docker" / "cc-runtime" / "pyproject.toml"
PROXY_CONFIG = Path("NessieAI") / "docker" / "bedrock-proxy" / "app" / "config.py"
MODEL_CLASS_MAP = (Path("NessieAI") / "dmac_assistant" / "build_context"
                   / "router_model_class_map.json")

PROXY_SERVICE = "bedrock-proxy"
APP_SERVICE = "nextseek"

# The `pip install` name of a container-extra package whose import is also
# exercised, and what is imported. matplotlib is imported with the Agg backend,
# the one a headless container has: an installed but unimportable chart library
# is what the 2026-09-25 dev run could not tell from an absent one.
IMPORT_PROBES = {"matplotlib": "matplotlib.pyplot"}

CC_RUNTIME_TIMEOUT_S = 180

_REBUILD_CC_AGENT = "./startup.sh rebuild --component cc-agent"
_REBUILD_PROXY = "./startup.sh rebuild --component bedrock-proxy"
_REBUILD_APP = "./startup.sh rebuild"


# --------------------------------------------------------------------------- #
# what the checkout says the images carry
# --------------------------------------------------------------------------- #

_NODE_FROM = re.compile(r"^FROM\s+(?:--\S+\s+)*node:(\d+)\b", re.M)
_CLAUDE_CODE_PIN = re.compile(r"@anthropic-ai/claude-code@([0-9][0-9A-Za-z.+-]*)")
_DIST_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def expected_cc_runtime(checkout: Path) -> dict:
    """The node major, the Claude Code version and the ``container`` extra's
    distributions the checkout's cc-runtime build installs. A value the files do
    not state is None (or an empty list), and is then not checked."""
    dockerfile = (checkout / CC_RUNTIME_DOCKERFILE).read_text(encoding="utf-8")
    node = _NODE_FROM.search(dockerfile)
    claude = _CLAUDE_CODE_PIN.search(dockerfile)
    pyproject = tomllib.loads((checkout / CC_RUNTIME_PYPROJECT).read_text(encoding="utf-8"))
    extra = (pyproject.get("project", {}).get("optional-dependencies", {})
             .get("container", []))
    dists = [m.group(1) for m in (_DIST_NAME.match(spec) for spec in extra) if m]
    return {
        "node_major": int(node.group(1)) if node else None,
        "claude_code": claude.group(1) if claude else None,
        "dists": dists,
    }


def expected_proxy_allow_list(checkout: Path) -> tuple[str, ...]:
    """``_DEFAULT_ALLOWED_MODELS`` in the checkout's proxy config, read with ast:
    the proxy directory is hyphenated and is not importable."""
    tree = ast.parse((checkout / PROXY_CONFIG).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "_DEFAULT_ALLOWED_MODELS":
            return tuple(ast.literal_eval(node.value))
    raise ValueError(f"{PROXY_CONFIG} defines no _DEFAULT_ALLOWED_MODELS")


def model_class_map(checkout: Path) -> dict[str, str]:
    return json.loads((checkout / MODEL_CLASS_MAP).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# cc-agent: node, Claude Code, the container extra
# --------------------------------------------------------------------------- #

# Run by the image's own Python (the venv the container extra is installed into),
# with the distributions to look up as argv. One JSON line, prefixed, so a
# warning another tool prints cannot be mistaken for it.
CC_RUNTIME_PROBE = r"""
import importlib, importlib.metadata as md, json, subprocess, sys

def run(*argv):
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=60)
    except Exception as exc:
        return "error: %s: %s" % (type(exc).__name__, exc)
    return ((p.stdout or "") + (p.stderr or "")).strip()

imports = json.loads(sys.argv[1])
out = {"node": run("node", "--version"), "claude": run("claude", "--version"),
       "dists": {}, "imports": {}}
for dist in sys.argv[2:]:
    try:
        out["dists"][dist] = md.version(dist)
    except md.PackageNotFoundError:
        out["dists"][dist] = None
for module in imports:
    try:
        if module.startswith("matplotlib"):
            import matplotlib
            matplotlib.use("Agg")
        importlib.import_module(module)
        out["imports"][module] = "ok"
    except Exception as exc:
        out["imports"][module] = "%s: %s" % (type(exc).__name__, exc)
print("CC-RUNTIME " + json.dumps(out))
"""


def _marked_json(text: str, marker: str) -> dict | list | None:
    """The JSON after the last line starting with ``marker``, or None."""
    for line in reversed(text.splitlines()):
        if line.startswith(marker + " "):
            try:
                return json.loads(line[len(marker) + 1:])
            except ValueError:
                return None
    return None


def cc_runtime_problems(expected: dict, seen: dict) -> list[str]:
    """Each way the image's runtime is not what the checkout builds, in words."""
    problems: list[str] = []
    want_node = expected.get("node_major")
    if want_node is not None:
        node = str(seen.get("node") or "")
        m = re.match(r"v(\d+)\.", node)
        if not m:
            problems.append(f"node did not report a version ({node[:80]!r}); the "
                            f"checkout builds FROM node:{want_node}")
        elif int(m.group(1)) != want_node:
            problems.append(f"node {node} where the checkout builds FROM node:{want_node}")
    want_cc = expected.get("claude_code")
    if want_cc:
        claude = str(seen.get("claude") or "")
        if claude.split(" ", 1)[0] != want_cc:
            problems.append(f"Claude Code {claude[:80]!r} where the checkout pins "
                            f"@anthropic-ai/claude-code@{want_cc}")
    dists = seen.get("dists") or {}
    for dist in expected.get("dists") or []:
        if not dists.get(dist):
            problems.append(f"{dist} is not installed, but the checkout's container "
                            "extra lists it")
    imports = seen.get("imports") or {}
    for dist, module in IMPORT_PROBES.items():
        if dist in (expected.get("dists") or []) and dists.get(dist):
            if imports.get(module) != "ok":
                problems.append(f"{module} does not import: {imports.get(module)}")
    return problems


def check_cc_agent_runtime(checkout: Path, compose_project_name: str = "nextseek") -> HealthResult:
    """Whether the cc-agent image carries the node, Claude Code and container
    extra that the checkout's cc-runtime Dockerfile and pyproject install.

    The CC 503 fallback needs Claude Code 2.1.282 or later, which needs node 22,
    and the agent's charts need matplotlib; the smoke suite never starts a CC
    turn, so without this a skipped ``--component cc-agent`` leaves every one of
    them on the old image under a green run. The container runs the image's
    Python with the entrypoint replaced (the agent never starts), ``--network
    none``, no mounts, and is removed on exit.
    """
    name = "cc-agent runtime"
    image = _cc_agent_image(compose_project_name)
    try:
        expected = expected_cc_runtime(checkout)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return HealthResult(name=name, ok=True, warn=True,
                            detail=f"skipped: cannot read the cc-runtime pins from the "
                                   f"checkout ({exc})")
    try:
        present = image_exists(image)
    except (DockerOpsError, OSError) as exc:
        return HealthResult(name=name, ok=False, detail=str(exc))
    if not present:
        return HealthResult(name=name, ok=True, warn=True,
                            detail=(f"skipped: {image} is absent (first-party images "
                                    "says how to build it)"))
    probes = [module for dist, module in IMPORT_PROBES.items() if dist in expected["dists"]]
    cmd = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
           image, "-c", CC_RUNTIME_PROBE, json.dumps(probes), *expected["dists"]]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=CC_RUNTIME_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return HealthResult(name=name, ok=False,
                            detail=f"{image} did not answer within {CC_RUNTIME_TIMEOUT_S}s")
    except OSError as exc:
        return HealthResult(name=name, ok=False, detail=f"cannot run docker: {exc}")
    seen = _marked_json(result.stdout or "", "CC-RUNTIME")
    if not isinstance(seen, dict):
        tail = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
        return HealthResult(name=name, ok=False,
                            detail=(f"{image} gave no runtime report (exit {result.returncode}): "
                                    f"{tail[-1][:200] if tail else 'no output'}"))
    problems = cc_runtime_problems(expected, seen)
    if problems:
        return HealthResult(name=name, ok=False,
                            detail=(f"STALE: {image}: {'; '.join(problems)}. Rebuild with: "
                                    f"{_REBUILD_CC_AGENT}"))
    extras = ", ".join(f"{d} {(seen.get('dists') or {}).get(d)}" for d in expected["dists"])
    return HealthResult(
        name=name, ok=True,
        detail=(f"{image}: node {seen.get('node')}, Claude Code "
                f"{str(seen.get('claude')).split(' ', 1)[0]}, {extras}"
                + (f"; {', '.join(probes)} imports" if probes else "")),
    )


# --------------------------------------------------------------------------- #
# bedrock-proxy allow list, and the models the app's CC turn names
# --------------------------------------------------------------------------- #

PROXY_PROBE = ("import json\n"
               "from app.config import ProxyConfig\n"
               "print('PROXY-ALLOW ' + json.dumps(list(ProxyConfig.from_env().allowed_models)))\n")

# What a Container-CC turn would be started with, built by the running app's own
# code with no user, no session and no model call: the command's --model and
# --fallback-model, and the three agent env keys the CC 503 fallback added. Only
# those keys are printed; the environment's other values never leave the app.
CC_WIRING_PROBE = (
    "import json\n"
    "from NessieAI.cc import cc_engine\n"
    "from dmac_assistant.router import models\n"
    "cmd = cc_engine._build_command(model_id=models.resolve_cc_model())\n"
    "env = cc_engine.build_agent_environment(api_user=None, api_pass=None, path_mappings={})\n"
    "def flag(n):\n"
    "    return cmd[cmd.index(n) + 1] if n in cmd else None\n"
    "keys = ('CLAUDE_CODE_MAX_RETRIES', 'API_TIMEOUT_MS', 'ANTHROPIC_DEFAULT_SONNET_MODEL')\n"
    "print('CC-WIRING ' + json.dumps({'model': flag('--model'),"
    " 'fallback_model': flag('--fallback-model'), 'env': {k: env.get(k) for k in keys}}))\n"
)

# The app image has no bare `python` on PATH (check_cc_runner).
_APP_PYTHON = ("uv", "run", "--no-sync", "python")


def running_proxy_allow_list(repo_root: Path, env: dict[str, str]) -> list[str]:
    """The allow list the RUNNING bedrock-proxy enforces. Raises DockerOpsError."""
    out = compose_exec(service=PROXY_SERVICE, command=["python", "-c", PROXY_PROBE],
                       project_dir=repo_root, env=env)
    seen = _marked_json(out, "PROXY-ALLOW")
    if not isinstance(seen, list):
        raise DockerOpsError(f"the running {PROXY_SERVICE} printed no allow list")
    return [str(m) for m in seen]


def running_cc_wiring(repo_root: Path, env: dict[str, str]) -> dict:
    """What the running app would start a CC turn with. Raises DockerOpsError."""
    out = compose_exec(service=APP_SERVICE, command=[*_APP_PYTHON, "-c", CC_WIRING_PROBE],
                       project_dir=repo_root, env=env)
    seen = _marked_json(out, "CC-WIRING")
    if not isinstance(seen, dict):
        raise DockerOpsError(f"the {APP_SERVICE} container printed no CC command")
    return seen


def proxy_result(expected: tuple[str, ...], running: list[str] | None,
                 error: str | None = None) -> HealthResult:
    name = "bedrock-proxy allow list"
    if running is None:
        return HealthResult(name=name, ok=False,
                            detail=f"could not read it from the running {PROXY_SERVICE}: {error}")
    missing = [m for m in expected if m not in running]
    extra = [m for m in running if m not in expected]
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"not in the checkout: {', '.join(extra)}")
        return HealthResult(
            name=name, ok=False,
            detail=(f"STALE: the running {PROXY_SERVICE} allows {', '.join(running) or 'nothing'} "
                    f"({'; '.join(parts)}); the checkout's app/config.py allows "
                    f"{', '.join(expected)}. A model it refuses is a 403, and Claude Code "
                    f"never falls back on a 403. Rebuild with: {_REBUILD_PROXY}"),
        )
    return HealthResult(name=name, ok=True,
                        detail=f"the running {PROXY_SERVICE} allows exactly the checkout's "
                               f"{len(expected)}: {', '.join(expected)}")


def cc_wiring_result(model_map: dict[str, str], wiring: dict | None,
                     allowed: list[str] | None, error: str | None = None) -> HealthResult:
    """The app's CC command names the checkout's models, carries the fallback's
    env when the checkout declares a fallback, and asks for nothing the running
    proxy refuses."""
    name = "CC fallback wiring"
    if wiring is None:
        return HealthResult(name=name, ok=False,
                            detail=f"could not build a CC command in the running {APP_SERVICE}: {error}")
    problems: list[str] = []
    model = wiring.get("model")
    fallback = wiring.get("fallback_model")
    agent_env = wiring.get("env") or {}
    classifier = agent_env.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
    want_model = model_map.get("opus")
    want_fallback = model_map.get("opus_fallback")
    if want_model and model != want_model:
        problems.append(f"--model is {model!r} where the checkout's map says {want_model}")
    if want_fallback:
        if fallback != want_fallback:
            problems.append(f"--fallback-model is {fallback!r} where the checkout's map "
                            f"says {want_fallback}")
        for key in ("CLAUDE_CODE_MAX_RETRIES", "API_TIMEOUT_MS", "ANTHROPIC_DEFAULT_SONNET_MODEL"):
            if not agent_env.get(key):
                problems.append(f"the agent env has no {key}")
    if problems:
        return HealthResult(
            name=name, ok=False,
            detail=(f"STALE: the running {APP_SERVICE} builds a CC turn unlike the checkout: "
                    f"{'; '.join(problems)}. Rebuild with: {_REBUILD_APP}"),
        )
    named = [m for m in (model, fallback, classifier) if m]
    if allowed is not None:
        refused = [m for m in named if m not in allowed]
        if refused:
            return HealthResult(
                name=name, ok=False,
                detail=(f"a CC turn names {', '.join(refused)}, which the running "
                        f"{PROXY_SERVICE} refuses (it allows {', '.join(allowed)}). Rebuild "
                        f"the proxy with or before the app: {_REBUILD_PROXY}"),
            )
    return HealthResult(
        name=name, ok=True,
        detail=(f"--model {model}"
                + (f", --fallback-model {fallback}" if fallback else ", no fallback model")
                + (f", classifier {classifier}" if classifier else "")
                + (f", retries {agent_env.get('CLAUDE_CODE_MAX_RETRIES')}, request timeout "
                   f"{agent_env.get('API_TIMEOUT_MS')} ms" if agent_env.get('API_TIMEOUT_MS') else "")
                + ("; every one allowed by the running proxy" if allowed is not None else "")),
    )


def check_cc_models(repo_root: Path, env: dict[str, str], checkout: Path) -> tuple[HealthResult, HealthResult]:
    """The running proxy's allow list against the checkout's, then the app's CC
    command against the checkout's model map and the running proxy."""
    try:
        expected = expected_proxy_allow_list(checkout)
        model_map = model_class_map(checkout)
    except (OSError, ValueError) as exc:
        skipped = HealthResult(name="bedrock-proxy allow list", ok=True, warn=True,
                               detail=f"skipped: cannot read the checkout's model lists ({exc})")
        return skipped, HealthResult(name="CC fallback wiring", ok=True, warn=True,
                                     detail="skipped: see bedrock-proxy allow list")
    try:
        allowed: list[str] | None = running_proxy_allow_list(repo_root, env)
        proxy_error = None
    except (DockerOpsError, OSError) as exc:
        allowed, proxy_error = None, str(exc)
    try:
        wiring: dict | None = running_cc_wiring(repo_root, env)
        wiring_error = None
    except (DockerOpsError, OSError) as exc:
        wiring, wiring_error = None, str(exc)
    return (proxy_result(expected, allowed, proxy_error),
            cc_wiring_result(model_map, wiring, allowed, wiring_error))


# --------------------------------------------------------------------------- #
# the app container runs the checkout's code
# --------------------------------------------------------------------------- #

# What the app image is built from (`COPY . /app/`) and runs. Tracked files only.
APP_CODE_ROOTS = ("dmac", "seek", "nextseek_api", "api_app", "templates", "NessieAI",
                  "static/js/chat_assistant", "manage.py", "gunicorn.conf.py")
# Tracked paths the image does not carry (.dockerignore) or rewrites at run time:
# the chat_nextseek context files are refreshed from the database inside the
# container, and the cc-agent context check already compares them.
APP_CODE_EXCLUDED = (
    "NessieAI/history/",
    "NessieAI/chat_nextseek/old/",
    "NessieAI/chat_nextseek/.python-version",
    "NessieAI/chat_nextseek/src/chat_nextseek/context/",
)
APP_CODE_EXCLUDED_PARTS = (".claude", "node_modules", "__pycache__")
APP_CODE_SHOWN = 6

APP_CODE_PROBE = (
    "import hashlib, json, os, sys\n"
    "want = json.load(sys.stdin)\n"
    "differs, absent = [], []\n"
    "for path, digest in want.items():\n"
    "    try:\n"
    "        with open(os.path.join('/app', path), 'rb') as f:\n"
    "            got = hashlib.sha256(f.read()).hexdigest()\n"
    "    except OSError:\n"
    "        absent.append(path)\n"
    "        continue\n"
    "    if got != digest:\n"
    "        differs.append(path)\n"
    "print('APP-CODE ' + json.dumps({'checked': len(want), 'differs': differs, 'absent': absent}))\n"
)


def app_code_paths(checkout: Path) -> list[str]:
    """The tracked files under APP_CODE_ROOTS, minus what the image never holds."""
    result = subprocess.run(["git", "-C", str(checkout), "ls-files", "-z", "--", *APP_CODE_ROOTS],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise OSError(f"git ls-files failed: {result.stderr.strip()}")
    paths = []
    for path in result.stdout.split("\0"):
        if not path or path.startswith(APP_CODE_EXCLUDED):
            continue
        if any(part in APP_CODE_EXCLUDED_PARTS for part in path.split("/")):
            continue
        paths.append(path)
    return paths


def app_code_digests(checkout: Path, paths: list[str]) -> dict[str, str]:
    digests = {}
    for path in paths:
        file = checkout / path
        if file.is_file():
            digests[path] = hashlib.sha256(file.read_bytes()).hexdigest()
    return digests


def app_code_result(report: dict) -> HealthResult:
    name = "app image code"
    checked = report.get("checked", 0)
    differs = list(report.get("differs") or [])
    absent = list(report.get("absent") or [])
    if not differs and not absent:
        return HealthResult(name=name, ok=True,
                            detail=f"the running {APP_SERVICE} holds all {checked} tracked app "
                                   "files as the checkout has them")
    parts = []
    for label, paths in (("differ", differs), ("absent", absent)):
        if paths:
            shown = ", ".join(paths[:APP_CODE_SHOWN])
            more = f" and {len(paths) - APP_CODE_SHOWN} more" if len(paths) > APP_CODE_SHOWN else ""
            parts.append(f"{len(paths)} {label} ({shown}{more})")
    return HealthResult(
        name=name, ok=False,
        detail=(f"STALE: of {checked} tracked app files, {'; '.join(parts)} in the running "
                f"{APP_SERVICE}. It is not running the checkout's code. Rebuild with: "
                f"{_REBUILD_APP}"),
    )


def check_app_code(repo_root: Path, env: dict[str, str], checkout: Path) -> HealthResult:
    """Whether the running app container holds the checkout's tracked code.

    A green smoke run says the site answers; it does not say the answer came
    from this checkout. An app rebuild that failed quietly, a restart that was
    deferred, or a pull after the build leaves the old code running under a
    green suite, and every behaviour change in the pull (the NS fallback ladder,
    a turn's cost, the graph reviewer, a template fix) is then not live. This
    hashes each tracked file under APP_CODE_ROOTS in the checkout and in the
    running container and names the ones that differ.
    """
    name = "app image code"
    try:
        digests = app_code_digests(checkout, app_code_paths(checkout))
    except OSError as exc:
        return HealthResult(name=name, ok=True, warn=True,
                            detail=f"skipped: cannot list the checkout's files ({exc})")
    try:
        out = compose_exec(service=APP_SERVICE, command=[*_APP_PYTHON, "-c", APP_CODE_PROBE],
                           project_dir=repo_root, env=env,
                           stdin=json.dumps(digests).encode("utf-8"))
    except (DockerOpsError, OSError) as exc:
        return HealthResult(name=name, ok=False, detail=str(exc))
    report = _marked_json(out, "APP-CODE")
    if not isinstance(report, dict):
        return HealthResult(name=name, ok=False,
                            detail=f"the {APP_SERVICE} container printed no report")
    return app_code_result(report)


def deploy_checks(repo_root: Path, env: dict[str, str], compose_project_name: str,
                  checkout: Path) -> tuple[HealthResult, ...]:
    """Every check above, in the order stack health prints them."""
    proxy, wiring = check_cc_models(repo_root, env, checkout)
    return (
        check_app_code(repo_root, env, checkout),
        check_cc_agent_runtime(checkout, compose_project_name),
        proxy,
        wiring,
    )
