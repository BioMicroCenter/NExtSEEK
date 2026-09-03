"""Project list and project detail pages."""

import logging

from dmac.dbtable_clades import DBtable_clades
from ..dbtable_projects import DBtable_projects
from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades as DBtable_stc
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from ..models import Projects, Projects_samples
from ..seekdb import SeekDB
from itertools import groupby
import pandas as pd
import re
from django.shortcuts import render
from django.conf import settings
from django.views.decorators.clickjacking import xframe_options_sameorigin

from ..decorators import requires_seek_login_redirect
from ..decorators import verifySuperUser

from nextseek_api.services.context_catalog import (
    CLADE_ORDER, UNASSIGNED_CLADE, load_project_context, load_sample_types,
)
from nextseek_api.services.project_connections import (
    connection_rows, connections_html, project_bundles, types_in_use,
)

from .shared import PUBLISH_STATS_FILE

logger = logging.getLogger(__name__)

@requires_seek_login_redirect('/seek/projects/')
def projects(request):
    seekdb = request.seekdb

    projectsdb = DBtable_projects()
    current_user = seekdb.getCurrentUser() or {}
    user_projects = (
        current_user.get('data', {})
        .get('relationships', {})
        .get('projects', {})
        .get('data', [])
    )
    user_project_ids = [project.get('id') for project in user_projects if project.get('id') is not None]

    if verifySuperUser(request) == 1:
        projects = list(Projects.objects.all().values('id', 'title', 'avatar_id'))
    else:
        projects = list(Projects.objects.filter(id__in=user_project_ids).values('id', 'title', 'avatar_id'))

    for project in projects:
        try:
            stats = projectsdb.sample_count(project['id'])
            stats.update(projectsdb.files_count(project['id']))
        except Exception:
            logger.exception("Failed to build project stats for project_id=%s", project.get('id'))
            stats = {'sample_count': 0, 'sop_count': 0, 'df_count': 0}
        project['stats'] = stats

    try:
        stcdb = DBtable_stc()
        clade_rows = stcdb.getAllCounts() or []
        clade_rows = sorted(clade_rows, key=lambda row: (row.get('title') or '', row.get('st_group') or ''))
        clade_data = {k: list(v) for k, v in groupby(clade_rows, lambda x: x.get('title') or 'Uncategorized')}
    except Exception:
        logger.exception("Failed to build clade data for projects page")
        clade_data = {}

    for k, group in clade_data.items():
        total = sum((item.get('count') or 0) for item in group)
        for item in group:
            item['total'] = total

    # Order the clade summary by pipeline order (Source, Processed, Raw, Analyzed),
    # not alphabetically; unknown clades follow, Unassigned/Uncategorized last.
    def _clade_rank(name):
        if name in CLADE_ORDER:
            return (0, CLADE_ORDER.index(name), name)
        if name in (UNASSIGNED_CLADE, 'Uncategorized'):
            return (2, 0, name)
        return (1, 0, name)
    clade_data = {k: clade_data[k] for k in sorted(clade_data, key=_clade_rank)}

    return render(request, 'projectsList.html', {'projects': projects,
                                                 'clade_data': clade_data,
                                                 'seek_public_url': settings.SEEK_PUBLIC_URL})

def project_page(request, project_id):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)

    if not user_seek['status']:
        url_redirect = f"/login/?next=/seek/projects/{project_id}"
        return HttpResponseRedirect(url_redirect) 
    else:
        if verifySuperUser(request) == 1:
            admin = True
        else:
            admin = False

        user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
        user_project_ids = list(map(lambda x: int(x['id']), user_projects))
        user_in_project = int(project_id) in user_project_ids

        if admin is False and user_in_project is False:
                data = {'msg': 'You are not in this project', 'status': 0, 'link': ''}
                return render(request, 'error.html', {'data': data})

        project = Projects.objects.get(id=project_id)

        # Every one of these is independently soft. The graph being down costs
        # the diagram and the derived bundles; a missing projects_context row
        # costs the enriched header; neither costs the page.
        rows = connection_rows(project.id)
        known_codes = {e.code for e in load_sample_types()}
        used = [c for c in types_in_use(rows) if c in known_codes]
        bundles_all = project_bundles(project.id, rows, known_codes)

        # KPI counts are cheap COUNT()s. The full per-type breakdown (a heavy
        # aggregation over every sample) is deferred to /seek/projects/<id>/samples/.
        try:
            samples_total = Projects_samples.objects.filter(project_id=project.id).count()
        except Exception:
            samples_total = 0
        try:
            # files_count returns {'sop_count': N, 'df_count': N}; the tile wants data files.
            files_total = DBtable_projects().files_count(project.id).get('df_count')
        except Exception:
            files_total = None
        kpis = {
            "samples": samples_total,
            "data_files": files_total,
            "types": len(used),
        }

        return render(request, 'projectPage.html', {'id': project.id,
                                                    'title': re.sub("-|_", " ", project.title),
                                                    'description': project.description.replace("\r", "\n"),
                                                    'seek_public_url': settings.SEEK_PUBLIC_URL,
                                                    'avatar_id': project.avatar_id,
                                                    'ctx': load_project_context(project.id),
                                                    'kpis': kpis,
                                                    'bundles': bundles_all[:8],
                                                    'bundles_all': bundles_all,
                                                    'types_in_use': used,})


def _project_clade_data(project_id, project_title):
    """Per-clade, per-type sample counts for one project, grouped and sorted by
    clade order. Fail-soft to [] so a stats failure never blanks the page.

    Returns a list of groups; each group is a list of per-type dicts carrying
    'title' (clade), 'st_group' (type), 'count', 'published', 'total',
    'published_total', 'order'.

    Cached per project (1h): the underlying query aggregates every sample in the
    project (tens of thousands), so the /samples/ modal must not re-run it on
    every open. Counts change on upload, a weekly event at most.
    """
    from django.core.cache import cache
    cache_key = "projclade:%s" % project_id
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        clade_data = DBtable_clades().getCladeProjectStats(project_id)
        titles = set(x['title'] for x in clade_data)
        clade_data = [[y for y in clade_data if y['title'] == t] for t in titles]
        try:
            published_stats = pd.read_excel(PUBLISH_STATS_FILE, sheet_name=None)[project_title].fillna(0)
            published_stats['Published'] = published_stats['Published'].astype(int)
            pub = dict(zip(published_stats['Data Types'], published_stats['Published']))
            for group in clade_data:
                for item in group:
                    item['published'] = pub.get(item['st_group'], 0)
        except Exception:
            for group in clade_data:
                for item in group:
                    item['published'] = 0
        for group in clade_data:
            for item in group:
                item['total'] = sum(i['count'] for i in group)
                item['published_total'] = sum(i['published'] for i in group)
        clade_data.sort(key=lambda x: x[0]['order'])
        cache.set(cache_key, clade_data, 3600)
        return clade_data
    except Exception:
        logger.exception("clade data failed for project %s", project_id)
        return []


@requires_seek_login_redirect()
def project_samples(request, project_id):
    """The per-type sample counts for one project, as a standalone full-view page
    (opened as a modal from the project page, or visited directly). Membership
    gated exactly like project_connections; the counts table is rebuilt clean so
    the first row of each clade group left-aligns and the all-zero Published
    column is dropped.
    """
    if not _may_see_project(request, project_id):
        return HttpResponseForbidden("You are not in this project")
    project = Projects.objects.filter(id=project_id).first()
    title = re.sub("-|_", " ", str(project.title)) if project else "Project"
    clade_data = _project_clade_data(project_id, project.title if project else "")
    has_published = any(item.get('published') for group in clade_data for item in group)
    return render(request, "project_samples.html", {
        "id": project_id, "title": title,
        "clade_data": clade_data, "has_published": has_published,
    })


def _may_see_project(request, project_id) -> bool:
    """Whether this caller may view this project. Superuser, or a member.

    Mirrors the gate in project_page. is_superuser via verifySuperUser, never
    is_staff: dmac/views.py:80,97 sets is_staff on every SEEK user at login.
    """
    if verifySuperUser(request) == 1:
        return True
    try:
        user_projects = request.seekdb.getCurrentUser()['data']['relationships']['projects']['data']
    except Exception:
        logger.exception("could not resolve caller projects; denying project_id=%s", project_id)
        return False
    return int(project_id) in [int(p['id']) for p in user_projects]


# Django's XFrameOptionsMiddleware is enabled with no X_FRAME_OPTIONS setting,
# so the default DENY applies and blocks this document even in a SAME-ORIGIN
# frame. Without this decorator the project page shows a broken-document icon
# where the diagram should be, and nothing is logged: the refusal happens in the
# browser, so the response is a clean 200 and CI's status sweep sees nothing
# wrong. sameorigin, not exempt: the frame is only ever ours.
@xframe_options_sameorigin
@requires_seek_login_redirect()
def project_connections(request, project_id):
    """The connection diagram for one project, as a standalone HTML document.

    Served on its own route and pulled into the project page with an iframe
    rather than inlined. rows_to_html returns a complete document that loads
    three CDN scripts, so it is an iframe payload and not a fragment; serving it
    separately also keeps the page HTML small, lets the browser cache the frame,
    and means a slow graph query cannot slow the page down.

    The superuser-only connections ENDPOINT is untouched. This runs the same
    functions in-process, behind the same membership check project_page applies.
    """
    if not _may_see_project(request, project_id):
        return HttpResponseForbidden("You are not in this project")

    project = Projects.objects.filter(id=project_id).first()
    title = f"{re.sub('-|_', ' ', project.title)} sample flow" if project else "Sample flow"

    html = connections_html(project_id, title=title)
    if not html:
        # A 200 with an explanation, not a 404: the route resolved and the
        # project exists. An iframe showing "no data" is the intended degraded
        # state, and CI declares 200 for exactly this reason.
        return HttpResponse(
            '<!doctype html><meta charset="utf-8">'
            '<body style="margin:0;font:13px system-ui;color:#6b7280;'
            'display:flex;align-items:center;justify-content:center;height:100vh">'
            'No sample-type connections recorded for this project.</body>',
            content_type="text/html; charset=utf-8",
        )
    return HttpResponse(html, content_type="text/html; charset=utf-8")
