# Bayesian Evaluation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a paired run into a graded table HiBayes can consume: collect every artifact both engines produced, emit the locked HiBayes CSVs, grade each answer twice (LLM and human, independently), and surface where the two disagree.

**Architecture:** Three independent stages joined by files on disk. `collect.py` pulls artifacts from four sources keyed four different ways. `export.py` emits one CSV per arm plus a separate exclusions file. `output-skill-bayesian` renders a split report where the human grades blind before the LLM's verdict is revealed, then `merge_grades.py` joins everything.

**Tech Stack:** Python 3, pydantic v2, pytest, MySQL client (already vendored), zstandard (already a dependency of `cc_transcript_store`). Stage C runs cross-repo in `dmac-assistant` and is not built here.

**Spec:** `docs/nessie-bayesian-mode-design.md` §8 to §10. **Depends on plans 1 and 2.**

## Global Constraints

- **Outages are excluded, never scored.** An outage means the provider fallback chain died before the product ran. Emitting it as `is_error=true` teaches the posterior that Bedrock downtime is CC incapability. `nessie_tests/outage.py` holds the one definition; do not write a second.
- **`None` is not zero.** Only `container_cc` emits `total_cost_usd`. An NS arm's cost is unobserved, and the CSV must carry an empty cell rather than `0`.
- **A row is a VARIANT, but the spend behind it is TURNS.** The selection is 127 variants and **158 turns**, so the CSVs have ~127 rows per arm while the run that produced them paid for 158 turns per arm. Any cost figure derived by multiplying rows by a per-turn price runs about 24% low. The skew concentrates in `refine_and_recall`: 25 variants, **50 turns**, and its `cost_usd` cells will look disproportionately large for the same reason. That is correct, not a collector bug — do not "normalise" it.
- **The paired manifest is `bayes_manifest.MANIFEST_NAME`, never a literal.** Plan 2 originally specified `manifest.json` — the same filename `runner.py` writes for a normal run — and it was changed during execution after two failures were reproduced: `read_bayes_manifest` returns an EMPTY `BayesManifest` rather than raising when handed a normal manifest (so `--resume` silently repays the whole run), and `write_bayes_manifest` destroys the prior run's record on the first pair. Read the name from `nessie_tests.bayes_manifest`; do not hardcode either name anywhere in this plan's code or tests.
- **Corrected during plan 2's execution:** the selection figures above were 130 variants / 161 turns until the 3 `route_gate` variants were dropped from `is_bayesian`. Under a forced route their only criteria strip away, so they evaluated nothing on both arms while their CC arms billed as full Opus turns whose cost `--max-usd` could never observe. The `refine_and_recall` skew below is unaffected and still exactly 25 variants / 50 turns.
- **The two HiBayes column tuples are locked upstream.** Copy them verbatim from `dmac-assistant/tools/hibayes/exporter.py` (`HIBAYES_CSV_COLUMNS`, 14) and `tools/hibayes/functional_inputs.py` (`CSV_HEADER_12`). A test pins our header against our pinned copy. It **cannot** detect upstream drift, because the repos are separate. Do not claim otherwise in a docstring.
- **Test command, exactly this, from the repo root:**
  ```bash
  uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
    python -m pytest nessie_tests/tests -q -p no:cacheprovider
  ```
- **No live DB or docker in the unit suite.** Every collector test injects a fake row source and a fake copier.
- **Never edit anything under `chat_nextseek/`.** Task 1 touches `nextseek_api/`, which is not vendored.
- **Conventional commits with module scopes.**

---

## File Structure

| File | Responsibility |
|---|---|
| `nextseek_api/services/cc_assistant.py` | **Modified.** Emit an `ns_run_root` event so NS artifacts have a join key. |
| `nessie_tests/collect.py` | **New.** Post-hoc pull from the four artifact sources into one tree. |
| `nessie_tests/export.py` | **New.** Per-arm HiBayes CSVs, Stage B inputs, exclusions. |
| `nessie_tests/output-skill-bayesian/SKILL.md` | **New.** The sequence, written for whoever runs it next. |
| `nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py` | **New.** Manifest + collection + Stage C output to HTML. |
| `nessie_tests/output-skill-bayesian/scripts/merge_grades.py` | **New.** Join both grade sources into the graded table. |
| `nessie_tests/output-skill-bayesian/templates/report_bayes.html.tpl` | **New.** Split layout, blind-then-reveal grading. |
| `nessie_tests/tests/test_collect.py` | **New.** |
| `nessie_tests/tests/test_export.py` | **New.** |
| `nessie_tests/tests/test_bayes_report.py` | **New.** |

---

## Task 1: Give NS artifacts a join key

`run_root` is set into the chat_nextseek session dict at `orchestrator.py:335` and never escapes. `QueryTask` has no field for it and no event carries it, so nothing can say which `outputs/<ts>_<user>/` belongs to which turn.

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py`
- Test: `nextseek_api/cc_assistant/tests/test_ns_run_root_event.py`

**Interfaces:**
- Consumes: the chat_nextseek session adapter already held in `_run_ns_turn`'s scope.
- Produces: a `ns_run_root` progress event with `{"run_root": "<abs path>"}`.

- [ ] **Step 1: Find the NS call site**

```bash
grep -n "ROUTE_NS" -A 30 nextseek_api/services/cc_assistant.py | head -50
```
You are looking for where the chat_nextseek orchestrator is invoked and where its session object is still in scope afterwards. Note the line number; the next step edits immediately after that call.

- [ ] **Step 2: Write the failing test**

Create `nextseek_api/cc_assistant/tests/test_ns_run_root_event.py`:

```python
"""The NS engine's run_root must reach the event stream.

Without it nothing can join a turn to its outputs/<ts>_<user>/ directory:
run_root lives only in the chat_nextseek session dict (orchestrator.py:335) and
QueryTask has no field for it. The collector's fallback is a timestamp window,
which is only unambiguous while runs are strictly sequential.

Vendored code is NOT touched. This reads the session dict the Django side already
holds, so startup/scripts/sync_chat_nextseek.sh cannot clobber it.
"""
from nextseek_api.services import cc_assistant as svc


def test_run_root_is_emitted_when_the_session_carries_one():
    events = []
    session = {"run_root_dir": "/app/outputs/260804_101500_demo"}
    svc._emit_ns_run_root(lambda e, d: events.append((e, d)), session)
    assert events == [("ns_run_root", {"run_root": "/app/outputs/260804_101500_demo"})]


def test_nothing_is_emitted_when_the_session_has_no_run_root():
    """A turn that never reached the orchestrator has no run_root. Emitting an
    empty one would make the collector look for a directory that never existed."""
    events = []
    svc._emit_ns_run_root(lambda e, d: events.append((e, d)), {})
    assert events == []


def test_a_broken_session_object_never_breaks_the_turn():
    """This is instrumentation. It must not be able to fail a real user's query."""
    class Hostile:
        def get(self, _k, _d=None):
            raise RuntimeError("boom")

    events = []
    svc._emit_ns_run_root(lambda e, d: events.append((e, d)), Hostile())
    assert events == []
```

- [ ] **Step 3: Run it and watch it fail**

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/cc_assistant/tests/test_ns_run_root_event.py --no-migrations -q'
```
Expected: `AttributeError: module ... has no attribute '_emit_ns_run_root'`.

If the container is not running, this task's tests are the one place in these three plans that need it. Everything else runs on the host lane.

- [ ] **Step 4: Implement**

In `nextseek_api/services/cc_assistant.py`, add near the other private helpers:

```python
def _emit_ns_run_root(send_event, session) -> None:
    """Publish the NS engine's per-turn output directory to the event stream.

    `run_root` is set into the chat_nextseek session dict by the orchestrator and
    otherwise never leaves it, so a test harness or a support request cannot join
    a task_id to its console.txt, api_requests.json or files/.

    Deliberately total: this is instrumentation, and instrumentation must never be
    able to fail a real user's turn. A session object that raises, or one with no
    run_root, emits nothing.
    """
    try:
        run_root = session.get("run_root_dir") if session is not None else None
    except Exception:
        return
    if run_root:
        send_event("ns_run_root", {"run_root": str(run_root)})
```

Then call it immediately after the orchestrator returns, at the line you found in Step 1:

```python
                    _emit_ns_run_root(send_event, ns_session)
```

using whatever that scope names the session object.

- [ ] **Step 5: Run and commit**

```bash
git add nextseek_api/services/cc_assistant.py \
        nextseek_api/cc_assistant/tests/test_ns_run_root_event.py
git commit -m "feat(cc_assistant): emit ns_run_root so NS artifacts have a join key

run_root lived only in the chat_nextseek session dict, so nothing could say
which outputs/<ts>_<user>/ belonged to which turn. Reads the session object the
Django side already holds; the vendored subpackage is untouched.

Total by construction: a hostile or empty session emits nothing rather than
failing a user's turn."
```

---

## Task 2: The collector

**Files:**
- Create: `nessie_tests/collect.py`
- Test: `nessie_tests/tests/test_collect.py`

**Interfaces:**
- Consumes: `bayes_manifest.read_bayes_manifest`, `outage.PROVIDER_OUTAGE_MARKER`.
- Produces:
  ```python
  class Sources(Protocol):
      def task_rows(self, task_ids: list[str]) -> dict[str, dict]: ...
      def cc_transcript(self, session_id: str) -> bytes | None: ...
      def copy_tree(self, src: str, dest: pathlib.Path) -> bool: ...
  def collect(manifest, out_dir, sources, outputs_root=None) -> dict
  ```
  `collect` returns the `collection.json` payload it also writes.

- [ ] **Step 1: Write the failing test**

Create `nessie_tests/tests/test_collect.py`:

```python
"""The collector pulls from four sources keyed four different ways.

Every source is injected. A unit test must not need MySQL, docker, or a volume.
"""
import json
import pathlib

from nessie_tests import collect
from nessie_tests.bayes_manifest import BayesManifest, BayesPair
from nessie_tests.manifest import NessieManifestEntry


class FakeSources:
    def __init__(self, rows=None, transcript=b"", copyable=()):
        self._rows = rows or {}
        self._transcript = transcript
        self._copyable = set(copyable)
        self.copied = []

    def task_rows(self, task_ids):
        return {t: self._rows[t] for t in task_ids if t in self._rows}

    def cc_transcript(self, session_id):
        return self._transcript or None

    def copy_tree(self, src, dest):
        self.copied.append((src, str(dest)))
        if src not in self._copyable:
            return False
        pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(dest) / "console.txt").write_text("trace", encoding="utf-8")
        return True


def _entry(vid, task_id, cost=None):
    e = NessieManifestEntry(id=vid, family="f", tier="full", status="passed", cost=cost)
    e.reason = f"task_id={task_id}"
    return e


def _manifest():
    return BayesManifest(run_meta={"mode": "bayesian"}, pairs=[
        BayesPair(id="a.one", family="f", hibayes_subtype="Search-Basic",
                  ns=_entry("a.one", "t-ns"), cc=_entry("a.one", "t-cc", cost=0.2)),
    ])


def test_layout_is_one_directory_per_variant_per_arm(tmp_path):
    src = FakeSources(rows={"t-ns": {"progress": [], "result": None}})
    collect.collect(_manifest(), tmp_path, src)
    assert (tmp_path / "artifacts" / "a.one" / "ns").is_dir()
    assert (tmp_path / "artifacts" / "a.one" / "cc").is_dir()


def test_task_row_is_written_per_arm(tmp_path):
    rows = {"t-ns": {"progress": [{"event": "query_complete"}], "result": {"reply": "hi"}}}
    collect.collect(_manifest(), tmp_path, FakeSources(rows=rows))
    task = json.loads((tmp_path / "artifacts" / "a.one" / "ns" / "task.json").read_text())
    assert task["result"]["reply"] == "hi"


def test_ns_run_root_is_taken_from_the_event_when_present(tmp_path):
    rows = {"t-ns": {"progress": [
        {"event": "ns_run_root", "data": {"run_root": "/app/outputs/260804_101500_demo"}}],
        "result": None}}
    src = FakeSources(rows=rows, copyable={"/app/outputs/260804_101500_demo"})
    collect.collect(_manifest(), tmp_path, src)
    assert (tmp_path / "artifacts" / "a.one" / "ns" / "run_root" / "console.txt").is_file()


def test_a_missing_artifact_is_recorded_rather_than_silently_absent(tmp_path):
    """An artifact that could not be COLLECTED and one that was never PRODUCED are
    different facts, and the grader has to be able to tell them apart."""
    rows = {"t-ns": {"progress": [
        {"event": "ns_run_root", "data": {"run_root": "/gone"}}], "result": None}}
    out = collect.collect(_manifest(), tmp_path, FakeSources(rows=rows))
    misses = [m for m in out["missing"] if m["what"] == "run_root"]
    assert misses and misses[0]["path"] == "/gone"
    assert misses[0]["reason"]


def test_no_ns_run_root_event_records_the_gap_explicitly(tmp_path):
    out = collect.collect(_manifest(), tmp_path,
                          FakeSources(rows={"t-ns": {"progress": [], "result": None}}))
    assert any(m["what"] == "ns_run_root_event" for m in out["missing"])


def test_cc_transcript_is_decompressed_to_jsonl(tmp_path):
    import zstandard
    raw = b'{"type":"assistant"}\n'
    src = FakeSources(rows={}, transcript=zstandard.ZstdCompressor().compress(raw))
    collect.collect(_manifest(), tmp_path, src)
    assert (tmp_path / "artifacts" / "a.one" / "cc" / "session.jsonl").read_bytes() == raw


def test_collection_json_is_written_and_counts_both_arms(tmp_path):
    out = collect.collect(_manifest(), tmp_path, FakeSources())
    on_disk = json.loads((tmp_path / "collection.json").read_text())
    assert on_disk == out
    assert on_disk["arms_seen"] == 2
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError: No module named 'nessie_tests.collect'`.

- [ ] **Step 3: Implement**

Create `nessie_tests/collect.py`:

```python
"""Post-hoc artifact collection for a paired run.

Four sources, four different keys:

    task row + events   task_id       direct
    CC artifacts list   in the events direct
    CC scratch tree     run_id        docker cp off the dmac-cc-users volume
    CC transcript       session_id    zstd blob in CCSessionTranscript
    NS run_root         ns_run_root   event added in plan 3 task 1, or a
                                      timestamp window for older runs

Post-hoc rather than inline so it is re-runnable without repaying for the suite.
The known cost is `nextseek_api/cc_assistant/cc_sweep.py`: CC scratch can be
reaped between the turn and the collection. That is why every miss is RECORDED
rather than skipped. If misses turn out to be common, move CC scratch collection
inline; do not start guessing at what was there.
"""
from __future__ import annotations

import json
import pathlib
import re

TASK_ID_RE = re.compile(r"task_id=([0-9a-fA-F-]+)")


def _task_id(entry) -> str | None:
    if entry is None:
        return None
    m = TASK_ID_RE.search(entry.reason or "")
    return m.group(1) if m else None


def _event(row: dict, name: str) -> dict | None:
    for ev in (row.get("progress") or []):
        if ev.get("event") == name:
            return ev.get("data") or {}
    return None


def collect(manifest, out_dir, sources, outputs_root=None) -> dict:
    out_dir = pathlib.Path(out_dir)
    art_root = out_dir / "artifacts"
    missing: list[dict] = []
    arms_seen = 0

    wanted = [t for p in manifest.pairs for t in (_task_id(p.ns), _task_id(p.cc)) if t]
    rows = sources.task_rows(wanted) if wanted else {}

    for pair in manifest.pairs:
        for arm in ("ns", "cc"):
            entry = getattr(pair, arm)
            if entry is None:
                continue
            arms_seen += 1
            dest = art_root / pair.id / arm
            dest.mkdir(parents=True, exist_ok=True)

            tid = _task_id(entry)
            row = rows.get(tid) if tid else None
            if row is None:
                missing.append({"id": pair.id, "arm": arm, "what": "task_row",
                                "path": tid, "reason": "no row for this task_id"})
                continue
            (dest / "task.json").write_text(json.dumps(row, indent=2, default=str),
                                            encoding="utf-8")

            if arm == "ns":
                data = _event(row, "ns_run_root")
                run_root = (data or {}).get("run_root")
                if not run_root:
                    missing.append({
                        "id": pair.id, "arm": arm, "what": "ns_run_root_event",
                        "path": None,
                        "reason": "no ns_run_root event; run predates it, or the turn "
                                  "never reached the orchestrator"})
                elif not sources.copy_tree(run_root, dest / "run_root"):
                    missing.append({"id": pair.id, "arm": arm, "what": "run_root",
                                    "path": run_root, "reason": "copy failed or path gone"})
            else:
                qc = _event(row, "query_complete") or {}
                (dest / "artifacts.json").write_text(json.dumps({
                    "artifacts": qc.get("artifacts") or [],
                    "cc_raw_files": qc.get("cc_raw_files") or [],
                }, indent=2), encoding="utf-8")

                scratch = (_event(row, "cc_turn_meta") or {}).get("scratch_dir")
                if scratch and not sources.copy_tree(scratch, dest / "cc_scratch"):
                    missing.append({"id": pair.id, "arm": arm, "what": "cc_scratch",
                                    "path": scratch,
                                    "reason": "copy failed; cc_sweep may have reaped it"})

                blob = sources.cc_transcript((row.get("result") or {}).get("session_id")
                                             or qc.get("session_id") or "")
                if blob:
                    import zstandard
                    (dest / "session.jsonl").write_bytes(
                        zstandard.ZstdDecompressor().decompress(blob))
                else:
                    missing.append({"id": pair.id, "arm": arm, "what": "cc_transcript",
                                    "path": None, "reason": "no transcript row"})

    payload = {"arms_seen": arms_seen, "pairs": len(manifest.pairs), "missing": missing}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "collection.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
```

- [ ] **Step 4: Record `task_id` on the entry rather than parsing it out of `reason`**

The regex above is a stopgap and a bad one: `reason` is prose and nothing guarantees it carries the id. Add the field properly.

In `nessie_tests/manifest.py`, add to `NessieManifestEntry`:

```python
    # The endpoint's task id, so a collector can join this entry to its
    # assistant_query_task row without parsing prose. Optional so manifests
    # written before this field existed still load.
    task_id: str | None = None
```

In `nessie_tests/runner.py`'s `run_case`, capture `res.task_id` into a `v_task_id` accumulator on the first turn and pass `task_id=v_task_id` when building the entry. Then replace `collect._task_id` with:

```python
def _task_id(entry) -> str | None:
    return getattr(entry, "task_id", None) if entry is not None else None
```

and delete `TASK_ID_RE`. Update the test's `_entry` helper to set `task_id=` instead of stuffing it into `reason`.

- [ ] **Step 5: Run and commit**

```bash
git add nessie_tests/collect.py nessie_tests/manifest.py nessie_tests/runner.py \
        nessie_tests/tests/test_collect.py
git commit -m "feat(nessie): post-hoc artifact collector for paired runs

Four sources keyed four ways into artifacts/<variant>/<arm>/. Every miss is
recorded in collection.json: an artifact that could not be collected and one
that was never produced are different facts and the grader must tell them apart.

Adds task_id to the manifest entry so the join does not depend on parsing prose."
```

---

## Task 3: The export

**Files:**
- Create: `nessie_tests/export.py`
- Test: `nessie_tests/tests/test_export.py`

**Interfaces:**
- Consumes: `bayes_manifest.read_bayes_manifest`, `corpus.hibayes_meta`, `outage`.
- Produces:
  ```python
  HIBAYES_CSV_COLUMNS: tuple[str, ...]   # 14, pinned from upstream
  CSV_HEADER_12: tuple[str, ...]         # 12, pinned from upstream
  ARM_IMAGE = {"ns": "nextseek_query", "cc": "container_cc"}
  def runtime_row(entry, *, arm, family, subtype, artifact_count) -> dict
  def export(manifest, out_dir, artifacts_dir=None, corpus_path=None) -> dict
  ```

- [ ] **Step 1: Write the failing test**

Create `nessie_tests/tests/test_export.py`:

```python
"""Per-arm HiBayes CSVs. The column tuples are locked upstream and copied verbatim."""
import csv
import pathlib

from nessie_tests import export, outage
from nessie_tests.bayes_manifest import BayesManifest, BayesPair
from nessie_tests.manifest import NessieManifestEntry


def _entry(vid="a.one", status="passed", cost=None, outaged=False, elapsed=1.5):
    return NessieManifestEntry(id=vid, family="f", tier="full", status=status,
                               cost=cost, elapsed_s=elapsed, outage=outaged)


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_the_locked_column_tuples_match_the_pinned_upstream_copies():
    """Catches OUR drift. It cannot catch upstream's: the repos are separate and
    nothing here reads dmac-assistant. See this module's docstring."""
    assert export.HIBAYES_CSV_COLUMNS == (
        "query_id", "task_family", "task_subtype", "image", "answer_provided",
        "is_error", "timed_out", "runtime_success", "failure_mode",
        "latency_seconds", "cost_usd", "tool_calls_total", "artifact_count", "is_opus")
    assert export.CSV_HEADER_12 == (
        "query_id", "task_family", "query_text", "final_answer", "answer_provided",
        "runtime_success", "failure_mode", "artifact_expected", "artifact_status",
        "artifact_kind", "declared_artifact_count", "expected_behavior")


def test_one_csv_per_arm_because_is_opus_must_be_uniform(tmp_path):
    """dmac-assistant's _validate_consistency check #6 requires is_opus uniform
    within a file, and an NS turn is not Opus at all."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", hibayes_subtype="S",
                                       ns=_entry(), cc=_entry(cost=0.2))])
    export.export(m, tmp_path)
    ns = _rows(tmp_path / "hibayes_eval_rows_ns.csv")
    cc = _rows(tmp_path / "hibayes_eval_rows_cc.csv")
    assert {r["is_opus"] for r in ns} == {"0"}
    assert {r["is_opus"] for r in cc} == {"1"}
    assert ns[0]["image"] == "nextseek_query" and cc[0]["image"] == "container_cc"


def test_an_unobserved_cost_is_empty_not_zero(tmp_path):
    """NS emits no total_cost_usd. `0` would be an accounting claim the harness
    cannot support, and cost_summary already refuses to make it."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(cost=None),
                                       cc=_entry(cost=0.25))])
    export.export(m, tmp_path)
    assert _rows(tmp_path / "hibayes_eval_rows_ns.csv")[0]["cost_usd"] == ""
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv")[0]["cost_usd"] == "0.25"


def test_an_outage_row_is_excluded_not_scored(tmp_path):
    """An outage means the fallback chain died BEFORE the product ran. Scoring it
    as is_error teaches the posterior that Bedrock downtime is CC incapability."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f", ns=_entry(),
                                       cc=_entry(status="error", outaged=True))])
    export.export(m, tmp_path)
    assert _rows(tmp_path / "hibayes_eval_rows_cc.csv") == []
    ex = _rows(tmp_path / "excluded.csv")
    assert len(ex) == 1 and ex[0]["arm"] == "cc"
    assert outage.PROVIDER_OUTAGE_MARKER.split()[0].lower() in ex[0]["reason"].lower()


def test_a_non_outage_error_IS_scored(tmp_path):
    """A dead endpoint is not a provider outage. It still failed."""
    m = BayesManifest(pairs=[BayesPair(id="a.one", family="f",
                                       cc=_entry(status="error", outaged=False))])
    export.export(m, tmp_path)
    rows = _rows(tmp_path / "hibayes_eval_rows_cc.csv")
    assert len(rows) == 1 and rows[0]["is_error"] == "true"
    assert rows[0]["runtime_success"] == "false"
    assert rows[0]["failure_mode"] == "error"


def test_failure_mode_priority_is_timeout_then_error_then_no_answer():
    assert export.failure_mode(answer_provided=False, is_error=True, timed_out=True) == "timeout"
    assert export.failure_mode(answer_provided=False, is_error=True, timed_out=False) == "error"
    assert export.failure_mode(answer_provided=False, is_error=False, timed_out=False) == "no_answer"
    assert export.failure_mode(answer_provided=True, is_error=False, timed_out=False) == "none"


def test_runtime_success_is_the_conjunction_upstream_validates():
    row = export.runtime_row(_entry(), arm="ns", family="f", subtype="S", artifact_count=0)
    assert row["runtime_success"] is (
        row["answer_provided"] and not row["is_error"] and not row["timed_out"])


def test_tool_calls_total_is_never_a_false_zero_for_ns(tmp_path):
    """The column is int, not nullable. 0 for NS would be false: NS really does
    issue API and graph calls. It is defined as engine operation count."""
    row = export.runtime_row(
        _entry(), arm="ns", family="f", subtype="S", artifact_count=0,
        engine_ops=3)
    assert row["tool_calls_total"] == 3
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError: No module named 'nessie_tests.export'`.

- [ ] **Step 3: Implement**

Create `nessie_tests/export.py`:

```python
"""Paired manifest -> the locked HiBayes CSVs, one per arm.

COLUMN TUPLES ARE COPIED, NOT IMPORTED. They are locked in
dmac-assistant/tools/hibayes/exporter.py (HIBAYES_CSV_COLUMNS) and
tools/hibayes/functional_inputs.py (CSV_HEADER_12). `test_export.py` pins our
header against the copy below, which catches OUR drift and cannot catch
upstream's, because the repos are separate and nothing here reads dmac-assistant.
That limit is real; do not write a docstring that implies otherwise.

One file per arm because upstream's `_validate_consistency` check #6 requires
`is_opus` uniform within a file, and an NS turn is not Opus at all. The two
concatenate for the model.
"""
from __future__ import annotations

import csv
import pathlib

from nessie_tests import corpus, outage

HIBAYES_CSV_COLUMNS: tuple[str, ...] = (
    "query_id", "task_family", "task_subtype", "image", "answer_provided",
    "is_error", "timed_out", "runtime_success", "failure_mode",
    "latency_seconds", "cost_usd", "tool_calls_total", "artifact_count", "is_opus",
)

CSV_HEADER_12: tuple[str, ...] = (
    "query_id", "task_family", "query_text", "final_answer", "answer_provided",
    "runtime_success", "failure_mode", "artifact_expected", "artifact_status",
    "artifact_kind", "declared_artifact_count", "expected_behavior",
)

EXCLUDED_COLUMNS: tuple[str, ...] = ("query_id", "arm", "status", "reason")

# `image` carries the ARM. It is the discriminator the model conditions on.
ARM_IMAGE = {"ns": "nextseek_query", "cc": "container_cc"}
ARM_IS_OPUS = {"ns": 0, "cc": 1}


def failure_mode(*, answer_provided: bool, is_error: bool, timed_out: bool) -> str:
    """Priority: timeout > error > no_answer > none. Mirrors upstream DD-05."""
    if timed_out:
        return "timeout"
    if is_error:
        return "error"
    if not answer_provided:
        return "no_answer"
    return "none"


def runtime_row(entry, *, arm, family, subtype, artifact_count, engine_ops=0) -> dict:
    answer_provided = entry.status in ("passed", "failed", "xpass", "no_assertions")
    is_error = entry.status == "error"
    timed_out = "timeout" in (entry.reason or "").lower()
    return {
        "query_id": entry.id,
        "task_family": family,
        "task_subtype": subtype,
        "image": ARM_IMAGE[arm],
        "answer_provided": answer_provided,
        "is_error": is_error,
        "timed_out": timed_out,
        "runtime_success": answer_provided and not is_error and not timed_out,
        "failure_mode": failure_mode(answer_provided=answer_provided,
                                     is_error=is_error, timed_out=timed_out),
        "latency_seconds": entry.elapsed_s,
        # `None` is NOT zero. Only container_cc emits total_cost_usd.
        "cost_usd": entry.cost,
        # int, not nullable. 0 for NS would be false: NS really does issue API and
        # graph calls. Defined as ENGINE OPERATION count, so the column means the
        # same kind of thing on both arms.
        "tool_calls_total": engine_ops,
        "artifact_count": artifact_count,
        "is_opus": ARM_IS_OPUS[arm],
    }


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write(path, columns, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns))
        w.writeheader()
        for r in rows:
            w.writerow({c: _fmt(r.get(c)) for c in columns})


def export(manifest, out_dir, artifacts_dir=None, corpus_path=None) -> dict:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_arm: dict[str, list[dict]] = {"ns": [], "cc": []}
    excluded: list[dict] = []

    for pair in manifest.pairs:
        for arm in ("ns", "cc"):
            entry = getattr(pair, arm)
            if entry is None:
                continue
            if entry.outage:
                # Excluded, not scored. Nothing about the product was exercised.
                excluded.append({
                    "query_id": pair.id, "arm": arm, "status": entry.status,
                    "reason": f"provider outage: {outage.PROVIDER_OUTAGE_MARKER}",
                })
                continue
            by_arm[arm].append(runtime_row(
                entry, arm=arm, family=pair.family, subtype=pair.hibayes_subtype,
                artifact_count=_artifact_count(artifacts_dir, pair.id, arm)))

    for arm, rows in by_arm.items():
        _write(out_dir / f"hibayes_eval_rows_{arm}.csv", HIBAYES_CSV_COLUMNS, rows)
    _write(out_dir / "excluded.csv", EXCLUDED_COLUMNS, excluded)
    return {"ns": len(by_arm["ns"]), "cc": len(by_arm["cc"]), "excluded": len(excluded)}


def _artifact_count(artifacts_dir, variant_id, arm) -> int:
    """CC: the published artifact list. NS: files under run_root/files/."""
    if not artifacts_dir:
        return 0
    base = pathlib.Path(artifacts_dir) / variant_id / arm
    if arm == "cc":
        import json
        p = base / "artifacts.json"
        if not p.is_file():
            return 0
        return len(json.loads(p.read_text(encoding="utf-8")).get("artifacts") or [])
    files = base / "run_root" / "files"
    return sum(1 for _ in files.rglob("*")) if files.is_dir() else 0
```

- [ ] **Step 4: Add the Stage B input builder**

Append to `nessie_tests/export.py`:

```python
# Presence and readability ONLY. Schema validation (the GEO xlsx template parity
# dmac-assistant's artifact_validator.py does) is deferred, and anything it would
# have judged is emitted Indeterminate rather than guessed at.
def artifact_status(*, expected: bool, path: pathlib.Path | None) -> str:
    if not expected:
        return "NotExpected"
    if path is None or not path.exists():
        return "Missing"
    try:
        path.read_bytes() if path.is_file() else list(path.iterdir())
    except OSError:
        return "Unreadable"
    return "Indeterminate"


def export_stage_b(manifest, out_dir, artifacts_dir=None, corpus_path=None) -> int:
    """The 12-column Stage C input. One file covering BOTH arms: Stage C grades an
    answer, and which engine produced it is not part of that judgement."""
    out_dir = pathlib.Path(out_dir)
    rows = []
    for pair in manifest.pairs:
        meta = corpus.hibayes_meta(pair.id, corpus_path)
        for arm in ("ns", "cc"):
            entry = getattr(pair, arm)
            if entry is None or entry.outage:
                continue
            base = pathlib.Path(artifacts_dir) / pair.id / arm if artifacts_dir else None
            rows.append({
                "query_id": f"{pair.id}::{arm}",
                "task_family": pair.family,
                "query_text": "",
                "final_answer": "",
                "answer_provided": entry.status != "error",
                "runtime_success": entry.status in ("passed", "xpass"),
                "failure_mode": failure_mode(
                    answer_provided=entry.status != "error",
                    is_error=entry.status == "error",
                    timed_out="timeout" in (entry.reason or "").lower()),
                "artifact_expected": bool(meta["artifact_expected"]),
                "artifact_status": artifact_status(
                    expected=bool(meta["artifact_expected"]),
                    path=(base / "cc_scratch") if base and arm == "cc" else
                         (base / "run_root" / "files") if base else None),
                "artifact_kind": meta["artifact_kind"],
                "declared_artifact_count": _artifact_count(artifacts_dir, pair.id, arm),
                "expected_behavior": meta["expected_behavior"],
            })
    _write(out_dir / "hibayes_functional_eval_inputs.csv", CSV_HEADER_12, rows)
    return len(rows)
```

`query_text` and `final_answer` are filled from the collected `task.json` in Task 4's report builder, which is the only place that has both the corpus and the collection in hand. Add a test asserting they are non-empty once that wiring exists; leaving them empty here is a staged build, not a placeholder.

- [ ] **Step 5: Run and commit**

```bash
git add nessie_tests/export.py nessie_tests/tests/test_export.py
git commit -m "feat(nessie): per-arm HiBayes CSV export with outage exclusion

One file per arm because upstream requires is_opus uniform within a file and NS
is not Opus. Outages go to excluded.csv rather than scoring as is_error, which
would teach the posterior that Bedrock downtime is CC incapability.

Unobserved NS costs are empty cells, never 0. tool_calls_total is engine
operation count so the column means the same thing on both arms."
```

---

## Task 4: The split report with blind-then-reveal grading

**Files:**
- Create: `nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py`
- Create: `nessie_tests/output-skill-bayesian/templates/report_bayes.html.tpl`
- Create: `nessie_tests/output-skill-bayesian/SKILL.md`
- Test: `nessie_tests/tests/test_bayes_report.py`

**Interfaces:**
- Consumes: `BayesManifest`, `collection.json`, the collected `task.json` files, and optionally Stage C's output.
- Produces: `report_bayes.html` embedding `const PAIRS`, `const META`, and `const LLM` (the Stage C verdicts, present but not rendered until graded).

- [ ] **Step 1: Write the failing test**

Create `nessie_tests/tests/test_bayes_report.py`:

```python
"""The split report. Grading is BLIND then revealed.

Showing the LLM's verdict beside the transcript while the human grades inflates
agreement by anchoring, and the disagreement set is the whole reason both graders
exist. So the verdict ships in the page but is not rendered for a row until that
row has a human grade.
"""
import json
import pathlib
import re
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "output-skill-bayesian" / "scripts"


def _build(tmp_path, manifest, llm=None):
    (tmp_path / bayes_manifest.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    if llm is not None:
        (tmp_path / "stage_c.json").write_text(json.dumps(llm), encoding="utf-8")
    out = tmp_path / "report_bayes.html"
    subprocess.run([sys.executable, str(SCRIPTS / "build_bayes_report.py"),
                    "--run", str(tmp_path), "--out", str(out)], check=True)
    return out.read_text(encoding="utf-8")


def _literal(html, name):
    m = re.search(rf"const {name}\s*=\s*(\[.*?\]|\{{.*?\}});", html, re.S)
    return json.loads(m.group(1)) if m else None


MANIFEST = {"run_meta": {"mode": "bayesian"}, "pairs": [
    {"id": "a.one", "family": "f", "hibayes_subtype": "Search-Basic",
     "ns": {"id": "a.one", "family": "f", "tier": "full", "status": "passed"},
     "cc": {"id": "a.one", "family": "f", "tier": "full", "status": "failed"}}]}


def test_every_pair_reaches_the_page_with_both_arms(tmp_path):
    pairs = _literal(_build(tmp_path, MANIFEST), "PAIRS")
    assert len(pairs) == 1
    assert pairs[0]["ns"]["status"] == "passed"
    assert pairs[0]["cc"]["status"] == "failed"


def test_the_llm_verdict_is_embedded_but_flagged_blind(tmp_path):
    html = _build(tmp_path, MANIFEST,
                  llm={"a.one::ns": {"outcome": "FullySatisfied", "usefulness_score": 4}})
    assert _literal(html, "LLM")["a.one::ns"]["outcome"] == "FullySatisfied"
    assert "BLIND_UNTIL_GRADED" in html


def test_the_page_never_renders_a_verdict_before_a_grade_server_side(tmp_path):
    """The verdict must reach the reader only through the blind-gated JS path, not
    as static markup a browser paints immediately."""
    html = _build(tmp_path, MANIFEST,
                  llm={"a.one::ns": {"outcome": "FullySatisfied", "usefulness_score": 4}})
    body = html.split("<script", 1)[0]
    assert "FullySatisfied" not in body


def test_grades_autosave_key_is_run_scoped(tmp_path):
    """Two runs graded in the same browser must not share a localStorage bucket."""
    html = _build(tmp_path, MANIFEST)
    assert "nessie-bayes-grades:" in html


def test_the_report_builds_without_stage_c_output(tmp_path):
    """Human grading comes first in the workflow, so the report must be usable
    before the LLM has run at all."""
    html = _build(tmp_path, MANIFEST)
    assert _literal(html, "LLM") == {}
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `FileNotFoundError` on `build_bayes_report.py`.

- [ ] **Step 3: Implement the builder**

Create `nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py`. It reads the paired manifest — `bayes_manifest.MANIFEST_NAME`, never a literal — plus the optional `stage_c.json` and the collected `task.json` files; embeds `const PAIRS`, `const META` and `const LLM`; and writes the template with those literals substituted. Model it on `output-skill/scripts/build_report.py`, which already does exactly this shape of substitution.

Two rules the tests pin:

- `LLM` defaults to `{}` when `stage_c.json` is absent, so the report is usable before the grader has run.
- No verdict string may appear outside a `<script>` block. Render verdicts only through the JS path gated on `BLIND_UNTIL_GRADED`.

- [ ] **Step 4: Implement the template**

Create `templates/report_bayes.html.tpl` starting from `output-skill/templates/report.html.tpl`, keeping its `localStorage` autosave and download-blob mechanism (lines 529 to 591) and changing the record from free text to `{grade: "pass"|"fail", note: str, ts: iso}` per `(variant, arm)`. Key it `"nessie-bayes-grades:" + META.run_id`.

Three columns per question: the question and metadata, then NS and CC side by side with final reply, per-turn trace, artifact list, cost, latency and surviving criterion observations.

Two house-CSS traps, both of which have bitten this template before, and both of which must be checked in a real browser rather than reasoned about:

- `.reply` carries a 78ch clamp that fights a half-width column.
- Nested-grid rows shrink to fit rather than filling.

Grading is keyboard-driven: `j`/`k` to move, `1`/`2` to grade the focused arm pass/fail, `n` to open the note. 300 grades by mouse is its own failure mode.

- [ ] **Step 5: Verify the layout headless, not by reading the CSS**

```bash
python3 -m http.server 8901 --directory <run-dir> &
```
Open `report_bayes.html`, confirm both arms render full-width in their columns and no row collapses, then kill the server. Screenshot it into the run directory as evidence.

- [ ] **Step 6: Write SKILL.md and commit**

`SKILL.md` records the sequence: run `--bayesian`, run `collect.py`, run `export.py`, build the report, grade it blind, download `grades.json`, run Stage C in `dmac-assistant`, rebuild the report with `stage_c.json`, reveal, then `merge_grades.py`. State plainly that `fetch_run.py` from the sibling skill is reused unchanged.

```bash
git add nessie_tests/output-skill-bayesian/ nessie_tests/tests/test_bayes_report.py
git commit -m "feat(nessie): split bayes report with blind-then-reveal grading

The Stage C verdict ships in the page but is not rendered for a row until that
row has a human grade. Showing it first would inflate agreement by anchoring,
and the disagreement set is the entire reason both graders exist."
```

---

## Task 5: Merge the grades

**Files:**
- Create: `nessie_tests/output-skill-bayesian/scripts/merge_grades.py`
- Test: `nessie_tests/tests/test_bayes_report.py`

**Interfaces:**
- Consumes: `hibayes_eval_rows_{ns,cc}.csv`, `stage_c.json`, `grades.json`.
- Produces: `graded_rows.csv` with the 14 runtime columns plus `human_success`, `llm_success`, `agree`, `usefulness_score`, `primary_issue`.

- [ ] **Step 1: Write the failing test**

Append to `nessie_tests/tests/test_bayes_report.py`:

```python
from nessie_tests.output_skill_bayesian import merge_grades  # noqa: E402

SUCCESS_OUTCOMES = {"FullySatisfied", "AppropriateClarification", "AppropriateBoundary"}


def test_llm_outcome_projects_to_binary_per_upstream_dd08():
    for o in SUCCESS_OUTCOMES:
        assert merge_grades.llm_success(o) is True
    for o in ("PartiallySatisfied", "NotSatisfied"):
        assert merge_grades.llm_success(o) is False


def test_not_assessable_is_neither_success_nor_failure():
    """It is excluded alongside the outages rather than counted as a loss."""
    assert merge_grades.llm_success("NotAssessable") is None


def test_agreement_is_computed_only_where_both_graders_spoke():
    assert merge_grades.agreement(True, True) is True
    assert merge_grades.agreement(True, False) is False
    assert merge_grades.agreement(True, None) is None


def test_a_missing_human_grade_fails_loudly(tmp_path):
    """A quietly shorter table is how a partial grading pass gets read as
    complete. It must be impossible to produce one by accident."""
    import pytest
    with pytest.raises(merge_grades.IncompleteGrading) as e:
        merge_grades.merge(rows=[{"query_id": "a.one", "image": "nextseek_query"}],
                           grades={}, llm={})
    assert "a.one" in str(e.value)


def test_merged_row_carries_both_grades_and_the_agreement_flag():
    out = merge_grades.merge(
        rows=[{"query_id": "a.one", "image": "nextseek_query"}],
        grades={"a.one::ns": {"grade": "pass"}},
        llm={"a.one::ns": {"outcome": "NotSatisfied", "usefulness_score": 1,
                           "primary_issue": "InsufficientEvidence"}})
    assert out[0]["human_success"] is True
    assert out[0]["llm_success"] is False
    assert out[0]["agree"] is False
    assert out[0]["usefulness_score"] == 1
    assert out[0]["primary_issue"] == "InsufficientEvidence"
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError`. Add `nessie_tests/output_skill_bayesian/__init__.py` re-exporting from the scripts directory, or move `merge_grades.py` into an importable package and have the skill's `scripts/` call it. Prefer the latter: a script that cannot be imported cannot be unit tested.

- [ ] **Step 3: Implement**

```python
"""Join the runtime rows, the LLM verdicts and the human grades into one table."""
from __future__ import annotations

ARM_FROM_IMAGE = {"nextseek_query": "ns", "container_cc": "cc"}

# Upstream DD-08. These three outcomes are successes; NotAssessable is neither.
SUCCESS_OUTCOMES = frozenset({"FullySatisfied", "AppropriateClarification",
                              "AppropriateBoundary"})


class IncompleteGrading(RuntimeError):
    """A row has no human grade. Refusing to emit is the point: a quietly shorter
    table is how a partial grading pass gets read as a complete one."""


def llm_success(outcome: str | None) -> bool | None:
    if outcome == "NotAssessable" or outcome is None:
        return None
    return outcome in SUCCESS_OUTCOMES


def agreement(human: bool | None, llm: bool | None) -> bool | None:
    if human is None or llm is None:
        return None
    return human == llm


def merge(*, rows, grades, llm) -> list[dict]:
    out, ungraded = [], []
    for row in rows:
        key = f"{row['query_id']}::{ARM_FROM_IMAGE[row['image']]}"
        g = grades.get(key)
        if not g or g.get("grade") not in ("pass", "fail"):
            ungraded.append(key)
            continue
        human = g["grade"] == "pass"
        verdict = llm.get(key) or {}
        machine = llm_success(verdict.get("outcome"))
        out.append({**row,
                    "human_success": human,
                    "llm_success": machine,
                    "agree": agreement(human, machine),
                    "usefulness_score": verdict.get("usefulness_score"),
                    "primary_issue": verdict.get("primary_issue")})
    if ungraded:
        raise IncompleteGrading(
            f"{len(ungraded)} row(s) have no human grade: {sorted(ungraded)[:10]}. "
            f"Finish the grading pass, re-download grades.json, and rerun.")
    return out
```

Add a `main()` reading the two per-arm CSVs, `grades.json` and `stage_c.json`, and writing `graded_rows.csv`.

- [ ] **Step 4: Run and commit**

```bash
git add nessie_tests/output_skill_bayesian/ nessie_tests/output-skill-bayesian/ \
        nessie_tests/tests/test_bayes_report.py
git commit -m "feat(nessie): merge human and LLM grades into the graded table

Projects Stage C's 6-value outcome to binary per upstream DD-08, with
NotAssessable excluded rather than counted as a loss. Fails loudly on any
ungraded row: a quietly shorter table reads as a complete grading pass."
```

---

## Done when

- [ ] `ns_run_root` reaches the event stream and the collector uses it.
- [ ] `collection.json` records every miss with a reason; no artifact is silently absent.
- [ ] Two per-arm CSVs exist with `is_opus` uniform in each, unobserved costs empty, and every outage in `excluded.csv`.
- [ ] The report builds with and without Stage C output, and no verdict string appears outside a `<script>` block.
- [ ] The split layout has been checked in a real browser, with a screenshot in the run directory.
- [ ] `merge_grades` raises on an ungraded row and produces `graded_rows.csv` otherwise.

## Deferred, deliberately

- **Full Stage A schema validation.** Presence and readability only; everything else is `Indeterminate` rather than guessed.
- **Upstream column drift detection.** Structurally impossible across separate repos. Stated in `export.py`'s docstring rather than papered over.
- **The HiBayes model itself.** These three plans end at `graded_rows.csv`.
