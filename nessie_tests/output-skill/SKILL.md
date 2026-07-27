---
name: nessie-run-review
description: Use when triaging a nessie_tests run and producing a reviewable report - after `manage.py nessie --tier full` has run on the dev box, when someone asks "why did these tests fail", "are these real bugs or drift", or wants an HTML review of a test run. Recovers observed values from the database so failures can be judged without re-running paid turns.
---

# Reviewing a nessie_tests run

Turns a nessie_tests run into a reviewable HTML report where every failure carries
its **expected vs observed** values and a verdict.

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
python scripts/fetch_run.py --out ./run-<date> \
    --manifest /app/nessie_out_full/manifest.json \
    --since "2026-07-24 20:05:00" --until "2026-07-24 20:45:00"
```

Writes `manifest.json` and `turns.json`. `turns.json` holds, per turn: the routing
decision (route, source, and the router's own reasoning), the parser mode, and the
**full engine call** — the graph plan with its bound parameters and result meta, or
the API plan with its complete request body and result meta — plus the reporter
plan, CC model id and cost. Graph result rows are stripped so the file stays small.

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
    --repo <dev-v3-merge checkout> --triage ./triage.json --out ./report.html
```

It joins each manifest entry to its declared turns, their asserted criteria (from
`chat_nextseek/e2e/catalog.json` + `nessie_tests/overlay.json`), each turn's task
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
             {"verdict": "real", "family": "search_advanced",
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

## Gotchas that will mislead you

Every one of these produced a wrong conclusion on the first pass.

- **`TimeoutError` in the manifest is never a server hang.** `drive()` breaks on
  its own deadline, it does not raise. The only source is the **30s socket
  timeout** on the harness's own HTTP calls. Always check the task's real status
  in the database: a "timed out" case may have completed fine server-side.
- **`route: None` does not mean unrouted.** It means the harness gave up before it
  observed `route_decided`. The real route is in the task's progress column.
- **A `container_cc` route is not automatically a misroute.** The harness injects
  `route == nextseek_query` into *all* imported variants via
  `default_route_criterion`, because `load_base` tags them `"base"`. That is a
  harness assumption, not a curated expectation. Read the router's own `reasoning`
  before calling it wrong.
- **When a case routes to CC, every NS criterion fails as a cascade.** There is no
  `parser_plan` on the CC route. One decision, many red criteria. Count causes,
  not criteria.
- **Check `expected_fail` cases that passed.** There is no xpass detection, so a
  known-fail that starts passing still reads green.
- **Check whether graph results equal the LIMIT cap** (250). A capped result set is
  indistinguishable from a complete one, and no criterion in the corpus looks at
  counts.
- **`api_artifact.*` criteria can never pass.** They resolve against `run_root`,
  and `evaluate.py` never passes one. Always `drift`.
- **Cases are not isolated.** Pipeline agent state leaks across cases, so a failure
  may have been caused by the case before it. Check execution order in the
  manifest, which is append-order.
- **Consistency groups never set `elapsed_s`**, so `0.0` is a reporting artifact,
  not an instant failure.

## Do not

- Re-run cases to triage them. Query the database.
- Trust a prior handoff's failure interpretation without re-deriving it. Several
  claims in the 2026-07-27 handoff were wrong, including "writes hang" and "both
  known-fails correctly red".
- Put credentials in any file here. The MySQL password is read live from the
  container environment at query time.
