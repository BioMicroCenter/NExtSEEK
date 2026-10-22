"""upload-reingest's two writing host seams (spec 2026-10-20 3.5).

Called only from inside upload-reingest, after its write gate. Each takes the
op's own request, so SEEK sees the signed-in user on a create.

Neither the assay-registration service nor a reused assay asks SEEK about the
caller's rights: the service only checks that a sample and its assay share a
project, and a reused assay is read from the database. So both seams hold every
assay they hand back or write to inside the caller's projects themselves
(SEC-001), with the scope the batch upload uses (``_caller_project_scope``):
``None`` for a superuser, otherwise the caller's project ids, empty when they
cannot be read.

SEEK can commit a create and still answer with a timeout, a 5xx or a 2xx with
no id (dev's SEEK 504s on POSTs that land). ``create_study_assay`` therefore
looks for an untagged assay of the same title before it creates one and again
after any such answer, and adopts exactly one, so a retry never makes a second.
"""
from __future__ import annotations

import json
import logging

from nextseek_api.services import reingest_lookups

log = logging.getLogger(__name__)

ASSAY_TYPE_URI = "http://jermontology.org/ontology/JERMOntology#Experimental_assay_type"

_UNCONFIRMED = ("SEEK did not confirm that the assay was created, and no such assay was "
                "found in the study afterwards; it is safe to try again")


def _client():
    from nextseek_api.helpers import SeekAPIClient
    return SeekAPIClient()


def _link_model():
    from seek.models import Assays_internal_assays
    return Assays_internal_assays


def _caller_scope(request):
    """The caller's project ids, or None for a superuser. Empty on any failure (fails closed)."""
    from nextseek_api.batch_upload import views as batch_views
    return batch_views._caller_project_scope(request.user)


def _assay_projects(assay_ids: list[int]) -> dict[int, set[int]]:
    """SEEK assay id -> its project ids (assay -> study -> investigation -> projects)."""
    from nextseek_api.assay_registration.resolver import projects_for_assays
    from nextseek_api.batch_upload.db_engine import get_connection

    with get_connection() as conn:
        return projects_for_assays(assay_ids, conn)


def _in_scope_ids(assay_ids, scope) -> set[int]:
    """The assays among ``assay_ids`` the caller may write to. Asks nothing for a superuser
    or an empty scope."""
    ids = sorted({int(a) for a in assay_ids})
    if scope is None:
        return set(ids)
    allowed = set(scope)
    if not allowed or not ids:
        return set()
    projects = _assay_projects(ids)
    return {a for a in ids if projects.get(a, set()) & allowed}


def _require_in_scope(request, assay_id: int, study_id: int) -> None:
    from NessieAI.ns.reingest.upload import AssayCreateRefused

    if assay_id not in _in_scope_ids([assay_id], _caller_scope(request)):
        raise AssayCreateRefused(
            f"assay {assay_id} in study {study_id} is not in one of your projects")


def _sync(delay_s: float = 0) -> None:
    from nextseek_api.graph_sync import hooks
    hooks.enqueue("assay_map", "*", delay_s=delay_s)
    hooks.enqueue("isa", "*", delay_s=delay_s)


def _adopt(request, assay_id: int, house_id: int, study_id: int) -> int:
    """Tag an untagged assay of the study with its house assay and queue the graph."""
    _require_in_scope(request, assay_id, study_id)
    _link_model().objects.create(assay_id=assay_id, internal_assay_id=house_id)
    _sync()
    log.info("upload-reingest adopted untagged assay %s in study %s", assay_id, study_id)
    return assay_id


def _adopt_after_unconfirmed(request, *, study_id: int, assay_title: str, house_id: int) -> int:
    from NessieAI.ns.reingest.upload import AssayCreateRefused

    try:
        found = reingest_lookups.untagged_assay_ids_in_study_strict(study_id, assay_title)
    except RuntimeError as exc:
        log.warning("upload-reingest: could not look for an unconfirmed assay in study %s: %s",
                    study_id, exc)
        raise AssayCreateRefused(_UNCONFIRMED) from exc
    if len(found) == 1:
        return _adopt(request, found[0], house_id, study_id)
    if found:
        raise AssayCreateRefused(
            f"SEEK did not confirm that the assay was created, and study {study_id} now has "
            f"{len(found)} assays titled '{assay_title}'; an administrator must keep one")
    raise AssayCreateRefused(_UNCONFIRMED)


def create_study_assay(request, *, study_id: int, internal_assay_title: str,
                       assay_title: str) -> int:
    from NessieAI.ns.reingest.upload import AssayCreateRefused
    from nextseek_api.graph_sync import hooks

    existing = reingest_lookups.assay_ids_in_study_strict(study_id, internal_assay_title)
    if len(existing) == 1:
        _require_in_scope(request, existing[0], study_id)
        return existing[0]
    if existing:
        raise AssayCreateRefused(
            f"study {study_id} already has {len(existing)} '{internal_assay_title}' assays")
    house_id = reingest_lookups.internal_assay_id_strict(internal_assay_title)
    if house_id is None:
        raise AssayCreateRefused(f"there is no house assay '{internal_assay_title}'")
    # An earlier create SEEK did not confirm may have landed: adopt it, never make a second.
    untagged = reingest_lookups.untagged_assay_ids_in_study_strict(study_id, assay_title)
    if len(untagged) == 1:
        return _adopt(request, untagged[0], house_id, study_id)
    if untagged:
        raise AssayCreateRefused(
            f"study {study_id} already has {len(untagged)} assays titled '{assay_title}'; "
            "an administrator must keep one")
    payload = {"data": {
        "type": "assays",
        "attributes": {"title": assay_title, "assay_class": {"key": "EXP"},
                       "assay_type": {"uri": ASSAY_TYPE_URI}},
        "relationships": {"study": {"data": {"type": "studies", "id": str(study_id)}}},
    }}
    unconfirmed = dict(request=request, study_id=study_id, assay_title=assay_title,
                       house_id=house_id)
    try:
        body, code, _headers, _resp = _client().create_assay(request, payload)
    except Exception as exc:  # noqa: BLE001 -- SEEK may have committed what it did not answer
        log.warning("upload-reingest: SEEK create in study %s raised %s", study_id,
                    type(exc).__name__)
        _sync(hooks.UNCONFIRMED_DELAY_S)
        return _adopt_after_unconfirmed(**unconfirmed)
    code = int(code or 0)
    if code >= 500:
        # Held back, as the assay proxy does: SEEK may have committed it anyway.
        _sync(hooks.UNCONFIRMED_DELAY_S)
        return _adopt_after_unconfirmed(**unconfirmed)
    if not 200 <= code < 300:
        raise AssayCreateRefused(f"SEEK refused to create the assay (status {code})")
    try:
        assay_id = int(json.loads(body or b"{}")["data"]["id"])
    except (KeyError, TypeError, ValueError):
        log.warning("upload-reingest: SEEK answered %s with no assay id in study %s",
                    code, study_id)
        _sync()  # a 2xx is committed, whatever its body says (as the assay proxy has it)
        return _adopt_after_unconfirmed(**unconfirmed)
    _link_model().objects.create(assay_id=assay_id, internal_assay_id=house_id)
    _sync()
    log.info("upload-reingest created assay %s in study %s", assay_id, study_id)
    return assay_id


def _failed_row(pair: dict, code: str, message: str) -> dict:
    return {"sample_uid": pair["parent_uid"], "assay_id": int(pair["assay_id"]),
            "status": "failed", "error": {"code": code, "message": message}}


def _register_chunk(chunk: list[dict], request, counts: dict, rows: list) -> None:
    """Send one chunk to the registration service and merge its counts and rows."""
    from nextseek_api.assay_registration import service
    from nextseek_api.assay_registration.schemas import RegistrationRequest, RegistrationRow

    payload = RegistrationRequest(registrations=[
        RegistrationRow(sample_uid=p["parent_uid"], assay_id=int(p["assay_id"]))
        for p in chunk])
    raw, code = service.register(payload, request)
    if code == 202:
        # Above the synchronous threshold the service only queues a job: nothing is
        # written yet, so no pair may be counted as made. The chunking below keeps
        # every call synchronous; this stays as a defence.
        log.info("upload-reingest: %d parent registrations queued as job %s",
                 len(chunk), (raw or {}).get("job_id"))
        counts["submitted"] += len(chunk)
        counts["failed"] += len(chunk)
        rows.extend(_failed_row(p, "queued", "the registration was queued and will "
                                "finish in the background") for p in chunk)
        return
    body = raw or {}
    for name, value in (body.get("counts") or {}).items():
        counts[name] = int(counts.get(name) or 0) + int(value or 0)
    rows.extend(body.get("rows") or [])


def register_parents(request, pairs: list[dict]) -> dict:
    """Register each (parent, assay) pair whose assay is in the caller's projects.

    Pairs go to the service in chunks no larger than its synchronous threshold
    (``ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD``), so it writes them now instead of
    queueing a job. Every pair not sent, out of scope or still queued comes back as
    a failed row and in ``counts.failed``, so the reply never counts it as made."""
    from django.conf import settings

    allowed = _in_scope_ids([p["assay_id"] for p in pairs], _caller_scope(request))
    sent = [p for p in pairs if int(p["assay_id"]) in allowed]
    dropped = [p for p in pairs if int(p["assay_id"]) not in allowed]
    counts = {"submitted": 0, "written": 0, "already_present": 0, "skipped": 0, "failed": 0}
    rows: list = []
    size = max(1, int(settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD))
    for start in range(0, len(sent), size):
        _register_chunk(sent[start:start + size], request, counts, rows)
    if dropped:
        log.warning("upload-reingest: %d parent registrations outside the caller's projects "
                    "were not sent", len(dropped))
        counts["submitted"] += len(dropped)
        counts["failed"] += len(dropped)
        rows.extend(_failed_row(p, "permission_denied", "not in your projects")
                    for p in dropped)
    return {"counts": counts, "rows": rows}
