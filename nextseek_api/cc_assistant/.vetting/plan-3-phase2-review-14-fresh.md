# PLAN-3 Phase 2 Review — Iteration 14 (Fresh, Cold Context)

**Target:** `nextseek_api/cc_assistant/PLAN-3-ui-based-io.md`  
**Locked design:** `SPEC-3-ui-based-io.md`  
**Reviewer:** Independent adversarial pre-execution review (iter 14)  
**Date:** 2026-06-30  

---

## Executive summary

PLAN-3 is substantially hardened after 13 prior vet rounds: Task 11/11a ordering, turn-scoped artifacts, Celery registration, live-gate evidence commit, and PLAN-7 deploy sequencing are coherent. Two **HIGH** frontend contract gaps remain that would let an implementer pass hermetic backend tests while shipping broken CC artifact download / activity behavior after session reload. Several **MEDIUM** gaps around coverage enforcement, DEPLOY note timing, and prop/type wiring remain gameable.

**Verdict basis:** 0 CRITICAL, 2 HIGH, 8 MEDIUM → **CONDITIONAL_ACCEPTANCE**

---

## 2A — Vet (execution snags & permissions)

### HIGH

| ID | Location + quote | Why | Fix |
|----|------------------|-----|-----|
| 2A-H1 | Task 12 Interfaces + Step 5: add ``Step`` to ``lib/types/chat.ts``; Step 5 uses ``message.mode === "cc"`` | ``chat.ts`` already exports ``Step`` for ``ProcessingStepper`` (``index/label/agentName/status``). Adding a second ``Step`` for CCTrace is a **name collision** — ``tsc -b`` fails or the implementer must guess a rename. Separately, ``Message`` has **no** ``mode`` field and ``hydrateFromTurns`` (``useMessages.ts:80-90``) never maps ``turn.mode``, so after reload ``message.mode === "cc"`` is always falsy and CC turns route artifact clicks through ``onArtifactDownload(message.bundleId!, key)`` with ``bundle_id: 0`` → broken downloads despite Task 13 Step 5b passing on live-first-load. | Rename trace step type to ``CCTraceStep`` (or ``CCActivityStep``). Add ``mode?: string`` to ``Message``; map ``mode: turn.mode`` in ``hydrateFromTurns``; set ``mode: "cc"`` on live ``query_complete`` patches in both layouts. |

### MEDIUM

| ID | Location + quote | Why | Fix |
|----|------------------|-----|-----|
| 2A-M1 | Global Constraints L30–31 vs Task verify commands (Tasks 1, 3–5, 7, 9, 9b) | Declares **≥95% line coverage** on pure modules with ``--cov-fail-under=95``, but only Task 6 Step 5b embeds ``--cov=…`` in its run command. Other tasks' "Expected: PASS" lines omit coverage — implementer can skip the floor. | Append module-specific ``--cov=nextseek_api.cc_assistant.<module> --cov-fail-under=95`` to every listed task's Step 4/verify command (mirror Task 6 Step 5b). |
| 2A-M2 | Task 12 Step 5 + MessageBubble/ChatPanel | Plan adds ``onCcArtifactDownload`` usage in ``MessageBubble`` but does not specify updated props on ``MessageBubble``, ``MessageList``, and ``ChatPanel`` (still typed as ``onArtifactDownload?: (bundleId, key) => void`` only). ``AppLayout`` currently passes **no** ``onArtifactDownload`` to ``ChatPanel`` at all (``AppLayout.tsx:190-196``). Checklist mentions wiring but not the signature change or AppLayout native handler. | Add explicit interface deltas for all three components; require AppLayout to wire both ``onArtifactDownload`` (native) and ``onCcArtifactDownload`` (CC) through ``ChatPanel``; add Vitest asserting CC branch fires after ``hydrateFromTurns``. |
| 2A-M3 | Task 11a paste L1759: ``session.save(update_fields=["extra_state"])`` vs Global Constraints L25 | Canonical extra_state RMW requires ``update_fields=["extra_state", "updated_at"]``. Plan contradicts itself — implementer following 11a paste skips ``updated_at``. | Change 11a paste to ``update_fields=["extra_state", "updated_at"]``. |
| 2A-M4 | Task 13 Step 3b L1926: "Before Step 9 commit, append …" | Step lives in **Task 13** deploy; "Step 9" is upload implementation — wrong gate. Implementer may append DEPLOY notes at the wrong time or skip until deploy. | Replace with "Before Task 13 Step 3 (snapshot + deploy), append …" (consistent with PLAN-7 merge-order note in same step). |
| 2A-M5 | Task 11 Interfaces §5 L1593–1600 vs Task 11a ``_append_cc_turn_complete`` | Interface bullet lists inline ``CCSessionTranscript.objects.update_or_create`` **and** delegates persist to ``on_turn_complete``, which **also** upserts in 11a. Lazy implementer may double-write or diverge keys. | Remove the standalone upsert from Task 11 interface prose; state single owner: ``_append_cc_turn_complete`` only. |

### Permissions catalogue (Task 13 live gate assumes all prior tasks merged)

| Resource | Tasks | Notes |
|----------|-------|-------|
| Hermetic ``uv run pytest`` | 1–7, 9, 9b, guards | No DB CREATE on dev box |
| ``makemigrations`` (no migrate until 13) | 2 | Model shape only |
| Celery broker + ``batch_upload`` queue + worker import | 9, 13 Step 4 | ``cc_assistant.upload`` must register |
| ``MEDIA_ROOT`` staging write | 9 | Temp files before Celery move |
| Host/mount FS: ``input_mnt``, ``output/artifacts``, ``cc-state`` jsonl | 3, 9, 10, 11 | Django container mount paths |
| Django ORM + migrate 0007 | 2, 11, 13 | ``CCSessionTranscript``, ``chat_log`` |
| DRF owner-scoped endpoints | 9, 9b, 10 | SEEK session auth |
| Docker rebuild/recreate + ``rollback.sh`` | 13 | Per-change sign-off |
| Playwright forced-CC (≤ $2) | 13 | Upload, split, reload, 3e, 1b/1c regression |
| ``npm run test`` + ``build:embedded`` | 12, 13 | Vitest + static bundles |
| Git on ``cc-step3-ui-io`` | all | Evidence commit required for Step 7 gate |

---

## 2B — Stress test

### Most likely failure mode
**Celery upload 202 without worker task** (Task 9) — mitigated by Step 3b import + Task 13 inspect grep; still the #1 operational miss if deploy skips worker restart.

### Most catastrophic failure mode
**Owner-scoping bypass on artifact/transcript download** (Task 10) — cross-user data leak. Plan's guards are directionally correct; live proof only in Task 13.

### Hidden dependencies
- ``chat_log`` writer (Task 11a) is load-bearing for reload — documented, but easy to execute Task 11 Step 2 early despite warnings.
- ``ProcessingStepper`` ``Step`` type vs trace ``Step`` — not documented as conflict (2A-H1).
- ``register_job(..., project_id=project.id)`` passes ``str`` SEEK ids (and ``personal-*`` slugs) into an API typed ``int`` — JSON storage tolerates it; personal namespace jobs may not match batch_upload semantics (LOW).

### Ambiguous success conditions
- Task 13 Step 6 allows JSON excerpt proof — good hardening vs prose-only.
- Task 4 "full suite PASS" for 1c byte-identical ``_tool_use_line`` — no explicit golden-string test; drift possible if summary tests don't cover all tool kinds.

### Coverage risk
Declared ≥95% is **non-trivial** for ``cc_trace.extract_trace`` branch coverage; without per-task ``--cov`` commands (2A-M1), floor is unenforced.

### Rollback
- **Pause and ask:** persist/reload failures, Celery not registered, migration not applied.
- **Revert:** atomic Task 6+8 ``cc_engine`` handler; ``rollback.sh`` for deploy; do not flip tracker without committed ``live_gate_transcript.txt``.

---

## 2C — Validate external dependencies

| Dependency | Plan claim | Validation | Risk |
|------------|------------|------------|------|
| ``zstandard>=0.25`` | Task 1 + image | Not in ``pyproject.toml`` / ``requirements.txt`` today; PyPI package active; ``stream_reader`` supports bounded reads | OK once Task 1 lands |
| pydantic v2 unpinned | Ordered ``Union``, ``_Other`` last | Matches ``parse_transcript`` unparsed lines ``{"_type":"unparsed"}`` | OK |
| Celery ``batch_upload.celery_app`` | Task 9 pattern | ``celery_app.py:54`` already imports ``cc_sweep``; route ``cc_assistant.*`` exists L37 | OK with explicit upload import |
| ``npm run build:embedded`` | Task 13 | ``package.json`` script confirmed | OK |
| Migration 0007 | Depends ``0006_merge_extra_state_guards`` | ``0006`` exists; no ``0007`` yet | OK |
| Vitest | Task 12 | ``vitest run`` in ``package.json`` | OK |
| PLAN-7 ``step3_deploy_gate`` | Task 13 Step 9 evidence commit | Aligns with sibling SPEC-7 §8 / PLAN-7 Task 1 | OK — no PLAN-7 edits proposed |

---

## 2D — Gameproof

Ranked by ease × intent loss:

### 1. Task 12 reload artifact download (HIGH — see 2A-H1)

**Success condition (Task 12 Step 5):** ``message.mode === "cc"`` → ``onCcArtifactDownload?.(key)``.

**Cheapest fake:** Implement branch in ``MessageBubble`` only; skip ``Message.mode`` + ``hydrateFromTurns`` mapping. Live-first-turn works via WS ``updateLastAssistantMessage``; reload breaks silently.

**No-op test:** Current plan Vitest does not assert post-hydrate CC download path.

**Remedy:** Require ``hydrateFromTurns`` test with ``mode: "cc", bundle_id: 0, artifacts: [...]`` → CC handler called.

### 2. Coverage floor (MEDIUM — 2A-M1)

**Success condition:** "Pure modules … require ≥95% line coverage."

**Cheapest fake:** Run pytest without ``--cov`` on Tasks 1, 3–5, 7, 9, 9b.

**Remedy:** Embed ``--cov-fail-under=95`` in each task verify command.

### 3. Task 9 Celery body (MEDIUM — partially remediated)

**Success condition:** Validator tests pass.

**Cheapest fake:** Pass validator only; omit ``celery_app.py`` import (plan warns; Task 13 Step 4 catches).

**Remedy:** Already in plan Step 3b + live gate — keep.

### 4. Task 4 ``classify_tool_use`` / 1c drift (MEDIUM)

**Success condition:** "_tool_use_line output MUST stay byte-identical."

**Cheapest fake:** Refactor strings subtly; full suite still passes if no golden tests on rendered lines.

**Remedy:** Add ``test_tool_use_line_golden_strings`` pinning one line per tool kind before/after refactor.

### 5. Task 11 persist (MEDIUM — partially remediated)

**Success condition:** Task 13 reload shows non-empty ``cc_traces``.

**Cheapest fake:** Emit ``cc_traces`` on WS only; skip ``chat_log`` (blocked if 11a order obeyed).

**Remedy:** Task 11a order + Task 13 Step 6 JSON excerpt — keep.

### 6. Task 8 grep-guard (LOW)

**Cheapest fake:** Move "Dropbox" string to comment outside grep paths — plan scans ``cc_engine.py`` + ``services/cc_assistant.py`` only.

**Remedy:** Extend guard to ``nextseek_api/cc_assistant/*.py`` or whole-package string scan.

---

## Cosmetic / non-blocking

- SPEC §6.5 per-step ``line`` deep-link to transcript recover endpoint — plan mentions but Task 12 panel test does not require link UI.
- Task 13 Step 1 re-adds ``zstandard`` to image after Task 1 already adds to ``pyproject.toml`` — redundant, not harmful.
- ``QueryCompleteData.bundle_id: number`` vs CC ``null`` — pre-existing TS looseness.
- ``Step`` processing UI vs trace naming confusion in prose (fixed by rename recommendation).
- Task 4 second fixture anti-overfit — good; could add third fixture with only ``_type: unparsed`` lines.

---

## Counts

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 8 |
| LOW | 3 (cosmetic register_job typing, grep scope, redundant zstd step) |

---

## Top findings (priority order)

1. **HIGH — ``Step`` type collision + missing ``Message.mode`` / hydrate mapping** breaks CC artifact download after reload (Task 12).
2. **HIGH — Same root cause:** ``message.mode === "cc"`` gameable without type + hydration contract.
3. **MEDIUM — ≥95% coverage declared but not enforced** in most task verify commands.
4. **MEDIUM — Task 13 Step 3b "Before Step 9 commit"** is wrong sequencing for DEPLOY.md append.
5. **MEDIUM — ChatPanel prop chain / AppLayout wiring** for ``onCcArtifactDownload`` underspecified vs EmbeddedApp parity.

---

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
