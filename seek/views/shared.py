"""Module-level state and settings-derived paths shared by the view modules.

``report`` is the one that matters. It is a module-level dict mutated by
``upload.batchUpload``, ``upload.datafileUpload``, ``assets.sopQuery`` and
``assets.datafileQuery``, and it is shared across requests and across those
modules -- a leak, recorded as ``LATENT_BUGS.md`` #2 and deliberately preserved
here. It lives in exactly one module so that every user mutates the same object;
duplicating it per module would change behaviour.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

SEEK_DATABASE = settings.SEEK_DATABASE
NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'  
UPLOAD_DIRECTORY = settings.MEDIA_ROOT + "/uploads/"
SEEK_DATAFILE_ROOT = settings.SEEK_DATAFILE_ROOT
SAMPLE_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/SAMPLE_TEMPLATE.xlsx"
SAMPLE_TEMPLATES_FOLDER = settings.SAMPLE_TEMPLATES_FOLDER
SAMPLE_TEMPLATES_FOLDER_PROJECT = settings.SAMPLE_TEMPLATES_FOLDER_PROJECT
PUBLISH_STATS_FILE = settings.PUBLISH_STATS_FILE
SEEK_HOSTNAME = settings.SEEK_HOSTNAME
report = {}
