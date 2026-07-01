---
name: nextseek
description: >
  NExtSEEK data query workflow. Trigger whenever the user asks to query
  NExtSEEK, find/list/show samples or projects, count samples ("how many",
  "list all", "show me", "which mice/samples..."), retrieve a sample by UID,
  look up a sample tree, run a graph lineage query, refine a previous search
  ("which of those..."), recall results from earlier in the conversation
  ("what sample types were in those results"), or ask what NExtSEEK can do.
  Default path is one `nextseek-query` call that runs the entire chat_nextseek
  pipeline in a single Python invocation and returns a final user-facing reply.
  Do NOT trigger on general bioinformatics questions, code/file edits,
  non-NExtSEEK data sources, or pure file-system tasks. For explicit writes
  (create/update/delete), submission generation (GEO/SRA/nf-core/PRIDE), and
  pipeline-stage debugging, see the escape-hatch section.
disable-model-invocation: false
---

# nextseek

NExtSEEK query workflow. Read this entire file before taking any action.

---

## Default path: `nextseek-query` (use this for ~95% of questions)

For any natural-language NExtSEEK question, the default action is **one** call:

```bash
nextseek-query --query "<user's full question verbatim>"
```

`nextseek-query` runs the entire pipeline (entity extraction, parser, API/memory/reporter/graph routing, NExtSEEK REST/Neo4j calls, final-reply composition) in a single Python invocation and **prints only the user-facing reply text to stdout** — exactly what should be surfaced to the user. No JSON parsing, no field extraction.

**Take the stdout as the reply, surface it, and stop.** Do not call any other `nextseek-*` tool unless the routing rules below say to. Do not run `nextseek-entity-extract`, `nextseek-parse`, or `nextseek-plan` first — `nextseek-query` does all of that internally. Do not pipe the output through `jq`, `python3 -c`, `grep`, or any extractor — the shim already does it.

If you need the full structured dict (parser_plan, api_plan, bundle_id, artifacts, files) — for debug-ladder inspection or to drive a downstream tool — add `--json`:

```bash
nextseek-query --query "..." --json
```

Then the dict is:

```json
{
  "reply": "<final user-facing answer text — what the user should see>",
  "debug": {
    "mode": "new_search | refine_last_search | ask_about_last_results | reporter | graph_query | system_question | unsupported",
    "parser_plan": { ... },
    "api_plan": { ... }
  },
  "bundle_id": 42,            // int OR null when no bundle was created
  "artifacts": [ ... ],       // optional, present only if files were written
  "files": [ ... ]            // optional, present only if files were written
}
```

The `--json` form is rarely needed. Use it only when SKILL.md routing or escape-hatch §4 explicitly says to.

### Worked example (schematic — no real data)

When the user asks a NExtSEEK data question, your invocation pattern is:

```bash
nextseek-query --query "<the user's full question, verbatim, in double quotes>"
```

Stdout is plain text — a final user-facing reply that the pipeline composed. Surface it as-is. The shape will look something like:

```
Found <N> <sampletype> samples matching <criteria>.

<formatted result list, table, or summary the chatter agent produced>

<optional follow-up offer, e.g. "Would you like me to export to /data/scratch/?">
```

That's it. One tool call, one answer. Do not validate the count, fabricate UIDs, or fill in numbers from your own knowledge — surface only what `nextseek-query` returned. If the reply references a file path, translate it via `DMAC_PATH_MAPPINGS` per "Reply hygiene" below before sending.

### When to add `--planner`

Use `nextseek-query --query "..." --planner` only when the user asks a compound question that explicitly requires multi-step execution. The pattern is "do X, **then** do Y where Y depends on X's results" — e.g. "find <criterion>, then look up the lineage of those" or "list <kind> from <project> and compare against <published artifact>".

Heuristic: if the user's question contains a sequencing conjunction ("then", "and then", "after that", "based on those results, …"), pass `--planner`. Otherwise omit it (the standard pipeline is faster).

`--planner` is read-class only — `run_query_plan` routes through the same read-only API surface as the standard pipeline (`nextseek-api-read`-equivalent calls), never `nextseek-api-write`. If the planner advises a write, the orchestrator stops and surfaces the proposed write in the `reply` for the user to confirm; you must then drop into `nextseek-api-write` with the L3 protocol below.

### Refinement and memory queries

`nextseek-query` reads session state from the SQLite session DB on every call. So follow-up questions that use demonstratives ("which of those …", "what … were in those results", "narrow that to …", "tell me more about #3") are **the same single tool call** — just pass the new question verbatim:

```bash
nextseek-query --query "<the user's follow-up question, verbatim>"
```

The parser routes this to `refine_last_search` or `ask_about_last_results` automatically because the prior `bundle_id` is in the session DB. Do NOT manually pass prior context — the pipeline handles it.

**Edge case — demonstrative without prior bundle**: if the user asks "which of those…" / "what sample types were in those results…" but there is no prior `bundle_id` in the session, `nextseek-query` will return `debug.mode == "unsupported"` (or an empty result). Do NOT silently retry with a guessed query; reply to the user with: "I don't have a prior search to refine — please run a fresh search first (e.g. 'Find me X')."

---

## Tool capability matrix — what each tool does

This is the authoritative contract. Do NOT infer capabilities from binary names, repeated `--help` invocations, or by reading `/app/plugins/nextseek/bin/*` source. If a question can't be answered from this matrix, ask the user — don't go spelunking.

| Tool | What it does | Input | Output | Class | When to use |
|---|---|---|---|---|---|
| `nextseek-query` | Full read pipeline: entity → parser → api/memory/reporter/graph/system → chatter. The parser already routes "build a GEO/SRA/PRIDE submission for ..." to reporter mode `report_generation` and calls `report_writer_agent` with the user's full question + a reporter-agent-built plan. | `--query "<text>"` (+ optional `--planner`, `--json`) | Plain-text reply on stdout (or full dict with `--json`) | Read-only | **Default for everything** — search, retrieve-by-UID, refine, recall, sample-tree / lineage, project summary reports (samples/protocols/published/rppr), submission generation (GEO/SRA/PRIDE/nf-core), system-capability questions |
| `nextseek-api-write` | Direct POST/PUT/DELETE against NExtSEEK | parser-plan body + `--confirmed-write` | JSON API response | **WRITE** (3-layer protocol applies) | Explicit create/update/delete after L3 user confirmation |
| `nextseek-generate-submission` | Low-level shortcut: skips the parser + reporter agents and calls `report_writer_agent` directly with a minimal plan. Receives an *empty* user-question string, so it has less context than `nextseek-query` and frequently returns nulls. | `--type {GEO,SRA,NFCORE_RNASEQ,NFCORE_SCRNASEQ,PRIDE}` + `--uids <comma-list>` | JSON `{"report": <bundle-or-null>, "type": "<type>"}` on stdout | Read-fetch + local file write | **Rarely.** Only when scripting and you intentionally want to bypass the parser. For any user-facing GEO/SRA/PRIDE/nf-core ask, prefer `nextseek-query`. |
| `nextseek-report` | Standalone project-summary report (same surface `nextseek-query` reaches via reporter mode) | `--mode {samples,protocols,published,rppr} --project <name>` | JSON report | Read-only | Rarely — `nextseek-query` reaches the same place from natural language. Use only when you have both `--mode` and `--project` named explicitly and want to skip the parser. |
| `nextseek-entity-extract`, `nextseek-parse`, `nextseek-plan`, `nextseek-api-read`, `nextseek-graph` | Single-stage debug probes | varies | structured JSON | Read-only | Debug ladder ONLY — invoke when `nextseek-query` returned something you don't trust and you need to inspect a specific stage |

### `nextseek-query` IS the path for submissions — don't reach for the shortcut

A user asking "Build me a GEO submission for D.SEQ-... and D.SEQ-..." is a normal `nextseek-query` call. The parser detects "geo / sra / pride" in the query text (`_infer_report_type_from_query`), routes to reporter mode `report_generation`, the reporter_agent builds a full `ReportWriterPlan` from the user's question, and `report_writer_agent` runs with that plan **plus the user's verbatim question**. Same downstream agent, much more context. Just do:

```bash
nextseek-query --query "<the user's full question, verbatim>"
```

The standalone `nextseek-generate-submission` exists for scripting cases where you have a UID list and want to bypass the parser. It calls `report_writer_agent(config, "", ReportWriterPlan(report_type, uids))` — note the empty question string and the minimal plan. With that little context, the agent often returns null fields. **Do not use it as the default for user-facing submission asks. Do not use it as a "lower-level retry" if `nextseek-query` returned something unexpected — that's the wrong direction.**

### Things `nextseek-query` does NOT do

- It does **not** write to NExtSEEK. Writes go through `nextseek-api-write` under the L3 protocol.

### What "nulls" / empty fields from `nextseek-generate-submission` mean

If you ended up calling `nextseek-generate-submission` directly and got a `{"report": null}` or all-null fields, the most likely cause is the empty-question / minimal-plan invocation above — the writer agent had nothing to anchor on. The correct recovery is to run the user's actual question through `nextseek-query` instead. Other possible causes (UIDs not published, UIDs typo'd, credential scope) only matter if `nextseek-query` ALSO comes back empty — at which point apply the Stop-after-2 rule and ask the user.

---

## When NOT to use `nextseek-query` — the four escape-hatch categories

Drop into a fine-grained tool only in these explicit cases:

### 1. Writes (POST/PUT/DELETE)

`nextseek-query` is read-only. For any operation that creates, updates, or deletes data on the NExtSEEK server, route through `nextseek-api-write` and apply the **3-layer write safety** below. Layer 1 (Claude Code permission allowlist) blocks `nextseek-api-write` unless explicitly approved by the user; Layer 2 (the shim) refuses without `--confirmed-write`; Layer 3 is the behavioral protocol in this file.

### 2. Submission generation — actually still `nextseek-query`

GEO / SRA / nf-core / PRIDE submission asks are **not** an escape hatch. The parser routes them to reporter mode `report_generation` automatically. Default path:

```bash
nextseek-query --query "Build me a GEO submission for <UID-1>, <UID-2>, ..."
```

The standalone `nextseek-generate-submission` exists, but per the capability matrix above it is a low-level shortcut that bypasses the parser/reporter agents and frequently returns nulls. Reach for it only when scripting around the parser; never as a fallback for an unexpected `nextseek-query` result.

### 3. Project summary reports

`nextseek-query` will route project summary asks through the reporter agent automatically. Use the standalone `nextseek-report` only when the user explicitly says "build me the report" with a known project name AND mode:

```bash
nextseek-report --mode samples --project IMPACT
nextseek-report --mode rppr --project IMPACT
```

Modes: `samples`, `protocols`, `published`, `rppr`.

### 4. Debugging / structured plan inspection

If `nextseek-query` returns a `reply` you don't trust (e.g. routing to `unsupported` when it shouldn't, or empty results that look wrong), THEN you may probe the pipeline stages individually:

| Tool | Purpose |
|---|---|
| `nextseek-entity-extract --query "<text>"` | See what sampletypes/assays/keywords were extracted |
| `nextseek-parse --query "<text>"` | See the parser plan (mode, target endpoint, filters) |
| `nextseek-plan --query "<text>"` | See the multi-step planner output. Executes read-only steps and returns advisor recommendations; never executes writes. |
| `nextseek-api-read --parser-plan '<json>'` | Manually execute a read-safe API call from a parser plan. Only endpoints in `read_safe_endpoints.json` are accepted; others exit `WRITE_BLOCKED` (5). |
| `nextseek-graph --query "<text>"` | Manually run a Neo4j Cypher lineage query |

Use these as a debugging ladder, not a default workflow.

---

## Catalog access (still relevant for capability questions)

For pure capability questions ("what can I ask?", "what sampletypes do you have?", "what assays are tracked?"), prefer reading the cached catalogs directly via the `Read` tool — no nextseek-* call needed:

- `/app/plugins/nextseek/context/min_api_endpoints_enriched.json` — endpoint catalog.
- `/app/plugins/nextseek/context/min_sampletypes_db.json` — sampletype vocabulary.
- `/app/plugins/nextseek/context/min_assays_db.json` — assay vocabulary.
- `/app/plugins/nextseek/context/projects_db.json` — project list.
- `/app/plugins/nextseek/context/neo4j_schema.json` — Neo4j schema.
- `/app/plugins/nextseek/context/capabilities.md` — user-facing capability doc.

For *data* questions ("find me mice with X", "how many Y in Z"), always use `nextseek-query` instead — the catalogs alone won't answer those.

---

## Reply hygiene

After any `nextseek-*` call:

- The user-facing answer is always the `reply` field (for `nextseek-query`) or composed by you from the tool's structured output (for fine-grained tools). Surface what the user asked for, not raw JSON.
- Quote the **host-side path** of any artifact produced (file written under `/data/scratch/`, submission file, report). Read `DMAC_PATH_MAPPINGS` from the environment to translate container paths to host paths. Format is a JSON object mapping container roots to host roots (e.g. `{"/data/scratch": "/persistent/scratch/alice"}`). If `DMAC_PATH_MAPPINGS` is absent or unparseable, report the container path and note that the host mapping was unavailable.
- Do not dump raw JSON unless the user explicitly asks ("show me the parser plan", "show me the API response").
- Do not narrate intermediate steps ("calling entity-extract... calling parser..."). The pipeline runs internally; the user sees one tool call and one answer.

---

## Write safety — 3 layers

For non-GET operations (`nextseek-api-write`, write-class endpoints):

- **Layer 1 (mechanical, deployment-dependent)**: a Claude Code permission allowlist / deny rule that gates `nextseek-api-write`. **In the dmac-assistant bridge POC, the `container_cc` route runs under `--permission-mode auto` (per the host bridge's launch command), NOT `--dangerously-skip-permissions`.** Under auto mode, blanket `Bash(*)` allow rules are dropped and every tool call — including `nextseek-api-write` — is screened by the auto-mode classifier, which blocks escalation/exfiltration. That classifier is a behavioral gate, not a hard guarantee, and no explicit `Bash(nextseek-api-write:*)` deny rule is shipped here. Treat L1 as defense-in-depth, not as a guarantee — the load-bearing layers are L2 and L3.
- **Layer 2 (mechanical, always on — enforced server-side)**: an `api-write` op is refused unless write confirmation is explicit. The `nextseek-api-write` shim requires `--confirmed-write`, and the authoritative gate now runs **outside** the agent container: the sidecar's write gate (`sidecar/app/write_gate.py`) refuses the op unless `confirmed_write` is exactly `True`, and NExtSEEK enforces its own server-side write gate behind that. Because neither gate runs in a process the in-container agent controls, the agent cannot bypass L2.
- **Layer 3 (behavioral, this skill — load-bearing)**: NEVER call `AskUserQuestion` (`container/CLAUDE.md` forbids it; the chat UI doesn't render the widget). Instead, write plain text:

> "About to execute a WRITE-classified operation. Method: POST. Endpoint: /samples/<...>/. Body: {...}. **Confirm?**"

Then wait for the user's next message. If the user responds "yes" / "go ahead" / similar, invoke `nextseek-api-write` with `--confirmed-write`. If anything else, abort and acknowledge.

---

## Stop-after-2 rule (load-bearing)

This rule applies to **every** `nextseek-*` tool, not just `nextseek-query`. If a `nextseek-*` tool returns an unsupported answer, empty/null fields that look wrong for the question, or a non-zero exit, you MAY retry **once** with a corrected invocation — rephrase the question, fix a typo'd literal, add `--planner` for a multi-step ask, correct a wrong `--type` / `--uids` value. **Do NOT make a third attempt, and do NOT switch to a different `nextseek-*` tool to "preflight" or reverse-engineer the failure** (e.g. running `nextseek-query` to fetch metadata after `nextseek-generate-submission` returned nulls — see the matrix above; those tools do not chain).

If the second attempt also fails, STOP and reply to the user in plain text with:

- What was attempted (the two calls you made, including arguments)
- The error / unexpected output you observed
- One specific clarifying question that would unblock you (e.g. "Did you mean sample type X or Y?", "Are these UIDs published?", "Which project should I scope this to?")

The dmac-assistant chat UI does not render `AskUserQuestion`, so the clarification MUST be plain text.

### Hard prohibitions after a failed nextseek-* call

After a `nextseek-*` tool returns nulls, empty data, or a non-zero exit, you MUST NOT do any of the following — these are budget-sinks that cannot produce a correct answer:

- `Read` any file under `/app/plugins/nextseek/bin/` — those are the runner internals, not user-facing docs
- `Grep` or `Glob` `/app/plugins/nextseek/bin/` or the in-image `chat_nextseek` source for keywords (`dry_run`, `report_writer`, `submission`, etc.)
- run `python3 -c "import inspect; inspect.getsource(...)"` against any `chat_nextseek.*` symbol
- call `--help` repeatedly looking for hidden flags — the matrix above is the complete contract; there are no hidden flags
- call a sibling `nextseek-*` tool to attempt to "fetch what the failed tool needed"

The escape-hatch debug ladder (`nextseek-entity-extract` / `nextseek-parse` / `nextseek-api-read`) exists for a narrow purpose: the user explicitly asked you to inspect a specific pipeline stage, or the failure is mechanical (e.g. you need a parser_plan to construct an `nextseek-api-write` body). It is **not** a fallback for unknown answers.

This is a hard cap: two attempts per user question across all `nextseek-*` tools combined, then plain-text clarification ask.

## Errors

The runner emits structured errors as one-line JSON to stderr with these codes (exit code in parens):

- `CONFIG_MISSING` (2): `API_USER` / `API_PASS` not set. Tell the user; do not retry.
- `IMPORT_FAILED` (2): `chat_nextseek` not installed in the image. Surface a deploy-side message.
- `VALIDATION` (3): bad CLI args. Fix the call.
- `AGENT_FAILED` (4): LLM / network failure. Retry **once** with the exact same call; if still failing, surface the error to the user with the structured payload.
- `WRITE_BLOCKED` (5): write shim invoked without `--confirmed-write`, OR `nextseek-api-read` received a non-read-safe endpoint. Apply the L3 prompt only for true writes; otherwise fix routing.
- `CONFIG_ERROR` (6): plugin context file (e.g. `read_safe_endpoints.json`) missing in image. Deploy-side issue; surface as "plugin misconfiguration, please rebuild image."
