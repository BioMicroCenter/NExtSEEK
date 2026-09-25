---
name: nessie-run-review
description: Use when reviewing what Nessie did on ANY instance (local, fairdata-dev, production) - a nessie_tests run ("why did these tests fail", "are these real bugs or drift", an HTML review of a run) or the questions real users asked ("who asked what, what broke, what needs fixing"). Pulls every turn read-only from the database plus the output files each turn wrote, so failures are judged without re-running paid turns.
---

# Reviewing what Nessie did (any instance)

Two jobs, one pull:

- **A nessie_tests run** becomes a reviewable HTML report where every failure carries
  its **expected vs observed** values and a verdict (steps 1-4 below).
- **Real users' questions** on any instance become a written report: who asked what,
  what they got, what failed, and what to fix (see "Reviewing real users' questions").

## 0. Pick the instance

Every script takes `--instance`. Pulls are read-only everywhere.

| `--instance` | Reaches | Transport |
|---|---|---|
| `local` | the workstation's own docker daemon | none (`--host ""` is the older spelling) |
| `dev` (default) | fairdata-dev | `ssh fairdata-dev sudo -n -u service-account` |
| `prod` | fairdata, production | `ssh fairdata` (key logs in as service-account, no sudo) |

Container names (`nextseek`, `seek-mysql`) and the schema (`dmac`) are the same on
all three. Needs the MIT VPN for dev and prod. Never RUN anything on production (no
harness, no writes, no re-asked questions): testing goes local, then dev.

## Scoring the replies themselves, offline

A pass rate says nothing about how the answers read. `scripts/reply_style.py` counts
the machinery in a run's replies -- naming the search that ran, reciting what it was
constrained by, writing a type both ways, hedging, offering work instead of doing it --
from a `turns.json` a pull already wrote. It is free and asks no model anything, so a
prompt change gets a before number from the stored run and an after number from the
next one.

```bash
python scripts/reply_style.py <run-dir-or-turns.json> --worst 5
```

Baseline, the 2026-09-21 40-question re-run (43 NExtSEEK-routed replies): names the
search 34, recites its constraints 16, writes a type both ways 26, opens with machinery
17, and 27% of every word is machinery against a median reply of 54 words.

The markers are literal phrasings, not judgement: a marker that never fires is not
evidence a reply was good. Read the worst offenders by hand.

## The core insight

**The manifest is not enough to triage.** It records criterion *names*
(`main:parser_plan.mode`) and never the value that was actually resolved. A raw
failure list therefore cannot distinguish a product bug from a stale assertion.

**The values are recoverable.** Every turn is a row in `assistant_query_task`,
which stores the full progress event stream, including
`query_complete.data.debug`, plus the final result. So you can reconstruct what
the system actually produced for a run that finished days ago.

Consequence: **do not re-run cases to triage them.** Re-running costs money, is
slower, and gives you a *different* run rather than an explanation of the one you
have. Query the database instead. Only re-run to verify a fix.

## Workflow

### 1. Pull the evidence (read-only)

```bash
# a harness run (dev box is the default instance)
python scripts/fetch_run.py --out ./run-<date> \
    --manifest /app/nessie_out_full/manifest.json \
    --since "2026-07-24 20:05:00" --until "2026-07-24 20:45:00"

# every turn in a window on production, with raw rows and output files copied down
python scripts/fetch_run.py --instance prod --out ./prod-<date> --raw --outputs \
    --since "2026-08-28 00:00:00" --until "2026-09-10 23:59:59"
```

`--raw` adds `tasks/<id>.json` (the full progress stream and result). `--outputs`
copies every run root a turn wrote to into `outputs/` and writes
`outputs_index.json` (task id -> the files it wrote). Each turn in `turns.json` also
names `user`, `session`, `run_root` and `error`, the FIRST error in its progress
stream, which is the specific cause; the final "Internal pipeline error" is generic.

Writes `manifest.json` and `turns.json`. `turns.json` holds, per turn: the routing
decision (route, source, and the router's own reasoning), the parser mode, and the
**full engine call** — the graph plan with its bound parameters and result meta, or
the API plan with its complete request body and result meta — plus the reporter
plan, CC model id and cost. Graph result rows are stripped so the file stays small.
Each turn also carries the router's price off `route_decided` (`router_cost`), the
turn record (`models_used`, `model_fallback`, `router_model`, `router_fallback`, the
partial flags), and `turn_cost` / `turn_cost_partial`, summed by the harness's own
rule. With a manifest, `case_costs.json` sums each case over its turns the way the
run did, so grade against that, not against one turn's `cost`.

Check the printed summary immediately. **Any turn with `src: "pipeline"` means the
BAML router was bypassed entirely** — see the gotchas below.

### 2. Triage each failure, then write `triage.json`

For every non-passing case, resolve what actually happened. Most answers are
already in `turns.json`. For anything deeper, query the run's task rows directly
(see REFERENCE.md for the SQL patterns and the field-alias table, which you
**must** read before interpreting any criterion).

Assign one verdict per case:

| Verdict | Meaning |
|---|---|
| `real` | A genuine product defect. |
| `drift` | The assertion is stale, brittle, or unevaluable. Product is fine. |
| `policy` | Behaviour differs from the corpus by design; a human must decide which is right. |
| `masked` | Reported **pass** but is hiding a defect. Look for these deliberately. |
| `notrun` | Never executed, so it proves nothing. Do not count it as a pass or a fail. |
| `pass` | Genuinely fine. |

#### Read the manifest STATUS first — three of them are not what they look like

Your verdict is your own; the entry's `status` is the harness's. Three statuses
carry information a bare pass/fail reading destroys, and mis-triaging the first
one is the reason it exists.

| Status | What the harness is telling you | Verdict to reach for |
|---|---|---|
| `error` **with `"outage": true`** | Every provider in an agent's fallback chain returned 503. The reply carries `All provider fallbacks exhausted` (`NessieAI/tests/nessie_tests/outage.py`), so no parser ran, no query was issued, and no product behaviour was exercised. `gate_failed()` exempts it. | **`notrun`.** Not `real` — nothing was tested. Not `drift` — the assertion is fine. Ten of the eighteen reds in the 2026-08-03 seed-6 run were one Bedrock outage, and three reviewers spent time triaging that noise as product behaviour. Only a re-run can say anything. |
| `no_assertions` | The case evaluated **zero** criteria — every one it carried was recorded `skipped` as unobservable, or it carried none. Counted as a real failure (`runner._is_real_failure`). | **`drift`**, and act on it: a case that proves nothing is corpus drift. `known_fail` does NOT excuse it — the tag claims the case fails, and this case demonstrated neither that nor its absence. Fix the case; never re-file it as `pass`. |
| `xpass` | A `known_fail` case or group passed every criterion. Counted as a real failure. | **`drift`.** The expectation is stale. Retire the tag, which flips the case into a live regression guard. |

An `error` **without** the outage flag — a dead endpoint, five consecutive poll
failures — still fails the gate and is still infrastructure. Check the task row
before calling it either way.

**`scripts/build_report.py` writes those three answers as its defaults.** It has
a tile each for `provider outages`, `xpass` and `asserted nothing`, `errored`
counts only the errors that are NOT outages, and an untriaged case takes the
verdict from the table above rather than `real` — see `DEFAULT_VERDICT` in that
file, and `tests/test_output_skill_scripts.py`, which pins every mapping.

That is a DEFAULT, not a judgement. The tool cannot tell a stale expectation from
a real one; it can only stop captioning an outage "real product defect" before you
have looked. Override any of it per case in `triage.json`, or replace the tiles
wholesale with your own `stats` block.

`graph_limit` now comes from `NessieAI/tests/nessie_tests/limits.py` (currently 5000). Set it in
`triage.json` only to review an OLDER run that really was capped at 250.

Copy `examples/triage.json` and edit. Each verdict entry takes:

```json
"advanced.find_me_nhp_samples_from_study": {
  "verdict": "real",
  "head": "One-line statement of what went wrong",
  "observed": [["field", "expected", "what actually came back", "fail"]],
  "note": "The reasoning, with the evidence that supports it."
}
```

The fourth element of an `observed` row is `ok`, `fail`, or `info`. Use `info` for
context that is not a criterion, such as a row count.

`findings`, `gaps` and `next` hold the cross-cutting analysis. Their `body`
entries are HTML strings, so inline markup is allowed.

### 3. Build

```bash
python scripts/build_report.py --run ./run-<date> \
    --repo <NExtSEEK checkout> --triage ./triage.json --out ./report.html
```

It joins each manifest entry to its declared turns, their asserted criteria (from
`NessieAI/tests/nessie_tests/corpus.json`), each turn's task
and engine call, and your verdict. Coverage is computed automatically. It warns
about non-passing cases you left unjudged.

The output is **one self-contained HTML file**: no external assets, no network
fetches, everything inlined. Publish it with the Artifact tool for a link, or pass
`--standalone` to emit a complete `<!doctype html>` document to open locally or
send. The default output omits the document skeleton because the Artifact
publisher supplies it.

**Everything about a case lands in that case's own record**, so a reviewer never
has to cross-reference: per turn it shows the query, the routing decision with the
router's reasoning, the exact call (cypher plus bound parameters, or method,
endpoint and request body) and its result, followed by the criteria table, your
analysis, and the trace. Turns are matched to tasks forward-only in execution
order, because query text repeats across variants and a global lookup mis-assigns.

### 4. Hand it to a reviewer, then fold their notes back in

The report is a review surface, not just a summary. Every case has a **Your notes**
box, and there is an **Overall review notes** box above the case list. Typing
autosaves to browser storage; **Ctrl-S** (or Cmd-S, or the Save notes button)
downloads a `nessie-notes.json` the reviewer sends back:

```json
{ "report": "...", "saved_at": "...", "overall": "...",
  "cases": { "advanced.find_me_nhp_samples_from_study":
             {"verdict": "real", "family": "sample_search",
              "status": "failed", "note": "reviewer's text"} } }
```

Each note carries the verdict and family it was written against, so you can tell
whether the reviewer was agreeing with or disputing your call. Fold the result
back into `triage.json` (adjusting verdicts, notes, findings) and rebuild. The
"Import" button restores a JSON into the page, so a reviewer can resume later or
a second reviewer can build on the first one's pass.

Notes are keyed in browser storage by `notes_id`, which defaults to the run
directory name, so two different runs never share a notes store. Set it
explicitly in `triage.json` if you rebuild the same run into several reports.

Tell the reviewer to press Ctrl-S before closing: Chrome sometimes blocks local
storage on `file://` pages. The code degrades gracefully (notes stay in memory and
the export still works), but the autosave cannot be relied on there. The page's
status line reads "saved to this browser" when storage is working.

## Reviewing real users' questions (no harness run)

Same pull, no manifest (the "no manifest" line is expected). There are no criteria,
so each turn is judged by reading its reply against ground truth, and
`build_report.py` does not apply: it joins manifest entries to corpus variants.

1. Pull with `--raw --outputs` over the window. Store it OUTSIDE any git repo:
   production output files carry human-subject metadata.
2. Group `turns.json` by `user`. Separate real users from test accounts
   (charlie-test-3, cdemurjian) before counting anything.
3. For every turn decide: answered correctly, wrong or misleading, no answer, or a
   capability the product does not have. `status=error` and a non-null `error` are the
   no-answer turns; the wrong ones only show up by reading replies against each other
   and against ground truth (same question, different numbers, is the usual tell).
4. Write a report grouped by ISSUE, not by turn. Per issue: who and which turns, the
   question, what they got, what failed, the local evidence files (from
   `outputs_index.json` and `tasks/`), what to fix, and a candidate nessie_tests case.
   Do not trace each failure to its root cause during the review; list it, point at the
   evidence, and leave debugging to a separate pass.

## Gotchas that will mislead you

- **The app side runs on the container's clock, which is UTC on dev and prod**:
  `created_at`, run-root folder names, file-name stamps and file mtimes all agree
  (verified on production 2026-09-10). The trap is the HOST: fairdata's shell is US
  Eastern, and `docker logs --since 2026-09-07T18:16:00` without a `Z` is read as
  host-local time, which puts you four hours away from the turn. Always append `Z`.
  `pull.json` records the container's offset.
- **Run-root folders are per gunicorn process, not per user or session.** The user in
  `260904_132113_wesselr` is whoever's request started that process; later turns from
  anyone land in it. Use each turn's `run_root` and `outputs_index.json`, never the
  folder name.
- **`docker logs` only reach back to the last container recreate.** On an instance
  that was rebuilt since the turn, the progress stream (`--raw`) is the only record.

Every one of these produced a wrong conclusion on the first pass. **Re-verified
against the harness at `a85dde9` (2026-08-03).** The previous version of this list
was a 2026-07-24 snapshot and seven of its nine bullets had gone false or stale —
including one that told a triager to discount a genuine route failure — so
re-derive rather than trusting this a third time.

- **`TimeoutError` in the manifest is never a server hang.** `drive()` breaks on
  its own deadline (defaults 60s route / 600s full at `http_driver.py:61`, set at
  `:81`, enforced by the `break` at `:101-102`), it does not raise. The socket timeout on the harness's own HTTP calls is **120s, not 30**
  (`SOCKET_TIMEOUT_S`, `http_driver.py:14`), and a single socket failure mid-poll
  is swallowed: it increments the entry's `poll_errors` and only **five
  consecutive** failures re-raise (`http_driver.py:20`, `:89-93`). So a
  `TimeoutError` means the endpoint was down for five polls running, or the
  opening POST — which is outside that loop (`:44`) — timed out. Read the entry's
  `poll_errors`, then check the task's real status in the database: a "timed out"
  case may have completed fine server-side.
- **`route: None` does not mean unrouted.** It means the harness gave up before it
  observed `route_decided`. The real route is in the task's progress column.
- **A `container_cc` route is not automatically a misroute — and the harness
  stopped claiming otherwise.** `runner.default_route_criterion` now returns
  `None` (`runner.py:33-46`); its docstring records why. It used to inject
  `route == nextseek_query` into every variant tagged `"base"`, which
  `corpus.load_base` applies to all of them — nobody ever curated that, and it
  made deliberate `container_cc` routing (open-ended analysis, resource creation)
  read as a product failure. What ended is the BLANKET assumption, not injection:
  `corpus.apply_route_policy` reads the curated `route_policy` block in
  `corpus.json` — twelve families plus seven per-variant overrides — and
  attaches a `route` criterion to turn 0 of **268** variants, while 15 more write
  one inline. **All 283 resolved variants carry a `route` criterion; only three
  are `route_gate`** (`route.ns_advanced`, `route.unrelated`,
  `route.ns_plain_study_membership`). So a `route` criterion on a case that is
  not a gate is the policy working, NOT harness residue — reading it as residue
  is how a real misroute gets discounted. **A route failure you see today is a
  curated expectation, not a harness assumption. Do not discount it.** Read the
  router's own `reasoning` to judge it, not to excuse it.
- **When a case routes to CC, some NS criteria cascade — but not all, and the
  difference is the whole point.** There is no `parser_plan` on the CC route. The
  four DERIVED NS outcome fields — `api_outcome_observed`,
  `graph_outcome_observed`, `report_produced_output`, `outcome_observed` — are
  recorded `skipped` on an observed `container_cc` turn rather than failed
  (`evaluate.py:394-401`), because a CC `query_complete` carries no `debug` key at
  all and they were constant-false by construction. Inline, hand-written criteria
  — `api_ok`, `neo4j_ok`, `parser_plan.*`, `api_plan.*`, `graph_result.*` — are
  deliberately NOT skipped and still fail, because a case carrying them is
  claiming a particular engine answered it. Count causes, not criteria.
- **xpass detection EXISTS.** A `known_fail` case, or a `known_fail` consistency
  group, that passes every criterion is promoted to `status="xpass"` by
  `runner._apply_xpass` (`runner.py:363-375`, called from the variant loop at
  `:280` and the group branch at `:322`), and `_is_real_failure` counts it
  (`runner.py:415-416`). You no longer have to hunt green known-fails by hand —
  but you do have to explain every `xpass`, because it means the corpus asserts
  something that is no longer true.
- **The graph LIMIT is 5000 now, and the sentinel check is a fallback, not the
  signal.** `NessieAI/tests/nessie_tests/limits.py` holds `GRAPH_LIMIT_SENTINELS = (250, 5000)`,
  covering the historical cap and the current one, but inferring truncation from a
  row count landing on a limit is only ever a guess — it went dead the moment the
  limit moved. The real signal is `graph_result.truncated`, which the Neo4j tool
  sets by comparing the returned count against the query's own trailing LIMIT. The
  criterion to read is `graph_truncation_disclosed` (`evaluate.py:270-297`): it
  asks whether the result is complete **or honest about being capped**, so a
  capped result passes only if it also reports a real `total` exceeding the rows
  returned. That is the right invariant — `graph.tissue_cell_impact` has 10,688
  legitimate rows against a 5,000 limit and can never satisfy "not truncated",
  while the failure that actually burned us was the 2026-07-27 run reporting
  exactly 5,000 as if it were the total. A disclosure failure, not a truncation
  one.
- **`api_artifact.*` criteria CAN pass, including on a Container-CC turn.** They
  are no longer resolved against a `run_root`. `build_artifact_index`
  (`evaluate.py:89-138`) builds a per-turn basename -> path index from the turn's
  own `query_complete`, reading `debug.report_saved_files`, `files`, `artifacts`
  (file-typed entries only, so an inline `"table"` or `"preview"` label does not
  fake a file) and `cc_raw_files`. The last two are what let a CC turn prove it
  produced a file at all. Two real limits survive, both recorded in that
  docstring:
  - **A multi-deliverable CC turn only ever exposes `artifacts.zip`.**
    `_publish_artifacts` zips whenever it finds more than one deliverable and
    emits a SINGLE artifact so labelled; the member filenames never reach
    `query_complete`. A reingest turn that writes a workbook plus anything else
    resolves `api_artifact.upload.xlsx` **False** and only
    `api_artifact.artifacts.zip` True. That is a payload limit with no
    harness-side fix, not a product defect — CC criteria must assert
    `artifacts.zip` for multi-file turns, and only a single-deliverable turn can
    assert a real basename.
  - **`.rows_gte` returns 0 for a CC artifact.** A CC or reporter artifact carries
    no `path` and is indexed under its bare label, so `resolve_artifact`
    (`evaluate.py:157-172`) returns 0 rather than resolving that label against the
    harness cwd and counting rows out of an unrelated same-named file
    (`samplesheet.csv` is asserted 3 times across 2 active variants,
    `pipeline.end_to_end_emit` and `pipeline.happy_path_scrnaseq`). A failing
    `.rows_gte` on a CC turn is unevaluable, not evidence.
- **Cases are not isolated.** Pipeline agent state leaks across cases, so a failure
  may have been caused by the case before it. Check execution order in the
  manifest, which is append-order.
- **Consistency groups DO set `elapsed_s`** (`runner.py:327`, and `:353` on the
  error path), so a `0.0` there is a genuinely instant result, not a reporting
  artifact. They also record each query's `count` and `route` as `observations`,
  so a group failure can be triaged from the manifest without re-running it.

## Do not

- Run the harness, re-ask a question, or write anything on production. Pull only.
- Re-run cases to triage them. Query the database.
- Trust a prior handoff's failure interpretation without re-deriving it. Several
  claims in the 2026-07-27 handoff were wrong, including "writes hang" and "both
  known-fails correctly red".
- Put credentials in any file here. The MySQL password is read live from the
  container environment at query time.
