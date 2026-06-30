# PLAN-3 Phase 2 Review 16 (Fresh, Canonical Prompt)

**Target:** `nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`  
**Locked design:** `SPEC-3-ui-based-io.md`  
**Sibling context (sequencing only, not re-vetted):** PLAN-7 / SPEC-7 — Step 3 Task 13 must deploy and commit `live_gate_transcript.txt` before Step 7; `DEPLOY.md` append-only merge order preserved.  
**Reviewer stance:** Cold-start TDD contract; adversarial; no prior vetting files read.

**Live spot-checks (claim verification only):** `cc_engine.py:573-588` still Dropbox + list `_publish_artifacts` return; `cc_provision.py:68-76` has `input_src` but no `input_mnt`; `services/cc_assistant.py:337-349` `run_cc_turn(...)` lacks `chat_session` / `on_turn_complete`; `test_cc_realstack.py:190-212` still `artifacts_published` + `assertTrue(published)`; `validate_cc_acceptance.py:121-130` still flat `{user_id}/` heuristic; `AppLayout.tsx:42,62-64,112-125` dual `NextseekApiService` + `query_complete` only `addAssistantMessage` / WS `d.session_id`; `useMessages.ts:66-90` hydrate omits `ccTraces` / `mode`; `translate.py:149-156` lacks `num_turns`/`duration_ms`; `batch_upload/celery_app.py:54` imports `cc_sweep` only (no upload task); `zstandard.stream_reader` exists (uv probe).

---

## 2A — Vet

### Finding 2A-1 — HIGH — `serialize_cc_chat_log_entry` is not paste-ready

**Location:** Task 11 — `cc_turn_complete.py` / `TurnCompletePayload`; snippet ends with `def serialize_cc_chat_log_entry(payload: TurnCompletePayload) -> dict: ...`

**Why it is a defect:** Task 11a and Task 12 reload hydration depend on an exact `chat_log` entry shape (`assistant_reply`, `mode: "cc"`, `artifacts`, `cc_traces`, `turn_id`, `ts`, `user_query`). The serializer body is literally `...`. A cold-start implementer must invent field names and omit `mode: "cc"` — then Task 12 Step 5 (`message.mode === "cc"`) fails on reload and CC artifact download routes to the native bundle handler (`bundleId!`), satisfying neither §5 nor §6.5.

**Concrete fix:** Paste the full function, e.g.:

```python
def serialize_cc_chat_log_entry(payload: TurnCompletePayload) -> dict:
    return {
        "user_query": payload.user_query,
        "assistant_reply": payload.assistant_reply,
        "mode": "cc",
        "ts": payload.ts,
        "artifacts": payload.artifacts,
        "cc_traces": payload.cc_traces,
        "turn_id": payload.turn_id,
    }
```

Add a hermetic test in `test_cc_chat_log_writer.py` asserting every key including `"mode": "cc"`.

---

### Finding 2A-2 — HIGH — Task 6 Step 5c leaves `test_cc_realstack.py` capture + assertions incomplete

**Location:** Task 6 Step 5c — *"Also update `test_cc_realstack.py`"* vs live `test_cc_realstack.py:190-212`

**Why it is a defect:** Plan pastes only an extended `forced_result.json` dump fragment. Live code still sets `published = data.get("artifacts_published")`, writes `published_files.json` from that list, and asserts `self.assertTrue(published)`. After Task 6 removes `artifacts_published`, hermetic realstack + `validate_cc_acceptance` check 16 will fail unless the implementer reverse-engineers the full diff. Lazy fix: delete/weaken assertions while Task 13 live gate still passes — losing turn-scoped artifact proof (§5, Global Constraints turn-scoped keys).

**Concrete fix:** Paste-ready replacement for lines ~181-212: dump `"artifacts": data.get("artifacts") or []`; drop `published_files.json` or repurpose; replace `self.assertTrue(published)` with non-empty `artifacts` list + each key contains `/` (turn-scoped); align check 16 with the pasted `artifacts_turn_scoped` body; add `test_validate_cc_acceptance.py` fixture test (named in Step 5c but not pasted).

---

### Finding 2A-3 — MEDIUM — File Structure omits modules the plan creates

**Location:** § File Structure (Create) vs Tasks 9b, 10, 11, 11a

**Why it is a defect:** Cold-start implementers use the File Structure as the inventory. Missing from **Create:** `cc_turn_complete.py`, `cc_endpoint_guards.py`, `cc_upload_list.py`, `test_cc_newest_jsonl.py`, `test_cc_chat_log_writer.py`, `test_cc_endpoint_guards.py`, `test_cc_upload_list.py`, `test_validate_cc_acceptance.py` (extended). Risk: modules land ad hoc, Task 13 commit manifests incomplete, Step 7 gate evidence path forgotten.

**Concrete fix:** Extend **Create** list to match every `Create` path referenced in Tasks 9b–11a; add `cc_engine.py` signature kwargs to **Modify** (`chat_session`, `user_query`, `on_turn_complete`).

---

### Finding 2A-4 — MEDIUM — Permissions catalogue incomplete for staging hygiene

**Location:** § Permissions Required; Task 9 upload staging

**Why it is a defect:** Upload view writes temp files under `MEDIA_ROOT/cc_upload_staging/` with timestamped names but the plan never specifies cleanup on Celery failure, duplicate upload, or worker crash. Over time this fills `/media` on the dev/prod host — silent I/O failure unrelated to CC turns.

**Concrete fix:** Task 9 Step 5 bullet: Celery task `finally` unlinks each `tmp_path`; optional TTL sweep or reuse single staging subdir per job_id; Task 13 Step 4 evidence includes `ls` of staging dir before/after upload.

---

### Permissions catalogue (complete for execution)

| Permission / resource | Tasks | Notes |
|----------------------|-------|-------|
| Hermetic `uv run` pytest | 1–7, 9, 9b, guards | No DB CREATE on vet box |
| `makemigrations` (no migrate) | 2 | Model read only |
| Celery broker + `batch_upload` queue | 9, 13 Step 0/4 | Explicit `cc_upload_tasks` import in `celery_app.py` |
| `MEDIA_ROOT` write + cleanup | 9 | Staging + job index under `celery_jobs/` |
| Host/mount FS: `DMAC_USER_ROOT`, `input_mnt`, `output/artifacts/<turn_id>/`, `cc-state` jsonl | 3, 6, 9, 10, 11 | Bind vs mount discipline |
| Django ORM + migrate `0007` | 2, 11a, 13 | `CCSessionTranscript`, `extra_state` RMW |
| DRF owner-scoped endpoints | 9, 9b, 10 | SEEK session auth |
| Docker rebuild + recreate | 13 | Step-0 procedure; per-change sign-off |
| Playwright forced-CC ≤ $2 | 13 | Upload, split, reload panel, 3e, 1b/1c regression |
| `npm run test` + `build:embedded` | 12, 13 | Vitest + `static/js/chat_assistant/` |
| Git branch `cc-step3-ui-io` | all | Merge to `feat/dmac-assistant-full-integration` |
| Commit `evidence/3-ui-based-io-live/live_gate_transcript.txt` | 13 Step 9 | Hard gate for PLAN-7 Step 7 |

---

## 2B — Stress Test

1. **Most likely failure:** Celery upload task not registered (`cc_assistant.upload` missing from worker) → HTTP 202 with stuck `PENDING` (Risk Register #2; mitigated in Task 9 Step 3b + Task 13 Step 0 — must not be skipped).

2. **Most catastrophic failure:** Owner-scoping bug on artifact-download or transcript-recover (cross-user leak) — Task 10 guards are pure-only; live proof deferred to Task 13 with no scripted negative test (another user’s session id). Rollback: block deploy immediately.

3. **Hidden dependencies:** (a) Task 11a before Task 11 Step 2 (documented but Task 11 section precedes 11a); (b) Tasks 6+8 atomic on `cc_engine.py`; (c) PLAN-7 blocks on committed `live_gate_transcript.txt`; (d) 1c `classify_tool_use` refactor must keep `_tool_use_line` byte-identical (Task 4 Step 4 full suite gate).

4. **Ambiguous success conditions:** Task 13 “Confirm panel survives reload” is hardened by `live_gate_transcript.txt` + `jq` on `turns[*].cc_traces` — good. Weaker: Task 9/10/11 “import check only; live in Task 13” — acceptable if Task 13 gate is mandatory (it is).

5. **Coverage risk:** Global Constraints require ≥95% on pure modules, but most task verify commands omit `--cov-fail-under=95` (only Task 6 Step 5b shows the flag). Implementer can skip coverage and still “PASS” per-task commands.

6. **Rollback:** `rollback.sh` + `:pre-step3` snapshot (Task 13); pause before E8 default change (Task 8 separate commit); persist `RuntimeError` policy (Task 11) prevents silent false-done.

---

## 2C — Validate External Dependencies

| Dependency | Verification | Status |
|------------|--------------|--------|
| `zstandard>=0.25` | PyPI; `ZstdDecompressor.stream_reader` probed OK | OK — Task 1 early add |
| pydantic v2 unpinned | Ordered `Union`, `_Other` last | OK — matches SPEC |
| orjson | Already in 1c path | OK |
| Celery `batch_upload.celery_app` | Live import pattern + route `cc_assistant.*` | OK — requires explicit task import |
| Vitest / `build:embedded` | `package.json` scripts confirmed | OK |
| Migration `0007` | Depends on live `0006_merge_extra_state_guards` | OK |
| `register_job(..., project_id=int)` | `ProjectIdentity.id` is often non-digit (`personal-*`) → stores `0`; `user_owns_job` ignores `project_id` | OK — no defect |
| Playwright / live CC | External spend | Accepted Task 13 exception |

### Finding 2C-1 — MEDIUM — Recover endpoint omits SPEC §10 decompress bound

**Location:** Task 10 Step 2 — `jsonl = decompress(bytes(row.blob))`

**Why it is a defect:** SPEC §10 / Task 1 require bounded decompression (`max_bytes`). Pure module tests the bound; HTTP recover path does not pass `max_bytes`, so a corrupted `CCSessionTranscript` row could exhaust worker memory — spec/security gap at the boundary the plan claims to implement.

**Concrete fix:** `decompress(bytes(row.blob), max_bytes=settings.CC_TRANSCRIPT_MAX_BYTES or 256*1024*1024)`; hermetic test on guard helper or integration note in Task 13 Step 5 transcript fetch.

---

## 2D — Gameproof

| Rank | Task / condition (quoted) | Cheapest fake | Remedy |
|------|---------------------------|---------------|--------|
| 1 | Task 11a: *"grep/source guard for `assistant_reply` key"* | Serializer returns `{"assistant_reply": ""}` only; omit `mode`, `cc_traces`, `turn_id` | Full paste serializer + assert all keys (2A-1) |
| 2 | Task 6 Step 5c: *"Update test_cc_realstack.py"* | Change json dump only; leave `artifacts_published` assert | Full paste block (2A-2) |
| 3 | Global: *"≥95% line coverage … commit blocked if below floor"* | Run pytest without `--cov`; skip Task 6 Step 5b flag on other modules | Append `--cov=… --cov-fail-under=95` to every pure-module verify command (Tasks 1,3,4,5,7,9,9b, guards) |
| 4 | Task 13: *"upload Celery job completes"* | Manual one-line “SUCCESS” in markdown index | Already mitigated: `live_gate_transcript.txt` + celery inspect in Step 0 |
| 5 | Task 4: fixture jsonl → `steps` | Hardcode expected list in extractor | Second fixture `cc_transcript_multitool.jsonl` (Task 4 Step 9b) — keep |
| 6 | Task 11: *"re-raise on persist failure"* | `except: pass` around `on_turn_complete` | Task 13 Step 6 reload gate + no try/except in paste block |
| 7 | Task 12 Vitest: *"assert fetch URLs match"* | Mock fetch; never assert response handling | Require status/body assertions on upload + download mocks |
| 8 | Task 8 grep-guard | Move Dropbox string to comment in unrelated file | Scan `cc_assistant/` + `services/cc_assistant.py` (already specified) |

**No-op oracle examples:** Task 7 `Turn.cc_traces` field alone passes tests without Task 11a writing `chat_log` — Task 13 Step 6 reload catches this. Task 5 `_handle_result` keys pass without WS frontend wiring — Task 12 + 13 catch.

---

## Non-blocking cosmetic notes

- Task 13 step numbering non-monotonic (Step 3b before Step 2).
- Phase 2 Vetting Log references prior `.vetting/` paths — informational only.
- Duplicate `zstandard` add in Task 1 and Task 13 Step 1 (harmless redundancy).
- Task 11 section appears before Task 11a in document order (execution order warnings are present).

---

## Summary for orchestrator

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 4 |
| LOW | 3 (cosmetic only) |

**Top findings (one line each):**
1. **HIGH** — `serialize_cc_chat_log_entry` is `...` only; reload needs explicit `mode: "cc"` and full chat_log shape.
2. **HIGH** — Task 6 Step 5c does not paste-complete `test_cc_realstack.py` / validator migration off `artifacts_published`.
3. **MEDIUM** — File Structure missing several Create modules/tests cold-start agents need.
4. **MEDIUM** — Global 95% coverage floor not enforced in most task verify commands.
5. **MEDIUM** — Transcript recover endpoint skips decompress `max_bytes` (SPEC §10).

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
