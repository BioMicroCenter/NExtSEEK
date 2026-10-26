# Endpoint authorization register (`/nextseek_api/`)

Tracking: #64. Rewritten 2026-10-13 to match the code and the buckets ruled under #64. It replaces the 2026-08-11
register, whose bucket names (public-to-authenticated, project-scoped, admin-only) map onto the buckets below as
3, 1 and 2, with owner-scoped rows in bucket 2.

## What this is

One row for every route and method served under `/nextseek_api/`: who may call it, what limits apply to what it
returns or changes, and its bucket. `nextseek_api/urls.py` decides what is routed; `ci/routes.py` is the CI copy of
the same list. The handler and permission columns were read from Django's URL resolver, not from the source by eye.
When you add or change a route, add or change its row here in the same change.

## The buckets

| Bucket | Meaning | Routes |
|---|---|---|
| 1. Project-scoped | Reads project-owned rows and returns only those in the caller's projects. Superusers are not limited. | 13 |
| 2. Restricted another way | Superuser only, or only the owner of the chat, task, job or upload, or works on the caller's own folder or SEEK person. | 67 |
| 3. Correct as it is | Needs no project filter of its own: SEEK decides with the caller's own SEEK login, or it returns definitions only, or it reads no project rows. | 59 |
| **Total** | | **139** |

Of these, 116 routes are served from `nextseek_api/services/` (11, 54 and 51 by bucket)
and 23 from elsewhere in `nextseek_api/` (the sample tree, the older retrieve path, attributes,
assay registrations, batch upload, the API root and the three API description pages).

## Rules every route shares

- **Sign-in.** The default authenticators are the Container-CC turn pass, a DRF token, the Django session and HTTP
  Basic (`REST_FRAMEWORK` in `dmac/settings.py`); several ViewSets narrow that list. A turn pass is accepted only on
  the routes in its allow table (`nextseek_api/assistant/turn_pass_allow.py`), checked before any view runs, and a
  caller on a pass acts as the turn's user with no superuser read-any (`may_read_any` in
  `nextseek_api/permissions.py`).
- **Admin means `is_superuser`.** The SEEK login sets `is_staff` on every account, so no route treats `is_staff` as
  admin (`IsSuperUser` in `nextseek_api/permissions.py`, `IsDjangoSuperuser` in `nextseek_api/services/users.py`).
- **Project scope.** The caller's projects are read per request from SEEK's MySQL tables
  (`nextseek_api/graph_search/scope.py`: `resolve_scope`, `plain_scope`, `caller_block`, `current_project_ids`). A
  membership counts until the person leaves the project: SEEK's `has_left` flag, or a `time_left_at` that has passed.
  The scope fails closed: a caller who maps to no SEEK person, or whose memberships cannot be read, sees nothing.
  Readers that take the caller's projects from SEEK's own person record go through `seek.seekdb.SeekDB.getCurrentUser`,
  which keeps only the projects the person still belongs to.
- **SEEK proxies run as the caller.** Every proxy call carries the caller's own SEEK credential
  (`SeekAPIClient` and `resolve_seek_auth` in `nextseek_api/helpers.py`); there is no shared SEEK account on any proxy
  path, and SEEK applies its own project and sharing policies. A sample, SOP or data file the caller may not see answers
  404 with the same body as one that does not exist, on reads, writes and the two download routes. A caller with no
  SEEK login of its own gets 401 on those routes before anything is looked up (`HasOwnSeekLogin` in
  `nextseek_api/services/content_blobs.py`).
- **Assistant ops run with the caller's own login.** The granular ops under `assistant/` (`entity` to
  `build-upload-xlsx`) need a login of the caller's own (a Basic pair, or the SEEK login held in the Django session);
  without one they answer 401 `AUTH_FAILED` and never run as the configured service login.
- **The pilot gate is a feature gate.** `UserInParticipatingProject` (`nextseek_api/services/assistant.py`) lets an
  account use the `assistant/` routes when it is a member of one of the projects listed in
  `ASSISTANT_PARTICIPATING_PROJECTS` that it has not left (SEEK's leaving rule, as in the project scope above). A
  member who has left every pilot project still reads their own past chats (the owner-only GETs under
  `assistant/sessions/` and `assistant/tasks/`) and is refused everything else; an account that was never in a pilot
  project is refused every route. A superuser's left pilot projects still count. It limits no data; the data limits
  are the project scope above. The `cc-assistant/` and `nessie/` chat routes do not carry it, by decision under #64.

## 1. Project-scoped (13)

| Method | Path | Guard | Handler | Permission classes | #64 |
|---|---|---|---|---|---|
| POST | `samples/advanced_search/` | Limited to the projects the caller belongs to in SEEK, read with the caller's own SEEK login; a caller with only a token, or a failed project read, finds nothing. Superusers are not limited. Validation details in an error answer are for superusers only. | `SampleAdvancedSearchViewSet.create` | IsAuthenticated | changed |
| POST | `samples/graph_search/` | Limited to the caller's projects: every match, the count and every sample on a lineage path must be in them, and a caller with no projects reads nothing. Superusers are not limited. | `GraphSearchViewSet.create` | IsAuthenticated | changed |
| POST | `samples/retrieve/` | Limited to the caller's projects: a sample outside them answers 404, the same as an unknown one, and related samples (parents and children) are filtered the same way. Superusers are not limited. | `SampleRetrieveViewSet.create` | IsAuthenticated | changed |
| GET | `entity_tree/edge_attributes/` | Any signed-in user sees the sample type structure over the whole graph, with no counts; the study titles it adds are limited to the caller's projects. Superusers are not limited. | `EntityTreeViewSet.list_edge_attributes` | IsAuthenticated | changed |
| POST | `entity_tree/lineage/` | Limited to the caller's projects: the sample and every sample on the path must be in them, otherwise the answer is not found. Superusers are not limited. | `EntityTreeViewSet.lineage` | IsAuthenticated | changed |
| GET | `sampletypes/{uid}/child_types/` | Limited to the caller's projects: it follows only samples in them, and anything outside them answers not found. Superusers are not limited. | `SampleTypeChildrenViewSet.child_types` | IsAuthenticated | changed |
| POST | `sample_types/get_parents/parents_by_child_types/` | Limited to the caller's projects: the child samples, their parents and every sample on the path must be in them. Superusers are not limited. | `SamplesByChildTypesViewSet.parents_by_child_types` | IsAuthenticated | changed |
| POST | `assistant/graph/` | Members of the pilot projects only. It answers a question from the graph; the graph reader sees only samples in the caller's projects and refuses without a project scope. Superusers are not limited. | `AssistantViewSet.graph` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/aggregate/` | Members of the pilot projects only. It counts and groups graph results; the graph reader sees only samples in the caller's projects. Superusers are not limited. | `AssistantViewSet.aggregate` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/graph-schema/` | Members of the pilot projects only. It returns the graph's shape and value lists; counts and bounds are hidden from non-superusers and the value lists use the caller's project scope. | `AssistantViewSet.graph_schema` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/report/` | Members of the pilot projects only. It builds a report; a project outside the caller's projects is refused, and the investigation path goes through the scoped graph reader. | `AssistantViewSet.report` | IsAuthenticated, UserInParticipatingProject | changed |
| GET | `sample-tree/{uid}/tree/` | Limited to the caller's projects, read with the caller's own SEEK login: the root sample must be in them and the tree is cut at their edge. Superusers are not limited. | `SampleTreeViewSet.get_tree` | IsAuthenticated | changed |
| POST | `admin/samples/retrieve/` | The older path of samples/retrieve/: the same handler and the same limits. | `AdminSampleViewSet.admin_retrieve_samples` | IsAuthenticated | changed |

## 2. Restricted another way (67)

| Method | Path | Guard | Handler | Permission classes | #64 |
|---|---|---|---|---|---|
| POST | `sample-shares/` | Superuser only. It creates a job that shares samples between any two projects. | `SampleShareViewSet.create` | IsAuthenticated, IsDjangoSuperuser |  |
| GET | `sample-shares/{share_id}/` | Superuser only. It reads the state of any share job. | `SampleShareViewSet.retrieve` | IsAuthenticated, IsDjangoSuperuser |  |
| POST | `sample-shares/{share_id}/apply/` | Superuser only. It applies one step of a share job and adds the project links; the caller's own SEEK login is checked first. | `SampleShareViewSet.apply` | IsAuthenticated, IsDjangoSuperuser |  |
| GET | `sample_types/connections/` | Superuser only. It lists which sample types connect to which, with counts across all projects, by design. | `SampleTypeConnectionsViewSet.list` | IsAuthenticated, IsSuperUser |  |
| POST | `templates/generate/` | Superuser only. It generates a blank upload template. | `TemplatesViewSet.generate` | IsAuthenticated, IsSuperUser |  |
| GET | `users/` | Superuser only. It lists every user account. | `UsersViewSet.list` | IsAuthenticated, IsDjangoSuperuser |  |
| POST | `users/` | Superuser only. It creates a user account. | `UsersViewSet.create` | IsAuthenticated, IsDjangoSuperuser |  |
| GET | `users/{uid}/` | Superuser only. It reads one user account. | `UsersViewSet.retrieve` | IsAuthenticated, IsDjangoSuperuser |  |
| PATCH | `users/{uid}/` | Superuser only. It changes one user account. | `UsersViewSet.partial_update` | IsAuthenticated, IsDjangoSuperuser |  |
| GET | `reingest-proposals/` | Superuser only. It lists the reingest attribute proposal queue, a global operator worklist, not project data. | `ReingestProposalViewSet.list` | IsAuthenticated, IsSuperUser |  |
| GET | `reingest-proposals/{pk}/` | Superuser only. It reads one reingest attribute proposal. | `ReingestProposalViewSet.retrieve` | IsAuthenticated, IsSuperUser |  |
| POST | `reingest-proposals/{pk}/approve/` | Superuser only. It approves one proposal, which the reingest mapper then reads as a rule; it refuses (409) while the proposed attribute is not defined on its sample type. | `ReingestProposalViewSet.approve` | IsAuthenticated, IsSuperUser |  |
| POST | `reingest-proposals/{pk}/reject/` | Superuser only. It rejects one proposal for good. | `ReingestProposalViewSet.reject` | IsAuthenticated, IsSuperUser |  |
| POST | `schema_rag/ingest/` | Superuser only. It loads an API description from a web address into a temporary search index. | `SchemaRAGViewSet.ingest` | IsAuthenticated, IsSuperUser | changed |
| GET | `admin/project-export/{pk}/` | Superuser only. It exports every sample of any project you name, as a file, by design. | `ProjectExportViewSet.retrieve` | IsAuthenticated, IsSuperUser |  |
| POST | `admin/project-export/run/` | Superuser only. It starts a bulk export of every sample in a project. | `ProjectExportViewSet.run` | IsAuthenticated, IsSuperUser |  |
| GET | `admin/graph-sync/status/` | Superuser only. It shows the graph sync's own state; no sample data. | `GraphSyncStatusViewSet.status` | IsAuthenticated, IsDjangoSuperuser |  |
| GET | `assistant/sessions/` | Members of a pilot project they have not left, or one who has left, reading their own past chats. It lists only the caller's own chats. | `AssistantViewSet.list_sessions` | IsAuthenticated, UserInParticipatingProject |  |
| POST | `assistant/sessions/` | Members of the pilot projects only. It creates a chat for the caller. | `AssistantViewSet.create_session` | IsAuthenticated, UserInParticipatingProject |  |
| GET | `assistant/sessions/{sid}/` | Members of a pilot project they have not left, or one who has left, reading their own past chats. Only the chat's owner or a superuser may read it. | `AssistantViewSet.get_session` | IsAuthenticated, UserInParticipatingProject |  |
| PATCH | `assistant/sessions/{sid}/` | Members of the pilot projects only. Only the chat's owner may change it. | `AssistantViewSet.patch_session` | IsAuthenticated, UserInParticipatingProject |  |
| DELETE | `assistant/sessions/{sid}/` | Members of the pilot projects only. Only the chat's owner may delete it. | `AssistantViewSet.delete_session` | IsAuthenticated, UserInParticipatingProject |  |
| POST | `assistant/query/` | Members of the pilot projects only. It runs a question in the caller's own chat, with the caller's login and project scope; the production model setup and `mode` `plan` are for superusers only (`plan` also for a turn pass; 403 otherwise). | `AssistantViewSet.query` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/query/async/` | Members of the pilot projects only. It runs a question in the caller's own chat in the background, with the caller's login and project scope; the production model setup and `mode` `plan` are for superusers only (`plan` also for a turn pass; 403 otherwise). | `AssistantViewSet.query_async` | IsAuthenticated, UserInParticipatingProject | changed |
| GET | `assistant/tasks/{id}/progress/` | Members of a pilot project they have not left, or one who has left, reading their own past chats. Only the task's owner or a superuser may read its progress. | `AssistantViewSet.task_progress` | IsAuthenticated, UserInParticipatingProject |  |
| GET | `assistant/sessions/{sid}/bundles/{bid}/` | Members of a pilot project they have not left, or one who has left, reading their own past chats. Only the chat's owner or a superuser may read the result bundle. | `AssistantViewSet.download_bundle` | IsAuthenticated, UserInParticipatingProject |  |
| GET | `assistant/sessions/{sid}/bundles/{bid}/artifacts/{key}/` | Members of a pilot project they have not left, or one who has left, reading their own past chats. Only the chat's owner or a superuser may download the file, and it must sit inside the bundle's own folder. | `AssistantViewSet.download_artifact` | IsAuthenticated, UserInParticipatingProject |  |
| GET | `assistant/sessions/{sid}/download/` | Members of a pilot project they have not left, or one who has left, reading their own past chats. Only the chat's owner or a superuser may download it. | `AssistantViewSet.download_session` | IsAuthenticated, UserInParticipatingProject |  |
| GET | `assistant/test-cases/` | Superuser only, and the caller must also be in a pilot project. It returns the fixed list of test cases from the site settings. | `AssistantViewSet.test_cases` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/run-ls/` | Members of the pilot projects only. It lists the files of one Luria run folder over the cluster connection, for the run's owner or a superuser only. | `AssistantViewSet.run_ls` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/run-harvest/` | Members of the pilot projects only. It copies the allowlisted outputs of one finished Luria run folder off the cluster and parses them into a manifest; without a folder named, it picks the caller's own waiting launched run. It writes nothing to Luria or NExtSEEK. | `AssistantViewSet.run_harvest` | IsAuthenticated, UserInParticipatingProject |  |
| POST | `assistant/run-checksum/` | Members of the pilot projects only. It checksums files the caller names inside one Luria run folder over the cluster connection, and can fold the sums into a manifest the caller made. It writes nothing to Luria or NExtSEEK. | `AssistantViewSet.run_checksum` | IsAuthenticated, UserInParticipatingProject |  |
| POST | `assistant/upload-reingest/` | Members of the pilot projects only, with `confirmed_write` true. It uploads only build records the caller made, shown in an earlier turn of the caller's own running Container-CC chat, and starts each batch-upload job with the caller's project scope, so a project outside the caller's projects is refused before any job starts. | `AssistantViewSet.upload_reingest` | IsAuthenticated, UserInParticipatingProject |  |
| POST | `cc-assistant/query/async/` | Any signed-in user. The chat must be the caller's, and the caller's login and project scope are handed down; only a superuser can use the production model setup or `mode` `plan` (403 otherwise). | `CCAssistantViewSet.query_async` | IsAuthenticated | changed |
| POST | `cc-assistant/cc/query/async/` | A superuser (403 otherwise), the same rule as `force_route`. The same as the query route above, forced to a Container-CC run. | `CCAssistantViewSet.cc_query_async` | IsAuthenticated + superuser check in the method | changed |
| GET | `cc-assistant/tasks/{id}/progress/` | Any signed-in user, but only the task's owner or a superuser may read its progress. | `CCAssistantViewSet.task_progress` | IsAuthenticated |  |
| POST | `cc-assistant/upload/` | Any signed-in user. It writes only into the caller's own upload folder; the project comes from the caller's SEEK login. Only projects the caller still belongs to count. | `CCAssistantViewSet.upload` | IsAuthenticated | changed |
| GET | `cc-assistant/upload/status/{job}/` | Any signed-in user, but only the upload's owner or a superuser may read its status. | `CCAssistantViewSet.upload_status` | IsAuthenticated |  |
| GET | `cc-assistant/upload/list/` | Any signed-in user. It lists only the caller's own upload folder. The folder follows the caller's current SEEK project. | `CCAssistantViewSet.upload_list` | IsAuthenticated | changed |
| GET | `cc-assistant/artifacts/{session}/download/` | Any signed-in user, but only the chat's owner or a superuser may download its files. The files come from the chat owner's folder; a chat with no Container-CC turn has none. | `CCAssistantViewSet.download_artifact` | IsAuthenticated | changed |
| GET | `cc-assistant/transcript/{session}/{turn}/` | Any signed-in user, but only the chat's owner or a superuser may read the transcript. | `CCAssistantViewSet.recover_transcript` | IsAuthenticated |  |
| GET | `evaluator/tasks/{id}/retry-context/` | Superuser only. It reads the retry details of any user's task. | `EvaluatorViewSet.retry_context_by_task` | IsAuthenticated, IsSuperUser |  |
| GET | `evaluator/sessions/{sid}/bundles/{bid}/retry-context/` | Superuser only. It reads the retry details of one result bundle. | `EvaluatorViewSet.retry_context_by_bundle` | IsAuthenticated, IsSuperUser |  |
| GET | `evaluator/runs/` | Superuser only. It lists every user's evaluator tasks. | `EvaluatorViewSet.runs_list` | IsAuthenticated, IsSuperUser |  |
| POST | `evaluator/retry/` | Superuser only. It runs a retry with the superuser's own login. | `EvaluatorViewSet.retry_execute` | IsAuthenticated, IsSuperUser |  |
| GET | `nessie/sessions/{id}/debug/` | Superuser only. It shows the debug record of any user's chat. | `NessieViewSet.session_debug` | IsAuthenticated, IsSuperUser |  |
| POST | `nessie/query/` | Any signed-in user. The same rules as cc-assistant/query/async/. | `NessieChatViewSet.query` | IsAuthenticated | changed |
| POST | `nessie/query/cc/` | A superuser (403 otherwise). The same rules as cc-assistant/cc/query/async/. | `NessieChatViewSet.query_cc` | IsAuthenticated + superuser check in the delegated method | changed |
| GET | `nessie/tasks/{id}/progress/` | Any signed-in user, but only the task's owner or a superuser may read its progress. | `NessieChatViewSet.task_progress` | IsAuthenticated |  |
| GET | `nessie/uploads/` | Any signed-in user. It lists only the caller's own upload folder. The folder follows the caller's current SEEK project. | `NessieChatViewSet.uploads_list` | IsAuthenticated | changed |
| POST | `nessie/uploads/` | Any signed-in user. It writes only into the caller's own upload folder; the project comes from the caller's SEEK login. Only projects the caller still belongs to count. | `NessieChatViewSet.uploads_create` | IsAuthenticated | changed |
| GET | `nessie/uploads/{job}/` | Any signed-in user, but only the upload's owner or a superuser may read its status. | `NessieChatViewSet.uploads_status` | IsAuthenticated |  |
| GET | `nessie/sessions/{s}/artifacts/` | Any signed-in user, but only the chat's owner or a superuser may download its files. The files come from the chat owner's folder; a chat with no Container-CC turn has none. | `NessieChatViewSet.session_artifacts` | IsAuthenticated | changed |
| GET | `nessie/sessions/{s}/transcript/{turn}/` | Any signed-in user, but only the chat's owner or a superuser may read the transcript. | `NessieChatViewSet.session_transcript` | IsAuthenticated |  |
| POST | `attributes/batch-create/` | Superuser only, with a proved SEEK login. | `AttributeViewSet.batch_create` | SeekAuthenticated, IsSuperUser |  |
| PATCH | `attributes/batch-patch/` | Superuser only, with a proved SEEK login. | `AttributeViewSet.batch_patch` | SeekAuthenticated, IsSuperUser |  |
| POST | `attributes/batch-delete/` | Superuser only, with a proved SEEK login. | `AttributeViewSet.batch_delete` | SeekAuthenticated, IsSuperUser |  |
| GET | `attributes/jobs/{job_id}/` | Superuser only. The state of one attribute change job. | `AttributeViewSet.job` | SeekAuthenticated, IsSuperUser |  |
| POST | `attributes/jobs/{job_id}/cancel/` | Superuser only, and only the superuser who started the job. | `AttributeViewSet.job_cancel` | SeekAuthenticated, IsSuperUser, CanCancelAttributeJob |  |
| POST | `assay-registrations/` | Superuser only. Records which samples belong to which assays. | `AssayRegistrationViewSet.create` | IsAuthenticated, IsSuperUser |  |
| GET | `assay-registrations/jobs/{job_id}/` | Superuser only. The state of one registration job. | `AssayRegistrationViewSet.job` | IsAuthenticated, IsSuperUser |  |
| POST | `assay-registrations/jobs/{job_id}/cancel/` | Superuser only. | `AssayRegistrationViewSet.job_cancel` | IsAuthenticated, IsSuperUser |  |
| POST | `batch-upload/start/` | A signed-in member of the target project (403 otherwise; a superuser may use any project). The job belongs to the caller and files the rows under the caller's own SEEK person; another person or lab, and the access grant in `config_overrides`, are a superuser's choice only. An upsert updates only samples in the caller's own projects. | `BatchUploadViewSet.start` | IsAuthenticated |  |
| GET | `batch-upload/` | Any signed-in user. Lists only the caller's own jobs. | `BatchUploadViewSet.list` | IsAuthenticated |  |
| GET | `batch-upload/status/{job_id}/` | Any signed-in user, but only the job's owner; another user's job answers 404. | `BatchUploadViewSet.job_status` | IsAuthenticated |  |
| GET | `batch-upload/summary/{job_id}/` | Any signed-in user, but only the job's owner; another user's job answers 404. | `BatchUploadViewSet.summary` | IsAuthenticated |  |
| DELETE | `batch-upload/cancel/{job_id}/` | Any signed-in user, but only the job's owner; another user's job answers 404. | `BatchUploadViewSet.cancel` | IsAuthenticated |  |

## 3. Correct as it is (59)

| Method | Path | Guard | Handler | Permission classes | #64 |
|---|---|---|---|---|---|
| GET | `projects/` | Any signed-in user. It reads the list of projects from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `ProjectProxyViewSet.list` | IsAuthenticated |  |
| POST | `projects/` | Any signed-in user. It creates a project in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `ProjectProxyViewSet.create` | IsAuthenticated |  |
| GET | `projects/{uid}/` | Any signed-in user. It reads one project, found by its title from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `ProjectProxyViewSet.retrieve` | IsAuthenticated |  |
| PATCH | `projects/{uid}/` | Any signed-in user. It changes one project in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `ProjectProxyViewSet.partial_update` | IsAuthenticated |  |
| GET | `people/` | Any signed-in user. It reads the list of people from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `PeopleProxyViewSet.list` | IsAuthenticated |  |
| POST | `people/` | Any signed-in user. It creates a person in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `PeopleProxyViewSet.create` | IsAuthenticated |  |
| GET | `people/{uid}/` | Any signed-in user. It reads one person, found by SEEK number from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `PeopleProxyViewSet.retrieve` | IsAuthenticated |  |
| PATCH | `people/{uid}/` | Any signed-in user. It changes one person in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `PeopleProxyViewSet.partial_update` | IsAuthenticated |  |
| GET | `people/current/` | Any signed-in user. It returns the caller's own SEEK person record, read with the caller's own SEEK login. | `PeopleProxyViewSet.current` | IsAuthenticated |  |
| GET | `investigations/` | Any signed-in user. It reads the list of investigations from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `InvestigationProxyViewSet.list` | IsAuthenticated |  |
| POST | `investigations/` | Any signed-in user. It creates an investigation in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `InvestigationProxyViewSet.create` | IsAuthenticated |  |
| GET | `investigations/{uid}/` | Any signed-in user. It reads one investigation, found by SEEK number from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `InvestigationProxyViewSet.retrieve` | IsAuthenticated |  |
| PATCH | `investigations/{uid}/` | Any signed-in user. It changes one investigation in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `InvestigationProxyViewSet.partial_update` | IsAuthenticated |  |
| GET | `studies/` | Any signed-in user. It reads the list of studies from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `StudyProxyViewSet.list` | IsAuthenticated |  |
| POST | `studies/` | Any signed-in user. It creates a study in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `StudyProxyViewSet.create` | IsAuthenticated |  |
| GET | `studies/{uid}/` | Any signed-in user. It reads one study from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `StudyProxyViewSet.retrieve` | IsAuthenticated |  |
| PATCH | `studies/{uid}/` | Any signed-in user. It changes one study in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `StudyProxyViewSet.partial_update` | IsAuthenticated |  |
| GET | `assays/` | Any signed-in user. It reads the list of assays from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `AssayProxyViewSet.list` | IsAuthenticated |  |
| POST | `assays/` | Any signed-in user. It creates an assay in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `AssayProxyViewSet.create` | IsAuthenticated |  |
| GET | `assays/{uid}/` | Any signed-in user. It reads one assay from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `AssayProxyViewSet.retrieve` | IsAuthenticated |  |
| PATCH | `assays/{uid}/` | Any signed-in user. It changes one assay in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may; it also looks up the assay's files to keep the graph in step and returns none of that. | `AssayProxyViewSet.partial_update` | IsAuthenticated |  |
| GET | `sops/` | Any signed-in user. It reads the list of SOPs from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. The answer then gives each listed SOP's file names, read from SEEK's `content_blobs` table for only the ids SEEK returned to this caller. | `SopProxyViewSet.list` | IsAuthenticated, HasOwnSeekLogin |  |
| POST | `sops/` | Any signed-in user. It creates a SOP in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. A title that already exists answers 409, with the existing SOP's id only when the caller can see it. | `SopProxyViewSet.create` | IsAuthenticated, HasOwnSeekLogin |  |
| GET | `sops/{uid}/` | Any signed-in user. It finds the SOP by its title or UID, then reads it from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `SopProxyViewSet.retrieve` | IsAuthenticated, HasOwnSeekLogin | changed |
| PATCH | `sops/{uid}/` | Any signed-in user. It finds the SOP by its title or UID, then changes it in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. A path that names no SOP the caller can see answers 404, whatever id the body carries. | `SopProxyViewSet.partial_update` | IsAuthenticated, HasOwnSeekLogin | changed |
| DELETE | `sops/{uid}/` | Any signed-in user. It reads the SOP from SEEK with the caller's own SEEK login (a SOP the caller cannot see answers 404), refuses with 409, giving no count, while any sample names it as its Protocol (a title another SOP also holds does not count), then deletes it in SEEK with the caller's own SEEK login, so SEEK decides who may: on dev, only the SOP's creator. | `SopProxyViewSet.destroy` | IsAuthenticated, HasOwnSeekLogin | new |
| POST | `sops/download/` | Any signed-in user. It finds the SOP, then fetches both its record and its file with the caller's own SEEK login, so SEEK decides whether the caller may download it. | `SopProxyViewSet.download` | IsAuthenticated, HasOwnSeekLogin | changed |
| GET | `data_files/` | Any signed-in user. It reads the list of data files from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `DataFileProxyViewSet.list` | IsAuthenticated, HasOwnSeekLogin |  |
| POST | `data_files/` | Any signed-in user. It creates a data file in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `DataFileProxyViewSet.create` | IsAuthenticated, HasOwnSeekLogin |  |
| GET | `data_files/{uid}/` | Any signed-in user. It finds the data file by its title, UID or title prefix, then reads it from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `DataFileProxyViewSet.retrieve` | IsAuthenticated, HasOwnSeekLogin | changed |
| PATCH | `data_files/{uid}/` | Any signed-in user. It finds the data file by its title, UID or title prefix, then changes it in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. A path that names no data file the caller can see answers 404, whatever id the body carries. | `DataFileProxyViewSet.partial_update` | IsAuthenticated, HasOwnSeekLogin | changed |
| POST | `data_files/download/` | Any signed-in user. It finds the data file, then fetches both its record and its file with the caller's own SEEK login, so SEEK decides whether the caller may download it. | `DataFileProxyViewSet.download` | IsAuthenticated, HasOwnSeekLogin | changed |
| POST | `samples/` | Any signed-in user. It creates a sample in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. Validation details in an error answer are for superusers only. | `SampleProxyViewSet.create` | IsAuthenticated, HasOwnSeekLogin |  |
| GET | `samples/{uid}/` | Any signed-in user. It finds the sample by its UID, then reads it from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. Validation details in an error answer are for superusers only. | `SampleProxyViewSet.retrieve` | IsAuthenticated, HasOwnSeekLogin | changed |
| PATCH | `samples/{uid}/` | Any signed-in user. It finds the sample by its UID, then changes it in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. A sample the caller cannot see answers 404, whatever id the body carries; a refused change to a sample the caller can see keeps SEEK's 403. Validation details in an error answer are for superusers only. | `SampleProxyViewSet.partial_update` | IsAuthenticated, HasOwnSeekLogin | changed |
| DELETE | `samples/{uid}/` | Any signed-in user. It finds the sample by its UID, then asks SEEK to delete it with the caller's own SEEK login, so SEEK decides whether the caller may; the graph copy is removed only once the sample is gone from the database. A refused delete of a sample the caller can see keeps SEEK's answer. | `SampleProxyViewSet.destroy` | IsAuthenticated, HasOwnSeekLogin | changed |
| GET | `entity_tree/nodes/` | Any signed-in user. It shows the sample type definitions only, with no samples. | `EntityTreeViewSet.list_nodes` | IsAuthenticated |  |
| GET | `entity_tree/edges/` | Any signed-in user. It shows which sample types connect, and the internal assay titles, over the whole graph: structure only, no counts. | `EntityTreeViewSet.list_edges` | IsAuthenticated |  |
| GET | `sample_types/` | Any signed-in user. It reads the list of sample types from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `SampleTypeProxyViewSet.list` | IsAuthenticated |  |
| POST | `sample_types/` | Any signed-in user. It creates a sample type in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `SampleTypeProxyViewSet.create` | IsAuthenticated |  |
| GET | `sample_types/{uid}/` | Any signed-in user. It reads one sample type from SEEK with the caller's own SEEK login, so SEEK decides what the caller sees. | `SampleTypeProxyViewSet.retrieve` | IsAuthenticated |  |
| PATCH | `sample_types/{uid}/` | Any signed-in user. It changes one sample type in SEEK with the caller's own SEEK login, so SEEK decides whether the caller may. | `SampleTypeProxyViewSet.partial_update` | IsAuthenticated |  |
| GET | `templates/catalog/` | Any signed-in user. It lists the blank upload templates that can be generated: sample type definitions only. | `TemplatesViewSet.catalog` | IsAuthenticated |  |
| POST | `schema_rag/retrieve/` | Any signed-in user, by a session id or by this instance's own API description; naming any other API description by web address is superuser only, because that loads it. It reads no project rows. | `SchemaRAGViewSet.retrieve_endpoints` | IsAuthenticated | changed |
| GET | `assistant/me/` | Members of the pilot projects only. It returns who the caller is. | `AssistantViewSet.me` | IsAuthenticated, UserInParticipatingProject |  |
| POST | `assistant/entity/` | Members of the pilot projects only. It matches names in a question against the site-wide lists of projects, labs and sample types; it reads no project samples. | `AssistantViewSet.entity` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/parse/` | Members of the pilot projects only. It parses a question inside the caller's own chat, or a throwaway one; it reads no samples. | `AssistantViewSet.parse` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/api-read/` | Members of the pilot projects only. It makes one read call from a fixed list of allowed routes, with the caller's own login, so that route's own rules apply. | `AssistantViewSet.api_read` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/api-write/` | Members of the pilot projects only. It cannot write by itself: it needs a confirmed-write flag, the REST tool refuses every write anyway, and the caller's own login is used. | `AssistantViewSet.api_write` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/generate-submission/` | Members of the pilot projects only. It builds a repository submission file from sample metadata fetched through the retrieve route as the caller, so the caller's project limits apply. | `AssistantViewSet.generate_submission` | IsAuthenticated, UserInParticipatingProject | changed |
| POST | `assistant/build-upload-xlsx/` | Members of the pilot projects only. It builds upload spreadsheets from rows the caller supplies or from a manifest the caller's own run-harvest made, and files a build record under the caller; it writes nothing to SEEK. | `AssistantViewSet.build_upload_xlsx` | IsAuthenticated, UserInParticipatingProject | changed |
| GET | `/` | Any signed-in user. The API root: it lists the routes, no records. | `APIRootView` | IsAuthenticated |  |
| GET | `schema/` | Any signed-in user. The OpenAPI description of the API, no records. | `SpectacularAPIView` | IsAuthenticated |  |
| GET | `swagger/` | Any signed-in user. The Swagger page over the API description, no records. | `SpectacularSwaggerView` | IsAuthenticated |  |
| GET | `redoc/` | Any signed-in user. The ReDoc page over the API description, no records. | `SpectacularRedocView` | IsAuthenticated |  |
| GET | `attributes/` | A signed-in user whose SEEK login is proved. Attribute definitions only, no sample values. | `AttributeViewSet.list` | SeekAuthenticated |  |
| GET | `attributes/{pk}/` | A signed-in user whose SEEK login is proved. One attribute definition. | `AttributeViewSet.retrieve` | SeekAuthenticated |  |
| POST | `attributes/search/` | A signed-in user whose SEEK login is proved. Searches attribute definitions. | `AttributeViewSet.search` | SeekAuthenticated |  |
| POST | `batch-upload/validate/` | A signed-in member of the target project (403 otherwise; a superuser may use any project). Checks an upload sheet and writes nothing. | `BatchUploadViewSet.validate` | IsAuthenticated |  |

## What changed for #64 (2026-10-13)

- Leaving a project ends read scope for it on every project-scoped route, in the assistant and Container-CC turns,
  and on the pages and routes that take the caller's projects from SEEK's person record.
- A sample, SOP or data file the caller may not see answers like one that does not exist, on its read, write and
  download routes, whatever id a write's body carries. A SOP or data file write whose path names nothing answers
  404. A write by SEEK's numeric id answers the same 404 for an item
  that does not exist. `debug_validation` details are for superusers only. A caller with no SEEK login of its own gets
  401 on these routes before anything is looked up.
- `entity_tree/edge_attributes/` and `entity_tree/lineage/` with attributes return only the study titles of studies
  that hold a sample in the caller's projects.
- `schema_rag/ingest/` is superuser only; references inside a loaded API description are followed over http and https
  only; `schema_rag/retrieve/` names another API description by web address for a superuser only.
- `assistant/run-ls/` lists a run folder for the superuser, or for the user one of whose chats launched that run, and
  never the runs folder itself.
- The assistant ops need the caller's own login.
- `assistant/test-cases/` is superuser only.
- The Container-CC file download reads the chat owner's folder, for the owner and for a superuser alike.
- The Container-CC and Nessie upload routes pick the caller's upload folder from the projects they still belong to.

## Notes by family

- **Sample search and download.** `samples/advanced_search/` takes the caller's projects from SEEK's person record and
  filters in SQL; `samples/graph_search/`, `samples/retrieve/` (and its older `admin/samples/retrieve/` path), the
  sample tree, `entity_tree/lineage/` and the two sample type lineage routes take them from `resolve_scope`. A sample
  outside the caller's projects answers as one that does not exist, and rows outside them are left out without a
  separate signal.
- **`entity_tree`.** `nodes/` returns sample type definitions and `edges/` which types derive from which, with internal
  assay names and no counts: structure, not samples, over the whole graph.
- **`schema_rag`.** A session holds a parsed API description and no NExtSEEK records. Retrieval by session id, or by
  this instance's own API description (built in process), is open to any signed-in user.
- **Cross-project by design, superuser only.** `admin/project-export/` exports any project named in the URL;
  `sample-shares/` links samples from one project into another; `sample_types/connections/` counts across projects;
  `users/` reads and writes accounts; `evaluator/` reads every user's tasks; `nessie/sessions/{id}/debug/` reads any
  chat's debug record. The superuser gate is the whole control on each.
- **Reingest.** `reingest-proposals/` is a superuser worklist over every pipeline, like `users/`. Its `approve`
  re-checks that the proposed attribute is defined on its sample type before writing, and answers 503, never 409, when
  the sample type catalog cannot be read: reingest never writes an attribute that does not exist.
  `assistant/upload-reingest/` is the one Container-CC op that writes to NExtSEEK; the turn pass allows it
  (`turn_pass_allow.py`).
- **Container-CC folders.** Uploads go to, and are listed from, the caller's own folder under the caller's current SEEK
  project. Files a Container-CC turn wrote are read from the folder saved with the chat.

## Not covered here

- The progress WebSocket (`nextseek_api/assistant/consumers.py`): not a DRF route.
- Code inside the Container-CC and sidecar images, and SEEK's own policies behind the proxies.
- Routes outside `/nextseek_api/` (the SEEK pages under `seek/`, the site pages under `dmac/`).
- The #64 route review covered the routes served from `nextseek_api/services/`. The rows for the sample tree, the older
  retrieve path, attributes, assay registrations, batch upload and the API description pages record their gates as
  routed; their buckets were not part of that review.
