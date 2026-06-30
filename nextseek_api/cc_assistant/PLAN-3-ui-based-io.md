# Step 3 — UI-based I/O Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vestigial Dropbox-shaped I/O with real UI-based I/O — per-user uploads into `input/`, a hybrid artifacts/raw output split with in-UI download, a reload-surviving CC activity panel, a zstd-compressed full-transcript record in the DB, removal of the Dropbox copy/laptop path, and the deferred authoritative-session-id fix.

**Architecture:** New host-side pure modules (`cc_transcript_store` for zstd, `cc_trace` for jsonl→trace extraction, `cc_artifacts` for the output partition + zip) keep logic hermetically testable. The existing `_publish_artifacts` (already nested per Step 2) is reworked to split `scratch/raw/` from deliverables; `translate._handle_result` is extended to surface `num_turns`/`duration_ms`; the per-turn `CCTrace` is persisted to `ChatSession.extra_state["cc_traces"]` and the full jsonl to a new `CCSessionTranscript` table. Four new DRF `@action`s on `CCAssistantViewSet` (upload, upload-status, artifact-download, transcript-recover) mirror the established `batch_upload`/SOP-download patterns, all owner-scoped. The frontend gains a file-attach control, a CC activity panel extending "Search Details", a null-bundle artifact download branch, and the HTTP-202-body session-id promotion.

**Tech Stack:** Python 3.12, Django + DRF (host process), Celery (`batch_upload` queue pattern), docker-py (CC sibling), pydantic v2 + orjson (trace), zstandard (transcript), Vite + React + TypeScript + Vitest (frontend), pytest hermetic units via `uv run`.

## Global Constraints

- **Spec of record:** `nextseek_api/cc_assistant/SPEC-3-ui-based-io.md` (locked decisions **E1–E10** in §11). Every task below traces to a spec section. The §6.2 trace schema fields were left "user may edit" — if the user has not edited them at execution time, implement §6.2 verbatim.
- **Builds on Step 2** (`done`, live): project-stratified `<DMAC_USER_ROOT>/<projectID-slug>/<user>/{input,scratch,cc-state,output}` + `<project>/shared/`. Reuse the Step-2 primitives `resolve_user_project`, `build_user_dirs`, `UserDirs`, `ProjectIdentity`, and the validators `_validate_user_id`/`_validate_project`/`_safe_relpath` — do not reinvent them.
- **TDD-first**, bite-sized steps, frequent commits. Implementation code only after a failing test.
- **Hermetic test command (the box cannot run the Django test-DB runner — `seek_db_user` lacks `CREATE`):**
  `uv run --no-project --with pytest --with orjson --with pydantic --with zstandard python -m pytest -q --noconftest <files>`
  Run from `/home/taishajo/work/NExtSEEK`. No Docker, no DB, no network, no spend. (`--with zstandard` matters only for Task 1; `--with orjson --with pydantic` for the trace tests; harmless elsewhere.)
- **DB / migration / endpoint logic is NOT hermetically testable here** (no test DB, no live HTTP in-suite). For those tasks the hermetic test covers the pure seam (validator, partition, zip-builder, compress round-trip, schema extraction); the endpoint/persistence path is proven in the Task 13 live gate (forced-CC, ≤ $2 cap, Playwright, per-change sign-off).
- **No regression** to 1b `--resume`, 1c memory, the NS route, Step 2 isolation, or **OI-3** (zero-creds agent). Upload/download/recover all run host-side in Django as the logged-in user using their own SEEK login; **no new credential reaches the agent**. The agent still sees only `input/` + `shared/` RO and writes only `scratch/` RW.
- **Owner-scoping is mandatory** on every new read endpoint: `ChatSession.objects.filter(user=request.user)` — a user can never fetch another user's artifacts or transcript.
- **Validate-before-interpolate:** every path segment that reaches a filesystem path (uploaded filename, `key`, `session`, `turn`) MUST be validated (`_safe_relpath` / basename / the Step-2 segment validators) before use.
- **`extra_state` write pattern (canonical, `services/cc_assistant.py:65-72`):** `es = dict(sess.extra_state or {})`; mutate `es`; `sess.extra_state = es`; `sess.save(update_fields=["extra_state", "updated_at"])`. Never mutate `sess.extra_state` in place.
- **pydantic is unpinned** in this repo (v2 syntax throughout: `model_validate`/`model_dump`). Use an **ordered `Union` with a catch-all last** for the jsonl record union (do not rely on a discriminated union requiring a specific pin).
- **Per-change sign-off** before touching the running instance. The E8 Dropbox-default change and any dead-config removal ship as their own reviewed diffs.
- **Deadline:** NExtSEEK prod before 2026-07-14.

---

## File Structure

**Create**
- `nextseek_api/cc_assistant/cc_transcript_store.py` — `compress(jsonl: bytes) -> bytes` / `decompress(blob: bytes, *, max_bytes) -> bytes` (zstd; decompression-bomb bound).
- `nextseek_api/cc_assistant/cc_trace.py` — pydantic `Step`/`ToolStep`/`TextStep`/`CCTrace` (§6.2) + the jsonl record union (`_Assistant`/`_User`/`_Other`, §6.3) + `extract_trace(records, *, cc_session_id, ts, files_created, files_modified, result_meta) -> CCTrace`.
- `nextseek_api/cc_assistant/cc_artifacts.py` — `partition_changed(changed: set[str]) -> tuple[set[str], set[str]]` (artifacts vs raw) + `build_artifact_zip(files: list[Path], dest_zip: Path) -> Path` (mirrors `content_blobs.download_batch`).
- Tests under `nextseek_api/cc_assistant/tests/`: `test_cc_transcript_store.py`, `test_cc_trace.py`, `test_translate_result_meta.py`, `test_cc_artifacts_split.py`, `test_cc_provision_input_mnt.py`, `test_turn_cc_traces.py`, `test_cc_upload_validate.py`, `test_cc_dropbox_grep_guard.py`, plus the fixture `tests/fixtures/cc_transcript_sample.jsonl`.
- `nextseek_api/migrations/0007_ccsessiontranscript.py` — additive migration.
- Frontend: `chat_frontend/src/components/ChatPanel/CCActivityPanel.tsx` (+ `CCActivityPanel.test.tsx`), `chat_frontend/src/components/ChatPanel/UploadControl.tsx` (+ `UploadControl.test.tsx`).

**Modify**
- `nextseek_api/cc_assistant/cc_provision.py` — add `input_mnt` to `UserDirs` + `build_user_dirs` (uploads write host-side via the mount).
- `nextseek_api/cc_assistant/translate.py` — `_handle_result` surfaces `num_turns`/`duration_ms` (`:130-156`).
- `nextseek_api/cc_assistant/cc_engine.py` — rework `_publish_artifacts` (`:639`) to the hybrid split; replace the "Saved to your Dropbox" augmentation (`:580-587`) with an `artifacts` channel + trace metadata on `query_complete`.
- `nextseek_api/cc_assistant/cc_config.py` — neutral default `/srv/dmac/users` (`:15`, E8).
- `nextseek_api/services/cc_assistant.py` — four new `@action`s (upload, upload-status, artifact-download, transcript-recover) on `CCAssistantViewSet` (`:114`); persist `CCTrace` + transcript blob in the CC branch.
- `nextseek_api/assistant/models_db.py` — `CCSessionTranscript` model (`app_label='nextseek_api'`).
- `nextseek_api/assistant/models_api.py` — add `cc_traces` to `Turn` (`:122-138`, `extra="forbid"` ⇒ must be declared).
- `nextseek_api/services/assistant.py` — surface `cc_traces` in the Turn projection (`:521-529`).
- `nextseek_api/batch_upload/tasks.py` *(pattern source only — read, do not edit)*; new CC upload task lives in `nextseek_api/cc_assistant/cc_upload_tasks.py`.
- `seek/views.py` — remove dead `DROPBOX_DIRECTORY` (`:94`, audited).
- Frontend: `MessageInput.tsx`, `MessageBubble.tsx`, `EmbeddedApp.tsx`, `AppLayout.tsx`, `lib/api/chatApi.ts`, `lib/types/chat.ts`, `lib/types/api.ts`, `hooks/useMessages.ts`, `ReportArtifacts.tsx`.
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

Run: `uv run --no-project --with pytest --with zstandard python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_transcript_store.py`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/cc_transcript_store.py nextseek_api/cc_assistant/tests/test_cc_transcript_store.py
git commit -m "feat(cc-step3): zstd transcript store with decompression-bomb bound (§7, E7)"
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

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_provision_input_mnt.py nextseek_api/cc_assistant/tests/test_cc_provision_paths.py`
Expected: PASS. Then the whole suite to confirm the additive field broke nothing:
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
- Consumes: `cc_summary.parse_transcript` (existing, `cc_summary.py:46` — returns `ParsedTranscript(raw_lines, records)`; `records` are plain dicts already orjson-decoded).
- Produces (§6.2/§6.3):
  - `ToolStep`, `TextStep`, `Step = Union[ToolStep, TextStep]`
  - `CCTrace` (pydantic) with the §6.2 fields
  - `RECORDS = TypeAdapter(list[Union[_Assistant, _User, _Other]])`
  - `extract_trace(records: list[dict], *, cc_session_id: str, ts: str, files_created: list[str], files_modified: list[str], result_meta: dict | None = None) -> CCTrace`

Spec refs: §6.1 (two sources, do not conflate), §6.2 (schema), §6.3 (orjson + ordered union, `_Other` last), E4/E10.

- [ ] **Step 1: Write the fixture jsonl**

```
# nextseek_api/cc_assistant/tests/fixtures/cc_transcript_sample.jsonl
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"list the input files"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll inspect the inputs."},{"type":"tool_use","name":"Bash","input":{"command":"ls /data/input"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Write","input":{"file_path":"/data/scratch/report.md"}}]}}
{"type":"summary","leafUuid":"abc"}
```

(The 4th line is an unknown record `type` — it MUST fall through to `_Other`, proving forward-compat.)

- [ ] **Step 2: Write the failing tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_trace.py
"""Hermetic trace extraction from a fixture jsonl. orjson + TypeAdapter."""
from pathlib import Path

from nextseek_api.cc_assistant import cc_summary
from nextseek_api.cc_assistant.cc_trace import extract_trace, CCTrace

FIX = Path(__file__).parent / "fixtures" / "cc_transcript_sample.jsonl"


def _records():
    return list(cc_summary.parse_transcript(FIX.read_bytes()).records)


def test_extract_builds_steps_commands_tools():
    t = extract_trace(_records(), cc_session_id="sess-1", ts="2026-06-30T00:00:00Z",
                      files_created=["report.md"], files_modified=[])
    assert isinstance(t, CCTrace)
    # one text step + two tool steps
    kinds = [s.type for s in t.steps]
    assert kinds == ["text", "tool_use", "tool_use"]
    assert t.commands == ["ls /data/input"]
    assert t.tools_used == {"Bash": 1, "Write": 1}
    # authoritative file lists come from the scratch diff (the args), not the jsonl
    assert t.files_created == ["report.md"]
    assert t.files_modified == []


def test_unknown_record_type_does_not_crash():
    # the "summary" line must be tolerated (ordered union, _Other last)
    t = extract_trace(_records(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[])
    assert isinstance(t, CCTrace)


def test_result_meta_is_surfaced():
    t = extract_trace(_records(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[],
                      result_meta={"num_turns": 5, "duration_ms": 1234, "cost_usd": 0.07})
    assert t.num_turns == 5 and t.duration_ms == 1234 and t.cost_usd == 0.07


def test_tool_step_detail_prefers_command_then_file_path():
    t = extract_trace(_records(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[])
    bash = next(s for s in t.steps if getattr(s, "tool", None) == "Bash")
    write = next(s for s in t.steps if getattr(s, "tool", None) == "Write")
    assert bash.detail == "ls /data/input"
    assert write.detail == "/data/scratch/report.md"
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_trace.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_trace'`

- [ ] **Step 4: Implement `cc_trace.py`**

```python
# nextseek_api/cc_assistant/cc_trace.py
"""Step 3 — per-turn activity trace (SPEC-3 §6).

ONE CCTrace == ONE chat turn. Assembled from two distinct sources (§6.1):
  1. the persisted .jsonl conversation records (steps / commands / tools_used),
  2. the headless `result` frame metadata (num_turns / duration_ms / cost_usd),
     which is NOT in the .jsonl and is passed in as ``result_meta``.
File lists are authoritative from the scratch diff (passed in), not from trusting
tool-call args. pydantic models; jsonl validated with an ordered Union (_Other last).
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class ToolStep(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    tool: str
    detail: str | None = None
    action: Literal["created", "modified"] | None = None


class TextStep(BaseModel):
    type: Literal["text"] = "text"
    summary: str


Step = Union[ToolStep, TextStep]


class CCTrace(BaseModel):
    cc_session_id: str
    ts: str
    num_turns: int | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    steps: list[Step] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    tools_used: dict[str, int] = Field(default_factory=dict)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


# --- jsonl record union (§6.3): ordered, _Other LAST (forward-compat) ---
class _Assistant(BaseModel):
    type: Literal["assistant"]
    message: dict


class _User(BaseModel):
    type: Literal["user"]
    message: dict | None = None


class _Other(BaseModel):
    type: str


RECORDS = TypeAdapter(list[Union[_Assistant, _User, _Other]])


def _content_blocks(message: dict) -> list[dict]:
    content = (message or {}).get("content")
    return content if isinstance(content, list) else []


def extract_trace(records, *, cc_session_id, ts, files_created, files_modified,
                  result_meta=None) -> CCTrace:
    """Build a CCTrace from orjson-decoded jsonl ``records`` + the authoritative
    scratch-diff file lists + the optional headless ``result_meta``."""
    validated = RECORDS.validate_python(list(records))
    steps: list[Step] = []
    commands: list[str] = []
    tools: dict[str, int] = {}
    for rec in validated:
        if not isinstance(rec, _Assistant):
            continue
        for block in _content_blocks(rec.message):
            btype = block.get("type")
            if btype == "text":
                txt = block.get("text") or ""
                if txt.strip():
                    steps.append(TextStep(summary=txt))
            elif btype == "tool_use":
                name = block.get("name") or "?"
                args = block.get("input") or {}
                detail = args.get("command") or args.get("file_path")
                steps.append(ToolStep(tool=name, detail=detail))
                tools[name] = tools.get(name, 0) + 1
                if name == "Bash" and args.get("command"):
                    commands.append(args["command"])
    meta = result_meta or {}
    return CCTrace(
        cc_session_id=cc_session_id, ts=ts,
        num_turns=meta.get("num_turns"), duration_ms=meta.get("duration_ms"),
        cost_usd=meta.get("cost_usd"),
        steps=steps, commands=commands, tools_used=tools,
        files_created=list(files_created), files_modified=list(files_modified),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_trace.py`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/cc_trace.py nextseek_api/cc_assistant/tests/test_cc_trace.py nextseek_api/cc_assistant/tests/fixtures/cc_transcript_sample.jsonl
git commit -m "feat(cc-step3): CCTrace schema + jsonl extractor (orjson + TypeAdapter, §6, E10)"
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
from nextseek_api.cc_assistant.translate import StreamTranslator  # adjust if class name differs


def _translator():
    # construct minimally; _handle_result only reads the payload + self.session_id
    t = StreamTranslator.__new__(StreamTranslator)
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

> **Before writing the test as-is**, open `translate.py` and confirm the class name that owns `_handle_result` and how `session_id`/`_terminated` are initialized; adjust `_translator()` to construct it the lightest way that lets `_handle_result` run (the method only reads `payload` + sets `self.session_id`/`self._terminated`). Keep the assertions.

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

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_translate_result_meta.py`
Expected: PASS (2 tests)

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
- Test: `nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py`

**Interfaces:**
- Produces:
  - `RAW_PREFIX = "raw/"` ; `partition_changed(changed: set[str]) -> tuple[set[str], set[str]]` returns `(artifact_rels, raw_rels)` where a rel under `raw/` is raw, else artifact.
  - `build_artifact_zip(srcs: list[Path], dest_zip: Path) -> Path` — tempfile-free deterministic zip (mirrors `content_blobs.download_batch`'s `zipfile.ZipFile` + `writestr` approach).
  - Reworked `_publish_artifacts(scratch_mount, output_mount, *, output_host_root, before) -> dict` returning `{"artifacts": [ArtifactFile-like dict...], "raw": [host paths], "raw_zip": Path | None}` (replaces the old `list[str]`).

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
    assert "a.txt" in names and "b.txt" in names   # basenames, de-duped
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


def build_artifact_zip(srcs: list[Path], dest_zip: Path) -> Path:
    """Zip ``srcs`` into ``dest_zip`` by basename (de-duped), deterministic order.
    Mirrors content_blobs.download_batch's ZipFile + writestr approach."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(srcs, key=lambda p: p.name):
            name = src.name
            if name in seen:
                seen[name] += 1
                stem, dot, ext = name.partition(".")
                name = f"{stem}_{seen[name]}{dot}{ext}"
            else:
                seen[name] = 0
            zf.writestr(name, src.read_bytes())
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
    output_host_root: str,
    before: dict[str, tuple[int, int]],
) -> dict:
    """Diff scratch; split deliverables (artifacts) from scratch/raw/ (raw).
    Artifacts -> output/artifacts/ (zipped if >1, downloadable); raw -> output/raw/
    (on disk, not bundled). Returns {"artifacts": [...], "raw": [...], "raw_zip": None}."""
    from dmac_assistant.run_tracker import diff_files
    from . import cc_artifacts

    after = _snapshot_tree(scratch_mount)
    changed = diff_files(before, after)
    if not changed:
        return {"artifacts": [], "raw": [], "raw_zip": None}

    art_rels, raw_rels = cc_artifacts.partition_changed(set(changed))
    art_dir = output_mount / "artifacts"
    raw_dir = output_mount / "raw"

    def _copy(rels: set[str], dest_root: Path) -> list[Path]:
        written: list[Path] = []
        for rel in sorted(rels):
            if not _safe_relpath(rel):
                logger.warning("CC: refusing unsafe artifact relpath %r", rel)
                continue
            src = scratch_mount / rel
            if src.is_symlink() or not src.is_file():
                continue
            dst = dest_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            written.append(dst)
        return written

    art_files = _copy(art_rels, art_dir)
    raw_files = _copy(raw_rels, raw_dir)

    artifacts: list[dict] = []
    for dst in art_files:
        rel = dst.relative_to(art_dir)
        artifacts.append({
            "artifact_type": "file", "key": str(rel),
            "label": dst.name, "file_format": dst.suffix.lstrip(".") or "file",
        })
    return {
        "artifacts": artifacts,
        "raw": [str(Path(output_host_root) / "raw" / p.relative_to(raw_dir)) for p in raw_files],
        "raw_zip": None,
    }
```

> Zipping the artifacts for a single download bundle is done lazily at **download time** in Task 10 (so the on-disk `output/artifacts/` stays browsable and the zip is built per-request, matching `download_batch`). The `key` is the artifact's relpath under `output/artifacts/`; the download endpoint maps `(session, key)` → that file.

- [ ] **Step 6: Update the caller of `_publish_artifacts`**

In `cc_engine.py` (`:573`), the call site assigns `published = _publish_artifacts(...)`. Update it to consume the dict (the "Saved to your Dropbox" block at `:580-587` is replaced in Task 8 — for now, keep the turn working by setting the artifacts channel):

```python
        result = _publish_artifacts(
            scratch_mount, output_mount,
            output_host_root=dirs.output_src, before=before,
        )
        if event == "query_complete":
            data = dict(data)
            data["artifacts"] = result["artifacts"] or None
            data["cc_raw_files"] = result["raw"]
```

(Leave the old `published`/Dropbox lines in place until Task 8 removes them, OR if they reference the old `list[str]` shape and now break, comment them out with a `# replaced in Task 8` note and rely on the grep-guard in Task 8 to force their removal. Run the suite after this step; fix any reference to the old return shape.)

- [ ] **Step 7: Run the full hermetic suite**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). Any `_build_volumes`/engine tests unaffected.

- [ ] **Step 8: Commit**

```bash
git add nextseek_api/cc_assistant/cc_artifacts.py nextseek_api/cc_assistant/cc_engine.py nextseek_api/cc_assistant/tests/test_cc_artifacts_split.py
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
             cc_traces=[{"cc_session_id": "s", "ts": "t", "commands": ["ls"]}])
    d = t.model_dump(mode="json")
    assert d["cc_traces"][0]["commands"] == ["ls"]


def test_turn_cc_traces_defaults_none():
    t = Turn(bundle_id=0, user_query="hi", reply="ok", mode="cc")
    assert t.model_dump(mode="json")["cc_traces"] is None
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

- [ ] **Step 5: Run tests + full suite**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_turn_cc_traces.py nextseek_api/cc_assistant/tests/`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/assistant/models_api.py nextseek_api/services/assistant.py nextseek_api/cc_assistant/tests/test_turn_cc_traces.py
git commit -m "feat(cc-step3): Turn.cc_traces field + projection passthrough (§6.5)"
```

---

### Task 8: remove Dropbox — reply text, neutral default, dead-config audit, grep-guard

**Files:**
- Modify: `nextseek_api/cc_assistant/cc_engine.py` (`:580-587`)
- Modify: `nextseek_api/cc_assistant/cc_config.py` (`:15`)
- Modify: `seek/views.py` (`:94`, audited)
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
    blob = (CC / "cc_engine.py").read_text()
    assert "Saved to your Dropbox" not in blob
    assert "artifacts_published" not in blob   # old field removed


def test_no_laptop_default_path():
    cfg = (CC / "cc_config.py").read_text()
    assert "/Users/taishajoseph" not in cfg
    assert '/srv/dmac/users' in cfg            # neutral default present
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_dropbox_grep_guard.py`
Expected: FAIL.

- [ ] **Step 3: Remove the Dropbox reply augmentation**

In `cc_engine.py`, delete the block at `:580-587` (the `if event == "query_complete" and published:` … `data["artifacts_published"] = published`). The Task-6 artifacts channel (`data["artifacts"]`) replaces it. No replacement user-facing copy (the UI shows downloads).

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
git add nextseek_api/cc_assistant/cc_engine.py seek/views.py nextseek_api/cc_assistant/tests/test_cc_dropbox_grep_guard.py
git commit -m "feat(cc-step3): remove Dropbox reply copy + dead DROPBOX_DIRECTORY (§8)"
git add nextseek_api/cc_assistant/cc_config.py
git commit -m "chore(cc-step3): neutral /srv/dmac/users default, drop laptop path (E8) [signed-off]"
```

---

### Task 9: upload endpoint + Celery task + status poll

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (`CCAssistantViewSet`, `:114`)
- Create: `nextseek_api/cc_assistant/cc_upload_tasks.py`
- Test: `nextseek_api/cc_assistant/tests/test_cc_upload_validate.py`

**Interfaces:**
- Produces:
  - Pure helper `validate_upload_filename(name: str) -> str` (in `cc_upload_tasks.py`) — returns a safe basename or raises `ValueError` (reject `/`, `..`, NUL, absolute, empty).
  - DRF `@action(detail=False, methods=["post"], url_path="upload")` `upload(self, request)` — `request.FILES.getlist("file")`; size cap via `settings.BATCH_UPLOAD_MAX_TOTAL_BYTES`; resolves the user's project (`resolve_user_project(api_user, api_pass)`), builds `dirs.input_mnt`, enqueues `run_cc_upload_task.delay(...)`; returns `{"job_id", "status": "queued"}` 202.
  - `@action(detail=False, methods=["get"], url_path=r"upload/status/(?P<job_id>[^/.]+)")` `upload_status(self, request, job_id=None)` — `AsyncResult`, returns `{job_id, state, meta, result}` (mirror `batch_upload.job_status`).
  - Celery task `run_cc_upload_task` (queue `batch_upload`, `bind=True`) — validated save of each file into `input_mnt`, `update_state` progress.

Spec refs: §4 (upload, async, E1/E2), §10 (host-side, no agent cred, filename validation).

- [ ] **Step 1: Write the failing validator tests**

```python
# nextseek_api/cc_assistant/tests/test_cc_upload_validate.py
"""Hermetic filename validation for CC uploads. No Django, no Celery."""
import pytest

from nextseek_api.cc_assistant.cc_upload_tasks import validate_upload_filename


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
Expected: FAIL — `ModuleNotFoundError: No module named 'nextseek_api.cc_assistant.cc_upload_tasks'`

- [ ] **Step 3: Implement the validator + Celery task**

```python
# nextseek_api/cc_assistant/cc_upload_tasks.py
"""Step 3 — CC upload Celery task + filename validation (SPEC-3 §4).

Saves uploaded files into the user's persistent input/ dir (E2). Runs host-side;
no credential reaches the agent (OI-3). Mirrors batch_upload's async + update_state.
"""
from __future__ import annotations

import os
from pathlib import Path


def validate_upload_filename(name: str) -> str:
    """Return a safe single-segment basename or raise ValueError. Rejects path
    separators, traversal, NUL, absolute, empty."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise ValueError(f"invalid filename: {name!r}")
    if "/" in name or "\\" in name or "\x00" in name or os.path.isabs(name):
        raise ValueError(f"invalid filename: {name!r}")
    base = os.path.basename(name)
    if base != name or not base:
        raise ValueError(f"invalid filename: {name!r}")
    return base


try:
    from nextseek_api.batch_upload.celery_app import app  # reuse the existing app
except Exception:  # pragma: no cover - import path confirmed at plan time
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
    for i, f in enumerate(files):
        safe = validate_upload_filename(f["name"])
        dst = dest_root / safe
        os.replace(f["tmp_path"], dst)
        saved.append(safe)
        self.update_state(state="PROGRESS",
                          meta={"progress_pct": int((i + 1) / total * 100), "saved": saved})
    return {"saved": saved, "count": len(saved)}
```

> **[CONFIRM@PLAN]** the exact import path of the existing Celery `app` (Task-1 agent found tasks registered as `@app.task(... name="batch_upload.run")` in `batch_upload/tasks.py` — open it and copy the real `app` import; replace the `try/except` shim above with the confirmed import).

- [ ] **Step 4: Run the validator tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_upload_validate.py`
Expected: PASS (validator cases). The Celery task body is exercised live in Task 13.

- [ ] **Step 5: Add the `upload` + `upload_status` actions**

In `services/cc_assistant.py`, add to `CCAssistantViewSet` (mirror `batch_upload.start`/`job_status`; use the existing `self._resolve_credentials(request)` at `:142`):

```python
    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        from django.conf import settings
        from rest_framework.response import Response
        from rest_framework import status as drf_status
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_tasks import (
            run_cc_upload_task, validate_upload_filename)

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
        stage_root = os.path.join(getattr(settings, "MEDIA_ROOT", "/tmp"), "cc_upload_staging")
        os.makedirs(stage_root, exist_ok=True)
        for f in uploaded:
            safe = validate_upload_filename(getattr(f, "name", ""))
            tmp = os.path.join(stage_root, f"{int_time_unique()}_{safe}")
            with open(tmp, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            staged.append({"name": safe, "tmp_path": tmp})

        task = run_cc_upload_task.delay(input_mnt=dirs.input_mnt, files=staged)
        return Response({"job_id": task.id, "status": "queued"},
                        status=drf_status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"upload/status/(?P<job_id>[^/.]+)")
    def upload_status(self, request, job_id=None):
        from rest_framework.response import Response
        from celery.result import AsyncResult
        from nextseek_api.batch_upload.tasks import app as celery_app  # confirm import
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

> Add `import os` + a small `int_time_unique()` (or reuse `batch_upload`'s `_save_uploaded_file` timestamp idiom — Task-1 agent quoted it: `f"{int(time.time())}_{safe_name}"`). Confirm the `celery_app`/`app` import path matches `batch_upload`.

- [ ] **Step 6: Run the full hermetic suite (no endpoint exec)**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). The actions are import-checked; live exec is Task 13.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/cc_assistant/cc_upload_tasks.py nextseek_api/services/cc_assistant.py nextseek_api/cc_assistant/tests/test_cc_upload_validate.py
git commit -m "feat(cc-step3): per-user upload endpoint + Celery task + status poll (§4, E1/E2)"
```

---

### Task 10: artifact-download + transcript-recover endpoints (owner-scoped)

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py`
- Test: covered by Task 6 zip + Task 1 decompress (pure seams); endpoints live in Task 13.

**Interfaces:**
- Produces:
  - `@action(detail=False, methods=["get"], url_path=r"artifacts/(?P<session>[0-9a-f-]+)/download")` `download_artifact(self, request, session=None)` — `?key=<rel>`; owner-scoped (`ChatSession.objects.filter(user=request.user, session_id=session)`); resolves the user's `output/artifacts/<key>` (or zips `output/artifacts/` if `key == "all"`); streams via `StreamingHttpResponse` + `Content-Disposition` (mirror `content_blobs.download_batch`).
  - `@action(detail=False, methods=["get"], url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")` `recover_transcript(self, request, session=None, turn=None)` — owner-scoped; loads `CCSessionTranscript`, `cc_transcript_store.decompress`, streams the jsonl.

Spec refs: §5 (download), §7 (recover), §10 (owner-scoping, traversal guard, bomb bound).

- [ ] **Step 1: Add the download action (owner-scoped, traversal-guarded)**

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
        project = resolve_user_project(api_user, api_pass)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        art_dir = Path(dirs.output_mnt) / "artifacts"

        if key == "all":
            import tempfile
            from nextseek_api.cc_assistant.cc_artifacts import build_artifact_zip
            files = [p for p in art_dir.rglob("*") if p.is_file()]
            tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
            build_artifact_zip(files, tmp)
            resp = StreamingHttpResponse(_iter_file(tmp), content_type="application/zip")
            resp["Content-Disposition"] = 'attachment; filename="artifacts.zip"'
            return resp

        target = art_dir / key
        if not target.is_file():
            raise Http404("not found")
        resp = StreamingHttpResponse(_iter_file(target), content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="{target.name}"'
        return resp
```

(Add a module-level `_iter_file(path, chunk=1024*1024)` generator that yields bytes then unlinks temp zips, mirroring `content_blobs._iter_and_cleanup`.)

- [ ] **Step 2: Add the transcript-recover action**

```python
    @action(detail=False, methods=["get"], url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")
    def recover_transcript(self, request, session=None, turn=None):
        from django.http import HttpResponse, Http404
        from nextseek_api.assistant.models_db import ChatSession, CCSessionTranscript
        from nextseek_api.cc_assistant.cc_transcript_store import decompress

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        row = (CCSessionTranscript.objects
               .filter(chat_session=cs, turn_id=turn).order_by("-created_at").first())
        if row is None:
            raise Http404("no transcript")
        jsonl = decompress(bytes(row.blob))
        resp = HttpResponse(jsonl, content_type="application/x-ndjson")
        resp["Content-Disposition"] = f'attachment; filename="transcript-{turn}.jsonl"'
        return resp
```

- [ ] **Step 3: Run the full hermetic suite (import check)**

Run: `uv run --no-project --with pytest --with orjson --with pydantic python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). Endpoint behavior verified live (Task 13).

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/services/cc_assistant.py
git commit -m "feat(cc-step3): owner-scoped artifact-download + transcript-recover endpoints (§5,§7,§10)"
```

---

### Task 11: persist `CCTrace` + transcript blob (caller wiring + emit)

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (CC branch)
- Modify: `nextseek_api/cc_assistant/cc_engine.py` (emit trace metadata on `query_complete`)
- Test: live (Task 13); the pure pieces (extract, compress, Turn field) are already covered by Tasks 1/4/7.

**Interfaces:**
- After a CC turn completes: read the session jsonl (the existing `read_bytes()` at `cc_assistant.py:103/300`), call `cc_trace.extract_trace(...)` with the scratch-diff file lists + the `result_meta` (num_turns/duration_ms/cost_usd from the `query_complete` frame, Task 5), append the `CCTrace.model_dump()` to `extra_state["cc_traces"]` AND to the turn `entry` so it rides the projection (Task 7), and upsert `CCSessionTranscript(blob=compress(jsonl), uncompressed_size=len(jsonl))`.

Spec refs: §6.5 (persist + live + reload), §7 (write path), §6.1 (assemble from both sources + scratch diff).

- [ ] **Step 1: Capture `result_meta` from the terminal frame**

In `cc_engine.py` where `query_complete` is handled (Task 6 Step 6 edit), thread `num_turns`/`duration_ms`/`total_cost_usd` from `data` into the publish/return path so the caller can build `result_meta = {"num_turns": ..., "duration_ms": ..., "cost_usd": data.get("total_cost_usd")}`. Attach the raw scratch-diff lists too (the `result["artifacts"]`/`result["raw"]` already identify changed files; for the trace, pass `files_created`/`files_modified` derived from the diff — created = new in `after` not in `before`, modified = changed).

- [ ] **Step 2: Persist in the CC branch**

In `services/cc_assistant.py`, in the CC branch after the turn's terminal event, add (using the canonical `extra_state` pattern + the new helpers):

```python
                    # --- Step 3: persist activity trace + full transcript ---
                    try:
                        from nextseek_api.cc_assistant import cc_trace, cc_summary, cc_transcript_store
                        from nextseek_api.assistant.models_db import CCSessionTranscript
                        raw = Path(mount_path).read_bytes()          # the session jsonl
                        records = list(cc_summary.parse_transcript(raw).records)
                        trace = cc_trace.extract_trace(
                            records, cc_session_id=cc_session_id,
                            ts=_now_iso(),
                            files_created=files_created, files_modified=files_modified,
                            result_meta=result_meta,
                        )
                        es = dict(chat_session.extra_state or {})
                        es.setdefault("cc_traces", []).append(trace.model_dump())
                        chat_session.extra_state = es
                        chat_session.save(update_fields=["extra_state", "updated_at"])
                        CCSessionTranscript.objects.update_or_create(
                            chat_session=chat_session, cc_session_id=cc_session_id, turn_id=run_id,
                            defaults={"blob": cc_transcript_store.compress(raw),
                                      "uncompressed_size": len(raw)},
                        )
                    except Exception:
                        logger.exception("cc-step3: trace/transcript persist failed; continuing")
```

> The `cc_traces` must also reach the **turn entry** that the projection reads (Task 7 reads `entry.get("cc_traces")`). Confirm where the per-turn `entry` is appended to `results_history`/`chat_log` (the same place `artifacts` is recorded) and attach this turn's `trace.model_dump()` there, so reload hydrates it. `_now_iso()`, `mount_path`, `cc_session_id`, `run_id`, `files_created/modified`, and `result_meta` must be in scope — wire them from the surrounding `_run` closure (they are produced by the engine/translate path this task threads through). This is the one task with no hermetic seam; it is the core of the Task 13 live gate.

- [ ] **Step 3: Run the full hermetic suite (import/regression check)**

Run: `uv run --no-project --with pytest --with orjson --with pydantic --with zstandard python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/`
Expected: PASS (all). No new hermetic test (DB-bound); regression-only here.

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/services/cc_assistant.py nextseek_api/cc_assistant/cc_engine.py
git commit -m "feat(cc-step3): persist CCTrace to extra_state + zstd transcript to DB (§6.5,§7)"
```

---

### Task 12: frontend — upload control, activity panel, artifact download branch, 3e session-id

**Files:**
- Create: `chat_frontend/src/components/ChatPanel/UploadControl.tsx` (+ `.test.tsx`)
- Create: `chat_frontend/src/components/ChatPanel/CCActivityPanel.tsx` (+ `.test.tsx`)
- Modify: `MessageInput.tsx`, `MessageBubble.tsx`, `EmbeddedApp.tsx`, `AppLayout.tsx`, `lib/api/chatApi.ts`, `lib/types/chat.ts`, `lib/types/api.ts`, `hooks/useMessages.ts`, `ReportArtifacts.tsx`

**Interfaces:**
- `lib/types/chat.ts`: add `CCTrace` TS type mirroring §6.2 (`cc_session_id`, `ts`, `num_turns?`, `duration_ms?`, `cost_usd?`, `steps`, `commands`, `tools_used`, `files_created`, `files_modified`) + `ccTraces?: CCTrace[]` on `Message`.
- `lib/types/api.ts`: add `cc_traces?: CCTrace[]` to `Turn`.
- `chatApi.ts`: `uploadFiles(files: File[]): Promise<{job_id}>`, `pollUpload(jobId)`, and `downloadCcArtifact(sessionId, key)` → `GET /nextseek_api/cc-assistant/artifacts/{session}/download?key={key}`.

Spec refs: §4 (upload UI), §6 (panel survives reload), §5 (download), §9 (3e).

- [ ] **Step 1 (3e first — smallest, lowest-risk): promote the authoritative session id**

Vitest can't easily drive these handlers; this is a 2-line change guarded by the existing TODO. In `EmbeddedApp.tsx:133` and `AppLayout.tsx:124`, replace the promotion source:

```ts
        // 3e: promote from the AUTHORITATIVE HTTP-202 body id, not the WS event.
        const authSid = serviceRef.current.sessionId ?? d.session_id;   // AppLayout: service.sessionId
        if (authSid) {
          if (sessions.pendingNewChat) sessions.promoteCreatedSession(authSid);
          else sessions.refresh();
        }
```

Remove the now-resolved TODO comment block at both sites. (AppLayout uses `service.sessionId`; EmbeddedApp uses `serviceRef.current.sessionId` — per the Task-4 agent map.)

- [ ] **Step 2: `CCTrace` type + `ccTraces` on Message + hydrate**

In `lib/types/chat.ts` add the `CCTrace` interface (§6.2 fields) and `ccTraces?: CCTrace[]` on `Message`. In `lib/types/api.ts` add `cc_traces?: CCTrace[]` to `Turn`. In `hooks/useMessages.ts:88` (`hydrateFromTurns`), map it instead of dropping it:

```ts
        ccTraces: turn.cc_traces ?? undefined,
```

And in the live `query_complete` handler (EmbeddedApp), attach `ccTraces` from `d` (the frame now carries trace metadata via the artifacts/trace channel) alongside `debugEntries`.

- [ ] **Step 3: `CCActivityPanel` component + Vitest test**

Write `CCActivityPanel.tsx` rendering one trace (num_turns, duration, cost, commands list, files created/modified, tools tally), and a colocated test:

```tsx
// CCActivityPanel.test.tsx
import { render, screen } from "@testing-library/react";
import { CCActivityPanel } from "./CCActivityPanel";

test("renders commands and file changes from a trace", () => {
  render(<CCActivityPanel trace={{
    cc_session_id: "s", ts: "t", num_turns: 3, commands: ["ls /data/input"],
    files_created: ["report.md"], files_modified: [], steps: [], tools_used: { Bash: 1 },
  }} />);
  expect(screen.getByText("ls /data/input")).toBeInTheDocument();
  expect(screen.getByText("report.md")).toBeInTheDocument();
});
```

Render the panel from `MessageBubble.tsx` inside the existing collapsible "Search Details" chrome (`:111-159`) when `message.ccTraces?.length` — reuse the toggle; do not overload `debugEntries`.

- [ ] **Step 4: `UploadControl` component + Vitest test + wire into `MessageInput`**

`UploadControl.tsx`: a file-attach button + selected-file list + progress (calls `chatApi.uploadFiles` then polls `pollUpload`). Colocated test asserts files render and the upload callback fires on submit. Insert it into `MessageInput.tsx` near the composer button row (`:70-117`, the `flex items-end gap-2` row).

- [ ] **Step 5: artifact download — branch the null-bundle CC case**

In `MessageBubble.tsx:106`, the existing wrapper calls `onArtifactDownload?.(message.bundleId!, key)`. For CC turns `bundleId` is null. Branch: if `message.bundleId == null`, call the new CC path:

```ts
  const handleDl = (key: string) =>
    message.bundleId != null
      ? onArtifactDownload?.(message.bundleId, key)
      : onCcArtifactDownload?.(message /* session */, key);
```

Wire `onCcArtifactDownload` from `EmbeddedApp`/`AppLayout` to `serviceRef.current.downloadCcArtifact(sessionId, key)` (chatApi). The session id is the message's session (authoritative, from Step 1).

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

- [ ] **Step 1: Add `zstandard` to the image**

Add `zstandard` to the image's Python deps (`pyproject`/requirements that the Dockerfile installs) so `cc_transcript_store` imports at runtime. Confirm the dmac venv inside the image gets it on rebuild.

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

Drive the chat UI with Playwright (see `nextseek-playwright.md`), forced-CC, ≤ $2 cap: upload a file via the new control → confirm it lands in `<root>/<project>/demo/input/` on the host → a CC turn reads it (RO at `/data/input`).

- [ ] **Step 5: Output split + download + raw + transcript**

A CC turn writes a deliverable to `scratch/` and something to `scratch/raw/`: confirm the deliverable appears in `output/artifacts/`, downloads via the UI button (zip if >1), the raw file lands in `output/raw/`, and `GET …/transcript/<session>/<turn>/` returns the full jsonl (zstd round-trip).

- [ ] **Step 6: Activity panel survives reload**

Confirm the panel shows commands/files/num_turns live, then **reload the session** and confirm the panel is still populated (proves `cc_traces` persisted + hydrated, unlike native ephemeral `debugEntries`).

- [ ] **Step 7: 3e + no regressions**

New chat, second turn does not 404 (3e). Re-run the **1b resume** A/B and the **1c memory** live check (per their evidence docs) at the nested paths — confirm no regression. Use an on-domain agentic prompt for 1c recall (the router gates content-free recall by design).

- [ ] **Step 8: Record evidence + flip the tracker**

Write live evidence under `nextseek_api/cc_assistant/evidence/3-ui-based-io-live.md` (secret-scan before commit). With the user's OK, set `integration-plan.json` step **3** (and substeps 3a–3e) `status` → `done` (status field ONLY; never add keys). Capture the session with `/handoff`.

- [ ] **Step 9: Commit deploy artifacts + DEPLOY.md**

```bash
git add nextseek_api/cc_assistant/DEPLOY.md <image dep file> static/js/chat_assistant
git commit -m "chore(cc-step3): zstandard dep, migration 0007, embedded frontend build + deploy notes (§12,§13)"
```

---

## Self-Review

**1. Spec coverage:**
- §4 upload (E1/E2) → Task 3 (`input_mnt`), Task 9 (endpoint+task+status). ✔
- §5 output split + download (E3/E9) → Task 6 (split+zip), Task 10 (download endpoint), Task 12 Step 5 (UI branch). ✔
- §6 activity panel (E4/E5/E10) → Task 4 (schema+extractor), Task 5 (result meta), Task 7 (Turn field), Task 11 (persist), Task 12 Step 3 (panel). ✔
- §6.4 translate extension → Task 5. ✔
- §7 transcript recoverability (E6/E7) → Task 1 (zstd), Task 2 (model+migration), Task 10 (recover endpoint), Task 11 (write path). ✔
- §8 remove Dropbox (E8) → Task 8. ✔
- §9 authoritative session id (3e) → Task 12 Step 1. ✔
- §10 isolation/security → owner-scoping (Tasks 9/10), filename validation (Task 9), bomb bound (Task 1), no-cred-to-agent (Tasks 9/10 run host-side). ✔
- §12 testing → every task's hermetic seam + Task 13 live. ✔  §13 deployment → Task 13. ✔
- §14 out-of-scope (multi-project, shared population, upload delete/quota, ingestion, re-summarization) — intentionally excluded. ✔

**2. Placeholder scan:** Every code step carries real code. Three explicit verify-against-runtime flags remain, each a confirmation not a gap: (a) the `StreamTranslator` class/init in Task 5 Step 1 (the method body edit is exact); (b) the Celery `app` import path in Task 9 (Task-1 agent quoted `@app.task(name="batch_upload.run")` — copy the real import); (c) the exact `_run`-closure variable names (`mount_path`, `cc_session_id`, `run_id`, `files_created/modified`, `result_meta`, `_now_iso`) in Task 11, which exist in the surrounding code and must be wired, not invented. These are the only `[CONFIRM@PLAN]`s and all are import/name confirmations against live code, resolved at execution by reading the cited file.

**3. Type consistency:** `CCTrace`/`Step`/`ToolStep`/`TextStep` field names (Task 4) are reused verbatim in Task 7 (`Turn.cc_traces`), Task 11 (`trace.model_dump()`), and Task 12 (TS `CCTrace`). `UserDirs.input_mnt` (Task 3) consumed in Task 9 (`dirs.input_mnt`) and Task 10 (`dirs.output_mnt`). `_publish_artifacts` returns the dict `{"artifacts","raw","raw_zip"}` (Task 6) consumed by the caller in Task 6 Step 6 + Task 11. `compress`/`decompress` (Task 1) used in Tasks 10/11. The artifact `key` is the relpath under `output/artifacts/` everywhere (Task 6 produces it, Task 10 resolves it, Task 12 sends it).

**Known coupling note (mirrors PLAN-2):** Tasks 6 and 8 both touch the `query_complete` handler in `cc_engine.py` — Task 6 adds the artifacts channel, Task 8 removes the old Dropbox lines; if Task 6 Step 6 leaves the old `published`/`artifacts_published` lines referencing the new dict shape, the suite may go red until Task 8's grep-guard forces their removal. The full suite is the gate at Task 6 Step 7, Task 7 Step 5, Task 8 Step 6, and Task 9 Step 6. Tasks 11 and 13 have no hermetic seam by design (DB/HTTP/Docker) and are proven in the Task 13 live gate, which is the real acceptance bar for Step 3.
