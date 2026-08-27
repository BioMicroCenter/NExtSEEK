"""Project list and project detail pages."""

import logging

from dmac.dbtable_clades import DBtable_clades
from ..dbtable_projects import DBtable_projects
from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades as DBtable_stc
from django.http import HttpResponseRedirect
from ..models import Projects
from ..seekdb import SeekDB
from itertools import groupby
import pandas as pd
import re
from django.shortcuts import render
from django.conf import settings
from ..decorators import requires_seek_login_redirect
from ..decorators import verifySuperUser

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

        cladedb = DBtable_clades()
        
        clade_data = cladedb.getCladeProjectStats(project_id)
        values = set(map(lambda x: x['title'], clade_data))
        clade_data = [[y for y in clade_data if y['title']==x] for x in values]

        try:
            published_stats = pd.read_excel(PUBLISH_STATS_FILE, sheet_name=None)
            published_stats = published_stats[project.title]
            published_stats = published_stats.fillna(0)
            published_stats['Published'] = published_stats['Published'].astype(int)
            type_published_counts = dict(zip(published_stats['Data Types'],
                                             published_stats['Published']))
            for clade_list in clade_data:
                for clade in clade_list:
                    data_type = clade['st_group']
                    published_count = type_published_counts.get(data_type, 0)
                    clade['published'] = published_count
                    
        except Exception:
            for clade_list in clade_data:
                for clade in clade_list:
                    clade['published'] = 0

        for group in clade_data:
            for item in group:
                item['total'] = sum(i['count'] for i in group)
                item['published_total'] = sum(i['published'] for i in group)

        clade_data.sort(key=lambda x: x[0]['order'])

        return render(request, 'projectPage.html', {'id': project.id,
                                                    'title': re.sub("-|_", " ", project.title),
                                                    'description': project.description.replace("\r", "\n"),
                                                    'seek_public_url': settings.SEEK_PUBLIC_URL,
                                                    'avatar_id': project.avatar_id,
                                                    'clade_data': clade_data,})
