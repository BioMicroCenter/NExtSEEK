Below is a **fresh, implementation-ready roadmap** for replacing the half-finished `api_app` with a *useful* read-only Django REST Framework layer whose logic is refactored straight out of the “real” code in **`seek/views.py`**.

---

## 1  Inventory: Which functions become API endpoints?

| New endpoint                                                          | Source function (seek/views.py)   | Notes                                                              |
| --------------------------------------------------------------------- | --------------------------------- | ------------------------------------------------------------------ |
| **`GET /api/samples/<int:sample_id>/tree/`**                          | `sampleTreeNew` (≈ L 2750)        | Already returns a DRF `Response`; just needs to move into a class. |
| **`GET /api/samples/<uuid:uid>/tree/`**                               | `sampleTreeNewUID` (≈ L 2810)     | Same logic but keyed by UUID.                                      |
| **`GET /api/nhp/<str:nhp_name>/info/`**                               | `nhp_info` (≈ L 2400)             | Pure read-only JSON.                                               |
| **`GET /api/nhp/<str:nhp_name>/events/<str:event_type>/<str:date>/`** | `fetch_event_data` (≈ L 2415)     | Already returns `Response`.                                        |
| **`GET /api/nhp/<str:nhp_name>/timeline/`**                           | `get_nhp_data` (≈ L 2435)         |                                                                    |
| **`GET /api/nhp/<str:nhp_name>/download/`**                           | `download_nhp_data` (≈ L 2455)    | Streams an Excel file.                                             |
| **`GET /api/samples/query/`**                                         | `retrieveSamples` (≈ L 2080)      | Raw JSON from DB; good candidate for pagination later.             |
| **`POST /api/admin/samples/retrieve/`**                               | `adminRetrieveSamples` (≈ L 2230) | CSV/XLSX export for admins.                                        |

Everything else in `seek/views.py` is **either** HTML-template UI, file-upload logic, or admin-only batch utilities; those can stay out of the first API pass.

---

## 2  Refactor functions → DRF classes (≈ 6 hrs)

Create **`nextseek_api/viewsets.py`**:

```python
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from seek import views as legacy         # import the old functions
from .serializers import (
    SampleTreeSerializer,               # defined in §3
    NHPInfoSerializer,
    EventDataSerializer,
)

class SampleTreeViewSet(viewsets.GenericViewSet):
    """
    read_only:
    * /samples/{pk}/tree/      (numeric id)
    * /samples/{uid}/tree/     (uuid)
    """
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["get"], url_path="tree")
    def by_id(self, request, pk=None):
        return legacy.sampleTreeNew(request, sample_id=pk)

    @action(detail=False, methods=["get"], url_path=r"(?P<uid>[0-9A-Fa-f\-]+)/tree")
    def by_uid(self, request, uid=None):
        return legacy.sampleTreeNewUID(request, uid=uid)


class NHPViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["get"], url_path="info")
    def info(self, request, pk=None):
        return legacy.nhp_info(request, nhp_name=pk)

    @action(detail=True, methods=["get"], url_path=r"events/(?P<event_type>[^/]+)/(?P<date>[^/]+)")
    def events(self, request, pk=None, event_type=None, date=None):
        return legacy.fetch_event_data(request, nhp_name=pk,
                                       event_type=event_type, date=date)

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        return legacy.get_nhp_data(request, nhp_name=pk)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        return legacy.download_nhp_data(request, nhp_name=pk)
```

*Why not subclass `ReadOnlyModelViewSet`?* — Because these endpoints don’t map 1-to-1 to Django models; they’re graph/Neo4j look-ups and Nextflow helpers.

---

## 3  Lightweight serializers (≈ 1 hr)

Most legacy functions already return fully-formed JSON objects, so serializers can be simple “pass-through” stubs used only for schema generation:

```python
from rest_framework import serializers

class SampleNodeSerializer(serializers.Serializer):
    id        = serializers.CharField()
    uuid      = serializers.UUIDField()
    type      = serializers.CharField()
    color     = serializers.CharField()
    parentIds = serializers.ListField(child=serializers.CharField())

class SampleTreeSerializer(serializers.ListSerializer):
    child = SampleNodeSerializer()

class NHPInfoSerializer(serializers.DictField)        # any JSON
class EventDataSerializer(serializers.DictField)
```

---

## 4  Router & URL wiring (≈ 0.5 hr)

```python
# nextseek_api/urls.py
from rest_framework.routers import DefaultRouter
from .viewsets import SampleTreeViewSet, NHPViewSet
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

router = DefaultRouter()
router.register(r"samples", SampleTreeViewSet, basename="samples")
router.register(r"nhp",     NHPViewSet,        basename="nhp")

urlpatterns = router.urls + [
    path("schema/",  SpectacularAPIView.as_view(),                 name="schema"),
    path("swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/",   SpectacularRedocView.as_view(url_name="schema"),   name="redoc"),
]
```

Remove `api_app.urls` from `dmac/urls.py`, then:

```python
urlpatterns += [re_path(r"^api/", include("nextseek_api.urls"))]
```

---

## 5  OpenAPI parity via `@extend_schema` (≈ 2 hrs)

```python
from drf_spectacular.utils import extend_schema, extend_schema_view

@extend_schema_view(
    by_id      = extend_schema(responses={200: SampleTreeSerializer}),
    by_uid     = extend_schema(responses={200: SampleTreeSerializer}),
)
class SampleTreeViewSet(...): ...
```

Repeat for `NHPViewSet` methods, declaring `200: NHPInfoSerializer` or `EventDataSerializer`.
Run `python manage.py spectacular --file nextseek_api/openapi.yaml` and diff against **`NExtSEEK API.yaml`** to be sure the paths and response objects line up.

---

## 6  Unit tests (≈ 2 hrs)

```python
# nextseek_api/tests/test_sample_tree.py
class SampleTreeAPITest(APITestCase):
    fixtures = ["minimal.json"]               # sample + user
    def setUp(self): self.client.force_authenticate(User.objects.first())

    def test_tree_by_id(self):
        url = "/api/samples/1/tree/"
        self.assertEqual(self.client.get(url).status_code, 200)
```

Write one happy-path test per new action.

---

## 7  Timeline / effort

| Block                              | Time                          |
| ---------------------------------- | ----------------------------- |
| **Step 2** refactor FBVs → classes | 6 h                           |
| **Step 3** serializers             | 1 h                           |
| **Step 4** router & wiring         | 0.5 h                         |
| **Step 5** schema annotations      | 2 h                           |
| **Step 6** tests                   | 2 h                           |
| *Buffer / QA*                      | 1 h                           |
| **Total**                          | **≈ 12.5 hrs** (1.5 dev-days) |

---

### Why we ignored `api_app/*`

A grep of the repo shows `SamplesListViews` et al. are referenced **only inside `api_app/urls.py`**, never imported elsewhere. They work—*but their behaviour is limited to simple list/detail on two tables*. The endpoints above offer far richer functionality, so the new `nextseek_api` is built directly around the FBVs we actually use.

---

### What’s next?

1. **Pagination & caching** on heavy endpoints like `/samples/query/`.
2. **Pydantic v2** output models once DRF 4.0 lands support.
3. **Django Channels** if you ever need live progress for big Excel exports.

That’s the full, line-mapped roadmap to turn `seek/views.py` into a clean DRF surface while ditching the half-baked `api_app`.
