# Wave 07 — Task 12 (frontend upload, activity panel, artifact download, 3e session id)

**Merge commit:** `61ff478` on cc-step3-ui-io

**Task branch:** cc-step3-ui-io → 61ff478

## Reproduction

```bash
cd /home/taishajo/work/NExtSEEK/chat_frontend
npm ci
npm run test
```

**Expected:** 21 test files passed, 124 tests passed, exit 0 (see `verify-vitest.txt`).

## Scope (PLAN-3 Task 12)

- `UploadControl` + `CCActivityPanel` components with Vitest specs
- `CCTrace` / `CCTraceStep` types; `mode` + `ccTraces` on `Message`; hydrate from `Turn`
- `chatApi.uploadFiles`, `pollUpload`, `downloadCcArtifact`
- `getAuthoritativeSessionId` + single `NextseekApiService` in `AppLayout`
- `MessageBubble` CC artifact download branch (`mode === "cc"`)
- Live `query_complete` wiring in `AppLayout` + `EmbeddedApp`

**Reviewer verdict:** PASS — Vitest green; §4 upload UI, §5 CC download branch, §6 activity panel + reload hydrate, §9 authoritative session id wired.
