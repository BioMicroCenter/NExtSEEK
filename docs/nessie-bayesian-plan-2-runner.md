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

and a reader for the two criterion shapes, since `criteria` mixes them:

```python
def _criterion_field(c):
    """`criteria` mixes PassCriterion objects with the plain dicts
    `default_route_criterion` is declared to return, so both shapes are read."""
    return c.get("field") if isinstance(c, dict) else getattr(c, "field", None)
```

Inside `run_case`, where `criteria` is currently built:

```python
            criteria = list(turn.pass_criteria) + ([extra] if extra else [])
            if strip_route_criteria:
                kept = [c for c in criteria
                        if _criterion_field(c) not in STRIPPED_UNDER_FORCING]
                stripped += len(criteria) - len(kept)
                criteria = kept
```

> **Corrected 2026-08-04 (whole-branch fix wave).** The field read was originally inline as
> `getattr(c, "field", c.get("field") if isinstance(c, dict) else None)`, which reads as if
> the attribute were the normal case and evaluates a dict lookup as a `getattr` default.
> It is a named `_criterion_field` helper in the shipped code, stating plainly that
> `criteria` holds two shapes. The snippet's indentation was also one level too deep for
> the loop it lives in.

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
import os
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
    """Serialise to a sibling temp file and `os.replace` it into place.

    ATOMIC ON PURPOSE. `bayesian.run_paired` writes after every ARM, so a
    ~130-variant paired run rewrites this file ~260 times, and a plain
    `write_text` truncates before it writes. A Ctrl-C, an OOM kill or a full disk
    landing inside any one of those windows leaves half a JSON document, and
    `read_bayes_manifest` then raises on the whole file — destroying every
    completed arm recorded by the 259 writes that succeeded, which is precisely
    what writing per arm exists to protect.

    The temp file is a SIBLING so `os.replace` is a same-filesystem rename and
    therefore actually atomic; `/tmp` would silently degrade to a copy across a
    mount boundary. It carries the pid so two runs sharing an out_dir cannot
    consume each other's partial file.
    """
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_NAME
    tmp = out / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
    tmp.write_text(m.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
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

> **Also changed during execution (2026-08-04, Task 5 fix round 1).** This plan
> originally specified a plain `path.write_text`. Task 5 writes the manifest after every
> ARM, so a ~130-variant run rewrites this file ~260 times, and `write_text` truncates
> before it writes: a Ctrl-C inside any one of those windows leaves half a JSON document
> and `read_bayes_manifest` then raises on the whole file, destroying every arm the other
> 259 writes recorded. It is now a sibling temp file plus `os.replace`, pinned by
> `test_an_interrupted_write_leaves_the_previous_manifest_intact`. The temp file must stay
> a SIBLING (same filesystem, so the rename is really atomic) and keep the pid suffix.
> The snippet's import block gained `import os` in the same correction (2026-08-04,
> whole-branch fix wave): the atomic write above needs `os.getpid` and `os.replace`, and
> the import list had been left on the pre-atomic version, so re-executing this task from
> the document would have produced a `NameError` on the first write.

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
from nessie_tests import route_observer as ro

PROBE_QUERY = "What is the weather in Boston tomorrow?"


class ForceRouteRejected(RuntimeError):
    """The server ignored `force_route`. The account is almost certainly not staff."""


def assert_force_route_works(post_query, get_progress) -> None:
    """One out-of-scope turn forced to `ns`. Raises if the force was dropped.

    Out-of-scope on purpose. A forced decision is ROUTE_NS or ROUTE_CC and never
    ROUTE_UNRELATED, so a question the router WOULD call unrelated gives a clean
    two-valued answer: `nextseek_query` means the force landed, `unrelated` means
    it did not. Cheapest possible discriminator, and it is an NS turn either way.

    A turn that never emits `route_decided` leaves both fields None, which is
    inconclusive rather than refused -- it takes the raising path too, because
    proceeding on an unproven force is the exact failure this guard exists for.
    It gets its own message: the two conditions have different remedies, and
    telling an operator whose endpoint is hung to switch accounts sends them
    somewhere that cannot help.

    Driven at `route` tier, not `full`. `route_decided` is emitted before either
    engine runs (cc_assistant.py:403, immediately after `_decide_route`), so the
    discriminator is identical -- but the poll loop breaks at the event in ~2s
    and a hung probe hits `route_timeout_s=60` instead of `full_timeout_s=600`.
    """
    res = http_driver.drive(PROBE_QUERY, tier="route", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    route, source = res.route_obs.route, res.route_obs.source
    if source == "forced" and route != "unrelated":
        return
    observed = f"route={route!r} source={source!r}"
    if not ro.has_route_decided(res.payload):
        # No `route_decided` event at all: the turn never reported a routing
        # decision, so there is no observation to contradict. Claiming the force
        # was dropped here would be asserting a cause we cannot see -- the same
        # refusal `cost_summary` makes when it reports `unmeasured` over $0.00.
        raise ForceRouteRejected(
            f"the probe turn produced NO routing decision: {observed}, "
            f"status={res.status!r}, no `route_decided` event arrived.\n"
            f"This is INCONCLUSIVE, not evidence that force_route was dropped: a "
            f"hung, erroring or unreachable endpoint looks exactly like this, and "
            f"so does a turn that died before it routed. Check the stack is up and "
            f"that one turn completes at all before suspecting the account.\n"
            f"Raising regardless -- an UNPROVEN force is as unsafe to spend a "
            f"300-turn run on as a refused one.")
    raise ForceRouteRejected(
        f"force_route was not honoured: {observed}, expected "
        f"route='nextseek_query' source='forced'.\n"
        f"force_route is gated on is_staff/is_superuser and a non-admin's value is "
        f"dropped silently. Run --bayesian as a staff account; the harness default "
        f"'demo' is not one. Without this the whole run measures the router, not "
        f"the engines.")
```

> **Corrected 2026-08-04 (whole-branch fix wave).** The snippet above was left on the
> as-planned version after two changes landed during execution, and re-executing it would
> have undone both:
>
> 1. **The inconclusive case is split out.** A turn that never emits `route_decided` leaves
>    `route` and `source` both None, and the single message told that operator their
>    account was not staff -- a cause the probe cannot see, and a remedy that cannot help
>    someone whose endpoint is simply down. It still RAISES; only the diagnosis is split.
>    Pinned by `test_a_probe_that_never_routed_is_not_diagnosed_as_a_dropped_force` and
>    `test_a_demonstrably_dropped_force_keeps_the_staff_account_guidance`.
> 2. **The probe drives `route` tier, not `full`.** `route_decided` is emitted before
>    either engine runs, so the discriminator is identical, but the poll loop then breaks
>    at the event: a hung probe costs `route_timeout_s=60` rather than `full_timeout_s=600`.
>    Pinned by `test_the_probe_runs_at_route_tier_so_a_hang_costs_60s_not_600s` and
>    `test_the_route_tier_probe_still_observes_the_routing_decision`.
>
> Step 1's test list is likewise the original four; the shipped `tests/test_preflight.py`
> carries six more, including the two pairs named above.

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
answer. `run_suite`'s `cases_path` makes the same call for the same reason: an
explicit running order replaces scope, family, variant, sample and seed outright
rather than combining with them. (It is `cases_path`, not `--cases`: no flag on
this branch's parser reaches it.)
"""
from __future__ import annotations

import time

from nessie_tests import corpus, http_driver, preflight, runner
from nessie_tests.bayes_manifest import (
    MANIFEST_NAME, BayesManifest, BayesPair, completed_arms, read_bayes_manifest,
    write_bayes_manifest,
)

ARMS = ("ns", "cc")


class BudgetExceeded(RuntimeError):
    """The run-level USD ceiling was reached. Resume with --resume after raising it."""


class PriorRunWouldBeOverwritten(RuntimeError):
    """`out_dir` already holds a paired manifest and this run is not a resume.

    Protects completed PAID pairs. Pairs are written as they complete, so a
    second non-resume run into the same directory replaces a finished record
    with its own first pair and everything after that point is unrecoverable.
    Reproduced at 130 pairs -> 2.
    """


class NoRunToResume(RuntimeError):
    """`--resume` was given but `out_dir` holds no paired manifest.

    The mirror of `PriorRunWouldBeOverwritten`, and the same defect class: that
    one refuses a fresh run onto a prior record, and nothing refused a resume
    onto NOTHING, which silently starts a fresh ~322-arm paid run.

    The likely route in is the budget abort's own advice -- "rerun the SAME
    command with a higher --max-usd and --resume" -- retyped without `--out`,
    which defaults to `nessie_out_bayes` rather than to the run's directory. The
    operator pays twice and the original run is never continued.
    """


class CorpusChanged(RuntimeError):
    """The corpus is not PROVABLY the one the run being resumed was selected from.

    Protects completed PAID pairs, and the comparability the fingerprint exists
    for. `manifest.pairs` is rebuilt from the CURRENT selection rather than
    merged with the prior pairs, so any id that left the selection loses its
    paid result silently -- and selection can only change if corpus.json did.

    Two routes here, one refusal and two different messages: the fingerprints
    disagree, or the prior manifest records none at all. Their remedies do not
    overlap, so they do not share wording.
    """


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

    # BEFORE the preflight, and before anything is selected: a mistyped --out must
    # cost zero turns. Read first, decide, and only then spend. This guard lives in
    # `run_paired` rather than in the CLI because `run_paired` is importable on its
    # own and the damage happens here.
    existing = read_bayes_manifest(out_dir)
    if existing is not None and not resume:
        raise PriorRunWouldBeOverwritten(
            f"{out_dir} already holds a paired manifest with {len(existing.pairs)} "
            f"pair(s), and this run would rewrite it from its own first pair "
            f"onward.\nThose pairs were PAID FOR and are not recoverable once "
            f"overwritten.\nEither continue that run with --resume, or send this "
            f"one somewhere else with --out.")
    if existing is None and resume:
        # The other direction of the same guard. `--resume` is a promise that a
        # run exists here; if none does, honouring it silently would spend a
        # FULL fresh run while the paid arms it was meant to continue sit
        # untouched in the directory the operator meant to name.
        raise NoRunToResume(
            f"--resume was given but {out_dir} holds no paired manifest (no "
            f"{MANIFEST_NAME}), so there is nothing to continue -- this would "
            f"start a FRESH full paid run instead of finishing one.\n"
            f"The likeliest cause is a missing or mistyped --out. The budget "
            f"abort tells you to rerun the SAME command with a higher --max-usd "
            f"and --resume; dropping --out from that rerun sends it to the "
            f"default nessie_out_bayes/ rather than to your run's directory, and "
            f"the arms you already paid for are never continued.\n"
            f"Point --out at the directory holding {MANIFEST_NAME}, or drop "
            f"--resume if a fresh run really is what you want.")

    fresh_fingerprint = runner.corpus_fingerprint(corpus_path)
    prior = existing if resume else None
    if prior is not None:
        prior_fingerprint = prior.run_meta.get("corpus_fingerprint")
        if prior_fingerprint is None:
            # Its own message, not the drift one. `preflight` was split for
            # exactly this reason: the two conditions reach the same refusal by
            # different routes and have DIFFERENT remedies, and the drift
            # message's two exits are both wrong here -- the corpus did not
            # change, so there is nothing to "restore", and "start a fresh run"
            # costs a whole paid run to escape a missing JSON key.
            raise CorpusChanged(
                f"this manifest records NO corpus_fingerprint, so the corpus it "
                f"was selected from cannot be identified.\n"
                f"`run_paired` always writes that key, so nothing it produced can "
                f"land here: a manifest without one was hand-edited or truncated. "
                f"An UNPROVEN match is as unsafe to resume a paid run onto as a "
                f"refused one -- pairs are rebuilt from the CURRENT selection, so "
                f"any id that has since left it loses its paid result silently. "
                f"`preflight` makes the same call on its own inconclusive case.\n"
                f"If you know these pairs came from the corpus in this checkout, "
                f"add \"corpus_fingerprint\": {fresh_fingerprint!r} to run_meta in "
                f"{out_dir}/{MANIFEST_NAME} and resume. Do NOT delete the "
                f"manifest to clear this: that repays for every arm on disk.")
        if prior_fingerprint != fresh_fingerprint:
            raise CorpusChanged(
                f"the corpus is not the one this run was selected from: prior "
                f"fingerprint {prior_fingerprint!r}, current {fresh_fingerprint!r}.\n"
                f"Selection comes from corpus.json, so a changed corpus means a "
                f"changed selection -- and pairs are rebuilt from the CURRENT "
                f"selection, so resuming would silently DROP the paid result of "
                f"every id that left it. The two runs are also no longer "
                f"comparable, which is what the fingerprint is for.\n"
                f"Restore the corpus to resume, or start a fresh run in a new --out.")

    if not skip_preflight:
        # Before anything is spent. A dropped force makes the entire run measure
        # the router instead of the engines.
        preflight.assert_force_route_works(post_query, get_progress)

    selected = corpus.bayesian_ids(corpus_path)
    by_id = {v.id: v for v in corpus.merged(corpus_path)}

    done = completed_arms(prior) if prior else set()
    pairs = {p.id: p for p in (prior.pairs if prior else [])}

    # A resumed run's arms were NOT all produced by the build recorded below.
    # `run_meta` is rebuilt from scratch every time, so a resume after a rebuild
    # silently restated one `git_sha` and one `base_url` for a two-build run --
    # the inverse of the honesty `corpus_fingerprint` is guarded for, which stops
    # the QUESTIONS changing mid-run while nothing recorded that the thing being
    # MEASURED had. A changed sha does not raise: unlike a corpus edit, finishing
    # a run after a rebuild is legitimate and sometimes the only way to finish it.
    # It is made visible instead, oldest segment first, flattened so a reader gets
    # one list rather than a chain of nested manifests: every build and base_url
    # that contributed arms is `[m["git_sha"] for m in superseded] + [git_sha]`.
    # Always present, `[]` on a fresh run, so plan 3 need not special-case it.
    superseded = []
    if prior is not None:
        prior_meta = dict(prior.run_meta)
        superseded = list(prior_meta.pop("superseded_runs", []))
        superseded.append(prior_meta)

    manifest = BayesManifest(
        run_meta={
            "mode": "bayesian",
            "arms": list(ARMS),
            "corpus_fingerprint": fresh_fingerprint,
            "git_sha": runner.git_sha(),
            "base_url": base_url,
            "selected_ids": selected,
            "max_usd": max_usd,
            "resumed": bool(prior),
            "superseded_runs": superseded,
        },
        pairs=[],
    )

    costs: list[float | None] = []
    for vid in selected:
        v = by_id[vid]
        meta = corpus.hibayes_meta(vid, corpus_path)
        pair = pairs.get(vid) or BayesPair(
            id=vid, family=v.family, hibayes_subtype=meta["hibayes_subtype"])
        # Appended ONCE, before either arm runs, so the per-arm writes below
        # persist this pair's partial state without ever duplicating it.
        manifest.pairs.append(pair)

        # Both arms for THIS question before moving on. Two passes would confound
        # engine with wall-clock time, and a provider outage during one pass would
        # read as a real engine effect the model cannot separate out.
        for arm in ARMS:
            if (vid, arm) in done:
                costs.append(getattr(pair, arm).cost if getattr(pair, arm) else None)
                continue
            if max_usd is not None and _spent(costs) >= max_usd:
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
            # Per ARM, not per pair. `completed_arms` is keyed on the arm so that
            # "a run interrupted between the NS and CC halves of one question must
            # not repay for the NS half" -- but writing only once both arms were
            # done meant no crash could ever persist a half pair, and that
            # machinery was decorative on the exact path it names. Measured: a
            # Ctrl-C on pair 2's cc arm paid for 3 arms and persisted 2.
            write_bayes_manifest(manifest, out_dir)

    # A run that resumed with nothing left to do never entered a write above, and
    # would otherwise return a manifest whose `run_meta` (`resumed`, `max_usd`,
    # `git_sha`) disagrees with the file on disk. One write makes "the file equals
    # what was returned" true unconditionally.
    write_bayes_manifest(manifest, out_dir)
    return manifest
```

> **Changed during execution (2026-08-04, commit `4d7ca1f` + fix round 1).** The module
> above carries three guards this plan did not originally specify. All three were
> reproduced as real data loss against the as-planned code, and all three protect pairs
> that have already been PAID FOR:
>
> 1. **`PriorRunWouldBeOverwritten`.** A second non-resume run in the same `--out`
>    replaced a 130-pair manifest with its own first pair (measured: 130 → 2, dying at
>    turn 5). This is the same defect class `MANIFEST_NAME` already overrode this plan to
>    fix — Task 3 spends 19 lines making the *cross-schema* collision impossible, and the
>    *same-schema* axis was simply left open. The guard lives in `run_paired`, not in the
>    CLI, because `run_paired` is importable without `cli.py` and the damage happens here;
>    it runs BEFORE the preflight so a mistyped `--out` costs zero turns.
> 2. **`CorpusChanged`.** `manifest.pairs` is rebuilt from the CURRENT selection rather
>    than merged with `prior.pairs`, so any id dropped from selection lost its paid result
>    on resume (reproduced by clearing one completed id's `is_bayesian`: the pair vanished
>    from the rewritten file). Selection can only change if corpus.json changed, so
>    comparing the fingerprint closes the deletion path and the comparability question
>    together. A prior manifest with NO fingerprint takes the raising path too, matching
>    the call `preflight` already makes on its own inconclusive case.
> 3. **Writing per ARM, not per pair.** `completed_arms`' docstring promises that "a run
>    interrupted between the NS and CC halves of one question must not repay for the NS
>    half", but with the write after the arm loop no crash could ever persist a half pair,
>    so that machinery was decorative on the exact path it names (measured: Ctrl-C on pair
>    2's `cc` paid for 3 arms, persisted 2, and the resume repaid the lost `ns`). The pair
>    is appended once, before either arm, so per-arm writes never duplicate it. This is
>    what makes the atomic `write_bayes_manifest` above load-bearing rather than tidy.
>
> Pinned by `test_a_fresh_run_refuses_to_overwrite_a_prior_paired_manifest`,
> `test_the_overwrite_guard_fires_before_the_preflight_spends_a_turn`,
> `test_resume_refuses_when_the_corpus_changed_underneath_it`,
> `test_resume_refuses_a_prior_manifest_that_records_no_fingerprint` and
> `test_a_crash_between_the_arms_of_one_pair_keeps_the_completed_ns_arm`. Each was
> verified by mutation: disabling any one guard fails its own tests and no others.

> **Also corrected 2026-08-04 (whole-branch fix wave).** Three more changes are in the
> snippet above; the first is the third instance of the same data-destroying defect class
> as `MANIFEST_NAME` and `PriorRunWouldBeOverwritten`:
>
> 1. **`NoRunToResume`.** The overwrite guard was one-directional. Nothing refused
>    `--resume` onto an EMPTY directory, and `run_paired(resume=True)` there drove all
>    ~322 arms and billed for them. The likely route in is the budget abort's own advice
>    — "rerun the SAME command with a higher `--max-usd` and `--resume`" — retyped without
>    `--out`, which defaults to `nessie_out_bayes` rather than the run's own directory, so
>    a full fresh paid run executes while the arms already bought are never continued. It
>    sits beside the overwrite guard and, like it, BEFORE the preflight. Wired to its own
>    exit code 8 in `cli.py`. Pinned by
>    `test_resume_onto_a_directory_holding_no_run_refuses_instead_of_paying_twice`,
>    `test_the_resume_guard_fires_before_the_preflight_spends_a_turn` and
>    `test_a_run_that_is_not_a_resume_still_starts_normally_in_an_empty_directory`.
> 2. **`run_meta.superseded_runs`.** `run_meta` was rebuilt from scratch on resume, so a
>    run finished after a rebuild silently restated one `git_sha` and one `base_url` for
>    what was a two-build run — the inverse of the honesty `corpus_fingerprint` is guarded
>    for, which stops the QUESTIONS changing mid-run while nothing recorded that the thing
>    being MEASURED had. The superseded `run_meta` is now carried forward under that key,
>    oldest segment first and flattened so plan 3 reads one list rather than walking a
>    chain; it is always present, `[]` on a fresh run. A changed `git_sha` deliberately
>    does NOT raise: unlike a corpus edit, finishing a run after a rebuild is legitimate
>    and sometimes the only way to finish it. Pinned by
>    `test_a_resume_after_a_rebuild_keeps_the_prior_runs_provenance`,
>    `test_a_changed_build_is_recorded_rather_than_refused`,
>    `test_a_fresh_run_records_an_empty_provenance_chain`,
>    `test_repeated_resumes_flatten_into_one_readable_list` and
>    `test_the_provenance_chain_survives_the_round_trip_to_disk`.
> 3. **The no-fingerprint refusal gets its own message.** It reaches `CorpusChanged` by a
>    different route and both of the drift message's remedies are wrong for it: the corpus
>    did not change, so there is nothing to "restore", and "start a fresh run in a new
>    `--out`" costs a whole paid run to escape a missing JSON key. The real exit — add the
>    key, whose value the message now prints — was unnamed. Same split `preflight` already
>    made, for the same reason. Pinned by
>    `test_the_missing_fingerprint_refusal_names_its_own_way_out` and
>    `test_the_drift_refusal_keeps_its_own_message`.

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
- Consumes: `bayesian.run_paired`, `bayesian.BudgetExceeded`, `bayesian.PriorRunWouldBeOverwritten`, `bayesian.NoRunToResume`, `bayesian.CorpusChanged`, `preflight.ForceRouteRejected`.
- Produces: `--bayesian`, `--max-usd`, `--resume`, `--full-timeout` on the existing parser.

- [ ] **Step 1: Write the failing test**

Append to `nessie_tests/tests/test_cli.py`:

```python
def test_bayesian_flag_parses_with_its_own_options():
    a = cli.build_parser().parse_args(
        ["--base-url", "http://x", "--bayesian", "--max-usd", "40", "--resume"])
    assert a.bayesian is True and a.max_usd == 40.0 and a.resume is True


def test_bayesian_defaults_its_own_output_directory(monkeypatch):
    """Asserted on what `run_paired` RECEIVES, not on `parse_args`.

    `--out`'s default depends on `--bayesian`, which argparse cannot express, so
    it is resolved in `main` and the parsed value is None. Pinning the parsed
    value would pin None; pinning the passed value pins the directory the paid
    run actually writes into.
    """
    captured = _capture_paired(monkeypatch)
    assert cli.main(["--base-url", "http://x", "--bayesian"]) == 0
    assert captured["out_dir"] == Path("nessie_out_bayes")


@pytest.mark.parametrize("extra", [
    ["--tier", "full"], ["--scope", "all"], ["--sample", "0.5"], ["--seed", "3"],
    ["--family", "reporting"], ["--variant", "green.mus_ndma"], ["--consistency"],
    # ...and the same flags at their DEFAULT values, which a value comparison
    # cannot see: ["--tier", "route"], ["--scope", "specific"], ["--sample", "1.0"],
    # ["--seed", "0"]. See the correction note below.
])
def test_bayesian_refuses_every_other_selection_source(extra, monkeypatch, capsys):
    """is_bayesian IS the selection. Two sources for 'what ran' makes a run
    unexplainable, which is the same reason `run_suite`'s cases_path refuses them."""
    _tripwire_on_every_spend(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(["--base-url", "http://x", "--bayesian", *extra])
    assert e.value.code == 2, "argparse.error() owns 2; see the abort-code test"
    # Only the text AFTER argparse's "error:" prefix counts. The usage line above
    # it lists every option name on the parser, so asserting against the whole of
    # stderr would pass for any flag whether or not the check names it.
    msg = capsys.readouterr().err.split("error:", 1)[1]
    assert "--bayesian" in msg and extra[0] in msg
```

> **Corrected 2026-08-04 (whole-branch fix wave).** Three things in this test snippet were
> wrong by the time the branch shipped, and re-executing it as written would have
> reintroduced two live defects:
>
> - **`--cases` does not exist on this branch.** Nothing on the parser exposes it
>   (`run_suite` takes `cases_path`; no flag reaches it), so argparse rejects it as
>   unrecognized and the parametrize case proved nothing. It is dropped.
> - **`--family` and `--variant` were missing.** Both are live on the parser, and `--scope`
>   defaults to `"specific"`, so `--bayesian --family reporting` cleared the whole check
>   and was then SILENTLY IGNORED — the exact second-selection-source hazard the check
>   exists to prevent. `--consistency` is in for the same reason: `run_paired` has no
>   consistency-group parameter at all.
> - **`assert str(a.out) == "nessie_out_bayes"` cannot hold.** `--out`'s default depends on
>   another flag, which argparse cannot express, so it is resolved in `main` and
>   `parse_args` returns None. The assertion moved onto the value `run_paired` receives,
>   which is the one that decides where the paid run writes.
>
> The parametrize list must also cover each conflicting flag AT ITS DEFAULT VALUE
> (`--tier route`, `--scope specific`, `--sample 1.0`, `--seed 0`): see the second
> correction note under Step 3. The shipped tests carry those as a separate parametrized
> case, `test_bayesian_refuses_a_conflicting_flag_set_to_its_default_value`. Both tests
> use `_tripwire_on_every_spend`, which replaces `run_paired`, `run_suite` and
> `make_default_clients` with raisers so an argument-validation test that reached a
> spending path fails loudly instead of buying turns.

- [ ] **Step 2: Run it and watch it fail**

Expected: `AttributeError: 'Namespace' object has no attribute 'bayesian'`.

- [ ] **Step 3: Implement**

In `nessie_tests/cli.py`, add to `build_parser()` before `return p`:

```python
    p.add_argument("--bayesian", action="store_true", default=False,
                   help="PAID, ~260 turns. Paired dual-route run over the corpus's is_bayesian "
                        "selection (130 variants today): each one is driven down BOTH engines, "
                        "NS then CC, interleaved per question, with the router forced out. "
                        "Full depth, every case, no sampling. Needs a STAFF account, since "
                        "force_route is silently dropped for anyone else. Budget it with "
                        "--max-usd and resume it with --resume.")
    p.add_argument("--max-usd", type=float, default=None,
                   help="--bayesian only. Run-level USD ceiling, cumulative across resumes. "
                        "Aborts cleanly before the arm that would breach it, keeping every "
                        "completed arm; exit 3. Only container_cc reports cost, so NS spend "
                        "is invisible to this ceiling and the real total is higher.")
    p.add_argument("--resume", action="store_true", default=False,
                   help="--bayesian only. Continue the paired run in --out: every (variant, arm) "
                        "already recorded there is skipped rather than repaid.")
    p.add_argument("--full-timeout", type=float, default=FULL_TIMEOUT_DEFAULT_S,
                   help="--bayesian only. Per-turn deadline in seconds for full-depth turns.")
```

Change the `--out` default so `--bayesian` gets its own directory, and describe the
resolution rule in its help:

```python
    p.add_argument("--out", type=Path, default=None,
                   help="Output directory. Default nessie_out, or nessie_out_bayes under "
                        "--bayesian, which keeps a paired run out of a normal run's directory.")
```

Above the parser, the exit-code epilog, the mutual-exclusion tables, and the probe that
answers "was this flag SUPPLIED":

```python
EXIT_CODES = """exit codes
  0  the run completed
  1  a normal run had real failures (--bayesian never returns this: in a paired
     run a wrong answer is the measurement, not a gate failure)
  2  bad arguments. Owned by argparse, which is why no abort below reuses it:
     a wrapper that retried "the budget code" would loop forever on a typo.
  3  --bayesian: the budget ceiling was reached. MONEY WAS SPENT and every
     completed arm is on disk; rerun with a higher --max-usd and --resume.
  4  --bayesian: refused, --out already holds a paired run. Nothing was billed.
  5  --bayesian: refused, the server did not honour force_route. The preflight's
     own probe turn WAS sent, so one turn was billed; no paired arm was.
  6  --bayesian: refused, the corpus changed under a --resume. Nothing was billed.
  7  --bayesian: could not talk to --base-url. Any completed arms are on disk.
  8  --bayesian: refused, --resume was given but --out holds no paired run to
     continue. Nothing was billed.
"""

# The default per-turn deadline. It is a named constant so the parser's help and
# this module agree on one value; it is NOT how the mutual-exclusion checks tell a
# supplied flag from a default one. Comparing `a.full_timeout != 600.0` cannot:
# `--full-timeout 600` is indistinguishable from silence and slipped straight
# through. `_supplied_flags` answers that question directly.
FULL_TIMEOUT_DEFAULT_S = 600.0


class _NotSupplied:
    """Sentinel for "argparse never saw this flag on the command line"."""

    def __repr__(self) -> str:
        return "<not supplied>"


_NOT_SUPPLIED = _NotSupplied()

# The two mutual-exclusion lists, as (flag name, parser dest). Named here rather
# than inline so the refusal messages and the supplied-ness probe cannot drift
# apart, and so the order the flags are reported in is fixed.
_SELECTION_FLAGS = (
    ("--tier", "tier"), ("--scope", "scope"), ("--sample", "sample"),
    ("--seed", "seed"), ("--family", "family"), ("--variant", "variant"),
    ("--consistency", "consistency"),
)
_PAIRED_ONLY_FLAGS = (
    ("--max-usd", "max_usd"), ("--resume", "resume"), ("--full-timeout", "full_timeout"),
)


def _supplied_flags(argv) -> set[str]:
    """The dests the operator actually typed, whatever value they typed.

    Value-based exclusion is not the same question and got the answer wrong in
    both directions: `--bayesian --tier route` was ACCEPTED (route is the default
    value, so nothing looked conflicting) and bought a ~322-arm full-depth paid
    run for an operator who had explicitly asked for the cheap tier; on the other
    side `--full-timeout 600` on a normal run was accepted and silently ignored.

    Answered by re-parsing the same argv through a parser whose watched defaults
    are a sentinel: anything still holding the sentinel was not supplied. That
    delegates every parsing rule -- `--tier=full`, prefix abbreviations, `store_true`
    -- to argparse instead of re-implementing them over raw argv. `set_defaults`
    is argparse's own public API for this, and the sentinel is deliberately not a
    `str`, so argparse's string-default conversion and `choices` checks never see
    it. The first parse in `main` has already accepted this argv, so this parse
    cannot be the one that errors.
    """
    watched = {dest for _name, dest in _SELECTION_FLAGS + _PAIRED_ONLY_FLAGS}
    p = build_parser()
    p.set_defaults(**{d: _NOT_SUPPLIED for d in watched})
    seen = p.parse_args(argv)
    return {d for d in watched if getattr(seen, d) is not _NOT_SUPPLIED}
```

Then the paired run itself, split out of `main` so its abort paths read as one unit:

```python
def _run_bayesian(a, auth, supplied) -> int:
    """The paired dual-route run. Split out of `main` so the abort paths read as
    one unit and `main` stays a dispatcher over two unrelated run shapes."""
    # `is_bayesian` IS the selection. Accepting a second selection source would
    # make "what ran" depend on two things at once.
    #
    # Keyed on SUPPLIED-ness, not on value: `--bayesian --tier route` names the
    # default value, so a value comparison saw no conflict and let it through --
    # a full-depth ~322-arm paid run for an operator who asked for the cheap tier.
    #
    # --family and --variant are in this list even though the plan omitted them:
    # --scope defaults to "specific", so `--bayesian --family reporting` cleared
    # the whole check and was then SILENTLY IGNORED. --consistency is here for the
    # same reason: `run_paired` has no consistency-group parameter at all, so it
    # was accepted and dropped on the floor. --cases is not, because this branch's
    # parser has no such flag (run_suite takes cases_path; nothing exposes it) and
    # argparse already rejects it as unrecognized.
    conflicting = [name for name, dest in _SELECTION_FLAGS if dest in supplied]
    if conflicting:
        build_parser().error(
            f"--bayesian selects on the corpus's is_bayesian flag and cannot be "
            f"combined with {', '.join(conflicting)}.")

    from nessie_tests import bayes_manifest, bayesian, preflight
    try:
        m = bayesian.run_paired(
            base_url=a.base_url, auth_header=auth, out_dir=a.out,
            # Named, not left to `run_paired`'s default, so both run shapes
            # resolve the corpus by the same rule. The paired run fingerprints
            # this file and refuses a `--resume` onto a different one.
            corpus_path=_CORPUS,
            max_usd=a.max_usd, resume=a.resume,
            full_timeout_s=a.full_timeout, pace_s=a.pace)
    # Six aborts, six exit codes, none of them 0, 1 or 2. They share nothing an
    # operator would act on: the first spent real money and left resumable work on
    # disk, three of the rest refused before a single turn was billed, one refused
    # after the preflight's single probe turn, and each has a different remedy. A
    # single code would force a wrapper script to parse English out of stdout to
    # tell "raise the ceiling and continue" from "you are on the wrong account" --
    # and 2 in particular is argparse's own usage error, so a wrapper that retried
    # on "the budget code" would loop forever on a mistyped flag if the budget
    # code were 2.
    except bayesian.BudgetExceeded as e:
        print("nessie: budget ceiling reached, run stopped (exit 3).")
        print(f"nessie: {e}")
        print(f"nessie: {a.out}/{bayes_manifest.MANIFEST_NAME} holds every completed "
              f"arm. Rerun the SAME command with a higher --max-usd and --resume; "
              f"completed arms are skipped, not repaid.")
        return 3
    except bayesian.PriorRunWouldBeOverwritten as e:
        print("nessie: refused, nothing was billed (exit 4).")
        print(f"nessie: {e}")
        return 4
    except bayesian.NoRunToResume as e:
        print("nessie: refused the resume, nothing was billed (exit 8).")
        print(f"nessie: {e}")
        return 8
    # NOT "nothing was billed". The preflight drives a REAL forced-NS turn against
    # the endpoint, and a turn keeps billing after the harness stops polling it
    # (http_driver.py:96-98 vs cc_assistant.py:352-366) -- which is exactly why
    # the normal run's cost line below reports `unmeasured` rather than $0.00.
    # Claiming $0 here would be the one claim `manifest.cost_summary` refuses to
    # make. One probe turn was sent; the run's ~322 arms were not.
    except preflight.ForceRouteRejected as e:
        print("nessie: preflight refused the run, no paired arm was billed (exit 5).")
        print(f"nessie: {e}")
        print("nessie: the preflight's own probe turn WAS sent to the endpoint and "
              "keeps billing after the harness stops polling it, so this cost one "
              "turn -- not zero, and not ~322.")
        return 5
    except bayesian.CorpusChanged as e:
        print("nessie: refused the resume, nothing was billed (exit 6).")
        print(f"nessie: {e}")
        return 6
    # The likeliest first-run failure of all is a wrong port, and it is raised by
    # urllib inside the preflight's own POST -- before any harness guard can see
    # it. Uncaught it reached the operator as fifteen lines of urllib frames under
    # exit 1, the code that means "a normal run had real failures". HTTPError is a
    # URLError subclass, so a 500 or a 403 lands here too and prints its own status.
    except urllib.error.URLError as e:
        print("nessie: could not talk to the endpoint, run stopped (exit 7).")
        print(f"nessie: {a.base_url}: {e}")
        print(f"nessie: check the stack is up and --base-url is right. Any arms "
              f"that did complete are in {a.out}/{bayes_manifest.MANIFEST_NAME} "
              f"and --resume will skip them.")
        return 7

    both = sum(1 for p in m.pairs if p.ns and p.cc)
    print(f"nessie: {both}/{len(m.pairs)} complete pairs "
          f"({2 * len(m.pairs)} arms); manifest → {a.out}/{bayes_manifest.MANIFEST_NAME}")
    return 0
```

and `main` becomes a dispatcher over the two run shapes, with the mirror check on the
normal path:

```python
def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    # Resolved here rather than as an argparse default because it depends on
    # another flag. A paired run gets its own directory: its manifest, its report
    # and a normal run's are three different schemas that must not share a home.
    if a.out is None:
        a.out = Path("nessie_out_bayes" if a.bayesian else "nessie_out")
    auth = http_driver.basic_auth(a.user, a.password)
    supplied = _supplied_flags(argv)

    if a.bayesian:
        return _run_bayesian(a, auth, supplied)

    # The mirror of `_run_bayesian`'s mutual exclusion, and keyed on supplied-ness
    # for the same reason: `--full-timeout 600` names the default value, so a
    # value comparison saw nothing and accepted it. `run_suite` has no budget
    # ceiling, no resume and no per-turn deadline parameter, so silently accepting
    # these would leave an operator believing a spending cap is in force on a paid
    # full-tier run while nothing at all is capped.
    paired_only = [name for name, dest in _PAIRED_ONLY_FLAGS if dest in supplied]
    if paired_only:
        build_parser().error(
            f"{', '.join(paired_only)} only applies to --bayesian; a normal run has "
            f"no budget ceiling, no resume and no per-turn deadline.")
```

> **Corrected 2026-08-04 (whole-branch fix wave).** The block above is the shipped code;
> the plan's original version has been replaced wholesale rather than patched, because
> five separate things in it were superseded during execution:
>
> 1. **`--cases` is not a flag on this branch.** `run_suite` takes a `cases_path`
>    argument but nothing on the parser reaches it, so `("--cases", ...)` guarded a value
>    that could never be set. Dropped. **`--family`, `--variant` and `--consistency` take
>    its place**: all three are live, and `--scope` defaults to `"specific"`, so
>    `--bayesian --family reporting` cleared the entire check and was then silently
>    ignored.
> 2. **The exclusion asks about SUPPLIED-ness, not value.** `--bayesian --tier route`
>    passed a value comparison (route IS the default) and reached `run_paired`, buying a
>    ~322-arm full-depth paid run for an operator who had explicitly asked for the cheap
>    tier. Design §7.6 says `--bayesian` refuses to combine with `--tier`, not with some
>    of its values. `_supplied_flags` re-parses the same argv through a parser whose
>    watched defaults are a sentinel, so `--tier=full` and prefix abbreviations stay
>    argparse's problem. The same correction applies to the mirror check in `main`, where
>    `--full-timeout 600` had been slipping past.
> 3. **The aborts are six, and none of them is 2.** The plan returned 2 for
>    `BudgetExceeded`; 2 is argparse's own usage error, so a wrapper script that raised
>    `--max-usd` and retried on "the budget code" would have looped forever on a mistyped
>    flag. Budget is 3, and 4/5/6/7/8 are the prior-run, dropped-force, corpus-drift,
>    unreachable-endpoint and nothing-to-resume refusals. `urllib.error.URLError` is
>    caught because the likeliest first-run failure of all, a wrong port, is raised inside
>    the preflight's own POST where no harness guard can see it.
> 4. **Exit 5 does not say "nothing was billed".** The preflight drives a real forced-NS
>    turn, and a turn keeps billing after the harness stops polling it — the same reason
>    the cost line reports `unmeasured` rather than `$0.00`. It reports one probe turn.
> 5. **The success line prints `bayes_manifest.MANIFEST_NAME`, never a literal.** The
>    original printed `{a.out}/manifest.json`, which is the file `runner.run_suite` writes
>    for a NORMAL run and the exact name Task 3's `MANIFEST_NAME` override exists to keep
>    OUT of a paired output directory (see the block at Task 3, Step 3). It would have
>    sent the operator to a path that does not exist, named after the collision.
>
> The `--out` default also moved off the parser and into `main`, because it depends on
> another flag and argparse cannot express that; `parse_args().out` is therefore `None`,
> which is why Step 1's assertion had to move onto the value `run_paired` receives.

- [ ] **Step 4: Run and commit**

```bash
git add nessie_tests/cli.py nessie_tests/tests/test_cli.py
git commit -m "feat(nessie): --bayesian CLI with budget, resume and mutual exclusion

Refuses --tier/--scope/--sample/--seed/--family/--variant/--consistency when
SUPPLIED, whatever their value: the is_bayesian flag is the selection, and two
sources for 'what ran' makes a run unexplainable."
```

---

## Done when

- [ ] `run_suite`'s behaviour is unchanged; the whole existing suite is green at baseline.
- [ ] `run_case()` returns one entry, honours `force_route`, and strips route/engine criteria only when asked.
- [ ] `preflight.assert_force_route_works` has been *seen* to raise on a simulated dropped force.
- [ ] `--bayesian` interleaves NS then CC per question, provable from the recorded call order.
- [ ] Killing a run mid-way and rerunning with `--resume` re-runs zero completed arms.
- [ ] A budget ceiling aborts with a resumable manifest on disk.
- [ ] No paid arm can be lost or repaid by an `--out` mistake in either direction: a fresh run onto a prior manifest and a `--resume` onto nothing both refuse before the preflight, with their own exit codes (4 and 8).
- [ ] A run finished after a rebuild says so: `run_meta.superseded_runs` lists every earlier build and base_url that contributed arms.

**Then** proceed to plan 3 (`docs/nessie-bayesian-plan-3-evaluation.md`).
