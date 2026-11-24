"""Copy production's definitions (sample types, attributes, the context tables, internal assays, clades, attribute
meanings) onto this box. Dry run unless ``--apply``; a second ``--apply`` finds nothing to do.

    manage.py definitions_sync --source PATH|-  [--seek-login NAME] [--project TITLE] [--json]
    manage.py definitions_sync --source PATH|-  --apply --seek-login NAME [--wait S] [--json]

``--source`` is production's bundle as ``nextseek_api/definitions_sync/dump.py`` prints it, or
``{"prod": <bundle>, "hold": [...], "project": "<title>"}`` with the hold list (cells that keep this box's value, each
``{"table", "key", "column"}``) and the project; ``-`` reads it from stdin. This box is read in process with the same function.

The dry run prints the plan, every difference it leaves on purpose, the attribute API's own preview of its writes
(with ``--seek-login``) and, for each SEEK sample type this box lacks, the body to ``POST /nextseek_api/sample_types/``
as the operator (``--project`` names the project it joins here). Those types are never written by this command:
SEEK keeps a permission lookup per type that only SEEK's own create fills. Run again after posting them and their
attributes, clade and context row follow.

Exit status: 0 done (or nothing to do), 1 a write was refused or failed (the report says which), 2 refused before
any write (bad source, login not a superuser bound to one SEEK admin).
"""
from __future__ import annotations

import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections

from nextseek_api.definitions_sync import apply as applier
from nextseek_api.definitions_sync import dump, sync


def read_box() -> dict:
    def q(sql, params=()):
        with connections[settings.NEXTSEEK_DATABASE].cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    return dump.bundle(q, settings.DATABASES[settings.NEXTSEEK_DATABASE]["NAME"],
                       settings.DATABASES[settings.SEEK_DATABASE]["NAME"])


def project_id(title: str | None) -> str | None:
    """This box's id for the project titled exactly ``title`` (MySQL's _ci match narrowed in Python)."""
    if not title:
        return None
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute("SELECT id, title FROM projects WHERE title = %s", [title])
        ids = [i for i, t in cur.fetchall() if t == title]
    return str(ids[0]) if len(ids) == 1 else None


def type_payloads(p: dict, project: str | None) -> list[dict]:
    """The body of each ``POST /nextseek_api/sample_types/``: the type, its text, its title attribute, the project
    (``project`` is its id here). Checked against the proxy's own request model when complete."""
    from nextseek_api.models import SampleTypeCreateRequest

    out = []
    for t in p["seek_types"]:
        ta = t["title_attribute"]
        attrs = [] if ta is None else [{"title": ta["title"], "sample_attribute_type": {"title": ta["sample_attribute_type"]},
                                         "required": ta["required"], "is_title": True, "pos": 1,
                                         **({"description": ta["description"]} if ta["description"] else {})}]
        body = {"data": {"type": "sample_types",
                         "attributes": {"title": t["title"], "sample_attributes": attrs,
                                        **({"description": t["description"]} if t["description"] else {})},
                         "relationships": {"projects": {"data": [{"id": project or "<project id>",
                                                                  "type": "projects"}]}}}}
        if project and ta is not None:
            SampleTypeCreateRequest.model_validate(body)
        out.append(body)
    return out


class Command(BaseCommand):
    help = "Copy production's definitions onto this box (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="production's bundle (dump.py), or - for stdin")
        parser.add_argument("--apply", action="store_true", help="write; without it nothing is written")
        parser.add_argument("--seek-login", metavar="NAME", help="the operator's login: a Django superuser bound to "
                                                                 "one SEEK person with SEEK's admin role")
        parser.add_argument("--project", metavar="TITLE", help="the project new SEEK sample types join on this box")
        parser.add_argument("--wait", type=float, default=4 * 3600, metavar="S",
                            help="seconds to wait for an attribute job before reporting it as still running")
        parser.add_argument("--json", action="store_true", help="the full report as JSON on stdout")

    def handle(self, *args, **o):
        def log(msg):
            self.stderr.write(msg)

        try:
            raw = json.load(sys.stdin if o["source"] == "-" else open(o["source"], encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"refused: cannot read --source: {exc}")
            sys.exit(2)
        prod, hold = (raw["prod"], raw.get("hold", [])) if "prod" in raw else (raw, [])
        project = o["project"] or raw.get("project")   # in the envelope, a title with spaces needs no shell quoting
        if "tables" not in prod:
            log("refused: --source holds no bundle (no 'tables'): make it with nextseek_api/definitions_sync/dump.py")
            sys.exit(2)
        person = None
        if o["seek_login"]:
            try:
                person = applier.actor(o["seek_login"])
            except applier.Refused as exc:
                log(f"refused: {exc}")
                sys.exit(2)
        if o["apply"] and person is None:
            log("refused: --apply needs --seek-login")
            sys.exit(2)

        plan = sync.plan(prod, read_box(), hold)
        report = {"mode": "apply" if o["apply"] else "dry_run", "prod_exported_at": prod.get("exported_at"),
                  "plan": plan, "seek_type_payloads": type_payloads(plan, project_id(project))}
        failed = False
        if o["apply"]:
            report["applied"] = applier.apply(plan, person=person, wait_s=o["wait"], log=log)
            failed = _failed(report["applied"])
            after = sync.plan(prod, read_box(), hold)
            report["after"] = {"counts": after["counts"], "waiting": after["waiting"]}
        elif person is not None and plan["attributes"]:
            report["preview"] = applier.attributes(plan, dry_run=True, person=person, wait_s=0, log=log)

        if o["json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, default=str))
        else:
            self.stdout.write(_text(report))
        sys.exit(1 if failed else 0)


def _failed(applied: dict) -> bool:
    """Any refusal: a row the page functions refused, an attribute request answered 4xx, or a 207 with errors."""
    for part in applied.values():
        if not isinstance(part, dict):
            continue
        if part.get("errors"):
            return True
        for name in ("text", "rules", "create"):
            r = part.get(name)
            if r and (r.get("http_status", 200) >= 400 or r.get("errors") or (r.get("result") or {}).get("errors")):
                return True
    return False


def _text(r: dict) -> str:
    p = r["plan"]
    lines = [f"definitions_sync {r['mode']} (production read {r['prod_exported_at']})", "", "actions:"]
    lines += [f"  {k}: {v}" for k, v in p["counts"].items() if v]
    if not sync.action_count(p):
        lines.append("  none")
    w = p["waiting"]
    if p["seek_types"]:
        lines.append(f"SEEK sample types to create as the operator (POST /nextseek_api/sample_types/): "
                     f"{', '.join(t['title'] for t in p['seek_types'])}; then run again for their "
                     f"{sum(w['attributes'].values())} other attributes, clades and context rows")
    if p["blocked"]:
        lines.append(f"blocked ({len(p['blocked'])}): " + "; ".join(f"{'/'.join(b['key'])}: {b['reason']}"
                                                                     for b in p["blocked"]))
    lines.append(f"held (dev keeps its value): {len(p['held'])}")
    e = p["expected"]
    lines.append("left on purpose: " + ", ".join(
        [f"dev only {t} {len(v)}" for t, v in e["dev_only"].items()]
        + [f"production only {t} {len(v)}" for t, v in e["prod_only"].items()]
        + [f"derived context cells {len(e['derived_cells'])}", f"case-only names {len(e['case_only'])}"]
        + [f"SEEK's own {k} {v}" for k, v in e["seek_own"].items()]))
    for key in ("preview", "applied"):
        if key in r:
            lines.append(f"{key}: {json.dumps(r[key], default=str)[:4000]}")
    if "after" in r:
        lines.append(f"after: {json.dumps(r['after'], default=str)}")
    return "\n".join(lines) + "\n"
