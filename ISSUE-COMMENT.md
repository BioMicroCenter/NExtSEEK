<!--
DRAFT ONLY — do not post. The repo is public and this fix is unreviewed.
Posting is the merger's call, after human approval, per docs/ISSUE-CONVENTIONS.md.
Branch: fix/issue-94-authenticate-schema-selfingest, base 3d71a23 on dev-v4-merge.
-->

## Fixed on `fix/issue-94-authenticate-schema-selfingest`

Both consumers named in the issue were confirmed broken against the running
stack before any edit, and both are fixed. The issue body was accurate in every
checkable claim; two line numbers had drifted by one or two lines and one
consumer count was understated (see "Corrections").

### Reproduced first (`docker exec nextseek`, base `3d71a23`)

- reproduced-live: anonymous `GET /nextseek_api/schema/` → `401`, 90 bytes;
  as `demo` → `200`, 332,595 bytes.
- reproduced-live: `POST /nextseek_api/schema_rag/ingest/` with
  `{"schema_url": "http://127.0.0.1:8000/nextseek_api/schema/"}`, authenticated
  as `demo` → `502 SCHEMA_FETCH_FAILED`,
  `"exception": "HTTP request failed: 401 Client Error: Unauthorized"`.
- reproduced-live: `ChatConfig._load_api_schema_from_remote` with valid
  `API_USER`/`API_PASS` bound printed `Non-200 when fetching schema: 401` and
  returned `{}` — the silent half.

### The ruling

Of the three candidates in the issue, the fix takes the third — special-case
self-targeted URLs — in the form that needs no credential at all: **when the
requested URL is this instance's own schema route, build the document
in-process** with
`drf_spectacular.generators.SchemaGenerator().get_schema(request=None, public=True)`
instead of fetching it over HTTP.

- test-proven: the in-process document is equivalent to the served one —
  same 55 paths, same 205 `components.schemas`, same top-level keys.
- The self-check requires **both** the hostname (`_own_public_hosts()` plus the
  `NEXTSEEK_INTERNAL_BASE_URL` host) **and** the path
  (`reverse("nextseek_api:schema")`). Host alone would let any URL on our own
  host be answered from the generator.
- Strictly additive: any failure to generate falls through to the existing HTTP
  path unchanged, so `resolve_transport_url` still governs every other
  self-hosted URL and still governs the schema route under the fallback.

Rejected, with reasons:

- **Forwarding the caller's credentials.** `schema_url` is fully
  caller-controlled, so attaching credentials is a credential-exfiltration
  primitive unless gated on the same self-check — and `resolve_seek_auth`'s
  `SESSION` branch yields the user's real SEEK Basic password. Once gated, the
  credential buys nothing over generating locally. It also needs
  `nextseek_api/schema_rag/service.py`, outside this fix's file set.
- **A service account.** `docker exec nextseek printenv` has no
  `API_USER`/`API_PASS`: there is no ambient service credential on the Django
  side today, so this needs a new secret provisioned in `docker/nextseek.env` on
  every existing install. As a standing credential it has a strictly worse leak
  profile than the caller's own under the same attack.
- **Exempting the route from `IsAuthenticated` for local callers.** Re-opens the
  disclosure #77 closed. The route cannot distinguish "our own ingest" from "an
  anonymous caller who reached the host".

The `chat_nextseek` consumer took the one-argument fix the issue predicted: it
now sends the Basic `API_USER`/`API_PASS` it already holds
(`orchestrator.py:197-198` binds them per turn; `helpers/tools/nextseek_api.py:132`
already sends them), and a 401/403 now prints a diagnostic naming the missing
credentials and stating that body-field validation is off. Fail-soft, the
two-attempt cap and `validate_request_body`'s fail-open are all deliberately
unchanged — the issue asks to fix the silence, not the fail-open.

### Corrections to the issue body

- The issue attributes the break to the ingest endpoint. `retrieve_endpoints`
  auto-ingests at `nextseek_api/schema_rag/service.py:747`, so
  `POST /nextseek_api/schema_rag/retrieve/` with a `schema_url` was broken by
  the same line. Both are fixed by the one change.
- The `OpenApiExample` that names this instance's own schema URL is the
  **retrieve** example ("NExtSEEK Full Mode Query"), not an ingest example. Both
  ingest examples name `fairdomhub.org`.

### Residual, not fixed here

`docs/endpoint-authorization-register.md:178-180` still records
`GET /nextseek_api/schema/`, `/swagger/` and `/redoc/` as `AllowAny (default;
dmac/settings.py:376-383 sets no SERVE_PERMISSIONS)`. #77 made all three
`IsAuthenticated` at the route. The register is stale and is outside this fix's
file set — worth a `type: docs` issue.
