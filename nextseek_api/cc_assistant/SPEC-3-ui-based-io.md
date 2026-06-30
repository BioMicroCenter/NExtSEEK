# Spec: Step 3 — UI-based I/O (upload, output split, activity panel, Dropbox removal, session-id)

**Date:** 2026-06-30
**Tracker:** `integration-plan.json` step **3** ("UI-based I/O") — substeps 3a–3e.
**Status:** design, awaiting user review → writing-plans
**Builds on:** Step **2** (project-stratified per-user dirs: `<DMAC_USER_ROOT>/<project>/<user>/{input,scratch,cc-state,output}` + `<project>/shared/`). Step 3 fills `input/` (uploads), splits `scratch/` on the way out, and removes the Dropbox copier.
**Deadline:** NExtSEEK prod before **2026-07-14**.

> **Grounding note.** Every reuse target below was located in the running codebase this
> session (file:line cited). Where an exact type/field still needs confirmation at
> plan time, it is flagged **[CONFIRM@PLAN]**.

---

## 1. Problem

Step 2 gave each user an isolated, project-stratified directory tree, but I/O is still
vestigially Dropbox-shaped:

- **No upload path.** `input/` exists and mounts RO at `/data/input`, but nothing puts
  files there — there is **no upload UI** in the frontend (`MessageInput.tsx` has no file
  control) and no upload endpoint for CC.
- **Output goes nowhere useful.** After a turn, `_publish_artifacts` (`cc_engine.py:639`)
  diffs `scratch/`, copies changed files to `output/`, and appends a hardcoded
  **"📁 Saved to your Dropbox"** string to the reply (`cc_engine.py:584`). There is no
  download, no artifact/raw separation, and the message is misleading (there is no Dropbox).
- **No durable record of what the agent did.** The session `.jsonl` transcript lives only
  on the host disk (`cc-state/<session>/.claude/projects/**/*.jsonl`); 1c stores a Gemini
  *summary* + fingerprint in `extra_state["summary"]`, but the **full transcript is never
  persisted to the DB** and there is no UI activity panel for CC turns. (Native
  `chat_nextseek` shows a "Search Details" panel from live `agent_complete` events, but those
  `debugEntries` are **ephemeral — empty on reload**; `MessageBubble.tsx:111-159`.)
- **Dropbox/laptop cruft.** `cc_config.py:15` hardcodes a laptop default
  `_DEFAULT_HOST_USER_ROOT = "/Users/taishajoseph/dmac-dev/users"`; dead `dropbox_root`
  config and `seek/views.py:94 DROPBOX_DIRECTORY` linger.
- **Frontend session-id (3e).** New-chat `activeSessionId` is promoted from the WS
  `query_complete` `d.session_id` (`AppLayout.tsx:117`, `EmbeddedApp.tsx:126`); both sites
  carry a TODO to promote from the **authoritative HTTP-202 body** id instead
  (defense-in-depth after commit `4016a9b`).

Per the user's canonical spec (**HANDOFF-2026-06-26 §F-5/§F-6**), replace Dropbox with a
robust, generalized **UI-based I/O** modeled on the existing `batch_upload` and SOP-download
APIs, with absolute per-user isolation preserved.

## 2. Goal / success criteria

- **Upload (3a):** a user uploads files through the chat UI; they land in **that user's
  `input/`** dir and are RO-visible (`/data/input`) to all of that user's subsequent CC turns.
  Cross-user isolation from Step 2 is preserved (no new credential reaches the agent; OI-3).
- **Output split (3b):** on turn end, new `scratch/` content is split **hybrid** —
  **artifacts** (deliverables, everything outside `scratch/raw/`) are bundled (zip if >1) and
  **downloadable in the UI**; **raw** (`scratch/raw/` + the session jsonl) goes to the user's
  `output/raw/` and to the DB.
- **Activity panel (3c):** the UI shows, per CC turn, a structured **activity panel**
  (commands run, files created/modified, tools used) that **survives session reload** (unlike
  native ephemeral `debugEntries`).
- **Recoverable raw (raw):** the **full session jsonl** is stored **zstd-compressed in the DB**
  (dedicated table, on-demand load) so a turn's complete transcript is recoverable even if the
  host dir is wiped.
- **Dropbox removed (3d):** no "Saved to your Dropbox" copy, no hardcoded laptop path, no dead
  Dropbox config referenced by CC.
- **Authoritative session id (3e):** new-chat id promoted from the HTTP-202 body.
- **No regression** to Step 1b `--resume`, 1c memory, the NS route, Step 2 isolation, or OI-3.

## 3. Target layout

Extends the Step-2 tree (new/changed nodes marked **NEW**):

```
<DMAC_USER_ROOT>/<projectID-slug>/
├── shared/                          RO -> /data/shared
└── <user>/
    ├── input/                       RO -> /data/input    NEW: populated by uploads (3a)
    ├── scratch/                     RW -> /data/scratch  (the only dir CC writes)
    │   └── raw/                      NEW (convention): agent debug output -> raw, NOT bundled
    ├── cc-state/<session>/          RW -> Claude home    (1b/1c, unchanged)
    └── output/
        ├── artifacts/                NEW: published deliverables (zip source for download)
        └── raw/                      NEW: scratch/raw/ + transcript copy (debug, on disk)
```

Plus **DB** (3c + raw):
- `ChatSession.extra_state["cc_traces"]` — list of per-turn **extracted trace** objects (§6).
- **NEW table** `CCSessionTranscript` — zstd-compressed full jsonl per (session, turn) (§7).

## 4. Upload (3a) — async, modeled on `batch_upload`

**Pattern source (verified):** `batch_upload/views.py:160` (`POST /api/batch-upload/start/`,
multipart `getlist("file")`, `BATCH_UPLOAD_MAX_TOTAL_BYTES`=200MB at `settings.py:430`,
saves under `MEDIA_ROOT`, enqueues Celery, per-user file `job_index`, polled via
`/status/{job_id}/`). We mirror the **UI + async pattern**, NOT the ingestion DAG.

- **Endpoint:** `POST /nextseek_api/cc-assistant/upload/` — multipart, `getlist("file")`.
  Enforce a size cap (reuse `BATCH_UPLOAD_MAX_TOTAL_BYTES` or a CC-specific
  `CC_UPLOAD_MAX_TOTAL_BYTES`). Authenticated as the logged-in user.
- **Destination:** resolve the user's project with the **Step-2 primitive**
  `resolve_user_project(api_user, api_pass)` → `build_user_dirs(...).input_src` =
  `<root>/<project>/<user>/input/`. Files are saved there. **Filenames validated**
  (reuse the Step-2 `_validate_*` traversal guards; reject `/`, `..`, NUL, absolute paths).
- **Async (Celery):** enqueue a CC-upload task (mirror `batch_upload/tasks.py` + `job_index`
  + `update_state` progress); frontend polls a `GET …/upload/status/<job_id>/`. The task body
  is just *validated save into `input/`* — no extract/convert/insert.
  - Rationale: keeps gunicorn workers responsive for large multi-file uploads; matches the
    established `batch_upload` UX. (Decision **E1**.)
- **Lifecycle:** uploads are **persistent** in `input/` and visible (RO) to all of that user's
  later turns — not scoped to a single message (matches §F-6 "a directory spawned with all the
  files uploaded, mounted RO"). (Decision **E2**.) A later substep MAY add delete/list; Step 3
  ships upload + list.
- **Frontend:** add a file-attach control + upload progress + uploaded-file list near the
  composer (`MessageInput.tsx:53`). Reuse the `batch_upload.embed.html` UI idioms (multi-file,
  progress poll). No file-upload UI exists today.

## 5. Output split + publish (3b) — hybrid

**Pattern source (verified):** `_publish_artifacts` (`cc_engine.py:639`),
`run_tracker.snapshot_scratch_files/diff_files`, `copier.copy_files`
(`dmac_assistant/src/dmac_assistant/{run_tracker,copier}.py`); zip-download pattern
`content_blobs.download_batch()` (tempfile zip + `manifest.json` + `StreamingHttpResponse`
with `Content-Disposition: attachment`); frontend artifact renderer
`ReportArtifacts.tsx` consuming `ArtifactFile {artifact_type:"file", key, label, file_format}`
(`chat_frontend/src/lib/types/chat.ts:23`).

- **Partition (hybrid, Decision E3):** rework `_publish_artifacts` to diff `scratch/` (as
  today) and split the changed set:
  - **artifacts** = changed files **NOT** under `scratch/raw/` → copied to `output/artifacts/`.
  - **raw-files** = changed files **under** `scratch/raw/` → copied to `output/raw/`
    (NOT bundled, NOT downloaded by default).
- **Bundle + download:** if artifacts > 1, zip them (reuse `content_blobs.download_batch`'s
  tempfile/zip/manifest approach); single artifact → direct file. New endpoint
  `GET /nextseek_api/cc-assistant/artifacts/<session>/download/?key=<key>` streams the
  file/zip with `Content-Disposition` (mirror SOP `StreamingHttpResponse`).
- **Wire into the turn:** **replace** the `artifacts_published` list + the "Saved to your
  Dropbox" reply text (`cc_engine.py:580-587`) with **`artifacts` entries** on the
  `query_complete` event — one `ArtifactFile` per deliverable (or one for the zip), whose
  **`key`** resolves to the download endpoint. The existing `ReportArtifacts` UI renders them
  with download buttons; `onArtifactDownload(key)` must map a CC key → the new endpoint.
  **[CONFIRM@PLAN]** the exact `key→URL` wiring the frontend download handler uses today
  (bundle_id + key vs key alone) and extend it for CC.

## 6. Activity panel (3c) — extracted trace → `extra_state` → "Search Details"

**Pattern source (verified):** `MessageBubble.tsx:111-159` (collapsible "Search Details"
rendering `message.debugEntries`); native `debugEntries` accumulate from live `agent_complete`
events and are **not persisted** (`EmbeddedApp.tsx:98-123`; empty on reload via
`useMessages.hydrateFromTurns`). Trace source: `cc_summary.parse_transcript` (`cc_summary.py:46`)
already parses the jsonl with **orjson** (`orjson.loads` per line, `cc_summary.py:59`) into
records and an "actions_view" (queries, assistant text, tool invocations); the codebase uses
pydantic (`cc_summary.py:186 model_validate`).

### 6.1 Two data sources (do not conflate)

The trace is assembled from **two distinct sources** — this is load-bearing:

1. **The persisted `.jsonl`** (`cc-state/<session>/.claude/projects/**/*.jsonl`) — the
   conversation records (`user`/`assistant` with content blocks, tool results). Parsed with
   **orjson** + bulk-validated with a **pydantic `TypeAdapter`** (§6.3). Yields
   `steps` / `commands` / `tools_used`.
2. **The headless `result` frame** — Claude Code's terminal `{"type":"result", …}` event on the
   stream. **Verified this session: it is NOT written into the persisted `.jsonl`** (a real
   transcript on the live box has no `"type":"result"` record). It is consumed at runtime by
   `translate._handle_result` (`translate.py:130-156`), which today surfaces `total_cost_usd`
   (`:155`) but **drops `num_turns` and `duration_ms`**. Yields `num_turns` / `duration_ms` /
   `cost_usd`.

Plus the **scratch diff** (§5) for the authoritative `files_created` / `files_modified`.

> **`num_turns` semantics (corrected):** this is the number of **internal CC agent turns**
> Claude Code took *within one chat turn* (headless metadata), **not** a count of user messages.
> One `CCTrace` == one chat turn; `num_turns` is the inner loop count.

### 6.2 Schema — pydantic models (DRAFT, Decision E4; user may edit fields)

```python
from pydantic import BaseModel, Field, TypeAdapter
from typing import Annotated, Literal, Union

class ToolStep(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    tool: str                              # "Bash" | "Write" | "Edit" | "Read" | ...
    detail: str | None = None              # the command, or the file path
    action: Literal["created", "modified"] | None = None

class TextStep(BaseModel):
    type: Literal["text"] = "text"
    summary: str

Step = Annotated[Union[ToolStep, TextStep], Field(discriminator="type")]

class CCTrace(BaseModel):
    cc_session_id: str
    ts: str                                # ISO-8601
    # --- headless result-frame metadata (from translate._handle_result, NOT the .jsonl) ---
    num_turns: int | None = None           # CC internal agent turns within this ONE chat turn
    duration_ms: int | None = None
    cost_usd: float | None = None          # result frame total_cost_usd
    # --- parsed from the persisted .jsonl (orjson + TypeAdapter, §6.3) ---
    steps: list[Step] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    tools_used: dict[str, int] = Field(default_factory=dict)  # tally of tool_use.name
    # --- authoritative, from the scratch diff (§5) ---
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
```

### 6.3 jsonl parsing — orjson + bulk `TypeAdapter`

Per the pattern SPEC-1c already prescribes ("orjson + bulk `TypeAdapter` over a discriminated
union"): orjson-decode every line (the existing `parse_transcript` path), then validate the whole
list at once. A trailing catch-all keeps it forward-compatible if Claude Code adds record types.

```python
class _Assistant(BaseModel):
    type: Literal["assistant"]
    message: dict                          # {role, content: [{type:"text"|"tool_use", ...}]}
class _User(BaseModel):
    type: Literal["user"]
    message: dict | None = None
class _Other(BaseModel):                   # forward-compat catch-all for new/unknown types
    type: str

Record  = Union[_Assistant, _User, _Other]   # left-to-right; _Other last
RECORDS = TypeAdapter(list[Record])          # RECORDS.validate_python(orjson-decoded lines)
```

`steps` are built from `_Assistant.message.content` blocks (`text` → `TextStep`; `tool_use` →
`ToolStep` with `tool=name`, `detail` = `input.command` for Bash or `input.file_path` for
Write/Edit); `commands` = the Bash `detail`s; `tools_used` = a tally of `tool_use` names.
`files_created`/`files_modified` come from the **§5 scratch diff** (authoritative), not from
trusting tool calls. **[CONFIRM@PLAN]** the exact discriminated-union-vs-ordered-union handling
of unknown `type` values against the installed pydantic version (the project pins
`pydantic>=2.13`).

### 6.4 translate.py extension (required for `num_turns`/`duration_ms`)

Extend `_handle_result` (`translate.py:149-156`) so the `query_complete` frame also carries
`num_turns` and `duration_ms` from the result `payload` (it already carries `total_cost_usd`):

```python
        return [(
            "query_complete",
            {"reply": reply or "(no response)", "bundle_id": None,
             "cc_session_id": self.session_id,
             "total_cost_usd": payload.get("total_cost_usd"),
             "num_turns": payload.get("num_turns"),
             "duration_ms": payload.get("duration_ms")},
        )]
```

### 6.5 Persist + render

- **Persist:** append the `CCTrace` (as `model_dump()`) to `ChatSession.extra_state["cc_traces"]`
  via the existing `session_adapter` (Decision E5 — chosen over a dedicated table for the
  *display* trace: no migration, matches `chat_nextseek`, hydrates through the existing Turn
  path). Keep it small (it is loaded on every session read; full fidelity lives in §7).
- **Live + reload:** emit the `CCTrace` on `query_complete` so the panel shows immediately (like
  `debugEntries`), AND persist so it **survives reload**. Extend the Turn serialization
  (`services/assistant.py:476-545`) and `useMessages.hydrateFromTurns` to surface `cc_traces`.
- **Frontend:** extend the **Search Details** collapsible (`MessageBubble.tsx:111`) to render a
  CC activity view (num_turns, commands, files created/modified, tools, cost, duration) from the
  trace. Reuse the existing toggle + panel chrome. Add a `CCTrace` TS type mirroring §6.2.

## 7. Full transcript recoverability (raw → DB, zstd)

**Verified:** no compression / DB transcript storage exists today (grep `zstd|lz4|gzip|zlib|
BinaryField|base64` across `cc_assistant`+`assistant` = none); `zstandard` is **not installed**
in the deployed venv.

- **New model `CCSessionTranscript`** (Decision E6): `BinaryField` holding the **zstd-compressed
  full jsonl**, keyed by `(chat_session, cc_session_id, turn/run_id)`, with `created_at` and an
  uncompressed-size column. Stored in its **own table** (NOT `extra_state`) so it is loaded
  **only on demand** and never bloats `ChatSession` hot reads.
- **Compression:** **zstd** via the `zstandard` package (Decision E7). Add it to the image
  build (`pyproject`/requirements + rebuild). Provide a tiny `cc_transcript_store` module with
  `compress(jsonl_bytes) -> bytes` / `decompress(blob) -> bytes` (zstd level configurable;
  default a fast level) and round-trip-tested.
- **Write path:** after a turn, read the session jsonl (the same `read_bytes()` already done at
  `services/cc_assistant.py:103/300`), compress, and upsert the `CCSessionTranscript` row.
- **Recover endpoint:** `GET /nextseek_api/cc-assistant/transcript/<session>/<turn>/` →
  decompress → stream the jsonl (auth: owner only, `ChatSession.user == request.user`).
- This is the durable "raw … added to our persistent sessioninfo database" of §F-6; the
  on-disk `output/raw/` copy (§5) is convenience, the DB blob is the source of truth.

## 8. Remove Dropbox (3d)

- **Reply text:** remove the "📁 Saved to your Dropbox" augmentation (`cc_engine.py:580-587`);
  the §5 `artifacts` channel now delivers files. No replacement copy needed (the UI shows the
  download); if any text is kept it must not say "Dropbox".
- **Laptop default (Decision E8 — needs per-change sign-off, behavior change):** replace
  `cc_config.py:15 _DEFAULT_HOST_USER_ROOT = "/Users/taishajoseph/dmac-dev/users"` with a
  **neutral container default `/srv/dmac/users`** (the real prod/dev host root). Do **not**
  fail-closed; keep a sane default.
- **Dead config audit:** remove `dmac_assistant/src/dmac_assistant/config.py` `dropbox_root`
  field + `_DEV_DEFAULT_DROPBOX_ROOT` and `seek/views.py:94 DROPBOX_DIRECTORY` **only if** a
  grep proves they are unreferenced by any live CC/NS route (audit first; do not break native
  code). Update the `cc_config.py` module docstring ("Dropbox mounts" → "user-scoped mounts").

## 9. Authoritative session id (3e)

Implement the two pre-marked TODOs: in `AppLayout.tsx:117` and `EmbeddedApp.tsx:126`, promote a
new chat's `activeSessionId` from the **authoritative HTTP-202 body** id
(`serviceRef.current.sessionId` / `service.sessionId`, the `AsyncQueryResponse.session_id`
captured at `chatApi.ts:96`) instead of the WS `query_complete` `d.session_id`. Defense-in-depth
(not a live bug after `4016a9b`). Low-risk, frontend-only.

## 10. Isolation / security (preserve Step 2 + OI-3)

- **Upload** runs host-side in Django as the logged-in user; the project is resolved with the
  user's own creds (Step-2 `resolve_user_project`); **no new credential reaches the agent**.
  Files land only in **that user's** `input/`. Uploaded filenames are validated before any path
  interpolation (Step-2 guards). The agent still sees only `input/` + `shared/` RO and writes
  only `scratch/` (Step 2 invariant unchanged).
- **Download / recover / transcript** endpoints are **owner-scoped**
  (`ChatSession.objects.filter(user=request.user)`); a user can never fetch another user's
  artifacts/transcript. Path arguments validated; no traversal out of the user's `output/`.
- **zstd decompression** operates only on bytes we wrote; bound output size on decompress to
  avoid a decompression bomb from a corrupted row.

## 11. Resolved decisions (locked 2026-06-30, user-selected)

- **E1 — Upload transport:** **async (Celery + job-status polling)**, mirroring `batch_upload`.
- **E2 — Upload lifecycle:** uploads populate the user's **persistent `input/`** (RO to all
  later turns), not attached to a single message.
- **E3 — Output split:** **hybrid** — artifacts = new scratch outside `scratch/raw/`;
  raw = `scratch/raw/` + the jsonl transcript.
- **E4 — Trace schema:** Claude **drafts** (§6), user edits at the spec-review gate.
- **E5 — Display trace storage:** `ChatSession.extra_state["cc_traces"]` (no migration).
- **E6 — Full transcript storage:** **dedicated `CCSessionTranscript` table** (BinaryField,
  on-demand load), not `extra_state`.
- **E7 — Compression:** **zstd** (`zstandard` dependency added to the image).
- **E8 — Dropbox default:** keep a **neutral default `/srv/dmac/users`** (remove the laptop
  path); do not fail-closed.
- **E9 — Artifacts UI:** **reuse** the existing `artifacts`/`ReportArtifacts` channel.
- **E10 — Trace schema = pydantic; jsonl via orjson + `TypeAdapter`:** `CCTrace`/`Step` are
  pydantic models (§6.2); jsonl parsed with orjson and bulk-validated with a `TypeAdapter` over a
  record union (§6.3). `num_turns` is the **headless internal-turn count** from the `result`
  frame (§6.1/§6.4), **not** a user-message count, and is **not** in the persisted `.jsonl` —
  `translate._handle_result` is extended to surface `num_turns`/`duration_ms`.

## 12. Testing (TDD-first)

Hermetic units (Step-2 `uv run --no-project --with pytest … --noconftest` harness; no Docker/DB/
network/spend):
- **Upload save:** validated multipart save into `input_src`; size-cap rejection; filename
  traversal rejection; project resolution (stubbed SeekDB, as in Step 2); fail-closed on
  resolution error.
- **Publish partition (§5):** changed-file set splits correctly — `scratch/raw/*` → raw,
  everything else → artifacts; zip-if-multiple; single-file passthrough; nothing-changed → no
  artifacts.
- **Trace extraction (§6):** orjson + `TypeAdapter` bulk-validate a **fixture jsonl** → expected
  `steps`/`commands`/`tools_used`; `files_created`/`files_modified` from a stubbed scratch diff;
  unknown record `type` falls through to `_Other` (forward-compat); deterministic `CCTrace`.
- **Result-frame metadata (§6.4):** `translate._handle_result` on a fixture `result` payload
  surfaces `num_turns`/`duration_ms`/`total_cost_usd` on the `query_complete` frame; missing
  fields → `None` (not a crash). Confirms `num_turns` is **not** sourced from the `.jsonl`.
- **Transcript store (§7):** `compress`/`decompress` round-trips byte-identical jsonl; size
  bound on decompress.
- **Owner-scoping:** download/recover endpoints reject a non-owner (unit at the queryset/guard
  seam where reachable without the Django test DB).
- **Dropbox grep-guard (extend Step-2 guard):** no "Saved to your Dropbox", no laptop path,
  no removed dead symbols reappear.

Frontend: component tests if the project has a runner **[CONFIRM@PLAN]**; otherwise live
Playwright.

Live verification (forced-CC, ≤ $2 cap, Playwright per standing preference; per-change
sign-off): upload a file → appears in `input/` → CC turn reads it; CC writes a deliverable +
something to `scratch/raw/` → deliverable zips & downloads, raw lands in `output/raw/` + DB;
activity panel shows commands/files and **persists across reload**; full jsonl recoverable via
the transcript endpoint; 3e: new chat second turn does not 404. Then re-run the **1b resume** +
**1c memory** live checks (no regression).

## 13. Deployment

- **New dependency:** `zstandard` added to the image (`pyproject`/requirements + rebuild). The
  hermetic harness adds `--with zstandard`.
- **Migration:** one migration for `CCSessionTranscript` (additive; no change to `ChatSession`).
- **Frontend rebuild:** `chat_frontend` (vite) → `static/js` bundles; the build step must run
  before deploy. **[CONFIRM@PLAN]** the exact frontend build command/output path.
- **Env:** no new required env (size cap optional `CC_UPLOAD_MAX_TOTAL_BYTES`); `DMAC_USER_ROOT`
  already set (Step 2). `MEDIA_ROOT` (`/media`) already exists for any staged uploads.
- **Procedure:** same Step-0 deploy (snapshot `:pre-step3` → fast-forward SA clone → rebuild →
  recreate `--no-deps nextseek` via the SA `docker:cli` helper). Rollback via `rollback.sh`.
  Per-change sign-off before touching the running instance; the Dropbox-default change (E8) and
  any dead-config removal ship as their own reviewed diffs.

## 14. Out of scope (Step 3)

- **Multi-project / admin routing** (Step-2 resolver computes the list; consumer uses `[0]`).
- **Shared-folder population/management** (the dir exists + mounts RO; contents are future).
- **Upload delete/quota management** beyond upload + list (later substep if needed).
- **NExtSEEK ingestion of uploads** (the batch_upload DAG) — uploads are agent inputs only (E1
  mirrors the *UI/async pattern*, not the pipeline).
- **Re-summarization / analytics over stored transcripts** — the recover endpoint exists; richer
  tooling is future.
