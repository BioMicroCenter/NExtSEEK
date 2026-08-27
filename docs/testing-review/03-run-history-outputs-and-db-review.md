# Run-history review: outputs/ traces + DB store (NExtSEEK assistant)

Read-only forensic analysis of 13 real NS-mode run traces under `./outputs/` and the
Django chat-persistence tables in MySQL `dmac`. Goal: a concrete "what worked / what
failed" picture that feeds test-case design and the #32 (traverse-and-aggregate /
thin-bundle) and #33 (unstable NHP-sequencing routing + LIMIT-cap counts) fixes.

Date of analysis: 2026-07-24. Stack live at commit tip of `feat/luria-launch-mode`.
All counts/keys below are copied from the live DB and container, not reconstructed.

---

## 0. Two routing levels (important framing)

There are **two** routers, and the task's "top-level PARSER → NS/CC" is the first of them:

1. **Top-level route (NS vs CC vs Unrelated)** — `nextseek_api/cc_assistant/router.py`
   `decide()` (BAML `RouterAgent`, keyword-heuristic fallback at `router.py:110-135`).
   Returns `nextseek_query` / `container_cc` / `unrelated`. CC turns persist to
   `assistant_cc_transcript`; NS turns persist to `assistant_chat_session.results_history`.

2. **NS-internal parser (the `[DEBUG][PARSER] Parsed plan → "mode"`)** — the
   chat_nextseek orchestrator's parser (`chat_nextseek/src/chat_nextseek/agents/parser.py`).
   Emits `mode ∈ {new_search, refine_last_search, ask_about_last_results, graph_query,
   system_question, reporter}` plus `target_endpoint` and `target_result_id`. **This is
   where the #32/#33 instability lives** — every trace in `outputs/` is an NS turn, so
   what we see is the NS-internal parser choosing between `advanced_search`,
   `parents_by_child_types`, `admin/samples/retrieve`, and Neo4j `graph_query`.

All 13 analyzed run dirs are **NS-mode** turns. The two CC turns in this window
("Create a file at /data/scratch/hello.txt", "Write a Python one-liner…") left no
`outputs/*/console.txt` orchestrator trace and stored a zstd transcript blob instead
(see Part B).

---

## PART A — outputs/ trace analysis

Each `outputs/<ts>_demo/console.txt` is a Streamlit session log that may contain several
user turns. Grep anchors used: `[DEBUG][PARSER] User query:` / `Parsed plan` (mode +
`target_result_id`), `[DEBUG][GRAPH] Generated cypher:`, `[DEBUG][GRAPHDB] Query returned
N records`, `[DEBUG][API] Response … "total":`, `[DEBUG][MEMORY_CODER]`,
`[DEBUG][CHATTER] Final answer`, `STRUCTURED_PARSE … validation_errors`.

### A.1 Per-turn table

Legend: **route** = NS-internal parser mode → engine/endpoint. **PASS** = returned a
correct, non-empty, non-capped answer to what was asked. **PARTIAL** = returned *a*
number but wrong entity / capped / misleading. **FAIL** = empty/error/incoherent.

| Run dir (session) | User question | Parse mode → engine | Result | P/F | Root cause |
|---|---|---|---|---|---|
| **260722_180627** (3a95bb21) L193 | How many samples in the database? | `graph_query` → `MATCH (s:Sample) RETURN count(s)` | 50,889 | PASS | Global count; graph is right tool. |
| ″ L305/590 | Find all Mouse (MUS) treated with NDMA | `new_search` → advanced_search `{sampletype:MUS, filter_searchText:NDMA}` | total **195** | PASS | keyword LIKE match works. |
| ″ L416 | …including their age | `new_search` → advanced_search `filter_searchText:"NDMA age"` | total **0** | FAIL | Multi-keyword joined into one LIKE `%NDMA age%` → no hit; self-recovered on retry to NDMA-only (195). Age is a *display* attr, not a filter term. |
| ″ L1088 | Find me sequencing data associated with non human primates | `new_search` → **parents_by_child_types** `{child:[D.SEQ], parent_filter:[NHP]}` | total **139** | PARTIAL | Returns **NHP parents** (thin id/uuid), not the D.SEQ files the user asked for; chatter mislabels them "139 D.SEQ records". |
| ″ L1267 | unique counts of sex and species of those monkeys | `ask_about_last_results` target_id=1 → memory_coder | **"No sex/species fields found"** | FAIL | Bundle is thin (parents_by_child_types stores only id/uuid/sample_type). #32b. |
| **260722_184930** (b714f5ae) L182 | What sample types are available? | `system_question` → get_searches | catalog narrative | PASS | Static capabilities answer. |
| ″ (same NHP + follow-up pair) | Find…NHP seq data / counts of sex+species | `new_search`→parents_by_child_types (139) ; `ask_about_last_results` id=1 | 139 ; **no sex/species** | PARTIAL / FAIL | Same thin-bundle failure as above. |
| **260722_185027** (aa93d142) | Find…NHP seq data / sex+species counts | parents_by_child_types (139) ; memory_coder | 139 ; **no sex/species** | PARTIAL / FAIL | idem. |
| **260722_185413** (84f8b1cb) | Find…NHP seq data / sex+species counts | parents_by_child_types (139) ; memory_coder id=1 | 139 ; **computed "No sex or species fields"** | PARTIAL / FAIL | Memory bundle proves fields absent (see A.3). |
| **260722_195815** (f5aeebbc) L202/382/452 | Find…NHP seq / "for those results counts of sex+species" / explicit DERIVED_FROM traverse | `new_search`→parents_by_child_types (139) ; then `graph_query` with **empty cypher `''`** ; advanced_search NHP total 0 | 139 ; **graph produced empty cypher** ; 0 | PARTIAL / FAIL | Graph agent emitted no cypher for the aggregate-on-parent intent; #32a (can't traverse child→parent AND aggregate parent attr). |
| **260723_192223** (cdc3927e) L202 | Find me sequencing data associated with non human primates | `new_search`→parents_by_child_types | total **139** | PARTIAL | thin NHP parents. |
| ″ L352 | What species are those SED NHP's | `ask_about_last_results` **target_id=1** → memory_coder | **species: [] (0 found)** ; also 1× MemoryCoder JSON parse retry (L392) | FAIL | #32b thin bundle: samples carry only id/uuid/sample_type; memory_coder filtered to 19 "SED" uuids, found no species field. |
| ″ L600 | Build nf-core rnaseq run for 4 D.SEQ UIDs, grouped by treatment+dose | `reporter` → ReporterPlan | **3× ReporterPlan parse fail (EOF)** → StructuredOutputError; fell back to admin/samples/retrieve (total_samples 22) | FAIL | LLM emitted JSON missing closing brace 3×; reporter aborted. |
| **260723_192601** (28c9e008) | (same 4-query nf-core + NHP battery) | reporter → parse-fail; parents_by_child_types 139; graph_query 250 | reporter FAIL; 139; **250** | mixed | Same ReporterPlan EOF; **graph route now returns 250 (capped)** — see A.2. |
| **260723_192822** (0752fe8c) L191 | Find NHP sequencing data | `new_search`→parents_by_child_types | total **139** | PARTIAL | thin. |
| ″ L354 | Which of those are RNA-seq? | `ask_about_last_results` id=1 → memory_coder | **count 0 ("No samples identified as RNA-seq")** | FAIL | #32b: bundle rows have no assay/library field; RNA-seq undecidable from id/uuid. |
| ″ L598/732 | Find sequencing data for non-human primates (×2) | `graph_query` (after 1× ParserPlan EOF retry L600) → DERIVED_FROM child→parent, `LIMIT 250` | **250** (both) | PARTIAL | 250 is the LIMIT cap, not a count; true D.SEQ count = **1608** (see A.2). |
| ″ L1034 | Find me monkeys with the 4 week study attribute | `new_search`→advanced_search `filter_searchText:"4 week study"` | total **0**, then OR-retry `4 OR week OR study…` total 0 | FAIL | Attribute phrased as free-text keyword; not in json_metadata as that literal. |
| ″ L1230 | Try that search again but the study as "4 week" | `refine_last_search` id=1 → advanced_search `"4 week"` | total **303** | PASS | Looser term matched 303 NHP (Macaca mulatta / Seder Lab). Rich bundle. |
| ″ L1374 | Find me sequencing data associated with those two NHPs above | `new_search`→admin/samples/retrieve id=2 | **3× APIRequestPlan parse fail (EOF, dup "descendants) descendants")** → chatter: "could not be completed, missing identifiers" | FAIL | LLM JSON malformed 3×; api_agent aborted. |
| ″ L1526/1680 | Find sequencing data for NHP-…FLY-1/2-PUB (explicit UIDs) | `graph_query` DERIVED_FROM with `$uids` | **0 records** | FAIL | Cypher matched `ancestor.UID IN $uids OR ancestor.Name IN $uids` — property likely `Name`/`uuid` mismatch; 0 hits though the 2 NHPs exist. |
| ″ L1764 | Trace full lineage both directions, return connected D.SEQ | `new_search`→retrieve, then graph repair | cypher **repair "references unknown properties ['SEQ']" → empty plan**, Traceback | FAIL | `descendant.type = "D.SEQ"` string-literal split on `.`; repair loop gave up. |
| **260723_193044** (8332ce61) L191/325 | Find sequencing data for non-human primates (×2) | `graph_query` (1× ParserPlan EOF retry) → DERIVED_FROM, LIMIT 250 | **250** (both) | PARTIAL | LIMIT cap; graph_result nodes = {id,uuid,type} only. |
| ″ (rest of battery) | 4-week / two-NHP / lineage | advanced_search 303/2 ; graph 0 ; retrieve parse-fail | mixed | mixed | Same failures as 192822. |
| **260723_193529** (9e249c0a) L189 | Find me monkeys with the 4 week study attribute | `new_search`→advanced_search | total **0** (+OR retry 0) | FAIL | idem 4-week keyword. |
| ″ L385 | Try again, study as "4 week" | `refine_last_search` id=1 | total **303** | PASS | rich bundle, 303 rows persisted. |
| ″ L529 | seq data associated with those two NHPs | `new_search`→retrieve id=2 | **APIRequestPlan EOF ×3** → "missing identifiers" | FAIL | idem malformed JSON. |
| ″ L681/835/919 | seq data for the 2 explicit NHP UIDs / lineage | `graph_query` `$uids` ; retrieve ; graph repair | **0 records** ; repair "unknown properties ['SEQ']" → empty | FAIL | idem UID-property mismatch + `D.SEQ` split. |
| **260723_203317** (e120442a) | (nf-core build) | `reporter`/retrieve | admin/retrieve total_samples 22 | PARTIAL | samplesheet artifacts written (files/nfcore_rnaseq/*), reporter plan still shaky. |
| **260723_205301** (88df4f11) L215 | Build nf-core rnaseq run for 4 D.SEQ UIDs | `reporter` → retrieve (22 samples) then samplesheet | files written; | PARTIAL | Produced samplesheet.csv/params.yml/launch.yml. |
| ″ L528 | List all Mouse treated with NDMA, incl age | `new_search`→advanced_search NDMA | total **195**; chatter adds age from json_metadata ("16 days old…") | PASS | advanced_search rich bundle → age answerable inline. |
| **260724_141629** (f9ae0afd) L190 | List all Mouse treated with NDMA, incl age | `new_search`→advanced_search | total **195**; age narrated from metadata | PASS | rich bundle; note results_history for this session = **897 KB** (full rows persisted). |

### A.2 The #33 evidence — same intent, three engines, three incompatible numbers

The near-identical phrasing "(find) sequencing data (associated with / for) non-human
primates" routed to **three different NS engines** across runs, each counting a
**different entity**, one of them **capped**:

| Phrasing | Engine chosen | Number | What it actually counts |
|---|---|---|---|
| "Find me sequencing data **associated with** non human primates" | `parents_by_child_types` (REST) | **139** | NHP **parents** that have ≥1 D.SEQ child |
| "Find sequencing data **for** non-human primates" | `graph_query` DERIVED_FROM child→parent `LIMIT 250` | **250** | D.SEQ **children** — **capped** |
| "Find NHP sequencing data" | `parents_by_child_types` | **139** | NHP parents |
| "List all Non Human Primate samples…" | advanced_search `{sampletype:NHP}` | **408 / 303** | NHP samples matching (rich) |

Ground truth pulled live from Neo4j during this review:

```
MATCH (child:Sample)-[:DERIVED_FROM*1..]->(parent:Sample)
WHERE child.type='D.SEQ' AND parent.type='NHP'
RETURN count(DISTINCT child)   → 1608     # true D.SEQ children  (graph reported 250 = LIMIT cap)
RETURN count(DISTINCT parent)  →  139     # NHP parents          (REST 139 is correct for parents)
```

So **250 is provably a `LIMIT 250` cap, not a true count** (true = 1608), and **139 vs
250 is not even the same entity** (parents vs children). Any test that asserts "NHP
sequencing data count" must pin (a) which entity, (b) that the graph query has no LIMIT
or a LIMIT ≥ true cardinality. Console refs: cypher+`LIMIT 250`+`returned 250 records`
at e.g. `260723_192822/console.txt:645-652`, `260723_193044/console.txt:238-245`.

### A.3 The #32b evidence — thin bundles kill every attribute follow-up

The memory-coder artifacts (`files/memory/memory_coder_bundle_1_*.json`) show the
follow-up path executing correctly but against attribute-less data:

- `260722_185413/…memory_coder_bundle_1_20260722T185509Z.json` — followup "unique counts
  of sex and species". `profile.record_arrays[0].examples` samples carry **only**
  `{id, uuid, sample_type, sample_type_description}`. `computed_result` =
  `"No sex or species fields were found in the sample results."`, `fields_checked` lists
  exactly those 4 keys. The memory_coder even generated correct extraction code checking
  `sex/gender/Sex/species/organism/…` — the fields simply were never in the bundle.
- `260723_192223/…_20260723T192511Z.json` — followup "What species are those SED NHP's".
  `computed_result.species = []`, `total_sed_samples = 19`, `metadata_summary = {}`.
- `260723_192822/…_20260723T192951Z.json` — followup "Which of those are RNA-seq?".
  `computed_result.count = 0`, `fields_checked = [id, sample_type, sample_type_description, uuid]`.

Contrast: when the **same NHP intent** goes through advanced_search (the "4 week" →
303 turn), the persisted rows carry full `json_metadata` (DateOfBirth, ID, Name, sex,
species, cohort…) and the chatter answers attribute questions inline
("primarily *Macaca mulatta* from the Seder Lab"). **The thin bundle is a property of
the chosen route, not of advanced_search.**

### A.4 Cross-cutting failure patterns (frequency across the 13 dirs)

1. **Thin-bundle attribute follow-ups (#32b)** — every `ask_about_last_results` on a
   `parents_by_child_types` or `graph_query` result returned empty (0 species / 0 RNA-seq
   / no sex). 5+ occurrences. **Highest-signal, fully reproducible.**
2. **Unstable engine selection for one intent (#33)** — 139 (REST parents) vs 250
   (graph children, capped) vs 303/408 (advanced_search) for "NHP sequencing data".
3. **LLM structured-output EOF truncation** — `ParserPlan` (graph_query for D.SEQ←NHP,
   `EOF at line 25 col 362`), `ReporterPlan` (nf-core, `EOF line 21 col 78`, 3×),
   `APIRequestPlan` (retrieve, `EOF line 11 col 271`, dup `"descendants) descendants"`,
   3×). Parser self-recovers on retry; Reporter/APIagent give up after 3 → user-visible
   FAIL ("could not be completed, missing identifiers"). Same fingerprint recurs
   verbatim across every replay → deterministic prompt/coercion bug, not flakiness.
4. **Cypher generation defects on explicit UIDs** — `ancestor.UID IN $uids OR ancestor.Name
   IN $uids` returns **0** for two real NHP UIDs; repair loop then chokes on
   `descendant.type = "D.SEQ"` splitting on the dot → `unknown properties ['SEQ']` →
   empty plan + Traceback. The two-explicit-UID lineage query never succeeds in any run.
5. **Multi-keyword AND-collapse** — advanced_search joins keyword lists into one LIKE
   (`%NDMA age%`, `%4 week study%`) → 0 hits; OR-retry sometimes helps, sometimes not.
6. **Chatter mislabels thin results** — calls 139 NHP *parents* "139 D.SEQ records"; calls
   250 (capped children) "a total of 250 … were found". Numbers are presented as
   authoritative counts with no cap disclosure.
7. **Benign noise** (ignore in tests): `ValueError: I/O operation on closed file` urllib3
   finalizer spam; `CCAssistantViewSet/EvaluatorViewSet: unable to guess serializer`
   drf-spectacular warnings at startup. Neither affects results.

### A.5 What worked (regression anchors)

- Global graph count: "How many samples" → 50,889 (`260722_180627:229-238`). PASS.
- advanced_search keyword: MUS+NDMA → 195, with age narrated from json_metadata
  (`260723_205301:603-613`, `260724_141629:265-275`). PASS.
- `system_question` catalog answer ("what sample types") → static capabilities. PASS.
- `refine_last_search` (target_result_id=1): "4 week study"(0) → refine "4 week"(303).
  Refine wiring + rich bundle persistence works (`260723_193529:385-412`). PASS.
- nf-core samplesheet emission: despite ReporterPlan parse noise, `admin/samples/retrieve`
  (22 samples, 7 types) + samplesheet.csv/params.yml/launch.yml were written
  (`260723_203317`, `260723_205301` `files/nfcore_rnaseq/`). PARTIAL-PASS.

---

## PART B — DB store analysis

### B.1 Tables (Django models → MySQL `dmac`)

Models: `nextseek_api/assistant/models_db.py`. Migration: `nextseek_api/migrations/0001_initial.py`.

| Django model | `db_table` | Purpose | Key columns |
|---|---|---|---|
| `ChatSession` (`models_db.py:7`) | `assistant_chat_session` | one NS chat session | `session_id` (uuid PK), `user_id` FK, **`results_history` JSON**, `last_debug` JSON, `extra_state` JSON, `title`, `created_at`, `updated_at` |
| `QueryTask` (`models_db.py:30`) | `assistant_query_task` | async query pipeline tracker | `task_id` uuid, `session_id` FK, `query` text, `status`, `progress` JSON, `result` JSON |
| `CCSessionTranscript` (`models_db.py:72`) | `assistant_cc_transcript` | **CC-mode** full jsonl, zstd-compressed | `chat_session_id` FK, `cc_session_id`, `turn_id`, **`blob` BinaryField**, `uncompressed_size`, unique `(chat_session, cc_session_id, turn_id)` |

Live row counts (2026-07-24): `assistant_chat_session` ≈ 19 recent; `assistant_cc_transcript`
= 9; `assistant_query_task` = 36.

**The per-turn result bundle for NS mode is the JSON array `assistant_chat_session.results_history`.**
There is no separate per-turn table for NS — each turn appends one bundle dict to that array.
CC turns write nothing to `results_history` (stays `[]`, 2 bytes) and instead store a
compressed transcript blob in `assistant_cc_transcript`.

### B.2 What IS vs IS NOT stored per NS turn

`results_history` size ranges from **2 bytes** (`[]`, for CC / reporter / system_question /
nf-core turns) to **1.5 MB** (advanced_search + refine session with full rows). So NS
search turns **do** persist a large, mostly-complete bundle. Bundle construction:
`chat_nextseek/artifacts.py:build_metadata_bundle` (60-150); standard path append at
`orchestrator.py:298 / 774`; planner path at `orchestrator.py:1490-1578`.

Top-level bundle keys (verified via `JSON_KEYS` on session `cdc3927e…`):
```
id, mode, user_query, timestamp, reply/terminal_reply, provisional_reply,
parser_plan, api_plan, endpoint, method, request_body, query_params,
api_result_full, api_result_slim, graph_plan, graph_result,
reporter_plan, reporter_result, report_saved_files, report_writer_output,
memory_payload, search_context, model_outputs, step_results, plan,
multi_parser_plan, files, paths, *_debug_path(s), memory_coder_artifact
```

Stored (good): full user_query, the parser plan (mode/target_endpoint/target_result_id),
the api_plan/request_body, the terminal reply, and — for advanced_search — the **complete
row set with `json_metadata`**. The memory_coder artifact (computed follow-up result) is
also persisted inline as `memory_coder_artifact` (a pointer, computed_result lives in the
outputs/ file).

NOT stored / lossy: no normalized per-sample table (everything is nested JSON, so
cross-turn "aggregate a parent attribute" needs a code path, not a query); graph turns
store **`memory_payload = null`** (the follow-up memory path has no rows-shaped payload);
CC-turn results never enter `results_history` (so an NS follow-up cannot see a prior CC
turn's data, and vice-versa — the cross-mode memory gap #9/#10/#11).

### B.3 The thin-vs-rich bundle question (#32b) — measured per route

Verified with `JSON_KEYS(results_history->'$[i]…samples[0]/rows[0]')` on live rows:

| Route (NS parser mode → engine) | Persisted per-sample keys | Rich? | Rows persisted |
|---|---|---|---|
| `advanced_search` (`new_search` / `refine_last_search`) — session `9e249c0a` b0/b1 | `id, uid, uuid, idurl, idlink, title, assays, created_at, first_name, sample_type, sample_type_id, contributor_id, attributeValue, **json_metadata**` | **YES** (json_metadata = sex, species, DoB, cohort…) | full (303, 2) |
| `parents_by_child_types` (`new_search`) — session `cdc3927e` b0 | **`id, uuid, sample_type, sample_type_description`** — nothing else, in BOTH `memory_payload.data.samples[]` AND `api_result_full.data.samples[]` | **NO** | full count (139) but attribute-free |
| `graph_query` (Neo4j DERIVED_FROM) — sessions `8332ce61` b0, `9e249c0a` b3 | `graph_result.data[]` nodes = **`id, uuid, type`** only; **`memory_payload = null`** | **NO** | capped at `count = 250` (= LIMIT), or 0 |

**Answer to the #32b question:** the stored bundles are thin **only for the
`parents_by_child_types` and `graph_query` routes** — those carry no per-sample attributes
(`{id,uuid,sample_type[,type]}`), which is exactly why every sex/species/age/RNA-seq
follow-up on them computed empty. `advanced_search` bundles are **not** thin — they persist
full `json_metadata`. The task's shorthand "advanced_search bundles store only {id,uuid}"
is slightly mislabeled: it is the **NHP-lineage routes** (get_parents + graph) that are
thin, and the parser sends most "seq data for NHP" phrasings there instead of to
advanced_search. Fixing #32 = either (a) enrich the get_parents/graph result with the
child rows' `json_metadata` before persisting, or (b) route attribute-bearing lineage
follow-ups to a join that re-fetches full rows for the recalled ids.

### B.4 Cross-reference: outputs/ dir ↔ DB session

Matched by timestamp + title (session start ≈ dir name):

| outputs dir | session_id | title (truncated) | rh_items / bytes |
|---|---|---|---|
| 260722_180627 | `3a95bb21…` | How many samples are in the database? | 1 / 4,944 |
| 260722_184930 | `b714f5ae…` | What sample types are available? | 0 / 2 |
| 260722_185027 | `aa93d142…` | Find me sequencing data … NHP | 1 / 78,795 |
| 260722_185413 | `84f8b1cb…` | Find me sequencing data … NHP | 1 / 79,070 |
| 260722_195815 | `f5aeebbc…` | Find me sequencing data … NHP | 1 / 78,286 |
| 260723_192223 | `cdc3927e…` | Find me sequencing data … NHP | 1 / 79,515 |
| 260723_192601 | `28c9e008…` | Build an nf-core rnaseq run … | 0 / 2 |
| 260723_192822 | `0752fe8c…` | Find NHP sequencing data | 1 / 78,989 |
| 260723_193044 | `8332ce61…` | Find sequencing data for non-human primates | 2 / 85,118 |
| 260723_193529 | `9e249c0a…` | Find me monkeys with the 4 week study attribute | 4 / 1,546,070 |
| 260723_203317 | `e120442a…` | Build an nf-core rnaseq run … | 0 / 2 |
| 260723_205301 | `88df4f11…` | Build an nf-core rnaseq run … | 0 / 2 |
| 260724_141629 | `f9ae0afd…` | List all NDMA-treated mice … histogram of their age | 1 / 897,016 |

Note: `reporter` (nf-core) and `system_question` and CC turns all persist `results_history
= []` (2 bytes) — their outputs live in `outputs/*/files/` or `assistant_cc_transcript`,
not in the bundle array. CC transcript sessions (`eef192e9`, `22708e22`, etc.) appear in
`assistant_cc_transcript` with 19 KB–136 KB zstd blobs.

---

## Test-design recommendations (real input → observed output)

Grouped by the bug each pins. Use the exact DB numbers above as oracles.

**#32b thin-bundle regression suite (deterministic, highest value):**
1. `new_search` "Find me sequencing data associated with non human primates" → assert
   route = parents_by_child_types, total = 139, and (post-fix) persisted samples carry
   attribute keys beyond `{id,uuid,sample_type,sample_type_description}`.
2. Follow-up "unique counts of sex and species of those monkeys" → currently
   `computed_result = "No sex or species fields found"`; post-fix must return non-empty
   sex/species counts. (memory_coder path itself works — only the data is missing.)
3. Follow-up "Which of those are RNA-seq?" → currently count 0; post-fix non-zero.
4. Graph route "Find sequencing data for non-human primates" → assert `memory_payload`
   is **not null** post-fix so follow-ups have a rows payload.

**#33 routing + LIMIT-cap suite:**
5. Feed the 4 near-identical NHP-seq phrasings; assert they resolve to **one** canonical
   engine (or at least one canonical *entity+count*). Today: 139 / 250 / 139 / 408.
6. Graph DERIVED_FROM D.SEQ←NHP must return **1608** (or paginate), never exactly 250;
   assert no bare `LIMIT 250` masquerading as a count. Chatter must disclose caps.

**Structured-output robustness suite (deterministic — same EOF fingerprint every replay):**
7. "Build an nf-core rnaseq run for D.SEQ-240910LAU-135/136/137/94-PUB grouped by
   treatment and dose" → ReporterPlan must parse (today: 3× `EOF line 21 col 78` → fail).
8. "Find me sequencing data associated with those two NHPs above" → APIRequestPlan must
   parse (today: 3× `EOF line 11 col 271`, duplicated `"descendants) descendants"` → fail).
9. Parser graph_query for "D.SEQ derived from NHP" → 1× `EOF line 25 col 362` then
   recovers; assert single-attempt success post-fix.

**Cypher-correctness suite:**
10. "Find sequencing data for NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB" (two real UIDs)
    → today **0 records** (UID/Name property mismatch); assert > 0.
11. Any query with a `D.SEQ` type literal → repair loop must not split on `.` and emit
    `unknown properties ['SEQ']`.

**advanced_search keyword suite:**
12. "List all Mouse treated with NDMA including their age" → 195 + age from json_metadata
    (PASS anchor). "Find … NDMA age" multi-term must not collapse to `%NDMA age%` → 0.
13. "4 week study" (0) then refine "4 week" (303) → refine_last_search + rich-bundle
    persistence anchor.

---

## Appendix — concrete pointers

**Django models / persistence:**
- `nextseek_api/assistant/models_db.py:7` `ChatSession` → table `assistant_chat_session`,
  `:14` `results_history` JSON, `:15` `last_debug`, `:16` `extra_state`.
- `nextseek_api/assistant/models_db.py:30` `QueryTask` → `assistant_query_task`.
- `nextseek_api/assistant/models_db.py:72` `CCSessionTranscript` → `assistant_cc_transcript`
  (`blob` zstd, `:91` unique_together).
- Bundle builder: `chat_nextseek/src/chat_nextseek/artifacts.py:60` `build_metadata_bundle`.
- Standard append: `chat_nextseek/src/chat_nextseek/orchestrator.py:298`, `:774`.
- Planner append + canonical_memory_payload logic: `orchestrator.py:1490-1578` (note
  `graph_query` branch `:1532-1541` sets memory_payload only if still None; the standard
  path leaves graph `memory_payload = null`).
- Follow-up memory reader: `chat_nextseek/src/chat_nextseek/agents/memory.py:34,97`
  (reads `result_bundle["memory_payload"]`).
- Top-level NS/CC/Unrelated router: `nextseek_api/cc_assistant/router.py:189` `decide`,
  heuristic `:110`.
- Artifact bundle registration (report/nf-core): `nextseek_api/services/assistant.py:1245`.

**Representative console.txt line refs:**
- Thin-bundle followup empties: `260722_185413/console.txt:382-393` (memory_coder id=1),
  `260723_192223/console.txt:352-394` (species [], + MemoryCoder parse retry L392),
  `260723_192822/console.txt:354-402` (RNA-seq count 0).
- Graph LIMIT-250 cap: `260723_192822/console.txt:645-657`,
  `260723_193044/console.txt:238-250`, `260723_192601/console.txt:952-964`.
- ReporterPlan EOF ×3: `260723_192601/console.txt:268-273`.
- APIRequestPlan EOF ×3: `260723_193529/console.txt:577-604`.
- Cypher 0-records on explicit UIDs + repair `['SEQ']` dead-end:
  `260723_193529/console.txt:731-745, 887-978`.
- Empty cypher for aggregate-on-parent: `260722_195815/console.txt:500`.
- PASS anchors: global count `260722_180627/console.txt:229-238`; MUS+NDMA+age
  `260723_205301/console.txt:603-613`; refine 4-week→303 `260723_193529/console.txt:385-461`.

**DB oracles (live 2026-07-24):**
- Neo4j true counts: D.SEQ children DERIVED_FROM NHP = **1608**; NHP parents = **139**.
- advanced_search NHP total = 408 (all) / 303 ("4 week") / 2 ("4 week" explicit pair).
- MUS+NDMA total = 195. Global Sample count = 50,889.
- Sessions of interest: `cdc3927e…` (thin get_parents bundle), `9e249c0a…` (4-bundle
  advanced+graph mix, 1.5 MB), `8332ce61…` (graph 250-capped, memory_payload null),
  `f9ae0afd…` (advanced rich, 897 KB).
- Creds used (read-only): `docker exec seek-mysql mysql -uroot -p<REDACTED — see docker/db.env> dmac`;
  Neo4j via container env `NEO4J_PASSWORD`.
