# Bayesian Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run every `is_bayesian` variant through both engines with the BAML router forced out of the way, interleaved per question, and write a paired manifest that survives interruption.

**Architecture:** Extract `run_suite`'s per-variant body into `run_case()` as a pure refactor, thread `force_route` down into `http_driver.drive`, then add a sibling `bayesian.py` that calls `run_case()` twice per variant. `run_suite` keeps its behaviour byte for byte; everything hard-won about the poll loop, route observation and outage handling stays in one place instead of being forked.

**Tech Stack:** Python 3, pydantic v2, pytest. No new dependencies.

**Spec:** `docs/nessie-bayesian-mode-design.md` §7. **Depends on plan 1 being complete** (`corpus.bayesian_ids()` and `corpus.hibayes_meta()` must exist).

## Global Constraints

- **`run_suite`'s observable behaviour must not change.** The extraction is a pure refactor. The existing suite is the instrument; any change to what `run_suite` returns is a defect, not a feature.
- **Test command, exactly this, from the repo root:**
  ```bash
  uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
    python -m pytest nessie_tests/tests -q -p no:cacheprovider
  ```
- **No live turns in the unit suite.** Every test injects `post_query` / `get_progress` fakes, as `tests/test_runner.py` already does. A unit test that spends money is a bug.
- **`force_route` is admin-gated** (`nextseek_api/services/cc_assistant.py:245-251`) and a non-admin's value is silently dropped. This is the single largest way to waste a whole run. Task 4 exists for it alone.
- **The selection is 130 variants but 161 TURNS.** Anything keyed to the variant count — a cost estimate, a progress bar, a per-case timeout budget — runs about 24% low, per arm. The skew is not spread evenly: `refine_and_recall` is 25 variants and **50 turns**, because plan 1 took that whole family deliberately (it is where NS and CC differ most). `pipeline_nfcore` is 7 variants / 11 turns, `nessie_green` 3 / 4, `search_tree` 5 / 6; every other family is 1:1. Count turns, not pairs, whenever the number is about spend or wall-clock.
- **The three selected `nessie_route` variants produce NO reply to compare.** `route.ns_advanced`, `route.unrelated` and `route.ns_plain_study_membership` are all tagged `route_gate`, and `runner.py:155-158` drives every `route_gate` case route-only *even in a full run*: `http_driver.drive` breaks its poll loop at `route_decided`, so the harness never observes a final reply. A paired arm therefore has nothing to put in `last_reply`, and both a text comparison and an LLM grade of "which answer was better" are undefined for them. **Decide explicitly**, do not let it fall out: either score them on the ROUTE alone (which is all these cases ever asserted) or exclude them from the paired run.
  If you exclude them by clearing `is_bayesian`, you must also relax `test_bayesian_selection_is_nonempty_active_and_family_balanced` in `nessie_tests/tests/test_unified_corpus.py` — it asserts the selected families equal *all* active families, so dropping the only three `nessie_route` members turns that test red. Scoring on route alone needs no corpus change and is the cheaper of the two.
- **Never edit anything under `chat_nextseek/`.**
- **Conventional commits with module scopes.**

---

## File Structure

| File | Responsibility |
|---|---|
| `nessie_tests/http_driver.py` | **Modified.** `drive()` gains `force_route`, adds it to the POST body. |
| `nessie_tests/runner.py` | **Modified.** Per-variant loop body extracted to `run_case()`. `run_suite` calls it. |
| `nessie_tests/preflight.py` | **New.** One function that proves `force_route` is honoured before any spend. |
| `nessie_tests/bayes_manifest.py` | **New.** `BayesPair` and `BayesManifest`, wrapping the existing entry model. |
| `nessie_tests/bayesian.py` | **New.** Interleaved paired orchestration, budget ceiling, resume. |
| `nessie_tests/cli.py` | **Modified.** `--bayesian`, `--max-usd`, `--resume`, `--full-timeout`, mutual exclusion. |
| `nessie_tests/tests/test_run_case.py` | **New.** Extraction equivalence and forced-route behaviour. |
| `nessie_tests/tests/test_preflight.py` | **New.** |
| `nessie_tests/tests/test_bayesian.py` | **New.** Interleaving, budget, resume, manifest shape. |

---

## Task 1: Extract `run_case()` as a pure refactor

**Files:**
- Modify: `nessie_tests/runner.py:128-285`
- Test: `nessie_tests/tests/test_run_case.py`

**Interfaces:**
- Consumes: `http_driver.drive`, `evaluate.evaluate_turn`, `NessieManifestEntry`, all unchanged.
- Produces:
  ```python
  def run_case(v, *, tier, post_query, get_progress, bundle_reader=None,
               pace_s=0.0, force_route=None, strip_route_criteria=False,
               full_timeout_s=600.0, sleep=time.sleep, clock=time.monotonic
               ) -> NessieManifestEntry
  ```

- [ ] **Step 1: Record your baseline**

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider 2>&1 | tail -1
```

- [ ] **Step 2: Write the failing test**

Create `nessie_tests/tests/test_run_case.py`:

```python
"""`run_case` is the per-variant body `run_suite` used to inline.

The extraction is a pure refactor, so these tests assert the boundary rather than
the behaviour: the behaviour is already pinned by tests/test_runner.py, and if any
of those moved, the refactor was not pure.
"""
import pathlib

from nessie_tests import corpus, runner
from nessie_tests.manifest import NessieManifestEntry

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _fakes(route="nextseek_query", reply="ok", cost=None):
    """Minimal endpoint doubles. Mirrors the shape tests/test_runner.py uses."""
    def post_query(body):
        post_query.bodies.append(body)
        return {"task_id": "t1", "session_id": "s1"}
    post_query.bodies = []

    data = {"reply": reply, "session_id": "s1"}
    if cost is not None:
        data["total_cost_usd"] = cost

    def get_progress(_task_id):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "source": "forced"}},
            {"event": "query_complete", "data": data},
        ]}
    return post_query, get_progress


def _variant(vid="green.mus_ndma"):
    return next(v for v in corpus.merged(CORPUS) if v.id == vid)


def test_run_case_returns_exactly_one_entry():
    post_query, get_progress = _fakes()
    entry = runner.run_case(_variant(), tier="full",
                            post_query=post_query, get_progress=get_progress)
    assert isinstance(entry, NessieManifestEntry)
    assert entry.id == "green.mus_ndma"


def test_run_case_forces_new_on_the_first_turn_only():
    """Isolate the case, but keep its own follow-ups in the session its seed opened."""
    post_query, get_progress = _fakes()
    runner.run_case(_variant("refrec.refine_to_cd8"), tier="full",
                    post_query=post_query, get_progress=get_progress)
    bodies = post_query.bodies
    assert len(bodies) >= 2
    assert bodies[0].get("force_new") is True
    assert all("force_new" not in b for b in bodies[1:])
    assert all(b.get("session_id") == "s1" for b in bodies[1:])


def test_run_case_records_a_requires_env_skip_rather_than_failing():
    v = _variant().model_copy(update={"requires_env": ["NESSIE_DEFINITELY_UNSET"]})
    post_query, get_progress = _fakes()
    entry = runner.run_case(v, tier="full",
                            post_query=post_query, get_progress=get_progress)
    assert entry.status == "skipped"
    assert "requires_env unset" in entry.reason
    assert not post_query.bodies, "a skipped case must not hit the endpoint"


def test_run_case_skips_a_non_gate_case_at_route_tier():
    post_query, get_progress = _fakes()
    entry = runner.run_case(_variant(), tier="route",
                            post_query=post_query, get_progress=get_progress)
    assert entry.status == "skipped"
    assert "skipped at route tier" in entry.reason
```

- [ ] **Step 3: Run it and watch it fail**

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests/test_run_case.py -q -p no:cacheprovider
```
Expected: `AttributeError: module 'nessie_tests.runner' has no attribute 'run_case'`.

- [ ] **Step 4: Do the extraction, moving code without rewriting it**

In `nessie_tests/runner.py`, cut lines 128 to 285 (the `for v in variants:` body, from `expected_fail = ...` through `entries.append(NessieManifestEntry(...))`) into a new module-level function placed directly above `run_suite`:

```python
def run_case(v, *, tier, post_query, get_progress, bundle_reader=None,
             pace_s=0.0, force_route=None, strip_route_criteria=False,
             full_timeout_s=600.0, sleep=time.sleep, clock=time.monotonic
             ) -> NessieManifestEntry:
    """Drive one variant to an entry. The body `run_suite` used to inline.

    Extracted so `bayesian.py` can call it twice per variant with opposite
    `force_route` values without forking the poll loop, the route observation
    rules, the outage handling or the cost accounting. Every one of those has been
    a bug at least once; there must go on being exactly one of each.

    `force_route` and `strip_route_criteria` are inert unless set, so `run_suite`
    behaves exactly as it did before the extraction.
    """
```

Three edits inside the moved code, and nothing else:

1. The two `entries.append(...); continue` skip paths become `return NessieManifestEntry(...)`.
2. The final `entries.append(NessieManifestEntry(...))` becomes `return NessieManifestEntry(...)`.
3. The `http_driver.drive(...)` call gains `force_route=force_route` and
   `full_timeout_s=full_timeout_s`. `drive` already accepts the latter; it was
   simply never reachable from the CLI, which is why prior CC runs died on the
   hardcoded default with no way to raise it.

Then `run_suite`'s loop becomes:

```python
    for v in variants:
        entries.append(run_case(
            v, tier=tier, post_query=post_query, get_progress=get_progress,
            bundle_reader=bundle_reader, pace_s=pace_s, sleep=sleep, clock=clock))
```

Do **not** take the opportunity to tidy anything inside the moved block. Every comment in it records a specific past failure; a "cleanup" during a move is how those get lost.

- [ ] **Step 5: Add `force_route` to the driver**

In `nessie_tests/http_driver.py`, add the parameter to `drive`'s signature after `force_new`:

```python
          force_route: str | None = None,
```

and immediately after `body = {"query": query, "mode": mode}`:

```python
    if force_route:
        # Admin-only server side; a non-admin's value is silently dropped back to
        # the router (cc_assistant.py:245-251). `preflight.assert_force_route_works`
        # is what stops that turning into a whole run of meaningless data.
        body["force_route"] = force_route
```

- [ ] **Step 6: Run the whole suite**

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider 2>&1 | tail -3
```
Expected: your Step 1 baseline plus 4 new passes. **Any** existing test that changed status means the extraction was not pure. Revert and redo the move rather than adjusting the test.

- [ ] **Step 7: Commit**

```bash
git add nessie_tests/runner.py nessie_tests/http_driver.py nessie_tests/tests/test_run_case.py
git commit -m "refactor(nessie): extract run_case() from run_suite, thread force_route

Pure refactor: run_suite's per-variant body moves out unchanged so bayesian.py
can call it twice per variant with opposite force_route values, rather than
forking the poll loop, route observation, outage handling and cost accounting.

force_route and strip_route_criteria are inert unless set."
```

---

## Task 2: Strip route criteria under forcing

**Files:**
- Modify: `nessie_tests/runner.py` (inside `run_case`)
- Test: `nessie_tests/tests/test_run_case.py`

**Interfaces:**
- Consumes: `run_case`'s `strip_route_criteria` parameter from Task 1.
- Produces: `runner.STRIPPED_UNDER_FORCING = frozenset({"route", "engine"})`, and `entry.reason` gaining a `stripped N route criteria` note when any were removed.

- [ ] **Step 1: Write the failing test**

Append to `nessie_tests/tests/test_run_case.py`:

```python
def test_route_criteria_are_stripped_when_forcing():
    """Forcing the route makes a route assertion tautological: it tests the harness,
    not the product. Every one of them goes, whatever its origin, including what
    corpus.apply_route_policy injects."""
    v = _variant("route.unrelated")
    post_query, get_progress = _fakes(route="nextseek_query")
    entry = runner.run_case(v, tier="full", force_route="ns", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)
    fields = {o.field for o in entry.observations}
    assert not (fields & runner.STRIPPED_UNDER_FORCING)


def test_route_criteria_survive_when_not_forcing():
    """run_suite must be unaffected. The flag is the only thing that changes this."""
    v = _variant("route.unrelated")
    post_query, get_progress = _fakes(route="unrelated")
    entry = runner.run_case(v, tier="route",
                            post_query=post_query, get_progress=get_progress)
    fields = {o.field for o in entry.observations}
    assert "route" in fields


def test_the_stripped_count_is_recorded_rather_than_silent():
    v = _variant("route.unrelated")
    post_query, get_progress = _fakes()
    entry = runner.run_case(v, tier="full", force_route="ns", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)
    assert "stripped" in entry.reason and "route criteri" in entry.reason


def test_known_fail_does_not_become_xpass_under_forcing():
    """The tag records an expectation about ROUTER-DECIDED NS behaviour. A forced
    arm says nothing about it, so promoting a pass to xpass would claim the
    expected failure had stopped happening on evidence that cannot support it."""
    v = _variant().model_copy(update={"tags": ["nessie", "full", "known_fail"]})
    post_query, get_progress = _fakes()
    entry = runner.run_case(v, tier="full", force_route="cc", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)
    assert entry.status != "xpass"
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `AttributeError: ... has no attribute 'STRIPPED_UNDER_FORCING'`.

- [ ] **Step 3: Implement**

In `nessie_tests/runner.py`, above `run_case`:

```python
# Criteria that cannot be honestly evaluated once the route is forced. A route
# assertion under `force_route` tests the harness's own request body, not the
# product's routing, so keeping it would manufacture a pass on every arm that
# happens to agree and a failure on every arm that does not. Neither is evidence.
STRIPPED_UNDER_FORCING = frozenset({"route", "engine"})
```

Inside `run_case`, where `criteria` is currently built:

```python
                criteria = list(turn.pass_criteria) + ([extra] if extra else [])
                if strip_route_criteria:
                    kept = [c for c in criteria
                            if getattr(c, "field", c.get("field") if isinstance(c, dict) else None)
                            not in STRIPPED_UNDER_FORCING]
                    stripped += len(criteria) - len(kept)
                    criteria = kept
```

Initialise `stripped = 0` beside the other per-case accumulators near the top of `run_case`, and before building the entry:

```python
        if stripped:
            note = f"stripped {stripped} route criteri{'on' if stripped == 1 else 'a'} (forced route)"
            reason = f"{reason}; {note}" if reason else note
```

Guard `_apply_xpass` so forcing disables the promotion:

```python
        if strip_route_criteria:
            # A forced arm is not evidence about a known_fail expectation.
            xpass_reason = None
        else:
            v_status, xpass_reason = _apply_xpass(v_status, expected_fail)
```

- [ ] **Step 4: Run the whole suite**

Expected: baseline plus 8 new passes, nothing else moved.

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/runner.py nessie_tests/tests/test_run_case.py
git commit -m "feat(nessie): strip route/engine criteria and disable xpass under forcing

A route assertion under force_route tests the harness's own request body, not the
product's routing. The stripped count lands in entry.reason so the removal is
visible rather than quiet."
```

---

## Task 3: The paired manifest

**Files:**
- Create: `nessie_tests/bayes_manifest.py`
- Test: `nessie_tests/tests/test_bayesian.py`

**Interfaces:**
- Consumes: `NessieManifestEntry` from `nessie_tests/manifest.py`.
- Produces:
  ```python
  class BayesPair(BaseModel):
      id: str; family: str; hibayes_subtype: str | None
      ns: NessieManifestEntry | None; cc: NessieManifestEntry | None
  class BayesManifest(BaseModel):
      run_meta: dict; pairs: list[BayesPair]
  def write_bayes_manifest(m: BayesManifest, out_dir) -> pathlib.Path
  def read_bayes_manifest(out_dir) -> BayesManifest | None
  def completed_arms(m: BayesManifest) -> set[tuple[str, str]]
  ```

- [ ] **Step 1: Write the failing test**

Create `nessie_tests/tests/test_bayesian.py`:

```python
import json

from nessie_tests import bayes_manifest as bm
from nessie_tests.manifest import NessieManifest, NessieManifestEntry, write_manifest


def _entry(vid="x.y", status="passed", cost=None):
    return NessieManifestEntry(id=vid, family="f", tier="full", status=status, cost=cost)


def test_a_pair_holds_both_arms():
    p = bm.BayesPair(id="x.y", family="f", hibayes_subtype="Search-Basic",
                     ns=_entry(), cc=_entry())
    assert p.ns.status == "passed" and p.cc.status == "passed"


def test_a_half_finished_pair_is_representable():
    """Pairs are written as they complete so --resume works. A pair whose CC arm
    has not run yet must round-trip rather than fail validation."""
    p = bm.BayesPair(id="x.y", family="f", hibayes_subtype=None, ns=_entry(), cc=None)
    assert bm.BayesPair.model_validate(json.loads(p.model_dump_json())).cc is None


def test_manifest_round_trips_through_disk(tmp_path):
    m = bm.BayesManifest(run_meta={"mode": "bayesian"},
                         pairs=[bm.BayesPair(id="x.y", family="f", hibayes_subtype=None,
                                             ns=_entry(), cc=_entry())])
    bm.write_bayes_manifest(m, tmp_path)
    assert bm.read_bayes_manifest(tmp_path).pairs[0].id == "x.y"


def test_read_returns_none_when_there_is_nothing_to_resume(tmp_path):
    assert bm.read_bayes_manifest(tmp_path) is None


def test_a_normal_run_directory_is_not_mistaken_for_a_resumable_paired_run(tmp_path):
    """A `run_suite` manifest must not read back as a paired one.

    Both models tolerate the other's JSON: pydantic ignores extra keys and both
    `BayesManifest` fields default, so a normal manifest would validate as an
    EMPTY paired manifest rather than raising. `--resume` would then see zero
    completed arms and repay for every arm of a ~150-variant two-engine run, and
    the first per-pair write would overwrite the prior run's record beyond
    recovery. The manifests must therefore not share a filename.
    """
    write_manifest(NessieManifest(started_at="t0", ended_at="t1", tier="full", scope="all"),
                   tmp_path / "manifest.json")
    assert bm.read_bayes_manifest(tmp_path) is None


def test_completed_arms_reports_only_arms_that_actually_ran():
    m = bm.BayesManifest(run_meta={}, pairs=[
        bm.BayesPair(id="a", family="f", hibayes_subtype=None, ns=_entry("a"), cc=_entry("a")),
        bm.BayesPair(id="b", family="f", hibayes_subtype=None, ns=_entry("b"), cc=None),
    ])
    assert bm.completed_arms(m) == {("a", "ns"), ("a", "cc"), ("b", "ns")}
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError: No module named 'nessie_tests.bayes_manifest'`.

- [ ] **Step 3: Implement**

Create `nessie_tests/bayes_manifest.py`:

```python
"""The paired run's record.

Wraps `NessieManifestEntry` UNCHANGED rather than defining a parallel entry model,
so outage detection, cost accounting and the observation schema all apply here
without a second implementation that can drift from the first.
"""
from __future__ import annotations

import json
import pathlib

from pydantic import BaseModel, Field

from nessie_tests.manifest import NessieManifestEntry

# NOT "manifest.json" — that is what `runner.run_suite` already writes for a
# normal run (runner.py:412), and a paired manifest is a DIFFERENT SCHEMA that
# happens to be structurally compatible in the worst possible way. Sharing the
# name is silently destructive in both directions:
#
#   Reading — pydantic ignores extra keys and both `BayesManifest` fields have
#   defaults, so a normal `manifest.json` validates as an EMPTY `BayesManifest`
#   rather than raising. `completed_arms` then returns the empty set, `--resume`
#   concludes nothing has run, and the whole paired run is repaid — the exact
#   outcome `completed_arms` exists to prevent.
#
#   Writing — pairs are written as they complete, so the FIRST pair overwrites
#   the prior run's record. `load_manifest` on the result fails with 4 missing
#   required fields (`started_at`, `ended_at`, `tier`, `scope`) and that run's
#   `entries` are gone for good.
#
# A distinct filename makes the collision impossible instead of merely unlikely,
# which is worth more than the shared constant it costs. Pinned by
# `test_a_normal_run_directory_is_not_mistaken_for_a_resumable_paired_run`.
MANIFEST_NAME = "bayes_manifest.json"


class BayesPair(BaseModel):
    id: str
    family: str
    hibayes_subtype: str | None = None
    # Either arm may be None: pairs are written as they complete so an interrupted
    # run can resume, and a half-written pair must round-trip rather than fail.
    ns: NessieManifestEntry | None = None
    cc: NessieManifestEntry | None = None


class BayesManifest(BaseModel):
    run_meta: dict = Field(default_factory=dict)
    pairs: list[BayesPair] = Field(default_factory=list)


def write_bayes_manifest(m: BayesManifest, out_dir) -> pathlib.Path:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_NAME
    path.write_text(m.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_bayes_manifest(out_dir) -> BayesManifest | None:
    path = pathlib.Path(out_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    return BayesManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def completed_arms(m: BayesManifest) -> set[tuple[str, str]]:
    """`(variant_id, arm)` for every arm that produced an entry.

    This is what `--resume` skips. Keyed on the ARM rather than the pair, because
    a run interrupted between the NS and CC halves of one question must not repay
    for the NS half.
    """
    done: set[tuple[str, str]] = set()
    for p in m.pairs:
        if p.ns is not None:
            done.add((p.id, "ns"))
        if p.cc is not None:
            done.add((p.id, "cc"))
    return done
```

> **Changed during execution (2026-08-04, commit `dca1e31` + fix commit).** This plan
> originally specified `MANIFEST_NAME = "manifest.json"`, which collides with the file
> `runner.run_suite` already writes at `runner.py:412`; the collision was reproduced as
> both a silent empty-resume (repaying a whole paired run) and a destructive overwrite of
> a prior run's manifest (`load_manifest` then fails with 4 missing required fields). The
> name is now `bayes_manifest.json` and is pinned by
> `test_a_normal_run_directory_is_not_mistaken_for_a_resumable_paired_run`. Do not
> "restore" the shared name.

- [ ] **Step 4: Run and commit**

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider 2>&1 | tail -3
git add nessie_tests/bayes_manifest.py nessie_tests/tests/test_bayesian.py
git commit -m "feat(nessie): BayesPair/BayesManifest wrapping the existing entry model

Either arm may be None so pairs can be written as they complete and --resume can
skip at arm granularity rather than repaying for a finished half."
```

---

## Task 4: The preflight that stops a worthless run

**Files:**
- Create: `nessie_tests/preflight.py`
- Test: `nessie_tests/tests/test_preflight.py`

**Interfaces:**
- Consumes: `http_driver.drive` with `force_route` from Task 1.
- Produces: `preflight.assert_force_route_works(post_query, get_progress) -> None`, raising `preflight.ForceRouteRejected`.

- [ ] **Step 1: Write the failing test**

Create `nessie_tests/tests/test_preflight.py`:

```python
"""force_route is admin-only and a non-admin's value is DROPPED SILENTLY.

Without this check the harness sends force_route on all 300 turns, the server
ignores every one of them, the router picks whatever it likes, and the run
completes looking perfectly healthy while measuring nothing it claims to measure.

The discriminator is cheap and exact: `_decide_route` returns ROUTE_NS/ROUTE_CC
for a forced decision and NEVER ROUTE_UNRELATED. So send an out-of-scope question
forced to `ns`. If it comes back `unrelated`, the force was dropped.
"""
import pytest

from nessie_tests import preflight


def _fakes(route, source="forced"):
    def post_query(body):
        post_query.bodies.append(body)
        return {"task_id": "t", "session_id": "s"}
    post_query.bodies = []

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "source": source}},
            {"event": "query_complete", "data": {"reply": "r", "session_id": "s"}},
        ]}
    return post_query, get_progress


def test_passes_when_the_force_is_honoured():
    post_query, get_progress = _fakes("nextseek_query")
    preflight.assert_force_route_works(post_query, get_progress)
    assert post_query.bodies[0]["force_route"] == "ns"


def test_raises_when_the_force_was_dropped():
    """`unrelated` is only reachable through the router, so it proves the drop."""
    post_query, get_progress = _fakes("unrelated", source="baml")
    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress)
    assert "is_staff" in str(e.value)


def test_raises_when_the_source_is_not_forced():
    """Belt and braces: the route can coincidentally match while the force was
    still ignored. `source` is the direct evidence, `route` is the fallback."""
    post_query, get_progress = _fakes("nextseek_query", source="baml")
    with pytest.raises(preflight.ForceRouteRejected):
        preflight.assert_force_route_works(post_query, get_progress)


def test_uses_exactly_one_turn():
    post_query, get_progress = _fakes("nextseek_query")
    preflight.assert_force_route_works(post_query, get_progress)
    assert len(post_query.bodies) == 1
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError: No module named 'nessie_tests.preflight'`.

- [ ] **Step 3: Implement**

Create `nessie_tests/preflight.py`:

```python
"""Prove `force_route` is honoured before a paired run spends anything.

`_decide_route` (nextseek_api/services/cc_assistant.py:245-251) drops a
non-admin's `force_route` and falls back to the router. Nothing in the response
says so. A whole 300-turn run would complete, cost real money, and measure the
router instead of the engines.
"""
from __future__ import annotations

from nessie_tests import http_driver

PROBE_QUERY = "What is the weather in Boston tomorrow?"


class ForceRouteRejected(RuntimeError):
    """The server ignored `force_route`. The account is almost certainly not staff."""


def assert_force_route_works(post_query, get_progress) -> None:
    """One out-of-scope turn forced to `ns`. Raises if the force was dropped.

    Out-of-scope on purpose. A forced decision is ROUTE_NS or ROUTE_CC and never
    ROUTE_UNRELATED, so a question the router WOULD call unrelated gives a clean
    two-valued answer: `nextseek_query` means the force landed, `unrelated` means
    it did not. Cheapest possible discriminator, and it is an NS turn either way.
    """
    res = http_driver.drive(PROBE_QUERY, tier="full", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    route, source = res.route_obs.route, res.route_obs.source
    if source == "forced" and route != "unrelated":
        return
    raise ForceRouteRejected(
        f"force_route was not honoured: route={route!r} source={source!r}, expected "
        f"route='nextseek_query' source='forced'.\n"
        f"force_route is gated on is_staff/is_superuser and a non-admin's value is "
        f"dropped silently. Run --bayesian as a staff account; the harness default "
        f"'demo' is not one. Without this the whole run measures the router, not "
        f"the engines.")
```

- [ ] **Step 4: Run and commit**

```bash
git add nessie_tests/preflight.py nessie_tests/tests/test_preflight.py
git commit -m "feat(nessie): preflight that force_route is actually honoured

One out-of-scope turn forced to ns. A forced decision is never ROUTE_UNRELATED,
so 'unrelated' proves the force was dropped. Aborts before any spend rather than
letting 300 turns complete while measuring the router."
```

---

## Task 5: The paired orchestrator

**Files:**
- Create: `nessie_tests/bayesian.py`
- Test: `nessie_tests/tests/test_bayesian.py`

**Interfaces:**
- Consumes: `corpus.bayesian_ids`, `corpus.hibayes_meta`, `corpus.merged`, `runner.run_case`, `runner.corpus_fingerprint`, `runner.git_sha`, `preflight.assert_force_route_works`, everything in `bayes_manifest`.
- Produces:
  ```python
  ARMS = ("ns", "cc")
  class BudgetExceeded(RuntimeError): ...
  def run_paired(*, base_url, auth_header, out_dir, corpus_path=None,
                 post_query=None, get_progress=None, max_usd=None, resume=False,
                 full_timeout_s=600.0, pace_s=0.0, skip_preflight=False,
                 sleep=time.sleep, clock=time.monotonic) -> BayesManifest
  ```

- [ ] **Step 1: Write the failing test**

Append to `nessie_tests/tests/test_bayesian.py`:

```python
import pathlib

import pytest

from nessie_tests import bayesian, corpus

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _recording_fakes(cost_per_cc=0.10):
    """Records the exact order of (query, force_route) so interleaving is provable."""
    calls = []

    def post_query(body):
        calls.append((body["query"], body.get("force_route")))
        return {"task_id": f"t{len(calls)}", "session_id": f"s{len(calls)}"}

    def get_progress(_):
        arm = calls[-1][1]
        data = {"reply": "ok", "session_id": "s"}
        if arm == "cc":
            data["total_cost_usd"] = cost_per_cc
        return {"status": "completed", "progress": [
            {"event": "route_decided",
             "data": {"route": "container_cc" if arm == "cc" else "nextseek_query",
                      "source": "forced"}},
            {"event": "query_complete", "data": data},
        ]}
    return post_query, get_progress, calls


def test_arms_are_interleaved_per_question_not_run_as_two_passes(tmp_path):
    """Two passes would confound engine with wall-clock time: an outage during one
    pass becomes a fake engine effect. Interleaving is the entire reason the design
    is paired, so it is asserted directly rather than assumed."""
    post_query, get_progress, calls = _recording_fakes()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True)
    arms = [arm for _q, arm in calls]
    assert arms[:4] == ["ns", "cc", "ns", "cc"], arms[:8]


def test_every_selected_variant_produces_a_complete_pair(tmp_path):
    post_query, get_progress, _ = _recording_fakes()
    m = bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True)
    assert [p.id for p in m.pairs] == corpus.bayesian_ids(CORPUS)
    assert all(p.ns is not None and p.cc is not None for p in m.pairs)


def test_run_meta_records_what_makes_two_runs_comparable(tmp_path):
    post_query, get_progress, _ = _recording_fakes()
    m = bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True)
    assert m.run_meta["mode"] == "bayesian"
    assert m.run_meta["arms"] == ["ns", "cc"]
    assert m.run_meta["corpus_fingerprint"]
    assert m.run_meta["selected_ids"] == corpus.bayesian_ids(CORPUS)


def test_budget_ceiling_aborts_rather_than_running_on(tmp_path):
    post_query, get_progress, calls = _recording_fakes(cost_per_cc=1.00)
    with pytest.raises(bayesian.BudgetExceeded):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True, max_usd=2.50)
    assert len(calls) < 2 * len(corpus.bayesian_ids(CORPUS))


def test_budget_treats_an_unobserved_cost_as_unknown_not_zero(tmp_path):
    """NS turns emit no total_cost_usd. Summing None as 0 would understate spend
    and let a run sail past its ceiling; the manifest already distinguishes
    'no cost observed' from 'free' and this must too."""
    assert bayesian._spent([None, 0.5, None, 0.25]) == 0.75


def test_the_manifest_is_written_as_each_pair_completes(tmp_path):
    """Written per pair, not at the end, which is what makes resume possible after
    a crash, a timeout or a Ctrl-C."""
    from nessie_tests import bayes_manifest as bm
    seen = []

    def post_query(body):
        seen.append(bm.read_bayes_manifest(tmp_path))
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "nextseek_query", "source": "forced"}},
            {"event": "query_complete", "data": {"reply": "ok", "session_id": "s"}},
        ]}

    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True)
    written = [m for m in seen if m is not None]
    assert written, "nothing was written until the run ended"
    assert len(written[-1].pairs) > 1


def test_resume_skips_completed_arms_and_reruns_nothing(tmp_path):
    post_query, get_progress, calls = _recording_fakes()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True)
    first = len(calls)
    calls.clear()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True, resume=True)
    assert first > 0
    assert calls == [], "resume re-ran arms that had already completed"


def test_preflight_runs_by_default_and_aborts_the_run(tmp_path):
    from nessie_tests import preflight

    def post_query(_body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "unrelated", "source": "baml"}},
            {"event": "query_complete", "data": {"reply": "r", "session_id": "s"}},
        ]}

    with pytest.raises(preflight.ForceRouteRejected):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress)
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError: No module named 'nessie_tests.bayesian'`.

- [ ] **Step 3: Implement**

Create `nessie_tests/bayesian.py`:

```python
"""Paired dual-route evaluation: every selected variant through BOTH engines.

Selection is `corpus.bayesian_ids()` and nothing else. No tier, no scope, no
sample, no seed: one flag, one source, so "what ran" always has exactly one
answer. `--cases` already refuses to mix selection sources for the same reason.
"""
from __future__ import annotations

import time

from nessie_tests import corpus, http_driver, preflight, runner
from nessie_tests.bayes_manifest import (
    BayesManifest, BayesPair, completed_arms, read_bayes_manifest, write_bayes_manifest,
)

ARMS = ("ns", "cc")


class BudgetExceeded(RuntimeError):
    """The run-level USD ceiling was reached. Resume with --resume after raising it."""


def _spent(costs) -> float:
    """Sum observed costs, skipping unobserved ones.

    `None` is NOT zero. Only container_cc emits `total_cost_usd` at all, so an NS
    arm always contributes `None`; treating that as 0.0 would be an accounting
    claim the harness cannot support, and `manifest.cost_summary` already refuses
    to make it.
    """
    return sum(c for c in costs if c is not None)


def run_paired(*, base_url, auth_header, out_dir, corpus_path=None,
               post_query=None, get_progress=None, max_usd=None, resume=False,
               full_timeout_s=600.0, pace_s=0.0, skip_preflight=False,
               sleep=time.sleep, clock=time.monotonic) -> BayesManifest:
    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)

    if not skip_preflight:
        # Before anything is selected or spent. A dropped force makes the entire
        # run measure the router instead of the engines.
        preflight.assert_force_route_works(post_query, get_progress)

    selected = corpus.bayesian_ids(corpus_path)
    by_id = {v.id: v for v in corpus.merged(corpus_path)}

    prior = read_bayes_manifest(out_dir) if resume else None
    done = completed_arms(prior) if prior else set()
    pairs = {p.id: p for p in (prior.pairs if prior else [])}

    manifest = BayesManifest(
        run_meta={
            "mode": "bayesian",
            "arms": list(ARMS),
            "corpus_fingerprint": runner.corpus_fingerprint(corpus_path),
            "git_sha": runner.git_sha(),
            "base_url": base_url,
            "selected_ids": selected,
            "max_usd": max_usd,
            "resumed": bool(prior),
        },
        pairs=[],
    )

    costs: list[float | None] = []
    for vid in selected:
        v = by_id[vid]
        meta = corpus.hibayes_meta(vid, corpus_path)
        pair = pairs.get(vid) or BayesPair(
            id=vid, family=v.family, hibayes_subtype=meta["hibayes_subtype"])

        # Both arms for THIS question before moving on. Two passes would confound
        # engine with wall-clock time, and a provider outage during one pass would
        # read as a real engine effect the model cannot separate out.
        for arm in ARMS:
            if (vid, arm) in done:
                costs.append(getattr(pair, arm).cost if getattr(pair, arm) else None)
                continue
            if max_usd is not None and _spent(costs) >= max_usd:
                manifest.pairs.append(pair)
                write_bayes_manifest(manifest, out_dir)
                raise BudgetExceeded(
                    f"spent ${_spent(costs):.2f} of ${max_usd:.2f} before {vid}:{arm}. "
                    f"Raise --max-usd and rerun with --resume; completed arms are kept.")
            entry = runner.run_case(
                v, tier="full", post_query=post_query, get_progress=get_progress,
                pace_s=pace_s, force_route=arm, strip_route_criteria=True,
                full_timeout_s=full_timeout_s, sleep=sleep, clock=clock)
            setattr(pair, arm, entry)
            costs.append(entry.cost)

        manifest.pairs.append(pair)
        # Per pair, not at the end: a crash, a timeout or a Ctrl-C must leave a
        # resumable manifest rather than nothing.
        write_bayes_manifest(manifest, out_dir)

    return manifest
```

- [ ] **Step 4: Run the whole suite**

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider 2>&1 | tail -3
```
Expected: baseline plus all of Task 3's and Task 5's tests.

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/bayesian.py nessie_tests/tests/test_bayesian.py
git commit -m "feat(nessie): paired interleaved orchestrator with budget and resume

Both arms per question before moving on, so a provider outage or a load spike
hits both equally instead of becoming a fake engine effect. Manifest is written
per pair so an interrupted run resumes at arm granularity. Unobserved NS costs
are skipped rather than summed as zero."
```

---

## Task 6: Wire the CLI

**Files:**
- Modify: `nessie_tests/cli.py`
- Test: `nessie_tests/tests/test_cli.py`

**Interfaces:**
- Consumes: `bayesian.run_paired`, `bayesian.BudgetExceeded`.
- Produces: `--bayesian`, `--max-usd`, `--resume`, `--full-timeout` on the existing parser.

- [ ] **Step 1: Write the failing test**

Append to `nessie_tests/tests/test_cli.py`:

```python
def test_bayesian_flag_parses_with_its_own_options():
    a = cli.build_parser().parse_args(
        ["--base-url", "http://x", "--bayesian", "--max-usd", "40", "--resume"])
    assert a.bayesian is True and a.max_usd == 40.0 and a.resume is True


def test_bayesian_defaults_its_own_output_directory():
    a = cli.build_parser().parse_args(["--base-url", "http://x", "--bayesian"])
    assert str(a.out) == "nessie_out_bayes"


@pytest.mark.parametrize("extra", [
    ["--tier", "full"], ["--scope", "all"], ["--sample", "0.5"],
    ["--seed", "3"], ["--cases", "p.json"],
])
def test_bayesian_refuses_every_other_selection_source(extra, capsys):
    """is_bayesian IS the selection. Two sources for 'what ran' makes a run
    unexplainable, which is the same reason --cases already refuses them."""
    with pytest.raises(SystemExit):
        cli.main(["--base-url", "http://x", "--bayesian", *extra])
    assert "--bayesian" in capsys.readouterr().err
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `AttributeError: 'Namespace' object has no attribute 'bayesian'`.

- [ ] **Step 3: Implement**

In `nessie_tests/cli.py`, add to `build_parser()` before `return p`:

```python
    p.add_argument("--bayesian", action="store_true", default=False,
                   help="Paired dual-route run over the is_bayesian corpus selection. "
                        "Forces each variant down BOTH engines, interleaved.")
    p.add_argument("--max-usd", type=float, default=None,
                   help="Run-level USD ceiling. Aborts cleanly; resume with --resume.")
    p.add_argument("--resume", action="store_true", default=False,
                   help="Skip (variant, arm) pairs already recorded in --out.")
    p.add_argument("--full-timeout", type=float, default=600.0,
                   help="Per-turn deadline in seconds for full-depth turns.")
```

Change the `--out` default so `--bayesian` gets its own directory:

```python
    p.add_argument("--out", type=Path, default=None)
```

and at the top of `main`, after parsing:

```python
    if a.out is None:
        a.out = Path("nessie_out_bayes" if a.bayesian else "nessie_out")

    if a.bayesian:
        # `is_bayesian` IS the selection. Accepting a second selection source
        # would make "what ran" depend on two things at once.
        conflicting = [name for name, val in (
            ("--tier", a.tier != "route"), ("--scope", a.scope != "specific"),
            ("--sample", a.sample != 1.0), ("--seed", a.seed != 0),
            ("--cases", getattr(a, "cases", None) is not None),
        ) if val]
        if conflicting:
            build_parser().error(
                f"--bayesian selects on the corpus's is_bayesian flag and cannot be "
                f"combined with {', '.join(conflicting)}.")
        from nessie_tests import bayesian
        try:
            m = bayesian.run_paired(
                base_url=a.base_url, auth_header=auth, out_dir=a.out,
                max_usd=a.max_usd, resume=a.resume,
                full_timeout_s=a.full_timeout, pace_s=a.pace)
        except bayesian.BudgetExceeded as e:
            print(f"nessie: {e}")
            return 2
        both = sum(1 for p in m.pairs if p.ns and p.cc)
        print(f"nessie: {both}/{len(m.pairs)} complete pairs "
              f"({2 * len(m.pairs)} arms); manifest -> {a.out}/manifest.json")
        return 0
```

Note the parser must define `--cases` if it does not already; if `--cases` is absent from this branch, drop that one entry from `conflicting` rather than inventing the flag.

- [ ] **Step 4: Run and commit**

```bash
git add nessie_tests/cli.py nessie_tests/tests/test_cli.py
git commit -m "feat(nessie): --bayesian CLI with budget, resume and mutual exclusion

Refuses --tier/--scope/--sample/--seed/--cases: the is_bayesian flag is the
selection, and two sources for 'what ran' makes a run unexplainable."
```

---

## Done when

- [ ] `run_suite`'s behaviour is unchanged; the whole existing suite is green at baseline.
- [ ] `run_case()` returns one entry, honours `force_route`, and strips route/engine criteria only when asked.
- [ ] `preflight.assert_force_route_works` has been *seen* to raise on a simulated dropped force.
- [ ] `--bayesian` interleaves NS then CC per question, provable from the recorded call order.
- [ ] Killing a run mid-way and rerunning with `--resume` re-runs zero completed arms.
- [ ] A budget ceiling aborts with a resumable manifest on disk.

**Then** proceed to plan 3 (`docs/nessie-bayesian-plan-3-evaluation.md`).
