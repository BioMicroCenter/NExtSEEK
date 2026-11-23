"""``/seek/admin/definitions/``: the superuser Definitions page.

One page for the sample type and assay definitions (``dmac.sample_types_context`` and ``dmac.assay_context``), with
today's Attributes, Clades and Assay map editors as tabs. Those three are the old pages, framed with ``?embed=1``
(``samples.sampleAttributes``, ``admin.adminClades``, ``admin.internalAssays``): each is a single-instance editor
with page-global state, so they cannot share one document. The writes go through
``nextseek_api/services/definitions_admin.py``; this view only reads, and reads no graph: connections come from
``sample_type_connections``, which graph_sync's context step keeps.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from django.conf import settings
from django.db import connections
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from nextseek_api.graph_sync import context_derived, sources
from nextseek_api.graph_sync.models_db import SampleTypeConnectionRow
from nextseek_api.services.context_catalog import CLADE_ORDER, UNASSIGNED_CLADE
from nextseek_api.services.definitions_admin import attention, stored_tiers
from seek.models import Sample_types_context

from ..decorators import requires_seek_login_redirect, requires_supervisor

logger = logging.getLogger(__name__)

URL = "/seek/admin/definitions/"
TIERS = (("required", "required_metadata", "Required"), ("standard", "standard_metadata", "Standard"),
         ("possible", "possible_metadata_fields", "Possible"))
ASSAY_SQL = ("SELECT id, assay_name, internal_assay_id, Description AS description, Tags AS tags, "
             "Alternative_Assay_Names AS alternative_names, "
             "Required_Parent_Sample_Types AS Required_Parent_Sample_Types, "
             "Optional_Parent_Sample_Types AS Optional_Parent_Sample_Types, "
             "Children_Sample_Types AS Children_Sample_Types FROM assay_context ORDER BY assay_name, id")


def _rows(sql: str) -> list[dict]:
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cur:
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def _flags(pairs) -> list[dict]:
    """``(name, designed, observed)`` triples folded to one entry per name: D when any is designed, O when any is
    observed."""
    acc = defaultdict(lambda: [False, False])
    for name, d, o in pairs:
        acc[name][0] |= d
        acc[name][1] |= o
    return [{"name": n, "designed": d, "observed": o} for n, (d, o) in sorted(acc.items())]


def load(selected_type: str | None, selected_assay: str | None) -> dict:
    context_rows = list(Sample_types_context.objects.order_by("sample_type").values())
    assay_rows = _rows(ASSAY_SQL)
    internal = _rows("SELECT id, internal_assay_title FROM internal_assays ORDER BY id")
    conns = [{"parent": r.parent_type, "assay": r.assay, "child": r.child_type, "designed": r.designed,
              "observed": r.observed} for r in SampleTypeConnectionRow.objects.using(settings.NEXTSEEK_DATABASE).all()]
    seek_types = sources.sample_types()
    seek_attrs = sources.sample_attributes()
    try:
        needs = attention(context_rows=context_rows, assay_rows=assay_rows, seek_types=seek_types,
                          seek_attributes=seek_attrs, type_clades=sources.type_clades(), internal_assays=internal,
                          connection_rows=conns)
    except Exception:  # noqa: BLE001 - the list is advice; the editors still work without it
        logger.exception("definitions page: the Needs attention list could not be built")
        needs = None

    if needs:   # each item links to the definition row that fixes it, where one exists
        row_id = {}
        for a in assay_rows:
            row_id.setdefault(a["assay_name"], a["id"])
        needs["undesigned"] = [{"assay": a, "id": row_id.get(a), "pairs": pairs}
                               for a, pairs in needs["undesigned"].items()]
        needs["spelling"] = [{**s, "id": row_id.get(s["definition"])} for s in needs["spelling"]]

    rank = {c: i for i, c in enumerate(CLADE_ORDER)}
    groups = defaultdict(list)
    for r in context_rows:
        groups[(r.get("clade") or UNASSIGNED_CLADE).strip() or UNASSIGNED_CLADE].append(r["sample_type"])
    clades = [{"clade": c, "codes": groups[c]} for c in sorted(groups, key=lambda c: (rank.get(c, 99), c))]

    ctx = {"clades": clades, "assays": assay_rows, "needs": needs, "url": URL,
           "counts": {"sample_types": len(context_rows), "assays": len(assay_rows),
                      "needs_types": len(needs["needs_definition"]["sample_types"]) if needs else 0,
                      "needs_assays": len(needs["needs_definition"]["assays"]) if needs else 0,
                      "attention": sum(len(u["pairs"]) for u in needs["undesigned"]) + len(needs["would_drop"])
                      + len(needs["spelling"]) if needs else 0},
           "tab": "assays" if selected_assay else "sample-types",
           # Off on every box at deploy: the live lists, ids and new attributes' tiers wait for it.
           "switch_on": context_derived.writes_columns()}

    row = next((r for r in context_rows if r["sample_type"] == selected_type), None)
    if row:
        ids = [t["id"] for t in seek_types if t["title"] == row["sample_type"]]
        attrs = [a for a in seek_attrs if ids and a["sample_type_id"] == min(ids)]
        titles = list(dict.fromkeys(a["title"].strip() for a in attrs if a["title"].strip()))
        required = {a["title"].strip() for a in attrs if a["required"]}
        stored = stored_tiers(row)
        # Stored titles in their tiers; a SEEK title no tier holds yet joins as new (possible until placed); a stored
        # title SEEK no longer has is pinned (the step drops it). With no SEEK attributes the lists stand as typed.
        fields = [{"title": t, "tier": tier, "seek_required": t in required,
                   "stale": bool(titles) and t not in titles, "new": False} for t, tier in stored.items()]
        fields += [{"title": t, "tier": "required" if t in required else "possible", "seek_required": t in required,
                    "stale": False, "new": True} for t in titles if t not in stored]
        code = row["sample_type"]
        ctx["type"] = {
            "row": row,
            "fields": fields,
            "tiers": TIERS,
            "unbacked": [d for d in (needs["would_drop"] if needs else []) if d["sample_type"] == code],
            "live": [
                ("Parents", _flags((c["parent"], c["designed"], c["observed"]) for c in conns if c["child"] == code)),
                ("Children", _flags((c["child"], c["designed"], c["observed"]) for c in conns if c["parent"] == code)),
                ("Assays in", _flags((c["assay"], c["designed"], c["observed"]) for c in conns if c["child"] == code)),
                ("Assays out", _flags((c["assay"], c["designed"], c["observed"]) for c in conns
                                      if c["parent"] == code)),
            ],
        }
    assay = next((a for a in assay_rows if str(a["id"]) == str(selected_assay)), None)
    if assay:
        ctx["assay"] = {"row": assay,
                        "seen": sorted((c for c in conns if c["assay"] == assay["assay_name"]),
                                       key=lambda c: (c["parent"], c["child"]))}
    return ctx


@ensure_csrf_cookie
@requires_seek_login_redirect(URL)
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def adminDefinitions(request):
    return render(request, "definitions.html", load(request.GET.get("type"), request.GET.get("assay")))
