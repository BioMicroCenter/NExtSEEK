# HiBayes × NExtSEEK Evaluation & Routing Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the measurement loop on the NExtSEEK assistant — record every turn's route, family and outcome durably, judge them on a nightly incremental schedule, fit per-family reliability posteriors, and feed the result back to the container agent and (later) to a routing risk gate.

**Architecture:** Three layers. **L1 (online)** adds `task_family` to the existing single router call and writes a durable per-turn ledger row alongside the existing JSON envelope. **L2 (offline)** vendors the evaluation pipeline into this repo, then runs a nightly Celery task that exports ledger rows, judges only new/changed turns against a fingerprinted cache, fits the existing hierarchical model, and publishes posteriors to a table. **L3 (consumers)** reads those posteriors — playbook guidance first, routing overlay second.

**Tech Stack:** Python 3.12, Django + Celery (`batch_upload` queue), BAML (judge + router), MySQL, pytest / pytest-django, Docker.

## Global Constraints

- **Anchor commit:** `9edd36958b6be06098d2cbdd8a5e3a0561e6623d` (`origin/dev`). Re-verify any cited line before relying on it; see the spec's Drift protocol.
- **Coverage target: 95%**, across unit, integration and live end-to-end tests.
- **No paid model call runs automatically.** Every paid path is behind an explicit opt-in env gate and is never invoked by CI or by a default test run.
- **No new credentials into the agent sandbox.** The isolation invariants are untouched by every task here.
- **Copy, do not rewrite.** Vendored evaluation logic must preserve the original's behaviour. Reformatting is acceptable; changing control flow or thresholds is not.
- **No dependency on a `dmac-assistant` checkout.** After Phase 2, every task must pass on a machine that does not have that repository.
- **The two BAML trees stay byte-identical** (`dmac_assistant/baml_src/` and `docker/cc-runtime/baml_src/`). Any edit lands in both in the same commit.
- **The capabilities file is hash-pinned by exactly one test** (`nextseek_api/cc_assistant/tests/test_f_constraint_pins.py:12,17`). Editing that file updates that pin in the same commit. A second test's docstring claims it also pins the file — it does not; ignore that docstring.
- **Taxonomy source of truth** is the **NExtSEEK-vendored** `dmac_assistant/build_context/route_capabilities.json`, not the standalone `dmac-assistant` copy. They are forked.
- **Never commit or push without the maintainer's explicit go-ahead.** Tasks end at `git commit` on the feature branch only.
- **Hermetic test command:** `pytest nextseek_api/cc_assistant/tests/`
- **DB-backed tests run inside the container:** `docker exec -w /app nextseek uv run --no-sync python -m pytest …` (`--no-sync` is mandatory).

## File Structure

| Path | Responsibility |
|---|---|
| `nextseek_api/assistant/models_db.py` | **Modify.** Add `TurnLedger` model. |
| `nextseek_api/migrations/0010_turn_ledger.py` | **Create.** Ledger table + FK + unique constraint, reusing the charset-alignment helper. |
| `nextseek_api/cc_assistant/turn_ledger.py` | **Create.** Single write path for ledger rows; both route writers call it. |
| `dmac_assistant/baml_src/router.baml` | **Modify.** `task_family` on the decision. |
| `docker/cc-runtime/baml_src/router.baml` | **Modify.** Byte-identical mirror. |
| `nextseek_api/cc_assistant/router.py` | **Modify.** Surface + fall back the family. |
| `nextseek_api/cc_assistant/family_fallback.py` | **Create.** Deterministic family for non-BAML turns. |
| `nextseek_api/eval/` | **Create.** Vendored evaluation package (tools + fit packages). |
| `nextseek_api/eval/export.py` | **Create.** Ledger → versioned eval rows. |
| `nextseek_api/eval/judge_cache.py` | **Create.** Fingerprint, lookup, invalidation, partial-failure policy. |
| `nextseek_api/eval/tasks.py` | **Create.** Celery nightly task, spend cap, force path. |
| `nextseek_api/eval/publish.py` | **Create.** Posterior store writer. |
| `nextseek_api/cc_assistant/playbook.py` | **Create.** Consumer (a). |
| `nextseek_api/cc_assistant/tests/test_*.py` | **Create.** One test module per task. |

---

## Phase 1 — Online foundation (no paid calls anywhere in this phase)

### Task 0: Shared test fixtures

**Runs first.** Every later task's tests consume these; without them each task would invent its own
and they would drift.

**Files:**
- Create: `nextseek_api/cc_assistant/tests/conftest_eval.py`
- Modify: `nextseek_api/cc_assistant/tests/conftest.py` (import the new fixtures)

**Interfaces:**
- Produces: `eval_row`, `one_row`, `many_rows`, `cached_rows`, `fake_judge`, `failing_judge`,
  `live_judge`, `fit_result`, `sparse_fit_result`, `posteriors`, `sparse_posteriors`,
  `brittle_posterior`, `sparse_posterior`, `no_posteriors`, `user_a`, `judgments_two_projects`,
  `query_turn_factory`, `cc_turn_factory`, `one_real_turn`.

- [ ] **Step 1: Write the fixtures**

```python
# nextseek_api/cc_assistant/tests/conftest_eval.py
"""Shared fixtures for the evaluation-loop tests.

`fake_judge` never performs a network call. `live_judge` is the only fixture that
can, and it is reachable solely from the RUN_EVAL_LIVE-gated module.
"""
from dataclasses import dataclass, field

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession, FamilyPosterior
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.export import EvalRow, export_rows


@pytest.fixture
def user_a(db):
    return get_user_model().objects.create(username="user_a")


@pytest.fixture
def eval_row(db):
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    return export_rows()[0]


@pytest.fixture
def one_row(eval_row):
    return [eval_row]


@pytest.fixture
def many_rows(db):
    s = ChatSession.objects.create()
    for i in range(1, 6):
        record_turn(str(s.session_id), i, "container_cc", "code_and_scripts", "baml")
    return export_rows()


@pytest.fixture
def cached_rows(many_rows):
    from nextseek_api.eval.judge_cache import record_judgment
    v = dict(prompt_version="p1", model_id="m1", schema_version=2)
    for r in many_rows:
        record_judgment(r, verdict={"ok": True}, **v)
    return many_rows


@dataclass
class _FakeJudge:
    cost_per_call: float = 0.0
    calls: int = 0

    def __call__(self, row):
        self.calls += 1
        return {"ok": True}, self.cost_per_call


@pytest.fixture
def fake_judge():
    def _make(cost_per_call=0.0):
        return _FakeJudge(cost_per_call=cost_per_call)
    return _make


@pytest.fixture
def failing_judge():
    def _judge(row):
        raise RuntimeError("judge exploded")
    return _judge


@dataclass
class _Group:
    name: str
    route: str = "container_cc"
    posterior_mean: float = 0.97
    band: str = "Reliable"
    n_total: int = 40


@dataclass
class _FitResult:
    groups: list = field(default_factory=list)


@pytest.fixture
def fit_result():
    return _FitResult(groups=[_Group("batch_upload_preparation"), _Group("code_and_scripts")])


@pytest.fixture
def sparse_fit_result():
    return _FitResult(groups=[
        _Group("memory_lookup", route="nextseek_query", posterior_mean=0.5,
               band="TooUncertain", n_total=2)
    ])


def _posterior(**kw):
    base = dict(task_family="batch_upload_preparation", route="container_cc",
                posterior_mean=0.97, band="Reliable", n_total=40)
    base.update(kw)
    return FamilyPosterior.objects.create(**base)


@pytest.fixture
def posteriors(db):
    return [_posterior(), _posterior(task_family="code_and_scripts")]


@pytest.fixture
def brittle_posterior(db):
    return _posterior(band="Brittle", posterior_mean=0.62)


@pytest.fixture
def sparse_posterior(db):
    return _posterior(task_family="memory_lookup", route="nextseek_query",
                      band="TooUncertain", n_total=2)


@pytest.fixture
def sparse_posteriors(sparse_posterior):
    return [sparse_posterior]


@pytest.fixture
def no_posteriors(db):
    FamilyPosterior.objects.all().delete()
    return []


@pytest.fixture
def judgments_two_projects(db, user_a):
    """Two projects; only project 1 belongs to the requesting user's scope."""
    from nextseek_api.eval.judge_cache import record_judgment
    v = dict(prompt_version="p1", model_id="m1", schema_version=2)
    for project_id, marker in ((1, "PROJECT_1_STUDY"), (2, "PROJECT_2_SECRET_STUDY")):
        s = ChatSession.objects.create(user=user_a if project_id == 1 else None)
        record_turn(str(s.session_id), 1, "container_cc", "batch_upload_preparation", "baml")
        row = export_rows()[-1]
        record_judgment(row, verdict={"ok": False, "note": marker}, **v)
    return True


@pytest.fixture
def query_turn_factory(db):
    def _make(session, turn_number, query, fail=False):
        record_turn(str(session.session_id), turn_number, "nextseek_query", None, "heuristic")
    return _make


@pytest.fixture
def cc_turn_factory(db):
    def _make(session, turn_number, query, fail=False):
        record_turn(str(session.session_id), turn_number, "container_cc", None,
                    "forced" if fail else "baml")
    return _make
```

- [ ] **Step 2: Register them**

```python
# nextseek_api/cc_assistant/tests/conftest.py — append
from .conftest_eval import *  # noqa: F401,F403
```

- [ ] **Step 3: Verify the fixtures load**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ --collect-only -q 2>&1 | tail -5 | tee evidence/task00.log`
Expected: collection succeeds with no fixture errors

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/cc_assistant/tests/conftest_eval.py nextseek_api/cc_assistant/tests/conftest.py
git commit -m "test(eval): shared fixtures for the evaluation-loop test suite"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/ --collect-only -q` exits 0 with output at `evidence/task00.log`, and no test in the suite reports `fixture '<name>' not found`.

**Note:** `eval_row`, `one_row`, `many_rows` and `cached_rows` depend on Tasks 1, 2 and 7. Write the
file now, but expect collection of those specific fixtures to fail until those tasks land — that is
why Step 3 checks collection of the suite, not execution.

**Rollback:** `git revert`.

---

### Task 1: Turn ledger model and migration

**Files:**
- Modify: `nextseek_api/assistant/models_db.py`
- Create: `nextseek_api/migrations/0010_turn_ledger.py`
- Test: `nextseek_api/cc_assistant/tests/test_turn_ledger_model.py`

**Interfaces:**
- Consumes: `ChatSession` (`models_db.py:7`), the charset helper `nextseek_api/migrations/_cc_transcript_heal.py:85-97`.
- Produces: `TurnLedger` with fields `session` (FK→ChatSession), `turn_number` (int), `route` (str), `task_family` (str, nullable), `family_source` (str), `created_at`; unique constraint `("session", "turn_number")`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_turn_ledger_model.py
import pytest
from django.db import IntegrityError
from nextseek_api.assistant.models_db import ChatSession, TurnLedger

pytestmark = pytest.mark.django_db


def _session():
    return ChatSession.objects.create()


def test_ledger_row_is_addressable_by_session_and_turn():
    s = _session()
    TurnLedger.objects.create(
        session=s, turn_number=1, route="nextseek_query",
        task_family="sample_search", family_source="baml",
    )
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "nextseek_query"
    assert row.task_family == "sample_search"


def test_duplicate_turn_number_in_one_session_is_rejected():
    s = _session()
    TurnLedger.objects.create(session=s, turn_number=1, route="container_cc",
                              task_family=None, family_source="forced")
    with pytest.raises(IntegrityError):
        TurnLedger.objects.create(session=s, turn_number=1, route="container_cc",
                                  task_family=None, family_source="forced")


def test_same_turn_number_in_different_sessions_is_allowed():
    a, b = _session(), _session()
    TurnLedger.objects.create(session=a, turn_number=1, route="nextseek_query",
                              task_family=None, family_source="heuristic")
    TurnLedger.objects.create(session=b, turn_number=1, route="nextseek_query",
                              task_family=None, family_source="heuristic")
    assert TurnLedger.objects.filter(turn_number=1).count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'TurnLedger'`

- [ ] **Step 3: Add the model**

```python
# nextseek_api/assistant/models_db.py — append
class TurnLedger(models.Model):
    """Durable per-turn identity. The chat_log turn number lives only inside a JSON
    blob, so it cannot be a foreign-key target; this table is that missing row."""

    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="turn_ledger"
    )
    turn_number = models.IntegerField()
    route = models.CharField(max_length=64)
    task_family = models.CharField(max_length=128, null=True, blank=True)
    family_source = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "turn_number"], name="uniq_turn_per_session"
            )
        ]
        indexes = [models.Index(fields=["task_family", "route"])]
```

- [ ] **Step 4: Generate and harden the migration**

Run: `docker exec -w /app nextseek uv run --no-sync python manage.py makemigrations nextseek_api --name turn_ledger`

Then edit the generated `0010_turn_ledger.py` to run the charset alignment **before** the FK is created, mirroring `0008_heal_cc_transcript_fk.py`:

```python
# nextseek_api/migrations/0010_turn_ledger.py — add above the CreateModel operation
from nextseek_api.migrations._cc_transcript_heal import align_charset_for_fk

operations = [
    migrations.RunPython(align_charset_for_fk, migrations.RunPython.noop),
    # ... generated CreateModel + AddConstraint follow ...
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_model.py -v 2>&1 | tee evidence/task01.log`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/assistant/models_db.py nextseek_api/migrations/0010_turn_ledger.py nextseek_api/cc_assistant/tests/test_turn_ledger_model.py
git commit -m "feat(eval): add TurnLedger table for durable per-turn identity"
```

**Success condition:** Met only if `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_model.py -v` exits 0, its output is saved to `evidence/task01.log`, `nextseek_api/migrations/0010_turn_ledger.py` exists, and `docker exec -w /app nextseek uv run --no-sync python manage.py sqlmigrate nextseek_api 0010` prints a `UNIQUE` constraint on `(session_id, turn_number)`.

**Failure conditions:** migration errors 3780 (charset alignment missing/misordered); unique constraint absent from `sqlmigrate` output.

**Rollback:** `git revert` the commit; the migration is additive, so `migrate nextseek_api 0009` reverses it.

---

### Task 2: Ledger write path, called by both routes

**Files:**
- Create: `nextseek_api/cc_assistant/turn_ledger.py`
- Test: `nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py`

**Interfaces:**
- Consumes: `TurnLedger` from Task 1.
- Produces: `record_turn(session_id: str, turn_number: int, route: str, task_family: str | None, family_source: str) -> TurnLedger`, and `LedgerCollision` (raised on a duplicate).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py
import pytest
from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant.turn_ledger import record_turn, LedgerCollision

pytestmark = pytest.mark.django_db


def test_record_turn_persists_a_row():
    s = ChatSession.objects.create()
    row = record_turn(str(s.session_id), 1, "nextseek_query", "sample_search", "baml")
    assert TurnLedger.objects.filter(pk=row.pk).exists()


def test_concurrent_same_turn_number_raises_collision_not_integrity_error():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    with pytest.raises(LedgerCollision):
        record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")


def test_null_family_is_allowed_with_a_source_recorded():
    s = ChatSession.objects.create()
    row = record_turn(str(s.session_id), 2, "container_cc", None, "forced")
    assert row.task_family is None
    assert row.family_source == "forced"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.cc_assistant.turn_ledger`

- [ ] **Step 3: Write the implementation**

```python
# nextseek_api/cc_assistant/turn_ledger.py
"""Single write path for the per-turn ledger.

Both route writers call this. The turn number is assigned upstream by a
read-modify-write over session state that holds no lock, so two concurrent
completions on one session can compute the same value. The unique constraint
surfaces that as LedgerCollision rather than letting it pass silently.
"""
from django.db import IntegrityError, transaction

from nextseek_api.assistant.models_db import TurnLedger


class LedgerCollision(RuntimeError):
    """Two turns claimed the same (session, turn_number)."""


def record_turn(session_id, turn_number, route, task_family, family_source):
    try:
        with transaction.atomic():
            return TurnLedger.objects.create(
                session_id=session_id,
                turn_number=turn_number,
                route=route,
                task_family=task_family,
                family_source=family_source,
            )
    except IntegrityError as exc:
        raise LedgerCollision(
            f"turn {turn_number} already recorded for session {session_id}"
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py -v 2>&1 | tee evidence/task02.log`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/turn_ledger.py nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py
git commit -m "feat(eval): single write path for turn ledger rows"
```

**Success condition:** Met only if the pytest command above exits 0, output saved to `evidence/task02.log`, and the collision test demonstrably raises `LedgerCollision` rather than `IntegrityError`.

**Failure conditions:** a bare `IntegrityError` escaping `record_turn`; a write outside a transaction.

**Rollback:** `git revert`; nothing else calls this module yet.

---

### Task 3: Router returns `task_family` in the same call

**Files:**
- Modify: `dmac_assistant/baml_src/router.baml`
- Modify: `docker/cc-runtime/baml_src/router.baml` (byte-identical)
- Modify: `nextseek_api/cc_assistant/router.py`
- Test: `nextseek_api/cc_assistant/tests/test_router_family.py`

**Interfaces:**
- Consumes: the eight families from `dmac_assistant/build_context/route_capabilities.json`.
- Produces: `RouteDecision.task_family: str | None` and `RouteDecision.family_source: str` (`"baml"` | `"heuristic"` | `"forced"`).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_router_family.py
import hashlib
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_A = _REPO / "dmac_assistant" / "baml_src" / "router.baml"
_B = _REPO / "docker" / "cc-runtime" / "baml_src" / "router.baml"
_CAPS = _REPO / "dmac_assistant" / "build_context" / "route_capabilities.json"


def test_router_baml_declares_task_family():
    assert "task_family" in _A.read_text()


def test_both_router_baml_copies_stay_byte_identical():
    assert hashlib.sha256(_A.read_bytes()).hexdigest() == \
           hashlib.sha256(_B.read_bytes()).hexdigest()


def test_declared_families_match_the_capabilities_file_exactly():
    caps = json.loads(_CAPS.read_text())
    expected = {f["name"] for r in caps["routes"] for f in r["task_families"]}
    text = _A.read_text()
    for name in expected:
        assert name in text, f"router.baml does not declare family {name}"
    assert len(expected) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v`
Expected: FAIL on `test_router_baml_declares_task_family`

- [ ] **Step 3: Add the field to the BAML contract**

In `dmac_assistant/baml_src/router.baml`, add an enum of the eight family names and a
`task_family` field on the decision class returned by the existing route function. Do **not**
add a second function — the family comes back from the same call.

```
enum TaskFamily {
  SampleSearch        @alias("sample_search")
  LineageOrGraph      @alias("lineage_or_graph")
  ReportGeneration    @alias("report_generation")
  MemoryLookup        @alias("memory_lookup")
  ReporterSummary     @alias("reporter_summary")
  FileIoAndSummarization @alias("file_io_and_summarization")
  CodeAndScripts      @alias("code_and_scripts")
  BatchUploadPreparation @alias("batch_upload_preparation")
}
```

Then copy the file verbatim to the mirror:

```bash
cp dmac_assistant/baml_src/router.baml docker/cc-runtime/baml_src/router.baml
```

- [ ] **Step 4: Surface it in the Python wrapper**

In `nextseek_api/cc_assistant/router.py`, add `task_family` and `family_source` to the decision
dataclass, populate them from the BAML result on the BAML path, and set
`family_source="baml"` there. Leave the heuristic path's family `None` for now — Task 4 fills it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v 2>&1 | tee evidence/task03.log`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add dmac_assistant/baml_src/router.baml docker/cc-runtime/baml_src/router.baml nextseek_api/cc_assistant/router.py nextseek_api/cc_assistant/tests/test_router_family.py
git commit -m "feat(router): classify task_family in the same RouteQuery call"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v` exits 0, output saved to `evidence/task03.log`, **and** `pytest nextseek_api/cc_assistant/tests/test_baml_router_schema.py -v` still exits 0 (the pre-existing byte-identity and prompt-region pins must not regress).

**Failure conditions:** the two copies diverging; a second BAML function added; any family name not matching the capabilities file verbatim.

**Rollback:** `git revert`; no runtime consumer reads the field until Task 5.

---

### Task 4: Deterministic family for non-BAML turns

**Files:**
- Create: `nextseek_api/cc_assistant/family_fallback.py`
- Modify: `nextseek_api/cc_assistant/router.py`
- Test: `nextseek_api/cc_assistant/tests/test_family_fallback.py`

**Interfaces:**
- Consumes: `route_capabilities.json`.
- Produces: `family_for(route: str, query: str) -> tuple[str | None, str]` returning `(family, source)`.

The maintainer's ruling is that a family is always classified, including on forced and heuristic
turns. On those paths there is no model call, so the family is derived deterministically from the
route's declared families and the query text; when nothing matches, the family is `None` with an
explicit source rather than a guess.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_family_fallback.py
from nextseek_api.cc_assistant.family_fallback import family_for


def test_forced_container_turn_gets_a_source_even_without_a_match():
    fam, src = family_for("container_cc", "zzzz nothing matches zzzz")
    assert fam is None
    assert src == "unmatched"


def test_query_route_keyword_maps_to_a_declared_family():
    fam, src = family_for("nextseek_query", "find me all PBMCs in the BTC study")
    assert fam == "sample_search"
    assert src == "heuristic"


def test_fallback_never_returns_a_family_from_the_other_route():
    fam, _ = family_for("nextseek_query", "write a python script to plot ages")
    assert fam != "code_and_scripts"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# nextseek_api/cc_assistant/family_fallback.py
"""Deterministic task_family for turns that never reach the BAML router.

Families are route-scoped: a query-route turn can only be labelled with a
query-route family. No cross-route label is ever produced, because the routes
are disjoint and a cross-route label would fabricate an observation.
"""
import json
from pathlib import Path

_CAPS = Path(__file__).resolve().parents[2] / "dmac_assistant" / "build_context" / "route_capabilities.json"

# Keyword hints per family, drawn from each family's own example_queries.
_HINTS = {
    "sample_search": ("find", "search", "which samples", "what samples"),
    "lineage_or_graph": ("lineage", "how many samples", "what assays", "study"),
    "report_generation": ("geo", "sra", "nfcore", "pride", "submission"),
    "memory_lookup": ("those results", "that result", "previous"),
    "reporter_summary": ("rppr", "summary for", "project summary"),
    "file_io_and_summarization": ("read /data", "summarize", "walk me through"),
    "code_and_scripts": ("write a python", "script", "refactor"),
    "batch_upload_preparation": ("upload sheet", "update sheet", "batch-upload", "workbook"),
}


def _families_for_route(route):
    caps = json.loads(_CAPS.read_text())
    for entry in caps["routes"]:
        if entry["route_name"] == route:
            return [f["name"] for f in entry["task_families"]]
    return []


def family_for(route, query):
    text = (query or "").lower()
    for name in _families_for_route(route):
        if any(h in text for h in _HINTS.get(name, ())):
            return name, "heuristic"
    return None, "unmatched"
```

- [ ] **Step 4: Wire it into the router's non-BAML paths**

In `nextseek_api/cc_assistant/router.py`, on the heuristic path and the forced path, call
`family_for(route, query)` and set `task_family` / `family_source` from its result. Keep
`family_source="forced"` for the forced path by overriding the returned source there, so a forced
turn stays distinguishable from ordinary heuristic traffic.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_fallback.py nextseek_api/cc_assistant/tests/test_router_heuristic.py -v 2>&1 | tee evidence/task04.log`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/family_fallback.py nextseek_api/cc_assistant/router.py nextseek_api/cc_assistant/tests/test_family_fallback.py
git commit -m "feat(router): deterministic family label for forced and heuristic turns"
```

**Success condition:** Met only if the pytest command above exits 0, output saved to `evidence/task04.log`, and a test proves no cross-route family label is ever produced.

**Failure conditions:** a query-route turn labelled with a container-route family or vice versa; a forced turn indistinguishable from a heuristic one.

**Rollback:** `git revert`.

---

### Task 5: Both route writers record a ledger row

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (container path turn completion)
- Modify: `chat_nextseek/src/chat_nextseek/chat_memory.py` call site in `nextseek_api/assistant/session_adapter.py` (query path)
- Test: `nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py`

**Interfaces:**
- Consumes: `record_turn` (Task 2), `RouteDecision.task_family` / `.family_source` (Tasks 3–4).
- Produces: one `TurnLedger` row per completed turn on either route.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py
import pytest
from nextseek_api.assistant.models_db import ChatSession, TurnLedger

pytestmark = pytest.mark.django_db


def test_query_route_turn_creates_a_ledger_row(query_turn_factory):
    s = ChatSession.objects.create()
    query_turn_factory(session=s, turn_number=1, query="find me all PBMCs")
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "nextseek_query"
    assert row.family_source in {"baml", "heuristic", "unmatched"}


def test_container_route_turn_creates_a_ledger_row(cc_turn_factory):
    s = ChatSession.objects.create()
    cc_turn_factory(session=s, turn_number=1, query="write a python script")
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "container_cc"


def test_a_failed_turn_still_creates_a_ledger_row(cc_turn_factory):
    """Failures must not be invisible to the evaluator — that biases every rate."""
    s = ChatSession.objects.create()
    cc_turn_factory(session=s, turn_number=1, query="boom", fail=True)
    assert TurnLedger.objects.filter(session=s, turn_number=1).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py -v`
Expected: FAIL — `TurnLedger.DoesNotExist`

- [ ] **Step 3: Add the ledger write to both writers**

At each site that appends a turn to the JSON envelope, call `record_turn(...)` inside the **same**
`transaction.atomic()` block as the envelope save, so the two cannot diverge. Catch
`LedgerCollision` and log it; do not fail the user's turn because of a ledger collision.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ -k "ledger or router or family" -v 2>&1 | tee evidence/task05.log`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/cc_assistant.py nextseek_api/assistant/session_adapter.py nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py
git commit -m "feat(eval): record a ledger row on every turn, both routes"
```

**Success condition:** Met only if the pytest command exits 0, output saved to `evidence/task05.log`, the failure-path test passes (a failed turn still produces a row), and the full hermetic suite `pytest nextseek_api/cc_assistant/tests/` exits 0 with no new failures versus the pre-task baseline recorded in `evidence/baseline.log`.

**Failure conditions:** any turn type that completes without a ledger row; a ledger write outside the envelope's transaction; a user-visible error caused by a collision.

**Rollback:** `git revert`; the ledger becomes write-free again and nothing downstream exists yet.

---

## Phase 2 — Vendoring (gate: no task past here may reference a `dmac-assistant` checkout)

### Task 6: Vendor the evaluation package into this repository

**Files:**
- Create: `nextseek_api/eval/` (package: judge tooling + the four fit packages + their configs and templates)
- Modify: `pyproject.toml` (numerical dependencies)
- Create: `docker/eval/Dockerfile` (NExtSEEK-owned image, built from this repo's context)
- Test: `nextseek_api/cc_assistant/tests/test_eval_vendoring.py`

**Interfaces:**
- Produces: `nextseek_api.eval.*` importable with no `dmac_assistant` eval dependency; a `docker/eval/Dockerfile` whose build context is this repository.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_vendoring.py
import importlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_eval_package_is_importable():
    assert importlib.import_module("nextseek_api.eval") is not None


def test_the_dangling_exporter_reference_is_now_satisfied():
    """The vendored judge contract pins tools.hibayes.exporter.FailureMode.
    Before this task that module did not exist anywhere in the tree."""
    mod = importlib.import_module("nextseek_api.eval.exporter")
    assert hasattr(mod, "FailureMode")


def test_no_module_imports_from_a_dmac_assistant_eval_checkout():
    offenders = []
    for p in (_REPO / "nextseek_api" / "eval").rglob("*.py"):
        text = p.read_text()
        if "dmac_assistant.eval" in text or "from tools.hibayes" in text:
            offenders.append(str(p.relative_to(_REPO)))
    assert offenders == [], f"external eval imports remain: {offenders}"


def test_eval_dockerfile_builds_from_this_repo_not_a_bind_mount():
    df = (_REPO / "docker" / "eval" / "Dockerfile").read_text()
    assert "COPY nextseek_api/eval" in df
    assert "/work/src" not in df, "still expects a bind-mounted external checkout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_eval_vendoring.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.eval`

- [ ] **Step 3: Copy the source in, preserving behaviour**

Copy from the `dmac-assistant` checkout **once**, verbatim, adjusting only import paths:
- `tools/hibayes/{exporter,expected_behavior,artifact_validator,functional_inputs,enums}.py` → `nextseek_api/eval/`
- `tools/e2e/functional_evaluator.py` → `nextseek_api/eval/judge.py`
- `src/dmac_assistant/eval/hibayes_{runtime_reliability,artifact_validity,functional_usefulness,combined_report}/` → `nextseek_api/eval/fit/` including their `config/*.yaml` and templates

Do not change thresholds, band logic, model selection, or control flow. Record the source commit in a
header comment on each copied file: `# vendored from dmac-assistant @ dcca50c — do not diverge without a spec amendment`.

- [ ] **Step 4: Add the numerical dependencies and write the image**

```dockerfile
# docker/eval/Dockerfile — NExtSEEK-owned; build context is the repo root
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --group eval
COPY nextseek_api/eval /app/nextseek_api/eval
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "nextseek_api.eval.fit"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_eval_vendoring.py -v 2>&1 | tee evidence/task06.log`
Expected: 4 passed

- [ ] **Step 6: Prove it builds without the external checkout**

Run: `docker build -f docker/eval/Dockerfile -t nextseek-eval:dev . 2>&1 | tee evidence/task06-build.log`
Expected: exit 0

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/eval docker/eval/Dockerfile pyproject.toml uv.lock nextseek_api/cc_assistant/tests/test_eval_vendoring.py
git commit -m "feat(eval): vendor the evaluation pipeline into NExtSEEK"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/test_eval_vendoring.py -v` exits 0 with output at `evidence/task06.log`; `docker build -f docker/eval/Dockerfile .` exits 0 with output at `evidence/task06-build.log`; and `grep -rn "dmac_assistant.eval\|from tools.hibayes" nextseek_api/eval/` returns no matches.

**Failure conditions:** any import reaching outside this repo; a Dockerfile referencing `/work/src` or any host path; a copied file whose control flow or thresholds differ from the source.

**Rollback:** `git revert`; nothing imports `nextseek_api.eval` until Task 7.

---

### Task 7: Export ledger rows to the versioned eval schema

**Files:**
- Create: `nextseek_api/eval/export.py`
- Test: `nextseek_api/cc_assistant/tests/test_eval_export.py`

**Interfaces:**
- Consumes: `TurnLedger` (Task 1).
- Produces: `export_rows(since=None) -> list[EvalRow]`; `EVAL_ROW_SCHEMA_VERSION = 2`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_export.py
import pytest
from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.export import export_rows, EVAL_ROW_SCHEMA_VERSION

pytestmark = pytest.mark.django_db


def test_schema_is_versioned_and_not_the_legacy_14_column_shape():
    assert EVAL_ROW_SCHEMA_VERSION >= 2


def test_every_row_carries_route_and_family_as_separate_columns():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    row = export_rows()[0]
    assert row.route == "container_cc"
    assert row.task_family == "code_and_scripts"


def test_forced_turns_are_distinguishable_from_router_chosen_turns():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "forced")
    assert export_rows()[0].family_source == "forced"


def test_export_is_incremental_by_watermark():
    s = ChatSession.objects.create()
    a = record_turn(str(s.session_id), 1, "nextseek_query", "sample_search", "baml")
    record_turn(str(s.session_id), 2, "nextseek_query", "sample_search", "baml")
    assert len(export_rows(since=a.created_at)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_export.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.eval.export`

- [ ] **Step 3: Write the implementation**

```python
# nextseek_api/eval/export.py
"""Ledger -> versioned evaluation rows.

Supersedes the legacy 14-column offline format, which was built for a headless
fixture and carries no route column at all.
"""
from dataclasses import dataclass

from nextseek_api.assistant.models_db import TurnLedger

EVAL_ROW_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EvalRow:
    session_id: str
    turn_number: int
    route: str
    task_family: str | None
    family_source: str
    created_at: object
    schema_version: int = EVAL_ROW_SCHEMA_VERSION


def export_rows(since=None):
    qs = TurnLedger.objects.all().order_by("created_at")
    if since is not None:
        qs = qs.filter(created_at__gt=since)
    return [
        EvalRow(
            session_id=str(r.session_id),
            turn_number=r.turn_number,
            route=r.route,
            task_family=r.task_family,
            family_source=r.family_source,
            created_at=r.created_at,
        )
        for r in qs
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_export.py -v 2>&1 | tee evidence/task07.log`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/eval/export.py nextseek_api/cc_assistant/tests/test_eval_export.py
git commit -m "feat(eval): versioned exporter over the turn ledger"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task07.log`, and a test proves route and family are separate columns and that forced turns remain distinguishable.

**Failure conditions:** route collapsed into family; forced rows indistinguishable; a non-incremental export.

**Rollback:** `git revert`.

---

### Task 8: Judgment cache with fingerprint invalidation

**Files:**
- Create: `nextseek_api/eval/judge_cache.py`
- Modify: `nextseek_api/assistant/models_db.py` (add `TurnJudgment`)
- Create: `nextseek_api/migrations/0011_turn_judgment.py`
- Test: `nextseek_api/cc_assistant/tests/test_judge_cache.py`

**Interfaces:**
- Consumes: `TurnLedger`, `EvalRow`.
- Produces: `fingerprint(row, *, prompt_version, model_id, schema_version) -> str`; `needs_judging(rows, ...) -> list[EvalRow]`; `record_judgment(...)`; `record_failure(...)`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_judge_cache.py
import pytest
from nextseek_api.eval.judge_cache import fingerprint, needs_judging, record_judgment, record_failure

pytestmark = pytest.mark.django_db
_V = dict(prompt_version="p1", model_id="m1", schema_version=2)


def test_fingerprint_changes_when_prompt_version_changes(eval_row):
    a = fingerprint(eval_row, **_V)
    b = fingerprint(eval_row, **{**_V, "prompt_version": "p2"})
    assert a != b


def test_fingerprint_changes_when_model_changes(eval_row):
    assert fingerprint(eval_row, **_V) != fingerprint(eval_row, **{**_V, "model_id": "m2"})


def test_already_judged_row_is_not_rejudged(eval_row):
    record_judgment(eval_row, verdict={"ok": True}, **_V)
    assert needs_judging([eval_row], **_V) == []


def test_a_failed_judgment_is_retried_not_skipped(eval_row):
    """A failure must never look like a completed judgment — that silently
    drops exactly the turns most worth looking at."""
    record_failure(eval_row, error="timeout", **_V)
    assert needs_judging([eval_row], **_V) == [eval_row]


def test_version_bump_invalidates_an_existing_judgment(eval_row):
    record_judgment(eval_row, verdict={"ok": True}, **_V)
    assert needs_judging([eval_row], **{**_V, "prompt_version": "p2"}) == [eval_row]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_judge_cache.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Add the model and migration**

```python
# nextseek_api/assistant/models_db.py — append
class TurnJudgment(models.Model):
    turn = models.ForeignKey(TurnLedger, on_delete=models.CASCADE, related_name="judgments")
    fingerprint = models.CharField(max_length=64, db_index=True)
    verdict = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16)  # "ok" | "failed"
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["turn", "fingerprint"], name="uniq_turn_fingerprint")
        ]
```

Then: `docker exec -w /app nextseek uv run --no-sync python manage.py makemigrations nextseek_api --name turn_judgment`

- [ ] **Step 4: Write the cache**

```python
# nextseek_api/eval/judge_cache.py
"""Fingerprinted judgment cache.

A fingerprint covers the row identity AND the prompt, model and schema versions,
so a version bump invalidates cleanly. A failed judgment is stored as a failure
and is re-attempted next run; it is never treated as done. There is no
mtime-based skip anywhere in this module.
"""
import hashlib
import json

from nextseek_api.assistant.models_db import TurnJudgment


def fingerprint(row, *, prompt_version, model_id, schema_version):
    payload = json.dumps(
        {
            "session": row.session_id,
            "turn": row.turn_number,
            "route": row.route,
            "family": row.task_family,
            "prompt_version": prompt_version,
            "model_id": model_id,
            "schema_version": schema_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def needs_judging(rows, **versions):
    out = []
    for row in rows:
        fp = fingerprint(row, **versions)
        if not TurnJudgment.objects.filter(fingerprint=fp, status="ok").exists():
            out.append(row)
    return out


def _turn_pk(row):
    from nextseek_api.assistant.models_db import TurnLedger
    return TurnLedger.objects.get(session_id=row.session_id, turn_number=row.turn_number)


def record_judgment(row, *, verdict, **versions):
    TurnJudgment.objects.update_or_create(
        turn=_turn_pk(row), fingerprint=fingerprint(row, **versions),
        defaults={"verdict": verdict, "status": "ok", "error": None},
    )


def record_failure(row, *, error, **versions):
    TurnJudgment.objects.update_or_create(
        turn=_turn_pk(row), fingerprint=fingerprint(row, **versions),
        defaults={"verdict": None, "status": "failed", "error": error},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_judge_cache.py -v 2>&1 | tee evidence/task08.log`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/eval/judge_cache.py nextseek_api/assistant/models_db.py nextseek_api/migrations/0011_turn_judgment.py nextseek_api/cc_assistant/tests/test_judge_cache.py
git commit -m "feat(eval): fingerprinted judgment cache with fail-retry semantics"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task08.log`, and specifically that `test_a_failed_judgment_is_retried_not_skipped` and `test_version_bump_invalidates_an_existing_judgment` both pass. Additionally `grep -rn "mtime\|st_mtime\|getmtime" nextseek_api/eval/judge_cache.py` must return no matches.

**Failure conditions:** a failed judgment satisfying the cache; a fingerprint omitting any version input; any mtime-based skip.

**Rollback:** `git revert`; migration is additive.

---

### Task 9: Nightly Celery task with hard spend cap and force path

**Files:**
- Create: `nextseek_api/eval/tasks.py`
- Modify: `nextseek_api/batch_upload/celery_app.py:39-44` (beat schedule)
- Test: `nextseek_api/cc_assistant/tests/test_eval_task.py`

**Interfaces:**
- Consumes: `export_rows`, `needs_judging`, `record_judgment`, `record_failure`.
- Produces: Celery task `eval.nightly_judge`; `run_judging(force: bool = False, cap_usd: float) -> RunReport`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_task.py
import pytest
from nextseek_api.eval.tasks import run_judging

pytestmark = pytest.mark.django_db


def test_job_pauses_when_the_spend_cap_is_reached(many_rows, fake_judge):
    report = run_judging(cap_usd=0.02, judge=fake_judge(cost_per_call=0.01))
    assert report.paused_on_cap is True
    assert report.judged == 2


def test_job_judges_nothing_when_everything_is_cached(cached_rows, fake_judge):
    report = run_judging(cap_usd=100.0, judge=fake_judge())
    assert report.judged == 0


def test_force_rejudges_cached_rows(cached_rows, fake_judge):
    report = run_judging(cap_usd=100.0, force=True, judge=fake_judge())
    assert report.judged > 0


def test_a_judge_exception_is_recorded_as_failure_not_swallowed(one_row, failing_judge):
    report = run_judging(cap_usd=100.0, judge=failing_judge)
    assert report.failed == 1
    assert report.judged == 0


def test_no_paid_call_happens_without_an_explicit_judge(one_row):
    """The default path must not construct a live client."""
    with pytest.raises(ValueError, match="judge"):
        run_judging(cap_usd=100.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_task.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the task**

`run_judging` must: export rows since the last watermark; filter through `needs_judging` unless
`force`; call the injected `judge` per row, accumulating cost; stop and set `paused_on_cap` when the
running total would exceed `cap_usd`; record each result via `record_judgment` or `record_failure`;
and return a `RunReport(judged, failed, skipped, cost_usd, paused_on_cap)`. It must raise
`ValueError` if no `judge` is supplied — there is no implicit live client.

- [ ] **Step 4: Register the beat entry**

```python
# nextseek_api/batch_upload/celery_app.py — inside beat_schedule
"eval-nightly-judge": {
    "task": "eval.nightly_judge",
    "schedule": crontab(hour=3, minute=0),
},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_task.py -v 2>&1 | tee evidence/task09.log`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/eval/tasks.py nextseek_api/batch_upload/celery_app.py nextseek_api/cc_assistant/tests/test_eval_task.py
git commit -m "feat(eval): nightly incremental judging task with spend cap and force path"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task09.log`; `test_no_paid_call_happens_without_an_explicit_judge` passes; and `docker exec -w /app nextseek uv run --no-sync python -c "from nextseek_api.batch_upload.celery_app import app; print('eval.nightly_judge' in str(app.conf.beat_schedule))"` prints `True`.

**Failure conditions:** any code path constructing a live model client without an injected judge; a cap that is checked after the call rather than before; a swallowed judge exception.

**Rollback:** `git revert`; remove the beat entry.

---

### Task 10: Fit and publish posteriors

**Files:**
- Create: `nextseek_api/eval/publish.py`
- Modify: `nextseek_api/assistant/models_db.py` (add `FamilyPosterior`)
- Create: `nextseek_api/migrations/0012_family_posterior.py`
- Test: `nextseek_api/cc_assistant/tests/test_eval_publish.py`

**Interfaces:**
- Consumes: vendored fit package (Task 6), `TurnJudgment`.
- Produces: `publish(fit_result) -> int`; `FamilyPosterior(task_family, route, posterior_mean, band, n_total, fitted_at)`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_publish.py
import pytest
from nextseek_api.assistant.models_db import FamilyPosterior
from nextseek_api.eval.publish import publish

pytestmark = pytest.mark.django_db


def test_publish_stores_one_row_per_family_route_pair(fit_result):
    assert publish(fit_result) == len(fit_result.groups)


def test_band_and_n_are_persisted_for_consumers(fit_result):
    publish(fit_result)
    row = FamilyPosterior.objects.first()
    assert row.band in {"Reliable", "Watch", "Brittle", "TooUncertain"}
    assert row.n_total >= 0


def test_a_family_below_the_floor_is_too_uncertain(sparse_fit_result):
    publish(sparse_fit_result)
    assert FamilyPosterior.objects.get(task_family="memory_lookup").band == "TooUncertain"


def test_republishing_replaces_rather_than_duplicates(fit_result):
    publish(fit_result)
    publish(fit_result)
    assert FamilyPosterior.objects.filter(task_family=fit_result.groups[0].name).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_publish.py -v`
Expected: FAIL — `ImportError: FamilyPosterior`

- [ ] **Step 3: Add the model, migration, and publisher**

`FamilyPosterior` carries `task_family`, `route`, `posterior_mean`, `band`, `n_total`, `fitted_at`,
with a unique constraint on `(task_family, route)`. `publish` upserts one row per fitted group and
returns the count. The band value comes from the vendored fit package unchanged — do not
re-implement banding here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_publish.py -v 2>&1 | tee evidence/task10.log`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/eval/publish.py nextseek_api/assistant/models_db.py nextseek_api/migrations/0012_family_posterior.py nextseek_api/cc_assistant/tests/test_eval_publish.py
git commit -m "feat(eval): publish per-family posteriors to a consumer-readable table"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task10.log`, and `grep -rn "0.95\|Reliable" nextseek_api/eval/publish.py` returns no matches (banding must come from the vendored fit code, not be re-implemented in the publisher).

**Failure conditions:** banding logic duplicated in `publish.py`; duplicate rows on republish.

**Rollback:** `git revert`; migration additive.

---

## Phase 3 — Consumers (playbook first, per the maintainer's phasing ruling)

### Task 11: Container playbook consumer

**Files:**
- Create: `nextseek_api/cc_assistant/playbook.py`
- Modify: `nextseek_api/cc_assistant/ns_digest.py` (inject the playbook block)
- Test: `nextseek_api/cc_assistant/tests/test_playbook.py`

**Interfaces:**
- Consumes: `FamilyPosterior` (Task 10), `TurnJudgment` (Task 8).
- Produces: `build_playbook(user, project_ids) -> str`.

Content is aggregate statistics **plus** worked examples; example content is scoped to the
requesting user's own projects, preserving the injection channel's existing per-user scoping.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_playbook.py
import pytest
from nextseek_api.cc_assistant.playbook import build_playbook

pytestmark = pytest.mark.django_db


def test_playbook_includes_aggregate_statistics(posteriors, user_a):
    text = build_playbook(user_a, project_ids=[1])
    assert "batch_upload_preparation" in text
    assert "%" in text


def test_examples_never_come_from_another_users_projects(judgments_two_projects, user_a):
    text = build_playbook(user_a, project_ids=[1])
    assert "PROJECT_2_SECRET_STUDY" not in text


def test_a_too_uncertain_family_is_reported_as_such_not_as_a_rate(sparse_posteriors, user_a):
    text = build_playbook(user_a, project_ids=[1])
    assert "not enough data" in text.lower()


def test_playbook_makes_no_claim_about_the_other_route(posteriors, user_a):
    text = build_playbook(user_a, project_ids=[1]).lower()
    for phrase in ("would have", "instead of", "better than the other route"):
        assert phrase not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_playbook.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`build_playbook` reads `FamilyPosterior` for aggregate lines, and `TurnJudgment` joined through
`TurnLedger` → `ChatSession` filtered to `project_ids` for worked examples. A family in the
`TooUncertain` band renders as "not enough data yet", never as a rate. No sentence may compare
routes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_playbook.py -v 2>&1 | tee evidence/task11.log`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/playbook.py nextseek_api/cc_assistant/ns_digest.py nextseek_api/cc_assistant/tests/test_playbook.py
git commit -m "feat(eval): container playbook consumer, project-scoped examples"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task11.log`, and both `test_examples_never_come_from_another_users_projects` and `test_playbook_makes_no_claim_about_the_other_route` pass.

**Failure conditions:** any example text sourced outside the requesting user's projects; any cross-route comparative claim; a `TooUncertain` family rendered as a rate.

**Rollback:** `git revert`; the injection block disappears and the agent's context returns to its prior shape.

---

### Task 12: Routing risk overlay (flag-gated, default off)

**Files:**
- Create: `nextseek_api/cc_assistant/risk_overlay.py`
- Test: `nextseek_api/cc_assistant/tests/test_risk_overlay.py`

**Interfaces:**
- Consumes: `FamilyPosterior`.
- Produces: `assess(route, task_family) -> RiskVerdict(level, reason, may_reroute=False)`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_risk_overlay.py
import pytest
from nextseek_api.cc_assistant.risk_overlay import assess

pytestmark = pytest.mark.django_db


def test_brittle_family_is_flagged(brittle_posterior):
    v = assess("container_cc", "batch_upload_preparation")
    assert v.level == "high"


def test_unknown_family_falls_back_to_the_legacy_router(no_posteriors):
    v = assess("container_cc", "code_and_scripts")
    assert v.level == "unknown"


def test_too_uncertain_never_produces_a_confident_verdict(sparse_posterior):
    assert assess("nextseek_query", "memory_lookup").level == "unknown"


def test_overlay_can_never_authorise_a_reroute(brittle_posterior):
    """Option A: risk given the route taken. No counterfactual, ever."""
    assert assess("container_cc", "batch_upload_preparation").may_reroute is False


def test_overlay_is_disabled_by_default(settings, brittle_posterior):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = False
    assert assess("container_cc", "batch_upload_preparation").level == "disabled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_risk_overlay.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`assess` returns `disabled` unless the feature flag is on; `unknown` when there is no posterior or
the band is `TooUncertain`; otherwise a level derived from the band. `may_reroute` is a constant
`False` on every path — there is no branch that can set it true.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_risk_overlay.py -v 2>&1 | tee evidence/task12.log`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/risk_overlay.py nextseek_api/cc_assistant/tests/test_risk_overlay.py
git commit -m "feat(eval): routing risk overlay, default off, no reroute authority"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task12.log`, `test_overlay_can_never_authorise_a_reroute` passes, and `grep -n "may_reroute" nextseek_api/cc_assistant/risk_overlay.py` shows the field assigned only the literal `False`.

**Failure conditions:** any code path setting `may_reroute=True`; a confident verdict from a `TooUncertain` band; the overlay active by default.

**Rollback:** `git revert`; nothing consumes it while the flag is off.

---

### Task 12b: Map the live op inventory under the eight families

**Dependency:** after Task 3 (families declared). Land before Task 11 if the playbook is to give
op-level guidance rather than family-level only.

This is the one task that **intentionally breaks an anchor**: it edits the hash-pinned capabilities
file, and updates that pin in the same commit. Per the maintainer's ruling the mapping goes **inside**
the pinned file rather than beside it, so there is a single source of truth for the taxonomy.

**Files:**
- Modify: `dmac_assistant/build_context/route_capabilities.json`
- Modify: `nextseek_api/cc_assistant/tests/test_f_constraint_pins.py:12` (the pin constant)
- Test: `nextseek_api/cc_assistant/tests/test_family_op_mapping.py`

**Interfaces:**
- Produces: each `task_families[]` entry gains `ops: list[str]`, naming the live `nextseek-*` bins
  that serve that family.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_family_op_mapping.py
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CAPS = _REPO / "dmac_assistant" / "build_context" / "route_capabilities.json"
_BIN = _REPO / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "bin"


def _declared_ops():
    caps = json.loads(_CAPS.read_text())
    return {op for r in caps["routes"] for f in r["task_families"] for op in f.get("ops", [])}


def _live_bins():
    return {p.name for p in _BIN.iterdir() if p.is_file() and not p.name.startswith("_")}


def test_every_family_declares_an_ops_list():
    caps = json.loads(_CAPS.read_text())
    for r in caps["routes"]:
        for f in r["task_families"]:
            assert "ops" in f, f"family {f['name']} has no ops list"


def test_no_declared_op_is_missing_from_the_live_bin_inventory():
    missing = _declared_ops() - _live_bins()
    assert missing == set(), f"declared ops that do not exist as bins: {sorted(missing)}"


def test_every_live_bin_is_claimed_by_at_least_one_family():
    """Prevents silent drift as bins are added — the count moved 15 -> 17 unnoticed once."""
    unclaimed = _live_bins() - _declared_ops()
    assert unclaimed == set(), f"live bins claimed by no family: {sorted(unclaimed)}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_op_mapping.py -v`
Expected: FAIL — `family sample_search has no ops list`

- [ ] **Step 3: Add the ops lists**

Add an `ops` array to each of the eight `task_families` entries, assigning each of the live public
`nextseek-*` bins to exactly the families it serves. Every live bin must be claimed at least once and
no invented op names may appear — the tests above enforce both directions.

- [ ] **Step 4: Update the hash pin in the same commit**

```bash
NEW=$(shasum -a 256 dmac_assistant/build_context/route_capabilities.json | cut -d' ' -f1)
python3 - "$NEW" <<'PY'
import re, sys, pathlib
p = pathlib.Path("nextseek_api/cc_assistant/tests/test_f_constraint_pins.py")
s = p.read_text()
p.write_text(re.sub(r'CAPABILITIES_SHA256 = "[0-9a-f]{64}"',
                    f'CAPABILITIES_SHA256 = "{sys.argv[1]}"', s))
print("pin updated ->", sys.argv[1])
PY
```

- [ ] **Step 5: Run both test modules to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_op_mapping.py nextseek_api/cc_assistant/tests/test_f_constraint_pins.py -v 2>&1 | tee evidence/task12b.log`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add dmac_assistant/build_context/route_capabilities.json nextseek_api/cc_assistant/tests/test_f_constraint_pins.py nextseek_api/cc_assistant/tests/test_family_op_mapping.py
git commit -m "feat(taxonomy): map live nextseek ops under the eight task families"
```

**Success condition:** Met only if the pytest command above exits 0 with output at
`evidence/task12b.log`; the recomputed sha256 of the capabilities file equals the constant now in
`test_f_constraint_pins.py`; and both directions of the drift check pass (no declared op missing from
the bin inventory, no live bin unclaimed).

**Failure conditions:** the pin left stale (that test goes red); an invented op name; a live bin left
unclaimed; the mapping placed in a second file instead of the pinned one.

**Rollback:** `git revert` — restores both the file and its pin together, which is why they must be
one commit.

---

### Task 13: Coverage gate and gated live end-to-end

**Files:**
- Create: `nextseek_api/cc_assistant/tests/test_eval_live_e2e.py`
- Test: the whole suite

- [ ] **Step 1: Write the gated live test**

Its two fixtures live **in this module**, not in the shared conftest, so no paid client is
constructible from the default fixture set.

```python
# nextseek_api/cc_assistant/tests/test_eval_live_e2e.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EVAL_LIVE") != "1",
    reason="paid live evaluation; set RUN_EVAL_LIVE=1 to opt in",
)


@pytest.fixture
def live_judge():
    """The only fixture in the suite that may perform a paid call.
    Import is deferred so collecting this module never builds a client."""
    from nextseek_api.eval.judge import build_live_judge
    return build_live_judge()


@pytest.fixture
def one_real_turn(db):
    from nextseek_api.assistant.models_db import ChatSession
    from nextseek_api.cc_assistant.turn_ledger import record_turn
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    return s


@pytest.mark.django_db
def test_one_real_turn_flows_ledger_to_posterior(live_judge, one_real_turn):
    from nextseek_api.eval.tasks import run_judging
    from nextseek_api.eval.publish import publish
    from nextseek_api.assistant.models_db import FamilyPosterior

    report = run_judging(cap_usd=1.00, judge=live_judge)
    assert report.judged == 1
    publish(live_judge.last_fit)
    assert FamilyPosterior.objects.exists()
```

- [ ] **Step 2: Verify it is skipped by default**

Run: `pytest nextseek_api/cc_assistant/tests/test_eval_live_e2e.py -v`
Expected: 1 skipped, 0 passed — **no paid call**

- [ ] **Step 3: Run the full suite with coverage**

Run:
```bash
docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ \
  --cov=nextseek_api.eval --cov=nextseek_api.cc_assistant.turn_ledger \
  --cov=nextseek_api.cc_assistant.family_fallback --cov=nextseek_api.cc_assistant.playbook \
  --cov=nextseek_api.cc_assistant.risk_overlay --cov-report=term --cov-report=xml:evidence/coverage.xml \
  2>&1 | tee evidence/task13.log
```
Expected: exit 0, coverage ≥ 95%

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/cc_assistant/tests/test_eval_live_e2e.py evidence/coverage.xml
git commit -m "test(eval): gated live e2e and 95% coverage gate"
```

**Success condition:** Met only if the coverage command exits 0, `evidence/coverage.xml` exists, a validator confirms line coverage ≥ 95% across the listed modules, and `pytest nextseek_api/cc_assistant/tests/test_eval_live_e2e.py` with `RUN_EVAL_LIVE` unset reports **skipped** and makes no network call.

**Failure conditions:** coverage below 95%; the live test running without the opt-in; any paid call during an ordinary suite run.

**Rollback:** `git revert`.

---

## Freeze boundaries

Do not modify, in any task: the router's conversation-history contract; in-container op preference;
the heuristic router's routing semantics; the agent sandbox's isolation configuration; the Bayesian
model architecture or its band thresholds; any platform access-control code.

## Non-goals restated

No exploration or forced dual-routing; no propensity-weighted estimation; no re-routing from a
cross-route comparison; no backfill of historical turns into the ledger; no fix to platform
access-control gaps (tracked separately).
