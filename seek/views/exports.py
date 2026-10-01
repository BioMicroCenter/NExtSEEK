"""Sample export files: written where no URL serves them, handed back only to the caller who made them.

The legacy export views answer with JSON carrying a ``link`` that the page then opens: ``samples.sampleDownload``,
``samples.sampleExport``, ``samples.sampleFindAjax`` and ``samples.sampleDelete``, and the sample upload page
(``upload.sampleUploadAjax``) for its feedback workbook. They used to write under ``MEDIA_ROOT/download/`` with a
per-minute name, under a route that served ``MEDIA_ROOT`` by name alone, so a name was the only thing standing
between a caller and someone else's export, and two exports made in the same minute shared one file.

``newExport`` gives each export its own directory, named by a random token (uuid4), under the export root: outside
``MEDIA_ROOT``, and served by no route but ``exportFile``. The directory records who made it, and ``exportFile``
streams a file from it only to that user or to a superuser. Anyone else, an unknown token and a bad name all get the
same 404, so a link confirms nothing to someone who did not make it; an anonymous caller is sent to the login page.
Directories older than ``EXPORT_TTL_SECONDS`` are removed when the next export is made.

The root is ``settings.NEXTSEEK_EXPORT_ROOT`` when a box sets one, else ``nextseek-exports`` in the system temporary
directory. It must be shared by every process that serves requests, which inside the one app container it is.
"""

import logging
import os
import re
import shutil
import tempfile
import time
import uuid

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseRedirect

logger = logging.getLogger(__name__)

EXPORT_URL = "/seek/exports/"
EXPORT_TTL_SECONDS = 24 * 60 * 60
EXPORT_NOT_FOUND = "Export not found"

_OWNER_FILE = ".owner"
_TOKEN = re.compile(r"[0-9a-f]{32}")
_FILENAME = re.compile(r"[\w-][\w.-]*")    # no separator, and no leading dot: never the owner file, never '..'


def exportRoot():
    """Where private exports live: never under MEDIA_ROOT, the tree /media/ serves from."""
    return getattr(settings, "NEXTSEEK_EXPORT_ROOT", None) or os.path.join(tempfile.gettempdir(), "nextseek-exports")


def exportName(filename):
    """``filename`` as a name ``exportFile`` serves: every character but a word character, '.' and '-' becomes '_',
    and a leading '.' goes. The upload page names its feedback after the uploaded sheet, which may hold anything."""
    name = re.sub(r"[^\w.-]", "_", os.path.basename(filename or "")).lstrip(".")
    return name or "export"


def newExport(request, filename):
    """A new private export for the logged-in caller: (the path to write ``filename`` to, the link that serves it).

    ``filename`` is made servable first (``exportName``). Files the writer puts beside it in the same directory (a
    zip's members) are private too."""
    filename = exportName(filename)
    _removeStaleExports()
    token = uuid.uuid4().hex
    directory = os.path.join(exportRoot(), token)
    os.makedirs(directory, mode=0o700)
    with open(os.path.join(directory, _OWNER_FILE), "w") as fh:
        fh.write(str(request.user.pk))
    return os.path.join(directory, filename), EXPORT_URL + token + "/" + filename


def exportFile(request, token, filename):
    """``GET /seek/exports/<token>/<filename>``: stream one export file to the caller who made it, or to a superuser."""
    if not request.user.is_authenticated:
        return HttpResponseRedirect('/login/?next=' + request.path)
    if not _TOKEN.fullmatch(token) or not _FILENAME.fullmatch(filename):
        raise Http404(EXPORT_NOT_FOUND)

    directory = os.path.join(exportRoot(), token)
    try:
        with open(os.path.join(directory, _OWNER_FILE)) as fh:
            owner = fh.read().strip()
    except OSError:
        raise Http404(EXPORT_NOT_FOUND)
    if owner != str(request.user.pk) and not getattr(request.user, "is_superuser", False):
        raise Http404(EXPORT_NOT_FOUND)

    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        raise Http404(EXPORT_NOT_FOUND)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)


def _removeStaleExports():
    """Remove export directories older than ``EXPORT_TTL_SECONDS``. Never raises: a failed cleanup costs disk only."""
    root = exportRoot()
    cutoff = time.time() - EXPORT_TTL_SECONDS
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        try:
            if _TOKEN.fullmatch(name) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            logger.debug("could not remove the stale export %s", name)
