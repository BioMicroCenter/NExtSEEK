"""Supervisor-only pages: retrieval, clades and internal assays."""

import logging

from dmac.dbtable_assaysinternalassays import DBtable_assaysinternalassays
from dmac.dbtable_clades import DBtable_clades
from dmac.dbtable_internalassays import DBtable_internalassays
from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades as DBtable_stc
from dmac.vocab_resolver import suggest as vocab_suggest
from neo4j import GraphDatabase
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
import MySQLdb
from ..seekdb import SeekDB
import datetime
import json
from ..models import Assays_internal_assays, Clades, Internal_assays, Sample_types_clades
from ..responses import json_response
import os
import pandas as pd
from django.shortcuts import render
from ..decorators import requires_seek_login
from ..decorators import requires_seek_login_redirect
from ..decorators import requires_supervisor
from django.conf import settings
import simplejson
from ..decorators import verifySuperUser
from nextseek_api.services.sample_workbook import write_samples_workbook

from .shared import DOWNLOAD_DIRECTORY, SEEK_DATABASE

logger = logging.getLogger(__name__)

def adminRetrieveSamples(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
    user_project_ids = map(lambda x: x['id'], user_projects)

    if verifySuperUser(request) == 1:
        admin = True
    else:
        admin = False

    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        return json_response(msg, status, docurl)
    else:
        if request.method == "POST":
            logger.debug(f"REQUEST: {request.POST.keys()}")
            uids = request.POST.get('retrieval_uids').strip().split()
            children_uids = get_children_uids(uids, user_project_ids, admin)

            datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = 'download-samples-' + datenow + '.xlsx'
            downloadfile = DOWNLOAD_DIRECTORY + filename

            sample_retrieval_data(children_uids, downloadfile)

            with open(downloadfile, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-excel")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(downloadfile)
                return response
        else:
            return render(request, "admin_retrieval.html")

def get_children_uids(sample_uids, user_project_ids, admin):
    db = settings.DATABASES[SEEK_DATABASE]
    NEO4J_DATABASE = settings.NEO4J_DATABASE
    with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
        r,s,k = driver.execute_query("""
		UNWIND $sample_uids AS sample_uid
        MATCH (s:Sample {uuid: sample_uid})
        MATCH parents=(s)-[:DERIVED_FROM*0..]->(parent)
        MATCH children=(s)<-[:DERIVED_FROM*0..]-(child)
        RETURN collect(DISTINCT s.uuid) + collect(DISTINCT parent.uuid) + collect(DISTINCT child.uuid) AS uuids
        """,
        sample_uids=sample_uids,
        database_=NEO4J_DATABASE['NAME'])
        uids = r[0]['uuids']

    db = settings.DATABASES[SEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()

    # Bound, never inlined: a quote in a uuid or a project id broke out of the
    # literal. Second copy of #78 -- the twin in dbtable_sample.py:906 was fixed
    # by feaa816, this one was outside that scope. Shape mirrors the view-layer
    # prior art at nextseek_api/views.py:829-852 (7698848). The uuids are not
    # request data but r[0]['uuids'] read back out of Neo4j above, so the taint
    # is second-order; user_project_ids comes from SEEK's getCurrentUser().
    # Only the schema name, from settings, is still interpolated.
    uid_placeholders = ', '.join(['%s'] * len(uids))

    if admin:
        query = f"""
        SELECT id,sample_type_id,uuid,json_metadata
        FROM {db["NAME"]}.samples
        WHERE uuid IN ({uid_placeholders})
        """
        params = list(uids)
    else:
        # user_project_ids is a single-pass map() (admin.py's caller), so it is
        # consumed here and only on the branch that needs it. The sentinel keeps
        # the statement valid, and matching nothing, when the caller has no
        # mapped projects; it used to emit `IN ()`, a MySQL syntax error.
        scoped_project_ids = [str(pid) for pid in user_project_ids] or ['']
        project_placeholders = ', '.join(['%s'] * len(scoped_project_ids))
        query = f"""
        SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
        FROM {db["NAME"]}.samples s
        JOIN {db["NAME"]}.projects_samples ps
        ON s.id = ps.sample_id
        WHERE s.uuid IN ({uid_placeholders}) AND ps.sample_id = s.id AND ps.project_id IN ({project_placeholders})
        """
        params = list(uids) + scoped_project_ids

    cursor.execute(query, params)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    samples_retrieved_df = pd.DataFrame(rows, columns=columns)

    cursor.close()
    conn.close()
    return samples_retrieved_df

def parse_json_metadata(metadata_series):
    return metadata_series.apply(lambda x: json.loads(x) if isinstance(x, str) else {})

def parse_children_uids(children_uids):
    children_uids['json_metadata'] = parse_json_metadata(children_uids['json_metadata'])

    metadata_df = pd.json_normalize(children_uids['json_metadata'])
    metadata_df = metadata_df.loc[:, ~metadata_df.columns.duplicated()]

    final_df = pd.concat([children_uids[['uuid']], metadata_df], axis=1)
    final_df.replace("", pd.NA, inplace=True)
    final_df.dropna(axis=1, how='all', inplace=True)

    return final_df

def sample_retrieval_data(children_uids, output):
    # Sheet layout, README included, is owned by
    # nextseek_api.services.sample_workbook so it cannot drift per call path.
    write_samples_workbook(parse_children_uids(children_uids), output)

@requires_seek_login_redirect('/seek/samples/attributes/')
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def adminClades(request):
    cladedb = DBtable_clades()
    stcdb = DBtable_stc()
    stc = simplejson.dumps(stcdb.getAllWithTitles(), default=str)
    
    return render(request,"clades.html", {'clades': list(cladedb.getAll()), 'stc': stc})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to perform this action.')
def cladesSyncSampleTypes(request):
    stcdb = DBtable_stc()
    stcdb.syncSampleTypes()
    
    return HttpResponse({})

# ---------------------------------------------------------------------------
# The association workbench: a second response envelope, and why it is second.
#
# ``..responses.json_envelope`` is the house AJAX shape -- ``msg``/``status``/
# ``link``, a contract with the jQuery under ``templates/``. The workbench
# needs two more keys: ``updated`` (how many records actually committed) and
# ``errors`` (which ones did not, and why), because
# ``static/js/custom/ns-vocab-workbench.js`` reads both to tell a partial save
# apart from a clean one. Adding them to ``json_envelope`` would push two keys
# nobody else reads into all 27 of its callers and change their key order, which
# ``responses.py`` pins down deliberately. So the two shapes stay separate, in
# the spirit of that module's own note about ``seek/dbtable_sample.py`` opting
# out. Neither is a migration target for the other.
#
# ``_wb_guard`` is likewise a deliberate second copy of what
# ``@requires_seek_login`` + ``@requires_supervisor`` do. Those decorators
# answer with ``json_response``, i.e. the three-key house envelope, so a
# workbench endpoint wearing them would reject with a body missing the very two
# keys the workbench JS reads. Rejections and partial successes have to arrive
# in one shape. Do not "tidy" these six views onto the decorators without
# teaching the decorators an envelope parameter first.
# ---------------------------------------------------------------------------

def _wb_envelope(status, msg, updated=0, errors=None):
    """The one response shape every workbench endpoint returns."""
    return JsonResponse({'status': status, 'msg': msg,
                         'updated': updated, 'errors': errors or []})


def _wb_records(request):
    """Records from a JSON body. Raises ValueError on anything malformed."""
    payload = json.loads(request.body.decode('utf-8') or '{}')
    if not isinstance(payload, dict):
        raise ValueError('request body must be a JSON object')
    records = payload.get('records')
    if not isinstance(records, list):
        raise ValueError('records must be a list')
    return records


def _wb_guard(request, action):
    """Shared auth gate. Returns an envelope to return early, or None to proceed."""
    user_seek = SeekDB(None, None, None).getSeekLogin(request, False)
    if not user_seek['status']:
        return _wb_envelope(0, user_seek.get('err', 'Not signed in'))
    if verifySuperUser(request) != 1:
        return _wb_envelope(0, f'You do not have permission to {action}.')
    return None


class _WbRowError(Exception):
    """One record is unusable. Its siblings are unaffected."""


def _wb_batch(records, apply_row, noun, verb='Saved'):
    """Apply apply_row to each record; commit what works, report what does not.

    ``verb`` is the past participle the messages are built from ('Saved',
    'Deleted', ...). The delete endpoints share this helper, and a save-shaped
    message there ("Saved 3 clade deletion(s).") reads as the opposite of what
    happened.

    Partial success is deliberate. Accepting 39 suggestions must not be undone
    because one row vanished to an intervening Sync, so each record gets its own
    transaction and a failure cannot poison its siblings.

    Note there is no sentinel exception used as control flow: a genuine
    ValueError raised inside apply_row is reported against its own record with
    its type, never mistaken for a rollback signal.
    """
    if not records:
        return _wb_envelope(0, 'No records supplied.', 0, [])

    errors = []
    updated = 0
    for record in records:
        try:
            with transaction.atomic():
                apply_row(record)
        except _WbRowError as exc:
            errors.append({'record': record, 'error': str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported per row, never swallowed
            errors.append({'record': record,
                           'error': f'{type(exc).__name__}: {exc}'})
        else:
            updated += 1

    if errors and not updated:
        return _wb_envelope(0, f'{verb} nothing; {len(errors)} record(s) failed.',
                            0, errors)
    if errors:
        return _wb_envelope(1, f'{verb} {updated} {noun}; {len(errors)} failed.',
                            updated, errors)
    return _wb_envelope(1, f'{verb} {updated} {noun}.', updated)


@require_POST
def cladeSave(request):
    blocked = _wb_guard(request, 'add the clade')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    clades = DBtable_clades()

    def apply_row(record):
        title = record.get('title')
        if not title:
            raise _WbRowError('missing title')
        # DBtable_clades.new/update both do int(order), so a missing order
        # raises TypeError rather than saving a null. Default it.
        color = record.get('color') or ''
        order = record.get('order')
        order = 0 if order in (None, '') else order
        if 'id' not in record:
            clades.new(title=title, color=color, order=order)
        else:
            clades.update(clade_id=record['id'], title=title,
                          color=color, order=order)

    return _wb_batch(records, apply_row, 'clade(s)')

@require_POST
def cladeDelete(request):
    blocked = _wb_guard(request, 'delete the clade')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    clades = DBtable_clades()

    def apply_row(record):
        if 'id' not in record:
            raise _WbRowError('missing id')
        try:
            clades.delete(record['id'])
        except Clades.DoesNotExist:
            raise _WbRowError('clade no longer exists')

    return _wb_batch(records, apply_row, 'clade(s)', verb='Deleted')

@require_POST
def cladeSampleTypesSave(request):
    blocked = _wb_guard(request, 'add the clade association')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    stc = DBtable_stc()

    def apply_row(record):
        try:
            stc.update(record.get('sample_type_id'), record.get('clade_id'))
        except Sample_types_clades.DoesNotExist:
            raise _WbRowError('association row no longer exists; re-sync and retry')

    return _wb_batch(records, apply_row, 'association(s)')

@requires_seek_login_redirect('/seek/samples/attributes/')
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def internalAssays(request):
    db_ia = DBtable_internalassays()
    db_aia = DBtable_assaysinternalassays()
    internal_assays = simplejson.dumps(db_ia.getAll(), default=list)
    assay_associations = simplejson.dumps(db_aia.getAllWithTitles(), default=list)

    return render(request,"internal_assays.html", {"internal_assays": internal_assays, "assay_associations": assay_associations})

def internalAssaySuggestions(request):
    """Read-only tiered suggestions for every unmapped assay.

    Deliberately fails soft: if fetching the data or resolving suggestions
    raises, the response still carries the same envelope shape with
    ``status: 0`` and an empty ``suggestions`` dict, so the workbench renders
    'unavailable' and stays usable rather than the page taking a 500 — the
    same way router.py degrades on a BAML failure.
    """
    def envelope(status, msg, suggestions=None):
        # Same shape as _wb_envelope, plus this endpoint's own suggestions key.
        return JsonResponse({'status': status, 'msg': msg, 'errors': [],
                             'suggestions': suggestions or {}})

    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        return envelope(0, user_seek.get('err', 'Not signed in'))

    if verifySuperUser(request) != 1:
        return envelope(0, 'Superuser required.')

    try:
        vocabulary = [(v['id'], v['internal_assay_title'])
                      for v in DBtable_internalassays().getAll()]
        rows = DBtable_assaysinternalassays().getAllWithTitles()
        precedents = [(r['assay_title'], r['internal_assay_id'], r['internal_assay_title'])
                      for r in rows if r.get('internal_assay_id')]

        suggestions = {}
        for row in rows:
            if row.get('internal_assay_id'):
                continue
            candidates = vocab_suggest(row.get('assay_title'), vocabulary, precedents)
            suggestions[str(row['assay_id'])] = [
                {'vocabulary_id': c.vocabulary_id, 'vocabulary_title': c.vocabulary_title,
                 'tier': c.tier, 'basis': c.basis, 'support': c.support}
                for c in candidates
            ]
    except Exception:
        logger.exception("Failed to build assay association suggestions")
        return envelope(0, 'Suggestions unavailable.')

    return envelope(1, '', suggestions)


@require_POST
def internalAssaySave(request):
    blocked = _wb_guard(request, 'add the internal assay')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    ia = DBtable_internalassays()

    def apply_row(record):
        title = record.get('internal_assay_title')
        if not title:
            raise _WbRowError('missing internal_assay_title')
        if 'id' not in record:
            ia.new(internal_assay_title=title)
        else:
            ia.update(internal_assay_id=record['id'], internal_assay_title=title)

    return _wb_batch(records, apply_row, 'internal assay(s)')

@require_POST
def internalAssayDelete(request):
    blocked = _wb_guard(request, 'delete the internal assay')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    ia = DBtable_internalassays()

    def apply_row(record):
        if 'id' not in record:
            raise _WbRowError('missing id')
        try:
            ia.delete(record['id'])
        except Internal_assays.DoesNotExist:
            raise _WbRowError('internal assay no longer exists')

    return _wb_batch(records, apply_row, 'internal assay(s)', verb='Deleted')

@require_POST
def assayAssociationSave(request):
    blocked = _wb_guard(request, 'add the assay association')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    aia = DBtable_assaysinternalassays()

    def apply_row(record):
        try:
            aia.update(record.get('assay_id'), record.get('internal_assay_id'))
        except Assays_internal_assays.DoesNotExist:
            # Row vanished between page load and save — almost always an
            # intervening Sync. Reported per row; siblings still commit.
            raise _WbRowError('association row no longer exists; re-sync and retry')

    return _wb_batch(records, apply_row, 'association(s)')

@requires_seek_login
@requires_supervisor('The login user does not have the permission to perform this action.')
def syncInternalAssays(request):
    aia = DBtable_assaysinternalassays()
    aia.syncAssays()
    
    return HttpResponse({})
