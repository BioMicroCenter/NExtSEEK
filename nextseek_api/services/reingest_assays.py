"""upload-reingest's two writing host seams (spec 2026-10-20 3.5).

Called only from inside upload-reingest, after its write gate. Each takes the
op's own request, so SEEK sees the signed-in user and the registration service
checks that user's project rights.
"""
from __future__ import annotations

import json
import logging

from nextseek_api.services import reingest_lookups

log = logging.getLogger(__name__)

ASSAY_TYPE_URI = "http://jermontology.org/ontology/JERMOntology#Experimental_assay_type"


def _client():
    from nextseek_api.helpers import SeekAPIClient
    return SeekAPIClient()


def _link_model():
    from seek.models import Assays_internal_assays
    return Assays_internal_assays


def create_study_assay(request, *, study_id: int, internal_assay_title: str,
                       assay_title: str) -> int:
    from NessieAI.ns.reingest.upload import AssayCreateRefused

    existing = reingest_lookups.assay_ids_in_study_strict(study_id, internal_assay_title)
    if len(existing) == 1:
        return existing[0]
    if existing:
        raise AssayCreateRefused(
            f"study {study_id} already has {len(existing)} '{internal_assay_title}' assays")
    house_id = reingest_lookups.internal_assay_id_strict(internal_assay_title)
    if house_id is None:
        raise AssayCreateRefused(f"there is no house assay '{internal_assay_title}'")
    payload = {"data": {
        "type": "assays",
        "attributes": {"title": assay_title, "assay_class": {"key": "EXP"},
                       "assay_type": {"uri": ASSAY_TYPE_URI}},
        "relationships": {"study": {"data": {"type": "studies", "id": str(study_id)}}},
    }}
    body, code, _headers, _resp = _client().create_assay(request, payload)
    if not 200 <= int(code or 0) < 300:
        raise AssayCreateRefused(f"SEEK refused to create the assay (status {code})")
    try:
        assay_id = int(json.loads(body or b"{}")["data"]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssayCreateRefused("SEEK created the assay but did not say its id") from exc
    _link_model().objects.create(assay_id=assay_id, internal_assay_id=house_id)
    from nextseek_api.graph_sync import hooks
    hooks.enqueue("assay_map", "*")
    hooks.enqueue("isa", "*")
    log.info("upload-reingest created assay %s in study %s", assay_id, study_id)
    return assay_id


def register_parents(request, pairs: list[dict]) -> dict:
    from nextseek_api.assay_registration import service
    from nextseek_api.assay_registration.schemas import RegistrationRequest, RegistrationRow

    payload = RegistrationRequest(registrations=[
        RegistrationRow(sample_uid=p["parent_uid"], assay_id=int(p["assay_id"])) for p in pairs])
    body, _code = service.register(payload, request)
    return body
