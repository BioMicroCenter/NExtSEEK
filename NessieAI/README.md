# NessieAI/

All of Nessie, the NExtSEEK chat assistant, except its API surface. The HTTP routes, the ORM
models and the migrations stay in `nextseek_api/` (see "What stays in nextseek_api" below).

`NessieAI/` is a plain Python package (`NessieAI/__init__.py`, empty), importable from the repo root
in every process that imports `nextseek_api`. It is not an installed dist and not a Django app.
Cross-unit paths go through `NessieAI/paths.py`. Agent rules that span units are in `NessieAI/CLAUDE.md`.

## What Nessie is

The router-dispatched chat endpoint is `POST /nextseek_api/cc-assistant/query/async/` (admins also have `.../cc-assistant/cc/query/async/`). Per turn, a router picks one of three routes:

| Route | What answers |
|---|---|
| `nextseek_query` | the NS engine: a deterministic multi-agent pipeline run inside Django |
| `container_cc` | the CC engine: a sandboxed Claude Code agent in a fresh container per turn |
| `unrelated` | nobody: a fixed out-of-scope reply |

Both engines write the same `QueryTask` rows and stream over one websocket, so the chat panel has no per-route code.

## One turn, end to end

| Step | Owner |
|---|---|
| 1. The chat panel posts the turn; its bundle is served from `static/js/chat_assistant/` | `NessieAI/chat_frontend/` |
| 2. `CCAssistantViewSet` checks auth, creates the task row and hands the turn to `start_task`, whose thread applies the overrides (`force_route`, `pipeline_agent`, follow-ups and sticky CC) | `nextseek_api/services/cc_assistant.py` (stays), `NessieAI/cc/turn.py`, `NessieAI/router/policy.py` |
| 3. The router picks the route, using BAML prompts and the model map | `NessieAI/router/`, `NessieAI/dmac_assistant/` |
| 4a. NS turn: the chat_nextseek agents run in-process inside Django | `NessieAI/chat_nextseek/` |
| 4b. CC turn: one agent container per turn; the model through the Bedrock proxy; ops through the sidecar, which calls the granular ops and their write gate | `NessieAI/cc/`, `NessieAI/docker/`, `NessieAI/ns/` |
| 5. Progress lands in `QueryTask` rows and streams on `ws/assistant/progress/{task_id}/` | `nextseek_api/assistant/` (stays) |

## Folders

<!-- BEGIN DOCS-MAP:folders -->
| Folder | What it does |
|---|---|
| `chat_nextseek/` | deterministic NS engine: agents, prompts, planner, Luria launch, reports, context catalogs |
| `dmac_assistant/` | canonical BAML prompts (router, classifier, CC summarize, HiBayes judges) and the router model map |
| `router/` | which engine answers a turn, including posterior routing and routing telemetry |
| `hibayes/` | Bayesian router evaluation: judge, fit, generation store, paired export |
| `cc/` | Container Claude Code engine and its op registry (`cc/op_registry/`) |
| `ns/` | native granular ops, the write gate, and NS bundle projections inside Django |
| `schema_rag/` | OpenAPI retrieval for agents |
| `build_tools/` | generators for CC surfaces and `route_capabilities.json` |
| `chat_frontend/` | React chat panel, with its own vitest and Playwright tests |
| `docker/` | the four AI images: `cc-runtime`, `bedrock-proxy`, `ns-sidecar`, `eval` |
| `tests/` | every Nessie Python test, mirrored by area |
| `docs/` | live Nessie docs |
| `history/` | frozen records: never edited, never collected, not in the image |
<!-- END DOCS-MAP:folders -->

## To change X, edit Y

| To change | Edit | Tests |
|---|---|---|
| Routing precedence (`force_route`, `pipeline_agent`, follow-ups and sticky CC) | `_decide_route` in `NessieAI/router/policy.py` | `NessieAI/tests/router/` |
| Router strategies, fallbacks, telemetry | `NessieAI/router/` | `NessieAI/tests/router/` |
| Classifier labels | `NessieAI/dmac_assistant/baml_src/classifier.baml`; family names come from `NessieAI/tests/nessie_tests/corpus.json` | `NessieAI/tests/router/` |
| Posterior routing (off by default) | `NessieAI/router/posterior_selector.py`, `NessieAI/hibayes/` | `NessieAI/tests/router/`, `NessieAI/tests/hibayes/` |
| The router model id | `NessieAI/dmac_assistant/build_context/router_model_class_map.json` only | `NessieAI/tests/router/` |
| A Container-CC op | follow `/add-cc-op` (`.claude/skills/add-cc-op/SKILL.md`) | `NessieAI/tests/cc/` |
| An NS agent, prompt or catalog | `NessieAI/chat_nextseek/` | `NessieAI/tests/chat_nextseek/` |
| The write gate or a granular op | `NessieAI/ns/` | `NessieAI/tests/ns/` |
| The chat UI | `NessieAI/chat_frontend/`, then commit the rebuilt bundle | in-package vitest |
| A judge schema | two files together: `NessieAI/dmac_assistant/baml_src/functional_evaluator.baml` and `NessieAI/hibayes/judge_models.py` | `NessieAI/tests/hibayes/` |
| What the CC agent is told | `NessieAI/docker/cc-runtime/container/CLAUDE.md` outside its marked blocks; regenerate the blocks | `NessieAI/tests/cc/` |
| Generated CC surfaces or `route_capabilities.json` | `python -m NessieAI.build_tools.gen_op_surfaces --write --root .` | `NessieAI/tests/build_tools/` |
| Schema retrieval | `NessieAI/schema_rag/` | `NessieAI/tests/schema_rag/` |
| The agent image, proxy or sidecar | `NessieAI/docker/<name>/` | `NessieAI/tests/cc/` (port guards), `NessieAI/docker/cc-runtime/tests/` |
| What post-deploy CI proves about Nessie | `QUESTIONS` in `ci/smoke/test_nessie.py`; `ci/smoke/README.md` "Nessie lane" | `ci/smoke/test_nessie_unit.py` |

HiBayes is spread over several folders (router, hibayes, dmac_assistant, the bayes harness, the eval
image, the `eval_*` tables). Its map is `NessieAI/hibayes/README.md` "HiBayes lives in these places".

## What stays in nextseek_api

| Stays | Why |
|---|---|
| `nextseek_api/assistant/models_db.py` (every assistant model, including the `eval_*` tables) and `nextseek_api/migrations/` | app label `nextseek_api` and migration ownership |
| The ViewSets: `nextseek_api/services/{assistant,cc_assistant,evaluator,nessie,schema_rag}.py` | the HTTP surface; URLs never change |
| `nextseek_api/assistant/` consumer, routing, adapters, descriptions, `excel_export.py`, `session_debug.py`, `CONTRACT.md` | websocket, persistence seams, OpenAPI text; `excel_export.py` is shared with the admin export |
| `nextseek_api/cc_assistant/` Django shell: `apps.py`, `cc_sweep.py`, `cc_upload_tasks.py`, `cc_endpoint_guards.py` | `INSTALLED_APPS` label `cc_assistant` and the Celery task names `cc_assistant.upload` and `cc_assistant.sweep_cc_summaries` |

## Sub-docs

| Read | When |
|---|---|
| `NessieAI/chat_nextseek/README.md`, `CLAUDE.md` | NS engine work |
| `NessieAI/chat_nextseek/src/chat_nextseek/evaluator/README.md` | the retry-context evaluator behind `/nextseek_api/evaluator/` (not HiBayes) |
| `NessieAI/chat_nextseek/src/chat_nextseek/luria/pipelines/scrnaseq_2_7_1_star/README.md` | the patched scrnaseq clone for Luria launches |
| `NessieAI/dmac_assistant/README.md`, `CLAUDE.md` | BAML sources, the generated client, the two JSON registries |
| `NessieAI/router/README.md`, `CLAUDE.md` | route decisions |
| `NessieAI/cc/README.md`, `CLAUDE.md` | the CC sandbox, path layout, op registry |
| `NessieAI/cc/DEPLOY.md` | deploying or accepting the CC route |
| `NessieAI/ns/README.md`, `CLAUDE.md`, and `nextseek_api/assistant/CONTRACT.md` | granular ops and their HTTP contract |
| `NessieAI/hibayes/README.md`, `CLAUDE.md` | HiBayes |
| `NessieAI/schema_rag/README.md` | schema retrieval |
| `NessieAI/build_tools/README.md`, `CLAUDE.md` | generated surfaces and the docs ingester |
| `NessieAI/chat_frontend/README.md`, `CLAUDE.md` | the chat panel |
| `NessieAI/docker/README.md`, `CLAUDE.md` | the AI images |
| `NessieAI/docker/cc-runtime/container/CLAUDE.md` | what the CC agent is told (ships in the image) |
| `NessieAI/tests/README.md` | every AI test lane and its command |
| `NessieAI/tests/nessie_tests/README.md`, `CLAUDE.md` | the router-aware harness |
| `NessieAI/tests/nessie_tests/output-skill/SKILL.md`, `NessieAI/tests/nessie_tests/output-skill/README.md` | triaging a finished harness run |
| `NessieAI/tests/nessie_tests/output-skill-bayesian/SKILL.md` | grading a paired `--bayesian` run |
| `NessieAI/docs/architecture.md` | how a Nessie turn works, in depth |
| `NessieAI/docs/dev-v5-merge-decisions.md` | before touching `route_capabilities.json` or the classifier labels |
| `NessieAI/docs/nessie-blocked-capabilities.md` | before adding corpus variants or criteria |
| `NessieAI/docs/nessie-question-set-2026-08-06.md` | the ground-truth question set (generated; regenerate, never hand-edit) |
| `NessieAI/docs/2026-07-31-hibayes-eval-routing-design.md` | the HiBayes loop design |
| `NessieAI/history/INDEX.md` | anything historical (read-only) |
