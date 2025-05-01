from django.urls import re_path
from filebrowser.sites import site
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    re_path(r'^remote/', views.remote, name='remote'),
    re_path(r'^samples/upload/', views.sampleUpload, name='sampleUpload'),
    re_path(r'^samples/query/', views.sampleQuery, name='sampleQuery'),
    re_path(r'^samples/search/', views.sampleSearch, name='sampleSearch'),
    re_path(r'^samples/searching/', views.sampleSearching, name='sampleSearching'),
    re_path(r'^templates/', views.templatesList, name="templatesList"),
    re_path(r'^retrieve/samples/', views.retrieveSamples, name='retrieveSamples'),
    
    re_path(r'^samples/attributes/', views.sampleAttributes, name='sampleAttributes'),
    re_path(r'^samples/retrieveType/', views.getSampleType, name='getSampleType'),
    
    re_path(r'^sample/id=(?P<id>\d+)/$', views.sample, name='sample'),
    re_path(r'^sampletree/uid=(?P<uid>[\w.-]{0,256})/$', views.sampleTree, name='sampleTree'),
    
    re_path(r'^samples/download/', views.sampleDownload, name='sampleDownload'),
    re_path(r'^samples/export/', views.sampleExport, name='sampleExport'),
    re_path(r'^samples/delete/', views.sampleDelete, name='sampleDelete'),
    re_path(r'^samples/publish/', views.samplePublish, name='samplePublish'),
    
    re_path(r'^samplefind/', views.sampleFindAjax, name='sampleFindAjax'),
    re_path(r'^samples/publishlist/(?P<sampleids>\d+(,\d+)*)/$',views.publish_samples, name='publish_samples'),
    
    re_path(r'^document/id=(?P<id>\d+)/$', views.document, name='document'),

    re_path(r'^attributes/id=(?P<id>\d+)/$', views.getAttributes, name='getAttributes'),
    re_path(r'^attribute/save/', views.sampleAttributeSave, name='sampleAttributeSave'), 
    re_path(r'^attribute/delete/', views.sampleAttributeDelete, name='sampleAttributeDelete'),
    re_path(r'^operators/$', views.getOperators, name='getOperators'),
    
    re_path(r'^sample_types/id=(?P<id>\d+)/$', views.sample_type, name='sample_type'),
    re_path(r'^sampleupload/', views.sampleUploadAjax, name='sampleUploadAjax'),
    re_path(r'^samplesvalidate/', views.samplesValidate, name='samplesValidate'),
    
    re_path(r'^url/(?P<url>[\w-]+)/$', views.seek, name='seek'),
    
    # get to the upload page of data files to server and to Seek
    re_path(r'^data/upload/', views.datafileUpload, name='datafileUpload'),
    re_path(r'^datafiles/batchupload/', views.filesBatchUpload, name='filesBatchUpload'),
    re_path(r'^datafiles/uploadtoseek/', views.uploadToSeek, name='uploadtoseek'),
    re_path(r'^datafiles/getuids/', views.filesGetUIDs, name='filesGetUIDs'),

    re_path(r'^datafile/query/', views.datafileQuery, name='datafileQuery'),
    re_path(r'^datafiles/publish/(?P<dfids>\d+(,\d+)*)/$',views.publish_datafiles, name='publish_datafiles'),

    re_path(r'^datafile/uid=(?P<uid>[\w.\-()_+]{0,256})/$', views.datafileDownload, name='datafileDownload'),

    re_path(r'^sop/uid=(?P<uid>[\w.\-()_+]{0,256})/$', views.sopDownload, name='sopDownload'),
    re_path(r'^sops/publish/(?P<sopids>\d+(,\d+)*)/$',views.publish_sops, name='publish_sops'),
    
    re_path(r'^sop/query/', views.sopQuery, name='sopQuery'),
    re_path(r'^retrieve/datafiles/', views.retrieveDatafiles, name='retrieveDatafiles'),
    re_path(r'^retrieve/sops/', views.retrieveSops, name='retrieveSops'),
    re_path(r'^batchdownload/datafiles/', views.batchdownloadDatafiles, name='batchdownloadDatafiles'),
    re_path(r'^batchdownload/sops/', views.batchdownloadSops, name='batchdownloadSops'),
    re_path(r'^batchpublish/datafiles/', views.batchpublishDatafiles, name='batchpublishDatafiles'),
    re_path(r'^batchpublish/sops/', views.batchpublishSops, name='batchpublishSops'),
    
    re_path(r'^dropbox/path/', views.getDropboxPath, name='getDropboxPath'),
    re_path(r'^dropbox/status/', views.getDropboxStatus, name='getDropboxStatus'),
    
    re_path(r'^publish/', views.publish, name='publish'),   
    re_path(r'^investigations/id=(?P<id>\d+)/$', views.getStudiesOptions, name='getStudiesOptions'),    
    re_path(r'^studies/id=(?P<id>\d+)/$', views.getAssaysOptions, name='getAssaysOptions'),
    re_path(r'^searchAssets/', views.publishSearching, name='publishSearching'),
    re_path(r'^publishAssets/', views.publishAssets, name='publishAssets'),    
    re_path(r'^instituion/id=(?P<id>\d+)/$', views.getInstituionUsers, name='getInstituionUsers'),    
    re_path(r'^search/', views.searchAdvanced, name='searchAdvanced'),
    re_path(r'^searchAdvanced/', views.searchingAdvanced, name='searchingAdvanced'),
    re_path(r'^searchUIDs/', views.searchingUIDs, name='searchingUIDs'),

    re_path(r'nhpinfo/(?P<nhp_name>[\w-]+)/$', views.nhp_info, name='nhp_info'),
    re_path(r'nhpdata/(?P<nhp_name>[\w-]+)/$', views.get_nhp_data, name='nhp_data'),
    re_path(r'^nhpdata/(?P<nhp_name>[\w-]+)/download/$', views.download_nhp_data, name='download_nhp_data'),
    re_path(r'^eventdata/(?P<nhp_name>[\w-]+)/(?P<event_type>[\w.-]+)/(?P<date>[\w-]+)/$', views.fetch_event_data, name='event_data'),
    re_path(r'^sample_timeline/.*$', TemplateView.as_view(template_name="sample_timeline.html")),

]

