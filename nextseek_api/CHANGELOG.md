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
- `@extend_schema(examples=[...])` minimal + realistic + v2-error examples for
  every endpoint on the v2 contract.

### Changed
- Custom version-aware `EXCEPTION_HANDLER` (`nextseek_api.exception_handler.handle_api_exception`):
  delegates to DRF default for v1, reshapes to `errors[]` for v2.
- `REST_FRAMEWORK` settings: `DEFAULT_VERSIONING_CLASS =
  AcceptHeaderVersioning`, `DEFAULT_VERSION = 'v1'`, `ALLOWED_VERSIONS = ['v1', 'v2']`.
- New `build_v2_list_envelope` helper alongside the existing
  `paginate_rows_in_envelope` (v1 untouched).

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
