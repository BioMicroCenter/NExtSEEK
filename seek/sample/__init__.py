"""The sample table, split into mixins by which entry point reaches them.

``seek/dbtable_sample.py`` remains as a shim re-exporting ``DBtable_sample``,
so none of the import sites in seek/, api_app/ or nextseek_api/ changed.

Layout: ``core`` plus ``trees``/``queries``/``immport`` hold the helpers that
more than one entry point reaches; ``upload``/``download``/``search``/``api``
hold the methods exclusive to one. Nothing in the first group calls into the
second, so the dependency runs one way.
"""

from .table import DBtable_sample
from .constants import SAMPLE_ERRORCODE

__all__ = ['DBtable_sample', 'SAMPLE_ERRORCODE']
