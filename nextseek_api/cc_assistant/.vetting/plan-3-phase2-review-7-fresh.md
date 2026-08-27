# PLAN-3 Phase 2 Adversarial Review (iter 7 — fresh, cold context)

**Target:** `nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`  
**Locked design:** `nextseek_api/cc_assistant/SPEC-3-ui-based-io.md` (E1–E10, §6.2)  
**Reviewer:** Independent cold-context pre-execution vet (iter 7)  
**Date:** 2026-06-30  
**Sibling note:** Step 7 gate requires committed `live_gate_transcript.txt` (PLAN-7 G7-10 / SPEC-7 §8). PLAN-7 not re-vetted here; PLAN-3 Task 13 Step 9 correctly commits that artifact.

**Live-code spot-checks:** `useChatApi.ts`, `AppLayout.tsx`, `EmbeddedApp.tsx`, `MessageBubble.tsx`, `cc_engine.py`, `services/assistant.py`, `services/cc_assistant.py`, `test_cc_engine_publish.py`, `test_cc_realstack.py`.

---

## 2A — Vet (correctness vs SPEC + live code)

### Finding 2A-1 — HIGH — Task 6 publish path omits zip-if-multiple required by SPEC §5

**Location:** Task 6 Step 5 prose vs pasted `_publish_artifacts` body; SPEC-3 §5 “Bundle + download”.

**Quote (plan Step 5 prose):**
> “…copies artifacts → `output_mount/"artifacts"`, copies raw → `output_mount/"raw"`, **zips artifacts if >1**, and returns the structured dict.”

**Quote (pasted implementation return):**
> `"raw_zip": None,` — always; loop emits one `ArtifactFile` dict per file, never a zip entry on `query_complete`.

**Why defect:** SPEC §5 and E9 require bundling when >1 deliverable (“zip them”; “one `ArtifactFile` per deliverable **or one for the zip**”). The pasted code contradicts the task’s own Step 5 instruction and leaves `raw_zip` permanently dead. UI will show N separate download buttons with no publish-time zip key; `key=all` in Task 10 is a fallback the frontend is not wired to use.

**Fix:** In `_publish_artifacts`, when `len(art_files) > 1`, call `build_artifact_zip`, write zip under `art_dir`, and emit a **single** artifact entry (e.g. `key=f"{turn_id}/artifacts.zip"`). When `len==1`, emit the single-file entry. Add hermetic assertion in `test_cc_engine_publish.py` for the >1 case.

---

### Finding 2A-2 — HIGH — Task 12 CC artifact download branch breaks on session reload (`bundleId === 0`)

**Location:** Task 12 Step 5; `services/assistant.py:521-529`; `useMessages.ts:87`; `MessageBubble.tsx:106`.

**Quote (plan Task 12 Step 5):**
```ts
message.bundleId != null
  ? onArtifactDownload?.(message.bundleId, key)
  : onCcArtifactDownload?.(key);
```

**Quote (live Turn projection today):**
```python
bundle_id=bid if bid is not None else 0,
```

**Quote (live hydrate):**
```ts
bundleId: turn.bundle_id,
```

**Why defect:** Live CC turns get `bundle_id: null` from WS → `bundleId` null → CC path works. After reload, CC `chat_log` entries have no `bundle_id` → projection forces **`bundle_id: 0`**. In JavaScript `0 != null` is **true**, so reloaded CC turns route to the native `downloadArtifact(session, 0, key)` path and 404. Task 13 Step 5b (two-turn download after reload) will fail unless caught only on the live (pre-reload) path.

**Fix:** Branch on CC mode or meaningful bundle id, e.g. `(message.bundleId ?? 0) > 0` for native path else CC path; or add `mode?: string` to `Message`, hydrate `turn.mode`, and use `mode === "cc"`. Add Vitest: hydrate CC turn with `bundle_id: 0, mode: "cc", artifacts: [...]` asserts CC download handler fires.

---

### Finding 2A-3 — MEDIUM — Coverage gate references `--cov-fail-under` without pytest-cov in harness

**Location:** Global Constraints “Coverage targets (Phase 2 hardened)”.

**Quote:**
> append `--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95` to each task's verify command

**Why defect:** Root `pyproject.toml` lists `coverage` but not `pytest-cov`. Documented hermetic command uses `uv run --with pytest` only. Executors will hit “ unrecognized arguments: --cov” or silently skip coverage, defeating the ≥95% gate.

**Fix:** Add `--with pytest-cov` (or `--with coverage[toml]` + documented `coverage run` wrapper) to the global hermetic command template and Task 1 verify example.

---

### Finding 2A-4 — MEDIUM — Task 10 paste nests `_iter_file` inside `download_artifact`

**Location:** Task 10 Step 1 code block (`PLAN-3-ui-based-io.md` ~1382–1430).

**Quote:** `def _iter_file(path: Path):` appears indented under `download_artifact` immediately before the `@action` decorator on the same method.

**Why defect:** Copy-paste from the plan yields a syntax/scope error or an unusable nested generator. The plan’s own note (“Add a module-level `_iter_file`…”) contradicts the pasted block.

**Fix:** Replace the Step 1 snippet with a module-level `_iter_file` / `_iter_and_cleanup` (mirror `content_blobs.py:359-371`) and a thin `download_artifact` that calls it.

---

### Finding 2A-5 — MEDIUM — `recover_transcript` under-specifies `cc_session_id` disambiguation

**Location:** Task 10 Step 2; Task 13 Step 5; SPEC-3 §7 unique key `(chat_session, cc_session_id, turn_id)`.

**Quote (Task 10):**
> `# Optional hardening: accept ?cc_session_id= to disambiguate`

**Quote (Task 13 Step 5):**
> `GET …/transcript/<session>/<turn>/?cc_session_id=…`

**Why defect:** Implementation filters only `turn_id` and takes `.first()` by `-created_at`. Task 13 live gate expects `cc_session_id` query param; leaving it optional invites wrong-row recovery if multiple Claude sessions share storage semantics later.

**Fix:** Require `?cc_session_id=` when more than one row matches `(chat_session, turn_id)`; filter ORM on all three key fields per SPEC-3 §7.

---

### Finding 2A-6 — MEDIUM — `test_cc_realstack.py` still asserts `artifacts_published`

**Location:** `test_cc_realstack.py:190-212`; Task 6 Step 6 / Task 8 (Dropbox removal).

**Quote (live test):**
```python
published = data.get("artifacts_published") or []
...
self.assertTrue(published, "copier published nothing")
```

**Why defect:** Plan removes `artifacts_published` from `query_complete` and replaces with `artifacts`. Hermetic realstack / acceptance validator will fail or false-negative after Task 6 unless updated. Plan updates `test_cc_engine_publish.py` but not realstack.

**Fix:** Add Task 6 Step 5c: update `test_cc_realstack.py` and `validate_cc_acceptance.py` to read `data.get("artifacts")` (and/or disk under `output/artifacts/<turn_id>/`).

---

## 2B — Stress (failure modes under load, resume, multi-turn)

### Finding 2B-1 — MEDIUM — `_newest_jsonl_under` by global mtime can bind wrong transcript

**Location:** Task 11 persist block; mirrors `_session_metas` (`cc_assistant.py:92-93`).

**Quote (plan):**
> `jsonl_path = _newest_jsonl_under(Path(dirs.cc_state_mnt) / "projects")`

**Quote (live `_session_metas`):**
```python
jsonls = sorted(store.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
transcript_mount_path = str(jsonls[0]) if jsonls else None
```

**Why defect:** Under `--resume`, one jsonl usually grows monotonically (OK). If multiple `*.jsonl` exist under `projects/` (project slug changes, stale files, parallel experiments), newest mtime may not be the transcript appended **this turn**. Wrong blob → wrong `steps`, wrong `CCSessionTranscript` row; Task 13 Step 6 reload gate may still pass if trace is non-empty but incorrect.

**Fix:** Prefer post-turn selection constrained to the active Claude project dir for `translator.session_id`, or snapshot mtime/size before/after container exit and pick the file that changed; add hermetic unit for two-jsonl directory layout.

---

### Finding 2B-2 — MEDIUM — Task 10 `key=all` zip path leaks temp files

**Location:** Task 10 Step 1 `download_artifact` `key == "all"` branch.

**Quote:**
```python
tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
build_artifact_zip(files, tmp)
resp = StreamingHttpResponse(_iter_file(tmp), ...)
```

**Why defect:** Plan note requires mirroring `content_blobs._iter_and_cleanup` (unlink after stream). Pasted `_iter_file` opens/closes without `finally: os.unlink`. Repeated multi-file downloads accumulate `/tmp` zip debris; disk exhaustion under stress.

**Fix:** Use `_iter_and_cleanup` pattern from `content_blobs.py:359-371`; set `Content-Length` when size known.

---

### Finding 2B-3 — MEDIUM — Task 11 hard fail on missing jsonl may flake on slow filesystems

**Location:** Task 11 “Empty/missing jsonl policy”.

**Quote:**
> `raise RuntimeError("cc persist: missing transcript jsonl after successful turn")`

**Why defect:** Immediately after container stop, cc-state bind mount may not yet reflect final jsonl flush on loaded hosts. Re-raise is correct for gameproofing, but plan has no bounded retry/wait before fail. Task 13 live gate could false-fail intermittently.

**Fix:** Add short bounded retry (e.g. 3× 200ms) on jsonl discovery before raise; log mount path + directory listing on failure for Task 13 transcript.

---

## 2C — Dependencies (cross-step, SPEC drift, downstream gates)

### Finding 2C-1 — MEDIUM — SPEC-3 §3 layout still shows flat `output/artifacts/` (plan is turn-scoped)

**Location:** SPEC-3 §3 vs plan Global Constraints “Turn-scoped artifacts (user decision 2026-06-30)”.

**Quote (SPEC §3):**
```
        ├── artifacts/                NEW: published deliverables
```

**Quote (plan):**
> deliverables land in `output/artifacts/<turn_id>/`; `ArtifactFile.key` is `"<turn_id>/<relpath>"`

**Why defect:** Locked user decision lives only in PLAN-3. Executor reading SPEC alone may publish flat paths, breaking Task 13 Step 5b and turn-scoped download keys.

**Fix:** Amend SPEC-3 §3/§5 to document `<turn_id>/` namespace (plan is authoritative for execution, but SPEC should match to avoid split-brain).

---

### Finding 2C-2 — MEDIUM — Task 11/11a ordering load-bearing but Task 11 Step 1 still invites early `run_cc_turn` kwargs work

**Location:** Global Constraints; Task 11 vs Task 11a.

**Quote (global):**
> **Task 11a MUST complete (commit) before Task 11 Step 2.**

**Why defect:** Global constraint is good (iter-6 fix). Task 11 Step 1 (“Add `TurnCompletePayload` + `_newest_jsonl_under`… extend `run_cc_turn` kwargs”) can still land partial wiring before `_append_cc_turn_complete` exists. Subagent-per-task execution may commit persist calls that no-op or crash.

**Fix:** Split Task 11: Step 1 = helpers only (no `on_turn_complete` call sites); Step 2 blocked behind Task 11a commit with explicit “do not call `on_turn_complete` until 11a merged” checkbox.

---

### Finding 2C-3 — LOW — Step 7 `live_gate_transcript.txt` commit path correctly specified

**Location:** Task 13 Step 8–9.

**Quote:**
> Secret-scan `live_gate_transcript.txt`… **Hard gate for Step 7:** this file **must** be committed

**Why note (positive):** Aligns with user-locked iter-6 decision and SPEC-7 §8 `live_gate_transcript_committed`. No defect; dependency satisfied if Task 13 Step 9 executed literally.

---

## 2D — Gameproof (cheapest fake pass)

### Finding 2D-1 — MEDIUM — Task 10 lists `test_cc_endpoint_guards.py` but defines no failing tests

**Location:** Task 10 Files/Interfaces vs steps (Steps 1–4).

**Quote (Files):**
> Test: `nextseek_api/cc_assistant/tests/test_cc_endpoint_guards.py` (pure owner/key guard helpers)

**Why defect:** No “write failing test → run fail → implement” steps. Executor can paste endpoints without owner/key hermetic guards and rely on Task 13 live gate (expensive, skippable under budget pressure).

**Fix:** Add Task 10 Step 0: pure helpers `resolve_artifact_path(dirs, key, turn_id)` / `assert_owner(session, user)` with parametrize traversal + cross-user negative cases; run before endpoint paste.

---

### Finding 2D-2 — MEDIUM — Task 9b upload list skips explicit fail-first TDD for DRF action

**Location:** Task 9b Steps 1–2.

**Why defect:** Pure helper has fail-first test; `@action upload_list` is pasted in Step 2 with no import/signature guard. Cheapest fake: helper tested, endpoint missing or wrong URL.

**Fix:** Add grep/import guard or minimal source-text assert that `url_path="upload/list"` exists on `CCAssistantViewSet` before Task 9b commit.

---

### Finding 2D-3 — LOW — Task 4 second fixture (Gameability Audit) adequately anti-overfit

**Location:** Task 4 Step 9b.

**Why note (positive):** `cc_transcript_multitool.jsonl` + `test_multitool_trace_kinds()` addresses primary-fixture overfit called out in Gameability table. Adequate if implemented.

---

## Cosmetic notes

- Task 6 contains duplicate “Step 5: Rework `_publish_artifacts`” headings (~836 and ~901); harmless but confusing for subagents.
- Task 12 Step 5 prose says “`bundleId` is null” but reload path uses `0`; wording should match the fix in 2A-2.
- Task 13 Step 1 duplicates `zstandard` add already required in Task 1 Step 5 (idempotent OK).
- Plan file list says `lib/api/chatApi.ts`; live tree uses `lib/services/chatApi.ts` (Task 12 body uses correct path).

---

## Summary counts

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 10 |
| LOW | 3 |

**Top findings (one line each):**

1. **HIGH:** Task 6 pasted `_publish_artifacts` never zip-if-multiple on publish despite SPEC §5 and the task’s own Step 5 prose.
2. **HIGH:** Task 12 download branch uses `bundleId != null` but reloaded CC turns hydrate with `bundle_id: 0`, routing to native bundle download.
3. **MEDIUM:** Global ≥95% coverage gate uses `--cov-fail-under` without documenting `pytest-cov` in the hermetic `uv run` harness.
4. **MEDIUM:** `_newest_jsonl_under` global mtime pick can attach the wrong jsonl when multiple files exist under cc-state.
5. **MEDIUM:** `test_cc_realstack.py` still gates on removed `artifacts_published` field — not updated in plan tasks.

**Verdict:** Plan is substantially improved (turn-scoped artifacts, Task 11a ordering, live transcript commit for Step 7, useChatApi sync id, AppLayout parity called out). Remaining HIGH/MEDIUM defects are fixable without architectural rework. **Not eligible for unconditional acceptance** until 2A-1, 2A-2, and coverage/realstack/zip cleanup are patched in the plan.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
