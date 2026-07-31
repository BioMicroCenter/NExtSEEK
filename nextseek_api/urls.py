from django.urls import re_path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from . import views

app_name = "nextseek_api"

# Create separate routers to avoid ID/UUID routing conflicts
router = DefaultRouter()

# Register all ViewSets created in Phase 3:
# Existing tree/legacy endpoints remain 
router.register(r"sample-tree", views.SampleTreeViewSet, basename="sample-tree")
# router.register(r"nhp", views.NHPViewSet, basename="nhp")
# router.register(r"sample-queries", views.SampleQueryViewSet, basename="sample-queries")
router.register(r"admin/samples", views.AdminSampleViewSet, basename="admin-samples")
router.register(r"sops", views.SopViewSet, basename="sops")
router.register(r"data_files", views.DataFileViewSet, basename="data_files")
router.register(r"projects", views.ProjectViewSet, basename="projects")
router.register(r"people", views.PeopleViewSet, basename="people")
router.register(r"investigations", views.InvestigationViewSet, basename="investigations")
router.register(r"assays", views.AssayViewSet, basename="assays")
router.register(r"sample_types", views.SampleTypeViewSet, basename="sample_types")
router.register(r"samples/advanced_search", views.SampleAdvancedSearchViewSet, basename="samples-advanced-search")
router.register(r"sample_types/get_parents", views.SamplesByChildTypesViewSet,basename="get-parents-by-childtype")
router.register(r"sampletypes", views.SampleTypeChildrenViewSet, basename="sampletypes")
router.register(r"samples", views.SampleViewSet, basename="samples")
router.register(r"schema_rag", views.SchemaRAGViewSet, basename="schema-rag")
router.register(r"entity_tree", views.EntityTreeViewSet, basename="entity-tree")
router.register(r"batch-upload", views.BatchUploadViewSet, basename="batch-upload")
router.register(r"assistant", views.AssistantViewSet, basename="assistant")
router.register(r"evaluator", views.EvaluatorViewSet, basename="evaluator")
router.register(r"admin/project-export", views.ProjectExportViewSet, basename="admin-project-export")


urlpatterns = [
    # OpenAPI Schema Documentation
    re_path(r'^schema/$', SpectacularAPIView.as_view(), name='schema'),
    re_path(r'^swagger/$', SpectacularSwaggerView.as_view(url_name='nextseek_api:schema'), name='swagger-ui'),
    re_path(r'^redoc/$', SpectacularRedocView.as_view(url_name='nextseek_api:schema'), name='redoc'),
    
    # Include router URLs
    re_path(r'^', include(router.urls))
]
