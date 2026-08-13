# ViewSet patterns — quick recipes

## Auth + upstream SEEK

```python
from rest_framework.authentication import BasicAuthentication
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
from nextseek_api.helpers import SeekAPIClient, resolve_seek_auth

class MyProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    client = SeekAPIClient()

    def list(self, request):
        basic, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic and not request.user.is_authenticated:
            return HttpResponse(
                b'{"detail":"Authentication required"}', status=401, content_type="application/json"
            )
        body, code, headers, _ = self.client.list_things(request, params=request.query_params)
        ...
```

## Superuser-only native ViewSet

```python
from nextseek_api.services.users import IsDjangoSuperuser

class MyAdminViewSet(viewsets.ViewSet):
    authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsDjangoSuperuser]
```

## Native project-scoped SQL

```python
from seek.seekdb import SeekDB

seekdb = SeekDB(None, None, None)
try:
    user_projects = seekdb.getCurrentUser()["data"]["relationships"]["projects"]["data"]
    user_project_ids = [x["id"] for x in user_projects]
except Exception:
    user_project_ids = []

if not user_project_ids:
    return Response({"results": []}, status=200)

# Parameterized query — never interpolate user-controlled IDs into SQL strings.
cursor.execute(
    "SELECT ... FROM samples s JOIN projects_samples ps ON ... WHERE ps.project_id IN %s",
    [tuple(user_project_ids)],
)
```

## Pydantic + spectacular decorator

```python
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError
from nextseek_api.endpoint_descriptions import MY_CREATE_DESC
from nextseek_api.models import MyCreateRequest, MySingleResponse, JsonApiErrorResponse

@extend_schema(
    operation_id="Create My Resource",
    description=MY_CREATE_DESC,
    request=MyCreateRequest,
    responses={201: MySingleResponse, 422: JsonApiErrorResponse},
    tags=["MyResource"],
    examples=[
        OpenApiExample(
            name="Create example",
            value={"title": "Vaccine Dose Response", "project_id": "1"},
            request_only=True,
        ),
        OpenApiExample(
            name="Created response",
            value={"data": {"id": "746", "type": "studies", "attributes": {"title": "Vaccine Dose Response"}}},
            response_only=True,
        ),
    ],
)
def create(self, request):
    try:
        body = MyCreateRequest.model_validate(request.data)
    except ValidationError as exc:
        return Response({"detail": "Invalid request", "errors": exc.errors()}, status=422)
    ...
```

## Standard error helpers

```python
# 401
return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type="application/json")

# JSON:API error (502 upstream)
return HttpResponse(
    b'{"errors":[{"title":"Invalid upstream response"}]}',
    status=502,
    content_type="application/json",
)
```

## Validate before done

```bash
uv run python scripts/validate_viewset_conventions.py
```

Do not extend `EXTEND_SCHEMA_EXAMPLES_ALLOWLIST` or `INLINE_DESCRIPTION_ALLOWLIST` in the validator — fix the ViewSet instead.
