# Endpoint authorization register (`nextseek_api` read surface)

Issue #64 deliverable. Branch `dev-v4-merge`. Compiled 2026-08-11.

## What this is

A classification of every **read** endpoint routed by the `nextseek_api` app, with a
`file:line` citation for whether any project-membership predicate is applied to the data
the endpoint returns, and a **proposed** authorization bucket for each.

## What this is not

- It is **not** a code sweep. No `permission_classes` were changed to produce it.
- The proposed buckets are **proposals for the user to rule on**, not decisions. Nothing in
  this document has been applied.
- Exactly **one** endpoint's behavior changed in this branch: `sample-tree/{uid}/tree/` is now
  project-scoped (commit `665a103`, issue #60). Everything else is described exactly as the tree
  is today.
- The other endpoint named in #60, `samples/advanced_search/`, was **deliberately not changed**.
  The reasons are in note A, and they are the kind of thing this register exists to surface.

**Authority for what is routed:** `nextseek_api/urls.py`, mounted at `/nextseek_api/` by
`dmac/urls.py:29`. Two registrations are commented out at `nextseek_api/urls.py:14-15`, so
`NHPViewSet` (`nextseek_api/views.py:274`) and `SampleQueryViewSet` (`nextseek_api/views.py:428`)
are **NOT ROUTED** and are excluded from the register proper (see the notes at the end).
The routed list below was cross-checked against Django's live resolver inside the `nextseek`
container, not just read off the router registrations.

**Line-number caveat:** several files were being edited in this working tree while the register
was compiled (a UTF-8 Basic-auth fix in `nextseek_api/helpers.py`, `seek/seekapi.py` and
`seek/views.py`; then the #60 scoping fix in `nextseek_api/views.py`). Every citation below was
read from the tree at compile time. Citations into `nextseek_api/views.py` below the sample-tree
viewset have since shifted by roughly +75 lines; the surrounding symbol names are the reliable
anchor, not the numbers.

---

## THE OPEN QUESTION: does `is_staff` still mean "admin"?

This is unfiled and it is the headline decision. It is a **product decision**, not a bug
report, and this section does not propose a fix.

### The three facts

**1.** The one read endpoint that actually implements project scoping decides admin like this
(`nextseek_api/views.py:642`):

```python
is_superuser = bool(getattr(request.user, 'is_superuser', False) or getattr(request.user, 'is_staff', False))
```

**2.** Every SEEK user synchronized into NExtSEEK is marked staff. `dmac/views.py:80` (the
create branch) and `dmac/views.py:97` (the update branch, which runs on **every** login) both
read:

```python
user.is_staff = 1
```

**3.** Net effect: `is_superuser or is_staff` is true for essentially every account, so
`getChildrenUIDs(requested_uids, user_project_ids, is_superuser)` at `nextseek_api/views.py:686`
takes the admin branch at `seek/dbtable_sample.py:901-905` (`WHERE uuid IN (...)`, no project
join) rather than the scoped branch at `seek/dbtable_sample.py:907-913`
(`... JOIN projects_samples ps ... AND ps.project_id IN (...)`). The same bypass applies to
the MySQL fallback path at `nextseek_api/views.py:710-716` versus `:717-729`. The project ids
are resolved correctly and for real at `nextseek_api/views.py:621-627`; they are simply not
reached.

The codebase already states this in two places. `nextseek_api/permissions.py:8-16`:

> Deliberately NOT `rest_framework.permissions.IsAdminUser`: that checks `is_staff`, and
> `dmac.views.userSynchronization` sets `is_staff = 1` on every SEEK user at login
> (dmac/views.py:80 and :97, on both the create and the update branch). `IsAdminUser` is
> therefore equivalent to `IsAuthenticated` in this project. `is_superuser` is never assigned
> by any live application code path, so it is the only trustworthy admin signal.

And the explicit comment at `nextseek_api/views.py:628-641`, which opens:

> `# SECURITY, known gap — still open, deliberately. Read before touching this line.`

and closes (quoted verbatim except that the source's two em-dashes are rendered here as commas,
per this document's style):

> The prerequisite this comment used to name, resolving the caller's projects for real, is now
> done above, so dropping the is_staff clause is a safe one-line change on its own terms. It is
> NOT made here because it changes what every staff account can read (11 of 20 accounts in the
> local seed) and would narrow the assistant and container-CC consumers, which currently depend
> on unfiltered reads. Assess that impact first, then drop it.

The comment's "11 of 20 accounts in the local seed" was independently re-verified against the
running stack and is still exact.

Note the comment is more specific than "we left it because it would break things": it records
that the technical prerequisite is satisfied and that what remains is an impact assessment.
This section is that assessment. For contrast, the legacy path this mirrors,
`seek/views.py:1249` inside `adminRetrieveSamples`, uses `verifySuperUser(request)`
(`seek/views.py:748-754`), which tests `is_superuser` alone.

### The question

**What breaks if `is_staff` stops implying admin?**

### Who is affected, by account

Counted live against the local seed DB:

| Population | Count | Effect of dropping the `is_staff` clause |
|---|---|---|
| Total Django users | 20 | |
| `is_staff or is_superuser` (unfiltered today) | 11 | |
| `is_superuser` | 8 | No change, still unfiltered |
| `is_staff` and not `is_superuser` | 3 | Flip to project-scoped reads |
| Neither | 9 | No change, already project-scoped |

The 3 staff-not-superuser accounts are `cdemu`, `fallback_user` and `user`. That shape matters
more than the count: `dmac/views.py:80,97` sets `is_staff` and never sets `is_superuser`, and
`nextseek_api/permissions.py:14-15` states `is_superuser` is never assigned by live application
code. So **on any instance where accounts arrive via SEEK login, every account is
staff-not-superuser**. The local seed's 8 superusers are hand-made fixtures. The practical blast
radius is therefore "every real user", not "3 accounts".

### The concrete consumers

All four consumer families below call `/nextseek_api/admin/samples/retrieve/` **as the end
user**, not as a service account. That is the crux: their `request.user` is a staff account, so
they all take the unfiltered branch today, and they would all become project-scoped together.

| # | Consumer | Entry point | Identity it authenticates as |
|---|---|---|---|
| 1 | Browser sample-download controls | `static/js/ns_sample_download.js:10` sets `ENDPOINT = "/nextseek_api/admin/samples/retrieve/"`; loaded by `seek/templates/newSearch.html:3`, `seek/templates/searchAdvanced.html:3`, `seek/templates/pages/samples.embed.html:1` | Django session cookie + CSRF, i.e. the logged-in user |
| 2 | NExtSEEK assistant (`chat_nextseek` engine, in-process) | endpoint allowlisted at `chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py:39`; outbound Basic auth built at `:132` from `config.API_USER/API_PASS`; report path at `chat_nextseek/src/chat_nextseek/reports/metadata.py:66` | The caller. `nextseek_api/services/assistant.py:287-302` and `:761-766` overwrite `API_USER`/`API_PASS` on a per-request `ChatConfig` copy with the credentials `resolve_seek_auth` returned |
| 3 | Container-CC agent, via the ns-sidecar | sidecar forwards ops to `/nextseek_api/assistant/{op}/` (`docker/ns-sidecar/app/ns_client.py:97`); the `api-read` op reaches this path because it is allowlisted at `nextseek_api/assistant/read_safe_endpoints.json:39` and gated by `nextseek_api/assistant/write_gate.py:94` | The caller. The sidecar holds no credentials of its own; per-request Basic auth is built from the `ns_login` frame at `docker/ns-sidecar/app/server.py:40-47` |
| 4 | LLM endpoint catalogs that steer both engines toward it | `chat_nextseek/src/chat_nextseek/context/min_api_endpoints.json:3`, `.../min_api_endpoints_enriched.json:3,71`, and the image copies under `docker/cc-runtime/build_context/plugins/nextseek/context/` | n/a, prompt context |

The **one** exception to "always the end user" is the admin-only PROD toggle: when a turn routes
to the PROD `ChatConfig`, `nextseek_api/services/assistant.py:293-297` and `:775-779` substitute
the configured `API_USER`/`API_PASS` instead. That is a genuine service identity, and its scope
would be whatever that account's SEEK projects are.

`/nextseek_api/entity_tree/lineage/` is also allowlisted for both engines
(`nextseek_api/assistant/read_safe_endpoints.json:51`) and recommended to the model by
`nextseek_api/endpoint_descriptions.py:18`, but it has no project predicate at all today, so
tightening `admin/samples` does not touch it. `entity_tree/nodes`, `edges` and `edge_attributes`
have **no consumer anywhere in the worktree**.

### What the user is actually being asked to decide

1. **Should ordinary users see only their own projects' samples in downloads and in assistant
   answers?** SEEK itself answers yes for its own resources (the proxy endpoints in the table
   below already inherit that). NExtSEEK's direct-DB and Neo4j endpoints answer no.
2. **If yes, who is admin?** `is_superuser` is the only trustworthy signal today, and no live
   code path grants it. Some set of curator and operator accounts would need it granted
   explicitly, and that list has to come from the user.
3. **Should the assistant and container-CC read as the end user (current behavior) or as a
   designated service identity?** These have opposite consequences. As the end user, scoping
   applies and answers narrow per user. As a service identity, scoping is centralized in one
   account, but every user's answer is that account's view.
4. **Should a scoped read tell the caller that rows were withheld?** Today the scoped branches
   at `seek/dbtable_sample.py:907-913` and `nextseek_api/views.py:717-729` silently return
   fewer rows. An assistant cannot distinguish "no such data" from "not your project", which is
   a correctness problem for generated answers regardless of which way question 1 is decided.
5. **What is the measurement cost?** Assistant ground-truth values in `nessie_tests` were all
   measured against unfiltered reads. Scoping changes the expected answers, so the corpus needs
   a re-baseline against whichever account the harness runs as.

Note that a cross-project export path already exists and is correctly gated:
`GET /nextseek_api/admin/project-export/{pk}/` is `IsSuperUser` (`nextseek_api/services/project_export.py:267`).

---

## Register

55 routed read endpoints. `permission_classes` values are the declared class list; several
endpoints add a second inline auth gate inside the handler, which is noted where it matters.

| Path | Viewset / action | permission_classes | Project predicate applied? (file:line) | Proposed bucket |
|---|---|---|---|---|
| `GET /nextseek_api/schema/` | `SpectacularAPIView` | `IsAuthenticated` at the route (`nextseek_api/urls.py:61`, #77). Was AllowAny: drf-spectacular sets `permission_classes` from `SERVE_PERMISSIONS` in its own class body, shadowing the project default | n/a, no data | public-to-authenticated |
| `GET /nextseek_api/swagger/` | `SpectacularSwaggerView` | `IsAuthenticated` at the route (`nextseek_api/urls.py:62`, #77) | n/a, no data | public-to-authenticated |
| `GET /nextseek_api/redoc/` | `SpectacularRedocView` | `IsAuthenticated` at the route (`nextseek_api/urls.py:63`, #77) | n/a, no data | public-to-authenticated |
| `GET /nextseek_api/sample-tree/{uid}/tree/` | `SampleTreeViewSet.get_tree` | `IsAuthenticated` (`views.py:109`) | **Yes, added in this branch** (`665a103`): root gate + lineage pruning against `projects_samples`, admin bypass on `is_superuser` alone. Pre-fix: none | project-scoped (done) |
| `POST /nextseek_api/samples/advanced_search/` | `SampleAdvancedSearchViewSet.create` | `IsAuthenticated` (`services/samples.py:357`) | **None. Deliberately NOT changed** in this branch, see note A | project-scoped (open, blocked) |
| `POST /nextseek_api/admin/samples/retrieve/` | `AdminSampleViewSet.admin_retrieve_samples` | `IsAuthenticated` (`views.py:537`) | Yes but bypassed for staff: `views.py:686` -> `seek/dbtable_sample.py:907-913`; bypass at `views.py:642`. See note B | project-scoped |
| `GET /nextseek_api/samples/{uid}/` | `SampleProxyViewSet.retrieve` | `IsAuthenticated` (`services/samples.py:74`) | Delegated to SEEK under the caller's creds (`services/samples.py:129` -> `helpers.py:135-148`) | project-scoped (already, upstream) |
| `GET /nextseek_api/sample_types/` | `SampleTypeProxyViewSet.list` | `IsAuthenticated` (`services/sample_types.py:58`) | Delegated to SEEK (`services/sample_types.py:89`) | public-to-authenticated |
| `GET /nextseek_api/sample_types/{uid}/` | `SampleTypeProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/sample_types.py:120`) | public-to-authenticated |
| `GET /nextseek_api/sampletypes/{uid}/child_types/` | `SampleTypeChildrenViewSet.child_types` | `IsAuthenticated` (`services/sample_types.py:216`) | **None.** Raw Neo4j at `services/sample_types.py:265-277`. See note C | project-scoped |
| `POST /nextseek_api/sample_types/get_parents/parents_by_child_types/` | `SamplesByChildTypesViewSet.parents_by_child_types` | `IsAuthenticated` (`services/sample_types.py:328`) | **None.** Raw Neo4j at `services/sample_types.py:400-434`. See note C | project-scoped |
| `GET /nextseek_api/entity_tree/nodes/` | `EntityTreeViewSet.list_nodes` | `IsAuthenticated` (`services/entity_tree.py:85`) | **None.** `SELECT ... FROM dmac.sample_types_context` at `services/entity_tree.py:138-148`. See note D | public-to-authenticated |
| `GET /nextseek_api/entity_tree/edges/` | `EntityTreeViewSet.list_edges` | `IsAuthenticated` (same) | **None.** Cypher at `services/entity_tree.py:303-311` | public-to-authenticated |
| `GET /nextseek_api/entity_tree/edge_attributes/` | `EntityTreeViewSet.list_edge_attributes` | `IsAuthenticated` (same) | **None.** Cypher at `services/entity_tree.py:388-397` | public-to-authenticated |
| `POST /nextseek_api/entity_tree/lineage/` | `EntityTreeViewSet.lineage` | `IsAuthenticated` (same) | **None.** Auth-only gate at `services/entity_tree.py:846-850`; walks Neo4j from caller-supplied ids. See note D | project-scoped |
| `GET /nextseek_api/sops/` | `SopProxyViewSet.list` | `IsAuthenticated` (`services/sops.py:48`) | Delegated to SEEK (`services/sops.py:84` -> `helpers.py:135-148`) | public-to-authenticated |
| `GET /nextseek_api/sops/{uid}/` | `SopProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/sops.py:140`) | public-to-authenticated |
| `POST /nextseek_api/sops/download/` | `SopProxyViewSet.download` | `IsAuthenticated` (same) | Delegated to SEEK, blob streamed under caller creds (`services/content_blobs.py:220-221` -> `helpers.py:334-342`). See note E | public-to-authenticated |
| `GET /nextseek_api/data_files/` | `DataFileProxyViewSet.list` | `IsAuthenticated` (`services/data_files.py:46`) | Delegated to SEEK (`services/data_files.py:79`) | public-to-authenticated |
| `GET /nextseek_api/data_files/{uid}/` | `DataFileProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/data_files.py:139`) | public-to-authenticated |
| `POST /nextseek_api/data_files/download/` | `DataFileProxyViewSet.download` | `IsAuthenticated` (same) | Delegated to SEEK, streamed under caller creds (`services/data_files.py:439-446`). See note E | public-to-authenticated |
| `GET /nextseek_api/projects/` | `ProjectProxyViewSet.list` | `IsAuthenticated` (`services/projects.py:40`) | Delegated to SEEK (`services/projects.py:72`) | public-to-authenticated |
| `GET /nextseek_api/projects/{uid}/` | `ProjectProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/projects.py:136`) | public-to-authenticated |
| `GET /nextseek_api/people/` | `PeopleProxyViewSet.list` | `IsAuthenticated` (`services/people.py:34`) | Delegated to SEEK (`services/people.py:65`) | public-to-authenticated |
| `GET /nextseek_api/people/{uid}/` | `PeopleProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/people.py:127`) | public-to-authenticated |
| `GET /nextseek_api/people/current/` | `PeopleProxyViewSet.current` | `IsAuthenticated` (same) | Delegated to SEEK; the answer *is* the caller identity (`services/people.py:182`) | public-to-authenticated |
| `GET /nextseek_api/investigations/` | `InvestigationProxyViewSet.list` | `IsAuthenticated` (`services/investigations.py:38`) | Delegated to SEEK (`services/investigations.py:69`) | public-to-authenticated |
| `GET /nextseek_api/investigations/{uid}/` | `InvestigationProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/investigations.py:116`) | public-to-authenticated |
| `GET /nextseek_api/studies/` | `StudyProxyViewSet.list` | `IsAuthenticated` (`services/studies.py:38`) | Delegated to SEEK (`services/studies.py:69`) | public-to-authenticated |
| `GET /nextseek_api/studies/{uid}/` | `StudyProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/studies.py:116`) | public-to-authenticated |
| `GET /nextseek_api/assays/` | `AssayProxyViewSet.list` | `IsAuthenticated` (`services/assays.py:37`) | Delegated to SEEK (`services/assays.py:68`) | public-to-authenticated |
| `GET /nextseek_api/assays/{uid}/` | `AssayProxyViewSet.retrieve` | `IsAuthenticated` (same) | Delegated to SEEK (`services/assays.py:115`) | public-to-authenticated |
| `GET /nextseek_api/users/` | `UsersViewSet.list` | `IsAuthenticated, IsDjangoSuperuser` (`services/users.py:361`) | **None**, full-table read at `services/users.py:385`. Correctly gated instead. See note F | admin-only |
| `GET /nextseek_api/users/{uid}/` | `UsersViewSet.retrieve` | `IsAuthenticated, IsDjangoSuperuser` (same) | **None**, id lookup at `services/users.py:427` | admin-only |
| `POST /nextseek_api/schema_rag/ingest/` | `SchemaRAGViewSet.ingest` | `IsAuthenticated` (`services/schema_rag.py:50`) | n/a, ingests a caller-supplied OpenAPI URL. No NExtSEEK data. See note G | public-to-authenticated |
| `POST /nextseek_api/schema_rag/retrieve/` | `SchemaRAGViewSet.retrieve_endpoints` | `IsAuthenticated` (same) | n/a, but NOT read-only: it auto-ingests when no live session exists, so it inherits every side effect of `ingest`. Classified WRITE for the CC agent (#86). See note G | public-to-authenticated |
| `GET /nextseek_api/assistant/me/` | `AssistantViewSet.me` | `IsAuthenticated, UserInParticipatingProject` (`services/assistant.py:411`) | n/a, echoes `request.user`. See note H | public-to-authenticated |
| `GET /nextseek_api/assistant/sessions/` | `AssistantViewSet.list_sessions` | same (`services/assistant.py:411`) | Owner-scoped, not project: `filter(user=request.user)` at `services/assistant.py:494` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/assistant/sessions/{sid}/` | `AssistantViewSet.get_session` | same | Owner-scoped: `services/assistant.py:554` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/assistant/sessions/{sid}/bundles/{bid}/` | `AssistantViewSet.download_bundle` | same | Owner-scoped: `services/assistant.py:1007` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/assistant/sessions/{sid}/bundles/{bid}/artifacts/{key}/` | `AssistantViewSet.download_artifact` | same | Owner-scoped: `services/assistant.py:1051` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/assistant/tasks/{task_id}/progress/` | `AssistantViewSet.task_progress` | same | Owner-scoped: `get(task_id=..., user=request.user)` at `services/assistant.py:959-962` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/assistant/test-cases/` | `AssistantViewSet.test_cases` | same, plus inline `is_staff or is_superuser` at `services/assistant.py:1195` | n/a, static prompt catalog. The inline gate is `is_staff`, so effectively any account | public-to-authenticated |
| `GET /nextseek_api/cc-assistant/tasks/{task_id}/progress/` | `CCAssistantViewSet.task_progress` | `IsAuthenticated` (`services/cc_assistant.py:355`) | Owner-scoped: `services/cc_assistant.py:744-745` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/cc-assistant/upload/status/{job_id}/` | `CCAssistantViewSet.upload_status` | same | Owner-scoped: `user_owns_job(request.user.pk, job_id)` at `services/cc_assistant.py:815-816` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/cc-assistant/upload/list/` | `CCAssistantViewSet.upload_list` | same | Owner-scoped by construction: dir built from caller creds + username at `services/cc_assistant.py:834-840` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/cc-assistant/artifacts/{session}/download/` | `CCAssistantViewSet.download_artifact` | same | Owner-scoped: `services/cc_assistant.py:849`, plus path guards at `:851, :862-864` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/cc-assistant/transcript/{session}/{turn}/` | `CCAssistantViewSet.recover_transcript` | same | Owner-scoped: `services/cc_assistant.py:894` then `:898` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/evaluator/tasks/{task_id}/retry-context/` | `EvaluatorViewSet.retry_context_by_task` | `IsAuthenticated, IsAdminUser` (`services/evaluator.py:420`) | **None, and no owner check**: `get(task_id=task_id)` at `services/evaluator.py:438-440`. See note I | admin-only |
| `GET /nextseek_api/evaluator/sessions/{sid}/bundles/{bid}/retry-context/` | `EvaluatorViewSet.retry_context_by_bundle` | same | **None, and no owner check**: `services/evaluator.py:470` | admin-only |
| `GET /nextseek_api/evaluator/runs/` | `EvaluatorViewSet.runs_list` | same | **None.** All users' tasks at `services/evaluator.py:517`; `user_id` at `:530-533` is an optional caller-supplied filter, not a predicate | admin-only |
| `GET /nextseek_api/batch-upload/` | `BatchUploadViewSet.list` | `IsAuthenticated` (`batch_upload/views.py:101`) | Owner-scoped: `list_jobs(user_id=request.user.pk, ...)` at `batch_upload/views.py:630-632` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/batch-upload/status/{job_id}/` | `BatchUploadViewSet.job_status` | same | Owner-scoped: `_check_ownership` at `batch_upload/views.py:538` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/batch-upload/summary/{job_id}/` | `BatchUploadViewSet.summary` | same | Owner-scoped: `_check_ownership` at `batch_upload/views.py:589` | public-to-authenticated (owner-scoped) |
| `GET /nextseek_api/admin/project-export/{pk}/` | `ProjectExportViewSet.retrieve` | `IsAuthenticated, IsSuperUser` (`services/project_export.py:267`) | **None on the caller's own membership**: `project_id` comes from the URL (`services/project_export.py:316` -> `:197`). Superuser gate is the whole control. See note J | admin-only |

### Bucket totals

| Bucket | Count |
|---|---|
| public-to-authenticated | 42 (of which 14 are owner-scoped) |
| project-scoped | 7 |
| admin-only | 6 |
| **Total** | **55** |

### NOT ROUTED

Registered lines are commented out at `nextseek_api/urls.py:14-15`. These are dead surface and
carry no current exposure. They are listed so a future re-enable is a deliberate act.

| Would-be path | Viewset / action | permission_classes | Project predicate | Note |
|---|---|---|---|---|
| `GET /nextseek_api/nhp/{pk}/info/` | `NHPViewSet.info` (`views.py:301`) | `IsAuthenticated` (`views.py:279`) | **NOT ROUTED** | `save_nhp_info_to_json(pk)` |
| `GET /nextseek_api/nhp/{pk}/events/{type}/{date}/` | `NHPViewSet.events` (`views.py:345`) | same | **NOT ROUTED** | |
| `GET /nextseek_api/nhp/{pk}/timeline/` | `NHPViewSet.timeline` (`views.py:379`) | same | **NOT ROUTED** | |
| `GET /nextseek_api/nhp/{pk}/download/` | `NHPViewSet.download` (`views.py:405`) | same | **NOT ROUTED** | Would stream an xlsx |
| `POST /nextseek_api/sample-queries/retrieve/` | `SampleQueryViewSet.retrieve_samples` (`views.py:465`) | `IsAuthenticated` (`views.py:433`) | **NOT ROUTED**. Would be the only caller passing a real `project_id` (`views.py:505`) | See note A |

---

## Per-family notes

### Note A: `samples/advanced_search` and the dead `project_id` hook

`seek/dbtable_sample.py:3841` declares:

```python
def searchAdvanced(self, user_seek, filters, searchType, project_id=0, skip_tree=False):
```

and the predicate is appended only when the argument is positive
(`seek/dbtable_sample.py:3913-3917`):

```python
if 'project_id' in filtersdic:
    project_id = filtersdic['project_id']
    if int(project_id)>0:
        sqlquery_filter = sqlquery_filter.replace('WHERE ', 'WHERE (')
        sqlquery_filter = sqlquery_filter + ") AND D.project_id=" + str(project_id)
```

The `projects_samples D` join that the predicate needs is likewise conditional
(`seek/dbtable_sample.py:1754-1755`), and the value flows in via
`__initSearchFilters` (`seek/dbtable_sample.py:1907, :1915`), default `0`.

The routed API path never supplies it. `nextseek_api/services/samples.py:509` calls:

```python
raw = DBtable_sample().searchAdvanced(user_seek, sub_filters, search_type, skip_tree=True)
```

with `search_type` in the third positional slot and `skip_tree` as a keyword, so `project_id`
takes its default of `0` and the predicate is never appended. Confirmed by grep: the only two
callers of `searchAdvanced` in application code are `nextseek_api/views.py:505` (inside
`SampleQueryViewSet`, **NOT ROUTED**) and `nextseek_api/services/samples.py:509`. In the routed
surface the `project_id` hook at `dbtable_sample.py:3915` is therefore **dead code today**.

**Status in this branch: NOT CHANGED, deliberately.** The plan was to generalize the
`dbtable_sample.py:3915` hook from `= project_id` to an `IN (...)` set and pass the caller's
SEEK project ids through. `SampleTreeViewSet.get_tree` was scoped that way and landed
(`665a103`). This endpoint was not, for three reasons, in descending order of weight.

**A1. The hook that was to be "generalized" emits invalid SQL for the searchType this endpoint
actually uses.** Not a theoretical concern; run against the live code in the container:

```
UIDs     + project 7 -> " WHERE (A.uuid in ('NHP-1','TIS-2');) AND D.project_id=7"
Advanced + project 7 -> "WHERE (A.json_metadata LIKE '%blood%' ) AND D.project_id=7"
```

The UID branch is a **syntax error**: `__designSearchMatchKeywords` terminates its clause with a
semicolon (`seek/search.py:368`), and the hook's `replace('WHERE ', 'WHERE (')` + `") AND ..."`
surgery wraps that semicolon inside the parentheses. That branch is not an edge case, it is the
fast path this endpoint takes for every UID term (`nextseek_api/services/samples.py:511`,
`search_type="UIDs"`). Turning the hook on would 500 every UID search. So the change is not
"generalize one line", it is "rewrite the clause assembly", and there is no routed caller today
whose tests would protect that rewrite (the hook's only real-value caller, `views.py:505`, is
NOT ROUTED).

While proving this, a second latent defect surfaced in the same builder: with
`searchType="Advanced"` and `searchText=None`, `designSearchPubmed` returns a bare `''`
(`seek/search.py:303`) while its caller unpacks two values (`seek/search.py:609`), raising
`ValueError: not enough values to unpack (expected 2, got 0)`. Independent of project scoping.
Worth a separate issue.

**A2. Silent row-dropping here is a data-integrity bug, not a visibility change.** The
container-CC batch-upload client calls this endpoint to recover the pre-update state of samples
(`docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_client.py:179`,
`search_samples_by_uid`). If scoping silently drops a row, the runner sees the UID as absent and
takes the **create** branch instead of **update**. `nextseek_api/batch_upload/views.py` performs
no project-membership check of its own, so a curator operating outside their SEEK project
memberships would silently fork records rather than update them. Any scoping added here needs an
explicit "N rows withheld by scope" signal first, which is question 4 in the headline section.

**A3. The blast radius is every search surface at once**, and all of them authenticate as the
real end user (no service account): the SEEK simple-search page
(`seek/templates/pages/samples_newsearch.embed.html:95`), the advanced-search page
(`seek/templates/pages/searchAdvanced_newsearch.embed.html:157`), the `chat_nextseek` NS engine,
and container-CC through both the sidecar `api-read` op and the direct client in A2.

`sample-tree` had none of these properties: one production consumer, no write path downstream,
and no broken SQL hook to rebuild. That asymmetry is why one landed and one did not.

**What the user is being asked to decide here:** whether to accept narrower search results in
exchange for scoping, and if so, to sequence it after (a) the clause-assembly rewrite in
`seek/search.py` / `dbtable_sample.py`, and (b) a decision on withheld-row signalling.

### Note B: `admin/samples/retrieve` is not admin-gated

The `admin/` in the route is historical. Commit `2690598` ("feat(nextseek_api): un-gate sample
retrieval, resolve projects, add include_tree") deliberately dropped `IsAdminUser` and left
`IsAuthenticated` (`nextseek_api/views.py:537`), because `IsAdminUser` checks `is_staff` and
therefore already admitted everyone. The same commit fixed the project resolution that had
always silently produced `user_project_ids = []`, and the docstring at `views.py:533-535`
records the intent: this is the single download API behind every sample-download control in the
UI. Do not describe it as admin-gated.

It is nonetheless the **only** read endpoint in the whole register that implements real project
scoping in NExtSEEK's own query layer (`seek/dbtable_sample.py:907-913` and
`nextseek_api/views.py:717-729`), and that scoping is what the headline open question is about.

### Note C: two unscoped Neo4j traversals in `sample_types.py`

`grep -in project nextseek_api/services/sample_types.py` returns **zero** hits (verified,
exit 1). Both actions call `resolve_seek_auth(request, ["BASIC", "SESSION"])` at
`services/sample_types.py:252` and `:355`, but the returned credentials are then discarded.
That call is a second **authentication** check, not authorization, and it is easy to misread as
scoping.

- `child_types` runs `MATCH (s: Sample {id: toInteger($id)}) MATCH (s)<-[:DERIVED_FROM*1..]-(child) RETURN DISTINCT child.type`
  (`services/sample_types.py:266-272`) against a caller-supplied sample id resolved at `:258`.
- `parents_by_child_types` runs an instance-wide `MATCH (p:Sample)` and returns `p.id, p.uuid, p.type`
  for every match (`services/sample_types.py:412-422`, executed at `:430-434`).

Neither goes through SEEK, so there is no upstream authorization to inherit either. Any
authenticated user can enumerate sample ids, uuids and types across all projects. Proposed
bucket `project-scoped` for both, but note that `child_types` returns only type names (low
sensitivity) while `parents_by_child_types` returns concrete sample identifiers.

### Note D: `entity_tree` has no project awareness at all

`grep -n -i project nextseek_api/services/entity_tree.py` returns **zero** hits (verified,
exit 1). This confirms the claim in the task brief, and is in fact stronger than stated: the
case-insensitive grep also finds nothing. All four actions gate on authentication only
(`services/entity_tree.py:132-134, :296-298, :380-382, :846-850`) and then query without any
membership filter.

`lineage` (`services/entity_tree.py:844`) is the one that returns per-sample data: it resolves
each caller-supplied identifier via `_resolve_uid_to_seek_id` and walks Neo4j from there, so an
authenticated user can retrieve the full derivation tree of any sample in the instance. Proposed
`project-scoped`.

`nodes`, `edges` and `edge_attributes` return schema-shaped information (sample type names,
clades, which types derive from which, assay titles), not per-sample data. They also have **zero
consumers anywhere in the worktree**: no frontend, no template, no static JS, no `chat_nextseek`
or `dmac_assistant` caller, and they are absent from the LLM endpoint catalogs, so even the
api_agent cannot normally select them. Proposed `public-to-authenticated`, which is a proposal
to accept the status quo for these three specifically because the payload is schema, not data.

### Note E: SEEK proxy endpoints inherit SEEK's authorization

Sixteen of the read endpoints in the table are thin proxies to the upstream SEEK Rails API. For
all of them the mechanism is the same and it is sound:

1. `resolve_seek_auth` (`nextseek_api/helpers.py:89`) resolves the **caller's own** credentials,
   trying the Basic header (`helpers.py:44`), then the SEEK password stored in the Django
   session (`helpers.py:58-62`), then a forwarded token (`helpers.py:70-86`).
2. `SeekAPIClient._request` (`helpers.py:132`) synthesizes a 401 when no credentials resolve
   (`helpers.py:136-138`) and otherwise builds a per-request `Authorization` header
   (`helpers.py:148`) before issuing the upstream call (`helpers.py:149-156`).
3. SEEK applies its own project and policy authorization server-side.

**There is no shared or service SEEK account on any read path.** Verified by grep for
`SEEK_USERNAME`/`SEEK_PASSWORD`-style settings (none exist) and by the hard 401s at
`helpers.py:136-138` and `helpers.py:334-336`. The one full-privilege escape hatch,
`run_seek_rails_runner` (`nextseek_api/services/seek_rails_runner.py:60`), is reachable only
from `users.py` write actions behind `IsDjangoSuperuser`.

The `sops/download/` and `data_files/download/` actions genuinely stream file blobs
(`StreamingHttpResponse` at `services/content_blobs.py:268-273`), and those streams are
authorized entirely by the caller's own SEEK credentials
(`services/content_blobs.py:220-221` -> `helpers.py:334-346`). Upstream 401/403/404 are
propagated (`services/content_blobs.py:228-247`).

Two wrinkles worth recording, neither a leak:

- **Effective principal is the SEEK identity, not `request.user`.** `IsAuthenticated` validates
  the Django user, while scoping is done by SEEK against whatever identity `resolve_seek_auth`
  produced. A request can pass `IsAuthenticated` as Django user A while SEEK scopes it as user B
  if a Basic header for B is supplied. That requires B's password, so it is not escalation, but
  the register's "who is this" answer for these rows is the SEEK identity.
- **UID resolution is project-blind.** The `_resolve_uid_to_seek_id`-style helpers
  (`services/samples.py:54-70`, `services/content_blobs.py:41-67`) map a title or UID to a
  numeric id with no membership filter, and the DataFile variant does a `title__startswith`
  prefix match picking the highest id on ties. SEEK then 403/404s on the actual fetch, so no
  content escapes, but the resolution step is an existence oracle and can silently pick a
  cross-project asset.

### Note F: `users/` is correctly gated, and shows the right pattern

`UsersViewSet` reads the full SEEK `users` table with no predicate
(`services/users.py:385`, `:427`) and joins `group_memberships`/`work_groups` to emit each
person's project and institution memberships (`services/users.py:230-242`). `project_id` there
is **output, not a filter**. That is fine because the gate is
`IsAuthenticated, IsDjangoSuperuser` (`services/users.py:361`), and `IsDjangoSuperuser`
(`services/users.py:215-220`) checks `is_superuser`, deliberately not `is_staff`.

This is the pattern the headline open question is about: two places in the codebase already
reject `IsAdminUser` as meaningless here (`services/users.py:215-220` and
`nextseek_api/permissions.py:6-30`), while `nextseek_api/views.py:642` still treats `is_staff`
as admin. `IsDjangoSuperuser` is a near-duplicate of `nextseek_api/permissions.py:IsSuperUser`
minus `has_object_permission`; consolidating them is cosmetic and out of scope here.

### Note G: `schema_rag` handles no NExtSEEK data

Both actions gate through `_check_auth` (`services/schema_rag.py:52-66`), which accepts BASIC,
SESSION or TOKEN. Neither touches NExtSEEK samples, projects or files: `ingest`
(`services/schema_rag.py:103`) fetches a caller-supplied OpenAPI URL and stores parsed endpoint
descriptions in a per-session DuckDB file, and `retrieve` (`services/schema_rag.py:304`) runs
semantic search over one of those files -- but `retrieve` also AUTO-INGESTS at
`nextseek_api/schema_rag/service.py:747` when no live session exists, and
`RetrieveRequest` accepts `schema_url` with no `session_id`, so that is its
first-call path rather than an edge case. It therefore carries every side effect
listed for `ingest`, and is classified WRITE for the CC agent (#86). There is nothing to apply a project predicate to,
hence `public-to-authenticated`.

Two things the user may still want to note, both out of scope for a project-scoping register:
sessions carry no owner (`nextseek_api/schema_rag/session.py:88-116` stores no user, and
`retrieve` resolves by `session_id` or `schema_url` with no ownership check), and `ingest`
fetches an arbitrary caller-supplied URL from the Django container.

### Note H: `UserInParticipatingProject` is a feature flag, not a data scope

`services/assistant.py:411` adds `UserInParticipatingProject` to the assistant's
`permission_classes`. It is easy to mistake for project scoping. It is not.

`UserInParticipatingProject.has_permission` (`services/assistant.py:108`) calls SEEK's
`/people/current`, extracts the caller's project ids, and tests
`project_ids & ASSISTANT_PARTICIPATING_PROJECTS != set()` (`services/assistant.py:123-127`).
`ASSISTANT_PARTICIPATING_PROJECTS` is a **static allowlist read from settings at import**
(`services/assistant.py:39`; `set(["1"])` in `dmac/local_settings.example.py:3` and
`startup/templates/local_settings.py.template:7`). Positive results are cached for 60s per user
(`services/assistant.py:116-120`).

So it answers "is this caller a member of at least one project in the hard-coded pilot list",
i.e. **may this account use the assistant at all**. It never touches a queryset, never returns
the caller's project ids to downstream code, and is not consulted when building any response.
All actual data scoping on this viewset is per-user row ownership on `ChatSession` and
`QueryTask`.

Minor defect worth recording but not fixing here: `has_object_permission`
(`services/assistant.py:136-137`) has the wrong signature (no `obj`) and calls a non-existent
`self.has_permissions`. It would raise `AttributeError` if invoked. These ViewSet actions never
invoke object permissions, so it is latent.

`CCAssistantViewSet` does **not** carry `UserInParticipatingProject`
(`services/cc_assistant.py:355` is `[IsAuthenticated]` alone), so the container-CC route is not
behind the pilot allowlist while the NExtSEEK route is. Whether that asymmetry is intended is a
secondary question for the user.

### Note I: `evaluator` reads every user's history under an `is_staff` gate

`EvaluatorViewSet` declares `IsAuthenticated, IsAdminUser` (`services/evaluator.py:420`). Per
`nextseek_api/permissions.py:8-16`, `IsAdminUser` checks `is_staff` and is therefore equivalent
to `IsAuthenticated` in this project. All three read actions are deliberately cross-user, with
no owner or project predicate:

- `retry_context_by_task`: `QueryTask.objects...get(task_id=task_id)` (`services/evaluator.py:438-440`)
- `retry_context_by_bundle`: `ChatSession.objects.get(session_id=session_id)` (`services/evaluator.py:470`)
- `runs_list`: `QueryTask.objects.select_related("session").order_by("-created_at")`
  (`services/evaluator.py:517`) over all users; the `user_id` query param at `:530-533` is an
  optional caller-supplied filter

Net: any account can read any other user's assistant query history, prompts and result bundles.
Cross-user visibility is the point of an evaluator, so the proposed bucket is `admin-only`; the
decision for the user is whether `admin-only` here should mean `IsSuperUser`
(`nextseek_api/permissions.py:6`) rather than the `IsAdminUser` that currently admits everyone.
This is the same `is_staff` question as the headline, applied to a second endpoint family.

### Note J: `admin/project-export` is superuser-gated but not membership-checked

`GET /nextseek_api/admin/project-export/{pk}/` (`services/project_export.py:316`) exports every
sample in the named project via `export_project(project_id, output_format)`
(`services/project_export.py:197`), as JSON or one xlsx sheet per sample type. `project_id`
comes straight from the URL; the caller's own SEEK membership is never consulted. The
`IsSuperUser` gate (`services/project_export.py:267`, real `is_superuser` check) is the entire
control, and that is a defensible design for a deliberate cross-project export tool. Recorded
here so that "superuser can export any project" is an explicit, ruled-on property rather than an
accident.

---

## Verification notes

Facts in this document were established as follows. Anything not verifiable is marked in place.

- **Routed surface**: read from `nextseek_api/urls.py`, then cross-checked against Django's live
  URL resolver inside the running `nextseek` container (read-only introspection, no writes).
- **`entity_tree` has no project predicate**: `grep -n -i project nextseek_api/services/entity_tree.py`
  returns zero hits (exit 1). Case-insensitive, so stronger than the case-sensitive claim.
- **`sample_types` has no project predicate**: `grep -in project nextseek_api/services/sample_types.py`
  returns zero hits (exit 1).
- **`searchAdvanced` callers**: `grep -rn "searchAdvanced" --include=*.py .` finds exactly two
  application call sites, `nextseek_api/views.py:505` (NOT ROUTED) and
  `nextseek_api/services/samples.py:509` (no `project_id`).
- **Account counts**: queried live against the local stack's Django `auth_user` table
  (20 total, 11 staff, 8 superuser, 3 staff-not-superuser).
- **drf-spectacular serve permissions**: `SPECTACULAR_SETTINGS` (`dmac/settings.py:376-383`)
  sets no `SERVE_PERMISSIONS`, and the installed package defaults to
  `['rest_framework.permissions.AllowAny']` (confirmed by reading
  `drf_spectacular/settings.py:59` in the container). So `/schema/`, `/swagger/` and `/redoc/`
  are reachable **unauthenticated**. They expose the API surface description, not data.
- **Consumer inventory** for `admin/samples/retrieve` and `entity_tree`: whole-worktree grep
  across `chat_nextseek/`, `dmac_assistant/`, `docker/ns-sidecar/`, `docker/cc-runtime/`,
  `chat_frontend/`, `nessie_tests/`, `seek/templates/`, `templates/` and `static/js/`.
