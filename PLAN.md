# PLAN — #94

Four independent tasks. Each carries its own test and must be proven RED against
pre-fix code before the implementation lands.

Baselines measured at `3d71a23` in this worktree, scope
`nextseek_api seek/tests --ignore=nextseek_api/cc_assistant/tests/test_router_history_plumbing.py --ignore=nextseek_api/cc_assistant/tests/test_agent_history_conversion.py`:

- `-m "not host_only"` → **3 failed, 4175 passed, 61 skipped, 176 deselected, 4 xfailed**
- `-m "host_only"` → **35 failed, 141 passed, 4243 deselected**
- `chat_nextseek` (`-w /work/chat_nextseek`, `tests/ --ignore=tests/evaluator`) → **4 failed, 841 passed, 2 xpassed**

File set: `nextseek_api/schema_rag/schema_processor.py`,
`nextseek_api/services/schema_rag.py`,
`chat_nextseek/src/chat_nextseek/config.py`, and tests covering them.
`nextseek_api/schema_rag/service.py` is **out of set** — do not edit it.

---

## Task 1 — self-schema detection in `schema_processor.py`

Add, next to `resolve_transport_url`:

- `_own_hosts()` — union of `_own_public_hosts()` and the hostname of
  `NEXTSEEK_INTERNAL_BASE_URL`. **Do not modify `_own_public_hosts()`**: adding
  the internal host there would change `resolve_transport_url`'s behavior and
  break `TestResolveTransportUrl`.
- `self_schema_route_path()` — `reverse("nextseek_api:schema")`, `None` on any
  exception (module supports standalone import; see `_own_public_hosts`).
- `is_self_schema_url(url) -> bool` — True only when hostname ∈ `_own_hosts()`
  **and** `urlsplit(url).path.rstrip("/") == route.rstrip("/")`.

Tests (`nextseek_api/tests/test_schema_rag_self_ingest.py`, new):
own public host + schema path → True; own host + a different path → False;
foreign host + schema path → False; internal base host + schema path → True;
`ALLOWED_HOSTS=["*"]` does not make the world ours; a non-URL string → False;
query string and trailing-slash variants still match.

Settings hygiene: use `override_settings(ALLOWED_HOSTS=[...])` and an explicit
env context manager. Never rely on the lane-copied `dmac/local_settings.py`.

---

## Task 2 — in-process generation in `fetch_schema`

- Factor the existing Step 3 `$ref` block into `_resolve_refs(parsed, schema_url)`
  and have the HTTP path call it. No behavior change.
- Add `_generate_own_schema()`: lazily import
  `drf_spectacular.generators.SchemaGenerator`, call
  `get_schema(request=None, public=True)`, return the dict, or `None` on any
  failure (import error, generation error, non-dict result) with a logged
  warning.
- At the top of `fetch_schema`: if `is_self_schema_url(schema_url)` and
  `_generate_own_schema()` returns a dict, return `_resolve_refs(...)` of it.
  Otherwise fall through to the untouched HTTP path.

Tests (same new file):
- self URL → `requests.get` is never called (patch it to `assert False`) and the
  result has `paths`.
- self URL with the generator patched to raise → HTTP path runs and the GET goes
  to the **internal** URL (this is the preserved assertion from
  `test_fetch_schema_gets_the_internal_url`).
- external URL → generator never consulted, HTTP GET on the original URL.
- `ingest_schema(IngestRequest(schema_url=<self url>))` returns `success=True`
  with `num_endpoints > 0` — the issue's own confirm-fixed recipe, driven
  through the out-of-set service layer without editing it.

Also update `nextseek_api/tests/test_schema_processor.py::TestFetchSchemaUsesInternalUrl::test_fetch_schema_gets_the_internal_url`
to disable the generator, keeping its assertion identical, with a docstring
naming #94.

---

## Task 3 — `chat_nextseek` sends its Basic credentials

In `_load_api_schema_from_remote` (`config.py:1316`):

- `auth = (self.API_USER, self.API_PASS) if self.API_USER and self.API_PASS else None`,
  passed to `requests.get`. Use `getattr(self, ..., None)` for safety.
- On a non-200, print a distinct line for 401/403 that names
  `API_USER`/`API_PASS` and says body-field validation is now off. **Never print
  the password or a mask of it** (`orchestrator.py:200-201` sets that rule).
- Nothing else changes: no cap change, no fail-open change, no new attribute.

Tests (`chat_nextseek/tests/test_config_api_schema_auth.py`, new):
- credentials present → `requests.get` receives `auth=("u", "p")`. RED pre-fix.
- credentials absent → `auth=None`, still no raise, still `{}`.
- 401 → returns `{}`, and stdout carries the credential diagnostic without the
  password value.

Run lane: the `chat_nextseek` suite, separately, per `chat_nextseek/CLAUDE.md`.

---

## Task 4 — pin the documented example (`services/schema_rag.py`)

The `OpenApiExample` at `services/schema_rag.py:186` is the thing #94 says is
broken. Add a guard test that the URL it advertises still has the schema route's
path, so a future edit that moves the example off the self-ingest path is caught.
No production edit expected in this file unless the guard fails.

Test lives in the new `nextseek_api/tests/test_schema_rag_self_ingest.py`.

---

## Verification

Both Django lanes at the exact scope above, plus the `chat_nextseek` suite,
compared against the recorded baselines. Any new failure is mine until proven
pre-existing at `3d71a23`. Then a live re-probe of the two `docker exec` checks
is **not** possible for consumer A without an image rebuild (forbidden), so the
live evidence stops at the pre-fix reproduction; say so plainly in FINDINGS.
