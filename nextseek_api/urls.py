from django.urls import re_path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from . import views

app_name = "nextseek_api"

# Create separate routers to avoid ID/UUID routing conflicts
router = DefaultRouter()

# Register all ViewSets created in Phase 3:
router.register(r"samples", views.SampleTreeByIDViewSet, basename="samples-by-id")
router.register(r"samples-uuid", views.SampleTreeByUUIDViewSet, basename="samples-by-uuid")  
router.register(r"nhp", views.NHPViewSet, basename="nhp")
router.register(r"sample-queries", views.SampleQueryViewSet, basename="sample-queries")
router.register(r"admin/samples", views.AdminSampleViewSet, basename="admin-samples")
router.register(r"sops", views.SopViewSet, basename="sops")
router.register(r"data_files", views.DataFileViewSet, basename="data_files")
router.register(r"projects", views.ProjectViewSet, basename="projects")
router.register(r"people", views.PeopleViewSet, basename="people")
router.register(r"investigations", views.InvestigationViewSet, basename="investigations")
router.register(r"assays", views.AssayViewSet, basename="assays")
router.register(r"sample_types", views.SampleTypeViewSet, basename="sample_types")

urlpatterns = [
    # OpenAPI Schema Documentation
    re_path(r'^schema/$', SpectacularAPIView.as_view(), name='schema'),
    re_path(r'^swagger/$', SpectacularSwaggerView.as_view(url_name='nextseek_api:schema'), name='swagger-ui'),
    re_path(r'^redoc/$', SpectacularRedocView.as_view(url_name='nextseek_api:schema'), name='redoc'),
    
    # Include router URLs
    re_path(r'^', include(router.urls)),
]
