"""Read-only catalog pages over the curated context tables.

Login is required; project membership is not. Sample types and assays are
schema definitions shared by every project, so there is nothing project-scoped
to expose. Same rule and the same reasoning as `templatesList` in assets.py.

HTTP only. Everything these views know comes from
`nextseek_api.services.context_catalog`, so no query lives here.
"""

import logging

from django.http import Http404
from django.shortcuts import render

from nextseek_api.services.context_catalog import (
    CLADE_ORDER,
    UNASSIGNED_CLADE,
    assay_slug_for_name,
    load_assay,
    load_assays,
    load_sample_type,
    load_sample_types,
)

from ..decorators import requires_seek_login_redirect

logger = logging.getLogger(__name__)


def _ordered_clades(buckets):
    """CLADE_ORDER first, then anything unmapped, then Unassigned last.

    Built from CLADE_ORDER rather than from the data, so the page reads in
    pipeline order rather than alphabetically. A clade the order does not know
    still appears, ahead of Unassigned, so an unexpected value degrades visibly
    instead of vanishing.
    """
    extra = [name for name in buckets
             if name not in CLADE_ORDER and name != UNASSIGNED_CLADE]
    return CLADE_ORDER + sorted(extra) + [UNASSIGNED_CLADE]


def _group_by_clade(entries):
    """[{"clade": name, "entries": [...]}, ...] in pipeline order, empties dropped."""
    buckets = {}
    for entry in entries:
        buckets.setdefault(entry.clade, []).append(entry)
    return [{"clade": name, "entries": buckets[name]}
            for name in _ordered_clades(buckets) if buckets.get(name)]


@requires_seek_login_redirect('/seek/sampletypes/')
def sampleTypesList(request):
    """Every curated sample type, grouped by clade."""
    entries = load_sample_types()
    return render(request, 'sampleTypesList.html', {
        'groups': _group_by_clade(entries),
        'total': len(entries),
    })


@requires_seek_login_redirect('/seek/sampletypes/')
def sampleTypeDetail(request, code):
    """One sample type. 404 for a code with no curated row."""
    entry = load_sample_type(code)
    if entry is None:
        raise Http404(f"No curated context for sample type {code!r}")
    return render(request, 'sampleTypeDetail.html', {
        'entry': entry,
        # Assay names are prose in the curator column; the slug is the link
        # target and also folds the two hyphenation variants onto one page.
        'assay_parents': [{'name': n, 'slug': assay_slug_for_name(n)}
                          for n in entry.assay_parents],
        'assay_children': [{'name': n, 'slug': assay_slug_for_name(n)}
                           for n in entry.assay_children],
    })


def _assay_group_key(entry):
    """Group assays by the clade they consume, falling back to Unassigned.

    An assay entry can carry two rows and they need not agree, so the first row
    that names a parent clade wins. Grouping on the parent side rather than the
    child side because the list reads as "what can I run on what I have".
    """
    for row in entry.rows:
        if row.parent_clade:
            return row.parent_clade
    return UNASSIGNED_CLADE


@requires_seek_login_redirect('/seek/assays/')
def assaysList(request):
    """Every curated assay, grouped by the clade it consumes."""
    entries = load_assays()
    buckets = {}
    for entry in entries:
        buckets.setdefault(_assay_group_key(entry), []).append(entry)
    return render(request, 'assaysList.html', {
        'groups': [{'clade': name, 'entries': buckets[name]}
                   for name in _ordered_clades(buckets) if buckets.get(name)],
        'total': len(entries),
    })


@requires_seek_login_redirect('/seek/assays/')
def assayDetail(request, slug):
    """One assay. Two rows when the name appears twice; neither is preferred."""
    entry = load_assay(slug)
    if entry is None:
        raise Http404(f"No curated context for assay {slug!r}")
    return render(request, 'assayDetail.html', {
        'entry': entry,
        # Computed here rather than with a template {% if %} on a length, so the
        # notice text and the condition live in one place.
        'is_split': len(entry.rows) > 1,
    })
