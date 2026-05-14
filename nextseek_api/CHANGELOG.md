# Changelog

All notable changes to NExtSEEK's public API are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Opt-in API v2 via `Accept: application/vnd.nextseek.v2+json`. v1 behavior is
  unchanged; v2 introduces the envelope and error standardization below.
- `{results, count, next, previous}` list envelope for
  `POST /samples/advanced_search/` under v2 (replaces `{rows, total,
  sampleTypes, noSampleTypes, footer}`).
- JSON:API-compliant `{errors: [{status, title, detail, source.pointer,
  meta.valid_values, meta.example}]}` envelope for all 4xx/5xx responses under
  v2.
- OpenAPI 3.1 schema endpoints per version: `/nextseek_api/schema/v1/`,
  `/nextseek_api/schema/v2/`, with matching Swagger UI pages.
- Schema drift test as a CI merge gate: committed
  `nextseek_api/openapi.v2.snapshot.yaml` must match live output.
- `@extend_schema(...)` minimal + realistic + v2-error examples for every
  endpoint on the v2 contract. Response-side examples are attached via
  `responses={N: OpenApiResponse(response=..., examples=[...])}` because
  drf-spectacular silently drops `response_only=True` examples passed at the
  top level of `@extend_schema`.
- `nextseek_api.versioning.VendorMediaTypeVersioning`: a BaseVersioning subclass
  that recognizes `application/vnd.nextseek.v{1,2}+json` Accept headers and sets
  `request.version` / `request._request.version` accordingly. Required because
  DRF's stock `AcceptHeaderVersioning` parses `; version=X` parameters rather
  than vendor media-type subtypes.
- `nextseek_api.renderers.V1JSONRenderer` and `V2JSONRenderer`: per-version
  renderers that emit the correct vendor Content-Type. Wired into
  `REST_FRAMEWORK.DEFAULT_RENDERER_CLASSES`.
- `dmac.openapi_hooks.swap_versioning_for_schema_gen` (PREPROCESSING) +
  `restore_versioning_post_schema_gen` (POSTPROCESSING): a hook pair that
  temporarily swaps `VendorMediaTypeVersioning` for stock
  `AcceptHeaderVersioning` during `manage.py spectacular` (so drf-spectacular's
  hardcoded `is_versioning_supported()` accepts the views), then strips the
  `; version=X` parameter from every media-type key in the generated schema so
  the docs match what the production runtime actually serves.

### Changed
- Custom version-aware `EXCEPTION_HANDLER`
  (`nextseek_api.exception_handler.handle_api_exception`): delegates to DRF's
  default for v1, reshapes to `errors[]` for v2.
- `REST_FRAMEWORK` settings:
  `DEFAULT_VERSIONING_CLASS = 'nextseek_api.versioning.VendorMediaTypeVersioning'`,
  `DEFAULT_VERSION = 'v1'`, `ALLOWED_VERSIONS = ['v1', 'v2']`.
- New `build_v2_list_envelope` helper alongside the existing
  `paginate_rows_in_envelope` (v1 untouched).
- `SampleAdvancedSearchRequest.filter_searchText` is now `Optional[str] = ""`.
  Empty/missing `filter_searchText` no longer rejects with 422 — legitimate
  unfiltered searches succeed.
- `POST /nextseek_api/batch-upload/start/` now returns `400 Bad Request` with a
  v2-shaped error envelope when `project_id` is missing or not an integer
  (previously returned `422` from Pydantic before view logic could shape the
  response).

### Fixed
- `GET /nextseek_api/samples/{uid}/` now passes through upstream 4xx responses
  with the original status code. Previously, `samples.py:retrieve` called
  `SampleSingleResponse.model_validate(...)` on every upstream body — including
  4xx error JSON — and re-shaped the response to `502 Invalid upstream response`
  on validation failure. A 4xx-early-return between fetch and validation
  preserves the real upstream status (e.g., 404 stays 404).
- `nextseek_api/batch_upload/views.py` `cancel` decorator declares its real 404
  response (raised by `_check_ownership` when the requesting user does not own
  the job). Previously declared only `204`; clients had no schema for the 404
  case.
- Test `fake_run_query` / `fake_run` mocks across the chat-assistant test suite
  now accept `**kwargs` to absorb the `credentials=` keyword argument added to
  `chat_nextseek.orchestrator.run_query`. Pure stale-mock fix; production code
  unchanged.

### Deprecated
- v1 envelope keys `rows`, `total`, `sampleTypes`, `noSampleTypes`, `footer` on
  `samples/advanced_search` — still emitted under no Accept header or v1 Accept
  header. No removal timeline.

### Notes for external consumers
- No hard break. Clients sending `Accept: application/json` or no Accept header
  continue to receive the v1 shape indefinitely.
- JSON:API single-item endpoints (`GET /samples/{uid}/` etc.) were already on
  `{data, jsonapi}` and are unchanged in both versions.
- Graph-shaped endpoints (`/sample-tree/`, `/entity_tree/*`) retain their
  bespoke `{nodes, rels}` / `{total, nodes}` shapes in both versions.

### Schema regeneration
After modifying any v2 endpoint decorator, regenerate the snapshot:
```bash
source /tmp/nextseek_test_env.sh  # or your equivalent env script
PYTHONPATH=/opt/NExtSEEK ./.venv/bin/python -c "
import io, yaml
from django.core.management import call_command
import django; django.setup()
buf = io.StringIO()
call_command('spectacular', api_version='v2', stdout=buf)
open('nextseek_api/openapi.v2.snapshot.yaml', 'w').write(
    yaml.safe_dump(yaml.safe_load(buf.getvalue()), sort_keys=False)
)
"
```
Or, equivalently, regenerate via the same pinned `@override_settings` path that
`nextseek_api/tests/test_schema_drift.py::_generate_schema()` uses. Both paths
share the same hook chain since `dmac/settings.py SPECTACULAR_SETTINGS` and the
test's pinned settings now register the same PREPROCESSING/POSTPROCESSING hooks.

The schema-drift test (`test_snapshot_matches_live_schema_generation`) is a
hard CI gate — any change that affects the schema must be accompanied by a
regenerated snapshot.
