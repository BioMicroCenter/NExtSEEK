# /// script
# requires-python = ">=3.10"
# dependencies = ["pydantic>=2,<3"]
# ///
"""launch.py: the forms, rules and renderers of the deploy skill's launch flow.

The agent fills small JSON forms; this script validates them, applies the rules in
rules.py, and writes every file the operator reads. The agent never hand-writes the
runner, the report or the commit review.

    uv run launch.py brief    --form brief-form.json            -> <launch dir>/brief.json
    uv run launch.py preflight-script --brief B                  -> the read-only preflight (stdout)
    uv run launch.py ssh      --brief B --purpose preflight --script pre.sh --out <dir>/preflight.out
    uv run launch.py preflight --brief B                          -> <dir>/preflight.json
    uv run launch.py commits  --brief B [--repo R]                -> <dir>/commit-review-form.json
    uv run launch.py review   --brief B --form <filled form>      -> commit-review.json + .md
    uv run launch.py runner   --brief B --out-dir S               -> launch-<TAG>-run.sh, watch.sh
    uv run launch.py ssh      --brief B --purpose start|watch|status|pull|read ...
    uv run launch.py judge    --brief B                           -> facts.json, report-form.json
    uv run launch.py report   --brief B --form <filled form>      -> launch-report.json, LAUNCH-REPORT.md

Exit codes: 0 ok · 2 invalid form or usage · 3 output exists (pass --force) · 5 a stop rule
fired (stop and report, do not work around) · 6 outside the time window · 7 the one-connection
rule refused the ssh.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NoReturn, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"

EXIT_OK, EXIT_INVALID, EXIT_EXISTS, EXIT_STOP, EXIT_WINDOW, EXIT_SSH = 0, 2, 3, 5, 6, 7
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
TAG_RE = re.compile(r"^\d{8}-\d{4}[a-z0-9-]*$")

Component = Literal["bedrock-proxy", "app", "cc-agent", "nextseek-sidecar"]


class Stop(Exception):
    """A rule fired. Carries every problem found, not only the first."""

    def __init__(self, code: int, problems: list[str]):
        super().__init__("\n".join(problems))
        self.code = code
        self.problems = problems


def die(code: int, msg: str) -> NoReturn:
    print(f"launch: {msg}", file=sys.stderr)
    sys.exit(code)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> dt.datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError(f"{s!r} has no timezone; write UTC with a Z")
    return t.astimezone(dt.timezone.utc)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-launch-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: Path, data, force: bool) -> None:
    if path.exists() and not force:
        raise Stop(EXIT_EXISTS, [f"{path} exists; pass --force to overwrite it"])
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_json(path: Path, what: str):
    if not path.is_file():
        raise Stop(EXIT_INVALID, [f"{what} not found: {path}"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Stop(EXIT_INVALID, [f"{what} is not valid JSON ({path}): {e}"])


def validate(model, data, what: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        lines = [f"{what} failed schema validation:"]
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "(top)"
            lines.append(f"  {loc}: {err['msg']}")
        raise Stop(EXIT_INVALID, lines)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# =====================================================================================
# 1. The brief form
# =====================================================================================
class LiveMarker(Strict):
    pattern: str = Field(min_length=3, description="a fixed string the change added")
    file: str = Field(min_length=2, description="the file inside the container that holds it")
    container: Literal["nextseek", "nextseek-sidecar", "dmac-bedrock-proxy"] = "nextseek"
    proves: str = Field(min_length=3, description="what finding it proves")

    @field_validator("pattern")
    @classmethod
    def _no_quote(cls, v: str) -> str:
        if "'" in v or "\n" in v:
            raise ValueError("a live marker may not hold a single quote or a newline")
        return v


class CasesFile(Strict):
    file: str = Field(description="local path of the cases file the run will use")
    cc_turns_estimate: int = Field(ge=0, description="turns likely to go to Container-CC")


class Nessie(Strict):
    cases: list[CasesFile] = Field(min_length=1)
    force_route: Optional[Literal["ns", "cc"]] = None
    pace_s: Optional[int] = Field(default=None, ge=1, le=120)


class Paid(Strict):
    approved: bool
    budget_usd: float = Field(ge=0)
    approved_by: str = Field(min_length=3, description="who approved, in their words")


class Waiver(Strict):
    check: str
    reason: str = Field(min_length=5, description="the operator's words")


class Ack(Strict):
    path: str = Field(min_length=1, description="a flagged path in the range, exactly as `commits` lists it")
    reason: str = Field(min_length=5, description="the operator's words")


class BriefForm(Strict):
    instance: Literal["dev", "prod", "local"]
    expected_sha: str
    change: str = Field(min_length=10)
    parent: Optional[str] = Field(description="the agent or session to report to; null if none")
    images: Optional[list[Component]] = Field(
        default=None, description="null: derive from the deploy range and announce it")
    live_markers: list[LiveMarker] = Field(default_factory=list)
    static_changed: Optional[bool] = None
    ci: bool = True
    ci_nessie_lane: bool = False
    nessie: Optional[Nessie] = None
    paid: Optional[Paid] = None
    prod_nessie: bool = False
    allowed_extras: list[str] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    continue_on_failure: list[Component] = Field(default_factory=list)
    migrations_expected: bool = False
    compose_change_expected: bool = False
    nginx_change_expected: bool = False
    local_ruling: Optional[str] = None
    waivers: list[Waiver] = Field(default_factory=list)
    laya_live_revision: Optional[str] = Field(
        default=None, description="the laya revision the operator approved for live routing; null: live must be off")
    acknowledged_flags: list[Ack] = Field(default_factory=list)
    tag: Optional[str] = None
    start_utc: Optional[str] = None

    @field_validator("expected_sha")
    @classmethod
    def _sha(cls, v: str) -> str:
        v = v.strip().lower()
        if not SHA_RE.match(v):
            raise ValueError("expected_sha must be 7 to 40 hex characters")
        return v

    @field_validator("tag")
    @classmethod
    def _tag(cls, v):
        if v is not None and not TAG_RE.match(v):
            raise ValueError("tag must look like YYYYMMDD-HHMM (UTC), optionally with a -suffix")
        return v

    @field_validator("allowed_extras")
    @classmethod
    def _extras(cls, v: list[str]) -> list[str]:
        bad = [x for x in v if x not in rules.ALLOWED_EXTRAS]
        if bad:
            raise ValueError(f"unknown extras {bad}; the vocabulary is {sorted(rules.ALLOWED_EXTRAS)}")
        return v

    @field_validator("images", "continue_on_failure")
    @classmethod
    def _unique(cls, v):
        if v is not None and len(v) != len(set(v)):
            raise ValueError("list each component once")
        return v



def ordered(components) -> list[str]:
    return [c for c in rules.COMPONENTS if c in set(components or [])]


def estimate_minutes(brief: BriefForm, images: list[str]) -> float:
    m = rules.STEP_MINUTES["pull"] + rules.STEP_MINUTES["checks"]
    for c in images:
        m += rules.STEP_MINUTES[c]
    if "app" in images:
        m += rules.STEP_MINUTES["collectstatic"] + (rules.STEP_MINUTES["labs"]
                                                  if rules.INSTANCES[brief.instance].labs_copy else 0)
    if brief.ci:
        m += rules.STEP_MINUTES["ci"]
    if brief.nessie:
        m += case_count(brief) * rules.NESSIE_MINUTES_PER_CASE
    return round(m, 1)


def case_count(brief: BriefForm) -> int:
    n = 0
    for c in (brief.nessie.cases if brief.nessie else []):
        spec = _read_cases(Path(c.file).expanduser())
        n += len(list(_variants(spec))) + len(spec.get("include_ids") or [])
    return n


def _read_cases(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _variants(spec: dict):
    for fam_name, fam in (spec.get("families") or {}).items():
        for v in fam.get("variants") or []:
            yield fam_name, v


def overlapping_windows(inst: rules.Instance, start: dt.datetime, minutes: float) -> list[str]:
    """The stop windows [start, start + minutes] touches, as readable lines."""
    end = start + dt.timedelta(minutes=minutes)
    hits = []
    day = start.date() - dt.timedelta(days=1)
    while day <= end.date():
        for w in inst.windows:
            ws = dt.datetime(day.year, day.month, day.day, *w.start_utc, tzinfo=dt.timezone.utc)
            we = dt.datetime(day.year, day.month, day.day, *w.end_utc, tzinfo=dt.timezone.utc)
            if we <= ws:
                we += dt.timedelta(days=1)
            if start < we and end > ws and w.blocks == "stop":
                hits.append(f"{ws:%H:%M}Z-{we:%H:%M}Z ({w.why}); the launch would run "
                            f"{start:%H:%M}Z-{end:%H:%M}Z. Next start after {we:%Y-%m-%d %H:%M}Z")
        day += dt.timedelta(days=1)
    return hits


def brief_rules(brief: BriefForm, now: dt.datetime) -> tuple[list[str], list[str], list[str]]:
    """(stops, window problems, warnings) for a schema-valid brief."""
    inst = rules.INSTANCES[brief.instance]
    stops: list[str] = []
    warns: list[str] = []
    images = brief.images or []
    extras = set(brief.allowed_extras)

    if brief.instance == "local" and not brief.local_ruling and (
            images or brief.ci or brief.nessie or brief.ci_nessie_lane):
        stops.append("local: rebuilds, ./startup.sh ci and the stack are off on this workstation "
                     "(operator ruling 2026-09-24). Set images [], ci false and nessie null, or quote "
                     "the operator's words allowing it in local_ruling")
    if brief.prod_nessie and brief.instance != "prod":
        stops.append("prod_nessie is set on a brief whose instance is not prod: contradictory")
    if brief.nessie and brief.instance == "prod" and not brief.prod_nessie:
        stops.append("Nessie on prod needs prod_nessie: true in the brief")
    if brief.ci_nessie_lane and brief.instance == "prod":
        stops.append("prod never runs the CI Nessie lane")
    if (brief.nessie or brief.ci_nessie_lane) and not (brief.paid and brief.paid.approved):
        stops.append("the Nessie step is paid: it needs paid.approved true with a budget")
    if brief.paid and brief.paid.approved and brief.paid.budget_usd <= 0:
        stops.append("paid.approved is true but paid.budget_usd is 0")
    usd = 0.0
    if brief.nessie:
        usd += sum(c.cc_turns_estimate for c in brief.nessie.cases) * rules.CC_TURN_USD_UPPER
    if brief.ci_nessie_lane:
        usd += 0.60
    if brief.paid and usd > brief.paid.budget_usd:
        stops.append(f"the paid estimate ${usd:.2f} is over the budget ${brief.paid.budget_usd:.2f}")
    both = extras & set(brief.ruled_out)
    if both:
        stops.append(f"{sorted(both)} is both allowed and ruled out")
    if "bedrock-proxy" in images and "bedrock-proxy rebuild ok" not in extras:
        stops.append("images names bedrock-proxy but allowed_extras lacks 'bedrock-proxy rebuild ok'")
    if brief.instance == "prod" and brief.migrations_expected and "db backup ok" not in extras:
        stops.append("a migration on prod needs a database backup first: allowed_extras 'db backup ok'")
    if brief.nginx_change_expected and "nginx recreate ok" not in extras:
        warns.append("the range changes docker/nginx.conf but no 'nginx recreate ok': the running nginx "
                     "keeps the old file; propose the recreate in the report")
    for c in brief.continue_on_failure:
        if c not in images:
            stops.append(f"continue_on_failure names {c}, which is not in images")
    if brief.images is not None and not images and not brief.ci and not brief.nessie:
        stops.append("the brief asks for nothing: no image, no CI, no Nessie")
    for w in brief.waivers:
        if w.check not in PREFLIGHT_CHECK_IDS:
            stops.append(f"waiver for unknown preflight check {w.check!r}; known: {sorted(PREFLIGHT_CHECK_IDS)}")
        elif w.check in UNWAIVABLE:
            stops.append(f"the preflight check {w.check!r} cannot be waived: it decides what ships")

    # cases files: present, loadable, and never a writing family on prod
    for c in (brief.nessie.cases if brief.nessie else []):
        p = Path(c.file).expanduser()
        spec = _read_cases(p)
        if not spec:
            stops.append(f"cases file {c.file} is missing or not JSON")
            continue
        if not spec.get("families") and not spec.get("include_ids"):
            stops.append(f"cases file {c.file} has neither families nor include_ids")
        if brief.instance == "prod":
            for fam, v in _variants(spec):
                vid, vfam = v.get("id", "?"), v.get("family", fam)
                if vfam in rules.PROD_FORBIDDEN_FAMILIES or vid.startswith(rules.PROD_FORBIDDEN_ID_PREFIXES):
                    stops.append(f"{c.file}: {vid} ({vfam}) writes or launches: never on prod")
            for i in spec.get("include_ids") or []:
                if i.startswith(rules.PROD_FORBIDDEN_ID_PREFIXES) or i.split(".")[0] in (
                        "write", "pipeline", "reingest", "batch"):
                    stops.append(f"{c.file}: include_ids {i} may write or launch: check it by hand, "
                                 "then name it in a waiver")
        if spec.get("_measure") and brief.instance in ("dev", "prod"):
            warns.append(f"{c.file} carries a _measure block: re-pin it on {inst.box} before the run "
                         "(references/nessie-questions.md section 3)")

    if not brief.live_markers and ("app" in images or brief.images is None):
        warns.append("no live marker: pick a string the change added from the diff, add it to "
                     "live_markers, and say you picked it")
    if not brief.ci and brief.instance in ("dev", "prod"):
        warns.append("ci is false: the report will say CI was not run (not in the brief)")

    window: list[str] = []
    if inst.windows and (images or brief.ci or brief.nessie):
        start = parse_utc(brief.start_utc) if brief.start_utc else now
        window = overlapping_windows(inst, start, estimate_minutes(brief, images or ["app"]))
    return stops, window, warns


def derive(brief: BriefForm, tag: str, now: dt.datetime) -> dict:
    inst = rules.INSTANCES[brief.instance]
    images = ordered(brief.images) if brief.images is not None else None
    usd = 0.0
    if brief.nessie:
        usd += sum(c.cc_turns_estimate for c in brief.nessie.cases) * rules.CC_TURN_USD_UPPER
    if brief.ci_nessie_lane:
        usd += 0.60
    return {
        "tag": tag,
        "box": inst.box,
        "ssh_host": inst.ssh_host,
        "transport": inst.transport,
        "in_dir": inst.in_dir,
        "repo": inst.repo,
        "images": images,
        "images_source": "brief" if images is not None else "derive from the range (commits step)",
        "seek_restart_ok": inst.seek_restart_default or "seek restart ok" in brief.allowed_extras,
        "labs_copy": inst.labs_copy,
        "known_ci_reds": [k.match for k in inst.known_reds if k.kind == "ci"],
        "known_health_reds": [k.match for k in inst.known_reds if k.kind == "health"],
        "estimate": {"minutes": estimate_minutes(brief, images or []),
                     "usd_upper": round(usd, 2),
                     "budget_usd": brief.paid.budget_usd if brief.paid else 0.0},
        "cases": case_count(brief),
        "report_to": brief.parent or "self",
    }


def launch_dir_for(instance: str, tag: str, override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return rules.reports_root() / f"{instance}-launch-{tag}"


def cmd_brief(a) -> int:
    raw = load_json(Path(a.form).expanduser(), "brief form")
    if isinstance(raw, dict) and "parent" not in raw:
        raise Stop(EXIT_INVALID, ["brief form: 'parent' is required: the agent or session name that "
                                  "spawned this launch, or null when there is none"])
    brief = validate(BriefForm, raw, "brief form")
    now = parse_utc(a.now) if a.now else utcnow()
    tag = brief.tag or now.strftime("%Y%m%d-%H%M")
    d = launch_dir_for(brief.instance, tag, a.out_dir)
    stops, window, warns = brief_rules(brief, now)
    for w in warns:
        print(f"WARN: {w}")
    verdict = "stop" if stops else "window" if window else "ok"
    out = {
        "schema": "launch-brief/v1",
        "validated_at": iso(now),
        "launch_dir": str(d),
        "verdict": verdict,
        "problems": {"stop": stops, "window": window},
        "brief": brief.model_dump(mode="json"),
        "derived": derive(brief, tag, now),
        "warnings": warns,
    }
    # written even when refused, so the stop can be reported (`report --no-facts`); every
    # later step refuses a brief whose verdict is not ok
    write_json(d / "brief.json", out, a.force)
    if stops or window:
        lines = []
        if stops:
            lines += ["STOP (the brief cannot run as written; ask the supervisor):"] + [f"  - {x}" for x in stops]
        if window:
            lines += ["WINDOW (do not start now):"] + [f"  - {x}" for x in window]
        lines.append(f"brief.json is written with verdict '{verdict}' (for the report only): {d / 'brief.json'}")
        raise Stop(EXIT_STOP if stops else EXIT_WINDOW, lines)
    der = out["derived"]
    print(f"OK: wrote {d / 'brief.json'}")
    print(f"TAG: {tag}")
    print(f"LAUNCH_DIR: {d}")
    print(f"IMAGES: {', '.join(der['images']) if der['images'] is not None else 'derive (run commits)'}")
    print(f"ESTIMATE: {der['estimate']['minutes']} min, ${der['estimate']['usd_upper']:.2f} paid upper bound")
    print(f"REPORT_TO: {der['report_to']}")
    return EXIT_OK


def load_brief(path: str, *, refused_ok: bool = False) -> tuple[dict, BriefForm, Path]:
    p = Path(path).expanduser()
    data = load_json(p, "brief.json")
    if data.get("schema") != "launch-brief/v1":
        raise Stop(EXIT_INVALID, [f"{p} is not a validated brief (run `launch.py brief` first)"])
    if data.get("verdict", "ok") != "ok" and not refused_ok:
        raise Stop(EXIT_STOP, [f"the brief was refused ({data['verdict']}): only `report --no-facts` may use it. "
                               "Fix the brief form and run `launch.py brief --force`"])
    return data, validate(BriefForm, data["brief"], "brief.json"), p.parent


# The preflight checks a brief may waive (their ids), filled in section 2.
PREFLIGHT_CHECK_IDS: set[str] = set()


# =====================================================================================
# 2. Preflight: the rendered read-only script, and the table it is read against
# =====================================================================================
# JevLevROUTING: on every instance (dev is where the posterior flag stuck). Values lose an inline comment, outer
# spaces and quotes, as compose's env_file parser does, so the row judges the value the app runs with.
_KV_VALUE = (' | cut -d= -f2 | sed -E \'s/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//\''
             ' | tr -d \'"\'')
LAYA_KV = ('s=$(grep -E "^NESSIE_LAYA_SHADOW=" docker/nextseek.env' + _KV_VALUE + '); '
           'l=$(grep -E "^NESSIE_LAYA_LIVE=" docker/nextseek.env' + _KV_VALUE + '); '
           'echo "KV laya_mode=shadow=${s:-unset} live=${l}"')
PREFLIGHT_EXTRA = {
    "dev": LAYA_KV + '\n' + 'if [ -f /tmp/labs_db.json ]; then echo "KV labs_source=present"; else echo "KV labs_source=missing"; fi',
    "prod": "\n".join([
        '[ -n "$E" ] && git diff --name-only HEAD.."$E" -- startup/seed/neo4j.cypher.gz startup/seed/seek_production.sql.gz | sed \'s/^/SEED_TOUCHED /\'',
        'for f in NessieAI/docker/bedrock-proxy/proxy-secret.env docker/seek-nginx.conf; do if [ -f "$f" ]; then echo "FILE present $f"; else echo "FILE missing $f"; fi; done',
        'if docker compose config -q >/dev/null 2>&1; then echo "KV compose_config=ok"; else echo "KV compose_config=bad"; fi',
        'v=$(grep -E "^NEXTSEEK_POSTERIOR_ROUTING_ENABLED=" docker/nextseek.env | cut -d= -f2); echo "KV posterior_routing=${v:-unset}"',
        LAYA_KV,
    ]),
}

# Checks that can never be waived: they decide what ships, or whether something dies.
UNWAIVABLE = {"complete", "branch", "expected", "origin_match", "ff", "busy", "fetch"}
PREFLIGHT_CHECK_IDS.update({
    "complete", "disk", "memory", "swap", "oom", "restarts", "busy", "tmux", "branch", "fetch",
    "expected", "origin_match", "ff", "dirty", "dirty_touched", "nextseek_env", "ci_env",
    "labs_source", "http", "seed_touched", "compose_files", "compose_config", "posterior_routing",
    "laya_mode",
    "seek_worker_oom",
})
CI_ENV_KEYS = ("CI_SMOKE_USER", "CI_SMOKE_PASS", "CI_WRITE_USER", "CI_WRITE_PASS")


def render(template: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace(f"@@{k}@@", v)
    left = sorted(set(re.findall(r"@@[A-Z_]+@@", text)))
    if left:
        raise Stop(EXIT_INVALID, [f"{template}: unfilled placeholders {left}"])
    return text


def cmd_preflight_script(a) -> int:
    data, brief, _ = load_brief(a.brief)
    if brief.instance == "local":
        raise Stop(EXIT_INVALID, ["local has no preflight over ssh: see references/local.md"])
    inst = rules.INSTANCES[brief.instance]
    sys.stdout.write(render("preflight.sh.tmpl", {
        "INSTANCE": inst.name, "BOX": inst.box, "REPO": inst.repo, "EXP": brief.expected_sha,
        "EXTRA": PREFLIGHT_EXTRA.get(inst.name, "")}))
    return EXIT_OK


def parse_preflight(text: str) -> dict:
    out: dict = {"kv": {}, "dirty": [], "dirty_touched": [], "images": {}, "containers": {},
                 "mem": {}, "tmux": [], "seed_touched": [], "files": {}}
    for line in text.splitlines():
        if line.startswith("KV "):
            k, _, v = line[3:].partition("=")
            out["kv"][k.strip()] = v.strip()
        elif line.startswith("DIRTY_TOUCHED "):
            out["dirty_touched"].append(line[len("DIRTY_TOUCHED "):].strip())
        elif line.startswith("DIRTY "):
            out["dirty"].append(line[len("DIRTY "):].rstrip("\n"))
        elif line.startswith("IMAGE "):
            parts = line.split(" ", 2)
            out["images"][parts[1]] = parts[2].strip() if len(parts) > 2 else ""
        elif line.startswith("CONTAINER "):
            parts = line.split(" ", 2)
            fields = dict(re.findall(r"(\w+)=(\S+)", parts[2] if len(parts) > 2 else ""))
            out["containers"][parts[1]] = fields or {"status": "missing"}
        elif line.startswith("MEM "):
            parts = line.split(" ", 2)
            out["mem"][parts[1]] = parts[2].strip() if len(parts) > 2 else ""
        elif line.startswith("TMUX "):
            out["tmux"].append(line[5:].strip())
        elif line.startswith("SEED_TOUCHED "):
            out["seed_touched"].append(line[len("SEED_TOUCHED "):].strip())
        elif line.startswith("FILE "):
            parts = line.split(" ", 2)
            out["files"][parts[2].strip()] = parts[1]
    return out


def _gib(usage: str) -> Optional[float]:
    m = re.match(r"([\d.]+)\s*([KMGT]i?B)", usage or "")
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return n * {"KiB": 1 / 1048576, "KB": 1 / 1048576, "MiB": 1 / 1024, "MB": 1 / 1024,
                "GiB": 1, "GB": 1, "TiB": 1024, "TB": 1024}[unit]


def judge_preflight(p: dict, brief: BriefForm, tag: str) -> list[dict]:
    """One row per check: {id, value, verdict (ok|warn|stop|waived), rule}."""
    inst = rules.INSTANCES[brief.instance]
    kv = p["kv"]
    rows: list[dict] = []

    def row(cid, value, ok, rule, warn=False):
        rows.append({"id": cid, "value": value, "verdict": "ok" if ok else ("warn" if warn else "stop"),
                     "rule": rule})

    row("complete", kv.get("done", "no"), kv.get("done") == "yes" and kv.get("repo") != "missing",
        "the preflight ran to its last line in the repo")
    disk = int(kv["disk_free_gb"]) if kv.get("disk_free_gb", "").isdigit() else None
    row("disk", f"{disk} GB free" if disk is not None else "unknown",
        disk is not None and disk >= inst.disk_floor_gb, f">= {inst.disk_floor_gb} GB free")
    mem = int(kv["mem_available_gib"]) if kv.get("mem_available_gib", "").isdigit() else None
    row("memory", f"{mem} GiB available" if mem is not None else "unknown",
        mem is not None and mem >= inst.memory_floor_gib, f">= {inst.memory_floor_gib} GiB available")
    st, su = kv.get("swap_total_gib", "0"), kv.get("swap_used_gib", "0")
    swap_full = st.isdigit() and su.isdigit() and int(st) > 0 and int(su) >= int(st)
    row("swap", f"{su} of {st} GiB used", not swap_full, "swap not full")
    ooms = [c for c, f in p["containers"].items() if f.get("oom") == "true"]
    # SEEK's cap kills a runaway Puma worker by design (docker-compose.yml) and Puma respawns it: with the
    # container running, not restart-looping and SEEK answering on :3000, that is a warning, not a stop
    # (operator 2026-09-29). Any other container's OOM, or a SEEK that does not answer, still stops.
    seek = p["containers"].get("seek", {})
    worker_oom = ("seek" in ooms and seek.get("status") == "running"
                  and int(seek.get("restarts", "0") or 0) < 3 and kv.get("seek_http") in ("200", "302"))
    hard = [c for c in ooms if not (c == "seek" and worker_oom)]
    row("oom", ", ".join(hard) or "none", not hard, "no container OOMKilled (a SEEK worker kill with SEEK "
        "running and answering is the seek_worker_oom row)")
    if worker_oom:
        row("seek_worker_oom", f"seek (worker killed; running, answers {kv.get('seek_http')})", False,
            "SEEK's memory cap killed a Puma worker and Puma respawned it; the runner restarts SEEK before a "
            "paid Nessie step where a restart is allowed", warn=True)
    loops = [c for c, f in p["containers"].items()
             if f.get("status") == "restarting" or int(f.get("restarts", "0") or 0) >= 3]
    row("restarts", ", ".join(loops) or "none", not loops, "no container restarting or restarted 3+ times")
    row("busy", kv.get("busy", "unknown"), kv.get("busy") == "none",
        "no graph sync, drift or harness run inside nextseek")
    ours = f"launch-{tag}"
    other_jobs = [t for t in p["tmux"] if re.match(r"^(launch-|nessie|rebuild|ci)", t)
                  and not t.startswith(ours + ":")]
    row("tmux", "; ".join(p["tmux"]) or "none", not other_jobs,
        "no other session's launch, rebuild or harness job in tmux")
    row("branch", kv.get("branch", "unknown"), kv.get("branch") == "dev", "the checkout is on dev")
    row("fetch", kv.get("fetch", "unknown"), kv.get("fetch") == "ok", "git fetch origin worked")
    exp = kv.get("expected", "NOT_FOUND")
    row("expected", exp, exp != "NOT_FOUND" and exp.startswith(brief.expected_sha),
        "the expected sha exists on the box after a fetch")
    row("origin_match", kv.get("origin_match", "unknown"), kv.get("origin_match") == "yes",
        "origin/dev IS the expected sha (the supervisor decides what ships)")
    row("ff", kv.get("ff", "unknown"), kv.get("ff") == "yes", "the box's HEAD is an ancestor of it")
    unknown_dirty = [d for d in p["dirty"] if d not in inst.known_dirty]
    row("dirty", "; ".join(unknown_dirty) or "only the known list", not unknown_dirty,
        "git status shows only the instance's known-dirty files (never stash, commit or revert others)")
    bad_touch = [f for f in p["dirty_touched"]
                 if not any(f.startswith(x) for x in inst.discard_before_pull)]
    row("dirty_touched", "; ".join(p["dirty_touched"]) or "none", not bad_touch,
        "the range touches no dirty file except the context refresh the runner discards")
    row("nextseek_env", kv.get("nextseek_env", "unknown"), kv.get("nextseek_env") == "ok",
        "docker/nextseek.env has no old /app/chat_nextseek style paths")
    keys = set(filter(None, kv.get("ci_env_keys", "").split(",")))
    need_keys = brief.ci or brief.nessie is not None
    missing = [k for k in CI_ENV_KEYS if k not in keys]
    row("ci_env", f"missing {missing}" if missing else "all 4 keys set", not (need_keys and missing),
        "~/.config/nextseek/ci.env holds the four CI keys")
    if inst.labs_copy:
        present = kv.get("labs_source") == "present"
        needs = "app" in (brief.images or ["app"])
        row("labs_source", kv.get("labs_source", "unknown"), present,
            "/tmp/labs_db.json exists (an app rebuild drops the copied labs file)", warn=not needs)
    row("http", kv.get("http", "unknown"), kv.get("http") == "200", "the app answers 200 on 127.0.0.1:8000")
    laya = kv.get("laya_mode", "unknown")
    m = re.match(r"^shadow=(\S*) live=(\S*)$", laya)
    shadow, live = (m.group(1), m.group(2)) if m else ("unset", "")  # absent reads as off
    laya_ok = (m is not None or laya == "unknown") and (
        (shadow in ("0", "1", "unset") and live == "") or (live != "" and live == brief.laya_live_revision))
    row("laya_mode", laya, laya_ok, "laya shadow is 0, 1 or absent and live is empty; a live value passes only "
        "when the brief's laya_live_revision names that revision; a value the rule cannot read stops")
    if inst.name == "prod":
        row("seed_touched", "; ".join(p["seed_touched"]) or "none", not p["seed_touched"],
            "the range does not touch the dirty production dumps")
        missing_files = [f for f, s in p["files"].items() if s != "present"]
        row("compose_files", "; ".join(missing_files) or "present", not missing_files,
            "proxy-secret.env and seek-nginx.conf exist")
        row("compose_config", kv.get("compose_config", "unknown"), kv.get("compose_config") == "ok",
            "docker compose config accepts the file")
        row("posterior_routing", kv.get("posterior_routing", "unknown"),
            kv.get("posterior_routing") in ("unset", "0"), "posterior routing unset or 0")

    waived = {w.check: w.reason for w in brief.waivers}
    for r in rows:
        if r["verdict"] in ("stop", "warn") and r["id"] in waived:
            if r["id"] in UNWAIVABLE:
                r["rule"] += " (cannot be waived)"
            else:
                r["verdict"] = "waived"
                r["rule"] += f" (waived: {waived[r['id']]})"
    return rows


def cmd_preflight(a) -> int:
    data, brief, d = load_brief(a.brief)
    tag = data["derived"]["tag"]
    src = Path(a.output).expanduser() if a.output else d / "preflight.out"
    if not src.is_file():
        raise Stop(EXIT_INVALID, [f"no preflight output at {src}; run the ssh preflight first"])
    p = parse_preflight(src.read_text(encoding="utf-8", errors="replace"))
    rows = judge_preflight(p, brief, tag)
    kv = p["kv"]
    out = {
        "schema": "launch-preflight/v1",
        "read_at": iso(utcnow()),
        "source": str(src),
        "box_utc": kv.get("utc"),
        "box_head": kv.get("head"),
        "box_head_subject": kv.get("head_subject"),
        "expected_full": kv.get("expected"),
        "range_count": int(kv["range_count"]) if kv.get("range_count", "").isdigit() else None,
        "images": p["images"],
        "containers": p["containers"],
        "memory": p["mem"],
        "checks": rows,
        "verdict": "stop" if any(r["verdict"] == "stop" for r in rows) else "ok",
    }
    write_json(d / "preflight.json", out, True)
    width = max(len(r["id"]) for r in rows)
    for r in rows:
        print(f"{r['verdict'].upper():6} {r['id']:<{width}}  {r['value']}  [{r['rule']}]")
    print(f"OK: wrote {d / 'preflight.json'}")
    if out["verdict"] == "stop":
        raise Stop(EXIT_STOP, ["STOP: a preflight check failed; report it, do not work around it:"]
                   + [f"  - {r['id']}: {r['value']} ({r['rule']})" for r in rows if r["verdict"] == "stop"])
    return EXIT_OK


# =====================================================================================
# 3. The commit review: the deploy range, what each change needs, proposed CI checks
# =====================================================================================
REVIEW_LIMIT = 50


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise Stop(EXIT_INVALID, [f"git {' '.join(args)} failed in {repo}: {r.stderr.strip()[:300]}"])
    return r.stdout


def classify_paths(paths: list[str]) -> dict:
    images: dict[str, list[str]] = {}
    flags: dict[str, list[str]] = {}
    for path in paths:
        rule = rules.rule_for(path)
        if rule is None:
            continue
        for img in rule.images:
            images.setdefault(img, []).append(path)
        if rule.flag:
            flags.setdefault(rule.flag, []).append(path)
    return {"images": images, "flags": flags}


def range_problems(brief: BriefForm, cls: dict) -> tuple[list[str], list[str]]:
    """(stops, warnings) from comparing the range with the brief."""
    stops, warns = [], []
    needed = ordered(cls["images"])
    if brief.images is not None:
        missing = [i for i in needed if i not in brief.images]
        extra = [i for i in brief.images if i not in needed]
        for i in missing:
            eg = ", ".join(cls["images"][i][:3])
            stops.append(f"the range needs {i} (e.g. {eg}) but the brief does not name it")
        for i in extra:
            warns.append(f"the brief rebuilds {i}, which no path in the range needs")
    expected = {"nginx": brief.nginx_change_expected, "compose": brief.compose_change_expected,
                "migration": brief.migrations_expected, "seed": False}
    acked = {a.path: a.reason for a in brief.acknowledged_flags}
    for flag, paths in cls["flags"].items():
        open_paths = [p for p in paths if p not in acked]
        for p in paths:
            if p in acked:
                warns.append(f"flagged path {p} ({flag}) acknowledged by the operator: {acked[p]}")
        if open_paths and not expected.get(flag, False):
            paths = open_paths
            stops.append(f"the range touches {', '.join(paths[:3])} ({flag}) and the brief did not name it "
                         f"(set {flag}_change_expected or migrations_expected, "
                         f"add it to acknowledged_flags with a reason, or ask)")
    return stops, warns


def cmd_commits(a) -> int:
    data, brief, d = load_brief(a.brief)
    repo = Path(a.repo).expanduser() if a.repo else rules.workstation_repo()
    base = a.base
    if not base:
        pf = d / "preflight.json"
        if not pf.is_file():
            raise Stop(EXIT_INVALID, ["no --base and no preflight.json: run the preflight first"])
        base = load_json(pf, "preflight.json").get("box_head")
    head = a.head or brief.expected_sha
    for sha in (base, head):
        if not sha or subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                                     capture_output=True).returncode != 0:
            raise Stop(EXIT_INVALID, [f"{sha} is not in {repo}; run `git -C {repo} fetch -q origin` first"])
    base_full = git(repo, "rev-parse", base).strip()
    head_full = git(repo, "rev-parse", head).strip()
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", base_full, head_full]).returncode:
        raise Stop(EXIT_STOP, [f"STOP: the box's HEAD {base_full[:8]} is not an ancestor of {head_full[:8]}"])
    total = int(git(repo, "rev-list", "--count", f"{base_full}..{head_full}").strip())
    fmt = "%x1e%H%x1f%h%x1f%s%x1f%cI%x1f%P"
    log = git(repo, "log", f"--max-count={REVIEW_LIMIT}", f"--format={fmt}", "--name-only",
              f"{base_full}..{head_full}")
    commits = []
    for chunk in log.split("\x1e")[1:]:
        head_line, _, rest = chunk.partition("\n")
        full, short, subject, date, parents = head_line.split("\x1f")
        files = [f for f in rest.splitlines() if f.strip()]
        cls = classify_paths(files)
        commits.append({"sha": short[:8], "full": full, "subject": subject, "date": date,
                        "merge": len(parents.split()) > 1, "files": files,
                        "images": ordered(cls["images"]), "flags": sorted(cls["flags"])})
    all_paths = [p for p in git(repo, "diff", "--name-only", f"{base_full}..{head_full}").splitlines() if p]
    cls = classify_paths(all_paths)
    stops, warns = range_problems(brief, cls)
    form = {
        "schema": "launch-commit-review-form/v1",
        "range": {"base": base_full, "head": head_full, "total": total, "listed": len(commits),
                  "skipped": total - len(commits)},
        "derived": {"images_needed": ordered(cls["images"]),
                    "brief_images": brief.images,
                    "paths_by_image": {k: v[:20] for k, v in cls["images"].items()},
                    "flags": cls["flags"], "stops": stops, "warnings": warns},
        "commits": commits,
        "summary": None,
        "behaviour_changes": [],
        "no_behaviour_change": [],
        "proposed_checks": [],
    }
    out = d / "commit-review-form.json"
    write_json(out, form, a.force)
    print(f"OK: wrote {out}")
    print(f"RANGE: {total} commits {base_full[:8]}..{head_full[:8]}; reviewing the latest {len(commits)}"
          + (f", {total - len(commits)} skipped" if total > len(commits) else ""))
    print(f"IMAGES_NEEDED: {', '.join(ordered(cls['images'])) or 'none'}")
    for w in warns:
        print(f"WARN: {w}")
    if stops:
        raise Stop(EXIT_STOP, ["STOP (the range and the brief disagree; report before building):"]
                   + [f"  - {s}" for s in stops])
    return EXIT_OK


class TestRef(Strict):
    path: str = Field(min_length=3, description="path, or path::test")
    lane: Literal["blocking", "informational", "harness_host", "container", "no_ci"]


class LiveCheck(Strict):
    kind: Literal["stack_health", "smoke", "runner_check", "nessie_case", "none"]
    ref: str = Field(min_length=2)
    proves: str = Field(min_length=3)


class BehaviourChange(Strict):
    id: str = Field(pattern=r"^BC\d+$")
    summary: str = Field(min_length=5)
    commits: list[str] = Field(min_length=1)
    unit_tests: list[TestRef] = Field(default_factory=list)
    live_check: Optional[LiveCheck] = None
    status: Literal["live", "unit_only", "needs_paid_turn", "needs_fault_injection", "untested"]
    proposals: list[str] = Field(default_factory=list)
    accepted_gap: Optional[str] = None
    covers_skipped: list[str] = Field(
        default_factory=list,
        description="short shas of skipped commits (past the latest 50) this change brings in through a listed merge")

    @field_validator("covers_skipped")
    @classmethod
    def _shas(cls, v):
        bad = [x for x in v if not SHA_RE.match(x)]
        if bad:
            raise ValueError(f"covers_skipped holds non-sha values {bad}")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        if self.status == "live" and (self.live_check is None or self.live_check.kind == "none"):
            raise ValueError(f"{self.id}: status live needs a live_check that is not 'none'")
        if self.status == "unit_only" and not self.unit_tests:
            raise ValueError(f"{self.id}: status unit_only needs at least one unit test")
        if self.status == "untested" and self.unit_tests:
            raise ValueError(f"{self.id}: status untested, but unit_tests is not empty")
        if self.status != "live" and not self.proposals and not self.accepted_gap:
            raise ValueError(f"{self.id}: a change that is not live needs a proposal or an accepted_gap")
        return self


class NoBehaviourChange(Strict):
    commits: list[str] = Field(min_length=1)
    reason: Literal["docs", "tests_only", "ci_or_startup_only", "refactor", "merge_commit",
                    "data_or_fixture", "revert_pair", "other"]
    note: Optional[str] = None


class ProposedCheck(Strict):
    id: str = Field(pattern=r"^P\d+$")
    kind: Literal["stack_health", "smoke", "unit", "blocking_glob", "ci_lane", "nessie_case",
                  "runner_check"]
    where: str = Field(min_length=3, description="the file the check would live in")
    proves: str = Field(min_length=5)
    fails_when: str = Field(min_length=5)
    cost: Literal["free", "paid"]
    priority: Literal["before_next_launch", "soon", "later"]
    covers: list[str] = Field(min_length=1)


class CommitRef(Strict):
    sha: str
    full: str
    subject: str
    date: str
    merge: bool
    files: list[str]
    images: list[str]
    flags: list[str]


class CommitReview(Strict):
    schema_: str = Field(alias="schema")
    range: dict
    derived: dict
    commits: list[CommitRef]
    summary: str = Field(min_length=10)
    behaviour_changes: list[BehaviourChange]
    no_behaviour_change: list[NoBehaviourChange]
    proposed_checks: list[ProposedCheck]

    @model_validator(mode="after")
    def _accounted(self):
        listed = {c.sha for c in self.commits}
        rng = self.range
        if rng.get("listed") != len(self.commits) or rng.get("total", 0) < len(self.commits):
            raise ValueError("range.listed/total do not match the commits list; do not edit them")
        if len(self.commits) > REVIEW_LIMIT:
            raise ValueError(f"more than {REVIEW_LIMIT} commits listed")

        def resolve(s: str, where: str) -> str:
            hits = [c for c in listed if c.startswith(s[:8]) or s.startswith(c)]
            if len(hits) != 1:
                raise ValueError(f"{where}: {s!r} is not one of the listed commits")
            return hits[0]

        in_bc: set[str] = set()
        for bc in self.behaviour_changes:
            in_bc |= {resolve(s, bc.id) for s in bc.commits}
            listed_skips = [x for x in bc.covers_skipped if any(c.startswith(x[:8]) for c in listed)]
            if listed_skips:
                raise ValueError(f"{bc.id}: covers_skipped names listed commits {listed_skips}; put them in commits")
        in_nbc: set[str] = set()
        for n in self.no_behaviour_change:
            in_nbc |= {resolve(s, f"no_behaviour_change ({n.reason})") for s in n.commits}
        both = in_bc & in_nbc
        if both:
            raise ValueError(f"commits in both a behaviour change and no_behaviour_change: {sorted(both)}")
        missing = listed - in_bc - in_nbc
        if missing:
            raise ValueError(f"{len(missing)} listed commit(s) are not accounted for: {sorted(missing)[:10]}")
        ids = [b.id for b in self.behaviour_changes]
        pids = [p.id for p in self.proposed_checks]
        for name, seq in (("behaviour change", ids), ("proposal", pids)):
            dup = {x for x in seq if seq.count(x) > 1}
            if dup:
                raise ValueError(f"duplicate {name} ids: {sorted(dup)}")
        for bc in self.behaviour_changes:
            for pid in bc.proposals:
                if pid not in pids:
                    raise ValueError(f"{bc.id} names proposal {pid}, which does not exist")
        for p in self.proposed_checks:
            bad = [c for c in p.covers if c not in ids]
            if bad:
                raise ValueError(f"{p.id} covers unknown behaviour changes {bad}")
        return self


def render_review_md(rv: dict, brief: BriefForm, tag: str) -> str:
    r = rv["range"]
    lines = [f"# Commit review and proposed CI checks: {brief.instance} launch {tag}", ""]
    lines.append(f"Range `{r['base'][:8]}..{r['head'][:8]}`: {r['total']} commits"
                 + (f"; the latest {r['listed']} are reviewed and {r['skipped']} older ones are skipped."
                    if r["skipped"] else "; all reviewed."))
    lines += ["", rv["summary"], ""]
    bcs = rv["behaviour_changes"]
    counts = {s: sum(1 for b in bcs if b["status"] == s)
              for s in ("live", "unit_only", "needs_paid_turn", "needs_fault_injection", "untested")}
    lines.append(f"Behaviour changes: {len(bcs)} (live {counts['live']}, unit only {counts['unit_only']}, "
                 f"needs a paid turn {counts['needs_paid_turn']}, needs a fault injected "
                 f"{counts['needs_fault_injection']}, untested {counts['untested']}). "
                 f"Proposed checks: {len(rv['proposed_checks'])}.")
    lines += ["", "## Proposed CI checks (not written; a launch runs only its brief's steps)", "",
              "| Id | Kind | Where | Proves | Fails when | Cost | Priority | Covers |",
              "|---|---|---|---|---|---|---|---|"]
    for p in rv["proposed_checks"]:
        lines.append(f"| {p['id']} | {p['kind']} | `{p['where']}` | {md(p['proves'])} | {md(p['fails_when'])} "
                     f"| {p['cost']} | {p['priority']} | {', '.join(p['covers'])} |")
    if not rv["proposed_checks"]:
        lines.append("| none | | | | | | | |")
    lines += ["", "## Behaviour changes", "",
              "| Id | Change | Commits | Unit tests (lane) | Live check (what it proves) | Status | Proposals |",
              "|---|---|---|---|---|---|---|"]
    for b in bcs:
        tests = "; ".join(f"`{t['path']}` ({t['lane']})" for t in b["unit_tests"]) or "none"
        lc = b.get("live_check")
        live = (f"{lc['kind']}: {md(lc['ref'])} ({md(lc['proves'])})" if lc and lc["kind"] != "none" else "none")
        props = ", ".join(b["proposals"]) or (f"accepted gap: {md(b['accepted_gap'])}" if b.get("accepted_gap") else "")
        commits = ", ".join(b["commits"]) + (f" (+ skipped {', '.join(b['covers_skipped'])})"
                                             if b.get("covers_skipped") else "")
        lines.append(f"| {b['id']} | {md(b['summary'])} | {commits} | {tests} | {live} "
                     f"| {b['status']} | {props} |")
    lines += ["", "## Commits with no behaviour change", "", "| Commits | Reason | Note |", "|---|---|---|"]
    for n in rv["no_behaviour_change"]:
        lines.append(f"| {', '.join(n['commits'])} | {n['reason']} | {md(n.get('note') or '')} |")
    lines += ["", "## The range", "", "| Commit | Subject | Images | Flags |", "|---|---|---|---|"]
    for c in rv["commits"]:
        lines.append(f"| {c['sha']} | {md(c['subject'])} | {', '.join(c['images']) or '-'} | "
                     f"{', '.join(c['flags']) or '-'} |")
    return "\n".join(lines) + "\n"


def md(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def cmd_review(a) -> int:
    data, brief, d = load_brief(a.brief)
    raw = load_json(Path(a.form).expanduser(), "commit review form")
    rv = validate(CommitReview, raw, "commit review form")
    out = rv.model_dump(mode="json", by_alias=True)
    out["schema"] = "launch-commit-review/v1"
    out["validated_at"] = iso(utcnow())
    out["deliver_to"] = brief.parent
    write_json(d / "commit-review.json", out, a.force)
    mdtext = render_review_md(out, brief, data["derived"]["tag"])
    if (d / "commit-review.md").exists() and not a.force:
        raise Stop(EXIT_EXISTS, [f"{d / 'commit-review.md'} exists; pass --force"])
    atomic_write(d / "commit-review.md", mdtext)
    print(f"OK: wrote {d / 'commit-review.json'} and {d / 'commit-review.md'}")
    if brief.parent:
        print(f"DELIVER: SendMessage to {brief.parent!r} now, with the full text of {d / 'commit-review.md'}")
    else:
        print("DELIVER: no parent; the section stays in your own LAUNCH-REPORT.md")
    return EXIT_OK


# =====================================================================================
# 4. The runner and the watcher, rendered from the brief (the agent never edits them)
# =====================================================================================
def images_for(brief: BriefForm, d: Path) -> list[str]:
    if brief.images is not None:
        return ordered(brief.images)
    form = d / "commit-review-form.json"
    if not form.is_file():
        raise Stop(EXIT_INVALID, ["the brief leaves images to the range: run `launch.py commits` first"])
    return ordered(load_json(form, "commit-review-form.json")["derived"]["images_needed"])


def ere_alternation(patterns: list[str]) -> str:
    if not patterns:
        return "a^"
    for p in patterns:
        if "'" in p:
            raise Stop(EXIT_INVALID, [f"a known-red pattern holds a single quote: {p}"])
    return "|".join(f"({p})" for p in patterns)


def windows_hhmm(inst: rules.Instance) -> str:
    return " ".join(f"{w.start_utc[0]:02d}{w.start_utc[1]:02d}-{w.end_utc[0]:02d}{w.end_utc[1]:02d}"
                    for w in inst.windows if w.blocks == "stop")


def runner_values(data: dict, brief: BriefForm, d: Path, exp_full: str, cases: list[str]) -> dict:
    inst = rules.INSTANCES[brief.instance]
    images = images_for(brief, d)
    tag = data["derived"]["tag"]
    flags = []
    if brief.nessie and brief.nessie.force_route:
        flags.append(f"--force-route {brief.nessie.force_route}")
    if brief.nessie and brief.nessie.pace_s:
        flags.append(f"--pace {brief.nessie.pace_s}")
    markers = "\n".join(
        f"  marker {m.container} {shlex.quote(m.file)} '{m.pattern}'" for m in brief.live_markers) or "  :"
    return {
        "TAG": tag, "EXP": exp_full, "INSTANCE": inst.name, "REPO": inst.repo, "IN": inst.in_dir,
        "HOME": inst.home,
        "COMPONENTS": " ".join(images),
        "CONTINUE_ON_FAILURE": " ".join(brief.continue_on_failure),
        "DO_STATIC": "1", "DO_LABS": "1" if (inst.labs_copy and "app" in images) else "0",
        "DO_CI": "1" if brief.ci else "0", "DO_NESSIE": "1" if brief.nessie else "0",
        "CI_FLAGS": "" if brief.ci_nessie_lane else "--no-nessie",
        "SEEK_RESTART_OK": "1" if data["derived"]["seek_restart_ok"] else "0",
        "SEEK_RESTART_GIB": str(rules.SEEK_RESTART_GIB),
        "CASES": " ".join(cases), "NESSIE_FLAGS": " ".join(flags),
        "KNOWN_CI_REDS": ere_alternation(data["derived"]["known_ci_reds"]),
        "KNOWN_HEALTH_REDS": ere_alternation(data["derived"]["known_health_reds"]),
        "WINDOWS": windows_hhmm(inst),
        "DISCARD": " ".join(inst.discard_before_pull),
        "REFRESH_MARKER": "NessieAI/chat_nextseek/src/chat_nextseek/context/.context_db_refresh",
        "LOGNAME_CASES": "\n".join(f"  {c}) echo {rules.LOG_NAME[c]};;" for c in rules.COMPONENTS),
        "SUCCESS_CASES": "\n".join(f"  {c}) echo {shlex.quote(rules.SUCCESS_LINE[c])};;" for c in rules.COMPONENTS),
        "HEALTH_CASES": "\n".join(f"  {shlex.quote(name)}*) echo {comp};;" for name, comp in rules.HEALTH_NEEDS),
        "MARKERS": markers,
    }


def cmd_runner(a) -> int:
    data, brief, d = load_brief(a.brief)
    if brief.instance == "local":
        raise Stop(EXIT_INVALID, ["local never runs a runner: see references/local.md"])
    pf = load_json(d / "preflight.json", "preflight.json (run the preflight first)")
    if pf.get("verdict") != "ok":
        raise Stop(EXIT_STOP, ["STOP: the preflight verdict is not ok; the runner is not rendered"])
    exp_full = pf.get("expected_full") or ""
    if not re.match(r"^[0-9a-f]{40}$", exp_full) or not exp_full.startswith(brief.expected_sha):
        raise Stop(EXIT_INVALID, ["preflight.json holds no full expected sha matching the brief"])
    if brief.images is None:
        rv = d / "commit-review-form.json"
        if rv.is_file() and load_json(rv, "commit-review-form.json")["derived"].get("stops"):
            raise Stop(EXIT_STOP, ["STOP: the commits step found stops; the runner is not rendered"])
    tag = data["derived"]["tag"]
    out = Path(a.out_dir).expanduser() if a.out_dir else d / "runner"
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for i, c in enumerate(brief.nessie.cases if brief.nessie else [], start=1):
        name = f"launch-{tag}-cases-{i}.json"
        (out / name).write_bytes(Path(c.file).expanduser().read_bytes())
        cases.append(name)
    run = render("run.sh.tmpl", runner_values(data, brief, d, exp_full, cases))
    watch = render("watch.sh.tmpl", {"TAG": tag, "LOOPS": "300", "HOME": rules.INSTANCES[brief.instance].home})
    run_p, watch_p = out / f"launch-{tag}-run.sh", out / "watch.sh"
    for p, text in ((run_p, run), (watch_p, watch)):
        if p.exists() and not a.force:
            raise Stop(EXIT_EXISTS, [f"{p} exists; pass --force to re-render"])
        atomic_write(p, text)
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        if r.returncode:
            raise Stop(EXIT_INVALID, [f"bash -n rejected {p}: {r.stderr.strip()}"])
    run_p.chmod(0o700)
    manifest = {"schema": "launch-runner/v1", "rendered_at": iso(utcnow()), "runner": str(run_p),
                "watch": str(watch_p), "cases": [str(out / c) for c in cases],
                "components": images_for(brief, d)}
    write_json(out / "runner.json", manifest, True)
    print(f"OK: wrote {run_p} (bash -n clean), {watch_p}")
    print(f"COMPONENTS: {' '.join(manifest['components']) or 'none'}; CI {'on' if brief.ci else 'off'}; "
          f"Nessie {len(cases)} file(s)")
    return EXIT_OK


# =====================================================================================
# 5. ssh: the only door to a box. One connection at a time, no retry after a failure.
# =====================================================================================
Purpose = Literal["preflight", "read", "start", "watch", "status", "pull"]


def remote_command(inst: rules.Instance, script: str, purpose: str) -> list[str]:
    b64 = base64.b64encode(script.encode()).decode()
    alive = ["-o", "ServerAliveInterval=30"] if purpose == "watch" else []
    if inst.transport == "sudo":
        # the whole remote command is ONE argument: ssh joins its args and the remote shell
        # re-parses them, so an unquoted `bash -lc '...'` silently runs as the login user.
        return ["ssh", "-o", "BatchMode=yes", *alive, inst.ssh_host,
                f"echo {b64} | base64 -d | sudo -n -u {inst.run_as} bash -l"]
    if inst.transport == "direct":
        return ["ssh", "-o", "BatchMode=yes", *alive, inst.ssh_host, f"echo {b64} | base64 -d | bash -l"]
    raise Stop(EXIT_INVALID, [f"{inst.name} has no ssh transport"])


def start_script(inst: rules.Instance, tag: str) -> str:
    run = f"launch-{tag}-run.sh"
    cp = "" if inst.in_dir == inst.home else f"cp {inst.in_dir}/{run} ~/ && "
    return (f"cd ~ && {cp}chmod 700 ~/{run} && "
            f"(tmux has-session -t launch-{tag} 2>/dev/null && echo SESSION_EXISTS || "
            f"tmux new-session -d -s launch-{tag} ~/{run}); sleep 3; tmux ls; cat ~/launch-{tag}.status\n")


def ledger(d: Path, entry: dict) -> None:
    with open(d / "connections.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def ledger_entries(d: Path) -> list[dict]:
    p = d / "connections.jsonl"
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def cmd_ssh(a) -> int:
    data, brief, d = load_brief(a.brief)
    inst = rules.INSTANCES[brief.instance]
    if inst.transport == "none":
        raise Stop(EXIT_INVALID, ["local has no ssh door"])
    tag = data["derived"]["tag"]
    failed = d / ".ssh-failed"
    if failed.exists() and not a.after_failure:
        raise Stop(EXIT_SSH, [f"a connection to {inst.box} already failed in this launch "
                              f"({failed.read_text().strip()}). One try, then report: it is usually the "
                              "VPN or a struggling box. Check the front door over HTTPS, report, and pass "
                              "--after-failure \"<the operator's words>\" only when they clear a retry."])
    if a.purpose == "start":
        start_at = parse_utc(a.now) if a.now else utcnow()
        hits = overlapping_windows(inst, start_at, data["derived"]["estimate"]["minutes"])
        if hits:
            raise Stop(EXIT_WINDOW, ["WINDOW (do not start now):"] + [f"  - {h}" for h in hits])
        pf = load_json(d / "preflight.json", "preflight.json (run the preflight first)")
        taken = parse_utc(pf["box_utc"]) if pf.get("box_utc") else None
        if taken is None or (start_at - taken).total_seconds() > rules.PREFLIGHT_MAX_AGE_MIN * 60:
            raise Stop(EXIT_STOP, [f"the preflight was taken at {pf.get('box_utc')}, more than "
                                   f"{rules.PREFLIGHT_MAX_AGE_MIN} min before this start: run it again "
                                   "(preflight-script, ssh --purpose preflight, preflight), then re-render the runner"])
    if a.purpose == "status":
        watches = [e for e in ledger_entries(d) if e.get("purpose") == "watch"]
        since = watches[-1]["start"] if watches else ""
        reads = [e for e in ledger_entries(d) if e.get("purpose") == "status" and e["start"] > since]
        if len(reads) >= rules.STATUS_READS_PER_WATCH:
            raise Stop(EXIT_SSH, ["one short status read per watch is allowed, and it was used: wait "
                                  "for the watcher, or report it stuck after 30 quiet minutes"])
    # purpose -> the remote script
    if a.purpose == "start":
        script = start_script(inst, tag)
    elif a.purpose == "pull":
        script = f"tar czf - -C ~ launch-{tag} launch-{tag}.status\n"
    elif a.purpose == "status":
        script = f"tail -5 ~/launch-{tag}.status; tmux ls\n"
    else:
        if not a.script:
            raise Stop(EXIT_INVALID, [f"--purpose {a.purpose} needs --script"])
        script = Path(a.script).expanduser().read_text(encoding="utf-8")
    lock_name = ".box-status.lock" if a.purpose == "status" else ".box.lock"
    lock_path = d / lock_name
    lock_path.touch()
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Stop(EXIT_SSH, [f"another connection to {inst.box} from this launch is open "
                              f"({lock_path}); one at a time, never in parallel"])
    try:
        return _run_ssh(a, inst, d, tag, script)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _run_ssh(a, inst: rules.Instance, d: Path, tag: str, script: str) -> int:
    entry = {"purpose": a.purpose, "box": inst.box, "start": iso(utcnow())}
    timeout = rules.SSH_TIMEOUT_S.get(a.purpose, 180) or None
    if a.purpose == "start":
        files = sorted((d / "runner").glob(f"launch-{tag}-*"))
        if not files:
            raise Stop(EXIT_INVALID, ["no rendered runner in the launch dir: run `launch.py runner`"])
        dest = f"{inst.ssh_host}:{inst.in_dir}/"
        cmd = ["scp", "-q", "-o", "BatchMode=yes", *map(str, files), dest]
        if a.dry_run:
            print("DRY:", " ".join(shlex.quote(c) for c in cmd))
        else:
            r = subprocess.run(cmd, timeout=timeout)
            if r.returncode:
                (d / ".ssh-failed").write_text(f"scp exit {r.returncode} at {iso(utcnow())}")
                ledger(d, {**entry, "end": iso(utcnow()), "exit": r.returncode, "step": "scp"})
                raise Stop(EXIT_SSH, [f"scp to {inst.box} failed (exit {r.returncode}); report, do not retry"])
        if inst.transport == "sudo":
            # the copied files land as the login user: chmod them readable before the sudo hop
            cmd = ["ssh", "-o", "BatchMode=yes", inst.ssh_host,
                   f"chmod 644 {inst.in_dir}/launch-{tag}-*; echo "
                   f"{base64.b64encode(start_script(inst, tag).encode()).decode()} | base64 -d | "
                   f"sudo -n -u {inst.run_as} bash -l"]
        else:
            cmd = remote_command(inst, script, a.purpose)
    else:
        cmd = remote_command(inst, script, a.purpose)
    if a.dry_run:
        print("DRY:", " ".join(shlex.quote(c) for c in cmd))
        return EXIT_OK
    out_path = Path(a.out).expanduser() if a.out else None
    if a.purpose == "pull" and not out_path:
        out_path = d / "box.tgz"
    try:
        if out_path:
            with open(out_path, "wb") as f:
                r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=timeout)
        else:
            r = subprocess.run(cmd, stderr=subprocess.PIPE, timeout=timeout)
        code = r.returncode
        err = r.stderr.decode(errors="replace").strip()[-400:]
    except subprocess.TimeoutExpired:
        code, err = 124, f"timed out after {timeout}s"
    entry.update({"end": iso(utcnow()), "exit": code})
    ledger(d, entry)
    if code in (124, 255):
        (d / ".ssh-failed").write_text(f"{a.purpose}: exit {code} at {entry['end']}: {err[:200]}")
        raise Stop(EXIT_SSH, [f"the {a.purpose} connection to {inst.box} failed (exit {code}: {err[:200]}). "
                              "One try, then report. Do not loop."])
    if a.purpose == "pull" and code == 0:
        subprocess.run(["tar", "xzf", str(out_path), "-C", str(d)], check=False)
        print(f"OK: pulled into {d}")
    elif out_path:
        print(f"OK: {a.purpose} output in {out_path} (exit {code})")
    if a.after_failure:
        ledger(d, {"purpose": "note", "after_failure": a.after_failure, "at": iso(utcnow())})
    return EXIT_OK if code == 0 else code


# =====================================================================================
# 6. judge: the pulled evidence -> facts.json, and a report form prefilled from them
# =====================================================================================
STAMP_RE = re.compile(r"\s(\d{2}:\d{2}:\d{2})\s*$")
CASE_RE = re.compile(r"^\[\s*(\d+)/(\d+)\]\s+(\S+)\s+(\S+)\s+(\S+)\s+([\d.]+)s\s+\$([\d.]+)\s+(\S+)(?:\s+<-\s+(.*))?$")
NOT_CONTINUATION = re.compile(r"^(╭|│|╰|FAILED|ERROR|XFAIL|XPASS|PASSED|=)")


def health_reds(text: str) -> list[str]:
    """Every ✗ line with its wrapped continuation lines joined (the runner's awk, in Python)."""
    reds, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^\s+✗ (.*)$", line)
        if m:
            if cur is not None:
                reds.append(cur.rstrip())
            cur = m.group(1)
            continue
        if cur is not None and line and not line[0].isspace() and not NOT_CONTINUATION.match(line):
            cur += line
            continue
        if cur is not None:
            reds.append(cur.rstrip())
        cur = None
    if cur is not None:
        reds.append(cur.rstrip())
    return reds


def classify_red(red: str, pending: set[str], known: list[str]) -> str:
    if red.startswith(rules.NOT_HEALTH):
        return "skip"
    if any(re.search(p, red) for p in known):
        return "known"
    for name, comp in rules.HEALTH_NEEDS:
        if red.startswith(name):
            return f"pending {comp}" if comp in pending else f"stale {comp}"
    return "unexplained"


def parse_status(text: str) -> dict:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    st: dict = {"lines": lines, "all_done": any(ln.startswith("ALL_DONE") for ln in lines),
                "stopped": None, "head": None, "rebuilds": {}, "rollback": {}, "failed": [],
                "ci_exit": None, "ci_result": None, "ci_new_reds": [], "findings": [], "reds": [],
                "nessie": [], "stamps": [], "checks_http": None, "labs": None, "static_exit": None}
    for ln in lines:
        m = STAMP_RE.search(ln)
        body = ln[: m.start()].strip() if m else ln.strip()
        if m:
            st["stamps"].append((m.group(1), body))
        if body.startswith("STOPPED:"):
            st["stopped"] = body[len("STOPPED:"):].strip()
        elif body.startswith("HEAD "):
            st["head"] = body[5:]
        elif mm := re.match(r"^REBUILD (\S+) exit=(\d+)", body):
            st["rebuilds"].setdefault(mm.group(1), {})["exit"] = int(mm.group(2))
        elif mm := re.match(r"^REBUILT (\S+)", body):
            st["rebuilds"].setdefault(mm.group(1), {})["success"] = True
        elif mm := re.match(r"^FAILED (\S+),", body):
            st["rebuilds"].setdefault(mm.group(1), {})["success"] = False
            st["failed"].append(mm.group(1))
        elif mm := re.match(r"^ROLLBACK (\S+) (\S+)", body):
            st["rollback"][mm.group(1)] = mm.group(2)
        elif mm := re.match(r"^RED (\S+) \[([^\]]+)\] (.*)$", body):
            st["reds"].append({"after": mm.group(1), "class": mm.group(2), "text": mm.group(3)})
        elif body.startswith("FINDING "):
            st["findings"].append(body[len("FINDING "):])
        elif mm := re.match(r"^CI exit=(\d+)", body):
            st["ci_exit"] = int(mm.group(1))
        elif body.startswith("CI_RESULT "):
            st["ci_result"] = body[len("CI_RESULT "):]
        elif body.startswith("CI_NEW_REDS "):
            st["ci_new_reds"] = body[len("CI_NEW_REDS "):].split()
        elif mm := re.match(r"^CHECKS CHECK http (\S+)", body):
            st["checks_http"] = mm.group(1)
        elif mm := re.match(r"^STATIC exit=(\d+)", body):
            st["static_exit"] = int(mm.group(1))
        elif body.startswith("LABS "):
            st["labs"] = body[5:]
        elif mm := re.match(r"^NESSIE_START run=(\S+) utc=(\S+)", body):
            st["nessie"].append({"run": mm.group(1), "start_utc": mm.group(2)})
        elif mm := re.match(r"^NESSIE_EXIT run=(\S+) exit=(\d+) utc=(\S+)", body):
            for n in st["nessie"]:
                if n["run"] == mm.group(1):
                    n.update({"exit": int(mm.group(2)), "end_utc": mm.group(3)})
    return st


def step_minutes(st: dict) -> list[dict]:
    """Durations between consecutive status stamps, labelled by the line that closed each."""
    out, prev = [], None
    for hhmmss, body in st["stamps"]:
        t = dt.datetime.strptime(hhmmss, "%H:%M:%S")
        if prev is not None:
            secs = (t - prev).total_seconds()
            if secs < 0:
                secs += 86400
            out.append({"line": body[:90], "end": hhmmss, "minutes": round(secs / 60, 1)})
        prev = t
    return out


def parse_checks(text: str) -> dict:
    ck: dict = {"containers": {}, "images": {}, "markers": [], "mem": {}}
    for ln in text.splitlines():
        if ln.startswith("CHECK http "):
            ck["http"] = ln.split()[2] if len(ln.split()) > 2 else None
        elif ln.startswith("CHECK container "):
            parts = ln.split(" ", 3)
            ck["containers"][parts[2]] = dict(re.findall(r"(\w+)=(\S+)", parts[3] if len(parts) > 3 else ""))
        elif ln.startswith("CHECK boot_markers "):
            ck["boot_markers"] = int(ln.split()[2]) if ln.split()[2].isdigit() else None
        elif ln.startswith("CHECK cc_runner "):
            ck["cc_runner"] = ln[len("CHECK cc_runner "):]
        elif ln.startswith("CHECK labs_line "):
            ck["labs_line"] = ln[len("CHECK labs_line "):]
        elif ln.startswith("CHECK labs_file "):
            ck["labs_file"] = ln[len("CHECK labs_file "):]
        elif ln.startswith("CHECK refresh_marker "):
            ck["refresh_marker"] = ln[len("CHECK refresh_marker "):]
        elif ln.startswith("CHECK sidecar_ops "):
            ck["sidecar_ops"] = ln.split()[2]
        elif ln.startswith("CHECK cc_agent_versions "):
            ck["cc_agent_versions"] = ln[len("CHECK cc_agent_versions "):]
        elif ln.startswith("CHECK memory "):
            ck["memory"] = ln[len("CHECK memory "):]
        elif ln.startswith("IMAGE "):
            parts = ln.split(" ", 2)
            ck["images"][parts[1]] = parts[2] if len(parts) > 2 else ""
        elif ln.startswith("MARKER "):
            f = dict(re.findall(r"(count|container|file)=(\S+)", ln))
            pat = ln.split(" pattern=", 1)[1] if " pattern=" in ln else ""
            ck["markers"].append({"count": int(f.get("count", "0") or 0), "container": f.get("container"),
                                  "file": f.get("file"), "pattern": pat})
        elif ln.startswith("MEM "):
            parts = ln.split(" ", 2)
            ck["mem"][parts[1]] = parts[2] if len(parts) > 2 else ""
    started = ck["containers"].get("nextseek", {}).get("started")
    rm = ck.get("refresh_marker")
    if started and rm and rm != "missing":
        try:
            marker_t = dt.datetime.strptime(rm[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            ck["refresh_after_start"] = marker_t >= parse_utc(started[:19] + "Z")
        except ValueError:
            ck["refresh_after_start"] = None
    return ck


def parse_ci(text: str, known: list[str]) -> dict:
    ids = []
    for ln in text.splitlines():
        m = re.match(r"^(FAILED|ERROR) (\S+)", ln)
        if m:
            tid = m.group(2)
            ids.append({"id": tid, "kind": m.group(1).lower(),
                        "known": any(re.search(p, tid) for p in known)})
    res = None
    for ln in text.splitlines():
        m = re.search(r"CI (passed|failed): (.*)$", ln)
        if m:
            res = {"result": m.group(1), "line": m.group(2).strip()}
    counts = {}
    if res:
        for n, what in re.findall(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?|warnings?)", res["line"]):
            counts[what.rstrip("s") if what.startswith("error") else what] = int(n)
    return {"ran": res is not None, "result": res["result"] if res else None,
            "line": res["line"] if res else None, "counts": counts, "failures": ids,
            "new_reds": [f["id"] for f in ids if not f["known"]]}


def parse_nessie_log(text: str) -> dict:
    cases, prev_cost = [], 0.0
    for ln in text.splitlines():
        m = CASE_RE.match(ln.strip())
        if not m:
            continue
        total = float(m.group(7))
        cases.append({"n": int(m.group(1)), "of": int(m.group(2)), "status": m.group(3),
                      "id": m.group(4), "route": m.group(5), "turn_s": float(m.group(6)),
                      "cost_running_usd": total, "cost_usd": round(total - prev_cost, 4),
                      "elapsed": m.group(8), "failed": (m.group(9) or "").strip() or None})
        prev_cost = total
    gate = next((ln.strip() for ln in text.splitlines() if ln.startswith("GATE:")), None)
    cost = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("cost ")), None)
    return {"cases": cases, "gate": gate, "cost_line": cost}


def cmd_judge(a) -> int:
    data, brief, d = load_brief(a.brief)
    tag = data["derived"]["tag"]
    ev = Path(a.evidence).expanduser() if a.evidence else d / f"launch-{tag}"
    status_p = Path(a.status).expanduser() if a.status else d / f"launch-{tag}.status"
    if not status_p.is_file():
        raise Stop(EXIT_INVALID, [f"no status file at {status_p}: pull the evidence first (ssh --purpose pull)"])
    st = parse_status(status_p.read_text(encoding="utf-8", errors="replace"))
    inst = rules.INSTANCES[brief.instance]
    known_h = data["derived"]["known_health_reds"]
    known_c = data["derived"]["known_ci_reds"]
    comps = (load_json(d / "runner" / "runner.json", "runner.json")["components"]
             if (d / "runner" / "runner.json").is_file() else images_for(brief, d))
    pending = set(comps)
    rebuilds = {}
    for c in comps:
        log = ev / f"{rules.LOG_NAME[c]}.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        pending.discard(c)
        reds = [{"text": r, "class": classify_red(r, pending, known_h)} for r in health_reds(text)]
        reds = [r for r in reds if r["class"] != "skip"]
        rebuilds[c] = {
            "ran": bool(text), "log": str(log),
            "success_line": rules.SUCCESS_LINE[c] in text,
            "exit": st["rebuilds"].get(c, {}).get("exit"),
            "rollback_tag": st["rollback"].get(c),
            "built_from": (st["rollback"].get(c) or "").rsplit("-", 1)[-1] or None,
            "reds": reds,
            "unexplained": [r["text"] for r in reds if r["class"] == "unexplained" or r["class"].startswith("stale")],
        }
    ck_p = ev / "checks.log"
    checks = parse_checks(ck_p.read_text(encoding="utf-8", errors="replace")) if ck_p.is_file() else {}
    for mk, form_mk in zip(checks.get("markers", []), brief.live_markers):
        mk["proves"] = form_mk.proves
    ci_p = ev / "ci.log"
    ci = parse_ci(ci_p.read_text(encoding="utf-8", errors="replace"), known_c) if ci_p.is_file() else {"ran": False}
    if ci.get("ran"):
        ci_text = ci_p.read_text(encoding="utf-8", errors="replace")
        ci["health_reds"] = [{"text": r, "class": classify_red(r, set(), known_h)}
                             for r in health_reds(ci_text)]
        ci["health_reds"] = [r for r in ci["health_reds"] if r["class"] != "skip"]
    nessie = []
    for n in st["nessie"]:
        log = ev / f"nessie-{n['run']}.log"
        parsed = parse_nessie_log(log.read_text(encoding="utf-8", errors="replace")) if log.is_file() else {"cases": []}
        nessie.append({**n, **parsed, "log": str(log)})
    head_ok = bool(st["head"]) and st["head"].split()[0].startswith(brief.expected_sha[:7])
    unexplained = sum(len(r["unexplained"]) for r in rebuilds.values())
    ci_health_bad = [r["text"] for r in ci.get("health_reds", []) if r["class"] not in ("known",)]
    facts = {
        "schema": "launch-facts/v1", "judged_at": iso(utcnow()), "tag": tag, "instance": brief.instance,
        "evidence": str(ev), "status_file": str(status_p),
        "status": {k: st[k] for k in ("all_done", "stopped", "head", "failed", "findings", "reds",
                                      "static_exit", "labs", "ci_exit", "ci_result", "ci_new_reds")},
        "steps": step_minutes(st),
        "rebuilds": rebuilds, "checks": checks, "ci": ci, "nessie": nessie,
        "verdict": {
            "all_done": st["all_done"],
            "stopped": st["stopped"],
            "on_expected_sha": head_ok,
            "every_image_rebuilt": all(r["success_line"] for r in rebuilds.values()),
            "unexplained_rebuild_reds": unexplained,
            "ci_green_apart_from_known": (not ci.get("new_reds")) if ci.get("ran") else None,
            "ci_health_reds": ci_health_bad,
            "live_markers_found": [m["count"] > 0 for m in checks.get("markers", [])],
            "http": checks.get("http"),
        },
    }
    write_json(d / "facts.json", facts, True)
    form_p = d / "report-form.json"
    if form_p.exists() and not a.force:
        print(f"OK: wrote {d / 'facts.json'}; kept the existing {form_p} (pass --force to re-prefill)")
    else:
        write_json(form_p, prefill_report(facts, brief, data), True)
        print(f"OK: wrote {d / 'facts.json'} and a prefilled {form_p}")
    v = facts["verdict"]
    print(f"HEAD on expected sha: {v['on_expected_sha']}; images rebuilt: {v['every_image_rebuilt']}; "
          f"unexplained rebuild reds: {v['unexplained_rebuild_reds']}; CI green apart from known: "
          f"{v['ci_green_apart_from_known']}; stopped: {v['stopped'] or 'no'}")
    return EXIT_OK


# =====================================================================================
# 7. The report form: prefilled mechanics, the agent's judgement, one rendering
# =====================================================================================
StepName = Literal["preflight", "commit_review", "pull", "bedrock-proxy", "app", "cc-agent",
                   "nextseek-sidecar", "collectstatic", "labs", "checks", "ci", "nessie", "evidence"]
StepStatus = Literal["ok", "ok_with_findings", "failed", "stopped", "skipped", "not_in_brief"]
Verdict = Literal["pass", "real", "drift", "policy", "masked", "notrun"]


class Step(Strict):
    step: StepName
    status: StepStatus
    evidence: list[str] = Field(default_factory=list)
    note: Optional[str] = None


class MarkerRow(Strict):
    pattern: str
    file: str
    container: Optional[str] = None
    count: int
    found: bool
    proves: Optional[str] = None
    note: Optional[str] = None


class CiFailure(Strict):
    id: str
    kind: Literal["failed", "error"] = "failed"
    known: bool
    first_error: Optional[str] = None
    caused_by_change: Optional[Literal["yes", "no", "unclear"]] = None


class CiBlock(Strict):
    ran: bool
    result: Optional[Literal["passed", "failed"]] = None
    line: Optional[str] = None
    counts: dict[str, int] = Field(default_factory=dict)
    failures: list[CiFailure] = Field(default_factory=list)
    health_reds: list[dict] = Field(default_factory=list)


class Question(Strict):
    case_id: str
    question: Optional[str] = None
    expected: Optional[str] = None
    reply_key_line: Optional[str] = None
    verdict: Optional[Verdict] = None
    evidence: Optional[str] = None
    route: Optional[str] = None
    time_s: Optional[float] = None
    cost_usd: Optional[float] = None
    harness_status: Optional[str] = None


class NessieBlock(Strict):
    ran: bool
    runs: list[dict] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    cost_note: Optional[str] = None


class Finding(Strict):
    severity: Literal["high", "medium", "low", "info"]
    text: str = Field(min_length=5)
    evidence: list[str] = Field(min_length=1)


class Proposal(Strict):
    kind: Literal["extra_step", "ci_rerun", "rekey", "defect", "ci_check", "rollback", "other"]
    text: str = Field(min_length=5)
    why: str = Field(min_length=5)


class LeftBehind(Strict):
    """What the launch leaves on the box and here, with what should happen to it (the handoff
    skill's inventory, for a launch)."""

    kind: Literal["tmux_session", "box_file", "box_folder", "image_tag", "run_dir", "local_folder"]
    name: str
    disposition: Literal["keep", "clean_up", "running"]
    note: Optional[str] = None


class Headline(Strict):
    verdict: Optional[Literal["shipped", "shipped_with_findings", "stopped", "failed"]] = None
    line: Optional[str] = None


class ReportForm(Strict):
    schema_: str = Field(alias="schema")
    headline: Headline
    summary: Optional[str] = None
    steps: list[Step]
    live_markers: list[MarkerRow] = Field(default_factory=list)
    ci: CiBlock
    nessie: NessieBlock
    findings: list[Finding] = Field(default_factory=list)
    anomalies: list[Finding] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    left_behind: list[LeftBehind] = Field(default_factory=list)


def left_behind(facts: dict, brief: BriefForm, tag: str, launch_dir: str) -> list[dict]:
    inst = rules.INSTANCES[brief.instance]
    items = [{"kind": "tmux_session", "name": f"launch-{tag}", "disposition": "keep",
              "note": "exits by itself after ALL_DONE"},
             {"kind": "box_folder", "name": f"~/launch-{tag}", "disposition": "keep", "note": "the evidence on the box"},
             {"kind": "box_file", "name": f"~/launch-{tag}.status", "disposition": "keep", "note": None},
             {"kind": "box_file", "name": f"{inst.in_dir}/launch-{tag}-*", "disposition": "clean_up",
              "note": "the copied runner and cases files"}]
    for c, r in facts.get("rebuilds", {}).items():
        if r.get("rollback_tag"):
            items.append({"kind": "image_tag", "name": r["rollback_tag"].split(" ")[0], "disposition": "keep",
                          "note": f"the {c} image before this launch: the operator's rollback"})
    for n in facts.get("nessie", []):
        items.append({"kind": "run_dir", "name": f"~/backups/{n['run']}", "disposition": "keep",
                      "note": "copied out of /app/runs, which the next recreate loses"})
    items.append({"kind": "local_folder", "name": launch_dir, "disposition": "keep", "note": "this launch's evidence"})
    return items


def prefill_report(facts: dict, brief: BriefForm, data: dict) -> dict:
    v = facts["verdict"]
    steps = [{"step": "preflight", "status": "ok", "evidence": ["preflight.json"], "note": None},
             {"step": "commit_review", "status": "ok", "evidence": ["commit-review.md"], "note": None},
             {"step": "pull", "status": "ok" if v["on_expected_sha"] else "stopped",
              "evidence": [f"launch-{facts['tag']}.status"], "note": facts["status"]["head"]}]
    for c in rules.COMPONENTS:
        r = facts["rebuilds"].get(c)
        if r is None:
            steps.append({"step": c, "status": "not_in_brief", "evidence": [], "note": None})
            continue
        status = ("ok" if r["success_line"] and not r["unexplained"] else
                  "ok_with_findings" if r["success_line"] else "failed" if r["ran"] else "stopped")
        steps.append({"step": c, "status": status, "evidence": [Path(r["log"]).name], "note": None})
    ran_app = "app" in facts["rebuilds"]
    steps.append({"step": "collectstatic", "status": ("ok" if facts["status"]["static_exit"] == 0 else
                                                      "failed" if facts["status"]["static_exit"] else
                                                      "not_in_brief" if not ran_app else "stopped"),
                  "evidence": ["static.log"] if ran_app else [], "note": None})
    if rules.INSTANCES[brief.instance].labs_copy:
        labs = facts["status"]["labs"] or ""
        steps.append({"step": "labs", "status": "ok" if labs.startswith("copied") else
                      ("not_in_brief" if not ran_app else "failed"), "evidence": ["checks.log"], "note": labs or None})
    steps.append({"step": "checks", "status": "ok" if facts["checks"].get("http") == "200" else
                  ("failed" if facts["checks"] else "stopped"), "evidence": ["checks.log"], "note": None})
    ci = facts["ci"]
    steps.append({"step": "ci", "status": ("not_in_brief" if not brief.ci else "stopped" if not ci.get("ran")
                                           else "ok" if not ci.get("new_reds") else "failed"),
                  "evidence": ["ci.log"] if ci.get("ran") else [], "note": ci.get("line")})
    steps.append({"step": "nessie", "status": ("not_in_brief" if not brief.nessie else
                                               "ok" if facts["nessie"] else "stopped"),
                  "evidence": [Path(n["log"]).name for n in facts["nessie"]], "note": None})
    steps.append({"step": "evidence", "status": "ok", "evidence": [facts["evidence"]], "note": None})
    questions = [{"case_id": c["id"], "question": None, "expected": None, "reply_key_line": None,
                  "verdict": None, "evidence": None, "route": c["route"], "time_s": c["turn_s"],
                  "cost_usd": c["cost_usd"], "harness_status": c["status"]}
                 for n in facts["nessie"] for c in n.get("cases", [])]
    form = {
        "schema": "launch-report-form/v1",
        "headline": {"verdict": None, "line": None},
        "summary": None,
        "steps": steps,
        "live_markers": [{"pattern": m["pattern"], "file": m["file"], "container": m.get("container"),
                          "count": m["count"], "found": m["count"] > 0, "proves": m.get("proves"),
                          "note": None} for m in facts["checks"].get("markers", [])],
        "ci": {"ran": bool(ci.get("ran")), "result": ci.get("result"), "line": ci.get("line"),
               "counts": ci.get("counts", {}),
               "failures": [{"id": f["id"], "kind": f["kind"], "known": f["known"], "first_error": None,
                             "caused_by_change": None} for f in ci.get("failures", [])],
               "health_reds": ci.get("health_reds", [])},
        "nessie": {"ran": bool(facts["nessie"]),
                   "runs": [{k: n.get(k) for k in ("run", "start_utc", "end_utc", "exit", "gate", "cost_line")}
                            for n in facts["nessie"]],
                   "questions": questions, "cost_note": None},
        "findings": [], "anomalies": [], "proposals": [],
        "left_behind": left_behind(facts, brief, facts["tag"], str(Path(facts["evidence"]).parent)),
    }
    # list only evidence that exists: a step the runner never reached has no log to point at
    ev, d = Path(facts["evidence"]), Path(facts["evidence"]).parent
    for s in form["steps"]:
        s["evidence"] = [e for e in s["evidence"] if (ev / e).exists() or (d / e).exists() or Path(e).exists()]
    return form


_OK_CLASS = {"ok": 0, "ok_with_findings": 0, "not_in_brief": 1, "skipped": 1, "failed": 2, "stopped": 2}


def report_problems(form: ReportForm, facts: Optional[dict], brief: BriefForm) -> list[str]:
    p: list[str] = []
    if form.headline.verdict is None or not form.headline.line:
        p.append("headline.verdict and headline.line are required")
    if not form.summary or len(form.summary) < 40:
        p.append("summary: two or three sentences (is the box on the sha with every image rebuilt, is CI "
                 "green apart from known reds, did the Nessie questions show the change)")
    for f in form.ci.failures:
        if not f.known and (not f.first_error or not f.caused_by_change):
            p.append(f"ci.failures {f.id}: a new red needs first_error and caused_by_change")
    if form.nessie.ran:
        for q in form.nessie.questions:
            missing = [k for k in ("question", "expected", "reply_key_line", "verdict", "evidence")
                       if getattr(q, k) in (None, "")]
            if missing:
                p.append(f"nessie.questions {q.case_id}: fill {missing}")
    names = [s.step for s in form.steps]
    if len(names) != len(set(names)):
        p.append("each step appears once")
    if not facts:
        return p
    # the form may explain the facts, never contradict them
    pre = {s["step"]: s for s in prefill_report(facts, brief, {})["steps"]}
    for s in form.steps:
        want = pre.get(s.step)
        if want and _OK_CLASS[s.status] < _OK_CLASS[want["status"]]:
            p.append(f"steps {s.step}: the facts say {want['status']}, the form says {s.status}")
    fids = sorted(f["id"] for f in facts["ci"].get("failures", []))
    if sorted(f.id for f in form.ci.failures) != fids:
        p.append(f"ci.failures must list exactly the failed test ids in ci.log: {fids}")
    known = {f["id"]: f["known"] for f in facts["ci"].get("failures", [])}
    for f in form.ci.failures:
        if f.id in known and f.known != known[f.id]:
            p.append(f"ci.failures {f.id}: known is {known[f.id]} by the known-reds table, not {f.known}")
    fc = facts["ci"].get("counts", {})
    if form.ci.counts != fc:
        p.append(f"ci.counts must equal the CI result line: {fc}")
    for m in form.live_markers:
        fm = next((x for x in facts["checks"].get("markers", []) if x["pattern"] == m.pattern), None)
        if fm is None:
            p.append(f"live marker {m.pattern!r} is not in checks.log")
        elif fm["count"] != m.count or m.found != (fm["count"] > 0):
            p.append(f"live marker {m.pattern!r}: checks.log counts {fm['count']}")
    v = facts["verdict"]
    bad_news = (v["stopped"] or not v["on_expected_sha"] or not v["every_image_rebuilt"]
                or v["unexplained_rebuild_reds"] or v["ci_green_apart_from_known"] is False
                or v["ci_health_reds"] or not all(v["live_markers_found"] or [True])
                or facts["status"]["findings"])
    if form.headline.verdict == "shipped" and bad_news:
        p.append("headline 'shipped' is not allowed: the facts carry a stop, a failed or stale image, an "
                 "unexplained red, a new CI red, a red health line, a missing live marker or a FINDING. "
                 "Use shipped_with_findings, stopped or failed")
    if v["stopped"] and form.headline.verdict not in ("stopped", "failed"):
        p.append(f"the runner stopped ({v['stopped']}): the headline must be stopped or failed")
    if bad_news and not (form.findings or form.anomalies):
        p.append("the facts carry bad news but findings and anomalies are empty")
    have = {x.name for x in form.left_behind}
    for item in left_behind(facts, brief, facts["tag"], str(Path(facts["evidence"]).parent)):
        if item["name"] not in have:
            p.append(f"left_behind: {item['kind']} {item['name']} was dropped; keep it and set its disposition")
    return p


EVIDENCE_FILE = re.compile(r"\.(json|log|md|out|txt|html|tgz|jsonl|sh)$")


def evidence_problems(form: ReportForm, d: Path, tag: str) -> list[str]:
    """Every evidence ref that names a file must exist in the launch folder (or as written)."""
    refs = [(f"steps {s.step}", e) for s in form.steps for e in s.evidence]
    refs += [(f"findings[{i}]", e) for i, f in enumerate(form.findings) for e in f.evidence]
    refs += [(f"anomalies[{i}]", e) for i, f in enumerate(form.anomalies) for e in f.evidence]
    out = []
    for where, ref in refs:
        r = ref.strip().strip("`")
        if " " in r or not (("/" in r) or EVIDENCE_FILE.search(r)):
            continue
        cands = [Path(r).expanduser(), d / r, d / f"launch-{tag}" / r]
        if not any(c.exists() for c in cands):
            out.append(f"{where}: evidence {ref!r} is not in {d} (nor launch-{tag}/)")
    return out


def render_report_md(rep: dict) -> str:
    b, f, fx = rep["brief"], rep["form"], rep.get("facts") or {}
    tag, inst = rep["tag"], b["instance"]
    L = [f"# Launch {inst} at {b['expected_sha'][:8]}: {f['headline']['line']}", "",
         f"**{f['headline']['verdict'].replace('_', ' ')}.** {f['summary']}", ""]
    probs = rep.get("brief_problems") or {}
    if rep.get("brief_verdict", "ok") != "ok":
        L += ["## Why the brief was refused", ""]
        L += [f"- stop: {md(x)}" for x in probs.get("stop", [])]
        L += [f"- window: {md(x)}" for x in probs.get("window", [])]
        L.append("")
    pfr = rep.get("preflight") or {}
    stops = [c for c in pfr.get("checks") or [] if c["verdict"] in ("stop", "waived")]
    if stops:
        L += ["## Preflight rows that stopped or were waived", "", "| Check | Value | Verdict | Rule |", "|---|---|---|---|"]
        L += [f"| {c['id']} | {md(c['value'])} | {c['verdict']} | {md(c['rule'])} |" for c in stops]
        L.append("")
    rb = fx.get("rebuilds", {})
    pf = rep.get("preflight") or {}
    checkout = fx.get("status", {}).get("head") or (
        f"{(pf.get('box_head') or '')[:8]} {pf.get('box_head_subject') or ''} (not moved; from the preflight)"
        if pf.get("box_head") else "unknown (no preflight)")
    L += ["## State", "", "| | |", "|---|---|",
          f"| Instance | {inst} ({rules.INSTANCES[inst].box}) |",
          f"| Checkout | {checkout} |"]
    for c in rules.COMPONENTS:
        r = rb.get(c)
        L.append(f"| {c} | " + (f"built from {r['built_from'] or '?'}, rollback tag `{r['rollback_tag'] or 'none'}`, "
                                f"success line {'yes' if r['success_line'] else 'NO'}" if r else "not rebuilt") + " |")
    L += [f"| Evidence | `{rep['launch_dir']}` |", f"| Reported to | {b.get('parent') or 'self'} |", ""]
    L += ["## Steps", "", "| Step | Status | Evidence | Note |", "|---|---|---|---|"]
    for s in f["steps"]:
        L.append(f"| {s['step']} | {s['status']} | {', '.join('`' + e + '`' for e in s['evidence'])} | {md(s.get('note') or '')} |")
    if fx.get("steps"):
        L += ["", "Timings from the status file (UTC stamps):", "", "| Line | End | Minutes |", "|---|---|---|"]
        for t in fx["steps"]:
            L.append(f"| {md(t['line'])} | {t['end']} | {t['minutes']} |")
    L += ["", "## Live markers", "", "| Pattern | File | Count | Found | Proves | Note |", "|---|---|---|---|---|---|"]
    for m in f["live_markers"] or []:
        L.append(f"| `{md(m['pattern'])}` | `{m['file']}` | {m['count']} | {'yes' if m['found'] else 'NO'} | "
                 f"{md(m.get('proves') or '')} | {md(m.get('note') or '')} |")
    if not f["live_markers"]:
        L.append("| none measured" + (" (the brief named " + ", ".join("`" + m["pattern"] + "`" for m in b["live_markers"])
                                      + ")" if b.get("live_markers") else "") + " | | | | | |")
    reds = [(c, r) for c, rr in rb.items() for r in rr["reds"]]
    if reds:
        L += ["", "## Stack-health reds during the launch", "", "| After | Class | Line |", "|---|---|---|"]
        for c, r in reds:
            L.append(f"| {c} | {r['class']} | {md(r['text'][:200])} |")
    ci = f["ci"]
    L += ["", "## CI", ""]
    why_not = ("the launch stopped before the runner started" if not fx else "the runner stopped first")
    if not ci["ran"]:
        L.append("Not run" + (" (not in the brief)." if not b["ci"] else f" ({why_not})."))
    else:
        L.append(f"`{ci['line']}`. Failures against the known reds:")
        L += ["", "| Test id | Known | First error | Caused by this change |", "|---|---|---|---|"]
        for x in ci["failures"]:
            L.append(f"| `{x['id']}` | {'yes' if x['known'] else 'NO'} | {md(x.get('first_error') or '')} | "
                     f"{x.get('caused_by_change') or ''} |")
        if not ci["failures"]:
            L.append("| none | | | |")
        hr = [r for r in ci.get("health_reds", []) if r["class"] != "known"]
        L.append("")
        L.append("Stack health at the top of CI: " + ("all green apart from the known reds." if not hr else
                                                       "RED: " + "; ".join(md(r["text"][:150]) for r in hr)))
    ns = f["nessie"]
    L += ["", "## Nessie questions", ""]
    if not ns["ran"]:
        L.append("Not run" + (" (not in the brief)." if not b.get("nessie") else f" ({why_not})."))
    else:
        for r in ns["runs"]:
            L.append(f"- `{r.get('run')}` {r.get('start_utc')} to {r.get('end_utc')}, exit {r.get('exit')}; "
                     f"{md(r.get('gate') or '')}; {md(r.get('cost_line') or '')}")
        if ns.get("cost_note"):
            L += ["", ns["cost_note"]]
        counts: dict[str, int] = {}
        for q in ns["questions"]:
            counts[q["verdict"]] = counts.get(q["verdict"], 0) + 1
        L += ["", "Verdicts (by reading every reply; the harness pass rate is a hint): "
              + ", ".join(f"{k} {counts[k]}" for k in ("pass", "real", "masked", "drift", "policy", "notrun") if k in counts), "",
              "| # | Case | Question | Expected | Reply (key line) | Verdict | Evidence | Route | Time s | Cost $ |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for i, q in enumerate(ns["questions"], 1):
            cost = f"{q['cost_usd']:.2f}" if q.get("cost_usd") is not None else "unpriced"
            L.append(f"| {i} | `{q['case_id']}` | {md(q['question'])} | {md(q['expected'])} | {md(q['reply_key_line'])} "
                     f"| **{q['verdict']}** | {md(q['evidence'])} | {q.get('route') or ''} | {q.get('time_s') or ''} | {cost} |")
    for title, key in (("Findings", "findings"), ("Anomalies", "anomalies")):
        L += ["", f"## {title}", ""]
        items = f[key]
        L += [f"- **{x['severity']}**: {x['text']} (evidence: {', '.join('`' + e + '`' for e in x['evidence'])})" for x in items] or ["- none"]
    if f.get("left_behind"):
        L += ["", "## Left behind", "", "| Kind | Name | Disposition | Note |", "|---|---|---|---|"]
        for x in f["left_behind"]:
            L.append(f"| {x['kind']} | `{md(x['name'])}` | {x['disposition'].replace('_', ' ')} | {md(x.get('note') or '')} |")
    L += ["", "## Proposals (not run; the supervisor decides)", ""]
    L += [f"- **{x['kind']}**: {x['text']} Why: {x['why']}" for x in f["proposals"]] or ["- none"]
    cr = rep.get("commit_review")
    L += ["", "## Commit review and proposed CI checks", ""]
    if cr:
        L.append(f"Delivered to {'`' + b['parent'] + '` by SendMessage' if b.get('parent') else 'no parent (kept here)'}. "
                 f"Full section: `commit-review.md`.")
        L.append("")
        L += [ln for ln in render_review_md(cr, BriefForm.model_validate(b), tag).splitlines()[2:]]
    else:
        L.append("Not done (no commit-review.json in the launch folder).")
    return "\n".join(L) + "\n"


def cmd_report(a) -> int:
    data, brief, d = load_brief(a.brief, refused_ok=True)
    raw = load_json(Path(a.form).expanduser() if a.form else d / "report-form.json", "report form")
    form = validate(ReportForm, raw, "report form")
    facts = load_json(d / "facts.json", "facts.json") if (d / "facts.json").is_file() else None
    if facts is None and not a.no_facts:
        raise Stop(EXIT_INVALID, ["no facts.json: run `launch.py judge` (or pass --no-facts for a launch "
                                  "that stopped before any evidence existed)"])
    problems = report_problems(form, facts, brief) + evidence_problems(form, d, data["derived"]["tag"])
    cr = load_json(d / "commit-review.json", "commit-review.json") if (d / "commit-review.json").is_file() else None
    if cr is not None:
        validate(CommitReview, {k: v for k, v in cr.items() if k not in ("validated_at", "deliver_to")},
                 "commit-review.json")
    elif facts is not None:
        problems.append("no commit-review.json: run `launch.py commits`, fill it, and `launch.py review`")
    if problems:
        raise Stop(EXIT_INVALID, ["report form refused:"] + [f"  - {x}" for x in problems])
    pf = load_json(d / "preflight.json", "preflight.json") if (d / "preflight.json").is_file() else None
    rep = {"schema": "launch-report/v1", "written_at": iso(utcnow()), "tag": data["derived"]["tag"],
           "launch_dir": str(d), "brief_verdict": data.get("verdict", "ok"),
           "brief_problems": data.get("problems"), "brief": data["brief"], "derived": data["derived"],
           "form": form.model_dump(mode="json", by_alias=True), "facts": facts, "commit_review": cr,
           "preflight": {k: pf.get(k) for k in ("box_utc", "box_head", "box_head_subject", "verdict", "checks")} if pf else None}
    write_json(d / "launch-report.json", rep, a.force)
    text = render_report_md(rep)
    if (d / "LAUNCH-REPORT.md").exists() and not a.force:
        raise Stop(EXIT_EXISTS, [f"{d / 'LAUNCH-REPORT.md'} exists; pass --force"])
    atomic_write(d / "LAUNCH-REPORT.md", text)
    print(f"OK: wrote {d / 'launch-report.json'} and {d / 'LAUNCH-REPORT.md'}")
    print(f"HEADLINE: {form.headline.verdict}: {form.headline.line}")
    if brief.parent and cr is not None:
        print(f"DELIVER: the commit review went to {brief.parent!r} at the review step; your final "
              "message is LAUNCH-REPORT.md")
    else:
        print("DELIVER: your final message is LAUNCH-REPORT.md")
    return EXIT_OK


# =====================================================================================
# CLI
# =====================================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="deploy skill launch flow: forms, rules and renderers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("brief"); s.add_argument("--form", required=True); s.add_argument("--out-dir")
    s.add_argument("--now", help="UTC time to judge the window against (tests)"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("preflight-script"); s.add_argument("--brief", required=True)
    s = sub.add_parser("preflight"); s.add_argument("--brief", required=True); s.add_argument("--output")
    s = sub.add_parser("commits"); s.add_argument("--brief", required=True); s.add_argument("--repo")
    s.add_argument("--base"); s.add_argument("--head"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("review"); s.add_argument("--brief", required=True); s.add_argument("--form", required=True)
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("runner"); s.add_argument("--brief", required=True); s.add_argument("--out-dir")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("ssh"); s.add_argument("--brief", required=True)
    s.add_argument("--purpose", required=True, choices=["preflight", "read", "start", "watch", "status", "pull"])
    s.add_argument("--script"); s.add_argument("--out"); s.add_argument("--after-failure")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--now")
    s = sub.add_parser("judge"); s.add_argument("--brief", required=True); s.add_argument("--evidence")
    s.add_argument("--status"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("report"); s.add_argument("--brief", required=True); s.add_argument("--form")
    s.add_argument("--no-facts", action="store_true"); s.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    handlers = {"brief": cmd_brief, "preflight-script": cmd_preflight_script, "preflight": cmd_preflight,
                "commits": cmd_commits, "review": cmd_review, "runner": cmd_runner, "ssh": cmd_ssh,
                "judge": cmd_judge, "report": cmd_report}
    try:
        return handlers[a.cmd](a)
    except Stop as e:
        print("\n".join(e.problems), file=sys.stderr)
        return e.code
    except rules.BoxesConfigError as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
