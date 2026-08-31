from django.urls import re_path, include
from rest_framework.permissions import IsAuthenticated
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
router.register(r"users", views.UsersViewSet, basename="users")
router.register(r"investigations", views.InvestigationViewSet, basename="investigations")
router.register(r"studies", views.StudyViewSet, basename="studies")
router.register(r"attributes", views.AttributeViewSet, basename="attribute")
router.register(r"assays", views.AssayViewSet, basename="assays")
router.register(r"sample_types/connections", views.SampleTypeConnectionsViewSet, basename="sampletype-connections")
router.register(r"sample_types", views.SampleTypeViewSet, basename="sample_types")
router.register(r"samples/advanced_search", views.SampleAdvancedSearchViewSet, basename="samples-advanced-search")
router.register(r"sample_types/get_parents", views.SamplesByChildTypesViewSet,basename="get-parents-by-childtype")
router.register(r"sampletypes", views.SampleTypeChildrenViewSet, basename="sampletypes")
router.register(r"samples", views.SampleViewSet, basename="samples")
router.register(r"schema_rag", views.SchemaRAGViewSet, basename="schema-rag")
router.register(r"entity_tree", views.EntityTreeViewSet, basename="entity-tree")
router.register(r"batch-upload", views.BatchUploadViewSet, basename="batch-upload")
router.register(r"assistant", views.AssistantViewSet, basename="assistant")
# Additive: router + Container-Claude-Code assistant (does NOT replace assistant).
router.register(r"cc-assistant", views.CCAssistantViewSet, basename="cc-assistant")
router.register(r"evaluator", views.EvaluatorViewSet, basename="evaluator")
router.register(r"admin/project-export", views.ProjectExportViewSet, basename="admin-project-export")


urlpatterns = [
    # OpenAPI Schema Documentation.
    #
    # permission_classes is declared HERE and not left to DRF's
    # DEFAULT_PERMISSION_CLASSES: each drf-spectacular serve-view assigns
    # `permission_classes = spectacular_settings.SERVE_PERMISSIONS` in its own class
    # body, and the package default is [AllowAny], so the project default never
    # applies. Registered plainly, these three routes publish the entire API surface
    # -- every path, parameter, request body and model, admin/ and evaluator/
    # included -- to anyone who can reach the host (#77).
    #
    # IsAuthenticated, deliberately, and NOT an admin gate:
    #   * IsAdminUser checks is_staff, which dmac/views.py:80,97 sets on every SEEK
    #     user at login -- it is IsAuthenticated under a misleading name (#74, #75).
    #   * IsSuperUser would over-gate: this is the working reference for legitimate
    #     token-holding API consumers, and it describes endpoints, it returns no
    #     records. docs/endpoint-authorization-register.md buckets all three as
    #     "public-to-authenticated".
    # Authentication is untouched: SERVE_AUTHENTICATION is unset, so the views fall
    # back to DEFAULT_AUTHENTICATION_CLASSES (Token, Session, Basic).
    re_path(r'^schema/$', SpectacularAPIView.as_view(permission_classes=[IsAuthenticated]), name='schema'),
    # template_name overrides drf-spectacular's default to add the effective-identity
    # banner (#119). Swagger's Authorize button is silently ignored whenever the browser
    # holds a NExtSEEK session cookie, because DRF stops at the first authenticator that
    # succeeds and CsrfExemptSessionAuthentication sits above BasicAuthentication. The
    # banner states who the requests will ACTUALLY run as, so a mismatch is visible
    # instead of being discovered by mis-diagnosing an authorization gap.
    re_path(r'^swagger/$', SpectacularSwaggerView.as_view(
        url_name='nextseek_api:schema',
        permission_classes=[IsAuthenticated],
        template_name='nextseek/swagger_ui.html',
    ), name='swagger-ui'),
    re_path(r'^redoc/$', SpectacularRedocView.as_view(url_name='nextseek_api:schema', permission_classes=[IsAuthenticated]), name='redoc'),

    # Include router URLs
    re_path(r'^', include(router.urls))
]
