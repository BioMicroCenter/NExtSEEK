"""The launch rules, as data. Every table the old SKILL.md held as prose lives here.

launch.py reads these tables; nothing else decides what is known, what stops a launch,
or which image a changed path needs. Change a rule here, add a test in
tests/test_launch.py, and the next launch follows it. Dated facts carry the date they
were last checked so a stale one is visible.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------------
# Components, in the order a runner rebuilds them. bedrock-proxy goes first (with or
# before app and cc-agent): a new app behind an old proxy names models the proxy
# refuses (CI-COVERAGE.md 2026-09-25, "CC fallback wiring").
# ---------------------------------------------------------------------------------
COMPONENTS = ("bedrock-proxy", "app", "cc-agent", "nextseek-sidecar")

# The log file each component's rebuild writes on the box, and the line that proves
# it worked (startup/cli.py: `{name} rebuilt and restarted`, `{name} image rebuilt`).
LOG_NAME = {"bedrock-proxy": "proxy", "app": "app", "cc-agent": "cc", "nextseek-sidecar": "sidecar"}
SUCCESS_LINE = {
    "bedrock-proxy": "bedrock-proxy rebuilt and restarted",
    "app": "app rebuilt and restarted",
    "cc-agent": "cc-agent image rebuilt",
    "nextseek-sidecar": "nextseek-sidecar rebuilt and restarted",
}

# Stack health runs after every rebuild and again at the top of CI. A red line that
# names a component still waiting to be rebuilt is expected mid-launch; after the last
# rebuild every one of these must be green. Name prefix -> the component that fixes it.
HEALTH_NEEDS = (
    ("app image code", "app"),
    ("CC fallback wiring", "app"),
    ("cc-agent runtime", "cc-agent"),
    ("cc-agent context", "cc-agent"),
    ("bedrock-proxy allow list", "bedrock-proxy"),
    ("model ids reachable", "app"),   # test/model-reachability (2026-09-28): the app probes, then the proxy
)

# Lines drawn with ✗ that are not stack-health checks.
NOT_HEALTH = ("CI failed", "CI passed", "stopped before building")

# The rebuild's closing line when the build and restart worked and only health lines are red
# (startup/cli.py). It is no stop of its own: it stops only beside a red the judge cannot explain
# (unexplained or stale). Rich drops the space where it wraps a line, so each space is optional.
SUMMARY_RED = r"^Rebuild finished but is red: .*No ?rollback ?is ?needed"


@dataclass(frozen=True)
class KnownRed:
    """A red that is known and needs no action on one instance."""

    kind: str            # "health" (a ✗ line) or "ci" (a FAILED/ERROR test id)
    match: str           # POSIX ERE (also valid Python re): the runner's grep -E and judge use it
    why: str
    checked: str         # date the fact was last true


@dataclass(frozen=True)
class Window:
    start_utc: tuple[int, int]
    end_utc: tuple[int, int]
    why: str
    blocks: str          # "stop": no heavy step may overlap it; "warn": say so


# ---------------------------------------------------------------------------------
# The local box config. Everything that names a real machine, account or path lives in
# one JSON file outside the repo (see references/boxes.md and boxes.example.json).
# ---------------------------------------------------------------------------------
BOXES_DEFAULT = "~/.config/nextseek/boxes.json"
BOXES_ENV = "NEXTSEEK_BOXES"
_BOX_KEYS = {
    "dev": ("box", "ssh_host", "transport", "run_as", "home", "in_dir", "repo"),
    "prod": ("box", "ssh_host", "transport", "run_as", "home", "in_dir", "repo"),
    "local": ("box", "repo"),
}


class BoxesConfigError(Exception):
    pass


def boxes_path() -> Path:
    return Path(os.environ.get(BOXES_ENV) or BOXES_DEFAULT).expanduser()


def load_boxes() -> dict:
    p = boxes_path()
    hint = (f"copy .claude/skills/deploy/boxes.example.json to {BOXES_DEFAULT} and fill it in "
            f"(or point {BOXES_ENV} at another file); fields: references/boxes.md")
    if not p.is_file():
        raise BoxesConfigError(f"box config missing: {p}. {hint}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise BoxesConfigError(f"box config {p} is not valid JSON: {e}") from None
    for top in ("reports_dir", "workstation_repo", "instances"):
        if top not in data:
            raise BoxesConfigError(f"box config {p}: missing key '{top}'. {hint}")
    return data


def reports_root() -> Path:
    return Path(load_boxes()["reports_dir"]).expanduser()


def workstation_repo() -> Path:
    return Path(load_boxes()["workstation_repo"]).expanduser()


def _host_fields(name: str) -> dict:
    data = load_boxes()
    inst = data["instances"].get(name)
    if inst is None:
        raise BoxesConfigError(f"box config {boxes_path()}: no instance '{name}' under 'instances'")
    missing = [k for k in _BOX_KEYS[name] if not inst.get(k)]
    if missing:
        raise BoxesConfigError(f"box config {boxes_path()}: instance '{name}' lacks {', '.join(missing)}")
    return {k: inst[k] for k in _BOX_KEYS[name]}


@dataclass(frozen=True)
class Instance:
    name: str
    box: str                       # what the operator calls it
    ssh_host: str
    transport: str                 # "sudo" (dev) or "direct" (prod) or "none" (local)
    in_dir: str                    # where copied files land on the box
    repo: str
    run_as: str                    # account that owns the stack on the box
    home: str                      # that account's home on the box
    disk_floor_gb: int
    memory_floor_gib: int
    seek_restart_default: bool     # SEEK restart before Nessie allowed without asking
    labs_copy: bool                # the runner re-copies /tmp/labs_db.json after an app rebuild
    known_dirty: tuple[str, ...]   # `git status --short` lines that are safe
    discard_before_pull: tuple[str, ...]  # the only paths the runner may reset
    known_reds: tuple[KnownRed, ...] = ()
    windows: tuple[Window, ...] = ()
    nessie_allowed_without_flag: bool = True
    rebuild_allowed: bool = True
    ci_allowed: bool = True


_CONTEXT = "NessieAI/chat_nextseek/src/chat_nextseek/context"
_REFRESH = tuple(f" M {_CONTEXT}/{f}" for f in (
    "assays_db.json", "min_assays_db.json", "min_sampletypes_db.json", "projects_db.json",
    "sampletypes_db.json")) + (f"?? {_CONTEXT}/.context_db_refresh",)

# Drift checks a box that is not production may fail (operator ruling OP14, 2026-10-02). The same set
# as OFF_PROD_ALLOWED_DRIFT in ci/smoke/test_graph_sync_status.py; a test pins the two together.
OFF_PROD_ALLOWED_DRIFT = frozenset({"catalog.assistant_investigations"})


def _graph_sync_drift_only() -> str:
    """The 'graph sync health' red whose ONLY problem is a drift run that failed only the allowed checks.

    The line (startup/steps/validate.py) is the summary (nextseek_api/graph_sync/health.py summary()), then one
    detail line per problem in the order: older body, stale jobs, dead rows, overdue rows, overdue runs, drift.
    Drift comes last, so the drift line follows the summary directly only when no other problem exists, and
    its names end at the '.' of the remedy, so another failed check breaks the match. Warnings may follow."""
    names = "(" + "|".join(re.escape(n) for n in sorted(OFF_PROD_ALLOWED_DRIFT)) + ")"
    p = (r"^graph sync health: failing outbox rows: [0-9]+ \(0 overdue\); failed runs: [0-9]+ \(0 overdue\); "
         r"latest drift check: run [0-9]+ drift drift run [0-9]+ \(finished [^)]*\) found drift in: "
         + names + "(, " + names + r")*\. It stays red")
    return p.replace(" ", " ?")   # rich drops the space where it wraps a line


DEV_SEEK_REDS = (
    KnownRed("ci", r"^ci/smoke/test_reachability\.py::test_route_is_reachable\[/seek/sample_types/id=",
             "SEEK SampleTypesController#show spends about 22 s in the database, past the 20 s client "
             "timeout (D14)", "2026-09-25"),
    KnownRed("ci", r"^ci/smoke/test_reachability\.py::test_route_is_reachable\[/nextseek_api/sample_types/",
             "same as the SEEK sample_types red (D14)", "2026-09-25"),
)

_INSTANCE_RULES = {
    "dev": dict(
        disk_floor_gb=20, memory_floor_gib=10, seek_restart_default=True, labs_copy=True,
        known_dirty=("?? logs/", "?? startup/.ci-write-run.xml") + _REFRESH,
        discard_before_pull=(f"{_CONTEXT}/",),
        known_reds=DEV_SEEK_REDS + (
            KnownRed("health", r"^graph drift: DRIFT: 1 of [0-9]+ checks failed: ?catalog\.assistant_investigations",
                     "dev lacks some investigations; this alone makes the rebuild exit 1", "2026-09-25"),
            KnownRed("health", r"^no usable GHCR credential",
                     "no ~/.config/nextseek/ghcr.env on dev (issue #87); harmless", "2026-09-25"),
            KnownRed("health", _graph_sync_drift_only(),
                     "graph sync health is red only because the latest drift run failed the checks allowed off "
                     "production (OP14: leave it red on dev); any other problem on that line still stops",
                     "2026-10-05"),
        ),
        windows=(
            Window((4, 0), (12, 0), "the nightly mariadb-dump hangs on a stuck NFS mount and holds "
                   "LOCK TABLES READ on every dmac table until wait_timeout drops it (blocks logins, "
                   "chat writes, CI)", "stop"),
            Window((1, 45), (2, 45), "the nightly graph sync runs about 02:00Z; a rebuild kills it",
                   "stop"),
        ),
    ),
    "prod": dict(
        disk_floor_gb=30, memory_floor_gib=10, seek_restart_default=False, labs_copy=False,
        known_dirty=(" M startup/seed/neo4j.cypher.gz", " M startup/seed/seek_production.sql.gz",
                     "?? attributes_error.txt", "?? dmac/local_settings.py.bk", "?? test.txt",
                     "?? logs/") + _REFRESH,
        discard_before_pull=(f"{_CONTEXT}/",),
        known_reds=(
            KnownRed("health", r"^no usable GHCR credential", "no GHCR credential file", "2026-09-23"),
        ),
        nessie_allowed_without_flag=False,
    ),
    "local": dict(
        disk_floor_gb=20, memory_floor_gib=4, seek_restart_default=False, labs_copy=False,
        known_dirty=(), discard_before_pull=(),
        rebuild_allowed=False, ci_allowed=False,   # operator ruling 2026-09-24 (OOM)
    ),
}


class _Instances(dict):
    """INSTANCES[name]: the rules above joined with the host fields from the box config.

    Read at first use, so a missing config fails with a message naming the file, and only for
    the instance a command actually needs."""

    def __missing__(self, name):
        if name not in _INSTANCE_RULES:
            raise KeyError(name)
        hosts = _host_fields(name)
        if name == "local":
            hosts.update(ssh_host="", transport="none", in_dir="", run_as="", home="")
        inst = Instance(name=name, **hosts, **_INSTANCE_RULES[name])
        self[name] = inst
        return inst


INSTANCES = _Instances()

# ---------------------------------------------------------------------------------
# Which images a changed path needs (DEPLOYMENT.md section 3.2; dev.md "Which images").
# First match wins per path. `flag` marks a path that needs the brief to name it.
# ---------------------------------------------------------------------------------
# startup/lib/layout.py CANONICAL_CONTEXT_FILES (checked 2026-09-25 at 1c070c08).
_SIX_CONTEXT = tuple(f"{_CONTEXT}/{f}" for f in (
    "capabilities.md", "min_api_endpoints.json", "min_api_endpoints_enriched.json",
    "min_assays_db.json", "min_sampletypes_db.json", "projects_db.json"))


@dataclass(frozen=True)
class PathRule:
    pattern: str                 # regex over the repo-relative path
    images: tuple[str, ...]
    flag: str | None = None      # nginx | compose | migration | seed | out_of_scope
    note: str = ""


IMAGE_RULES: tuple[PathRule, ...] = (
    PathRule(r"^docker/nginx\.conf$", (), "nginx",
             "single-file bind mount: needs an nginx --no-deps --force-recreate, only if allowed_extras says so"),
    PathRule(r"^docker-compose\.yml$", (), "compose", "a cap or env change may need a --no-deps recreate"),
    PathRule(r"^startup/seed/", (), "seed", "the dirty production dumps on prod would collide"),
    PathRule(r"(^|/)migrations/\d[^/]*\.py$", ("app",), "migration", "migrate runs at boot"),
    PathRule("^(" + "|".join(re.escape(p) for p in _SIX_CONTEXT) + ")$", ("app", "cc-agent"), None,
             "a canonical context file: skip the cc-agent and every 'cc-agent context' check fails"),
    PathRule(r"^NessieAI/dmac_assistant/baml_src/", ("app", "cc-agent")),
    PathRule(r"^NessieAI/docker/cc-runtime/", ("cc-agent",)),
    # Only what each Dockerfile copies; a README or PORT-EVIDENCE.json beside it builds nothing.
    PathRule(r"^NessieAI/docker/ns-sidecar/(Dockerfile|__init__\.py|app/)", ("nextseek-sidecar",)),
    PathRule(r"^NessieAI/docker/bedrock-proxy/(Dockerfile|app/)", ("bedrock-proxy",)),
    PathRule(r"^(static/|NessieAI/chat_frontend/)", ("app",), None, "then collectstatic"),
    PathRule(r"^themes/NextSeek/", (), None, "bind-mounted: live after the pull; static needs collectstatic"),
    PathRule(r"^(ci|startup)/", (), None, "CI and the CLI run from the host checkout"),
    PathRule(r"\.(md|txt)$", (), None, "documentation"),
    PathRule(r"^NessieAI/tests/", ("app",), None, "baked into the app image (the harness runs from /app)"),
    PathRule(r"^(nextseek_api|seek|dmac|NessieAI)/", ("app",)),
    PathRule(r"^(pyproject\.toml|uv\.lock|Dockerfile|manage\.py|gunicorn\.conf\.py)$", ("app",)),
)


def rule_for(path: str) -> PathRule | None:
    for rule in IMAGE_RULES:
        if re.search(rule.pattern, path):
            return rule
    return None


# ---------------------------------------------------------------------------------
# Brief vocabulary.
# ---------------------------------------------------------------------------------
# Extras a brief may allow. Anything outside the list is a proposal in the report.
ALLOWED_EXTRAS = {
    "seek restart ok": "restart SEEK before Nessie when it holds more than 12 GiB (a minute of errors on prod)",
    "nginx recreate ok": "docker compose up -d --no-deps --force-recreate nextseek_nginx",
    "db backup ok": "prod database backup before a migration (the operator's prod runbook, backup step)",
    "bedrock-proxy rebuild ok": "rebuild the bedrock-proxy component",
    "compose recreate ok": "a --no-deps recreate after a docker-compose.yml change",
}

# Families and id prefixes never run on production (they write or launch).
PROD_FORBIDDEN_FAMILIES = ("entity_write", "pipeline_launch", "pipeline_output_reingest",
                           "batch_upload_preparation")
PROD_FORBIDDEN_ID_PREFIXES = ("write.",)

# ---------------------------------------------------------------------------------
# Time. Minutes each step took on dev (dev.md section 4), for the window check.
# ---------------------------------------------------------------------------------
STEP_MINUTES = {"pull": 1, "bedrock-proxy": 3, "app": 15, "cc-agent": 5, "nextseek-sidecar": 3,
                "collectstatic": 1, "labs": 2, "checks": 2, "ci": 13}
NESSIE_MINUTES_PER_CASE = 1.2
CC_TURN_USD_UPPER = 0.50     # nessie-questions.md: estimate CC turns x $0.50
SEEK_RESTART_GIB = 4   # was 12; operator 2026-09-28: a dev CI alone bloats SEEK to 10-14 GiB and OOM-kills a
                       # Puma worker, so every paid run starts on a fresh SEEK (only where a restart is allowed)

# The one-connection rule, as numbers.
SSH_TIMEOUT_S = {"preflight": 180, "start": 90, "status": 90, "pull": 600, "pin": 180, "watch": 0}
STATUS_READS_PER_WATCH = 1
# A preflight older than this at the start is re-run: the box moves (another session's job,
# the nightly sync, an OOM) and the runner is rendered from what the preflight saw.
PREFLIGHT_MAX_AGE_MIN = 90
