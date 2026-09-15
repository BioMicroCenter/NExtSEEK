# graph_search follow-up 1: Nessie on the metadata graph (evidence POC)

- Date: 2026-09-15, revised three times the same day to the operator's rulings (section 3.1)
- Branch: `feat/graph-search-nessie`, cut from `feat/graph-search` at `4b3e087a`
- Status: draft for operator approval. **Nothing is built.** The branch holds only this spec and its plan. The first
  version of both said increment 1 (A0 to A4) was built; it was not.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Builds on: the POC spec `docs/superpowers/specs/2026-09-14-graph-search-poc-design.md` (sections 3 to 7 are the graph
  and endpoint this uses; section 11.1 was this follow-up's first scope) and `docs/neo4j-schema.md` ("v1.1").
- Plan: `docs/superpowers/plans/2026-09-15-graph-search-nessie.md`.
- The recon behind it (two stages, 2026-09-14) stays outside the repository because it cites local data. Numbers below
  are dated measurements from it or from the POC's runs; re-measure before relying on one.

## 1. Goal

Two questions, in the operator's words:

- **Group A, the contest:** "with the metadata in the graph, do Cypher queries outperform JSON advanced searches when
  an LLM turns the user's question into the query?" On metadata and advanced-search questions, does Nessie's graph
  agent (the LLM writes Cypher) give better and faster answers than its API agent (the LLM writes an `advanced_search`
  JSON body)? If it does, the API agent can be retired for metadata questions.
- **Group B, the check:** "does it still work for what we want?" On everything that normally routes to the graph
  (studies and investigations, traversal, lineage, the IMPACT and project questions, assays, protocols, publications),
  is the graph agent with its new v1.1 context still right? `advanced_search` cannot answer these, so they are not part
  of the contest.

The POC produces two things:

1. **Nessie's new graph schema.** The graph agent reads the v1.1 catalog live (cached on `GraphMeta.catalog_hash`) as
   compact text instead of a `keys(n) LIMIT 200` scan, and its property guard checks names per label.
2. **Evidence.** Single-turn questions go through the arms on the same code and the same data, every stage of every
   turn is checked against ground truth re-derived on the merged data, and pass rules fixed before any paid turn decide
   both answers.

The emphasis is the testing and the evidence, not routing.

Not the goal:
- Follow-ups, refinements and every multi-turn conversation; lineage questions that route to REST endpoints today;
  catalog and reporter questions. They move to section 8.2 with A8.
- Merging into `dev` or any other branch, or running anywhere but the operator's workstation.
- Scoping graph reads for non-admins (A1). The POC runs only as the superuser `demo`. A1 is its own stage after the
  POC, built with the merge work (section 8.1).
- Rewriting the BAML router; retiring `advanced_search` in chat_nextseek or container_cc; moving container_cc onto the
  graph agent (A9); A5, A6, A8 and A10.
- Changing `graph_sync`, `graph_search` or the catalog writer, or writing per-project statistics (follow-up 2).

## 2. What the recon found (short)

| Fact | Consequence |
|---|---|
| No Nessie graph path is scoped: `tool_neo4j_query` runs model Cypher with the service account, in the default (write) access mode | tolerable only while the POC runs as a superuser on the workstation; stage A1 closes it before any merge |
| The write-block regex scans unmasked text: it blocks the read-only `db.index.fulltext.queryNodes` and any literal containing SET or DELETE, and lets `apoc.refactor.*` through | apply it to masked text, allow the fulltext procedure, run every read in a READ transaction (kept in the POC) |
| Nessie's graph context is one `keys(n) LIMIT 200` scan written into the package `context/` directory, trusted by mtime, frozen per worker, sent whole (26.4 KB) on every graph call. On the metadata graph it scans 100+ labels, cuts Sample keys at 200 of 984, and the guard then rejects valid names | a live catalog reader that writes nothing (A2); every arm runs this branch's code |
| Variant (b) (structure, a type index, at most 3 resolved types at 25 attributes) is about 30.5 KB on the merged data; every type in full is 459 KB | render variant (b) as compact text (A3) |
| `known_node_properties` is a type-blind union and ignores backticked names | check names per label against the catalog (A4) |
| `graph_agent.txt` says Samples have "exactly three properties" and the graph "does NOT store metadata"; `min_graph_schema.json` says descriptive metadata is not on the node | the lines the graph agent needs change in the POC (A7 subset) |
| Every NS list answer is one `advanced_search` call with `page_size=1000`; the server materializes every match first (about 21.5 KB of worker memory per matched row) | the API arm can exhaust a capped worker on broad questions; the venue records that as an API-arm failure (section 6) |
| The corpus holds 66 single-turn metadata and search questions and 79 single-turn graph-routed questions (E4); the POC's ladder and benchmark add 35 more. The corpus's numeric oracles were set on the unmerged data | ground truth is re-derived on the merged data (1,084,754 samples) |
| The entity step's vocabulary is three committed JSON files: 17 of 118 merged types can never be emitted, and there is no TCGA project row. The local export fails with MySQL 1045 | a shared confound for every arm, flagged per question; A0 would not fix it (section 3.3, D11) |

## 3. Decisions

### 3.1 Operator rulings (2026-09-15)

First round:

| # | Ruling | Where it lands |
|---|---|---|
| R1 | The POC tests the claim in section 1, with emphasis on the testing and the evidence | sections 5 to 7; E7 |
| R2 | **A1 is programmatic clause injection.** Every LLM-written graph query gets the caller's project clause injected by the server, proven by an adversarial suite (path variables, `nodes()`/`relationships()`, variable-length interiors, UNION, CALL subqueries, OPTIONAL MATCH, WITH aliasing, collect/UNWIND, aggregations and counts, map projections, pattern comprehensions, EXISTS/COUNT subqueries, and any other shape found). Refusal is only the fallback for a shape the injector cannot prove safe; it is never "refuse non-admin LLM Cypher", which would cut non-admins off from lineage, assay, protocol and structure questions graph_search cannot answer. A1 is planned after the POC as its own stage with the merge work, not built in the POC | D1 to D5, D12, Q16, section 8.1, plan stage S |
| R3 | The POC runs only as the superuser `demo` on the operator's workstation. Kept in the POC: every Nessie graph read in a READ transaction, and the write regex on masked text with `db.index.fulltext.queryNodes` allowed | D3; plan task T3 |
| R4 | Routing, minimal effort: force `nextseek_query` with the existing admin `force_route`; the only new routing code exposes it for a normal run; inside NS one switch-gated deterministic guardrail forces the parser mode; no hand-written routing tables | E1 to E3 |
| R5 | In scope: A2, A3, A4, the A7 lines the graph agent needs, A0 only if the POC needs it, and the evidence harness. Out of scope: merging, the BAML router, retiring advanced_search, A9, and A5, A6, A8, A10 except a piece the harness needs | section 4; A0 is not needed (D11); no piece of A5, A6, A8 or A10 is needed |

Second round, with the operator's two corrections of the same day:

| # | Ruling | Where it lands |
|---|---|---|
| R6 | **Single-turn questions only**, in two groups. Group A: the metadata-like single-turn variants of `sample_search`, and of `harmonization` and `retrieval_path_selection` where a variant is a metadata or search question, plus the 25 ladder rungs and the 12 B2 shapes as natural-language questions. Group B: every single-turn corpus variant whose path today is the graph, across all families. `followup_over_results`, `search_refinement` and every multi-turn variant move to section 8.2 with A8 | E4; section 8.2 |
| R7 | Group A's pass rule has one stratum: the main rule (margin, McNemar, failure rate) and the latency and cost guards | E7 |
| R8 | Ground truth as designed (graph_search, Cypher or SQL, whichever is clearest; the second-engine cross-check). A graph-sync error is not a risk for this POC; nothing is added for it | E5 |
| R9 | Forcing is simplified to single-turn retrieval questions; the admin request field behind the environment flag stays | E2 |
| R10 | Four recommendations adopted as decided, revisitable later: the switch is the admin request field behind the environment flag (E2); the API arm is forced (E3); the truth's default reading (E5); the API arm's REST self-calls run inside the venue (section 6) | E2, E3, E5, section 6 |
| R11 | Stage B stays under 10 agents: the truth tooling joins the scorer in one task, and the docs are part of the gate B step | plan |
| R12 | **Group B is a "does it still work" check, not a second contest.** Its main result is the new graph agent's correctness on every graph-routed question against ground truth, per family, with every failure traced to its stage. An arm running the graph agent with today's context (the D7 fallback, forced by the evaluation switch) is **optional**, priced both ways | E1, E7, E10, section 7 |

### 3.2 The recon's questions for this follow-up

| # | Question | Decision |
|---|---|---|
| Q13 | Case variants and the long tail | Unchanged: resolve-time expansion in the entity step (A5, after the POC). The POC's ground truth states per question how case is read (E5). **Operator to confirm** |
| Q14 | `attributes[]` on the entity output | After the POC (A5). **Operator to confirm** |
| Q15 | Assay catalog and project aliases into the graph | Later. The POC adds no TCGA `projects_context` row (a write); a question that names TCGA as a project is flagged `entity_vocabulary_gap` (every arm shares the gap). **Operator to confirm** |
| Q16 | Free Cypher for non-admins, and Nessie's identity for graph reads | **Rewritten per R2.** Non-admins keep LLM-written Cypher, with their project clause injected by the server into every query (section 8.1) and proven by the adversarial suite; only a shape the injector cannot prove safe is refused, with an explanation. Every read runs as the per-request user's scope from MySQL membership (the POC resolver), never the service account's. The POC itself serves only the superuser |
| Q20 | K, meanings, zero-sample and deprecated types, the parser's view | K=25 attributes in full per resolved type, at most 3 types; meanings trimmed to their first clause (at most 120 characters); zero-sample types listed and flagged; deprecated types left out. The parser does not get the type index in the POC (routing is out of scope). **Operator to confirm** |
| Q21 | Follow-ups: re-query or aggregate held rows; page size | Later, with A8 (section 8.2); follow-ups are not in this POC |

### 3.3 Design decisions

Marked POC (built by this plan) or A1 stage (built after the POC, section 8.1).

| # | Stage | Decision | Why |
|---|---|---|---|
| D1 | A1 stage | The caller's scope is a `GraphScope {is_admin, project_ids}` set by the API host on the per-request config copy. The ViewSets resolve it from `request.user` with `nextseek_api/graph_search/scope.py::resolve_scope`; the engine never resolves it and never reads it from the model, the request body or Cypher parameters | one seam for every route; no new engine back-edge |
| D2 | A1 stage | Fail closed: no scope or an unresolvable caller is refused; a non-admin with no projects gets zero rows and no query | a missing scope must never mean "unscoped" |
| D3 | POC | Every Nessie graph read runs in a READ transaction (`session.execute_read`, or `execute_query` with READ routing) with a timeout, so the server refuses writes. The write check runs on masked text (literals, backticked names and comments blanked) and allows exactly one procedure, `db.index.fulltext.queryNodes`; `CALL { }` and `CALL (x) { }` subqueries are allowed and their bodies checked | server-enforced read-only; the fulltext search graph_search uses becomes legal |
| D4 | A1 stage | The injector (section 8.1) rewrites every LLM-written query before it runs; server-written Cypher (the reporter) binds the scope parameters itself through `run_scoped_read`, which refuses a statement that does not use them | scope by construction for every query, not by refusal |
| D5 | A1 stage | `entity_tree/lineage` is scoped after the query against MySQL `projects_samples` (a hidden anchor reads "Sample not found"; foreign nodes and their edges are dropped). `edges` and `edge_attributes` return type-level facts only and stay unscoped. The POC gives all three READ routing | the lineage read returns samples; the other two are catalog-level |
| D6 | POC | The catalog reader is lazy (nothing runs at `ChatConfig` construction), cached per process on `(NEO4J_URI, NEO4J_DATABASE)`, re-checks `GraphMeta.catalog_hash` at most every 60 s, and writes no file. Type details live 10 minutes; vocabulary (titles, assay connections) one hour, with no `LIMIT 300` | removes the boot scan, the mtime defect, the PROD collision and the package writes together |
| D7 | POC | Fallback: when the graph is unreachable, or has no `GraphMeta` with `schema_version` 1.1, Nessie uses the committed `context/neo4j_schema.json` and the old type-blind guard. A failed read is remembered for 60 s | a v1.0 graph keeps working; an outage costs one timeout a minute |
| D8 | POC | The structure section is hand-owned text in the package (`prompts/graph_schema_structure.txt`), kept consistent with `docs/neo4j-schema.md` v1.1 by a test | the runtime cannot read `docs/`; the doc stays the record |
| D9 | POC | Rendering is compact text with a hard 32,768-byte budget: when over, K drops (25, 15, 10, names only) before a resolved section is dropped | the budget holds whatever types resolve |
| D10 | A1 stage | Non-admin rendering carries no counts and no values unless per-project usage (`USED_IN`) exists, then the scoped form (the recon's Q2). The POC renders the admin form only | global statistics leak across projects |
| D11 | not built | **A0 is not needed by the POC.** The export's source tables lack the rows that matter (the 17 unreachable merged types have no context row; `projects_context` holds one stub), so a working export would produce the same vocabulary. Every arm shares the entity step, so the gap is a shared confound, flagged per question. A0 (per-source export status) stays a later increment | the POC changes nothing it cannot measure |
| D12 | A1 stage | The CLI and the MCP server supply no scope, so for non-admin use they are refused until an explicit operator scope exists. In the POC they run as today (admin) | fail closed; single-operator tools |
| D13 | POC | Whole-node returns are forbidden: the prompt says so, and the guard sends a query that returns or collects a bare Sample variable back for one repair, then refuses it (the same path as an unknown property) | otherwise the chatter, `results_history` and downloads ship every attribute to the model and into sessions |
| D14 | POC | In `min_graph_schema.json` the false claims change (metadata is on the node in v1.1); its routing preferences do not. Routing is out of scope, the switch overrides the parser's mode in every arm, and the parser's own choice is recorded on every turn (E2) | a factual fix without a routing change |
| D15 | POC | Every graph turn records which context the graph agent used, `catalog`, `legacy` or `fallback`, in `debug.graph_context` | the scorer checks that each arm ran the context it claims, and the pilot's stop rule can see the fallback |

### 3.4 Evidence decisions

| # | Decision | Why |
|---|---|---|
| E1 | **Arms.** Group A: G, the graph agent with the new context, against A, the API agent. Group B: G alone, or G against L, the graph agent with today's context (E10), when the operator chooses the optional arm. Every arm forces `nextseek_query` with `force_route: "ns"` (admin) and runs in the same venue container on the same snapshot of this branch against the same data, one question at a time, the arms of a question back to back; which arm goes first alternates by question index | interleaving removes time as a confounder (graph re-syncs, provider load); alternation cancels order effects |
| E2 | **The switch** (operator ruling R10). A new admin-only field on the chat request, `force_parser_mode` (`"graph"`, `"graph_legacy"` or `"api"`), is honoured only when the caller is a superuser **and** the process has `NEXTSEEK_EVAL_PARSER_FORCE=1` (the venue sets it; no compose file or env template does). The CC turn then hands the NS engine a shallow config copy carrying `FORCE_PARSER_MODE`, and `_apply_parser_guardrails` applies `_force_parser_mode` last, modelled on `_force_graph_for_uid_lineage`. Every question is a single turn in a fresh session, so the rule has two lines. **graph and graph_legacy:** `new_search` becomes `graph_query`. **api:** `graph_query` becomes `new_search` on the parser's first REST endpoint candidate, else `advanced_search`. (A `refine_last_search` on a fresh session is already turned into `new_search` by the existing refine guard, which runs first.) Any other mode is left as the parser chose; the scorer lists such questions. The note `forced to <mode> by the evaluation switch (parser chose <mode>)` is appended to `plan.notes`. `graph_legacy` also tells the graph agent to use today's context (E10) | minimal product code, off everywhere but the venue; the note records the unforced parser's choice (the status quo) on every turn |
| E3 | **The API arm is forced** (operator ruling R10). Unforced, the parser sends some Group A questions to the graph agent, which on this branch reads the v1.1 catalog, so arm A would answer part of the set with the graph agent and blur the comparison. The notes of E2 still say what the unforced parser would have done, at no extra cost | the claim compares the two agents, not two routers |
| E4 | **The questions: 180 single-turn questions in two groups.** The per-variant kept and excluded lists, with reasons, and the ladder and B2 questions live under `$GS_WORK/nessie/` (`selection.json`), never in the repository (one ladder value and several corpus variants name real people). Variant text is reused verbatim; the corpus's criteria are not (their oracles predate the merge). **Group A, 100 questions:** 66 from the corpus (57 of 66 `sample_search` variants, all 8 `harmonization`, 1 of 6 `retrieval_path_selection`), answerable from the sample type, metadata values, keywords, UIDs or lab codes; the 25 compat rungs of the external latency ladder; 9 of the 12 B2 shapes (the lineage shape goes to Group B, and two shapes whose bodies equal a ladder rung are merged into it). **Group B, 80 questions:** 79 corpus variants and the B2 lineage shape. A single-turn variant outside Group A joins Group B when (a) its criteria expect the graph path (parser mode `graph_query`, engine `graph_query`, `graph_cypher`, `neo4j_ok`, `graph_result.*`), (b) its family is `graph_traversal`, whose description names the graph, or (c) its question names what today's routing rules send to `graph_query` (a named study, investigation or project scope, a publication, an assay or protocol traversal, lineage structure) and its criteria expect no REST endpoint and no non-retrieval mode. Per family: `graph_traversal` 60, `lineage_tree` 7, `sample_search` 5 (the study, investigation and project-scoped ones), `project_summary_report` 4 (investigation inventories, not upload reports), `retrieval_path_selection` 1, `vocabulary_resolution` 1, `engine_routing` 1. **Excluded from both:** writes, launches, uploads and submissions; Container-CC variants; reporter-path questions (uploads over time, published-sample reports); catalog and system questions; questions with no referent in a single turn; exact duplicate texts; and the 31 lineage questions that route to REST endpoints today (13 single-UID trees through sample-tree, 18 parents-by-child-types), which are in section 8.2 | Group A is where both agents answer from the same metadata, the contest; Group B is where the graph agent is the path today, the check |
| E5 | **Ground truth**, re-derived on the merged data. Per question: the intended reading, the accepted answer (and any defensible alternate reading, marked as such), and the oracle that computes it: a graph_search body, a Cypher statement or a SQL SELECT, whichever is clearest, run read-only as the superuser against the same live stack the arms query, after the UID-fix re-sync. Every count flagged changed or interpretive, and at least one in five of the rest, is re-derived by a second engine where MySQL holds the same fact (samples, metadata, `projects_samples`, parent tokens); a graph-only fact (a paper-level Study, a DERIVED_FROM assay label) has one oracle, marked. A disagreement is resolved or the question is marked interpretive. Each truth file records a data fingerprint (sample count, `GraphMeta.catalog_hash`, `synced_at`). The ladder and B2 reuse their measured totals (the ladder after its UID rerun), re-checked by the same runner. A question whose answer cannot be established is excluded with a reason. **Default reading** (operator ruling R10): the question's own words decide; where a question leaves the spelling or case of a value open, the primary reading is case-insensitive on that attribute's value (what a user means by "lung"), and an exact-spelling answer is a marked alternate. Other alternates are declared per question, never by blanket rule. Alternates count as correct and are tallied separately, so every verdict can be read with and without them. Stored under `$GS_WORK/nessie/truth/`. The operator reviews the selection, changed oracles, alternates and exclusions before any paid turn (gate T) | the corpus's numbers were set on 166k samples; TCGA shifts some; interpretive questions (Organ Lung: 16,841 exact, 22,734 ignoring case, 46,981 by `LIKE`) need a declared reading |
| E6 | **Every stage is asserted** (table below). The first failing stage is the question's attribution; only the reply decides correctness | a wrong answer is traced to the step that produced it |
| E7 | **The pass rules** (below), for the operator to accept or change before P3 | "better" and "still works" are defined before the money is spent |
| E8 | **Cost.** NS turns report no cost today. chat_nextseek writes a per-call token ledger, `llm_calls.jsonl`, into every run root (one per chat session, hence one per question) and into `LOG_DIR`. The scorer prices it with an operator price table (`$GS_WORK/nessie/prices.json`: per model, input and output per million tokens, Bedrock rates for the Anthropic models). The run is strictly sequential, so attribution is exact | cost is one of Group A's scores |
| E9 | Nothing else about any arm changes. G: the graph agent with the rendered catalog, its standard-mode LIMIT and total probe. A: the API agent, `advanced_search` with `page_size=1000` and the OR-retry ladder. L: E10 | the comparison is the product as it would ship, with only the agent or its context switched |
| E10 | **Today's context, for the optional arm L.** `graph_legacy` runs the graph agent exactly as it was before this branch, on the same v1.1 graph: the committed `context/neo4j_schema.json`, protocol and assay JSON (the D7 fallback), the pre-branch prompt kept verbatim as `prompts/graph_agent_legacy.txt`, and the old type-blind guard (no whole-node guard). Both arms read in READ transactions, which changes no answer. It is not today's Nessie on today's graph: today's live schema fetch breaks on a metadata graph (section 2), and the v1.0 structure it describes is all still present in v1.1 except `CHILD_OF` | the closest runnable "today" on the same data |

**Stage assertions (E6):**

| Stage | Observed from | Checked against the truth |
|---|---|---|
| Route (precondition) | `route_decided` | `nextseek_query`, source `forced`. A question whose force did not land is void, not scored |
| Switch (precondition) | `debug.parser_plan.notes` | the force note is present and the forced mode is the arm's; a question whose parser chose a non-retrieval mode is listed and scored as the product behaved |
| Context (precondition, graph arms) | `debug.graph_context` (D15) | `catalog` for G, `legacy` for L. A `fallback` voids the question for that arm |
| Entities | `debug.entity_result` | the sample types the truth names are among the resolved codes |
| Parser | `debug.parser_plan` | the original mode (recorded), `filters.sampletype_code` and keywords |
| Engine request | graph arms: `debug.graph_plan.cypher`; A: `debug.api_plan` (`endpoint`, `requestBody`) | graph arms: a `T_` label or type filter per expected type, the expected attribute names or relationships, no whole-node return (G). A: the endpoint, `sampletype`, `attribute`, `filter_searchText` |
| Engine value | graph arms: the graph debug JSON the turn lists in `files` (`neo4j_output`, `data_preview`); A: the saved API result (`debug.raw_json_path`) | the engine's own total or aggregate |
| Reply | `query_complete.reply` | every required number (thousands separators tolerated) or every required item, and no contradicting total for the same quantity |

**Pass rules (E7), proposed:**

For every arm, a question is **correct** when its final reply states the ground-truth answer, by the primary reading or
a marked alternate (alternates tallied separately), and **failed** on an error, a timeout, an OOM-killed worker, a
guard refusal or no answer. A provider outage (the harness's existing outage marker) is neither: that question is
rerun. Every verdict is also shown without alternates, and the stage attribution is reported in every case.

- **Group A (G against A).** Main rule: G's correct rate exceeds A's by at least 15 percentage points; an exact
  two-sided McNemar test over the discordant pairs gives p < 0.05; and G's failure rate is at most A's plus 2 points.
  Guards: G's median wall time per turn is at most 1.25 times A's, and G's median cost per question at most 1.5 times
  A's. Verdict: SUPPORTED when the main rule and both guards hold; SUPPORTED WITH COSTS when the main rule holds and a
  guard fails; NOT SUPPORTED otherwise.
- **Group B, option 1 (G alone): an absolute pass rate.** STILL WORKS when G is correct on at least 80% of the scorable
  questions, no family with at least 5 questions falls below 60%, and at most 5% of questions fail outright. It tells
  how well the new graph agent answers graph questions; it cannot say whether the new context helped or hurt.
- **Group B, option 2 (G against L): no worse than today.** NO WORSE when G's correct rate is at least L's minus 5
  points and the discordant pairs do not favour L significantly (an exact two-sided McNemar test with L ahead at
  p < 0.05 fails the rule); option 1's figures are reported beside it. It attributes any change to the context, at the
  price of one more turn per question.

## 4. The POC build (product code)

Paths under `NessieAI/chat_nextseek/src/chat_nextseek/` are written short.

### 4.1 A2: the catalog reader

New module `graph_catalog.py`. Read-only queries, each in `execute_read` with a timeout:

| Name | Reads | Used for |
|---|---|---|
| `META` | `GraphMeta {schema_version, catalog_hash, synced_at}` and whether any `USED_IN` edge exists | validity, the cache key, D10 later |
| `INDEX` | every SampleType: `title`, `label`, `name`, `clade`, `sample_count`, `deprecated`, attributes with values | the type index |
| `GUARD` | per SampleType `label`, the titles of its attributes with values | A4 |
| `TYPES_ADMIN` | the resolved types in full (the recon's Q1): attribute `title`, `value_type`, `declared`, `needs_backticks`, `sample_count`, `meaning`, `unit_key`, `role`, and `top_values`, `top_counts`, `num_*`, `date_*` when present | the resolved sections |
| vocabulary | Investigation, Project and Study titles, published studies, DERIVED_FROM assay and protocol titles, assay connections | keyword-gated blocks |

`ChatConfig` loses `_fetch_neo4j_schema`, `_ensure_neo4j_schema`, `_ensure_schema_file`,
`_fetch_assay_sample_connections`, `_ensure_assay_sample_connections`, `_fetch_protocol_schema` and
`_ensure_protocol_schema`. `NEO4J_SCHEMA`, `PROTOCOL_SCHEMA` and `ASSAY_SAMPLE_CONNECTIONS` become the committed JSON,
read only, as the fallback and as today's context for arm L (the parser and the old guard keep reading them
unchanged). `get_config_snapshot` reports the catalog cache state without a network call.
`NessieAI/chat_nextseek/mcp_server.py` serves `neo4j-schema` as the rendering when the catalog is live.

Accept: constructing a `ChatConfig` and running a graph turn writes nothing under `context/`; a config whose
`NEO4J_URI` differs reads its own graph; a graph that is down or v1.0 yields the committed JSON.

### 4.2 A3: the renderer

New module `graph_context.py` (pure) and `prompts/graph_schema_structure.txt`. The text has three parts:

1. **Structure** (about 3 KB): labels, relationships, system properties and the ten rules of the recon's draft,
   corrected to `docs/neo4j-schema.md` v1.1 (properties the doc lists, `CHILD_OF` only as "does not exist").
2. **Type index**, one line per non-deprecated type: `TIS :T_TIS "Tissue Sample" clade Source, 107,412 samples,
   41 attributes with values`; `no samples` flags a zero-sample type.
3. **Resolved types**, at most 3 (codes from the parser plan's `resolved` and `filters.sampletype_code`, else the
   entity output), each a header, the first sentence of its summary, curated parents and children, the K most-filled
   attributes in full (`- Organ [string] n=16,841 | values: "Lung" 16,841, "lung" 5,893 | <meaning>`), one
   `also filled:` line of names, and the count of declared attributes that hold no value. Values render only when the
   catalog carries them (follow-up 2 adds `top_values`).

Used by `agents/graph.py::graph_agent` (replacing `json.dumps(NEO4J_SCHEMA, indent=2)`) and
`agents/system.py::system_agent`. Vocabulary goes in its own blocks: investigation and project titles always, study
titles and published studies when the question names a study or paper, assay titles and connections on the existing
assay words, protocol titles on the existing protocol words.

Accept: the three largest merged-shape types render in at most 32,768 bytes; no attribute appears that the catalog
does not list for that type; the rendering works with no `USED_IN`, no `top_values` and no meanings.

### 4.3 A4: the property guard, whole-node returns, and the context modes

`agents/graph.py` gains a catalog guard. A variable's labels come from its patterns (`(s:Sample:T_TIS)`) and label
predicates (`WHERE s:T_TIS`). A `T_X` variable may read the Sample system properties and the attributes of X with
values; a plain `Sample` variable the union over all types; other labels and relationship types their v1.1 property
sets; a variable of unknown label is checked against everything. Backticked names (`` s.`Catalog#` ``), map
projections (`s {.Organ}`) and unknown `T_` labels are checked; function and procedure names are not properties.
`RETURN s` or `collect(s)` over a Sample variable is a whole-node return (D13). The existing repair loop reports each
problem once (`TIS.Sequencer`, `whole node s`), then the turn gets the empty plan. With the catalog unavailable, the
old `known_node_properties` union from the committed JSON applies.

`graph_agent` runs in one of three context modes and records it (D15): `catalog` (the rendering, the catalog guard),
`fallback` (D7, when the catalog is unavailable), and `legacy` (E10, only when the switch asks for `graph_legacy`: the
committed JSON, `prompts/graph_agent_legacy.txt`, the old guard).

Accept: `MATCH (s:T_TIS) WHERE s.Sequencer = 'x'` is rejected when TIS has no `Sequencer` and passes on `s:T_D_SEQ`
when D.SEQ has it; `MATCH (s:T_TIS) RETURN s LIMIT 5` is repaired once, then refused; a `graph_legacy` turn sends the
legacy prompt and JSON even when the catalog is live, and records `legacy`.

### 4.4 A7 subset: the prompt lines

`prompts/graph_agent.txt`: the section "What the graph stores" (Samples carry "exactly three properties"; the graph
"does NOT store" metadata) is rewritten to v1.1 (metadata on the node, per-type labels, exact matching, the rendered
structure is the schema); the two lines that send names to "the REST API" are corrected; a rule forbids whole-node
returns (return `s.id`, `s.uuid`, `s.type` and named properties; count with `count(*)`). The pre-branch text is kept
verbatim as `prompts/graph_agent_legacy.txt`, read only in legacy mode. `context/min_graph_schema.json`: the Sample
description and the reason clause of the descriptive-attribute rule stop claiming metadata is absent (D14). Neither
file gains routing text.

Accept: no line in either live file says Samples carry no metadata; the legacy file equals the base commit's
`graph_agent.txt` byte for byte; the parser file still parses; the Container-CC context drift guard stays green (the
plugin keeps its own copy of `min_graph_schema.json`, out of scope with A9).

### 4.5 Read-only on every Nessie graph path (D3)

New module `cypher_text.py` (`mask_cypher`, moved from `agents/graph.py::_mask_cypher`, which becomes an alias, and
`write_clause`). `helpers/tools/neo4j.py::tool_neo4j_query` keeps its signature (the portable contract), checks the
masked text, and runs the query and its total probe in `execute_read` with a 60 s timeout. The NS graph turn, the
planner graph tool, the CC `graph` op (`NessieAI/ns/granular.py::_graph`) and the reporter all read through it. The
catalog reader reads in READ transactions. `nextseek_api/services/entity_tree.py` passes READ routing on its three
`execute_query` calls.

Accept: a write sent through `tool_neo4j_query` is refused by the check and, independently, would run in a READ
transaction; a fulltext call and a literal `'Data Set'` pass the check.

### 4.6 The switch (E2)

`agents/parser.py`: `_force_parser_mode(plan, force_mode)` runs last inside `_apply_parser_guardrails`;
`parser_agent` passes `getattr(config, "FORCE_PARSER_MODE", None)`. The planner's multi-parser path ignores the switch
(the harness drives standard mode). `nextseek_api/assistant/models_api.py::QueryRequest` gains
`force_parser_mode: Optional[Literal["graph", "graph_legacy", "api"]]`, documented as admin-only and evaluation-only.
`NessieAI/cc/turn.py::start_task` wraps the chosen config with `_with_parser_force(chat_config, request.user, req)`,
which returns the same object unless the caller is a superuser, `NEXTSEEK_EVAL_PARSER_FORCE` is `1` and the value is
valid, and otherwise a shallow copy carrying `FORCE_PARSER_MODE` (the shared singleton is never mutated, and the PROD
identity check still compares the singleton). `graph_agent` reads the same attribute to choose legacy mode.

Accept: with the switch off the parser's plan is returned unchanged, object for object; a non-superuser's value is
ignored; every arm's mapping matches E2.

## 5. The evidence harness

Reuse, not a new harness: the `nessie_tests` corpus and `--cases` files, `runner.run_case` (which already takes
`force_route` and strips the route criteria under forcing), `http_driver.drive`, the preflight, `route_observer`,
`evaluate` with the e2e criteria DSL, `report.generate_html`, the manifests, and the `nessie-run-review` output skill
for triage. HiBayes is not used for scoring: its paired export is built around the NS and CC routes; every arm here is
NS. The new code is small and listed in the plan (tasks T6 to T8).

| Piece | Where | What |
|---|---|---|
| Force a normal run | `cli.py`, `manage.py nessie`, `runner.run_suite` | `--force-route {ns,cc}` and `--force-parser-mode {graph,graph_legacy,api}` for a normal run (today only `--bayesian` forces) |
| Arms | `runner.run_arms`, `manage.py nessie --arms graph,api` (Group A), `--arms graph` or `--arms graph,graph_legacy` (Group B) | per question, every arm back to back (alternating first arm), one manifest and HTML report per arm, `arms.json`, every turn's full `query_complete` payload saved, `--resume`, `--max-turns` |
| Preflight | `preflight.assert_parser_force_works` | one forced turn per arm proves the switch landed (the force note, the mode, the graph context), after the existing `assert_force_route_works` |
| Ground truth | `engine_truth.py` (models), `scripts/derive_truth.py` (oracle runner, read-only, inside the venue) | E5; truth files under `$GS_WORK/nessie/truth/` |
| Cases | `scripts/build_engine_cases.py` | one catalog-shaped `--cases` file per group from the truth files: one inline variant per question, engine-neutral criteria only (reply numbers, entity codes, `outcome_observed`), so every arm is judged by the same criteria; the arm-specific stages are the scorer's |
| Scoring | `engine_compare.py` | reads the arm manifests, the saved payloads, the venue outputs (graph debug JSON, API results, `llm_calls.jsonl`) and the truth; writes per-stage verdicts, per-arm scores per family, the group's pass-rule verdict, `compare.json`, `compare.md` and `questions.csv` |

Flow: build (gate B), venue up and checked (gate V), truth derived and signed (gate T), preflight, pilot (gate P),
full runs in blocks, scoring, triage, the operator's verdicts.

## 6. The test venue

A throwaway app container on the workstation, never a merge into the live stack.

| Aspect | Choice |
|---|---|
| Image | `nextseek-nextseek:latest`, the image the live stack runs (rebuilt on 2026-09-15; V1 records its id). The venue runs this branch's snapshot on it, so the image only supplies the virtualenv |
| Code | a snapshot of this branch's HEAD (`git archive`) under `$GS_WORK/nessie/venue/src`, mounted read-only at `/src`, with `PYTHONPATH=/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src` ahead of the image's editable installs; working directory `/src`. A snapshot, so an edit during a run cannot change the code under test, and its sha is recorded |
| Settings | `dmac/local_settings.py` rendered into the snapshot from `startup/templates/local_settings.py.template` (the chat config comes from the environment; no PROD config), with only `ASSISTANT_PARTICIPATING_PROJECTS` taken from the operator's settings. The operator's own `local_settings.py` is never copied |
| Environment | `--env-file` for `docker/db.env` and `docker/nextseek.env` of the live checkout (credentials, never printed), then overrides: `DJANGO_ALLOWED_HOSTS="127.0.0.1 localhost"`; `NEXTSEEK_INTERNAL_BASE_URL` and `NEXTSEEK_BASE_URL` set to `http://127.0.0.1:8000`, so the API agent's REST self-calls stay in the venue (operator ruling R10: every arm in one container); `LOG_DIR=/venue/logs`; `NEXTSEEK_OUTPUTS_DIR=/venue/outputs`; `NEXTSEEK_EVAL_PARSER_FORCE=1`; `NEXTSEEK_POSTERIOR_ROUTING_ENABLED=0`; `PYTHONDONTWRITEBYTECODE=1`. The check step asserts each override by comparison, never by printing |
| Network | `nextseek_default` (reaches the stack's MySQL and Neo4j by service name, and the internet for the model providers); published on `127.0.0.1:8010`. No Docker socket (no CC container can start), no Luria key, no Celery, no `migrate`, no `collectstatic` |
| Process | `/app/.venv/bin/gunicorn dmac.wsgi --bind 0.0.0.0:8000 --workers 2 --threads 4 --worker-class gthread --timeout 1200`. WSGI: the harness polls, no websocket is needed |
| Memory | `--memory 6g --memory-swap 6g`; the up step refuses while `MemAvailable` is under 8 GiB or the benchmark flag exists. A broad `advanced_search` grows a worker by about 21.5 KB per matched row: an OOM-killed worker is an A-arm failure (read from the gunicorn log and `docker inspect`), and the broadest questions run in the last block |
| Models | the NS engine calls Gemini (entity, api, graph, chatter, system agents) with `GCP_API_KEY`, and the Anthropic models through Bedrock directly with `AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION` (parser: Opus 4.7 with high thinking), per `agent_model_catalog.json`'s default profile. The CC route is never used |
| What it writes | chat, session, task and routing-ledger rows into the live `dmac` database (`ChatSession`, `QueryTask` and the related assistant tables), and files under `$GS_WORK/nessie/venue/` (`outputs/`, `logs/`) and `$GS_WORK/nessie/runs/`. Nothing else: no SEEK write, no graph write, no migration |
| Harness | runs inside the venue (`manage.py nessie`), because the full tier needs the same Django process and database (`NessieAI/tests/nessie_tests/README.md` "Two entry points") |
| Bench window | the venue starts only while `$GS_WORK/.gs-bench-running` is absent, and a paid block starts only then |

## 7. Paid runs (operator)

Every paid step is run or approved by the operator, one approval per step. One turn per question per arm: Group A is
200 NS turns (100 questions, two arms); Group B is 80 NS turns without the optional arm L, 160 with it.

| Step | What | NS turns (B without L / with L) | Estimate | Stop rule |
|---|---|---:|---:|---|
| P1 preflight | `assert_force_route_works` plus one parser-force probe per arm | 3 / 4 | under $1 | any probe refused |
| P2 pilot | Group A: 10 questions (5 corpus search, 2 harmonization, 2 ladder, 1 B2), two arms; Group B: 6 questions (3 `graph_traversal`, 1 `lineage_tree`, 1 study-scoped, 1 investigation inventory) | 26 / 32 | about $4 / $5 | more than 2 infrastructure errors, any OOM, any `fallback` context, a stage the scorer cannot observe, or more than $0.30 per turn |
| P3 full | the other 90 Group A and 74 Group B questions, in blocks of about 60 turns, the broadest last | 254 / 328 | about $38 / $49, cap $60 / $75 | per block: more than 10% infrastructure errors, a provider outage, the data fingerprint changed, the benchmark flag present, or the cap reached |
| P4 repeats (optional, Group A) | 40 Group A questions, two more runs per arm, for within-arm variance | 160 | about $24, cap $35 | as P3 |

Totals for P1 to P3: about $43 without arm L, about $55 with it; about $67 and $79 with P4.

Basis: the local token ledger (`llm_calls.jsonl`, 45 NS turns, 2026-09-11 to 15) averages, per call, 15.5k input and
311 output tokens for the parser (Opus 4.7), 34.2k input for the entity agent, 3.1k for the API agent and 7.8k for the
graph agent (all Gemini 3.5 Flash; the new context adds about 7.6k tokens). At first-party list prices (Opus 4.7 $5 in,
$25 out per million) the parser is about $0.085 per turn. Gemini is assumed at $0.30 and $2.50 per million. Single
turns need no memory call. Planning figure $0.15 per turn, ceiling $0.20. Bedrock rates can differ, so the operator's
price table decides. Time: 20 to 40 s per turn, about 1.5 to 3.5 hours for P3 in sequence.

## 8. After the POC

### 8.1 Stage A1: server-injected scope (with the merge work)

Built before this branch merges anywhere, as its own stage (plan stage S). D1, D2, D4, D5, D10 and D12 apply.

**The injector** (new module `cypher_scope.py`, used by `tool_neo4j_query` for every non-admin query): it parses the
LLM's Cypher with the clause scanner (masking, clause splitting, bound variables), finds every variable that can bind
a Sample, and injects `visible(v)`, meaning `v:Sample AND any(p IN v.project_ids WHERE p IN $__scope_projects)`, at the
binding site, before any `WITH`. `OrphanSample` nodes are invisible to non-admins. Parameters named `__scope*` from the
model are refused; the server binds them.

| Shape | Handling |
|---|---|
| a node pattern with `Sample` or a `T_` label, an unlabelled node, or an endpoint of `DERIVED_FROM`, `OF_TYPE`, `IN_STUDY`, `IN_PROJECT` | the predicate joins that `MATCH`'s `WHERE` (created when absent); an unlabelled node gets `(NOT v:Sample OR visible(v))` |
| `OPTIONAL MATCH` | the predicate goes inside the optional pattern's `WHERE`, so a hidden sample becomes a null, not a dropped row |
| path variables, `nodes(p)`, `relationships(p)` | `all(n IN nodes(p) WHERE NOT n:Sample OR visible(n))` on the path |
| variable-length relationships | rewritten to a named path, then the all-nodes predicate (interiors checked) |
| `UNION`, `UNION ALL` | each branch injected independently |
| `CALL { }`, `CALL (x) { }` | injected recursively; imported variables are already scoped |
| `EXISTS { }`, `COUNT { }`, `COLLECT { }`, pattern comprehensions | the predicate goes inside the inner pattern |
| `WITH` aliasing, `collect`/`UNWIND`, aggregations and counts, map projections | safe once every binding site is scoped: rows are filtered before aggregation, so counts come out scoped |
| `CALL db.index.fulltext.queryNodes(...) YIELD node` | the predicate on the yielded variable |
| catalog statistics (`Attribute.sample_count`, `top_values`, `top_counts`, `SampleType.sample_count`) and `Person`/`MEMBER_OF` reads | refused for non-admins until the per-project `USED_IN` form exists (follow-up 2) |
| anything the scanner cannot classify: a label expression it does not model, a dynamic label, a procedure other than the fulltext one, a variable whose binding it cannot find, a `$__scope` mention | refused with an explanation (the fallback) |

**The proof.** An adversarial suite runs against a throwaway Neo4j holding a two-project fixture graph with orphans.
For every shape above (and each new one found), the scoped result must equal the same query run as an admin over a
copy of the graph from which the caller's invisible Samples were removed (a differential oracle), and an admin's result
must be unchanged. A refused query passes only when its shape is on the allowed-refusal list. Group B's questions are
a ready source of real graph-agent Cypher for the suite.

**Also in the stage:** the host seam (`nextseek_api/assistant/graph_scope.py`, attached in `AssistantViewSet.query`,
`query_async` and `_granular_chat_config`, `CCAssistantViewSet._start_task` through `start_task`, and the evaluator
retry), `run_scoped_read` for the reporter's two fixed reads, the lineage scope (D5), non-admin rendering (D10), and
the CLI and MCP refusal (D12). Gate: the suite passes, and a non-member sees zero foreign rows on every Nessie graph
path.

### 8.2 Later: the rest of the corpus, and A8

Moved out of this POC by ruling R6, to be taken up together:

- **Follow-ups and refinements:** `followup_over_results` (33 variants) and `search_refinement` (17 variants), with
  **A8** (follow-ups re-query the stored predicate when `total` exceeds the rows held, and disclose partial coverage;
  a graph bundle holds a 20-row preview today) and Q21.
- **Every multi-turn variant**, including `path.actually_hang_on_find_me_the_mic`.
- **Lineage that routes to REST today:** 13 single-UID tree questions (sample-tree) and 18 parents-by-child-types
  questions. They compare the graph agent with REST endpoints other than `advanced_search`, a different claim; 31 more
  questions at about $5 per arm if the operator wants them in Group B.
- **Catalog, system and reporter questions** that touch the graph only through fixed queries or not at all.
- The POC's harness, truth tooling and scorer carry over unchanged: a later run adds questions, groups and multi-turn
  truth.

### 8.3 Later increments

- **A0:** the context export reports success per source (table, rows, seconds, error), each source with its own day
  marker, visible on the config snapshot and logged through `logging`.
- **A5:** typed entities: additive `attributes[{sample_type, key, op, value, source}]` and `project_ids`, validated
  against the catalog with spelling expansion; graph SampleTypes as the type source (the 17 unreachable types
  resolve). Needs request R2.
- **A6:** attribute filters to graph_search; the 15 `advanced_search` literals in 6 files folded into one check.
  Retiring `advanced_search` in NS, if Group A's verdict supports it, starts here.
- **A7 (rest):** the parser and planner prompts, `capabilities.md`, and the parser's view of the type index.
- **A9:** a Container-CC `nextseek-graph-schema` op (via `/add-cc-op`) serving the same rendering; the plugin's baked
  schema files go.
- **A10:** the corpus: rewrite the 56 `api_plan.endpoint` criteria, add full-result facet and attribute-conditioned
  variants; the POC's truth files are the starting point.
- **Statistics and sensitivity:** render `USED_IN` statistics for non-admins once follow-up 2 writes them; never render
  the values of a `sensitive` attribute.

## 9. Requests to follow-up 2 and the POC

| # | To | Request |
|---|---|---|
| R1 | follow-up 2 | Stamp statistics freshness on `GraphMeta` (for example `stats_computed_at`), or fold statistics into `catalog_hash`. Today the hash covers structure only, so the type-detail cache also expires by age (10 minutes) |
| R2 | follow-up 2 | For A5: a distinct-value list per non-sensitive (type, attribute) pair with at most 200 distinct values, and `top_values`/`top_counts` per `USED_IN` edge after the `sensitive` filter and the email scrub |
| R3 | follow-up 2 | A `sensitive` flag on Attribute; Nessie never renders the values of one |
| R4 | POC | For stage A1: keep `nextseek_api/graph_search/scope.py::resolve_scope(user) -> Scope` and `ScopeUnavailable` stable |
| R5 | follow-up 2 | Keep `SampleType.deprecated`, `sample_count`, `Attribute.sample_count`, `meaning`, `role`, `unit_key` and `needs_backticks` as `docs/neo4j-schema.md` v1.1 lists them; the renderer reads them by name |
| R6 | follow-up 2 and the operator | Announce any live-graph re-sync between gate T and the end of P3; the runs check the data fingerprint per block and void a block whose fingerprint changed |

## 10. Testing

- Build tasks: the Django lane in a throwaway container over a read-only mount with the checkout's engine source first
  on `PYTHONPATH` (catalog, renderer, guard, context modes, prompts, read-only, switch; fake drivers, no network); the
  ns/api variant over a writable copy for the request model and the CC turn gate; the host lane for the harness, truth
  tooling, builder and scorer; the Container-CC hermetic lane for the context drift guard; `ci/docs_map.py` and the
  ViewSet conventions validator.
- The venue check (free): the catalog is live (not the fallback), the renderings fit the budget, graph reads run READ,
  the overrides took effect.
- Ground truth (free, read-only reads of the live stack): every oracle runs; counts are cross-checked by a second
  engine as E5 says.
- Paid: P1 to P4, each approved.
- Not verified before the venue runs: the catalog queries' cost on the live graph (the assay-title and connection
  reads scan about 2M DERIVED_FROM edges, lazily, once an hour); `-e` overriding `--env-file`; per-worker memory at
  rest; running the image's virtualenv under a non-root user.

## 11. Risks

- **Branch safety.** This branch has no A1. It must not be deployed where a non-superuser can reach Nessie. The venue
  serves `demo` only.
- **Ground truth decides the verdicts.** Interpretive questions (exact against case-insensitive against `LIKE`) need
  declared readings, and several Group B facts live only in the graph (one oracle); gate T exists for this.
- **Sample size.** 100 paired Group A questions: a 15-point margin is detectable only when the discordant pairs are
  lopsided; the report gives the discordant counts and the McNemar p-value, and P4 measures within-arm variance.
  Group B's per-family floors apply only to families with at least 5 questions.
- **Memory.** `advanced_search` on broad questions can OOM a capped worker; the host has about 7 GiB free and swap is
  full.
- **Shared confound.** The stale entity vocabulary (17 unreachable types, no TCGA project row) affects every arm; it is
  flagged per question, not hidden.
- **The live graph moves.** A re-sync mid-run changes answers; the fingerprint check voids the block.
- **Arm L is an approximation of today.** It is today's graph agent on the new graph, not today's Nessie on today's
  graph (E10).
- **Writes into the live `dmac` database.** Chat, session, task and ledger rows accumulate for `demo`; they are
  harmless and listed in section 6.
- **Cost estimate.** Bedrock and Gemini prices are the operator's to confirm; the caps stop a run, not a single turn.
- **Parser routing.** The `min_graph_schema.json` edit changes the unforced parser's input on this branch; the evidence
  records the parser's choice but does not measure routing.

## 12. Decisions: taken and open

Taken by the operator on 2026-09-15 (revisitable later):

1. The scope: single-turn questions in two groups, Group A the contest and Group B the check (R6, R12); the rest is in
   section 8.2.
2. Group A's pass rule has one stratum: the main rule plus the latency and cost guards (R7); the thresholds are still
   open (item 10 below).
3. Ground truth as designed; a graph-sync error is not treated as a risk (R8).
4. The switch: the admin request field behind `NEXTSEEK_EVAL_PARSER_FORCE=1` (E2, R10).
5. The API arm is forced (E3, R10).
6. The truth's default reading (E5, R10).
7. The API arm's REST self-calls run inside the venue (section 6, R10).
8. Group B's arm L (today's context) is optional (R12).
9. A1 is clause injection, after the POC (R2); READ transactions and the masked write check stay in the POC (R3).

Still open:

10. Group A's thresholds (E7): 15 points, p < 0.05, failure rate plus 2 points, latency 1.25 times, cost 1.5 times;
    accept or change before P3.
11. Group B: run arm L or not (option 2 costs about $12 more and buys "no worse than today"), and the chosen option's
    thresholds (option 1: 80% overall, 60% per family of 5 or more, at most 5% outright failures; option 2: within 5
    points of L and no significant L advantage).
12. The selection (E4): the kept and excluded lists, the exclusions found while deriving the truth, and whether the 31
    REST-routed lineage questions join Group B; reviewed at gate T.
13. The venue's memory cap (6 GiB) and memory floor (8 GiB), and whether other workloads stop during P3.
14. The price table and the caps (P3 $60 without arm L or $75 with it; P4 $35).
15. Each paid step, P1 to P4, approved separately.
16. No live-graph re-sync from gate T to the end of P3, or a truth re-derivation after one.
17. Q13 to Q16 and Q20 as recommended; A0 deferred (D11).
18. When stage A1 runs (with the merge), and whether follow-up 2's `USED_IN` statistics come first.
19. Q17 (point the local `MYSQL_PROD_PASSWORD` at local credentials): not needed by the POC.
