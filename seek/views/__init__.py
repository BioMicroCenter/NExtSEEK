"""The `seek` app's views, split by area.

Every name the URL conf and the other apps use is re-exported here, so
`seek.views.X` resolves exactly as it did when this was a single module.

Patching a name INTO a view module (`mock.patch("seek.views.foo")`) no longer
reaches the call site -- the call site reads its own module's globals. Patch
the owning module instead, e.g. `seek.views.admin.write_samples_workbook`.
"""

from .samples import (editSample, getAssaysOptions, getAttributes, getInstituionUsers, getOperators, getSampleType, getStudiesOptions, manageSample, retrieveSamples, sample, sampleAttributeDelete, sampleAttributeSave, sampleAttributes, sampleDelete, sampleDownload, sampleExport, sampleFindAjax, sampleQuery, sampleTree, sample_type, seek)
from .upload import (batchUpload, datafileUpload, sampleUploadAjax, samplesValidate)
from .assets import (datafileQuery, document, getTemplateFolders, sopQuery, templatesList)
from .search import (newSearch, remote, runSampleSearch, sampleSearch, sampleSearching, searchAdvanced, searchingAdvanced, searchingUIDs, smartSearch)
from .admin import (adminClades, adminRetrieveSamples, assayAssociationSave, cladeDelete, cladeSampleTypesSave, cladeSave, cladesSyncSampleTypes, get_children_uids, internalAssayDelete, internalAssaySave, internalAssays, parse_children_uids, parse_json_metadata, sample_retrieval_data, syncInternalAssays)
from .projects import (project_page, projects)
from .timeline import (download_nhp_data, fetch_event_data, get_nhp_data, nhp_info)
from .pages import (getting_started)
from .shared import report

__all__ = [
    'adminClades',
    'adminRetrieveSamples',
    'assayAssociationSave',
    'batchUpload',
    'cladeDelete',
    'cladeSampleTypesSave',
    'cladeSave',
    'cladesSyncSampleTypes',
    'datafileQuery',
    'datafileUpload',
    'document',
    'download_nhp_data',
    'editSample',
    'fetch_event_data',
    'getAssaysOptions',
    'getAttributes',
    'getInstituionUsers',
    'getOperators',
    'getSampleType',
    'getStudiesOptions',
    'getTemplateFolders',
    'get_children_uids',
    'get_nhp_data',
    'getting_started',
    'internalAssayDelete',
    'internalAssaySave',
    'internalAssays',
    'manageSample',
    'newSearch',
    'nhp_info',
    'parse_children_uids',
    'parse_json_metadata',
    'project_page',
    'projects',
    'remote',
    'report',
    'retrieveSamples',
    'runSampleSearch',
    'sample',
    'sampleAttributeDelete',
    'sampleAttributeSave',
    'sampleAttributes',
    'sampleDelete',
    'sampleDownload',
    'sampleExport',
    'sampleFindAjax',
    'sampleQuery',
    'sampleSearch',
    'sampleSearching',
    'sampleTree',
    'sampleUploadAjax',
    'sample_retrieval_data',
    'sample_type',
    'samplesValidate',
    'searchAdvanced',
    'searchingAdvanced',
    'searchingUIDs',
    'seek',
    'smartSearch',
    'sopQuery',
    'syncInternalAssays',
    'templatesList',
]
