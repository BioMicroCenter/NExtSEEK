"""``/media/``: ``MEDIA_ROOT``, served only to a logged-in caller, and only the tree the application links to.

Django serves this route itself: nginx has no ``/media`` location and proxies every path it does not own. It used to
be the plain static serve over all of ``MEDIA_ROOT``, with no login, and ``MEDIA_ROOT`` is the application's working
state, named by the minute, the second or the user id and linked from nowhere: the sample upload page's copy of each
sheet (``uploads/``), batch upload's files, reports, checkpoints and per-user job index (``batch_upload_uploads/``,
``batch_upload_reports/``, ``batch_upload_checkpoints/``, ``celery_jobs/``), the chat upload staging
(``cc_upload_staging/``), the sheet templates (``reserved/``) and the legacy ``download/`` directory. None of it is
addressed to a reader, and none of it belongs on a route that answers by name alone.

Now an anonymous caller is sent to the login page, and a logged-in caller, a superuser included, gets only the tree the
code builds ``/media/`` links to: the legacy data-file and SOP store, whose links are ``SEEK_DATAFILE_ROOT_WEBLINK``.
Every other path answers 404, exactly as a missing file does. A file a page hands to one user belongs in the private
store of ``seek.views.exports``, never here.

That store is laid out ``<project folder>/<LAB>/<file>``, the folder being the uploader's project title with spaces as
``_`` (``seek.dbtable_data_files``, ``seek.dbtable_sops``). A caller who is not a superuser gets only the files under a
folder of one of their projects; any other file answers 404 too.
"""

import posixpath
from urllib.parse import quote

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.views.static import serve

MEDIA_NOT_FOUND = "Not found"


def _served_trees():
    """The ``MEDIA_ROOT``-relative trees ``/media/`` serves: the data-file store, when its links are under MEDIA_URL."""
    link = getattr(settings, "SEEK_DATAFILE_ROOT_WEBLINK", "") or ""
    media = settings.MEDIA_URL or ""
    tree = link[len(media):].strip("/") if media and link.startswith(media) else ""
    return (tree + "/",) if tree else ()


def _readable_project_folders(user):
    """The store's project folders the caller may read: None for a superuser (every folder), else the folder name of
    each of their projects. Membership is ``graph_search.scope.resolve_scope`` (``is_superuser`` alone, never
    ``is_staff``). Fails closed: a scope or a project title that cannot be read gives no folder."""
    try:
        from nextseek_api.graph_search.scope import resolve_scope
        from seek.models import Projects

        scope = resolve_scope(user)
        if scope.is_admin:
            return None
        if not scope.project_ids:
            return set()
        titles = Projects.objects.filter(id__in=scope.project_ids).values_list("title", flat=True)
        return {title.replace(" ", "_") for title in titles if title}
    except Exception:  # noqa: BLE001 (ScopeUnavailable or a failed read: fail closed)
        return set()


def serve_media(request, path):
    """``GET /media/<path>``: a file of a served tree, to a logged-in caller in the file's project (or a superuser);
    the login page for anyone else."""
    if not request.user.is_authenticated:
        return HttpResponseRedirect("/login/?next=" + quote(request.path))
    # The same normalisation static serve applies, done first so a '..' cannot walk out of a served tree.
    normalized = posixpath.normpath(path).lstrip("/")
    tree = next((tree for tree in _served_trees() if normalized.startswith(tree)), None)
    if tree is None:
        raise Http404(MEDIA_NOT_FOUND)
    folders = _readable_project_folders(request.user)
    if folders is not None and normalized[len(tree):].split("/", 1)[0] not in folders:
        raise Http404(MEDIA_NOT_FOUND)
    return serve(request, normalized, document_root=settings.MEDIA_ROOT)
