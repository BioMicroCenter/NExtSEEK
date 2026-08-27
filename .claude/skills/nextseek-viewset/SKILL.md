---
name: nextseek-viewset
description: >-
  This skill should be used when working in the NExtSEEK codebase and the user
  asks to add, create, extend, or modify a nextseek_api ViewSet; register a
  router ViewSet; add a SEEK proxy or native API endpoint; project-scope an
  endpoint; write OpenAPI/Swagger schema or OpenApiExample blocks for a ViewSet;
  or run validate_viewset_conventions.py before finishing ViewSet work.
---

# NExtSEEK ViewSet conventions

Step-by-step guide for adding or extending a `nextseek_api` ViewSet. Canonical
siblings: SEEK proxy [`nextseek_api/services/studies.py`](../../../nextseek_api/services/studies.py),
superuser-native [`nextseek_api/services/users.py`](../../../nextseek_api/services/users.py),
native read [`nextseek_api/services/entity_tree.py`](../../../nextseek_api/services/entity_tree.py).

Mechanical rules live in [`scripts/validate_viewset_conventions.py`](../../../scripts/validate_viewset_conventions.py) — run it before calling the work done.

## 1. Classify the endpoint

| Question | If yes |
|---|---|
| Proxies SEEK JSON:API via `SeekAPIClient`? | **SEEK proxy** — project scope is free (SEEK filters by caller auth). |
| Reads/writes MySQL, Neo4j, or dmac tables directly? | **Native** — project-scope queries manually. |
| Only Django superusers may call it? | Use `IsDjangoSuperuser` (see §3). |
| Any authenticated lab user? | `IsAuthenticated` only. |

When **extending** an existing ViewSet, read its module first and match its auth,
scoping, and error-envelope patterns. New actions still need full `*_DESC`,
`OpenApiExample`s, tests, and a clean validator run.

## 2. Authentication (new ViewSets)

- Set `authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]`.
- Import `CsrfExemptSessionAuthentication` from [`nextseek_api/services/assistant.py`](../../../nextseek_api/services/assistant.py).
- **Token auth does not work.** Do not add `TokenAuthentication`. Do not pass `"TOKEN"` to `resolve_seek_auth`.
- Upstream SEEK calls: `resolve_seek_auth(request, ["BASIC", "SESSION"])` from [`nextseek_api/helpers.py`](../../../nextseek_api/helpers.py).

## 3. Privilege gates

- Default: `permission_classes = [IsAuthenticated]`.
- Superuser-only: `[IsAuthenticated, IsDjangoSuperuser]` — import `IsDjangoSuperuser` from [`nextseek_api/services/users.py`](../../../nextseek_api/services/users.py). Gate on `is_superuser`, not `is_staff`.
- **Do not use `IsAdminUser`.** SEEK-mirrored Django users are created with `is_staff=True`; `IsAdminUser` collapses to any authenticated user. Known live anti-patterns: [`AdminSampleViewSet`](../../../nextseek_api/views.py), [`EvaluatorViewSet`](../../../nextseek_api/services/evaluator.py).

## 4. Project-scoping

**SEEK proxy:** forward the request through `SeekAPIClient`; do not add a second project filter.

**Native:** load the caller's projects and constrain the query. See [`references/patterns.md`](references/patterns.md) for the `SeekDB.getCurrentUser()` + `project_id IN (...)` snippet. Empty project list → empty result set, not unscoped rows.

Pattern reference: [`AdminSampleViewSet.admin_retrieve_samples`](../../../nextseek_api/views.py). Do not use `resolve_user_project` for list scoping (that helper picks one project dirname for CC mounts).

Superuser-only endpoints that are intentionally global (e.g. admin user mutations) may omit native project filtering; document that choice in **USE WHEN** / **DO NOT USE WHEN**.

## 5. Pydantic request/response models

- Define models in [`nextseek_api/models.py`](../../../nextseek_api/models.py) (or a domain submodule such as `schema_rag/models.py`).
- Validate with `Model.model_validate(...)`; map `ValidationError` → HTTP 422.
- Reuse existing error envelopes — do not invent a third shape:
  - 401: `{"detail": "Authentication required"}`
  - JSON:API errors: `JsonApiErrorResponse` / `{"errors": [{"title": "...", "detail": "..."}]}`
  - Admin mutations: `AdminUserErrorResponse`

## 6. Pydantic v2 + drf-spectacular

Pydantic v2 **works** with drf-spectacular in this repo (pydantic 2.x + drf-spectacular 0.29+, OAS 3.1). Pass pydantic classes directly to `@extend_schema(request=..., responses={...})` — see [`references/patterns.md`](references/patterns.md) for a full decorator block.

Do **not** add DRF serializers solely because pydantic “does not work with spectacular.” Proof: [`test_services_users.py`](../../../nextseek_api/tests/test_services_users.py) asserts pydantic models appear in `SchemaGenerator` output.

Set `tags=["MyResource"]` and a stable `operation_id` for each action (see [`references/patterns.md`](references/patterns.md)).

## 7. Endpoint descriptions

Add a `*_DESC` constant to [`nextseek_api/endpoint_descriptions.py`](../../../nextseek_api/endpoint_descriptions.py) (or [`descriptions_evaluator.py`](../../../nextseek_api/assistant/descriptions_evaluator.py) / [`descriptions_cc.py`](../../../nextseek_api/assistant/descriptions_cc.py) when appropriate). The validator scans those three modules only — **not** [`assistant/descriptions.py`](../../../nextseek_api/assistant/descriptions.py) (legacy NS assistant prose, off spectacular). Required sections in order:

1. **SUMMARY**
2. **USE WHEN**
3. *(optional)* **DO NOT USE WHEN**
4. **ACCEPTS**
5. **RETURNS**
6. *(optional extra sections, e.g. ERROR CODES)*
7. **TRIGGER PHRASES**
8. **EXAMPLES** — at least one `- ` bullet; prefer real UIDs/IDs (see [`references/examples.md`](references/examples.md))

Import the constant into the ViewSet: `@extend_schema(description=MY_FETCH_DESC, ...)`.

Pass `description=<CONST_NAME>` — a `*_DESC` import from the description modules. **Do not** use inline string descriptions on `@extend_schema`; the validator rejects them (legacy NHP/timeline ops in [`views.py`](../../../nextseek_api/views.py) are grandfathered only).

## 8. OpenAPI examples

Every new `@extend_schema` must include a non-empty `examples=[OpenApiExample(...), ...]`.

- GET/list: at least one `response_only=True` example.
- POST/PATCH: request example plus success response example when applicable.
- Use real sample UIDs, study `746`, project `2558`, assay `351`, etc. — see [`references/examples.md`](references/examples.md).

## 9. Wire-up checklist

1. Pydantic models in `models.py`
2. `*_DESC` in `endpoint_descriptions.py`
3. ViewSet module under `nextseek_api/services/<name>.py`
4. Import alias in [`nextseek_api/views.py`](../../../nextseek_api/views.py)
5. `router.register(...)` in [`nextseek_api/urls.py`](../../../nextseek_api/urls.py)
6. Tests per §10
7. Validate:

```bash
uv run python scripts/validate_viewset_conventions.py
uv run pytest nextseek_api/tests/test_viewset_conventions.py nextseek_api/tests/test_viewset_conventions_schema.py -q
```

**Do not** add entries to `GRANDFATHER_OPS` or the derived allowlists (`EXTEND_SCHEMA_EXAMPLES_ALLOWLIST`, `INLINE_DESCRIPTION_ALLOWLIST`, `SCHEMA_EXAMPLES_OPERATION_ID_ALLOWLIST`) in `validate_viewset_conventions.py`.

## 10. Testing expectations

Every new ViewSet needs:

1. **Behavior tests** — auth gate (401 unauthenticated), privilege gate (403 non-superuser when applicable), happy path, and at least one validation/upstream-error path (422 or 502 as appropriate). Mirror [`test_services_studies.py`](../../../nextseek_api/tests/test_services_studies.py) or [`test_services_users.py`](../../../nextseek_api/tests/test_services_users.py).
2. **SchemaGenerator assertion** — new paths appear in `SchemaGenerator().get_schema()` and pydantic models land under `components.schemas` (see users/studies tests).
3. **Conventions CLI** — `uv run python scripts/validate_viewset_conventions.py` exits 0 before calling work done.

Do not extend validator grandfather allowlists; fix the ViewSet instead.

## Additional resources

- [`references/patterns.md`](references/patterns.md) — copy-paste recipes (auth, proxy skeleton, project SQL, errors).
- [`references/examples.md`](references/examples.md) — real identifiers and full `OpenApiExample` blocks.
