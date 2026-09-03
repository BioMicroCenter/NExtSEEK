# PLAN-3 Phase 2 Review 3 — Fresh Independent (2026-06-30)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md`  
**Authority:** `SPEC-3-ui-based-io.md` (E1–E10, §6.2 locked), `AGENTS.md`  
**Reviewer:** Independent cold-context iter-3 (no prior review/fix-log reads)

---

## Phase 2 Section Presence (2A gate)

| Required section | Present | Location |
|------------------|---------|----------|
| Permissions Required | Yes | L1614–1630 |
| Risk Register | Yes | L1634–1647 |
| Dependency Validation | Yes | L1651–1661 |
| Gameability Audit | Yes | L1665–1677 |
| Phase 2 Vetting Log | Yes | L1681–1689 |

Phase 2 structural completeness: **PASS**.

---

## 2A — Vet (execution readiness + permissions)

### Finding 2A-1 — HIGH — Task 11 omits `turn_id` / `cc_session_id` contract for transcript persist + recover

**Quote:**  
> `CCSessionTranscript.objects.update_or_create(..., blob=compress(raw), ...).` (Task 11, L1409)  
> `@action(... url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")` (Task 10, L1357–1368)

**Why defect:** Model `unique_together = (chat_session, cc_session_id, turn_id)` (Task 2, L182). Recover endpoint keys on `turn` URL segment. `run_cc_turn` already receives `run_id: str` (`cc_engine.py:405`; wired from `cc_run_id = str(query_task.task_id)` in `cc_assistant.py:177`). Plan never binds `run_id` → `turn_id`, `translator.session_id` → `cc_session_id`, or documents what the frontend/recover URL must pass. Cold implementer will guess; wrong key → Task 13 Step 5 transcript recover fails despite green hermetic suites.

**Fix:** In Task 11 Interfaces, add explicit contract: `turn_id=str(run_id)`, `cc_session_id=translator.session_id`, `chat_session=chat_session`; full `update_or_create` lookup keys + `defaults={blob, uncompressed_size}`; Task 13 Step 5 must recover using the same `turn_id` (document in live gate). Optionally add `turn_id` to `chat_log` entry for deep-link parity.

---

### Finding 2A-2 — HIGH — Task 11a `chat_log` writer uses wrong field name vs live projection

**Quote:**  
> `` `def _append_cc_chat_log(chat_session, *, user_query, reply, ts, artifacts, cc_traces) -> None` `` (Task 11a, L1433)

**Why defect:** `get_session` Turn projection reads `entry.get("assistant_reply")`, not `reply` (`assistant.py:515–518`; canonical shape in `test_assistant_unit.py:837–838`). Implementer following Task 11a literally writes `reply`; reload hydrates empty assistant text (cc_traces/artifacts may still work). Task 13 Step 6 checks panel, not reply body — defect can slip through live gate.

**Fix:** Rename parameter to `assistant_reply`; specify entry dict: `{user_query, assistant_reply, mode: "cc", ts, artifacts, cc_traces}` (no `bundle_id` or explicit `bundle_id: null`). Add grep or unit assertion that CC chat_log entries use `assistant_reply`.

---

### Finding 2A-3 — MEDIUM — Task 11 vs Task 11a contradict E5 mirror requirement

**Quote:**  
> `` Optionally mirrors to `es["cc_traces"]` list ... `` (Task 11, L1408)  
> `` Also mirror append to `extra_state["cc_traces"]` per locked E5 `` (Task 11a, L1426)

**Why defect:** Same persist path described as optional in Task 11 and mandatory in Task 11a. Locked E5 (`SPEC-3 §11`) names `extra_state["cc_traces"]`. Implementer following Task 11 alone may skip the mirror while satisfying reload via `chat_log` — SPEC-noncompliant and inconsistent with Self-Review §6 coverage claim.

**Fix:** Task 11 Step 5: change "Optionally" → "Must (E5)"; single RMW block updating both `chat_log` and `es["cc_traces"]` in one save; cross-ref Task 11a.

---

### Finding 2A-4 — MEDIUM — Task 12 artifact-download wiring contradicts `Message` type (no session on message)

**Quote:**  
> `` : onCcArtifactDownload?.(message /* session */, key); `` (Task 12, L1516)  
> `` The session id is the message's session (authoritative, from Step 1). `` (Task 12, L1519)

**Why defect:** `Message` has no `sessionId` field (`chat.ts:1–11`). Step 1 promotes **chat** session id on the service, not per-message. Implementer may add a nonexistent field or pass `message` object to `downloadCcArtifact(sessionId, key)` incorrectly → CC artifact download 404 in Task 13 Step 5.

**Fix:** Change handler to `onCcArtifactDownload?: (key: string) => void`; parent closes over `service.sessionId` / `serviceRef.current.sessionId`. Remove "message's session" prose.

---

### Finding 2A-5 — MEDIUM — Task 9 references undefined `int_time_unique()`

**Quote:**  
> `` tmp = os.path.join(stage_root, f"{int_time_unique()}_{safe}") `` (Task 9, L1237)  
> `` Add `import os` + a small `int_time_unique()` (or reuse `batch_upload`'s ... idiom) `` (Task 9, L1263)

**Why defect:** No definition, import path, or copy-paste snippet. TDD-first contract expects copy-pasteable steps; this stalls a cold implementer or yields inconsistent staging names vs `batch_upload`.

**Fix:** Inline `f"{int(time.time() * 1000)}_{safe}"` in the snippet or cite exact helper + file from `batch_upload/views.py`.

---

### Finding 2A-6 — LOW — SPEC §12 owner-scoping hermetic tests deferred entirely to Task 13

**Quote:** Global Constraints L21–22; Task 10 "covered by Task 13"; SPEC §12 L399–400 requires owner-scoping at queryset/guard seam.

**Why (non-blocking):** Acknowledged exception pattern, but no pure helper extracted for download/recover guards — regression only caught live.

**Fix:** Extract `_owner_chat_session(user, session_id)` pure validator or document explicit Task 13 negative test (cross-user 404) in live gate transcript.

---

## 2B — Stress Test

### Finding 2B-1 — HIGH — Task 11 persist block is under-specified vs other tasks (Risk Register #1)

**Quote:** Task 11 Steps 1–5 (L1414–1418) — checklist only, no callback payload struct, no `on_turn_complete` signature, no jsonl-read snippet, ellipsis on `update_or_create`.

**Why defect:** Highest-ranked risk (Risk Register L1638) is persist/chat_log failure. Tasks 1–10 include RED/GREEN code; Task 11 is the load-bearing integration seam with the thinnest contract. Mutation: skip `on_turn_complete` call → hermetic suite green, reload empty, live gate fails late.

**Fix:** Add typed callback payload dataclass; paste minimal persist block (jsonl read → extract_trace → emit → callback); require Task 11a complete before Task 11 Step 5 commit.

---

### Finding 2B-2 — MEDIUM — Task 6+8 atomic coupling relies on discipline, not a single task owner

**Quote:**  
> `` Tasks 6 and 8 both touch this handler — land the hybrid split **and** Dropbox removal atomically `` (Task 6, L914)  
> `` Dropbox reply copy in `cc_engine.py` is removed in **Task 6 Step 6** `` (Task 8, L1012)

**Why defect:** Split across two task numbers with separate commits (Task 6 Step 8, Task 8 Step 7). Subagent-driven mode (Global Constraints L13) assigns fresh agent per task — intermediate commit between 6 and 8 can leave `artifacts_published` + dict mismatch (Risk Register #4).

**Fix:** Merge Task 6 Step 6 + Task 8 grep-guard into one task or explicit "do not commit Task 6 until Step 6 Dropbox removal included" with single commit message covering §5+§8.

---

### Finding 2B-3 — MEDIUM — Cumulative session jsonl stored per-turn without slice semantics

**Quote:** Task 11 jsonl: "newest `*.jsonl` under ... projects by mtime" (L1402); model keyed per `(chat_session, cc_session_id, turn_id)` (Task 2).

**Why defect:** Claude Code session jsonl is cumulative across CC turns (1b/1c evidence). Each turn stores full file under distinct `turn_id` — correct for recover, but blob size grows O(turns²) if uncompressed_size not monitored. Not catastrophic for Step 3 scope but worth explicit note in Task 11.

**Fix:** Document that each row stores the full session jsonl snapshot at turn end; set `uncompressed_size=len(raw)`; optional Task 13 assertion on row count == turn count.

---

## 2C — Validate External Dependencies

### Finding 2C-1 — LOW — `zstandard` pinned only by narrative, not version floor

**Quote:** Dependency Validation L1655 — "PyPI ≥0.25"; Task 1 adds bare `zstandard` to pyproject.

**Why (non-blocking):** `stream_reader` bomb guard needs reasonably modern zstandard; no min version in pyproject snippet.

**Fix:** Add `zstandard>=0.25` in Task 1 Step 5 snippet.

---

### Finding 2C-2 — LOW — pydantic unpinned + `_Other.type: str | None` vs SPEC `type: str`

**Quote:** Plan L27–28, Task 4 `_Other`; SPEC §6.3 L237–238 `type: str`.

**Why (non-blocking):** Plan documents intentional tolerance for unparsed lines; aligned with E10 and `parse_transcript` behavior.

**Fix:** None required; note in Self-Review is sufficient.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Task 4 anti-overfit remedy not in commit step

**Quote:**  
> `` Step 9b: Add second fixture ... `cc_transcript_multitool.jsonl` `` (Task 4, L628–630)  
> Step 10 commit adds only `cc_transcript_sample.jsonl` (L634–638)  
> Gameability Audit L1675: "Add second fixture with different tool names"

**Why defect:** Cheapest fake: pass 5 tests on single fixture only; commit command omits second fixture → agent marks Task 4 done without audit remedy.

**Fix:** Include `cc_transcript_multitool.jsonl` + `test_multitool_trace_kinds` in Step 10 `git add` and Expected test count (6 tests).

---

### Finding 2D-2 — MEDIUM — ≥95% coverage target not wired into per-task verify commands

**Quote:** Global Constraints L29 (`--cov-fail-under=95`); Task 1 Step 4 runs pytest without `--cov` (L158).

**Why defect:** Success oracle is "PASS (4 tests)" only. Implementer skips coverage globally; violates Phase 2 hardened coverage claim without failing any task checkbox.

**Fix:** Append `--cov=... --cov-fail-under=95` to verify commands for Tasks 1, 3–7, 9 validator; or add Task 0.5 "coverage gate" step listing all pure modules.

---

### Finding 2D-3 — MEDIUM — Task 9b list endpoint gameable (outline-only)

**Quote:** Task 9b Steps 1–5 (L1290–1294) — no failing test snippet, no commit file list, no hermetic command.

**Why defect:** Cheapest fake: stub `upload_list` returning `{"files":[]}`; Task 13 Step 4 is only check. SPEC §4 "upload + list" lifecycle unverified until live.

**Fix:** Mirror Task 9 validator pattern: pure `list_input_files` test + explicit Step 2 code block + Task 13 Step 4 must assert non-empty list after upload.

---

### Finding 2D-4 — MEDIUM — Task 11 failure policy allows silent degrade

**Quote:** `` production may degrade only if explicitly signed off `` (Task 11, L1410)

**Why defect:** Swallowed persist exception → live WS shows `cc_traces`, reload empty; Task 13 Step 6 is the only hard gate. Gameability Audit L1671 mentions re-raise but Task 11 leaves production escape hatch without requiring evidence if used.

**Fix:** Default: always re-raise until Task 13 pass; remove production degrade branch or require signed-off evidence file in `evidence/3-ui-based-io-live/`.

---

## Severity Counts

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 3 |
| MEDIUM | 10 |
| LOW | 3 |

---

## Top Findings (priority order)

1. **2A-1 (HIGH)** — Bind `run_id`/`translator.session_id` to `CCSessionTranscript` + recover URL; full `update_or_create` contract missing.
2. **2A-2 (HIGH)** — Task 11a must write `assistant_reply` + `mode: "cc"`, not `reply`.
3. **2B-1 (HIGH)** — Task 11 persist integration under-specified; highest risk with thinnest steps.
4. **2A-3 (MEDIUM)** — Resolve Task 11 optional vs Task 11a mandatory `extra_state["cc_traces"]` mirror (E5).
5. **2A-4 (MEDIUM)** — Fix Task 12 CC artifact download to use service session id, not `Message`.

---

## Verdict Rationale

Prior iter-1/2 critical holes (Celery registration, `run_cc_turn` persist site, Task 11a existence, translator class, 6+8 atomic intent) are **addressed in plan text**. Remaining defects are **execution-contract gaps** on the highest-risk persist/transcript/reload path and **gameproof wiring** (coverage commands, second fixture commit, Task 9b outline). These are sufficient to stall or game a cold implementer and can fail Task 13 sub-steps without failing hermetic tasks.

**Not UNCONDITIONAL_ACCEPTANCE** — 3 HIGH + 10 MEDIUM substantive findings remain.

**Recommended verdict:** CONDITIONAL_ACCEPTANCE — harden Task 11/11a/12/4/9b per fixes above, then fresh iter-4 re-vet.
