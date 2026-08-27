# PLAN-3 Phase 2 Pre-Execution Review (iter-6, fresh cold context)

**Target:** `nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`  
**Locked spec:** `SPEC-3-ui-based-io.md` (E1–E10, enriched §6.2)  
**Reviewer:** Independent adversarial cold-context reviewer  
**Date:** 2026-06-30  
**Sibling note:** E8 `/srv/dmac/users` default may be superseded at Step 7 (PLAN-7 G7-10); not re-vetting PLAN-7.

---

## Executive summary

The plan is materially improved after iter-5 hardening: code anchors cited in the plan match live code (`cc_engine.py:573–587` Dropbox path, `translate.py:149–156` missing `num_turns`/`duration_ms`, `UserDirs` without `input_mnt`, `useChatApi.ts` late `sessionId` promotion, `assistant.py:515–529` `assistant_reply` projection, Celery `cc_sweep` import pattern in `batch_upload/celery_app.py`). Task 11/11a persist relocation, Celery registration Step 3b, AppLayout `getAuthoritativeSessionId`, and Task 9b list are present.

Remaining defects are concentrated in **turn-scoped artifact correctness**, **SPEC §3 on-disk jsonl copy**, **Task 11 paste-ready code vs fail-loud policy**, **coverage enforcement drift**, and **task-ordering traps**. None are plan-fatal if fixed before Task 11/12/13, but several can pass hermetic gates while failing user-visible behavior.

**Counts:** CRITICAL 0 · HIGH 2 · MEDIUM 6 · LOW 4

**Verdict:** CONDITIONAL_ACCEPTANCE

---

## 2A — Vet (correctness, spec traceability, anchor verification)

### Finding 2A-1 — HIGH — Artifact download path is user-global, not turn-scoped; keys can collide across turns

**Plan quotes:** Task 6 `_publish_artifacts` copies deliverables into `output/artifacts/` preserving scratch relpaths; Task 10 `download_artifact` resolves `art_dir = Path(dirs.output_mnt) / "artifacts"` with owner check only on `ChatSession`, then `target = art_dir / key`.

**Live code today:** `_publish_artifacts` (`cc_engine.py:639–672`) writes into a single per-user `output/` tree with no session/turn namespace.

**Why defect:** Each CC turn diffs scratch and copies **only that turn's** changed files, but into a **shared** artifacts directory. A later turn overwriting `report.md` replaces the on-disk file while an earlier turn's `chat_log` entry still lists `key: "report.md"`. Reload hydration shows the correct per-turn artifact list (Task 11a), but `GET …/artifacts/<session>/download/?key=report.md` reads whatever file exists now — not the blob belonging to that turn. SPEC §5/E9 expects the `artifacts` channel + download wiring to serve **that turn's** deliverables via `ReportArtifacts`.

**Concrete fix:** Namespace publish copy targets under `output/artifacts/<turn_id>/` (or `<chat_session_id>/<turn_id>/`), store namespaced keys in `ArtifactFile.key`, and resolve download against the same prefix; alternatively persist artifact bytes or zip per turn outside the shared dir. Task 13 live gate should include **two-turn same-basename** download check.

---

### Finding 2A-2 — MEDIUM — SPEC §3 on-disk jsonl copy to `output/raw/` not planned

**Spec quote (§3 target layout):** `output/raw/` — "scratch/raw/ + **transcript copy** (debug, on disk)". §5/E3: "raw = `scratch/raw/` + the session jsonl". §7 acknowledges DB is source of truth but §3/goal still require on-disk convenience copy.

**Plan coverage:** Task 11 reads jsonl from `cc-state` and upserts `CCSessionTranscript`; Task 6 copies only `scratch/raw/*` relpaths. No step copies the session `.jsonl` into `output/raw/`.

**Why defect:** Operator/debug workflows expecting `output/raw/` to mirror SPEC layout will find jsonl only in DB + cc-state. Recover endpoint covers API path, but Step 3 success criteria and layout diagram are not fully implemented.

**Concrete fix:** In Task 11 persist block (or Task 6 raw branch), after reading `raw` jsonl bytes, also `copy2` to `output_mount / "raw" / f"transcript-{turn_id}.jsonl"` (validated basename). Task 13 Step 5 should stat that path on host.

---

### Finding 2A-3 — MEDIUM — Task 11 paste-ready persist block omits mandatory fail-loud else branch

**Plan quotes:** Hardened policy (Task 11): "do not silently skip persist … **re-raise** `RuntimeError('cc persist: missing transcript jsonl after successful turn')`". Paste-ready block (Task 11 L1508–1534): `trace = … if parsed else None` then `if trace is not None: on_turn_complete(...)`.

**Why defect:** Implementer copying the snippet without the prose else-branch will emit live `query_complete` (with artifacts from Task 6) but write **no** `chat_log` / transcript row when jsonl is missing — exactly Risk Register #8 ("swallowed persist exceptions" variant). Hermetic suite stays green; Task 13 reload fails opaquely.

**Concrete fix:** Add explicit paste-ready else:

```python
elif event == "query_complete" and on_turn_complete and chat_session is not None:
    raise RuntimeError("cc persist: missing transcript jsonl after successful turn")
```

Or emit `query_error` and return without `query_complete`. Snippet and policy must match.

---

### Finding 2A-4 — MEDIUM — Task document order (11 → 11a) fights stated blocking dependency

**Plan quotes:** Task 11 section precedes Task 11a; Task 11 Step 2: "Task 11a helper must exist first"; Risk Register #1: chat_log never written.

**Why defect:** Subagent-driven execution typically walks tasks numerically. Task 11 Step 1 (`TurnCompletePayload`, `_newest_jsonl_under`, extend `run_cc_turn` kwargs) can start before Task 11a exists; Step 2 persist wiring needs `_append_cc_turn_complete`. Cross-refs help careful readers but not task-order automation.

**Concrete fix:** Renumber so **Task 11a precedes Task 11**, or merge into a single task with ordered steps 11a.1 → 11.2. Mark Task 11 Step 2 as **BLOCKED** until 11a commit SHA recorded.

---

### Finding 2A-5 — MEDIUM — `recover_transcript` ORM filter omits `cc_session_id` from composite key

**Plan quote (Task 10):** `CCSessionTranscript` `unique_together = (chat_session, cc_session_id, turn_id)`; recover filters `chat_session=cs, turn_id=turn` only, with optional hardening comment for `?cc_session_id=`.

**Why defect:** If Claude resume id rotates within one chat session and turn_id scheme ever collides (or manual re-run replays a turn_id during dev), `.order_by("-created_at").first()` returns the newest row, not necessarily the row for the active CC session. Low probability with UUID `run_id`, but the schema allows multiples.

**Concrete fix:** Require `cc_session_id` query param (or derive from `extra_state["cc_session_id"]` at recover time) and include in filter. Task 13 Step 5 should recover with explicit disambiguation.

---

### Finding 2A-6 — MEDIUM — SPEC §12 owner-scoping hermetic tests absent; deferred entirely to Task 13

**Spec quote (§12):** "Owner-scoping: download/recover endpoints reject a non-owner (unit at the queryset/guard seam where reachable without the Django test DB)."

**Plan quote:** Task 10 — "covered by Task 6 zip + Task 1 decompress (pure seams); endpoints live in Task 13."

**Why defect:** No hermetic seam test for owner queryset (`ChatSession.objects.filter(user=request.user)`) or `_safe_relpath` rejection on download keys. Task 13 live gate is manual and can miss auth regressions under time cap.

**Concrete fix:** Add `test_cc_endpoint_guards.py` with pure helpers extracted from view logic (path join + key validation + queryset filter construction) or source-text guards asserting `user=request.user` on both actions. Minimum: grep guard like Step-2 guards.

---

### Finding 2A-7 — LOW — Task 10 snippet incomplete for drop-in implementation

**Plan quote (Task 10 download action):** Uses `Path`, `Response`, `_iter_file` without module-level imports or `_iter_file` paste in the same task.

**Why defect:** Minor friction; implementer must infer from `content_blobs._iter_and_cleanup` (`content_blobs.py:359`). Not blocking for skilled worker.

**Concrete fix:** Paste `_iter_file` helper and required imports in Task 10 Step 1.

---

### Finding 2A-8 — LOW — `QueryCompleteData` TS type not extended for live WS fields

**Plan quote (Task 12):** Adds `ccTraces` on `Message` and `Turn`; handlers attach `artifacts` and `ccTraces` from WS payload.

**Live code:** `QueryCompleteData` (`chat_frontend/src/lib/types/api.ts:64–70`) has `artifacts?` but no `cc_traces?` or `cc_session_id?`.

**Why defect:** `tsc -b` may fail or force unsafe casts when handlers read `d.cc_traces`.

**Concrete fix:** Task 12 Step 0/2: extend `QueryCompleteData` with optional `cc_traces?: CCTrace[]`.

---

### Verified anchors (no defect)

| Claim | Verified |
|-------|----------|
| `_publish_artifacts` returns `list[str]` + Dropbox reply | `cc_engine.py:573–587`, `:639–672` |
| `_handle_result` lacks `num_turns`/`duration_ms` | `translate.py:149–156` |
| `UserDirs` has `input_src`, no `input_mnt` | `cc_provision.py:60–106` |
| `Turn` lacks `cc_traces`; projection ignores it | `models_api.py:122–138`, `assistant.py:521–529` |
| CC branch never writes `chat_log` | `cc_assistant.py:337–349` — no chat_log writer |
| `useChatApi` sets `sessionId` in `.finally()` only | `useChatApi.ts:42–46` |
| `make_db_event_callback` injects `session_id` on terminal events | `pipeline_adapter.py:21–22` |
| Celery app imports `cc_sweep`, not upload task yet | `batch_upload/celery_app.py:54` |
| Migration dep `0006_merge_extra_state_guards` exists | `migrations/0006_merge_extra_state_guards.py` |
| `DROPBOX_DIRECTORY` dead at `seek/views.py:94` | grep: definition only |
| Vitest + `build:embedded` scripts exist | `chat_frontend/package.json:8–12` |
| `_session_metas` jsonl discovery by mtime | `cc_assistant.py:92–93` |

---

## 2B — Stress Test (failure modes, coupling, execution pressure)

### Finding 2B-1 — HIGH — Multi-turn session stress: shared artifacts dir + reload-truth in DB

**Scenario:** Turn 1 writes `out.csv`; Turn 2 writes `out.csv` again with different content.

**Failure:** UI after reload shows two turns each listing `out.csv`; both download buttons fetch identical (latest) bytes. Activity panel reload test (Task 13 Step 6) passes; download correctness fails silently.

**Tied to:** 2A-1.

---

### Finding 2B-2 — MEDIUM — `_newest_jsonl_under` mtime selection under multi-jsonl stores

**Scenario:** Resumed CC session, stale jsonl touched (backup, sweep), or multiple project dirs under `cc-state/.../projects/`.

**Failure:** Wrong jsonl compressed into `CCSessionTranscript` and wrong `steps` in trace for that turn. Plan mirrors `_session_metas` (acceptable for 1c memory) but per-turn blob needs **turn-local** transcript, not global newest.

**Concrete fix:** Prefer jsonl modified after turn start timestamp, or path associated with `translator.session_id` / container stdout if available; fail loud if ambiguous.

---

### Finding 2B-3 — MEDIUM — Tasks 6+8 atomic coupling vs commit granularity

**Plan:** Task 6 Step 6 removes Dropbox in same edit as hybrid split; Task 8 assumes Dropbox already gone.

**Stress:** Task 6 Step 8 commit message omits Dropbox removal if implementer follows Step 5/5b/8 commits literally (Step 6 commit at L932–938 doesn't include handler caller change at L909–923 — that's Step 6 labeled again as Step 6 caller update).

**Failure:** Intermediate commit leaves `published`-as-list handler breakage or Dropbox string until Step 6 "Step 6" caller update — duplicate step numbering increases botch probability.

**Concrete fix:** Single commit checklist: `_publish_artifacts` dict return + caller + Dropbox removal + grep-guard green.

---

### Finding 2B-4 — LOW — Upload staging orphans on Celery failure

**Scenario:** View stages files under `MEDIA_ROOT/cc_upload_staging/`; worker dies before `os.replace`.

**Failure:** Disk clutter; no security boundary break (staging outside input). Acceptable for dev; note cleanup in Task 9.

---

## 2C — Validate External Dependencies

| Dependency | Plan assertion | Verification | Status |
|------------|----------------|--------------|--------|
| `zstandard>=0.25` | Task 1 pyproject + image | Not in root `pyproject.toml` yet (pre-impl) | OK — add Task 1 |
| pydantic v2 ordered Union | unpinned repo | `cc_summary.py` uses pydantic patterns | OK |
| Celery `batch_upload` queue | mirror `views.py:23` | `from nextseek_api.batch_upload.celery_app import app` pattern valid | OK — Step 3b required |
| Migration 0007 dep | `0006_merge_extra_state_guards` | file exists | OK |
| Vitest | Task 12/13 | `npm run test` in package.json | OK |
| Playwright live gate | Task 13 | external, budget cap stated | Accepted |
| `BATCH_UPLOAD_MAX_TOTAL_BYTES` | settings reuse | standard pattern in batch_upload | OK (not re-verified line-by-line) |
| E8 `/srv/dmac/users` | neutral default | live default still laptop path `cc_config.py:15` | OK — Task 8 behavior change w/ sign-off |

### Finding 2C-1 — LOW — Task 13 Step 1 duplicates Task 1 zstandard work

Redundant deploy reminder; harmless if Task 1 landed correctly. Risk if Task 1 Step 5 skipped.

---

## 2D — Gameproof (cheapest passing fakes)

### Finding 2D-1 — MEDIUM — Global ≥95% coverage mandate not wired into task verify commands

**Plan quote (Global Constraints L29):** Pure modules require `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95`.

**Task verify commands (e.g. Task 1 L112, Task 4 L625):** plain `pytest` without `--cov`.

**Cheapest fake:** Implement minimal lines, skip edge branches, claim Task N complete.

**Remedy:** Append coverage flags to each pure-module task verify step (Tasks 1, 3–7, 9, 9b) as the global constraint states.

---

### Finding 2D-2 — MEDIUM — Task 11a anti-fake remains grep/source guard–heavy

**Plan quote:** Task 11a Step 1 — "assert `_append_cc_turn_complete` source uses `assistant_reply`; mock ORM save" — still allows empty function body with string present elsewhere.

**Cheapest fake:** Stub `on_turn_complete` no-op; live WS shows traces; reload empty.

**Remedy:** Hermetic test calling a **pure** `serialize_cc_chat_log_entry(payload) -> dict` and asserting keys, or mock `ChatSession.save` asserting appended `chat_log[-1]` structure. Task 13 reload gate remains necessary backstop.

---

### Finding 2D-3 — LOW — Task 2 model-shape guard gameable via source-text fallback

Plan allows downgrade to literal grep in `models_db.py`. Acceptable given Task 13 migrate gate.

---

### Finding 2D-4 — LOW — Task 8 grep-guard gameable by moving strings to comments

Plan scans `cc_engine.py` + `services/cc_assistant.py` only; not comments vs strings distinction.

**Remedy:** Extend guard to fail on `Dropbox` case-insensitive in those modules except DEPLOY/evidence paths.

---

## Spec coverage matrix (abbreviated)

| Spec section | Plan tasks | Gap |
|--------------|------------|-----|
| §4 upload + list | 3, 9, 9b, 12 | — |
| §5 output split + download | 6, 10, 12 | Turn-scoped download (2A-1) |
| §6 activity panel | 4, 5, 7, 11, 11a, 12 | Task 11 snippet vs fail-loud (2A-3) |
| §7 transcript DB | 1, 2, 10, 11 | On-disk jsonl copy (2A-2) |
| §8 Dropbox removal | 6, 8 | — |
| §9 session id 3e | 12 | Anchors verified; fix planned |
| §10 security | 9, 10 | Hermetic owner tests missing (2A-6) |
| §12 testing | 1–12 hermetic + 13 live | Coverage enforcement (2D-1) |

---

## Prior iter-5 hardening — independent confirmation

| Hardening claim | Status |
|-----------------|--------|
| `getAuthoritativeSessionId` on `useChatApi` | Present in Task 12 Step 0; **not in live code yet** (expected) |
| AppLayout parity for artifacts/ccTraces | Task 12 Step 2 explicitly requires; live AppLayout only `addAssistantMessage(d.reply)` |
| Task 9b TDD | Task 9b includes failing test first |
| Coverage scope clarified | Global constraint present; **verify commands still omit --cov** |
| Evidence commit path | Task 13 Step 9 lists `live_gate_transcript.txt` |

---

## Required fixes before UNCONDITIONAL_ACCEPTANCE

1. **Turn-scoped artifact storage + download keys** (2A-1 / 2B-1) — HIGH  
2. **Paste-ready Task 11 fail-loud else branch** aligned with hardened policy (2A-3) — treat as HIGH until snippet fixed  
3. **Copy session jsonl to `output/raw/`** or SPEC amend (2A-2) — MEDIUM  
4. **Reorder/merge Task 11 vs 11a** (2A-4) — MEDIUM  
5. **Wire `--cov-fail-under=95` into pure-module verify commands** (2D-1) — MEDIUM  
6. **Owner-scoping hermetic guard** for download/recover (2A-6) — MEDIUM  
7. **Strengthen Task 11a test** beyond grep-only (2D-2) — MEDIUM  

---

## FINAL VERDICT

**CONDITIONAL_ACCEPTANCE** — Plan is executable and spec-aligned on core architecture (pure modules, Celery registration, chat_log reload path, frontend 3e/AppLayout parity intent, atomic Tasks 6+8). **Two HIGH issues** (turn-scoped artifact download correctness; Task 11 snippet vs fail-loud persist policy) and **six MEDIUM issues** remain. Do not assign UA until HIGH items and at least coverage/11a-ordering/owner-guard MEDIUM items are patched in the plan text.

---

*End of review-6-fresh.*
