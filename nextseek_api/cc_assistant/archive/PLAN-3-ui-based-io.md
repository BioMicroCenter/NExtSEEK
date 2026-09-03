# Step 3 — UI-based I/O Implementation Plan

> **ONBOARD / TRUE STATE — updated 2026-07-01 (post-merge + redeploy, live-verified).**
> **EXECUTED, merged to the integration branch, and live.** Implemented via
> `superpowers:subagent-driven-development` on `cc-step3-ui-io`; Phase 2 vetting =
> UNCONDITIONAL_ACCEPTANCE @ iter-23 (fresh); tracker Step 3 (3a–3e) = done.
> - **Integration branch:** `feat/dmac-assistant-full-integration` fast-forwarded to
>   `c87c874` and **pushed to origin** — GitHub now carries Steps 1–3 (the prerequisite
>   for the Step-7 off-server greenfield verify). Secret scan PASS before push.
> - **Live deploy:** `nextseek` container rebuilt from `feat@c87c874` (image `ad61f29`)
>   via the service-account clone + `collectstatic`; gunicorn+celery, migration `0007`
>   applied, `cc_assistant.upload` registered, HTTP 200.
> - **Live gate re-run on the feat build (2026-07-01):** step3 UI upload / download /
>   activity panel / reload / transcript recover ALL PASS + 1b resume (BANANA-42→84)
>   VERIFIED. Real spend $0.33 (`cc_trace.cost_usd`), max turn $0.19, under the $2/turn cap.
> - **The task checkboxes below were NOT tick-tracked during execution** — source of truth
>   is the git history + `integration-plan.json` + `evidence/3-ui-based-io-live/`.
> - **STALE (do not act on):** the "Phase 3 (task-spec writing) is gated" footer — execution
>   ran directly via subagent-driven-development, so ultraplan Phase-3 task-spec explosion
>   did not run for this plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vestigial Dropbox-shaped I/O with real UI-based I/O — per-user uploads into `input/`, a hybrid artifacts/raw output split with in-UI download, a reload-surviving CC activity panel, a zstd-compressed full-transcript record in the DB, removal of the Dropbox copy/laptop path, and the deferred authoritative-session-id fix.

**Architecture:** New host-side pure modules (`cc_transcript_store` for zstd, `cc_trace` for jsonl→trace extraction, `cc_artifacts` for the output partition + zip) keep logic hermetically testable. The existing `_publish_artifacts` (already nested per Step 2) is reworked to split `scratch/raw/` from deliverables; `translate._handle_result` is extended to surface `num_turns`/`duration_ms`; the per-turn `CCTrace` is persisted to `ChatSession.extra_state["cc_traces"]` and the full jsonl to a new `CCSessionTranscript` table. Four new DRF `@action`s on `CCAssistantViewSet` (upload, upload-status, artifact-download, transcript-recover) mirror the established `batch_upload`/SOP-download patterns, all owner-scoped. The frontend gains a file-attach control, a CC activity panel extending "Search Details", a null-bundle artifact download branch, and the HTTP-202-body session-id promotion.

**Tech Stack:** Python 3.12, Django + DRF (host process), Celery (`batch_upload` queue pattern), docker-py (CC sibling), pydantic v2 + orjson (trace), zstandard (transcript), Vite + React + TypeScript + Vitest (frontend), pytest hermetic units via `uv run`.

## Global Constraints

- **Execution mode (locked 2026-06-30):** **subagent-driven** (superpowers:subagent-driven-development) — fresh subagent per task, two-stage review (spec + quality) between tasks, as Steps 1b/2 were run.
- **Branch (locked 2026-06-30):** implement on a NEW child branch **`cc-step3-ui-io`** off `feat/dmac-assistant-full-integration` (mirroring `cc-step2-multi-user-provisioning`); merge the child back on completion. SPEC-3/PLAN-3 already live on the parent `feat/...` branch.
- **Spec of record:** `nextseek_api/cc_assistant/archive/SPEC-3-ui-based-io.md` (locked decisions **E1–E10** in §11). Every task below traces to a spec section. The §6.2 trace schema is **LOCKED (enriched) 2026-06-30** — implement it verbatim (Task 4).
- **Builds on Step 2** (`done`, live): project-stratified `<DMAC_USER_ROOT>/<projectID-slug>/<user>/{input,scratch,cc-state,output}` + `<project>/shared/`. Reuse the Step-2 primitives `resolve_user_project`, `build_user_dirs`, `UserDirs`, `ProjectIdentity`, and the validators `_validate_user_id`/`_validate_project`/`_safe_relpath` — do not reinvent them.
- **TDD-first**, bite-sized steps, frequent commits. Implementation code only after a failing test.
- **Hermetic test command (the box cannot run the Django test-DB runner — `seek_db_user` lacks `CREATE`):**
  `uv run --no-project --with pytest --with pytest-cov --with orjson --with pydantic --with zstandard python -m pytest -q --noconftest <files>`
  Run from `/home/taishajo/work/NExtSEEK`. No Docker, no DB, no network, no spend. (`--with zstandard` matters only for Task 1; `--with orjson --with pydantic` for the trace tests; harmless elsewhere.)
- **DB / migration / endpoint logic is NOT hermetically testable here** (no test DB, no live HTTP in-suite). For those tasks the hermetic test covers the pure seam (validator, partition, zip-builder, compress round-trip, schema extraction); the endpoint/persistence path is proven in the Task 13 live gate (forced-CC, ≤ $2 cap, Playwright, per-change sign-off).
- **No regression** to 1b `--resume`, 1c memory, the NS route, Step 2 isolation, or **OI-3** (zero-creds agent). Upload/download/recover all run host-side in Django as the logged-in user using their own SEEK login; **no new credential reaches the agent**. The agent still sees only `input/` + `shared/` RO and writes only `scratch/` RW.
- **Owner-scoping is mandatory** on every new read endpoint: `ChatSession.objects.filter(user=request.user)` — a user can never fetch another user's artifacts or transcript.
- **Validate-before-interpolate:** every path segment that reaches a filesystem path (uploaded filename, `key`, `session`, `turn`) MUST be validated (`_safe_relpath` / basename / the Step-2 segment validators) before use.
- **`extra_state` write pattern (canonical, `services/cc_assistant.py:65-72`):** `es = dict(sess.extra_state or {})`; mutate `es`; `sess.extra_state = es`; `sess.save(update_fields=["extra_state", "updated_at"])`. Never mutate `sess.extra_state` in place.
- **pydantic is unpinned** in this repo (v2 syntax throughout: `model_validate`/`model_dump`). Use an **ordered `Union` with a catch-all last** for the jsonl record union (do not rely on a discriminated union requiring a specific pin).
- **Per-change sign-off** before touching the running instance. The E8 Dropbox-default change and any dead-config removal ship as their own reviewed diffs.
- **Deadline:** NExtSEEK prod before 2026-07-14.
- **Turn-scoped artifacts (user decision 2026-06-30):** deliverables land in `output/artifacts/<turn_id>/`; `ArtifactFile.key` is `"<turn_id>/<relpath>"`. Task 13 Step 5b proves two-turn same-basename download correctness.
- **Coverage targets (Phase 2 hardened):** Each task below is gated at **≥95%** line coverage, wired **into that task's actual verify command** as `--with pytest-cov … --cov=<module> --cov-fail-under=95` (Task 6 Step 5b is the template; commit blocked if below floor). The gated `<module>` per task is: Task 1 → `nextseek_api.cc_assistant.cc_transcript_store`; Task 3 → `nextseek_api.cc_assistant.cc_provision` (the command must run **all four** existing provision test files so the shared Step-2 module reaches the floor — measured **96%**); Task 4 → `nextseek_api.cc_assistant.cc_trace`; Task 6 → `nextseek_api.cc_assistant.cc_artifacts`; Task 7 → `nextseek_api.assistant.models_api` (declarative pydantic — **≥96% on import**, exercised by `test_turn_cc_traces.py`); Task 9 → `nextseek_api.cc_assistant.cc_upload_validate` (the **celery-free** validator module split out this task); Task 9b → `nextseek_api.cc_assistant.cc_upload_list`. **Task 5 (translate) is NOT whole-module gated:** `translate.py` is a pre-existing shared *procedural* module that Task 5 extends by two `query_complete` keys — it adds **no new module**, and its uncovered lines (verified `58, 68, 97, 104, 123`) all live in `handle`/`finalize`/`_handle_system`/`_handle_assistant`/`_handle_user` branches **outside** the touched `_handle_result`, so a whole-module ≥95% gate is unreachable within this task's surgical scope (mixing the existing `from translate import …` and the dotted-import seam test also collapses coverage to "no data"). Task 5 instead runs `--cov=nextseek_api.cc_assistant.translate --cov-report=term-missing` (no `--cov-fail-under`) to surface the seam; the `_handle_result` change is proven by the two new assertions in Step 1 and the Task 13 live gate.
- **Task execution order (load-bearing):** **Task 11a MUST complete (commit) before Task 11 Step 2.** Do not implement Task 11 persist wiring until `_append_cc_turn_complete` exists.
- **`zstandard` dependency:** add to root `pyproject.toml` / image deps in **Task 1** (not deferred to Task 13) so production imports succeed after Task 11.

---

## File Structure

**Create**
- `nextseek_api/cc_assistant/cc_transcript_store.py` — `compress(jsonl: bytes) -> bytes` / `decompress(blob: bytes, *, max_bytes) -> bytes` (zstd; decompression-bomb bound).
- `nextseek_api/cc_assistant/cc_trace.py` — one flat pydantic `Step` (real `kind`) + `CCTrace` (enriched §6.2, with the `SessionSummary`-style envelope) + the ordered jsonl record union (`_Assistant`/`_User`/`_Other`, `_Other` last, §6.3) + `extract_trace(parsed, *, cc_session_id, ts, files_created, files_modified, result_meta) -> CCTrace`. Reuses the shared `cc_summary.classify_tool_use` + `ParsedTranscript` counts.
- `nextseek_api/cc_assistant/cc_artifacts.py` — `partition_changed(changed: set[str]) -> tuple[set[str], set[str]]` (artifacts vs raw) + `build_artifact_zip(files: list[Path], dest_zip: Path) -> Path` (mirrors `content_blobs.download_batch`).
- `nextseek_api/cc_assistant/cc_turn_complete.py` — `TurnCompletePayload` + `serialize_cc_chat_log_entry` + `append_capped` (the pure FIFO-cap helper; neutral module; Task 11/11a).
- `nextseek_api/cc_assistant/cc_endpoint_guards.py` — owner-scoped artifact path resolution (Task 10).
- `nextseek_api/cc_assistant/cc_upload_validate.py` — `validate_upload_filename` (celery-free pure module so the hermetic validator test imports with zero celery dependency; Task 9).
- `nextseek_api/cc_assistant/cc_upload_list.py` — `list_input_files` helper (Task 9b).
- Tests under `nextseek_api/cc_assistant/tests/`: `test_cc_transcript_store.py`, `test_cc_summary_classify.py`, `test_cc_trace.py`, `test_translate_result_meta.py`, `test_cc_artifacts_split.py`, `test_cc_provision_input_mnt.py`, `test_turn_cc_traces.py`, `test_cc_upload_validate.py`, `test_cc_dropbox_grep_guard.py`, `test_cc_newest_jsonl.py`, `test_cc_chat_log_writer.py`, `test_cc_endpoint_guards.py`, `test_cc_upload_list.py`, `test_validate_cc_acceptance.py`, plus the fixture `tests/fixtures/cc_transcript_sample.jsonl`.
- `nextseek_api/migrations/0007_ccsessiontranscript.py` — additive migration.
- Frontend: `chat_frontend/src/components/ChatPanel/CCActivityPanel.tsx` (+ `CCActivityPanel.test.tsx`), `chat_frontend/src/components/ChatPanel/UploadControl.tsx` (+ `UploadControl.test.tsx`).

**Modify**
- `nextseek_api/cc_assistant/cc_summary.py` — factor a structured `classify_tool_use(block) -> (kind, tool, detail)` out of the existing `_tool_use_line` (`:87`) and have both the string formatter and `cc_trace` consume it (shared classifier; no behavior change to 1c memory).
- `nextseek_api/cc_assistant/cc_provision.py` — add `input_mnt` to `UserDirs` + `build_user_dirs` (uploads write host-side via the mount).
- `nextseek_api/cc_assistant/translate.py` — `_handle_result` surfaces `num_turns`/`duration_ms` (`:130-156`).
- `nextseek_api/cc_assistant/cc_engine.py` — rework `_publish_artifacts` (`:639`) to the hybrid split; replace the "Saved to your Dropbox" augmentation (`:580-587`) with an `artifacts` channel + trace metadata on `query_complete`; extend `run_cc_turn` kwargs (`chat_session`, `user_query`, `on_turn_complete`).
- `nextseek_api/cc_assistant/cc_config.py` — neutral default `/srv/dmac/users` (`:15`, E8).
- `nextseek_api/services/cc_assistant.py` — four new `@action`s (upload, upload-status, artifact-download, transcript-recover) on `CCAssistantViewSet` (`:114`); persist `CCTrace` + transcript blob in the CC branch.
- `nextseek_api/assistant/models_db.py` — `CCSessionTranscript` model (`app_label='nextseek_api'`).
- `nextseek_api/assistant/models_api.py` — add `cc_traces` to `Turn` (`:122-138`, `extra="forbid"` ⇒ must be declared).
- `nextseek_api/services/assistant.py` — surface `cc_traces` in the Turn projection (`:521-529`).
- `nextseek_api/batch_upload/tasks.py` *(pattern source only — read, do not edit)*; new CC upload task lives in `nextseek_api/cc_assistant/cc_upload_tasks.py`.
- `seek/views.py` — remove dead `DROPBOX_DIRECTORY` (`:94`, audited).
- Frontend: `MessageInput.tsx`, `MessageBubble.tsx`, `EmbeddedApp.tsx`, `AppLayout.tsx`, `lib/services/chatApi.ts`, `lib/types/chat.ts`, `lib/types/api.ts`, `hooks/useMessages.ts`, `hooks/useChatApi.ts`, `components/ChatPanel/ReportArtifacts.tsx`.
- `nextseek_api/cc_assistant/DEPLOY.md`, image deps (`zstandard`).

---

### Task 1: `cc_transcript_store` — zstd compress/decompress (bomb-bounded)

**Files:**
- Create: `nextseek_api/cc_assistant/cc_transcript_store.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_transcript_store.py`

**Interfaces:**
- Produces:
  - `compress(jsonl: bytes, *, level: int = 10) -> bytes`
  - `decompress(blob: bytes, *, max_bytes: int = 256 * 1024 * 1024) -> bytes` — raises `TranscriptTooLarge` if the decompressed stream would exceed `max_bytes` (decompression-bomb guard, §10).
  - `class TranscriptTooLarge(Exception)`

Spec refs: §7 (zstd, dedicated store, size bound), E7.

- [ ] **Step 1: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_transcript_store.py
"""Hermetic tests for the zstd transcript store. No DB, no network."""
import pytest

from nextseek_api.cc_assistant.cc_transcript_store import (
    compress, decompress, TranscriptTooLarge,
)


def test_round_trip_is_byte_identical():
    raw = b'{"type":"user"}\n{"type":"assistant"}\n' * 1000
    blob = compress(raw)
    assert isinstance(blob, bytes)
    assert decompress(blob) == raw


def test_compression_actually_shrinks_repetitive_jsonl():
    raw = b'{"type":"assistant","message":{"role":"assistant"}}\n' * 5000
    assert len(compress(raw)) < len(raw)


def test_empty_round_trips():
    assert decompress(compress(b"")) == b""


def test_decompress_bomb_is_bounded():
    raw = b"A" * (2 * 1024 * 1024)          # compresses tiny, expands large
    blob = compress(raw)
    with pytest.raises(TranscriptTooLarge):
        decompress(blob, max_bytes=1024)    # cap below the real size -> reject
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project --with pytest --with zstandard python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_transcript_store.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_transcript_store'`

- [ ] **Step 3: Write the minimal implementation**

```python
# nextseek_api/cc_assistant/cc_transcript_store.py
"""Step 3 — durable full-transcript store (zstd).

The complete Claude Code session ``.jsonl`` is compressed with zstd and held in
the ``CCSessionTranscript`` table so a turn's transcript is recoverable even if
the on-disk ``cc-state`` dir is wiped (SPEC-3 §7, decision E7). Pure byte I/O;
no Django import so it stays hermetically testable.
"""
from __future__ import annotations

import zstandard


class TranscriptTooLarge(Exception):
    """Decompressed output would exceed the configured bound (bomb guard)."""


def compress(jsonl: bytes, *, level: int = 10) -> bytes:
    """zstd-compress raw jsonl bytes. ``level`` 10 is a fast/good default."""
    return zstandard.ZstdCompressor(level=level).compress(jsonl)


def decompress(blob: bytes, *, max_bytes: int = 256 * 1024 * 1024) -> bytes:
    """Inverse of ``compress``. Streams with a hard output cap so a corrupted or
    hostile row cannot exhaust memory (SPEC-3 §10)."""
    dctx = zstandard.ZstdDecompressor()
    out = bytearray()
    with dctx.stream_reader(blob) as reader:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            out.extend(chunk)
            if len(out) > max_bytes:
                raise TranscriptTooLarge(f"transcript exceeds {max_bytes} bytes")
    return bytes(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-project --with pytest --with pytest-cov --with zstandard python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_transcript_store.py --cov=nextseek_api.cc_assistant.cc_transcript_store --cov-fail-under=95`
Expected: PASS (4 tests), `cc_transcript_store` ≥95% line coverage (commit blocked below floor).

- [ ] **Step 5: Add `zstandard` to root Python deps (E7 — early, not Task 13-only)**

In `pyproject.toml` (and `requirements.txt` if the image installs from it), add `zstandard>=0.25`. The dmac venv / image must get it before Task 11 imports `cc_transcript_store` in production.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/cc_transcript_store.py nextseek_api/cc_assistant/tests/test_cc_transcript_store.py pyproject.toml requirements.txt
git commit -m "feat(cc-step3): zstd transcript store + zstandard dep (§7, E7)"
```

---

### Task 2: `CCSessionTranscript` model + migration

**Files:**
- Modify: `nextseek_api/assistant/models_db.py`
- Create: `nextseek_api/migrations/0007_ccsessiontranscript.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_transcript_store.py` (extend with a model-shape import guard that does not need the DB)

**Interfaces:**
- Produces: `CCSessionTranscript(models.Model)` with fields `chat_session` (FK → `ChatSession`), `cc_session_id: CharField`, `turn_id: CharField`, `blob: BinaryField`, `uncompressed_size: BigIntegerField`, `created_at: DateTimeField(auto_now_add=True)`; `db_table = "assistant_cc_transcript"`, `app_label = "nextseek_api"`, `unique_together = (("chat_session", "cc_session_id", "turn_id"),)`.

Spec refs: §7 (dedicated table, on-demand load, not `extra_state`), E6.

- [ ] **Step 1: Write the failing model-shape guard**

Append to `nextseek_api/cc_assistant/tests/test_cc_transcript_store.py`:

```python
def test_ccsessiontranscript_model_shape():
    """Field set + db_table guard — does not touch the DB (no migrate/connect)."""
    import django
    from django.conf import settings
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=["nextseek_api"], DATABASES={}, USE_TZ=True,
        )
        django.setup()
    from nextseek_api.assistant.models_db import CCSessionTranscript
    names = {f.name for f in CCSessionTranscript._meta.get_fields()}
    assert {"chat_session", "cc_session_id", "turn_id", "blob",
            "uncompressed_size", "created_at"} <= names
    assert CCSessionTranscript._meta.db_table == "assistant_cc_transcript"
```

> If standalone `django.setup()` proves brittle in the hermetic harness, downgrade this to a source-text guard (assert the field names + `db_table` literal appear in `models_db.py`) — the model is authoritatively exercised in the Task 13 live gate.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest "nextseek_api/cc_assistant/tests/test_cc_transcript_store.py::test_ccsessiontranscript_model_shape"`
Expected: FAIL — `ImportError: cannot import name 'CCSessionTranscript'`

- [ ] **Step 3: Add the model**

Append to `nextseek_api/assistant/models_db.py` (mirror the existing `ChatSession` Meta style — `db_table`, `app_label = 'nextseek_api'`):

```python
class CCSessionTranscript(models.Model):
    """Full Claude Code session jsonl, zstd-compressed, per (session, turn).

    Stored in its OWN table (NOT ChatSession.extra_state) so it is loaded only on
    demand and never bloats hot ChatSession reads (SPEC-3 §7, E6)."""

    chat_session = models.ForeignKey(
        "nextseek_api.ChatSession", on_delete=models.CASCADE,
        related_name="cc_transcripts",
    )
    cc_session_id = models.CharField(max_length=128)
    turn_id = models.CharField(max_length=128)
    blob = models.BinaryField()
    uncompressed_size = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assistant_cc_transcript"
        app_label = "nextseek_api"
        unique_together = (("chat_session", "cc_session_id", "turn_id"),)
        ordering = ["-created_at"]
```

- [ ] **Step 4: Generate the migration**

Run (uses the dmac venv python that the app runs under):
```bash
cd /home/taishajo/work/NExtSEEK && python manage.py makemigrations nextseek_api --name ccsessiontranscript
```
Expected: creates `nextseek_api/migrations/0007_ccsessiontranscript.py` with a single `CreateModel`. (`makemigrations` needs no DB CREATE; it only reads models. If it cannot import settings on this box, hand-author the migration mirroring `0002_querytask.py`'s structure with the fields above and `dependencies = [("nextseek_api", "0006_merge_extra_state_guards")]`.)

- [ ] **Step 5: Run the guard to verify it passes**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest "nextseek_api/cc_assistant/tests/test_cc_transcript_store.py"`
Expected: PASS. Do NOT run `migrate` here (no test DB / per-change sign-off) — it applies in Task 13.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/assistant/models_db.py nextseek_api/migrations/0007_ccsessiontranscript.py nextseek_api/cc_assistant/tests/test_cc_transcript_store.py
git commit -m "feat(cc-step3): CCSessionTranscript model + migration (§7, E6)"
```

---

### Task 3: add `input_mnt` to `UserDirs` / `build_user_dirs`

**Why:** uploads are written host-side by Django, which sees the **mount** root, not the host root. `UserDirs` today exposes `input_src` (host bind source) but no `input_mnt`. Add it additively (exactly as Step 2 added fields).

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_provision.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_provision_input_mnt.py`

**Interfaces:**
- Produces: `UserDirs` gains `input_mnt: str`; `build_user_dirs` sets `input_mnt=f"{user_mount}/input"`. All existing fields unchanged.

Spec refs: §4 (upload destination is the user's `input/`), §10 (host-side write).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_cc_provision_input_mnt.py
"""input_mnt is the container-mount path Django writes uploads to. Hermetic."""
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


def _paths() -> CCPaths:
    return CCPaths(host_user_root="/host/users", user_root_mount="/dmac/users")


def test_input_mnt_uses_mount_root_and_matches_input_src_shape():
    d = build_user_dirs(_paths(), "42-px", "alice", session_id="S1")
    assert d.input_mnt == "/dmac/users/42-px/alice/input"
    assert d.input_src == "/host/users/42-px/alice/input"   # unchanged
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_input_mnt.py`
Expected: FAIL — `AttributeError: 'UserDirs' object has no attribute 'input_mnt'`

- [ ] **Step 3: Add the field + builder line**

In `cc_provision.py`, add to the `UserDirs` dataclass body (after `input_src`):

```python
    input_mnt: str
```

In `build_user_dirs`, add to the returned `UserDirs(...)` (after `input_src=...`):

```python
        input_mnt=f"{user_mount}/input",
```

- [ ] **Step 4: Run the focused test + the full suite**

Run (all provision test files so the shared `cc_provision` module reaches the floor — measured 96%): `uv run --no-project --with pytest --with pytest-cov python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_input_mnt.py nextseek_api/cc_assistant/tests/test_cc_provision_paths.py nextseek_api/cc_assistant/tests/test_cc_provision_isolation.py nextseek_api/cc_assistant/tests/test_cc_provision_resolve.py nextseek_api/cc_assistant/tests/test_cc_provision_slug.py --cov=nextseek_api.cc_assistant.cc_provision --cov-fail-under=95`
Expected: PASS, `cc_provision` ≥95% (commit blocked below floor). Then the whole suite to confirm the additive field broke nothing:
`uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: all prior tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/cc_provision.py nextseek_api/cc_assistant/tests/test_cc_provision_input_mnt.py
git commit -m "feat(cc-step3): UserDirs.input_mnt for host-side upload writes (§4)"
```

---

### Task 4: trace schema + jsonl extractor (`cc_trace.py`)

**Files:**
- Create: `nextseek_api/cc_assistant/cc_trace.py`
- Create: `nextseek_api/cc_assistant/tests/fixtures/cc_transcript_sample.jsonl`
- Test: `nextseek_api/cc_assistant/tests/test_cc_trace.py`

**Interfaces:**
- Consumes: `cc_summary.parse_transcript` (existing, `cc_summary.py:46` — returns `ParsedTranscript(raw_lines, records)`; `.line_count`/`.turn_count` properties) and a NEW shared `cc_summary.classify_tool_use` (factored out of `_tool_use_line`, this task).
- Produces (enriched §6.2/§6.3):
  - `cc_summary.classify_tool_use(block: dict) -> tuple[str, str, str | None]` → `(kind, tool, detail)`; `kind ∈ {bash,write,edit,read,skill,tool}`. `_tool_use_line` is rewired to call it (no behavior change to 1c).
  - one flat `Step` (real `kind`, plus `line`/`tool`/`detail`/`text`/`action`/`status`) — NO single-value discriminator.
  - `CCTrace` with the enriched §6.2 envelope (`schema_version`/`transcript_line_count`/`turn_count` + result-frame meta + steps/tools + diff file lists).
  - `RECORDS = TypeAdapter(list[Union[_Assistant, _User, _Other]])` (ordered; `_Other.type` optional so blank/`{"_type":"unparsed"}` lines validate).
  - `extract_trace(parsed, *, cc_session_id, ts, files_created, files_modified, result_meta=None) -> CCTrace`

Spec refs: §6.1 (two sources, do not conflate), §6.2 (enriched schema), §6.3 (shared classifier + ordered union, `_Other` last), E4/E10.

- [ ] **Step 1: Write the failing test for the shared classifier**

```python
# nextseek_api/cc_assistant/tests/test_cc_summary_classify.py
"""classify_tool_use is the single tool classifier shared by 1c summary + trace."""
from nextseek_api.cc_assistant.cc_summary import classify_tool_use


def test_classify_bash_write_edit_read_skill_other():
    assert classify_tool_use({"name": "Bash", "input": {"command": "ls"}}) == ("bash", "Bash", "ls")
    assert classify_tool_use({"name": "Write", "input": {"file_path": "/x.md"}}) == ("write", "Write", "/x.md")
    assert classify_tool_use({"name": "MultiEdit", "input": {"file_path": "/y"}}) == ("edit", "MultiEdit", "/y")
    assert classify_tool_use({"name": "Read", "input": {"file_path": "/z"}}) == ("read", "Read", "/z")
    assert classify_tool_use({"name": "Task", "input": {"subagent_type": "Explore"}}) == ("skill", "Task", "Explore")
    assert classify_tool_use({"name": "WebFetch", "input": {}}) == ("tool", "WebFetch", None)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_summary_classify.py`
Expected: FAIL — `ImportError: cannot import name 'classify_tool_use'`

- [ ] **Step 3: Factor `classify_tool_use` out of `_tool_use_line`**

In `cc_summary.py`, add `classify_tool_use` and rewire `_tool_use_line` (`:87`) to call it (the rendered strings MUST stay byte-identical so 1c summary output does not change):

```python
def classify_tool_use(block: dict) -> tuple[str, str, str | None]:
    """Classify a tool_use content block into (kind, tool, detail). Shared by the
    1c memory summary (_tool_use_line) and the Step-3 trace (cc_trace) so the two
    can never drift. kind in {bash, write, edit, read, skill, tool}."""
    name = block.get("name", "") or ""
    inp = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
    n = name.lower()
    if n == "bash":
        return "bash", name, (str(inp.get("command", "")) or None)
    if n in ("write", "edit", "multiedit", "notebookedit"):
        kind = "write" if n == "write" else "edit"
        return kind, name, (inp.get("file_path") or inp.get("notebook_path") or None)
    if n == "read":
        return "read", name, (inp.get("file_path") or None)
    if n in ("skill", "task"):
        return "skill", name, (inp.get("skill") or inp.get("subagent_type") or name)
    return "tool", name, None


def _tool_use_line(block: dict, truncate_chars: int) -> str | None:
    kind, name, detail = classify_tool_use(block)
    if kind == "bash":
        return f"bash: {_truncate(detail or '', truncate_chars)}"
    if kind in ("write", "edit"):
        return f"{kind}: {detail or ''}"
    if kind == "read":
        return f"read: {detail or ''}"
    if kind == "skill":
        return f"skill: {detail or name}"
    return f"tool[{name}]"
```

- [ ] **Step 4: Run the classifier test + the FULL suite (1c must not regress)**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_summary_classify.py nextseek_api/cc_assistant/tests/`
Expected: PASS (classifier + every existing 1c test — `_tool_use_line`'s output is unchanged).

- [ ] **Step 5: Write the fixture jsonl (with paired tool_results for `status`)**

Create `nextseek_api/cc_assistant/tests/fixtures/cc_transcript_sample.jsonl` containing **EXACTLY these 6 jsonl records and NOTHING ELSE** — **no `#` path/header comment line** (unlike the `.py` pastes in this plan, the leading `# <path>` convention is a *label here, not file content*: `parse_transcript` keeps every non-empty line and maps an unparseable one to `{"_type":"unparsed"}` while still counting it, so a pasted `#` line makes `line_count == 7` and breaks the `transcript_line_count == p.line_count == 6` assertion):

```
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"list the input files"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll inspect the inputs."},{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls /data/input"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":false,"content":"a.csv"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t2","name":"Write","input":{"file_path":"/data/scratch/report.md"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","is_error":true,"content":"disk full"}]}}
{"type":"summary","leafUuid":"abc"}
```

(The **6th** record — the `summary` line — is an unknown record `type` that MUST fall through to `_Other`. The two `tool_result`s drive `status`.)

- [ ] **Step 6: Write the failing extractor tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_trace.py
"""Hermetic trace extraction from a fixture jsonl. orjson + TypeAdapter."""
from pathlib import Path

from nextseek_api.cc_assistant import cc_summary
from nextseek_api.cc_assistant.cc_trace import extract_trace, CCTrace

FIX = Path(__file__).parent / "fixtures" / "cc_transcript_sample.jsonl"


def _parsed():
    return cc_summary.parse_transcript(FIX.read_bytes())


def test_envelope_counts_reuse_parsed_transcript():
    p = _parsed()
    t = extract_trace(p, cc_session_id="sess-1", ts="2026-06-30T00:00:00Z",
                      files_created=["report.md"], files_modified=[])
    assert isinstance(t, CCTrace)
    assert t.schema_version == "3/trace-v1"
    assert t.transcript_line_count == p.line_count == 6
    assert t.turn_count == p.turn_count          # user-role record count (reused)


def test_steps_have_granular_kind_line_and_tools_tally():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=["report.md"], files_modified=[])
    kinds = [s.kind for s in t.steps]
    assert kinds == ["text", "bash", "write"]
    bash = next(s for s in t.steps if s.kind == "bash")
    write = next(s for s in t.steps if s.kind == "write")
    assert (bash.tool, bash.detail, bash.line) == ("Bash", "ls /data/input", 2)
    assert (write.tool, write.detail, write.line) == ("Write", "/data/scratch/report.md", 4)
    assert t.tools_used == {"Bash": 1, "Write": 1}


def test_action_from_diff_and_status_from_tool_result():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=["report.md"], files_modified=[])
    bash = next(s for s in t.steps if s.kind == "bash")
    write = next(s for s in t.steps if s.kind == "write")
    assert write.action == "created"             # report.md is in files_created (basename match)
    assert bash.status == "ok"                    # paired tool_result is_error=false
    assert write.status == "error"                # paired tool_result is_error=true
    # modified-action branch (covers `elif base in modified_base`, else dips <95%):
    t2 = extract_trace(_parsed(), cc_session_id="s", ts="t",
                       files_created=[], files_modified=["report.md"])
    w2 = next(s for s in t2.steps if s.kind == "write")
    assert w2.action == "modified"               # same basename via files_modified


def test_unknown_record_type_does_not_crash():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[])
    assert isinstance(t, CCTrace)                 # the "summary" line tolerated (_Other)


def test_result_meta_is_surfaced_and_distinct_from_turn_count():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[],
                      result_meta={"num_turns": 9, "duration_ms": 1234, "cost_usd": 0.07})
    assert t.num_turns == 9 and t.duration_ms == 1234 and t.cost_usd == 0.07
    assert t.num_turns != t.turn_count            # internal turns != user-message records
```

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_trace.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_trace'`

- [ ] **Step 8: Implement `cc_trace.py`**

```python
# nextseek_api/cc_assistant/cc_trace.py
"""Step 3 — per-turn activity trace (SPEC-3 §6, enriched).

ONE CCTrace == ONE chat turn. Assembled from THREE sources (§6.1), never conflated:
  1. the persisted .jsonl records -> ordered `steps` (kind/tool/detail/line/status) + tools tally,
  2. the headless `result` frame -> num_turns / duration_ms / cost_usd (passed as ``result_meta``),
  3. the §5 scratch diff -> authoritative files_created/modified + per-step `action`.
Reuses cc_summary.classify_tool_use (one shared classifier with 1c memory) and the
ParsedTranscript counts. jsonl validated with an ordered Union (_Other last).
"""
from __future__ import annotations

import os
from typing import Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from . import cc_summary

SCHEMA_VERSION = "3/trace-v1"


class Step(BaseModel):
    line: int
    kind: Literal["bash", "write", "edit", "read", "skill", "tool", "text"]
    tool: str | None = None
    detail: str | None = None
    text: str | None = None
    action: Literal["created", "modified"] | None = None
    status: Literal["ok", "error"] | None = None


class CCTrace(BaseModel):
    schema_version: str = SCHEMA_VERSION
    cc_session_id: str
    ts: str
    transcript_line_count: int = 0
    turn_count: int = 0
    num_turns: int | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    steps: list[Step] = Field(default_factory=list)
    tools_used: dict[str, int] = Field(default_factory=dict)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


# --- jsonl record union (§6.3): ordered, _Other LAST + total (optional type) ---
class _Assistant(BaseModel):
    type: Literal["assistant"]
    message: dict


class _User(BaseModel):
    type: Literal["user"]
    message: dict | None = None


class _Other(BaseModel):
    type: str | None = None   # optional so blank / {"_type":"unparsed"} lines still validate


RECORDS = TypeAdapter(list[Union[_Assistant, _User, _Other]])


def _content_blocks(message) -> list[dict]:
    content = (message or {}).get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def extract_trace(parsed, *, cc_session_id, ts, files_created, files_modified,
                  result_meta=None) -> CCTrace:
    """Build a CCTrace from a ParsedTranscript + the §5 diff lists + headless meta."""
    validated = RECORDS.validate_python([dict(r) for r in parsed.records])
    created_base = {os.path.basename(p) for p in files_created}
    modified_base = {os.path.basename(p) for p in files_modified}

    steps: list[Step] = []
    tools: dict[str, int] = {}
    by_id: dict[str, Step] = {}
    for idx, rec in enumerate(validated, start=1):
        if isinstance(rec, _Assistant):
            for block in _content_blocks(rec.message):
                btype = block.get("type")
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        steps.append(Step(line=idx, kind="text", text=txt))
                elif btype == "tool_use":
                    kind, tool, detail = cc_summary.classify_tool_use(block)
                    action = None
                    if kind in ("write", "edit") and detail:
                        base = os.path.basename(detail)
                        if base in created_base:
                            action = "created"
                        elif base in modified_base:
                            action = "modified"
                    step = Step(line=idx, kind=kind, tool=tool, detail=detail, action=action)
                    steps.append(step)
                    tools[tool] = tools.get(tool, 0) + 1
                    bid = block.get("id")
                    if bid:
                        by_id[bid] = step
        elif isinstance(rec, _User) and rec.message:
            for block in _content_blocks(rec.message):
                if block.get("type") == "tool_result":
                    step = by_id.get(block.get("tool_use_id"))
                    if step is not None:
                        step.status = "error" if block.get("is_error") else "ok"

    meta = result_meta or {}
    return CCTrace(
        cc_session_id=cc_session_id, ts=ts,
        transcript_line_count=parsed.line_count, turn_count=parsed.turn_count,
        num_turns=meta.get("num_turns"), duration_ms=meta.get("duration_ms"),
        cost_usd=meta.get("cost_usd"),
        steps=steps, tools_used=tools,
        files_created=list(files_created), files_modified=list(files_modified),
    )
```

> `Step` is a mutable pydantic model (default) so `step.status` can be set when its paired
> `tool_result` is reached. `action` matches by **basename** because the diff yields scratch
> relpaths while tool args carry container-absolute paths (`/data/scratch/...`).

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run --no-project --with pytest --with pytest-cov --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_trace.py --cov=nextseek_api.cc_assistant.cc_trace --cov-fail-under=95`
Expected: PASS (6 tests), `cc_trace` ≥95% line coverage (commit blocked below floor).

- [ ] **Step 9b: Add second fixture (Gameability Audit — anti-overfit)**

Create `tests/fixtures/cc_transcript_multitool.jsonl` with `WebFetch` + `Read` tools (different from sample) — again **jsonl records only, no `#` header/comment line** (same `line_count` trap as Step 5). Add `test_multitool_trace_kinds()` asserting distinct `kind` values. Prevents extractor overfit to the primary fixture.

- [ ] **Step 10: Commit**

```bash
git add nextseek_api/cc_assistant/cc_summary.py nextseek_api/cc_assistant/cc_trace.py \
        nextseek_api/cc_assistant/tests/test_cc_summary_classify.py \
        nextseek_api/cc_assistant/tests/test_cc_trace.py \
        nextseek_api/cc_assistant/tests/fixtures/cc_transcript_sample.jsonl \
        nextseek_api/cc_assistant/tests/fixtures/cc_transcript_multitool.jsonl
git commit -m "feat(cc-step3): enriched CCTrace + shared cc_summary.classify_tool_use (§6, E4/E10)"
```

---

### Task 5: extend `translate._handle_result` for `num_turns` / `duration_ms`

**Files:**
- Modify: `nextseek_api/cc_assistant/translate.py` (`:130-156`)
- Test: `nextseek_api/cc_assistant/tests/test_translate_result_meta.py`

**Interfaces:**
- Produces: the `query_complete` frame dict gains `num_turns` and `duration_ms` (alongside the existing `total_cost_usd`); both default to `None` when absent. No other behavior changes.

Spec refs: §6.4 (required for `num_turns`/`duration_ms`), §6.1 (these come from the result frame, not the jsonl).

- [ ] **Step 1: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_translate_result_meta.py
"""_handle_result surfaces num_turns/duration_ms on query_complete. Hermetic."""
from nextseek_api.cc_assistant.translate import CCStreamTranslator


def _translator():
    # construct minimally; _handle_result only reads the payload + self.session_id
    t = CCStreamTranslator.__new__(CCStreamTranslator)
    t.session_id = "sess-1"
    t._terminated = False
    return t


def test_result_surfaces_num_turns_and_duration():
    t = _translator()
    frames = t._handle_result({
        "subtype": "success", "result": "done",
        "total_cost_usd": 0.07, "num_turns": 5, "duration_ms": 1234,
        "session_id": "sess-1",
    })
    (evt, data), = frames
    assert evt == "query_complete"
    assert data["num_turns"] == 5
    assert data["duration_ms"] == 1234
    assert data["total_cost_usd"] == 0.07


def test_missing_meta_is_none_not_crash():
    t = _translator()
    frames = t._handle_result({"subtype": "success", "result": "ok", "session_id": "s"})
    (evt, data), = frames
    assert evt == "query_complete"
    assert data["num_turns"] is None and data["duration_ms"] is None
```

> **Confirmed (Phase 2 vetting):** `_handle_result` is on `CCStreamTranslator` (`translate.py:26`). `_translator()` above is correct.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_translate_result_meta.py`
Expected: FAIL — `KeyError: 'num_turns'` (the frame dict lacks the new keys).

- [ ] **Step 3: Extend the success return in `_handle_result`**

In `translate.py`, in the success branch (the `return [("query_complete", {...})]` at `:149-156`), add the two keys:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-project --with pytest --with pytest-cov python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_translate_result_meta.py --cov=nextseek_api.cc_assistant.translate --cov-report=term-missing`
Expected: PASS (2 tests). **No `--cov-fail-under` here:** `translate.py` is a pre-existing shared procedural module and this task adds no new module (see Global Constraints "Task 5 (translate) is NOT whole-module gated"); the `_handle_result` change is proven by the two assertions above and the Task 13 live gate. `--cov-report=term-missing` is informational only.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/translate.py nextseek_api/cc_assistant/tests/test_translate_result_meta.py
git commit -m "feat(cc-step3): surface num_turns/duration_ms on query_complete (§6.4)"
```

---

### Task 6: hybrid output split + artifact zip (`cc_artifacts.py` + `_publish_artifacts`)

**Files:**
- Create: `nextseek_api/cc_assistant/cc_artifacts.py`
- Modify: `nextseek_api/cc_assistant/cc_engine.py` (`_publish_artifacts` `:639`)
- Modify: `nextseek_api/cc_assistant/tests/test_cc_engine_publish.py` (assert dict return shape)
- Test: `nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py`

**Interfaces:**
- Produces:
  - `RAW_PREFIX = "raw/"` ; `partition_changed(changed: set[str]) -> tuple[set[str], set[str]]` returns `(artifact_rels, raw_rels)` where a rel under `raw/` is raw, else artifact.
  - `build_artifact_zip(srcs: list[Path], dest_zip: Path) -> Path` — tempfile-free deterministic zip (mirrors `content_blobs.download_batch`'s `zipfile.ZipFile` + `writestr` approach).
  - Reworked `_publish_artifacts(scratch_mount, output_mount, *, turn_id: str, output_host_root, before) -> dict` returning `{"artifacts": [ArtifactFile-like dict...], "raw": [host paths], "raw_zip": Path | None}` (replaces the old `list[str]`). **Turn-scoped:** copy deliverables to `output_mount/"artifacts"/<turn_id>/` so multi-turn same-basename files do not overwrite; `ArtifactFile.key` is `f"{turn_id}/{relpath}"`.

Spec refs: §5 (hybrid split, zip-if-multiple), §3 (`output/artifacts/` + `output/raw/`), E3/E9.

- [ ] **Step 1: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py
"""Hermetic hybrid-split + zip tests on a real tmp tree. No Docker, no DB."""
import zipfile
from pathlib import Path

from nextseek_api.cc_assistant.cc_artifacts import (
    partition_changed, build_artifact_zip, RAW_PREFIX,
)


def test_partition_splits_raw_from_artifacts():
    changed = {"report.md", "raw/debug.log", "data/out.csv", "raw/trace/x.txt"}
    artifacts, raw = partition_changed(changed)
    assert artifacts == {"report.md", "data/out.csv"}
    assert raw == {"raw/debug.log", "raw/trace/x.txt"}


def test_raw_prefix_constant():
    assert RAW_PREFIX == "raw/"


def test_build_zip_contains_all_sources(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("AAA")
    b = tmp_path / "sub" / "b.txt"; b.parent.mkdir(); b.write_text("BBB")
    dest = tmp_path / "bundle.zip"
    out = build_artifact_zip([a, b], dest)
    assert out == dest and dest.is_file()
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "a.txt" in names and "sub/b.txt" in names   # relpaths preserved (iter-10)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_artifacts'`

- [ ] **Step 3: Implement `cc_artifacts.py`**

```python
# nextseek_api/cc_assistant/cc_artifacts.py
"""Step 3 — hybrid output partition + artifact bundling (SPEC-3 §5).

Deliverables (everything NOT under scratch/raw/) become downloadable artifacts;
scratch/raw/ is debug output kept on disk + (the jsonl) in the DB, never bundled.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

RAW_PREFIX = "raw/"


def partition_changed(changed: set[str]) -> tuple[set[str], set[str]]:
    """Split changed scratch relpaths into (artifacts, raw). Rel under ``raw/`` is
    raw; everything else is a deliverable artifact."""
    raw = {r for r in changed if r == "raw" or r.startswith(RAW_PREFIX)}
    artifacts = set(changed) - raw
    return artifacts, raw


def build_artifact_zip(srcs: list[Path], dest_zip: Path, *, arc_prefix: Path | None = None) -> Path:
    """Zip ``srcs`` preserving relpaths under ``arc_prefix`` (default: common parent)."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    base = arc_prefix or (srcs[0].parent if srcs else Path("."))
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(srcs, key=lambda p: str(p)):
            arcname = str(src.relative_to(base))
            zf.writestr(arcname, src.read_bytes())
    return dest_zip
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Rework `_publish_artifacts` to the hybrid split**

In `cc_engine.py`, replace the body of `_publish_artifacts` (`:639-672`) so it: diffs as today, partitions via `cc_artifacts.partition_changed`, copies artifacts → `output_mount/"artifacts"`, copies raw → `output_mount/"raw"`, zips artifacts if >1, and returns the structured dict. Keep `_safe_relpath`/symlink guards.

```python
def _publish_artifacts(
    scratch_mount: Path,
    output_mount: Path,
    *,
    turn_id: str,
    output_host_root: str,
    before: dict[str, tuple[int, int]],
) -> dict:
    """Diff scratch; split deliverables (artifacts) from scratch/raw/ (raw).
    Artifacts -> output/artifacts/<turn_id>/ (zipped if >1 per turn, downloadable);
    raw -> output/raw/ (on disk, not bundled). Keys are turn-scoped: "<turn_id>/<relpath>"."""
    from dmac_assistant.run_tracker import diff_files
    from . import cc_artifacts

    after = _snapshot_tree(scratch_mount)
    changed = set(diff_files(before, after))
    if not changed:
        return {"artifacts": [], "raw": [], "raw_zip": None, "files_created": [], "files_modified": []}

    created = {r for r in changed if r not in before}
    modified = changed - created

    art_rels, raw_rels = cc_artifacts.partition_changed(set(changed))
    art_dir = output_mount / "artifacts" / turn_id
    raw_dir = output_mount / "raw"

    def _copy(rels: set[str], dest_root: Path, *, strip_raw_prefix: bool = False) -> list[Path]:
        written: list[Path] = []
        for rel in sorted(rels):
            if not _safe_relpath(rel):
                logger.warning("CC: refusing unsafe artifact relpath %r", rel)
                continue
            src = scratch_mount / rel
            if src.is_symlink() or not src.is_file():
                continue
            out_rel = rel.removeprefix("raw/") if strip_raw_prefix else rel
            dst = dest_root / out_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            written.append(dst)
        return written

    art_files = _copy(art_rels, art_dir)
    raw_files = _copy(raw_rels, raw_dir, strip_raw_prefix=True)

    artifacts: list[dict] = []
    if len(art_files) > 1:
        from nextseek_api.cc_assistant.cc_artifacts import build_artifact_zip
        zip_path = art_dir / "artifacts.zip"
        build_artifact_zip(art_files, zip_path, arc_prefix=art_dir)
        artifacts.append({
            "artifact_type": "file", "key": f"{turn_id}/artifacts.zip",
            "label": "artifacts.zip", "file_format": "zip",
        })
    elif len(art_files) == 1:
        dst = art_files[0]
        rel = dst.relative_to(art_dir)
        artifacts.append({
            "artifact_type": "file", "key": f"{turn_id}/{rel}",
            "label": dst.name, "file_format": dst.suffix.lstrip(".") or "file",
        })
    return {
        "artifacts": artifacts,
        "raw": [str(Path(output_host_root) / "raw" / p.relative_to(raw_dir)) for p in raw_files],
        "raw_zip": None,
        "files_created": sorted(created),
        "files_modified": sorted(modified),
    }
```

- [ ] **Step 5b: Update `test_cc_engine_publish.py`** — the rework makes `turn_id` a **required keyword-only** arg (no default) and changes the return from `list[str]` to a **dict** with the on-disk path `output/artifacts/<turn_id>/<rel>`, so BOTH existing functions (`test_publish_artifacts_copies_nested_scratch_changes`, `test_publish_artifacts_skips_symlinks`) `TypeError`/assert-fail unless updated. **Replace the two existing functions and add the >1-deliverable zip case with EXACTLY this** (paste-ready — keep the other test, `test_safe_relpath_rejects_escape_paths`, unchanged):

```python
def test_publish_artifacts_copies_nested_scratch_changes(tmp_path):
    scratch = tmp_path / "project" / "alice" / "scratch"
    output = tmp_path / "project" / "alice" / "output"
    scratch.mkdir(parents=True)
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "run1").mkdir()
    (scratch / "run1" / "result.txt").write_text("ok")

    result = cc_engine._publish_artifacts(
        scratch,
        output,
        turn_id="run1",
        output_host_root="/host/users/42-px/alice/output",
        before=before,
    )

    # single deliverable -> turn-scoped artifact dict (no zip), copied under
    # output/artifacts/<turn_id>/ (the scratch "run1/" subdir nests under it).
    assert (output / "artifacts" / "run1" / "run1" / "result.txt").read_text() == "ok"
    assert isinstance(result, dict)
    assert result["artifacts"] == [{
        "artifact_type": "file", "key": "run1/run1/result.txt",
        "label": "result.txt", "file_format": "txt",
    }]
    assert result["raw"] == [] and result["raw_zip"] is None
    assert result["files_created"] == ["run1/result.txt"]
    assert result["files_modified"] == []


def test_publish_artifacts_skips_symlinks(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    target = tmp_path / "secret.txt"
    target.write_text("secret")
    (scratch / "leak.txt").symlink_to(target)

    result = cc_engine._publish_artifacts(
        scratch,
        output,
        turn_id="run1",
        output_host_root="/host/output",
        before=before,
    )

    # _snapshot_tree skips symlinks -> nothing changed -> empty-result dict; no leak.
    assert result == {"artifacts": [], "raw": [], "raw_zip": None,
                      "files_created": [], "files_modified": []}
    assert not (output / "leak.txt").exists()
    assert not (output / "artifacts").exists()


def test_publish_artifacts_zips_multiple_and_splits_raw(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "a.txt").write_text("AAA")
    (scratch / "b.txt").write_text("BBB")
    (scratch / "raw").mkdir()
    (scratch / "raw" / "debug.log").write_text("noise")

    result = cc_engine._publish_artifacts(
        scratch,
        output,
        turn_id="run9",
        output_host_root="/host/users/42-px/alice/output",
        before=before,
    )

    # >1 deliverable -> ONE turn-scoped zip artifact (key = "<turn_id>/artifacts.zip").
    assert result["artifacts"] == [{
        "artifact_type": "file", "key": "run9/artifacts.zip",
        "label": "artifacts.zip", "file_format": "zip",
    }]
    assert (output / "artifacts" / "run9" / "artifacts.zip").is_file()
    # scratch/raw/ is split off (prefix stripped), copied to output/raw/, not bundled.
    assert (output / "raw" / "debug.log").read_text() == "noise"
    assert result["raw"] == ["/host/users/42-px/alice/output/raw/debug.log"]
    assert result["raw_zip"] is None
    assert result["files_created"] == ["a.txt", "b.txt", "raw/debug.log"]
    assert result["files_modified"] == []
```

Run:

```bash
uv run --no-project --with pytest python -m pytest -q --noconftest \
  nextseek_api/cc_assistant/tests/test_cc_engine_publish.py
```

Expected: FAIL before Step 5, PASS after. **Coverage (mandatory):** the `cc_artifacts` ≥95% floor must run **both** exercisers of that module together (mirrors Task 3's multi-file command) — `test_cc_artifacts_split.py` is the dedicated exerciser of `partition_changed`/`build_artifact_zip`/`RAW_PREFIX`, while `test_cc_engine_publish.py` drives the `_publish_artifacts` rework; gating on the engine-publish test alone hits `cc_artifacts` only transitively and can fail the floor spuriously:

```bash
uv run --no-project --with pytest --with pytest-cov python -m pytest -q --noconftest \
  nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py \
  nextseek_api/cc_assistant/tests/test_cc_engine_publish.py \
  --cov=nextseek_api.cc_assistant.cc_artifacts --cov-fail-under=95
```

Commit blocked if below floor.

- [ ] **Step 5c: Update `test_cc_realstack.py` + `validate_cc_acceptance.py`** — replace check 16 `copier_published_scoped` / `published_files.json` `{user_id}/`-only heuristic with turn-scoped validation:

```python
# validate_cc_acceptance.py — replace copier_published_scoped body:
def artifacts_turn_scoped(evidence_dir, user_id, project_dirname):
    forced = _load_json(evidence_dir / "forced_result.json")
    arts = forced.get("artifacts") or []
    assert arts, "query_complete missing artifacts"
    for a in arts:
        key = a.get("key", "")
        assert "/" in key, f"artifact key not turn-scoped: {key!r}"
    # optional: stat on-disk output/artifacts/<turn_id>/...
```

**Also update `test_cc_realstack.py`** — replace lines ~181–212:

```python
        (self.evid / "forced_result.json").write_text(json.dumps({
            "event": ev, "is_error": ev == "query_error",
            "reply": data.get("reply", ""), "error": data.get("error"),
            "total_cost_usd": data.get("total_cost_usd"),
            "artifacts": data.get("artifacts") or [],
        }))

        (self.evid / "agent_env_scan.txt").write_text(agent_env)
        net_containers = self._net_containers(NET)
        (self.evid / "network.json").write_text(json.dumps({"containers": net_containers}))
        artifacts = data.get("artifacts") or []
        cost = data.get("total_cost_usd")
        (self.evid / "ledger.json").write_text(json.dumps({"total_cost_usd": cost or 0.0}))
        (self.evid / "meta.json").write_text(json.dumps({
            "run_id": self.run_id, "user_id": self.user_id, "sentinel": self.sentinel,
            "model_id": OPUS, "budget_cap_usd": BUDGET_CAP,
        }))

        # --- direct assertions (the validator re-checks the committed bundle) ---
        self.assertEqual(ev, "query_complete", f"CC turn errored: {data.get('error')}")
        self.assertIn(self.sentinel, data.get("reply", ""),
                      "reply did not echo the per-run sentinel (no real turn?)")
        self.assertTrue(agent_env.strip(), "failed to capture the live agent env")
        for key in ("AWS_BEARER_TOKEN_BEDROCK", "NEO4J_PASSWORD", "MYSQL_PASSWORD", "GCP_API_KEY"):
            self.assertNotRegex(agent_env, rf"(^|\W){key}=", f"{key} leaked into the agent")
        self.assertNotIn("ABSK", agent_env, "AWS bearer token prefix in the agent env")
        self.assertRegex(proxy_window, re.escape(OPUS), "no opus-4-8 invoke in the proxy log")
        self.assertNotIn("ABSK", proxy_window, "proxy logged the bearer token")
        backend = [c for c in net_containers
                   if re.search(r"(^|[-_])(neo4j|seek|mysql)([-_]|$)", c)]
        self.assertEqual(backend, [], f"backend service on the agent network: {backend}")
        self.assertTrue(artifacts, "query_complete missing artifacts")
        for a in artifacts:
            key = a.get("key", "")
            self.assertIn("/", key, f"artifact key not turn-scoped: {key!r}")
```

Drop `published_files.json` capture (or repurpose only if validator still needs it). Add hermetic fixture test in `test_validate_cc_acceptance.py` with turn-scoped `artifacts` keys.

- [ ] **Step 6: Update the caller of `_publish_artifacts` (same commit as Task 8 Dropbox removal)**

In `cc_engine.py`, consume the dict and **remove the Dropbox block in the same edit** (do not leave intermediate `published`-as-list breakage).

> **ORDERING INVARIANT (Phase 2 hardened — read before pasting).** In the live source the region is, in order: `if terminal is None: … finalize()` (`:568-570`) → the `_publish_artifacts` call (`:573`, produces only `published`/`result`) → the `if terminal is None: terminal = (…)` default (`:576-578`) → **`event, data = terminal`** (`:579`, the **unpack** — `event`/`data` do not exist before this line) → the old `if event == "query_complete" and published:` Dropbox block (`:580-587`) → `send_event(event, data)` (`:588`). The publish call MUST stay **before** the unpack (it does not reference `event`/`data`); the consume block that reads `event` MUST stay **after** the unpack. A literal paste of `if event == "query_complete":` directly under the publish call (i.e. before `:579`) **`NameError`s** on `event`.

**Final assembled order — replace the live `:573-588` region with EXACTLY this** (this is the region BEFORE Task 11 inserts its persist block; Task 11 Step 2 inserts that block at the marked point, still after the unpack and before `send_event`):

```python
        # Post-turn publish: diff scratch, split deliverables from scratch/raw/.
        result = _publish_artifacts(
            scratch_mount, output_mount,
            turn_id=str(run_id),
            output_host_root=dirs.output_src, before=before,
        )

        if terminal is None:
            terminal = ("query_complete", {"reply": "(no response)", "bundle_id": None,
                                           "cc_session_id": translator.session_id})
        event, data = terminal                       # <-- unpack stays here (live :579)
        if event == "query_complete":
            data = dict(data)
            data["mode"] = "cc"
            data["artifacts"] = result["artifacts"] or None
            data["cc_raw_files"] = result["raw"]
            # Dropbox reply augmentation + artifacts_published REMOVED here (§8, E8);
            # the old `if event == "query_complete" and published:` block (live :580-587)
            # is fully replaced by this consume block.
        # >>> Task 11 persist block is inserted HERE (after the unpack/consume,
        # >>> before send_event) — see Task 11 "Minimal persist block".
        send_event(event, data)
```

> **Phase 2 coupling rule:** Tasks 6 and 8 both touch this handler — land the hybrid split **and** Dropbox removal atomically so the suite never references the old `list[str]` return shape. The `_publish_artifacts` **call site** keeps its original position relative to `event, data = terminal`; only the post-unpack block changes.

> **Docstring hygiene (same edit):** update the now-stale `run_cc_turn` docstring (`cc_engine.py:417-420`, currently `"Execute one Container-CC turn with scoped Dropbox mounts + artifact publish."` / `"reply augmented with published host paths"`) to describe UI-based I/O (scoped `input/`+`shared/` mounts, artifacts/raw split) — Dropbox is removed by Task 8.

- [ ] **Step 7: Run the full hermetic suite**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). Any `_build_volumes`/engine tests unaffected.

- [ ] **Step 8: Commit**

```bash
git add nextseek_api/cc_assistant/cc_artifacts.py nextseek_api/cc_assistant/cc_engine.py \
  nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py \
  nextseek_api/cc_assistant/tests/test_cc_engine_publish.py \
  nextseek_api/cc_assistant/tests/test_cc_realstack.py \
  nextseek_api/cc_assistant/tests/validate_cc_acceptance.py
git commit -m "feat(cc-step3): hybrid output split (artifacts vs scratch/raw) + artifact dicts (§5, E3)"
```

---

### Task 7: add `cc_traces` to the `Turn` model + surface it in the projection

**Files:**
- Modify: `nextseek_api/assistant/models_api.py` (`Turn`, `:122-138`)
- Modify: `nextseek_api/services/assistant.py` (Turn projection, `:521-529`)
- Test: `nextseek_api/cc_assistant/tests/test_turn_cc_traces.py`

**Interfaces:**
- Produces: `Turn` gains `cc_traces: Optional[List[Dict[str, Any]]] = None`. Because `Turn` has `model_config = ConfigDict(extra="forbid")`, this field MUST be declared or `cc_traces` cannot ride the serialized turn. The projection reads `entry`'s `cc_traces` (set in Task 11) and passes it through.

Spec refs: §6.5 (persist trace, survive reload), §6 (panel).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_turn_cc_traces.py
"""Turn carries cc_traces through model_dump (extra='forbid' requires the field)."""
from nextseek_api.assistant.models_api import Turn


def test_turn_accepts_and_dumps_cc_traces():
    t = Turn(bundle_id=0, user_query="hi", reply="ok", mode="cc",
             cc_traces=[{"cc_session_id": "s", "ts": "t",
                         "steps": [{"line": 2, "kind": "bash", "tool": "Bash", "detail": "ls"}]}])
    d = t.model_dump(mode="json")
    assert d["cc_traces"][0]["steps"][0]["detail"] == "ls"


def test_turn_cc_traces_defaults_none():
    t = Turn(bundle_id=0, user_query="hi", reply="ok", mode="cc")
    assert t.model_dump(mode="json")["cc_traces"] is None


def test_projection_passes_cc_traces_through():
    """Hermetic guard for the Step 4 reload wiring in services/assistant.py.
    The Turn projection (assistant.py:521-529) MUST pass the chat_log entry's
    persisted trace onto the Turn (`cc_traces=entry.get("cc_traces")`); without it,
    reload silently returns NO traces and only the paid Task 13 live gate catches it.
    The projection lives inside the DRF `get_session` @action and is not callable
    without a DB, so this is a source-text guard (same pattern as the Task 11a
    `assistant_reply` grep guard). MUTATION-SENSITIVE: deleting the passthrough line
    removes the substring and FAILS this assertion."""
    from pathlib import Path
    src = (Path(__file__).parents[2] / "services" / "assistant.py").read_text()
    assert 'cc_traces=entry.get("cc_traces")' in src   # Step 4 passthrough is wired
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_turn_cc_traces.py`
Expected: FAIL — pydantic `ValidationError: Extra inputs are not permitted [cc_traces]`

- [ ] **Step 3: Add the field**

In `models_api.py`, in the `Turn` model (after `artifacts`):

```python
    cc_traces: Optional[List[Dict[str, Any]]] = None
```

(Confirm `Dict`, `Any`, `List`, `Optional` are already imported in that module — they are used by `artifacts`.)

- [ ] **Step 4: Surface it in the projection**

In `services/assistant.py` (`:521-529`), add `cc_traces` to the `Turn(...)` construction:

```python
    turns.append(
        Turn(
            bundle_id=bid if bid is not None else 0,
            user_query=entry.get("user_query", ""),
            reply=reply,
            mode=entry.get("mode", ""),
            ts=entry.get("ts"),
            artifacts=artifacts or None,
            cc_traces=entry.get("cc_traces") or None,
        ).model_dump(mode="json")
    )
```

> The `cc_traces=entry.get("cc_traces")` passthrough is hermetically guarded by
> `test_projection_passes_cc_traces_through` (Step 1) — a silent drop of this line
> fails that source guard, not just the paid Task 13 reload assertion. Keep the
> substring byte-identical so the guard stays meaningful.

- [ ] **Step 5: Run tests + full suite**

Run (coverage gate on the module this task edits — `models_api` is declarative pydantic, ≥96% on import via `test_turn_cc_traces.py`): `uv run --no-project --with pytest --with pytest-cov --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_turn_cc_traces.py --cov=nextseek_api.assistant.models_api --cov-fail-under=95`
Expected: PASS, `models_api` ≥95% (commit blocked below floor). Then the full suite to confirm no regression: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/assistant/models_api.py nextseek_api/services/assistant.py nextseek_api/cc_assistant/tests/test_turn_cc_traces.py
git commit -m "feat(cc-step3): Turn.cc_traces field + projection passthrough (§6.5)"
```

---

### Task 8: remove Dropbox — neutral default, dead-config audit, grep-guard

**Note:** Dropbox reply copy in `cc_engine.py` is removed in **Task 6 Step 6** (atomic with hybrid split). This task handles config default (E8), dead `DROPBOX_DIRECTORY`, and grep guards.

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_config.py` (`:15`)
- Test: `nextseek_api/cc_assistant/tests/test_cc_dropbox_grep_guard.py`

**Interfaces:** no new symbols; removes the "Saved to your Dropbox" augmentation + the laptop default; the artifacts channel (Task 6) now carries deliverables.

Spec refs: §8 (remove Dropbox), E8 (neutral default `/srv/dmac/users`, do NOT fail-closed).

- [ ] **Step 1: Write the grep-guard (failing)**

```python
# nextseek_api/cc_assistant/tests/test_cc_dropbox_grep_guard.py
"""Guard: Dropbox copy + laptop path must not reappear in the CC route."""
from pathlib import Path

CC = Path(__file__).resolve().parents[1]   # nextseek_api/cc_assistant


def test_no_dropbox_reply_copy():
    assert "Saved to your Dropbox" not in (CC / "cc_engine.py").read_text()
    assert "artifacts_published" not in (CC / "cc_engine.py").read_text()
    svc = (CC.parent / "services" / "cc_assistant.py").read_text()
    assert "Saved to your Dropbox" not in svc


def test_no_laptop_default_path():
    cfg = (CC / "cc_config.py").read_text()
    assert "/Users/taishajoseph" not in cfg
    assert '/srv/dmac/users' in cfg            # neutral default present
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_dropbox_grep_guard.py`
Expected: FAIL.

- [ ] **Step 3: (Dropbox reply already removed in Task 6 — verify grep-guard passes)**

- [ ] **Step 4: Neutral default (E8) — per-change sign-off (behavior change)**

In `cc_config.py:15`, replace:

```python
_DEFAULT_HOST_USER_ROOT = "/srv/dmac/users"
```

Do NOT fail-closed; keep it a plain default (env `DMAC_USER_ROOT` still overrides via `CCPaths.from_env`). Update the module docstring wording "Dropbox mounts" → "user-scoped mounts" if present.

- [ ] **Step 5: Dead-config audit (conservative)**

`seek/views.py:94 DROPBOX_DIRECTORY` is provably dead (grep shows zero references). Remove that single line. **Do NOT** touch `dmac_assistant/src/dmac_assistant/config.py` `dropbox_root` / `_DEV_DEFAULT_DROPBOX_ROOT` — they are still referenced by the standalone bridge `load_config()` (`config.py:212`), which is NOT on the NExtSEEK CC route; removing them is out of Step-3 scope. Confirm with:

```bash
grep -rn "DROPBOX_DIRECTORY" /home/taishajo/work/NExtSEEK   # expect: only the def line (now removed)
```

- [ ] **Step 6: Run the grep-guard + full suite**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all, incl. the grep-guard).

- [ ] **Step 7: Commit (two diffs: behavior change isolated)**

```bash
git add nextseek_api/cc_assistant/cc_config.py seek/views.py nextseek_api/cc_assistant/tests/test_cc_dropbox_grep_guard.py
git commit -m "feat(cc-step3): dead DROPBOX_DIRECTORY + grep guards (§8)"
git add nextseek_api/cc_assistant/cc_config.py
git commit -m "chore(cc-step3): neutral /srv/dmac/users default, drop laptop path (E8) [signed-off]"
```

---

### Task 9: upload endpoint + Celery task + status poll

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (`CCAssistantViewSet`, `:114`)
- Create: `nextseek_api/cc_assistant/cc_upload_validate.py` (celery-free pure validator module)
- Create: `nextseek_api/cc_assistant/cc_upload_tasks.py` (celery task; imports the validator from `cc_upload_validate`)
- Test: `nextseek_api/cc_assistant/tests/test_cc_upload_validate.py`

**Interfaces:**
- Produces:
  - Pure helper `validate_upload_filename(name: str) -> str` (in the **celery-free** module `cc_upload_validate.py`, imported by both the test and `cc_upload_tasks.py`) — returns a safe basename or raises `ValueError` (reject `/`, `..`, NUL, absolute, empty). Keeping it out of `cc_upload_tasks.py` means the hermetic validator test imports with **zero celery dependency** (the `cc_upload_tasks` top-level celery import block would otherwise raise an uncaught `ModuleNotFoundError` under the no-celery harness).
  - DRF `@action(detail=False, methods=["post"], url_path="upload")` `upload(self, request)` — `request.FILES.getlist("file")`; size cap via `settings.BATCH_UPLOAD_MAX_TOTAL_BYTES`; resolves the user's project (`resolve_user_project(api_user, api_pass)`), builds `dirs.input_mnt`, enqueues `run_cc_upload_task.delay(...)`; returns `{"job_id", "status": "queued"}` 202.
  - `@action(detail=False, methods=["get"], url_path=r"upload/status/(?P<job_id>[^/.]+)")` `upload_status(self, request, job_id=None)` — `AsyncResult`, returns `{job_id, state, meta, result}` (mirror `batch_upload.job_status`).
  - Celery task `run_cc_upload_task` (queue `batch_upload`, `bind=True`) — validated save of each file into `input_mnt`, `update_state` progress.

Spec refs: §4 (upload, async, E1/E2), §10 (host-side, no agent cred, filename validation).

- [ ] **Step 1: Write the failing validator tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_upload_validate.py
"""Hermetic filename validation for CC uploads. No Django, no Celery.

Imports from the celery-free `cc_upload_validate` module so collection never
touches the celery import block in `cc_upload_tasks.py`."""
import pytest

from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename


@pytest.mark.parametrize("good", ["data.csv", "report 1.xlsx", "a_b-c.txt"])
def test_accepts_plain_filenames(good):
    assert validate_upload_filename(good) == good


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.txt", "/abs.txt", "", "x\x00y", ".."])
def test_rejects_traversal_and_separators(bad):
    with pytest.raises(ValueError):
        validate_upload_filename(bad)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_upload_validate.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_upload_validate'`

- [ ] **Step 3: Implement the validator + Celery task**

First, the **celery-free** validator module (imported by both the test and the task):

```python
# nextseek_api/cc_assistant/cc_upload_validate.py
"""Step 3 — CC upload filename validation (SPEC-3 §4, §10).

PURE module — no Django, no Celery import — so the hermetic validator test
(`test_cc_upload_validate.py`) imports it with zero celery dependency. Both the
celery task (`cc_upload_tasks.run_cc_upload_task`) and the upload action reuse it.
"""
from __future__ import annotations

import os


def validate_upload_filename(name: str) -> str:
    """Return a safe single-segment basename or raise ValueError. Rejects path
    separators, traversal, NUL, absolute, empty."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise ValueError(f"invalid filename: {name!r}")
    if "/" in name or "\\" in name or "\x00" in name or os.path.isabs(name):
        raise ValueError(f"invalid filename: {name!r}")
    base = os.path.basename(name)
    if base != name or not base:  # pragma: no cover - defensive; separators/absolute already rejected above
        raise ValueError(f"invalid filename: {name!r}")
    return base
```

> The `base != name or not base` branch is a defensive belt-and-suspenders that is provably unreachable once `/`, `\`, NUL and absolute paths are rejected above (verified: without the pragma the module sits at 91% — one dead line — and the `--cov-fail-under=95` floor fails). `# pragma: no cover` is the justified-exception mechanism so the floor measures only reachable lines; the module then covers 100%.

Then the celery task module (imports the validator — defines it **nowhere** itself):

```python
# nextseek_api/cc_assistant/cc_upload_tasks.py
"""Step 3 — CC upload Celery task (SPEC-3 §4).

Saves uploaded files into the user's persistent input/ dir (E2). Runs host-side;
no credential reaches the agent (OI-3). Mirrors batch_upload's async + update_state.
Filename validation lives in the celery-free `cc_upload_validate` module so the
hermetic validator test never imports the celery block below.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename


try:
    from nextseek_api.batch_upload.celery_app import app
except Exception:  # pragma: no cover
    from celery import shared_task as _shared

    def app_task(*a, **k):
        return _shared(*a, **k)
else:
    app_task = app.task


@app_task(bind=True, queue="batch_upload", name="cc_assistant.upload")
def run_cc_upload_task(self, *, input_mnt: str, files: list[dict]):
    """``files`` = [{"name": str, "tmp_path": str}] staged by the view under MEDIA_ROOT.
    Validate each name and move it into ``input_mnt``."""
    dest_root = Path(input_mnt)
    dest_root.mkdir(parents=True, exist_ok=True)
    saved = []
    total = len(files) or 1
    try:
        for i, f in enumerate(files):
            safe = validate_upload_filename(f["name"])
            dst = dest_root / safe
            # MUST be shutil.move, not os.replace/os.rename: the staging dir is
            # MEDIA_ROOT="/media" (container overlayfs, no volume mount) while
            # input_mnt is under the host bind mount /srv/dmac/users:/dmac/users —
            # a different device. os.replace is rename(2) and raises
            # OSError(EXDEV, "Invalid cross-device link") across devices; shutil.move
            # falls back to copy2 + unlink when rename fails cross-device.
            shutil.move(f["tmp_path"], dst)
            saved.append(safe)
            self.update_state(state="PROGRESS",
                              meta={"progress_pct": int((i + 1) / total * 100), "saved": saved})
        return {"saved": saved, "count": len(saved)}
    finally:
        for f in files:
            try:
                os.unlink(f["tmp_path"])
            except FileNotFoundError:
                pass
```

**Staging hygiene (Task 9 Step 5):** Celery `finally` unlinks each `tmp_path` after move (above). Because `shutil.move` already removes the source (rename, or copy2+unlink on the cross-device fallback), the `finally: os.unlink(tmp_path)` is a **no-op** for a successfully moved file (the `FileNotFoundError` is already swallowed); it only matters when the move never ran (e.g. a `validate_upload_filename` raise mid-batch leaves later `tmp_path`s un-moved). View may reuse a single staging subdir per `job_id` prefix; Task 13 Step 4 evidence includes `ls` of `MEDIA_ROOT/cc_upload_staging/` before/after upload to prove no orphan growth.

**Cross-device move (Phase 2 hardened):** This is the **only** filesystem move in the plan that crosses the `/media` overlayfs → `/dmac/users` bind-mount boundary; it MUST use `shutil.move` (not `os.replace`/`os.rename`). The only other rename-family call (`os.unlink` in the `finally`) stays within `/media`. The move is celery-gated (no hermetic seam), so Task 13 Step 4 adds an explicit on-host assertion that the file actually lands in `input/` (the live gate that catches an `EXDEV` regression).

> **Confirmed (Phase 2 vetting):** Celery app import is `from nextseek_api.batch_upload.celery_app import app` (mirror `batch_upload/views.py:23`). **Worker registration:** add to Task 9 Step 3b — in `batch_upload/celery_app.py` after the `cc_sweep` import:
> `import nextseek_api.cc_assistant.cc_upload_tasks  # noqa: F401, E402`
> Without this explicit import, `run_cc_upload_task` is **NotRegistered** in the worker despite the `cc_assistant.*` route.

- [ ] **Step 3b: Register upload task in Celery worker**

In `nextseek_api/batch_upload/celery_app.py`, add:
```python
import nextseek_api.cc_assistant.cc_upload_tasks  # noqa: F401, E402
```
Verify in Task 13: `celery -A nextseek_api.batch_upload.celery_app inspect registered | grep cc_assistant.upload` exits 0.

- [ ] **Step 4: Run the validator tests to verify they pass**

Run: `uv run --no-project --with pytest --with pytest-cov python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_upload_validate.py --cov=nextseek_api.cc_assistant.cc_upload_validate --cov-fail-under=95`
Expected: PASS (validator cases), `cc_upload_validate` ≥95% (commit blocked below floor). No `--with celery`: the validator lives in the celery-free `cc_upload_validate` module, so collection imports cleanly and the coverage floor measures **only** the pure validator (not the celery task body / import guard). The Celery task body is exercised live in Task 13.

- [ ] **Step 5: Add the `upload` + `upload_status` actions**

In `services/cc_assistant.py`, add to `CCAssistantViewSet` (mirror `batch_upload.start`/`job_status`; use the existing `self._resolve_credentials(request)` at `:142`). **Add `import os` and `import time` at module top** before the pasted upload action.

> **SPEC §4 note:** locked design says `input_src` (host bind source); Django runs in-container — implementation writes to **`dirs.input_mnt`** (same `*_mnt` convention as `output_mnt` publish in Step 2).

```python
    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        from django.conf import settings
        from rest_framework.response import Response
        from rest_framework import status as drf_status
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_tasks import run_cc_upload_task
        from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename

        uploaded = request.FILES.getlist("file")
        if not uploaded:
            return Response({"error": "no files"}, status=400)
        cap = getattr(settings, "BATCH_UPLOAD_MAX_TOTAL_BYTES", 200 * 1024 * 1024)
        if sum(f.size for f in uploaded) > cap:
            return Response({"error": "upload too large"}, status=413)

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)

        staged = []
        seen_names: set[str] = set()
        stage_root = os.path.join(getattr(settings, "MEDIA_ROOT", "/tmp"), "cc_upload_staging")
        os.makedirs(stage_root, exist_ok=True)
        for f in uploaded:
            safe = validate_upload_filename(getattr(f, "name", ""))
            if safe in seen_names:
                return Response({"error": f"duplicate filename in batch: {safe}"}, status=400)
            seen_names.add(safe)
            tmp = os.path.join(stage_root, f"{int(time.time() * 1000)}_{safe}")
            with open(tmp, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            staged.append({"name": safe, "tmp_path": tmp})

        from nextseek_api.batch_upload.job_index import register_job

        task = run_cc_upload_task.delay(input_mnt=dirs.input_mnt, files=staged)
        register_job(user_id=request.user.pk, job_id=task.id, project_id=int(project.id) if str(project.id).isdigit() else 0)
        return Response({"job_id": task.id, "status": "queued"},
                        status=drf_status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"upload/status/(?P<job_id>[^/.]+)")
    def upload_status(self, request, job_id=None):
        from rest_framework.response import Response
        from celery.result import AsyncResult
        from nextseek_api.batch_upload.celery_app import app as celery_app
        from nextseek_api.batch_upload.job_index import user_owns_job

        if not user_owns_job(request.user.pk, job_id):
            return Response({"error": "not found"}, status=404)
        r = AsyncResult(job_id, app=celery_app)
        resp = {"job_id": job_id, "state": r.state, "meta": {}, "result": None}
        if r.state == "PROGRESS":
            resp["meta"] = r.info or {}
        elif r.state == "SUCCESS":
            resp["result"] = r.result
        elif r.state == "FAILURE":
            resp["meta"] = {"error": str(r.result)}
        return Response(resp)
```

- [ ] **Step 6: Run the full hermetic suite (no endpoint exec)**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). The actions are import-checked; live exec is Task 13.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/cc_assistant/cc_upload_validate.py nextseek_api/cc_assistant/cc_upload_tasks.py nextseek_api/batch_upload/celery_app.py nextseek_api/services/cc_assistant.py nextseek_api/cc_assistant/tests/test_cc_upload_validate.py
git commit -m "feat(cc-step3): per-user upload endpoint + Celery task + status poll (§4, E1/E2)"
```

---

### Task 9b: upload list endpoint (SPEC §4 "upload + list")

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py`
- Create: `nextseek_api/cc_assistant/tests/test_cc_upload_list.py`

**Interfaces:**
- Pure helper: `def list_input_files(input_mnt: str) -> list[str]` — sorted basenames of regular files directly under `input_mnt` (no recursion; skip dotfiles).
- `@action(detail=False, methods=["get"], url_path="upload/list")` `upload_list(self, request)` — owner-scoped via `resolve_user_project` + `build_user_dirs`; returns `{"files": list_input_files(dirs.input_mnt)}`.

Spec refs: §4 lifecycle ("Step 3 ships upload + list"), E2.

- [ ] **Step 1: Write failing hermetic test**

```python
# nextseek_api/cc_assistant/tests/test_cc_upload_list.py
from pathlib import Path

from nextseek_api.cc_assistant.cc_upload_list import list_input_files


def test_list_input_files_sorted_basenames(tmp_path: Path):
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.csv").write_text("y")
    (tmp_path / ".hidden").write_text("z")
    (tmp_path / "subdir").mkdir()
    assert list_input_files(str(tmp_path)) == ["a.csv", "b.txt"]


def test_list_input_files_missing_dir_returns_empty(tmp_path: Path):
    # the user's input/ may not exist before the first upload -> empty, not an error
    assert list_input_files(str(tmp_path / "nope")) == []
```

> `list_input_files` must guard the not-yet-created `input/` dir (return `[]` on missing dir); the second test exercises that branch so the ≥95% floor (Step 3) is reachable.

- [ ] **Step 2: Implement pure helper + DRF action**

Add `cc_upload_list.py` with `list_input_files`. In `CCAssistantViewSet`, mirror Task 9 upload's credential/project resolution:

```python
    @action(detail=False, methods=["get"], url_path="upload/list")
    def upload_list(self, request):
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_list import list_input_files

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        return Response({"files": list_input_files(dirs.input_mnt)})
```

> **Self-contained (do NOT depend on a later task's module-top import):** the local import above includes `ProjectResolutionError` — mirroring Task 9's `upload` action — because the `except ProjectResolutionError:` clause references it and `services/cc_assistant.py` imports that name only *locally inside `_run`* (the module-top import is added by **Task 10**, which runs *after* this task). Without it this paste `NameError`s at call time, invisibly to the hermetic suite.

- [ ] **Step 3: Verify + grep guard**

```bash
uv run --no-project --with pytest --with pytest-cov python -m pytest -q --noconftest \
  nextseek_api/cc_assistant/tests/test_cc_upload_list.py \
  --cov=nextseek_api.cc_assistant.cc_upload_list --cov-fail-under=95
```

Also grep/source guard: `url_path="upload/list"` on `CCAssistantViewSet`.

Expected: PASS, `cc_upload_list` ≥95% (commit blocked below floor). Live HTTP exercised in Task 13 Step 4.

- [ ] **Step 4:** Wire optional "already uploaded" list in Task 12 `UploadControl` (calls `GET …/upload/list`).

- [ ] **Step 5:** Task 13 Step 4 live gate must list uploaded basenames via this endpoint (saved in `live_gate_transcript.txt`).

- [ ] **Step 6: Commit**

`git commit -m "feat(cc-step3): upload list endpoint (§4)"`

---

### Task 10: artifact-download + transcript-recover endpoints (owner-scoped)

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_endpoint_guards.py` (pure owner/key guard helpers); endpoints live in Task 13.

**Interfaces:**
- Produces:
  - `@action(detail=False, methods=["get"], url_path=r"artifacts/(?P<session>[0-9a-f-]+)/download")` `download_artifact(self, request, session=None)` — `?key=<turn-scoped relpath>` (e.g. `{turn_id}/report.md` from Task 6); owner-scoped; resolves `output/artifacts/<key>` with `_safe_relpath` on full key; `key == "all"` zips that turn's subtree only when `?turn_id=` provided.
  - `@action(detail=False, methods=["get"], url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")` `recover_transcript(self, request, session=None, turn=None)` — owner-scoped; **`cc_session_id` query param required** when multiple rows match `(chat_session, turn_id)`; loads `CCSessionTranscript`, decompress, stream jsonl.

Spec refs: §5 (download), §7 (recover), §10 (owner-scoping, traversal guard, bomb bound).

- [ ] **Step 0: Pure guard helpers + failing tests**

Create `test_cc_endpoint_guards.py`:

```python
def test_resolve_artifact_path_rejects_traversal(tmp_path):
    from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
    art = tmp_path / "artifacts" / "turn1"
    art.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_artifact_path(str(art.parent), "../etc/passwd")
```

Create `cc_endpoint_guards.py`:

```python
from pathlib import Path
from nextseek_api.cc_assistant.cc_engine import _safe_relpath

def resolve_artifact_path(artifacts_root: str, key: str) -> Path:
    if not key or not _safe_relpath(key):
        raise ValueError("bad key")
    root = Path(artifacts_root).resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ValueError("traversal")
    return target


def session_owned_by_user(user_id: int, session_id: str) -> bool:
    from nextseek_api.assistant.models_db import ChatSession
    return ChatSession.objects.filter(user_id=user_id, session_id=session_id).exists()
```

Add to `test_cc_endpoint_guards.py`:

```python
def test_session_owned_by_user_false_for_wrong_user(monkeypatch):
    from nextseek_api.cc_assistant import cc_endpoint_guards as g
    class Q:
        def filter(self, **kw): return self
        def exists(self): return False
    monkeypatch.setattr(
        "nextseek_api.assistant.models_db.ChatSession",
        type("M", (), {"objects": Q()}),
    )
    assert g.session_owned_by_user(1, "sess") is False
```

- [ ] **Step 1: Module-level stream helpers + download action**

Add at module top of `cc_assistant.py` edits: `from pathlib import Path`, `import os`, `from django.http import StreamingHttpResponse, Http404`, `from rest_framework.response import Response`, `from nextseek_api.cc_assistant.cc_provision import ProjectResolutionError`.

```python
def _iter_and_cleanup(path: Path):
    """Mirror content_blobs._iter_and_cleanup — unlink temp zip after stream."""
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                yield chunk
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def _iter_file(path: Path):
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            yield chunk
```

Add `download_artifact` on `CCAssistantViewSet` (class body — **not** nested under helpers):

```python
    @action(detail=False, methods=["get"], url_path=r"artifacts/(?P<session>[0-9a-f-]+)/download")
    def download_artifact(self, request, session=None):
        from django.http import StreamingHttpResponse, Http404
        from nextseek_api.assistant.models_db import ChatSession
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import resolve_user_project, build_user_dirs
        from nextseek_api.cc_assistant.cc_engine import _safe_relpath

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        key = request.query_params.get("key", "")
        if not key or (key != "all" and not _safe_relpath(key)):
            raise Http404("bad key")

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
        art_dir = Path(dirs.output_mnt) / "artifacts"
        if key == "all":
            turn_id = request.query_params.get("turn_id", "")
            if not turn_id or not _safe_relpath(turn_id):
                raise Http404("bad turn_id")
            art_dir = art_dir / turn_id
            import tempfile
            from nextseek_api.cc_assistant.cc_artifacts import build_artifact_zip
            # Exclude the per-turn artifacts.zip written by Task 6 into this same
            # art_dir, else key="all" nests the prior zip inside the new one.
            files = [p for p in art_dir.rglob("*") if p.is_file() and p.name != "artifacts.zip"]
            tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
            build_artifact_zip(files, tmp, arc_prefix=art_dir)
            resp = StreamingHttpResponse(_iter_and_cleanup(tmp), content_type="application/zip")
            resp["Content-Disposition"] = 'attachment; filename="artifacts.zip"'
            return resp

        target = resolve_artifact_path(str(art_dir), key)
        if not target.is_file():
            raise Http404("not found")
        resp = StreamingHttpResponse(_iter_file(target), content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="{target.name}"'
        return resp
```

(Use `_iter_and_cleanup` **only** for temp zip paths; permanent artifact files use `_iter_file` without unlink.)

- [ ] **Step 2: Add the transcript-recover action**

```python
    @action(detail=False, methods=["get"], url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")
    def recover_transcript(self, request, session=None, turn=None):
        from django.conf import settings
        from django.http import HttpResponse, Http404
        from nextseek_api.assistant.models_db import ChatSession, CCSessionTranscript
        from nextseek_api.cc_assistant.cc_transcript_store import decompress

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        cc_sid = request.query_params.get("cc_session_id")
        qs = CCSessionTranscript.objects.filter(chat_session=cs, turn_id=turn)
        if cc_sid:
            qs = qs.filter(cc_session_id=cc_sid)
        elif qs.count() > 1:
            return Response({"error": "cc_session_id required"}, status=400)
        row = qs.order_by("-created_at").first()
        if row is None:
            raise Http404("no transcript")
        jsonl = decompress(bytes(row.blob), max_bytes=getattr(settings, "CC_TRANSCRIPT_MAX_BYTES", 256 * 1024 * 1024))
        resp = HttpResponse(jsonl, content_type="application/x-ndjson")
        resp["Content-Disposition"] = f'attachment; filename="transcript-{turn}.jsonl"'
        return resp
```

- [ ] **Step 3: Run the full hermetic suite (import check)**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). Endpoint behavior verified live (Task 13).

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/services/cc_assistant.py \
  nextseek_api/cc_assistant/cc_endpoint_guards.py \
  nextseek_api/cc_assistant/tests/test_cc_endpoint_guards.py
git commit -m "feat(cc-step3): owner-scoped artifact-download + transcript-recover endpoints (§5,§7,§10)"
```

---

### Task 11: persist trace + transcript + live emit (inside `cc_engine.run_cc_turn`)

> **Execution order:** Task **11a** (section below) must commit **before** Task 11 Step 2 — despite doc numbering 11 before 11a.

> **BLOCKED until Task 11a commits.** Global Constraints require Task 11a before Task 11 Step 2.

**Why not `services/cc_assistant._run`:** Verified Phase 2 onboard — `_run` delegates to `cc_engine.run_cc_turn` and does **not** hold `mount_path`, scratch diff lists, or `result_meta`. All post-turn symbols live in `cc_engine.py` after `_publish_artifacts` (`:573–588` today).

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_engine.py` (post-publish block in `run_cc_turn`)
- Modify: `nextseek_api/services/cc_assistant.py` — pass `chat_session`, `user_query`, and an `on_turn_complete` callback **into** `run_cc_turn` (new optional kwargs) so DB writes stay owner-scoped without circular imports
- Test: live (Task 13); pure pieces covered by Tasks 1/4/7

**Interfaces (in `run_cc_turn`, after `_publish_artifacts`, before `send_event`):**
1. Resolve current-turn jsonl: newest `*.jsonl` under `Path(dirs.cc_state_mnt) / "projects"` by mtime (same algorithm as `_session_metas` / `cc_session.store_has_transcripts`).
2. `result_meta = {"num_turns": data.get("num_turns"), "duration_ms": data.get("duration_ms"), "cost_usd": data.get("total_cost_usd")}` from terminal `query_complete` frame (Task 5).
3. `extract_trace(parsed, cc_session_id=translator.session_id, ts=timezone.now().isoformat(), files_created=result["files_created"], files_modified=result["files_modified"], result_meta=result_meta)`.
4. **Live emit (SPEC §6.5):** `data["cc_traces"] = [trace.model_dump()]` on `query_complete` (alongside `artifacts` from Task 6).
5. **Persist (E5 + reload):** invoke `on_turn_complete(TurnCompletePayload(...))` which **must** (single RMW save via `_append_cc_turn_complete` in Task 11a — **sole owner** of `chat_log`, the locked-E5 `es["cc_traces"]` mirror, and the `CCSessionTranscript` upsert; the per-turn trace is written to **BOTH** stores in the **same** single RMW save):
   - Append **`chat_log` entry** with `{user_query, assistant_reply, mode: "cc", ts, artifacts, cc_traces, turn_id: str(run_id)}` — reload source of truth (`assistant.py` reads `assistant_reply`, not `reply`).
   - **Mirror append** the per-turn trace to `es["cc_traces"]` per locked **E5** (mandatory, not optional; same RMW save as the `chat_log` append; kept small per §6.5 since it loads on every session read).

   Recover URL contract: `GET …/transcript/{chat_session_id}/{turn_id}/` where `turn_id == str(query_task.task_id) == run_id` passed into `run_cc_turn`. Task 13 Step 5 must use the same `turn_id`.

6. **Failure policy (Phase 2 hardened — best-effort on success):** persistence (jsonl discovery, trace extract, `on_turn_complete` DB write) is **best-effort on the success path**. A turn that already produced a paid, successful reply is **never** converted to `query_error` solely because persistence failed — the reply (and the live `cc_traces` already attached to `data`) is always delivered via `send_event(event, data)`; the failure is logged at error level. A **hard re-raise** is kept **only** behind the dev/test `CC_PERSIST_STRICT` setting (default `False`) so the Task 13 live gate can opt into failing hard during verification. SPEC-3 §6.5/§7 lock the persist *write path* (E5/E6/E7) but do **not** lock a re-raise-and-discard behavior, so this is a plan-level policy, not a design override.

**Typed callback (paste into `nextseek_api/cc_assistant/cc_turn_complete.py` — neutral module; do NOT define in `services/cc_assistant.py` or import across `cc_engine` ↔ `services` boundary):**

```python
# nextseek_api/cc_assistant/cc_turn_complete.py
from __future__ import annotations

from dataclasses import dataclass

@dataclass
class TurnCompletePayload:
    chat_session: ChatSession  # import from assistant.models_db at use sites
    user_query: str
    assistant_reply: str
    ts: str
    artifacts: list[dict] | None
    cc_traces: list[dict]
    turn_id: str
    cc_session_id: str | None
    raw_jsonl: bytes

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


def append_capped(chat_log: list, entry: dict, *, cap: int = 50) -> list:
    """Append ``entry`` to ``chat_log`` and keep only the **newest** ``cap`` turns
    (FIFO eviction of the oldest). Returns a NEW list (does not mutate the input).

    Pure + Django-free so it is hermetically unit-tested (the live gate never
    reaches the 50-turn boundary). Newest-kept is load-bearing: a ``chat_log[:cap]``
    mutation (keep oldest) must FAIL `test_append_capped_keeps_newest_in_order`."""
    out = list(chat_log)
    out.append(entry)
    if len(out) > cap:
        out = out[-cap:]
    return out


def apply_turn_to_extra_state(extra_state: dict | None, payload: "TurnCompletePayload",
                              *, cap: int = 50) -> dict:
    """Pure RMW transform: return a NEW extra_state dict with the turn appended to
    BOTH stores in one shot — ``chat_log`` (reload source of truth) AND the locked
    SPEC-3 **E5/§6.5** ``cc_traces`` mirror. Django-free so the E5 mirror is
    hermetically guarded: removing the ``es["cc_traces"]`` append must FAIL
    ``test_apply_turn_writes_chat_log_and_cc_traces_mirror``. The mirror is FIFO-capped
    (same ``cap``) to stay small per §6.5 (loaded on every session read)."""
    es = dict(extra_state or {})
    es["chat_log"] = append_capped(
        list(es.get("chat_log") or []), serialize_cc_chat_log_entry(payload), cap=cap)
    cc_traces = list(es.get("cc_traces") or [])
    for tr in payload.cc_traces:                 # locked E5 mirror (per-turn trace[s])
        cc_traces = append_capped(cc_traces, tr, cap=cap)
    es["cc_traces"] = cc_traces
    return es
```

**Minimal persist block inside `run_cc_turn` (after `_publish_artifacts`, before `send_event`):**

At **turn start** (before container spawn): `translator._turn_start_ts = time.time()`.

```python
if event == "query_complete" and on_turn_complete and chat_session is not None:
    # LOCAL imports (not module-top): keeps a hermetic `import cc_engine` (used by
    # test_cc_newest_jsonl) Django-settings-free — `timezone.now()` reads
    # settings.USE_TZ and would raise ImproperlyConfigured if imported+exercised at
    # module scope without Django settings. cc_engine's module top (verified :22-35)
    # imports none of these. Copy these names verbatim:
    import time
    from django.utils import timezone
    from . import cc_summary, cc_trace
    from .cc_turn_complete import TurnCompletePayload
    turn_start = translator._turn_start_ts  # set at turn open — do not use time.time() here
    jsonl_path = None
    for _ in range(3):
        jsonl_path = _newest_jsonl_under(
            Path(dirs.cc_state_mnt) / "projects", min_mtime=turn_start - 1)
        if jsonl_path:
            break
        time.sleep(0.2)
    raw = jsonl_path.read_bytes() if jsonl_path else b""
    if raw:
        import shutil
        raw_copy = Path(dirs.output_mnt) / "raw" / f"transcript-{run_id}.jsonl"
        raw_copy.parent.mkdir(parents=True, exist_ok=True)
        if not _safe_relpath(raw_copy.name):
            raise ValueError("bad transcript basename")
        shutil.copy2(jsonl_path, raw_copy)
    parsed = cc_summary.parse_transcript(raw) if raw else None
    trace = cc_trace.extract_trace(
        parsed, cc_session_id=translator.session_id or "",
        ts=timezone.now().isoformat(),
        files_created=result["files_created"],
        files_modified=result["files_modified"],
        result_meta={"num_turns": data.get("num_turns"),
                     "duration_ms": data.get("duration_ms"),
                     "cost_usd": data.get("total_cost_usd")},
    ) if parsed else None
    from django.conf import settings
    strict = getattr(settings, "CC_PERSIST_STRICT", False)  # dev/test-only hard gate
    if trace is not None:
        data = dict(data)
        data["mode"] = "cc"
        data["cc_traces"] = [trace.model_dump()]   # live panel shows even if DB persist fails
        try:
            on_turn_complete(TurnCompletePayload(
                chat_session=chat_session, user_query=user_query or "",
                assistant_reply=data.get("reply") or "",
                ts=timezone.now().isoformat(),
                artifacts=data.get("artifacts"),
                cc_traces=[trace.model_dump()],
                turn_id=str(run_id),
                cc_session_id=translator.session_id,
                raw_jsonl=raw,
            ))
        except Exception:
            # BEST-EFFORT on the SUCCESS path: a paid, successful reply is NEVER
            # turned into query_error just because persistence failed. Log loudly;
            # the live trace already rode out on `data`, and `send_event(event, data)`
            # below still delivers the reply. Re-raise ONLY under the dev/test strict
            # gate so the Task 13 live gate can still fail hard during verification.
            logger.exception("CC persist failed after a successful turn "
                             "(run_id=%s); delivering reply, trace not persisted", run_id)
            if strict:
                raise
    else:
        # No jsonl discovered despite a successful turn: deliver the reply WITHOUT a
        # persisted trace rather than discarding paid output. Error-level log so
        # Task 13's reload assertion (empty cc_traces) still fails the gate loudly.
        logger.error("cc persist: missing transcript jsonl after successful turn "
                     "(run_id=%s); delivering reply without persisted trace", run_id)
        if strict:
            raise RuntimeError("cc persist: missing transcript jsonl after successful turn")
```

**Also (SPEC §3/E3):** after reading `raw`, `copy2` to `Path(dirs.output_mnt) / "raw" / f"transcript-{run_id}.jsonl"` (basename validated).

**Empty/missing jsonl policy (Phase 2 hardened — best-effort):** If `query_complete` fires but no jsonl is found under cc-state, **do not discard the paid reply** — log at **error** level and still deliver the reply (without a persisted trace). The hard `RuntimeError("cc persist: missing transcript jsonl after successful turn")` is raised **only** when `CC_PERSIST_STRICT` is set (dev/test gate). In normal operation the Task 13 reload assertion (Step 6: non-empty `turns[*].cc_traces` after reload) is what catches a persistent jsonl/path mismatch — it fails the gate loudly **without** destroying paid output. Enable `CC_PERSIST_STRICT=True` for the duration of the Task 13 live gate run if a hard, immediate failure signal is preferred over the reload assertion.

- [ ] **Step 1:** Helpers only — paste-ready `_newest_jsonl_under` + tests:

```python
def _newest_jsonl_under(root: Path, *, min_mtime: float | None = None) -> Path | None:
    """Pick newest *.jsonl under root; if min_mtime set, only files with mtime >= min_mtime."""
    candidates = [p for p in root.rglob("*.jsonl") if p.is_file()]
    if min_mtime is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= min_mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
```

Hermetic test in `nextseek_api/cc_assistant/tests/test_cc_newest_jsonl.py`:

```python
# nextseek_api/cc_assistant/tests/test_cc_newest_jsonl.py
"""Newest-jsonl selection on REAL stat data. Py3.12-safe: no monkeypatching of
pathlib.Path.stat (PosixPath uses __slots__ — `monkeypatch.setattr(<Path>, "stat", …)`
raises `AttributeError: 'PosixPath' object attribute 'stat' is read-only`). Instead
create real files and set distinct mtimes with os.utime."""
import os


def test_newest_jsonl_respects_min_mtime(tmp_path):
    from nextseek_api.cc_assistant.cc_engine import _newest_jsonl_under
    old = tmp_path / "old.jsonl"; old.write_text("x"); os.utime(old, (1.0, 1.0))
    new = tmp_path / "new.jsonl"; new.write_text("y"); os.utime(new, (10.0, 10.0))
    assert _newest_jsonl_under(tmp_path, min_mtime=5.0) == new   # only `new` clears the floor
    assert _newest_jsonl_under(tmp_path, min_mtime=20.0) is None  # both below floor
```

Add `TurnCompletePayload` dataclass in `cc_turn_complete.py` (see Task 11). Extend `run_cc_turn` kwargs — **do not call `on_turn_complete` yet**. On missing jsonl after **3× 200ms retry**, follow the locked best-effort policy (Step 6 / "Empty/missing jsonl policy" / the persist-block paste): **deliver the reply without a persisted trace and log at error level; raise `RuntimeError` only under `CC_PERSIST_STRICT`** — a paid, successful reply is never converted to `query_error` by a persist miss.

**Sub-step (turn open, before container spawn):** immediately after `translator = CCStreamTranslator()` in `run_cc_turn`, set `translator._turn_start_ts = time.time()` (import `time` at module top). Grep guard or hermetic source test: `_turn_start_ts` assigned before `containers.run`.

- [ ] **Step 2:** Implement persist block (**BLOCKED until Task 11a commit**).
- [ ] **Step 3:** Wire from `cc_assistant.py` `_run` closure:

```python
# cc_engine.run_cc_turn signature extension:
# ..., chat_session: ChatSession | None = None, user_query: str = "",
#     on_turn_complete: Callable[[TurnCompletePayload], None] | None = None

cc_engine.run_cc_turn(
    ...,
    chat_session=chat_session,
    user_query=req.query or "",
    on_turn_complete=_append_cc_turn_complete,
)
```
- [ ] **Step 4: Wiring guards (2D — close the "stubbed/unwired callback" gap) + run.** A stubbed `on_turn_complete` (kwarg accepted but never invoked) or a missing `_append_cc_turn_complete` wiring passes **every** other hermetic test — the pure helpers (`append_capped`/`apply_turn_to_extra_state`/`serialize_cc_chat_log_entry`/`_newest_jsonl_under`) are tested in isolation, and the existing source guards only check the helper bodies and the projection read, not that the engine actually CALLS the callback or that `services` actually WIRES it. Only the paid live gate would otherwise catch a "marked DONE, wiring stubbed" regression. Add two cheap source-text grep guards (run **after** Step 2 lands the persist-block call and Step 3 lands the `services` wiring, so each goes RED if its line is removed) by appending to `nextseek_api/cc_assistant/tests/test_cc_newest_jsonl.py`:

```python
from pathlib import Path

_NSAPI = Path(__file__).resolve().parents[2]   # .../nextseek_api


def test_cc_engine_actually_invokes_on_turn_complete():
    """RED if run_cc_turn's persist block never CALLS the callback (Task 11 Step 2).
    Reads by path (no import) so it stays Django-free/hermetic."""
    src = (_NSAPI / "cc_assistant" / "cc_engine.py").read_text()
    assert "on_turn_complete(TurnCompletePayload(" in src


def test_services_wires_append_cc_turn_complete_into_run_cc_turn():
    """RED if services/cc_assistant.py stops passing the real writer (Task 11 Step 3)."""
    src = (_NSAPI / "services" / "cc_assistant.py").read_text()
    assert "on_turn_complete=_append_cc_turn_complete" in src
```

Then run `test_newest_jsonl_under_*` + the two guards + regression suite:
`uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_newest_jsonl.py nextseek_api/cc_assistant/tests/`
- [ ] **Step 5:** Commit bundled with Task 11a.

---

### Task 11a: CC `chat_log` turn writer (reload hydration) — **execute before Task 11 Step 2**

**Why:** `get_session` builds turns from `extra_state["chat_log"]` only — without this task, Task 7 `Turn.cc_traces` and Task 12 reload hydration **cannot work**.

**Reload vs E5:** **`chat_log[].cc_traces` is the reload source of truth** for the UI. Also mirror append to `extra_state["cc_traces"]` per locked E5 (both stores updated in one RMW save).

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (`on_turn_complete` helper)
- Modify: `nextseek_api/services/assistant.py` — projection reads `entry.get("artifacts")` when `bundle_id` absent (CC turns)
- Test: `nextseek_api/cc_assistant/tests/test_cc_chat_log_writer.py` (grep/source guard for `assistant_reply` key)

**Interfaces:**
- `def _append_cc_turn_complete(payload: TurnCompletePayload) -> None` — single RMW: append `chat_log` entry `{user_query, assistant_reply, mode: "cc", ts, artifacts, cc_traces, turn_id}` (**FIFO cap 50 turns** applied via the pure `cc_turn_complete.append_capped` helper, `cap=MAX_CC_CHAT_LOG_TURNS`, matching `chat_nextseek/chat_memory.py:MAX_TURNS`); **also mirror** the per-turn trace into `es["cc_traces"]` per locked **E5** (mandatory — both stores written in the **SAME** single RMW save; kept small per §6.5 since it loads on every session read), while `chat_log[].cc_traces` remains the reload source of truth that reload/projection read from; upsert `CCSessionTranscript` using payload keys.
- Both the append+cap and the dual-store (chat_log + `es["cc_traces"]` mirror) RMW transform are **NOT** inline in this Django-importing helper — they live in the neutral, hermetically-importable `cc_turn_complete` module (`append_capped` + `apply_turn_to_extra_state`, Task 11) so the 50-turn boundary **and** the E5 mirror are unit-tested (the live gate covers neither cheaply; an inline `chat_log[:50]` mutation or a dropped `es["cc_traces"]` write would otherwise pass every hermetic check).
- Hermetic grep guard: CC `chat_log` entries must use key `assistant_reply` (not `reply`).

- [ ] **Step 1:** Write failing tests (pure helpers extracted from the writer; all Django-free):
  1. `serialize_cc_chat_log_entry(payload)` returns a dict with **`mode: "cc"`**, `assistant_reply`, `artifacts`, `cc_traces`, `turn_id`, `user_query`, `ts` keys.
  2. **FIFO cap (mutation-sensitive)** — feed >50 entries one at a time through `append_capped` and assert the **newest 50** are kept **in order**, so a `chat_log[:50]` (keep-oldest) mutation FAILS.
  3. **E5 mirror (mutation-sensitive)** — `apply_turn_to_extra_state` writes the per-turn trace to **both** `chat_log[].cc_traces` **and** `es["cc_traces"]` in one transform, so a dropped mirror append FAILS (`test_apply_turn_writes_chat_log_and_cc_traces_mirror`):

```python
# nextseek_api/cc_assistant/tests/test_cc_chat_log_writer.py
"""Hermetic: pure chat_log serialize + FIFO-cap helpers. No Django, no DB."""
from nextseek_api.cc_assistant.cc_turn_complete import append_capped


def test_append_capped_keeps_newest_in_order():
    log: list = []
    for i in range(60):                      # 60 turns through the cap
        log = append_capped(log, {"i": i}, cap=50)
    assert len(log) == 50
    # newest-50 kept, oldest-first: turns 10..59 (NOT 0..49 — a [:50] mutation
    # would keep the OLDEST and this assertion would fail).
    assert [e["i"] for e in log] == list(range(10, 60))


def test_append_capped_under_cap_keeps_all_in_order():
    log: list = []
    for i in range(5):
        log = append_capped(log, {"i": i}, cap=50)
    assert [e["i"] for e in log] == [0, 1, 2, 3, 4]


def test_apply_turn_writes_chat_log_and_cc_traces_mirror():
    """Locked SPEC-3 E5 / §6.5: the per-turn trace is written to BOTH chat_log[]
    (reload source of truth) AND the es["cc_traces"] mirror in ONE RMW transform.
    Hermetic + Django-free (TurnCompletePayload is a plain dataclass; chat_session
    is unused by the pure transform). MUTATION-SENSITIVE: deleting the
    `es["cc_traces"]` mirror append in apply_turn_to_extra_state must FAIL the
    second assertion (KeyError), and dropping the chat_log carry must fail the first."""
    from nextseek_api.cc_assistant.cc_turn_complete import (
        TurnCompletePayload, apply_turn_to_extra_state)
    trace = {"cc_session_id": "s", "ts": "t", "steps": []}
    payload = TurnCompletePayload(
        chat_session=None, user_query="q", assistant_reply="a", ts="t",
        artifacts=None, cc_traces=[trace], turn_id="T1",
        cc_session_id="s", raw_jsonl=b"")
    es = apply_turn_to_extra_state({}, payload, cap=50)
    assert es["chat_log"][-1]["cc_traces"] == [trace]   # reload source of truth (unchanged)
    assert es["cc_traces"] == [trace]                    # locked-E5 mirror (restored + guarded)
```

Paste-ready helpers in `services/cc_assistant.py` (import `TurnCompletePayload`, `serialize_cc_chat_log_entry` from `cc_turn_complete.py`):

```python
from nextseek_api.cc_assistant.cc_turn_complete import (
    TurnCompletePayload, serialize_cc_chat_log_entry, append_capped,
    apply_turn_to_extra_state)
from nextseek_api.cc_assistant import cc_transcript_store
from nextseek_api.assistant.models_db import CCSessionTranscript

MAX_CC_CHAT_LOG_TURNS = 50  # match chat_nextseek/chat_memory.py MAX_TURNS


def _append_cc_turn_complete(payload: TurnCompletePayload) -> None:
    session = payload.chat_session
    # Single RMW: the pure helper appends the turn to BOTH chat_log (reload source of
    # truth) AND the locked-E5 es["cc_traces"] mirror; one save persists both
    # (cc_assistant.py:65-72 RMW pattern — never mutate session.extra_state in place).
    session.extra_state = apply_turn_to_extra_state(
        session.extra_state, payload, cap=MAX_CC_CHAT_LOG_TURNS)
    session.save(update_fields=["extra_state", "updated_at"])
    CCSessionTranscript.objects.update_or_create(
        chat_session=session,
        cc_session_id=payload.cc_session_id or "",
        turn_id=payload.turn_id,
        defaults={
            "blob": cc_transcript_store.compress(payload.raw_jsonl),
            "uncompressed_size": len(payload.raw_jsonl),
        },
    )
```

- [ ] **Step 2:** Land the paste above + wire as `on_turn_complete` (**must land before Task 11 Step 2**).
- [ ] **Step 3:** Extend Turn projection: `artifacts = entry.get("artifacts") or (extract_table_artifacts(bundle) if bundle else None)`.
- [ ] **Step 4:** Verify: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_chat_log_writer.py`
- [ ] **Step 5:** Commit bundled with Task 11 or immediately after.

---

### Task 12: frontend — upload control, activity panel, artifact download branch, 3e session-id

**Files:**
- Create: `chat_frontend/src/components/ChatPanel/UploadControl.tsx` (+ `.test.tsx`)
- Create: `chat_frontend/src/components/ChatPanel/CCActivityPanel.tsx` (+ `.test.tsx`)
- Modify: `MessageInput.tsx`, `MessageBubble.tsx`, `EmbeddedApp.tsx`, `AppLayout.tsx`, `lib/services/chatApi.ts`, `lib/types/chat.ts`, `lib/types/api.ts`, `hooks/useMessages.ts`, `hooks/useChatApi.ts`, `components/ChatPanel/ReportArtifacts.tsx`

**Interfaces:**
- `lib/types/chat.ts`: add **`CCTraceStep`** (do **not** reuse existing `Step` used by `ProcessingStepper`) + `CCTrace` TS types mirroring enriched §6.2 — `CCTraceStep { line, kind: "bash"|"write"|"edit"|"read"|"skill"|"tool"|"text", tool?, detail?, text?, action?, status? }` and `CCTrace { schema_version, cc_session_id, ts, transcript_line_count, turn_count, num_turns?, duration_ms?, cost_usd?, steps: CCTraceStep[], tools_used, files_created, files_modified }` + `ccTraces?: CCTrace[]` and **`mode?: string`** on `Message`.
- `lib/types/api.ts`: add `cc_traces?: CCTrace[]` to `Turn`; extend `QueryCompleteData` with `cc_traces?: CCTrace[]` and **`mode?: "cc" | "ns"`** (CC path emits `"mode": "cc"` from `cc_engine.py` on every `query_complete`).
- `chatApi.ts` → **`lib/services/chatApi.ts`**: paste-ready methods:

```typescript
async uploadFiles(files: File[]): Promise<{ job_id: string }> {
  const fd = new FormData();
  files.forEach((f) => fd.append("file", f));
  const r = await fetch("/nextseek_api/cc-assistant/upload/", { method: "POST", body: fd, credentials: "include" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async pollUpload(jobId: string): Promise<{ state: string; result?: unknown }> {
  const r = await fetch(`/nextseek_api/cc-assistant/upload/status/${jobId}/`, { credentials: "include" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async downloadCcArtifact(sessionId: string, key: string): Promise<void> {
  const r = await fetch(`/nextseek_api/cc-assistant/artifacts/${sessionId}/download/?key=${encodeURIComponent(key)}`, { credentials: "include" });
  if (!r.ok) throw new Error(await r.text());
  const blob = await r.blob();
  const disposition = r.headers.get("Content-Disposition");
  const filenameMatch = disposition?.match(/filename="?(.+?)"?$/);
  const filename = filenameMatch?.[1] ?? key.split("/").pop() ?? "artifact";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
```

Vitest: assert fetch URLs match Task 9/10 routes (no stub-only `Promise.resolve()`).

Spec refs: §4 (upload UI), §6 (panel survives reload), §5 (download), §9 (3e).

- [ ] **Step 0: Extend `useChatApi` — expose synchronous session id + shared service**

`AppLayout` must use **one** `NextseekApiService` ref — delete `const [service] = useState(() => new NextseekApiService(...))`; pass `apiService` from `useChatApi` into `useSessions({ service: apiService, ... })`. Grep guard: `AppLayout.tsx` contains exactly one `new NextseekApiService(`.

`useChatApi.ts` sets `serviceRef.current._sessionId` on HTTP 202 (`chatApi.ts:96`) but React `sessionId` state updates only in `.finally()` after WS completes — too late for `query_complete`. Add to hook return:

```ts
getAuthoritativeSessionId: () => serviceRef.current.sessionId,
apiService: serviceRef.current,  // or return serviceRef for ref stability
```

Both layouts must use **`getAuthoritativeSessionId()`** (or the shared `apiService.sessionId`) at `query_complete`, not hook `sessionId` state.

- [ ] **Step 1 (3e first): promote the authoritative session id**

In `EmbeddedApp.tsx` and `AppLayout.tsx` `query_complete` handlers:

```ts
        const authSid = getAuthoritativeSessionId() ?? d.session_id;
        if (authSid) {
          if (sessions.pendingNewChat) sessions.promoteCreatedSession(authSid);
          else sessions.refresh();
        }
```

(EmbeddedApp may keep `serviceRef.current.sessionId` if it already holds the submitting ref.)

- [ ] **Step 2: `CCTrace` type + `ccTraces` on Message + hydrate**

In `lib/types/chat.ts` add the `CCTrace` / **`CCTraceStep`** interfaces (§6.2 fields — **not** the existing `ProcessingStepper` `Step` type) and `ccTraces?: CCTrace[]` + **`mode?: string`** on `Message`. In `lib/types/api.ts` add `cc_traces?: CCTrace[]` to `Turn`. In `hooks/useMessages.ts` (`hydrateFromTurns` map), map:

```ts
        ccTraces: turn.cc_traces ?? undefined,
        mode: turn.mode ?? undefined,
```

And in the live `query_complete` handler in **both** `EmbeddedApp.tsx` and `AppLayout.tsx`, attach `artifacts`, `ccTraces`, and **`mode: d.mode ?? "cc"`** from the WS payload (backend sets `"mode": "cc"` on CC turns in Task 6/11 — do **not** hardcode `"cc"` unconditionally or native NS turns mis-route downloads). Mirror EmbeddedApp's `updateLastAssistantMessage({ … artifacts, ccTraces, mode })` pattern; AppLayout must not only `addAssistantMessage(d.reply)`.

- [ ] **Step 3: `CCActivityPanel` component + Vitest test**

Write `CCActivityPanel.tsx` rendering one trace: the header line (num_turns · turn_count · duration · cost), the ordered `steps` (each `kind` icon + `tool`/`detail`, with an error badge when `status === "error"`), files created/modified, and the `tools_used` tally. Colocated test:

```tsx
// CCActivityPanel.test.tsx
import { render, screen } from "@testing-library/react";
import { CCActivityPanel } from "./CCActivityPanel";

test("renders steps and file changes from a trace", () => {
  render(<CCActivityPanel trace={{
    schema_version: "3/trace-v1", cc_session_id: "s", ts: "t",
    transcript_line_count: 6, turn_count: 3, num_turns: 3,
    files_created: ["report.md"], files_modified: [], tools_used: { Bash: 1 },
    steps: [{ line: 2, kind: "bash", tool: "Bash", detail: "ls /data/input", status: "ok" }],
  }} />);
  expect(screen.getByText("ls /data/input")).toBeInTheDocument();   // rendered from steps
  expect(screen.getByText("report.md")).toBeInTheDocument();
});
```

Render the panel from `MessageBubble.tsx` inside the existing collapsible "Search Details" chrome (`:111-159`) when `message.ccTraces?.length` — reuse the toggle; do not overload `debugEntries`.

**Also update `hasSearchDetails`** (`MessageBubble.tsx:77-79`): add `hasCcTrace = !message.isUser && (message.ccTraces?.length ?? 0) > 0` and set `hasSearchDetails = hasDebug || hasExtracted || hasCcTrace` so the collapsible chrome renders for trace-only CC turns.

- [ ] **Step 4: `UploadControl` component + Vitest test + wire into `MessageInput`**

`UploadControl.tsx`: a file-attach button + selected-file list + progress (calls `chatApi.uploadFiles` then polls `pollUpload`). Colocated test asserts files render and the upload callback fires on submit. Insert it into `MessageInput.tsx` near the composer button row (`:70-117`, the `flex items-end gap-2` row).

- [ ] **Step 5: artifact download — branch the null-bundle CC case**

In `MessageBubble.tsx:106`, branch on **`message.mode === "cc"`** (reload CC turns use `bundle_id: 0` from projection — do not use `bundleId != null`):

```ts
  const handleDl = (key: string) =>
    message.mode === "cc"
      ? onCcArtifactDownload?.(key)
      : onArtifactDownload?.(message.bundleId!, key);
```

Vitest: hydrate CC turn with `bundle_id: 0, mode: "cc", artifacts: [...]` → CC handler fires.

Wire `onCcArtifactDownload` from `EmbeddedApp`/`AppLayout` using the **same submitting `NextseekApiService`** from Step 0: `() => apiService.downloadCcArtifact(apiService.sessionId!, key)` (void — mirrors native `downloadArtifact`; do not await/use return Blob). Do not use AppLayout's separate `useSessions` `service` state.

**AppLayout checklist (required):** extend `MessageBubble`, `MessageList`, and `ChatPanel` props with `onCcArtifactDownload?: (key: string) => void` (keep existing `onArtifactDownload?: (bundleId: number, key: string) => void` for native). Pass both through `ChatPanel` → `MessageBubble`. AppLayout must wire **both** handlers (native + CC) — currently passes no artifact handler to `ChatPanel` at `:190-196`.

- [ ] **Step 6: Run the frontend unit tests**

Run: `cd /home/taishajo/work/NExtSEEK/chat_frontend && npm run test`
Expected: PASS (new `CCActivityPanel`/`UploadControl` specs + existing suite green). Fix type errors (`tsc -b` runs in build).

- [ ] **Step 7: Commit**

```bash
git add chat_frontend/src
git commit -m "feat(cc-step3): upload control, CC activity panel, artifact download branch, authoritative session id (§4,§5,§6,§9)"
```

---

### Task 13: deploy (zstd dep, migration, frontend build) + live verification gate

**Files:** image deps (`zstandard`), `nextseek_api/cc_assistant/DEPLOY.md`, built frontend bundles. Operational — no hermetic test.

**Pre-req:** explicit **per-change sign-off** before touching the running instance. Use the Step-0 deploy procedure via the **service-account `docker:cli` helper** (snapshot `:pre-step3` → fast-forward the SA build-context clone → rebuild → recreate `--no-deps nextseek`). Rollback via `/home/taishajo/work/state/rollback.sh`.

Spec refs: §12 (live verification), §13 (deployment), E7 (zstd dep), E6 (migration).

- [ ] **Step 0: Celery/broker preflight (before deploy)**

```bash
docker exec nextseek celery -A nextseek_api.batch_upload.celery_app inspect ping
docker exec nextseek celery -A nextseek_api.batch_upload.celery_app inspect registered | grep cc_assistant.upload
```

Both must exit 0. Record broker reachability note in evidence if non-default `CELERY_BROKER_URL`.

- [ ] **Step 1: Add `zstandard` to the image**

Add `zstandard` to the image's Python deps (`pyproject`/requirements that the Dockerfile installs) so `cc_transcript_store` imports at runtime. Confirm the dmac venv inside the image gets it on rebuild. Record in evidence: `docker exec nextseek python -c "import zstandard; print(zstandard.__version__)"`.

- [ ] **Step 3b: Append Step-3 deploy notes to `nextseek_api/cc_assistant/DEPLOY.md`**

Before **Task 13 Step 3** (snapshot + deploy), append (do not replace PLAN-7 Phase A/B — merge order per PLAN-7 gate):
- `python manage.py migrate nextseek_api 0007_ccsessiontranscript`
- Celery worker must register `cc_assistant.upload` (`celery inspect registered | grep cc_assistant.upload`)
- `cd chat_frontend && npm run build:embedded` before image rebuild
- `:pre-step3` snapshot tag procedure reference
- zstandard import check inside container

- [ ] **Step 2: Build the embedded frontend bundles**

Run: `cd /home/taishajo/work/NExtSEEK/chat_frontend && npm run build:embedded`
Output: `../static/js/chat_assistant/` (relative to `chat_frontend/`) with a fingerprinted `manifest.json`. Confirm the new bundles are emitted before the image build copies `static/`.

- [ ] **Step 3: Snapshot + deploy**

Tag the live image `nextseek-nextseek:pre-step3` (SA helper). Rebuild + recreate per Step-0. Apply the migration inside the running container against the real DB:
```bash
docker exec nextseek python manage.py migrate nextseek_api 0007_ccsessiontranscript
```
Confirm: container up gunicorn+celery, site 200, `cc_engine.cc_runner_available() == (True, "ok")`.

- [ ] **Step 4: Live upload → input → CC reads it**

Drive the chat UI with Playwright (see `nextseek-playwright.md`), forced-CC, ≤ $2 cap: upload a file via the new control → **assert the uploaded file actually lands** in `<root>/<project>/demo/input/` on the host (`docker exec`/host `ls -l` of that `input/` dir showing the file present and non-empty) → a CC turn reads it (RO at `/data/input`). **This `ls` assertion is the live gate for the cross-device `shutil.move` (2A HIGH): an `os.replace` regression would `EXDEV`-fail the Celery job and leave `input/` empty here.** Also capture the upload job's final `state` (must be `SUCCESS`, not `FAILURE`) in `live_gate_transcript.txt`.

- [ ] **Step 5: Output split + download + raw + transcript**

A CC turn writes a deliverable to `scratch/` and something to `scratch/raw/`: confirm the deliverable appears in `output/artifacts/<turn_id>/`, downloads via the UI button (zip if >1), the raw file lands in `output/raw/`, **transcript copy** at `output/raw/transcript-<turn_id>.jsonl`, and `GET …/transcript/<session>/<turn>/?cc_session_id=…` returns the full jsonl (zstd round-trip).

- [ ] **Step 5b: Two-turn same-basename download check**

Two forced-CC turns each write `report.md`; reload shows both keys; download each turn's button returns **different** bytes (proves turn-scoped artifact namespace).

- [ ] **Step 6: Activity panel survives reload**

Confirm the panel shows commands/files/num_turns live, then **reload the session** and confirm the panel is still populated (proves `cc_traces` persisted + hydrated, unlike native ephemeral `debugEntries`). Save a **JSON excerpt** in `live_gate_transcript.txt` from `GET /assistant/sessions/{id}?include=turns` showing non-empty `turns[*].cc_traces` **and** `chat_log`-backed `assistant_reply` on the CC turn (scripted `jq` — not UI prose alone). **This non-empty-`cc_traces`-after-reload assertion is the acceptance gate for persistence** (Task 11 is now best-effort on the success path — a persist failure no longer surfaces as `query_error`, so the reload assertion is what must catch it). Run this gate with `CC_PERSIST_STRICT=True` if a hard, immediate failure on persist miss is preferred.

- [ ] **Step 7: 3e + no regressions**

New chat, second turn does not 404 (3e). Re-run the **1b resume** A/B and the **1c memory** live check (per their evidence docs) at the nested paths — confirm no regression. Use an on-domain agentic prompt for 1c recall (the router gates content-free recall by design).

- [ ] **Step 8: Record evidence + flip the tracker**

Write live evidence under `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/` including:
- `live_gate_transcript.txt` — saved stdout/stderr + exit codes for every Task 13 command. **Ensure it contains the PLAN-7 §8 content-marker allowlist** that PLAN-7 Task 2's validator (PLAN-7:132) greps — these are the saved **stdout** strings (not the command lines; PLAN-7 explicitly does **not** grep the command substrings `migrate nextseek_api 0007` or `inspect registered`). **Byte-identical allowlist, shared verbatim with PLAN-7 Task 2 — both sides MUST name the same strings:**
  1. **Migration marker — `Applying nextseek_api.0007` OR `[X] 0007_ccsessiontranscript`** (at least one). Step 3's `migrate nextseek_api 0007_ccsessiontranscript` emits `Applying nextseek_api.0007_ccsessiontranscript… OK` **only when 0007 is unapplied**; on an already-applied DB it prints `No migrations to apply.` and that form is absent. **Therefore also run `python manage.py showmigrations nextseek_api` (`docker exec nextseek …`) and save its stdout into `live_gate_transcript.txt`** — it prints `[X] 0007_ccsessiontranscript` on **any** already-applied DB, so the migration marker is present whether or not 0007 was freshly applied (idempotency-robust; an already-deployed instance's committed transcript can never permanently fail PLAN-7's start-gate).
  2. **`cc_assistant.upload`** — from Step 0 `celery … inspect registered | grep cc_assistant.upload` stdout (registered-task name).
  3. **`cc_traces`** — the JSON key in the Step 6 saved `GET …?include=turns` excerpt.

  These markers are produced by the steps above plus the one added `showmigrations nextseek_api` capture; this makes the cross-target handshake explicit so the file is not committed missing a marker that PLAN-7's hard start-gate greps.
- `playwright/` — optional screenshots (secret-scanned)
- `nextseek_api/cc_assistant/evidence/3-ui-based-io-live.md` — index only (links to generated artifacts; not proof by itself)

Success is met only if reload shows non-empty `cc_traces` on the CC turn (Task 13 Step 6) and upload Celery job completes (Task 13 Step 4). With the user's OK, set `integration-plan.json` step **3** (and substeps 3a–3e) `status` → `done` (status field ONLY; never add keys). Capture the session with `/handoff`.

- [ ] **Step 9: Commit deploy artifacts + evidence + nextseek_api/cc_assistant/DEPLOY.md**

Secret-scan `live_gate_transcript.txt` and any Playwright artifacts before commit. **Hard gate for Step 7:** this file **must** be committed on the integration branch (SPEC-7 §8 / PLAN-7 Task 1 — user decision 2026-06-30).

```bash
git add nextseek_api/cc_assistant/DEPLOY.md \
  nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt \
  nextseek_api/cc_assistant/evidence/3-ui-based-io-live.md \
  <image dep file> static/js/chat_assistant
git commit -m "chore(cc-step3): zstandard dep, migration 0007, embedded frontend build + live gate evidence (§12,§13)"
```

---

## Self-Review

**1. Spec coverage:**
- §4 upload (E1/E2) → Task 3 (`input_mnt`), Task 9 (endpoint+task+status), **Task 9b (list)**. ✔
- §5 output split + download (E3/E9) → Task 6 (split+zip), Task 10 (download endpoint), Task 12 Step 5 (UI branch). ✔
- §6 activity panel (E4/E5/E10) → Task 4 (schema+extractor), Task 5 (result meta), Task 7 (Turn field), **Tasks 11+11a (persist + chat_log + live emit)**, Task 12 Step 3 (panel). ✔
- §6.4 translate extension → Task 5. ✔
- §7 transcript recoverability (E6/E7) → Task 1 (zstd), Task 2 (model+migration), Task 10 (recover endpoint), Task 11 (write path). ✔
- §8 remove Dropbox (E8) → Task 8. ✔
- §9 authoritative session id (3e) → Task 12 Step 1. ✔
- §10 isolation/security → owner-scoping (Tasks 9/10), filename validation (Task 9), bomb bound (Task 1), no-cred-to-agent (Tasks 9/10 run host-side). ✔
- §12 testing → every task's hermetic seam + Task 13 live. ✔  §13 deployment → Task 13. ✔
- §14 out-of-scope (multi-project, shared population, upload delete/quota, ingestion, re-summarization) — intentionally excluded. ✔

**2. Placeholder scan:** `[CONFIRM@PLAN]` items resolved in Phase 2 vetting: (a) `CCStreamTranslator` confirmed; (b) Celery `app` from `batch_upload.celery_app` + explicit worker import; (c) persist relocated to `cc_engine.run_cc_turn` + Task 11a `chat_log` writer. Task 4 `_Other.type: str | None` intentionally tolerates `parse_transcript`'s `{"_type":"unparsed"}` lines (SPEC §6.3 prose uses `type: str`; plan follows real jsonl shapes).

**Known coupling note:** Tasks 6 and 8 **must land atomically** on `cc_engine.py` `query_complete` handler (Phase 2 hardened). Tasks 11/11a/13 have no hermetic seam by design — Task 13 live gate is the acceptance bar for Step 3.

---

## Permissions Required

| Permission / resource | Tasks | Notes |
|----------------------|-------|-------|
| Hermetic pytest via `uv run` | 1–7, 9 validator, 9b | No DB CREATE on this box |
| Django `makemigrations` (no migrate) | 2 | Reads models only |
| Celery broker + `batch_upload` queue | 9, 13 Step 4 | Worker must register `cc_assistant.upload` |
| `MEDIA_ROOT` staging dir write | 9 | Upload temp files before Celery move |
| Host filesystem: `DMAC_USER_ROOT` / mount roots | 3, 9, 10, 11 | `input_mnt`, `output/artifacts`, `cc-state` jsonl |
| Django ORM + real DB migrate | 2, 11, 13 | `CCSessionTranscript`, `chat_log` persist |
| DRF endpoint exposure (owner-scoped) | 9, 9b, 10 | Requires logged-in SEEK user |
| Docker socket + image rebuild + container recreate | 13 | Step-0 deploy procedure; per-change sign-off |
| Playwright / live chat UI | 13 | ≤ $2 forced-CC cap |
| Frontend `npm run test` + `build:embedded` | 12, 13 | Vitest + static bundle emit |
| Git write on `cc-step3-ui-io` branch | all | Merge back to `feat/dmac-assistant-full-integration` on completion |

Resolved in Phase 5.5 before execution.

---

## Risk Register

| Rank | Task | Likely failure | Catastrophic failure | Rollback |
|------|------|----------------|---------------------|----------|
| 1 | 11/11a | Persist in wrong module; chat_log never written → reload empty | Users trust panel data that vanishes on reload | Pause; fix before deploy |
| 2 | 9 | Celery task not registered → upload 202 but job never runs | Silent I/O regression | Roll back celery_app import + endpoints |
| 3 | 4 | `classify_tool_use` drift breaks 1c memory strings | Cross-session memory corrupt | Revert cc_summary; block Step 3 merge |
| 4 | 6+8 | Partial handler edit leaves Dropbox + dict mismatch | Broken CC turns mid-deploy | Atomic revert of cc_engine handler |
| 5 | 2/11/13 | Migration not applied → ORM errors at runtime | DB inconsistency | `rollback.sh`; do not flip tracker |
| 6 | 10 | Owner-scoping bypass on download/recover | Cross-user data leak | Block deploy immediately |
| 7 | 12/13 | Frontend bundle not rebuilt → stale UI | Appears done while UI unchanged | Rebuild embedded + recreate container |
| 8 | 13 | Swallowed persist exceptions | False "done" with failed live gate | Task 13 must assert non-empty `cc_traces` after reload |
| 9 | 11 | Best-effort persist silently delivers replies without a saved trace if a jsonl/path mismatch persists post-gate | Panel empty on reload in prod despite successful turns (paid output preserved, but trace lost) | Error-level log on every persist miss; Task 13 reload assertion (row 8) gates it; run the gate with `CC_PERSIST_STRICT=True` for a hard signal. Best-effort guarantees a paid reply is **never** turned into `query_error` (no spend-doubling retry) |

Hidden dependencies: Step 2 multi-user paths (`UserDirs`, `resolve_user_project`); 1b resume cc-state mounts; 1c `cc_summary` output byte-identical after Task 4.

---

## Dependency Validation

| Dependency | Validation | Status |
|------------|------------|--------|
| `zstandard` (PyPI ≥0.25) | [pypi.org/project/zstandard](https://pypi.org/project/zstandard/) — `ZstdDecompressor.stream_reader` supports bounded reads (Task 1 bomb guard) | OK — add to pyproject Task 1 |
| pydantic v2 unpinned | Ordered `Union` with `_Other` last; `TypeAdapter.validate_python` | OK — plan mitigates |
| orjson | Already used in 1c tests | OK |
| Celery `batch_upload.celery_app` | `views.py:23` pattern; explicit import required (Phase 2) | OK — hardened in Task 9 |
| Vitest / `npm run build:embedded` | `chat_frontend/package.json` | OK |
| Django migration 0007 | Depends on `0006_merge_extra_state_guards` | OK |
| Playwright live gate | External; Task 13 only | Accepted exception |

---

## Gameability Audit

| Task | Success condition (as written) | Cheapest fake | Remedy |
|------|-------------------------------|---------------|--------|
| 2 | Model-shape guard without DB | Source-text grep only | Live migrate in Task 13; shape guard kept |
| 9 | Validator tests pass | Broken Celery body + missing worker import | Task 9 Step 3b + Task 13 Step 4 live upload |
| 11 | "Hermetic regression only" | Swallow persist errors | Best-effort: re-raise **only** under `CC_PERSIST_STRICT` (dev/test), else log+deliver; Task 13 reload asserts `cc_traces` |
| 11a | chat_log append | Write to wrong key | Grep + Task 13 reload hydration |
| 13 | "Confirm panel survives reload" | Agent prose in markdown | Require `evidence/3-ui-based-io-live/live_gate_transcript.txt` + saved Playwright output |
| 8 | grep-guard | Dropbox string moved to comment | Scan whole `cc_assistant/` + `services/cc_assistant.py` |
| 4 | Fixture jsonl tests | Overfit extractor to fixture | Add second fixture with different tool names; 1c full suite gate |

Deterministic validators per task: hermetic pytest for pure modules; Task 13 live gate for DB/HTTP/Docker paths.

---

## Phase 2 Vetting Log

| Iteration | Reviewer | Verdict | MEDIUM+ resolved |
|-----------|----------|---------|------------------|
| 1 | Independent cold-context (2026-06-30) | CONDITIONAL_ACCEPTANCE | 5 Critical, 11 Important — hardening applied |
| 2 | Fresh re-vet (2026-06-30, iter 2) | CONDITIONAL_ACCEPTANCE | Celery commit path, chat_log/E5 clarity, Task 9b gate, second fixture |
| 3 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-2 residual fixes applied |
| 4 | Fresh re-vet (2026-06-30, iter 3) | **CONDITIONAL_ACCEPTANCE** | 3 HIGH, 10 MEDIUM — see `.vetting/plan-3-phase2-review-3-fresh.md` |
| 5 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-3 HIGH fixes |
| 6 | Fresh re-vet (2026-06-30, iter 4) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, 8 MEDIUM — see `.vetting/plan-3-phase2-review-4-fresh.md` |
| 7 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-5: useChatApi sync session id, AppLayout live path, coverage scope |
| 8 | Fresh re-vet (2026-06-30, iter 5) | **CONDITIONAL_ACCEPTANCE** | 3 HIGH, 8 MEDIUM — see `.vetting/plan-3-phase2-review-5-fresh.md` |
| 9 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-5: useChatApi sync id, AppLayout parity, Task 9b TDD, coverage, evidence commit |
| 10 | Fresh re-vet (2026-06-30, iter 6) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 6 MEDIUM — see `.vetting/plan-3-phase2-review-6-fresh.md` |
| 11 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-6: turn-scoped artifacts, Task 11 else branch, 11a order, jsonl raw copy |
| 12 | User decisions (2026-06-30) | **Locked** | Turn-scoped artifact namespace; live transcript commit required for Step 7 gate |
| 13 | Fresh re-vet (2026-06-30, iter 7) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 10 MEDIUM — see `.vetting/plan-3-phase2-review-7-fresh.md` |
| 14 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-7: zip-if-multiple, bundleId reload, pytest-cov, realstack, guards |
| 15 | Fresh re-vet (2026-06-30, iter 8) | **CONDITIONAL_ACCEPTANCE** | 3 HIGH, 10 MEDIUM — see `.vetting/plan-3-phase2-review-8-fresh.md` |
| 16 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-8: Task 10 paste fix, raw path strip, recover cc_session_id, realstack in commit |
| 17 | Fresh re-vet (2026-06-30, iter 9) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, 9 MEDIUM — see `.vetting/plan-3-phase2-review-9-fresh.md` |
| 18 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-9: `_newest_jsonl_under`, guards, zip relpaths, validate_cc_acceptance commit |
| 19 | Fresh re-vet (2026-06-30, iter 10) | **CONDITIONAL_ACCEPTANCE** | 4 HIGH, 8 MEDIUM — see `.vetting/plan-3-phase2-review-10-fresh.md` |
| 20 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-10: min_mtime retry, run_cc_turn wiring, guards paste, zip test, validator |
| 21 | Fresh re-vet (2026-06-30, iter 11) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 6 MEDIUM — see `.vetting/plan-3-phase2-review-11-fresh.md` |
| 22 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-11: persist fence fix, _turn_start_ts, validator paste, AppLayout service |
| 23 | Fresh re-vet (2026-06-30, iter 12) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 2 HIGH, 6 MEDIUM — see `.vetting/plan-3-phase2-review-12-fresh.md` |
| 25 | Fresh re-vet (2026-06-30, iter 13, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 7 HIGH, 10 MEDIUM — see `.vetting/plan-3-phase2-review-13-fresh.md` |
| 26 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-13: job_index ownership, persist copy2, 11a paste, _turn_start_ts step, chatApi paste, DEPLOY notes |
| 27 | Fresh re-vet (2026-06-30, iter 14, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 8 MEDIUM — see `.vetting/plan-3-phase2-review-14-fresh.md` |
| 28 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-14: CCTraceStep rename, Message.mode hydrate, 11a update_fields, Task 11 single persist owner |
| 29 | Fresh re-vet (2026-06-30, iter 15, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 3 HIGH, 10 MEDIUM — see `.vetting/plan-3-phase2-review-15-fresh.md` |
| 30 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-15: cc_turn_complete.py, realstack artifacts dump, newest_jsonl test, AppLayout single service |
| 31 | Fresh re-vet (2026-06-30, iter 16, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 4 MEDIUM — see `.vetting/plan-3-phase2-review-16-fresh.md` |
| 32 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-16: serialize_cc_chat_log_entry paste, realstack lines 181–212, File Structure inventory, decompress max_bytes, upload staging cleanup |
| 33 | Fresh re-vet (2026-06-30, iter 17, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 3 HIGH, 2 MEDIUM — see `.vetting/plan-3-phase2-review-17-fresh.md` |
| 34 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-17: future annotations, chat_log FIFO cap, mode=cc payload, downloadCcArtifact void, duplicate upload reject |
| 35 | Fresh re-vet (2026-06-30, iter 18, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 3 MEDIUM — see `.vetting/plan-3-phase2-review-18-fresh.md` |
| 36 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-18: celery-free `cc_upload_validate.py` (Task 9), `os.utime` newest-jsonl test (Task 11), `settings` import in `recover_transcript` (Task 10), per-task `--cov-fail-under=95` wiring (Tasks 1/3/4/7/9/9b); Task 5 translate floor **escalated** as a documented exception (live gate covers it) — see `.vetting/plan-3-phase2-fix-log-iter18.md` |
| 37 | Fresh re-vet (2026-06-30, iter 19, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, 3 MEDIUM — see `.vetting/plan-3-phase2-review-19-fresh.md`; **Task 5 translate coverage exception adjudicated LEGITIMATE (PASS)** |
| 38 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-19: `os.replace`→`shutil.move` cross-device (Task 9) + Task 13 `ls` gate; FIFO cap extracted to neutral `cc_turn_complete.append_capped` + mutation test (Task 11a); `run_cc_turn` final order spelled out (Task 6 Step 6); success-path persistence made best-effort behind `CC_PERSIST_STRICT` so a paid reply is never discarded (Task 11) — see `.vetting/plan-3-phase2-fix-log-iter19.md` |
| 39 | Fresh re-vet (2026-06-30, iter 20, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 3 MEDIUM — see `.vetting/plan-3-phase2-review-20-fresh.md`. **HIGH-1 = locked-design conflict (`cc_traces` mirror: SPEC-3 E5/§6.5 mandates it, but iter-17 dropped it) → ESCALATED to user.** HIGH-2 = cross-plan marker handshake (Task 13 Step 8 ↔ PLAN-7:132). Task 5 + Task 9-pragma exceptions re-adjudicated LEGITIMATE |
| 40 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-20: **user decided RESTORE `cc_traces` mirror** (thread C) — 4 sites reconciled + dual-store write extracted to pure `cc_turn_complete.apply_turn_to_extra_state` with mutation-RED guard test; persist-block missing imports added; projection-passthrough guard test; fixture `# <path>` trap removed (`line_count==6`). Marker handshake (thread B) hardened by the PLAN-7 single-owner pass (Task 13 Step 8 ↔ PLAN-7:132 byte-identical). See `.vetting/plan-3-phase2-fix-log-iter20.md` + `.vetting/defect-lineage.md` |
| 41 | Fresh re-vet (2026-06-30, iter 21, **canonical prompt, un-steered**) | **CONDITIONAL_ACCEPTANCE** | **0 HIGH**, 2 MEDIUM, 2 LOW — see `.vetting/plan-3-phase2-review-21-fresh.md`. Threads **B + C CLOSED** (not re-raised; mirror + marker handshake verified). New: persist→reload wiring unguarded (2D), thread **E** (Task 11 Step 1 unconditional raise vs locked best-effort). Coverage exceptions re-confirmed legitimate |
| 42 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-21: two mutation-RED source guards on the persist→reload wiring (`on_turn_complete(TurnCompletePayload(` in cc_engine; `on_turn_complete=_append_cc_turn_complete` in services); **thread E** reconciled (no unconditional-raise wording remains); Task 6 Step 5b coverage run includes both `test_cc_artifacts_split.py` + `test_cc_engine_publish.py`; inventory paths fixed. See `.vetting/plan-3-phase2-fix-log-iter21.md` |
| 43 | Fresh re-vet (2026-06-30, iter 22, **canonical prompt, un-steered**) | **CONDITIONAL_ACCEPTANCE** | **0 HIGH, 1 MEDIUM**, 1 LOW — see `.vetting/plan-3-phase2-review-22-fresh.md`. Threads **B/C/E re-confirmed CLOSED** (post-reload `cc_traces` DB read closes best-effort gameability). New: Task 9b `ProjectResolutionError` local-import missing (NameError on verbatim paste). All coverage exceptions re-confirmed legitimate |
| 44 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-22: Task 9b local import → byte-identical to Task 9 `upload` (`resolve_user_project, ProjectResolutionError, build_user_dirs`) + Task-10 ordering note; Task 6 Step 5b `_publish_artifacts` test bodies pasted (turn_id kw-only, dict return, `output/artifacts/<turn_id>/`, +zip/raw-split test). See `.vetting/plan-3-phase2-fix-log-iter22.md` |
| 45 | Fresh re-vet (2026-06-30, iter 23, **canonical prompt, un-steered**) | **UNCONDITIONAL_ACCEPTANCE** | **0C / 0H / 0M / 0L** (4 cosmetic only) — see `.vetting/plan-3-phase2-review-23-fresh.md`. Reviewer empirically verified every load-bearing claim (primitives/signatures, byte-identical `classify_tool_use` refactor, `models_api` 96% measured, zstandard API live-probed, PLAN-7 marker handshake byte-identical) |

**Phase 2 status: ✅ COMPLETE** — iter-23 independent fresh reviewer returned **UNCONDITIONAL_ACCEPTANCE** (zero MEDIUM+). Per loop rule, a target that returns UA from a fresh reviewer is **not reopened**. Threads A/B/C/E all CLOSED en route. See `.vetting/defect-lineage.md`. **Phase 3 (task-spec writing) is gated on (a) PLAN-7 also completing Phase 2 and (b) a user Phase-2→3 checkpoint — do NOT auto-advance.**
