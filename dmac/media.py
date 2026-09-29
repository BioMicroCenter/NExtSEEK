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


def serve_media(request, path):
    """``GET /media/<path>``: a file of a served tree, to a logged-in caller; the login page for anyone else."""
    if not request.user.is_authenticated:
        return HttpResponseRedirect("/login/?next=" + quote(request.path))
    # The same normalisation static serve applies, done first so a '..' cannot walk out of a served tree.
    normalized = posixpath.normpath(path).lstrip("/")
    if not any(normalized.startswith(tree) for tree in _served_trees()):
        raise Http404(MEDIA_NOT_FOUND)
    return serve(request, normalized, document_root=settings.MEDIA_ROOT)
