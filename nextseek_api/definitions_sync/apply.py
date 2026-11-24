"""``sync.plan``'s writes on this box, each through the path that already writes it and queues its graph sync:
the attribute API (planner, executor, sample metadata rewrite, jobs), the Definitions page's functions
(``definitions_admin``), the Assay map's and the Clades page's table helpers. Each item stands alone: a refusal is
reported and the run goes on. ``dry_run`` asks the attribute API for its own preview and writes nothing.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections, transaction

from nextseek_api.graph_sync import hooks

JOB_POLL_S = 30


class Refused(Exception):
    """The command cannot act as the named login; nothing was written."""


def actor(login: str):
    """The operator's SEEK person, for the attribute API's job provenance: a Django superuser bound to exactly one
    SEEK person with SEEK's admin role (the same binding the API's token path proves)."""
    from nextseek_api.attributes.auth import AuthenticatedSeekPerson, _query_admin_role

    user = get_user_model().objects.filter(username=login).first()
    if user is None or not user.is_superuser:
        raise Refused(f"{login!r} is not a Django superuser on this box")
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute("SELECT people.id FROM people JOIN users ON users.person_id = people.id WHERE users.login = %s "
                    "LIMIT 2", [login])
        rows = cur.fetchall()
    if len(rows) != 1:
        raise Refused(f"{login!r} is not bound to exactly one SEEK person")
    if not _query_admin_role(int(rows[0][0])):
        raise Refused(f"{login!r} has no SEEK admin role")
    return AuthenticatedSeekPerson(person_id=int(rows[0][0]), django_user_id=int(user.pk), login=login,
                                   scheme="command")


def _attribute_call(kind: str, targets: list[dict], dry_run: bool, person) -> tuple[dict, int]:
    from nextseek_api.attributes.schemas import CREATE_REQUEST_ADAPTER, PATCH_REQUEST_ADAPTER
    from nextseek_api.attributes.service import AttributeServices

    adapter = CREATE_REQUEST_ADAPTER if kind == "create" else PATCH_REQUEST_ADAPTER
    payload = adapter.validate_python({"targets": targets, "dry_run": dry_run})
    request = SimpleNamespace(_attribute_seek_identity=person)
    return AttributeServices.build().mutate(kind, payload, dry_run, request)


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


def attributes(p: dict, *, dry_run: bool, person, wait_s: float, log) -> dict:
    """One patch request for every type (definition-only: no sample is rewritten), then one create request for
    every type (each type's samples rewritten once, as a job when they pass the API's threshold)."""
    report = {}
    patch_targets = [{"sample_type": t, "attributes": w["patches"]} for t, w in sorted(p["attributes"].items())
                     if w["patches"]]
    create_targets = [{"sample_type": t, "attributes": [{k: v for k, v in c.items() if v is not None}
                                                        for c in w["creates"]]}
                      for t, w in sorted(p["attributes"].items()) if w["creates"]]
    for kind, targets in (("patch", patch_targets), ("create", create_targets)):
        if not targets:
            continue
        body, code = _attribute_call(kind, targets, dry_run, person)
        report[kind] = _summary(body, code)
        if kind == "patch" and not dry_run and code >= 400:
            log("attribute patches failed; creates not sent, so positions cannot shift under them")
            break
        if body.get("job_id") and not dry_run and wait_s > 0:
            log(f"attribute {kind} job {body['job_id']} accepted; waiting up to {int(wait_s)} s")
            result = wait_for_job(body["job_id"], wait_s, log)
            report[kind]["result"] = None if result is None else _summary(result, result.get("http_status", 0))
    return report


def internal_assays(titles: list[str], log) -> dict:
    from dmac.dbtable_internalassays import DBtable_internalassays

    done, errors = [], []
    db = DBtable_internalassays()
    for title in titles:
        try:
            db.new(internal_assay_title=title)
            done.append(title)
        except Exception as exc:   # noqa: BLE001 -- reported, the next title goes on
            errors.append({"title": title, "error": str(exc)})
    return {"created": len(done), "errors": errors}


def clades(rows: list[dict]) -> dict:
    from dmac.dbtable_sampletypesclades import DBtable_stc
    from seek.models import Clades, Sample_types

    stc, done, errors = DBtable_stc(), 0, []
    for r in rows:
        try:
            st_id = Sample_types.objects.filter(title=r["sample_type"]).order_by("id").values_list("id", flat=True)[0]
            clade_id = Clades.objects.get(title=r["clade"]).id
            if stc.tablemodel.objects.filter(sample_type_id=st_id).exists():
                stc.update(st_id, clade_id)
            else:
                stc.tablemodel.objects.create(sample_type_id=st_id, clade_id=clade_id)
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
    from nextseek_api.models import DefinitionSampleTypeUpdateRequest, DefinitionTiers
    from nextseek_api.services import definitions_admin as da

    done, errors = [], []
    items = [(c["sample_type"], {k: v for k, v in c.items() if k != "sample_type"}, True) for c in work["create"]]
    items += [(code, cells, False) for code, cells in sorted(work["update"].items())]
    for code, cells, new in items:
        try:
            row = da.create_sample_type(code) if new else da.sample_type_row(code)
            text = {f: cells[col] for f, col in da.SAMPLE_TYPE_TEXT.items() if col in cells}
            if {"required_metadata", "standard_metadata"} & cells.keys():
                text["tiers"] = DefinitionTiers(**_tiers(cells, row))
            if text:
                da.update_sample_type(row["sample_type"], DefinitionSampleTypeUpdateRequest(**text))
            other = {c: cells[c] for c in ("repository_attributes", "sampletype_file_link") if c in cells}
            if other:
                da._write_sample_type(row["id"], other)
            done.append(code)
        except Exception as exc:   # noqa: BLE001 -- the page's refusals (Invalid, FileExistsError) included
            errors.append({"sample_type": code, "error": str(exc)})
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
            else:
                with connections[settings.NEXTSEEK_DATABASE].cursor() as cur:
                    cur.execute("SELECT id, assay_name FROM assay_context WHERE assay_name = %s", [name])
                    ids = [i for i, n in cur.fetchall() if n == name]   # _ci collation: keep exact names only
                if len(ids) != 1:
                    raise LookupError(f"{len(ids)} assay_context rows are named {name!r}")
            api = {f: cells[c] for c, f in _ASSAY_API.items() if c in cells}
            if api:
                da.update_assay(ids[0], DefinitionAssayUpdateRequest(**api))
            other = {c: v for c, v in cells.items() if c not in _ASSAY_API}
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
    # The paths above that queue nothing themselves (the page functions, the table helpers) are caught here,
    # as their own pages do after a save.
    for kind in ("catalog", "assay_map", "context"):
        hooks.enqueue(kind, "*")
    report["enqueued"] = ["catalog", "assay_map", "context"]
    return report
