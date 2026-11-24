"""``sync.plan``'s writes on this box, each through the path that already writes it and queues its graph sync:
the attribute API (planner, executor, sample metadata rewrite, jobs), the Definitions page's functions
(``definitions_admin``), the Assay map's and the Clades page's table helpers. Each item stands alone: a refusal is
reported and the run goes on. ``dry_run`` asks the attribute API for its own preview and writes nothing.
"""
from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections, transaction

from nextseek_api.graph_sync import hooks

JOB_POLL_S = 30


class Refused(Exception):
    """The command cannot act as the named login; nothing was written."""


def actor(login: str):
    """The operator's SEEK person, for the attribute API's job provenance: a Django superuser (the API's own admin
    gate) bound to exactly one SEEK person by login (the binding the API's token path proves)."""
    from nextseek_api.attributes.auth import AuthenticatedSeekPerson

    user = get_user_model().objects.filter(username=login).first()
    if user is None or not user.is_superuser:
        raise Refused(f"{login!r} is not a Django superuser on this box")
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute("SELECT people.id FROM people JOIN users ON users.person_id = people.id WHERE users.login = %s "
                    "LIMIT 2", [login])
        rows = cur.fetchall()
    if len(rows) != 1:
        raise Refused(f"{login!r} is not bound to exactly one SEEK person")
    return AuthenticatedSeekPerson(person_id=int(rows[0][0]), django_user_id=int(user.pk), login=login,
                                   scheme="command")


def _attribute_call(kind: str, targets: list[dict], dry_run: bool, person) -> tuple[dict, int]:
    from pydantic import ValidationError

    from nextseek_api.attributes.schemas import CREATE_REQUEST_ADAPTER, PATCH_REQUEST_ADAPTER
    from nextseek_api.attributes.service import AttributeServices

    adapter = CREATE_REQUEST_ADAPTER if kind == "create" else PATCH_REQUEST_ADAPTER
    try:
        payload = adapter.validate_python({"targets": targets, "dry_run": dry_run})
    except ValidationError as exc:   # the API's own 422: nothing sent
        return {"errors": [{"code": "request_validation_error", "message": e["msg"],
                            "field": ".".join(str(x) for x in e["loc"])} for e in exc.errors(include_url=False)]}, 422
    request = SimpleNamespace(_attribute_seek_identity=person)
    return AttributeServices.build().mutate(kind, payload, dry_run, request)


STALE_AFTER = timedelta(hours=6)


def running_jobs(now=None) -> tuple[list[str], list[str]]:
    """Unfinished attribute jobs, split into live ones (a lease still held, or made in the last six hours: a second
    create now would plan the same attributes again) and stale ones (lease long expired, nothing beating: a crashed
    run the recovery loop never closed). Only live ones hold the sync back; stale ones are reported."""
    from django.utils import timezone

    from nextseek_api.attributes.models_db import AttributeMutationJob

    now = now or timezone.now()
    live, stale = [], []
    for j, state, lease, made in (AttributeMutationJob.objects.filter(terminal_result__isnull=True)
                                  .values_list("job_id", "state", "lease_expires_at", "created_at")):
        alive = (lease is not None and lease > now) or (made is not None and now - made < STALE_AFTER)
        (live if alive else stale).append(f"{j} ({state}, made {made:%Y-%m-%d %H:%M} UTC)" if made else f"{j} ({state})")
    return live, stale


def wait_for_job(job_id, timeout_s: float, log) -> dict | None:
    """The job's terminal result, or None when ``timeout_s`` passed first (the job goes on in the worker)."""
    from nextseek_api.attributes.service import AttributeServices

    services, deadline = AttributeServices.build(), time.monotonic() + timeout_s
    while True:
        job = services.get_job_object(job_id)
        if job.terminal_result is not None:
            return job.terminal_result
        if time.monotonic() > deadline:
            return None
        log(f"  attribute job {job_id}: {job.state}")
        time.sleep(JOB_POLL_S)


def _summary(body: dict, code: int) -> dict:
    out = {"http_status": code, "mode": body.get("mode"), "overall_status": body.get("overall_status"),
           "counts": body.get("counts"), "job_id": body.get("job_id")}
    errors = [{"sample_type": o.get("sample_type_title"), **e} for o in body.get("outcomes", []) for e in o.get("errors", [])]
    errors += body.get("errors", [])
    if errors:
        out["errors"] = errors
    return {k: v for k, v in out.items() if v is not None}


RULE_FIELDS = ("required", "sample_attribute_type", "is_title", "unit", "sample_controlled_vocab", "linked_sample_type")


def requests_for(p: dict) -> list[tuple[str, str, list[dict]]]:
    """The attribute API requests, in order: text and positions (definition-only, no sample rewritten), then the
    rule changes on their own (a refusal fails its whole sample type, so it must not take the text with it), then
    the creates (each type's samples rewritten once, a job past the API's threshold)."""
    text, rules, creates = [], [], []
    for t, w in sorted(p["attributes"].items()):
        tx = [{"attribute": x["attribute"], "changes": {k: v for k, v in x["changes"].items() if k not in RULE_FIELDS}}
              for x in w["patches"]]
        rl = [{"attribute": x["attribute"], "changes": {k: v for k, v in x["changes"].items() if k in RULE_FIELDS}}
              for x in w["patches"]]
        if any(x["changes"] for x in tx):
            text.append({"sample_type": t, "attributes": [x for x in tx if x["changes"]]})
        if any(x["changes"] for x in rl):
            rules.append({"sample_type": t, "attributes": [x for x in rl if x["changes"]]})
        if w["creates"]:
            creates.append({"sample_type": t, "attributes": [{k: v for k, v in c.items() if v is not None}
                                                             for c in w["creates"]]})
    return [(n, k, tg) for n, k, tg in (("text", "patch", text), ("rules", "patch", rules), ("create", "create", creates))
            if tg]


def attributes(p: dict, *, dry_run: bool, person, wait_s: float, log) -> dict:
    report = {}
    if not dry_run:
        busy, stale = running_jobs()
        if stale:
            report["stale_jobs"] = stale
            log(f"stale attribute jobs, not waited for (their leases expired long ago): {', '.join(stale)}")
        if busy:
            log(f"attribute jobs still running ({', '.join(busy)}): attribute writes skipped; run again when they end")
            return {**report, "skipped": "attribute jobs still running", "jobs": busy}
    for name, kind, targets in requests_for(p):
        body, code = _attribute_call(kind, targets, dry_run, person)
        report[name] = _summary(body, code)
        if name == "text" and not dry_run and code >= 400:
            log("attribute text and position patches failed; creates not sent, so positions cannot shift under them")
            break
        if body.get("job_id") and not dry_run and wait_s > 0:
            log(f"attribute {name} job {body['job_id']} accepted; waiting up to {int(wait_s)} s")
            result = wait_for_job(body["job_id"], wait_s, log)
            report[name]["result"] = None if result is None else _summary(result, result.get("http_status", 0))
    return report


def internal_assays(titles: list[str], log) -> dict:
    """As the Assay map's save does (``DBtable_internalassays.new``): one row per title, through its model."""
    from seek.models import Internal_assays

    done, errors = [], []
    for title in titles:
        try:
            Internal_assays(internal_assay_title=title).save()
            done.append(title)
        except Exception as exc:   # noqa: BLE001 -- reported, the next title goes on
            errors.append({"title": title, "error": str(exc)})
    return {"created": len(done), "errors": errors}


def _exact_id(model, field: str, value: str):
    """The lowest id whose ``field`` equals ``value`` exactly: MySQL's _ci match narrowed in Python."""
    ids = sorted(i for i, v in model.objects.filter(**{field: value}).values_list("id", field) if v == value)
    if not ids:
        raise LookupError(f"no {model.__name__} row with {field} {value!r}")
    return ids[0]


def clades(rows: list[dict]) -> dict:
    """As the Clades page does (``DBtable_stc.update``, ``syncSampleTypes``): one row per sample type, by model."""
    from seek.models import Clades, Sample_types, Sample_types_clades

    done, errors = 0, []
    for r in rows:
        try:
            st_id = _exact_id(Sample_types, "title", r["sample_type"])
            clade_id = _exact_id(Clades, "title", r["clade"])
            row = Sample_types_clades.objects.filter(sample_type_id=st_id).first()
            if row is None:
                Sample_types_clades.objects.create(sample_type_id=st_id, clade_id=clade_id)
            else:
                row.clade_id = clade_id
                row.save()
            done += 1
        except Exception as exc:   # noqa: BLE001
            errors.append({**r, "error": str(exc)})
    return {"written": done, "errors": errors}


def meanings(rows: list[dict]) -> dict:
    done = 0
    with transaction.atomic(using=settings.NEXTSEEK_DATABASE), connections[settings.NEXTSEEK_DATABASE].cursor() as cur:
        for r in rows:
            cur.execute("UPDATE sample_attributes_unique SET meaning = %s WHERE sample_type = %s AND field_name = %s",
                        [r["meaning"], r["sample_type"], r["field_name"]])
            if cur.rowcount == 0:
                cur.execute("INSERT INTO sample_attributes_unique (field_name, sample_type, meaning) VALUES (%s, %s, %s)",
                            [r["field_name"], r["sample_type"], r["meaning"]])
            done += 1
    return {"written": done}


def _tiers(prod_cells: dict, row: dict) -> dict:
    """Production's required and standard lists; every other title the row lists stays, as possible."""
    from nextseek_api.services.context_catalog import parse_list
    from nextseek_api.services.definitions_admin import stored_tiers

    req = parse_list(prod_cells.get("required_metadata", row.get("required_metadata")))
    std = [t for t in parse_list(prod_cells.get("standard_metadata", row.get("standard_metadata"))) if t not in req]
    rest = [t for t in stored_tiers(row) if t not in req and t not in std]
    return {"required": req, "standard": std, "possible": rest}


def context_sample_types(work: dict) -> dict:
    """Through the Definitions page's functions: the text, then the tiers in their own call (a refused tier list
    must not take the text with it), then the columns the page does not edit. A new row also gets this box's
    ``sampletype_id`` (what the context step would set; a NULL id leaves the row unjoined)."""
    from nextseek_api.models import DefinitionSampleTypeUpdateRequest, DefinitionTiers
    from nextseek_api.services import definitions_admin as da
    from seek.models import Sample_types

    done, errors = [], []
    items = [(c["sample_type"], {k: v for k, v in c.items() if k != "sample_type"}, True) for c in work["create"]]
    items += [(code, cells, False) for code, cells in sorted(work["update"].items())]
    for code, cells, new in items:
        step = "row"
        try:
            row = da.create_sample_type(code) if new else da.sample_type_row(code)
            if row is None:
                raise LookupError(f"no sample_types_context row {code!r}")
            if new:
                da._write_sample_type(row["id"], {"sampletype_id": _exact_id(Sample_types, "title", code)})
            step = "text"
            text = {f: cells[col] for f, col in da.SAMPLE_TYPE_TEXT.items() if col in cells}
            if text:
                da.update_sample_type(row["sample_type"], DefinitionSampleTypeUpdateRequest(**text))
            step = "other"
            other = {c: (cells[c] or None) for c in ("repository_attributes", "sampletype_file_link") if c in cells}
            if other:
                da._write_sample_type(row["id"], other)
            step = "tiers"
            if {"required_metadata", "standard_metadata"} & cells.keys():
                row = da.sample_type_row(code)
                da.update_sample_type(row["sample_type"],
                                      DefinitionSampleTypeUpdateRequest(tiers=DefinitionTiers(**_tiers(cells, row))))
            done.append(code)
        except Exception as exc:   # noqa: BLE001 -- the page's refusals (Invalid, FileExistsError) included
            errors.append({"sample_type": code, "step": step, "error": str(exc)})
    return {"written": done, "errors": errors}


_ASSAY_API = {"Description": "description", "Tags": "tags", "Alternative_Assay_Names": "alternative_names",
              "Required_Parent_Sample_Types": "required_parents", "Optional_Parent_Sample_Types": "optional_parents",
              "Children_Sample_Types": "children"}


def context_assays(work: dict) -> dict:
    from nextseek_api.models import DefinitionAssayUpdateRequest
    from nextseek_api.services import definitions_admin as da

    done, errors = [], []
    items = [(c["assay_name"], {k: v for k, v in c.items() if k != "assay_name"}, True) for c in work["create"]]
    items += [(name, cells, False) for name, cells in sorted(work["update"].items())]
    for name, cells, new in items:
        try:
            if new:
                ids = [da.create_assay(name)["id"]]
                # this box's internal assay id, as the context step would set it: a NULL id gives no Assay node
                from seek.models import Internal_assays
                da._write_assay(ids[0], {"internal_assay_id": _exact_id(Internal_assays, "internal_assay_title", name)})
            else:
                with connections[settings.NEXTSEEK_DATABASE].cursor() as cur:
                    cur.execute("SELECT id, assay_name FROM assay_context WHERE assay_name = %s", [name])
                    ids = [i for i, n in cur.fetchall() if n == name]   # _ci collation: keep exact names only
                if len(ids) != 1:
                    raise LookupError(f"{len(ids)} assay_context rows are named {name!r}")
            api = {f: cells[c] for c, f in _ASSAY_API.items() if c in cells}
            if api:
                da.update_assay(ids[0], DefinitionAssayUpdateRequest(**api))
            other = {c: (v or None) for c, v in cells.items() if c not in _ASSAY_API}
            if other:
                da._write_assay(ids[0], other)
            done.append(name)
        except Exception as exc:   # noqa: BLE001
            errors.append({"assay_name": name, "error": str(exc)})
    return {"written": done, "errors": errors}


def apply(p: dict, *, person, wait_s: float, log) -> dict:
    """Every write, in the order each depends on: internal assays before the assay rows that name them,
    attributes before the tiers that list them, then the graph rows once."""
    report = {}
    try:
        if p["internal_assays"]:
            report["internal_assays"] = internal_assays(p["internal_assays"], log)
        if p["attributes"]:
            report["attributes"] = attributes(p, dry_run=False, person=person, wait_s=wait_s, log=log)
        if p["clades"]:
            report["clades"] = clades(p["clades"])
        if p["meanings"]:
            report["meanings"] = meanings(p["meanings"])
        if p["context_sample_types"]["create"] or p["context_sample_types"]["update"]:
            report["context_sample_types"] = context_sample_types(p["context_sample_types"])
        if p["context_assays"]["create"] or p["context_assays"]["update"]:
            report["context_assays"] = context_assays(p["context_assays"])
    finally:
        # The paths above that queue nothing themselves (the page functions, the table helpers) are caught here,
        # as their own pages do after a save; whatever landed before a failure is caught too.
        for kind in ("catalog", "assay_map", "context"):
            hooks.enqueue(kind, "*")
        report["enqueued"] = ["catalog", "assay_map", "context"]
    return report
