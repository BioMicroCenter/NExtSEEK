# SPEC — #94 schema_rag self-ingest fails once the docs routes require auth

Base: `3d71a23` on `dev-v4-merge`. Branch `fix/issue-94-authenticate-schema-selfingest`.

## The defect (reproduced live, not inferred)

`#77` (`e950d1b`) put `IsAuthenticated` on `/nextseek_api/schema/`. Two in-tree
consumers fetch that URL with no credentials.

Probe against the running stack, `docker exec nextseek`:

| probe | result |
|---|---|
| anonymous `GET /nextseek_api/schema/` | `401`, 90 bytes |
| `GET` as `demo` | `200`, 332,595 bytes |
| `POST /nextseek_api/schema_rag/ingest/` `{"schema_url": "http://127.0.0.1:8000/nextseek_api/schema/"}` as `demo` | `502 SCHEMA_FETCH_FAILED`, `"401 Client Error: Unauthorized"` |
| `ChatConfig._load_api_schema_from_remote` with valid `API_USER`/`API_PASS` set | prints `Non-200 ... 401`, returns `{}` |

Consumer A — `OpenAPISchemaProcessor.fetch_schema`
(`nextseek_api/schema_rag/schema_processor.py:139`) issues a bare
`requests.get(transport_url, timeout=...)`. Reached from both
`schema_rag/service.py:306` (`ingest_schema`) and `:747`
(`retrieve_endpoints`' auto-ingest), so **both** REST endpoints are affected,
not just ingest. Fails hard as `SCHEMA_FETCH_FAILED`.

Consumer B — `ChatConfig._load_api_schema_from_remote`
(`chat_nextseek/src/chat_nextseek/config.py:1316`) does the same and fails
**soft**: `{}` is cached under a two-attempt cap
(`config.py:293,306-339`) and `validate_request_body` returns `(True, None)`
for an unknown endpoint (`config.py:1480-1482`), so request-body validation
silently stops happening. The credentials it needs are already on the object:
`helpers/tools/nextseek_api.py:132` sends `(config.API_USER, config.API_PASS)`
as Basic on the very next line, and `orchestrator.py:197-198` binds them
per turn on a `copy.copy` of the config.

## Intended behavior

Self-ingest of this instance's own OpenAPI document — the published
`OpenApiExample` at `services/schema_rag.py:186` — succeeds, without
weakening the gate `#77` added, and without any new secret to provision.
`chat_nextseek`'s body-schema registry loads against an authenticated
instance, and says so loudly when it cannot.

## The fix chosen

**Consumer A — serve our own schema route in-process, never over HTTP.**
`fetch_schema` gains a self-check: hostname is one of ours **and** the path is
`reverse("nextseek_api:schema")`. Both halves are required — an arbitrary path
on our own host is still an arbitrary fetch. On a match the document comes from
`drf_spectacular.generators.SchemaGenerator().get_schema(request=None, public=True)`,
which is exactly what `SpectacularAPIView` serves (verified in the container:
55 paths, 205 component schemas, 75 operations after `jsonref` resolution).
`$ref` resolution is factored into one helper shared by both paths.
**Strictly additive**: any failure to generate falls through to the existing
HTTP path untouched, so nothing that worked before stops working.

Why this one:
- No credential exists to leak. The `schema_url` is fully caller-controlled, so
  *any* credential-attaching fix is an SSRF-flavoured exfiltration primitive
  unless it is gated on the same self-check — at which point the credential
  buys nothing over generating locally.
- Zero new configuration. Verified: `docker exec nextseek printenv` has no
  `API_USER`/`API_PASS`; there is no ambient service credential on the Django
  side today.
- It removes a self-HTTP call from a gunicorn worker. `gunicorn.conf.py` runs 4
  sync workers, and `config.py:307-315` documents this exact hazard biting the
  sibling consumer at cold boot.
- It is the issue's own third candidate, and reuses the `resolve_transport_url`
  self-detection precedent rather than inventing one.

**Consumer B — send the Basic credentials it already holds**, and make a
credential failure loud instead of silent. One `auth=` argument plus a
diagnostic. Fail-soft is preserved deliberately.

## Rejected

- **Forward the calling user's credentials.** Requires threading an auth
  argument view → `schema_rag/service.py::ingest_schema` → `fetch_schema`.
  `nextseek_api/schema_rag/service.py` is outside this fixer's file set and the
  batch's no-shared-file invariant forbids taking it. It would also still need
  the self-check above, or it leaks the caller's SEEK Basic password
  (`resolve_seek_auth`'s `SESSION` branch yields the real password) to any host
  named in `schema_url`.
- **A service account.** Needs a new secret provisioned in `docker/nextseek.env`
  on every existing install — an operator/deploy change this lane cannot make or
  test — and, being a standing credential, has a strictly worse leak profile
  than the caller's own under the same attack.
- **Exempting the route from `IsAuthenticated` for local callers.** Re-opens the
  332,595-byte anonymous API-surface disclosure `#77` closed; the route cannot
  tell "our own ingest" from "an anonymous caller inside the network".
- **Making `validate_request_body` fail closed.** It would start rejecting live
  request bodies whenever the schema is unavailable. Out of scope; the silence,
  not the fail-open, is what #94 asks to fix.

## Blast radius

- `fetch_schema` for every non-self URL: unchanged code path.
- `resolve_transport_url`: unchanged. Still applies to self-hosted URLs that are
  not the schema route, and to the schema route under the HTTP fallback.
- One existing test, `TestFetchSchemaUsesInternalUrl::test_fetch_schema_gets_the_internal_url`,
  asserts an HTTP GET for a self-hosted schema-route URL. Its assertion is kept
  verbatim, exercised through the fallback (generator made unavailable), and a
  new test covers the in-process path.
- `chat_nextseek`: `_load_api_schema_from_remote` only. No cap change, no
  fail-open change, no signature change.
