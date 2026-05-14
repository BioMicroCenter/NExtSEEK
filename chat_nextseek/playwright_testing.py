"""playwright_testing.py — Comprehensive browser-driven E2E test for chat_nextseek.

STATUS: SKELETON / SPEC ONLY. Hand to superpowers tomorrow for implementation.
        Nothing here runs end-to-end yet — function bodies are stubs.

----------------------------------------------------------------------------
What this is
----------------------------------------------------------------------------

Unlike smart_test.py (which drives run_query() directly in-process), this
suite drives the **actual chat UI in a real browser** via Playwright, then
verifies that every layer reports the same thing:

    UI rendered text  ≡  outputs/<run>/console.txt  ≡  MySQL chat_session row

If those three diverge, the test fails — that catches the whole class of
"backend did the right thing but the user saw nothing / saw something
different / lost it after a session reload" bugs that smart_test.py cannot
see.

----------------------------------------------------------------------------
Differences from smart_test.py
----------------------------------------------------------------------------

| Aspect                       | smart_test.py                   | playwright_testing.py                  |
|------------------------------|---------------------------------|----------------------------------------|
| Entry point                  | run_query() / run_query_plan()  | Playwright browser → chat UI input     |
| Session storage              | MySQLSessionState (in-process)  | Django chat_session row (read via API) |
| What's verified              | Debug dict from run_query()     | UI text + console.txt + DB row trio    |
| Pass-criteria depth          | Deep agent internals            | User-visible outcomes (reply, files,   |
|                              |                                 |   session state, artifact downloads)   |
| Failure-debug pattern        | Run debug_queries on fail       | NO debug fan-out — each test stands    |
|                              |                                 |   alone (user explicitly requested)    |
| Test selection               | All / --only T1,T5              | Random by default OR --key/--query     |
| Test matrix layout           | 24 flat tests                   | ~14 top-level keys × 4 queries each    |

----------------------------------------------------------------------------
Logging targets — what the test reads
----------------------------------------------------------------------------

Three sources of truth, all of which must agree for a test to pass:

1. UI (Playwright):
   - The last assistant message bubble's rendered text (markdown-rendered).
   - The processing stepper events captured during streaming.
   - Any artifact-download links/buttons.

2. Per-run output dir on the docker host:
   ``/home/cdemu/code/dmac/docker/NExtSEEK/outputs/<YYMMDD_HHMMSS>_<api_user>/``

   Contains:
   - ``console.txt``          — full stdout/stderr tee for the request
   - ``chat.txt``             — assembled chat transcript for the run
   - ``api_requests.json``    — every NExtSEEK REST call + response
   - ``prompts.json``         — every LLM call (system + user) and parsed response
   - ``files/api_result_bundle_<n>.json``  — full API response per turn
   - ``files/graph_debug_<ts>.json``       — graph agent plan + Neo4j output
   - ``files/plan_debug_<n>.json``         — planner steps + evaluator
   - ``files/report/<type>/...``           — generated GEO/SRA/PRIDE/NFCORE outputs

3. MySQL chat_session row (the persisted session state):
   - Database: ``dmac``
   - Table:    ``assistant_chat_session`` (Django model: ``ChatSession``)
   - PK:       ``session_id`` (32-char UUID, hyphens stripped)
   - Columns:  ``results_history`` (JSON), ``extra_state`` (JSON), ``title``,
               ``created_at``, ``updated_at``, ``user_id``
   - The ``extra_state`` JSON contains the rolling ``chat_log`` (≤50 turns).

   Standalone mode (cli.py) writes to SQLite at
   ``~/.local/state/chat_nextseek/db.sqlite`` instead — same schema.

Django app-wide logs (separate channel, not per-run):
   ``/home/cdemu/code/dmac/docker/NExtSEEK/logs/{django,seek,nextseek,django_crontab}.log``
   These are useful for environment-level assertions (no 5xx, no LLMFatalError
   tracebacks, no DB-connection-failed lines) but are not per-test.

----------------------------------------------------------------------------
Test matrix shape (the X top-level keys, 4 queries each)
----------------------------------------------------------------------------

Each top-level key targets one **distinct overall agent functionality** — when
that key runs, the four queries collectively exercise the happy path, a
follow-up variant, an edge case, and an adversarial / off-target case. Pass
criteria are per-query; running a key is "pass if all 4 queries pass".

Buckets (these become the TEST_MATRIX keys):

| Key                  | Subagents exercised                                | Notes                              |
|----------------------|----------------------------------------------------|------------------------------------|
| SEARCH_NEW           | entity → parser → api_agent_build_request → chatter | new_search mode                   |
| SEARCH_REFINE        | parser (refine_last_search) → api → chatter        | requires preceding new_search      |
| SEARCH_GRAPH         | entity → parser → graph_agent → Neo4j → chatter    | Investigation/Study/lineage queries|
| MEMORY               | parser → memory_agent → memory_coder → chatter     | ask_about_last_results             |
| SYSTEM               | parser → system_agent (get_capabilities/entities)  | capabilities + entity catalog Q&A  |
| REPORTER_SAMPLES     | parser → reporter (summary mode = samples / RPPR)  | SQL-backed                         |
| REPORTER_PROTOCOLS   | parser → reporter (summary mode = protocols)       | SQL-backed                         |
| REPORTER_PUBLISHED   | parser → reporter (summary mode = published)       | Neo4j + SQL hybrid                 |
| REPORT_GEO           | reporter (report_generation) → report_writer → xlsx| GEO_template.xlsx output           |
| REPORT_SRA           | reporter (report_generation) → report_writer → xlsx| SRA_metadata.xlsx + biosample.xlsx |
| REPORT_PRIDE         | reporter (report_generation) → report_writer       | pride.json output                  |
| WIZARD_NFCORE        | nfcore_wizard (slot-fill) → emitter + Tower        | multi-turn; covers guardrail too   |
| PLANNER              | multi_parser → planner → context_engineer → evaluator | -qp pipeline                    |
| GUARDRAILS           | unsupported / bulk-export / no-sequencing-data     | negative-path coverage             |

UI-specific buckets (still run in the same harness; verify the chat UI itself):

| Key                  | What's exercised                                                          |
|----------------------|---------------------------------------------------------------------------|
| SESSIONS             | New chat / switch / rename / delete / hydration after reload              |
| PROD_TOGGLE          | Admin-only checkbox; non-admin can't bypass; PROD routes to PROD config   |
| ARTIFACTS            | Downloadable files persist across session re-entry (was broken)           |

----------------------------------------------------------------------------
Per-query pass-criteria
----------------------------------------------------------------------------

Each query specifies criteria as a flat dict of ``field: spec`` pairs. The
field names are deliberately UI-oriented (not deep agent debug fields), but
also include three-way-consistency checks.

Supported ops (same vocabulary as testing.json plus the new UI ones):

  eq / contains / nonempty / true / gte / lte / mentions / matches_re   (text)
  ui_text_matches_db_reply                                              (cross)
  console_contains                                                      (cross)
  files_emitted: [".csv", ".xlsx", ...]                                 (artifacts)
  session_has_chat_log_turn                                             (DB)
  results_history_length: {op: ..., n: ...}                             (DB)

----------------------------------------------------------------------------
Runner CLI shape
----------------------------------------------------------------------------

  uv run playwright_testing.py                        # pick 1 random query and run it
  uv run playwright_testing.py --key SEARCH_NEW       # pick 1 random query within that key
  uv run playwright_testing.py --key SEARCH_NEW --all # run all 4 queries within that key
  uv run playwright_testing.py --query SEARCH_NEW.q1  # run a specific query by ID
  uv run playwright_testing.py --suite full           # run every query (sequential, slow)
  uv run playwright_testing.py --headed              # show browser (default headless)
  uv run playwright_testing.py --base-url URL        # default http://localhost:8000
  uv run playwright_testing.py --user / --pass       # auth (otherwise reads .env)
  uv run playwright_testing.py --no-prod             # never tick PROD even on PROD_TOGGLE
  uv run playwright_testing.py --report              # generate HTML report from prior run

Output lands in ``outputs/playwright_e2e_<YYMMDD_HHMMSS>/`` with one
sub-folder per test that ran, containing: ``screenshot.png``, ``trace.zip``,
``ui_transcript.txt``, the verified ``console.txt`` excerpt, the DB row JSON,
and a ``criteria_results.json``.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


# ---------------------------------------------------------------------------
# Config — where things live on the running docker host
# ---------------------------------------------------------------------------

OUTPUTS_DIR = Path("/home/cdemu/code/dmac/docker/NExtSEEK/outputs")
DJANGO_LOG_DIR = Path("/home/cdemu/code/dmac/docker/NExtSEEK/logs")

# Three Django app logs that should always be growing during a real run.
# Used by the smoke check in verify_logging_pipeline_alive().
DJANGO_LOG_FILES = ("django.log", "nextseek.log", "seek.log")

# MySQL — the persisted chat_session table the Django backend writes to.
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_NAME = "dmac"
DEFAULT_DB_TABLE = "assistant_chat_session"

DEFAULT_UI_BASE_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Query:
    """One concrete browser-driven test query."""

    id: str                                      # e.g. "SEARCH_NEW.q1"
    user_text: str                               # what to type into the chat input
    pipeline: Literal["standard", "plan"] = "standard"
    use_prod: bool = False                       # tick the PROD checkbox before sending
    setup_queries: list[str] = field(default_factory=list)  # prior queries run first in the same session
    pass_criteria: dict[str, Any] = field(default_factory=dict)
    artifact_expected: bool = False              # whether downloadable file(s) should appear
    notes: str = ""                              # human-readable rationale, shown in HTML report


@dataclass
class TestKey:
    """One top-level functional bucket. Contains exactly 4 queries."""

    key: str
    description: str
    queries: list[Query]


# ---------------------------------------------------------------------------
# THE TEST MATRIX — top-level keys with 4 queries each
# ---------------------------------------------------------------------------
#
# IMPLEMENTATION NOTE for tomorrow: queries below are placeholders. Refine
# the user_text + pass_criteria after a quick smoke-run-by-hand so each one
# is known to exercise the intended path. The matrix shape is the contract.


TEST_MATRIX: list[TestKey] = [

    TestKey(
        key="SEARCH_NEW",
        description=(
            "Fresh searches: entity_agent + parser_agent + api_agent_build_request "
            "+ chatter_agent_answer. Verifies the user sees the result count and a "
            "UID preview in the chat bubble, and that the chat_log + results_history "
            "row both record the bundle."
        ),
        queries=[
            Query(
                id="SEARCH_NEW.q1",
                user_text="Find me mice treated with NDMA.",
                pass_criteria={
                    "ui_reply_mentions": ["mice", "NDMA"],
                    "ui_text_matches_db_reply": True,
                    "console_contains": "advanced_search",
                    "results_history_length": {"op": "gte", "n": 1},
                },
                notes="Canonical T1-equivalent — should route to new_search with sampletype_code=MUS.",
            ),
            Query(
                id="SEARCH_NEW.q2",
                user_text="Show me all the IV-BCG monkeys from the Seder lab.",
                pass_criteria={
                    "ui_reply_mentions": ["NHP", "Seder"],
                    "ui_text_matches_db_reply": True,
                    "console_contains": "/advanced_search/",
                },
                notes="Multi-entity (project + lab + sampletype) — checks entity catalog shortlisting.",
            ),
            Query(
                id="SEARCH_NEW.q3",
                user_text="Find me ENG samples from any 2024 BTC study.",
                pass_criteria={
                    "ui_text_matches_db_reply": True,
                    "ui_reply_matches_re": r"\b\d+\b",   # some count appears in the bubble
                },
                notes="Date + project + sampletype combo — edge case for parser filter assembly.",
            ),
            Query(
                id="SEARCH_NEW.q4",
                user_text="give me all the data",
                pass_criteria={
                    "ui_reply_mentions": ["unsupported", "narrow"],
                    "console_contains": "_is_unscoped_bulk_export_request",
                },
                notes="Adversarial — should hit the bulk-export guardrail and stay in SEARCH_NEW's parser path.",
            ),
        ],
    ),

    TestKey(
        key="SEARCH_REFINE",
        description=(
            "Follow-ups that narrow the prior search via refine_last_search. "
            "Verifies pronoun / 'those' resolution via chat_log + parser routing."
        ),
        queries=[
            Query(
                id="SEARCH_REFINE.q1",
                setup_queries=["Find me mice treated with NDMA."],
                user_text="Now filter those to only CD8-depleted animals.",
                pass_criteria={
                    "ui_text_matches_db_reply": True,
                    "console_contains": "refine_last_search",
                    "results_history_length": {"op": "gte", "n": 2},
                },
                notes="T3-equivalent.",
            ),
            Query(
                id="SEARCH_REFINE.q2",
                setup_queries=["Find samples from the GBM study."],
                user_text="Drop the published ones.",
                pass_criteria={"console_contains": "refine_last_search"},
                notes="Refine using a negative filter.",
            ),
            Query(
                id="SEARCH_REFINE.q3",
                setup_queries=["Find me NHP samples."],
                user_text="Restrict that to ones with flow cytometry assays.",
                pass_criteria={"console_contains": "refine_last_search"},
                notes="Refine adds an assay filter.",
            ),
            Query(
                id="SEARCH_REFINE.q4",
                setup_queries=["Find me mice treated with NDMA."],
                user_text="Now find me monkeys.",
                pass_criteria={"console_contains": "new_search"},
                notes="Adversarial — 'now find me' should NOT refine; should be a fresh new_search.",
            ),
        ],
    ),

    TestKey(
        key="SEARCH_GRAPH",
        description=(
            "Structural/lineage queries route to graph_agent → Neo4j. Verifies "
            "Cypher generation, neo4j_ok, and that the auto-retry path doesn't "
            "produce duplicate replies."
        ),
        queries=[
            Query(
                id="SEARCH_GRAPH.q1",
                user_text="Find samples of PBMC type from the GBM study that also have flow cytometry.",
                pass_criteria={
                    "console_contains": "graph_query",
                    "files_emitted": [],   # graph_debug_<ts>.json should land in files/
                },
                notes="T11-equivalent.",
            ),
            Query(
                id="SEARCH_GRAPH.q2",
                user_text="What investigations is the Kamm Project linked to?",
                pass_criteria={"console_contains": "graph_query"},
                notes="Investigation-scoped traversal.",
            ),
            Query(
                id="SEARCH_GRAPH.q3",
                user_text="Show me the parent samples for D.SEQ-221031SHA-67-PUB.",
                pass_criteria={"console_contains": "graph_query"},
                notes="Single-node lineage walk upward.",
            ),
            Query(
                id="SEARCH_GRAPH.q4",
                user_text="Find all study assays for SRP.",
                pass_criteria={"console_contains": "graph_query"},
                notes="Adversarial — could route to either graph or reporter; verify routing decision.",
            ),
        ],
    ),

    TestKey(
        key="MEMORY",
        description=(
            "ask_about_last_results → memory_agent_answer → memory_coder_agent. "
            "Verifies that the chatter does NOT re-search and that the answer "
            "draws from the pinned bundle."
        ),
        queries=[
            Query(
                id="MEMORY.q1",
                setup_queries=["Find me mice treated with NDMA."],
                user_text="What sample types were represented in those results?",
                pass_criteria={
                    "ui_text_matches_db_reply": True,
                    "console_contains": "memory_agent",
                    "ui_reply_matches_re": r"(MUS|NHP|TIS|D\.SEQ)",
                },
                notes="T4-equivalent.",
            ),
            Query(
                id="MEMORY.q2",
                setup_queries=["Find me NHP samples from the Seder lab."],
                user_text="How many of those are tumor-related?",
                pass_criteria={"console_contains": "memory_agent"},
                notes="Forces memory_coder to count over a metadata field.",
            ),
            Query(
                id="MEMORY.q3",
                setup_queries=[
                    "Find me mice treated with NDMA.",
                    "Find me NHP samples.",
                    "Find me D.SEQ samples from the BTC project.",
                ],
                user_text="Going way back, what was the count from the NDMA search?",
                pass_criteria={
                    "console_contains": "ask_about_last_results",
                    "ui_reply_matches_re": r"\b\d+\b",
                },
                notes="T19-equivalent — distance-N memory recall.",
            ),
            Query(
                id="MEMORY.q4",
                setup_queries=["Find me mice in the SHA lab from the original GRI study."],
                user_text="How many of those have D.SEQ children?",
                pass_criteria={"console_contains": "memory_agent"},
                notes="Pre-fix bug #7 case — must not hallucinate GRI-prefix UIDs.",
            ),
        ],
    ),

    TestKey(
        key="SYSTEM",
        description=(
            "system_agent: capabilities, entity catalog, search-options. Verifies "
            "the get_capabilities / get_entities / get_searches dispatch."
        ),
        queries=[
            Query(
                id="SYSTEM.q1",
                user_text="What can NExtSEEK do?",
                pass_criteria={"console_contains": "system_question"},
                notes="get_capabilities path.",
            ),
            Query(
                id="SYSTEM.q2",
                user_text="What's the NIH Reporter link for the Kamm Project? What kinds of samples can I search for?",
                pass_criteria={"console_contains": "system_question"},
                notes="T5-equivalent — mixed entity + capability question.",
            ),
            Query(
                id="SYSTEM.q3",
                user_text="What assay codes do you recognize?",
                pass_criteria={"console_contains": "system_question"},
                notes="get_entities path.",
            ),
            Query(
                id="SYSTEM.q4",
                user_text="What query modes are available?",
                pass_criteria={"console_contains": "system_question"},
                notes="get_searches path.",
            ),
        ],
    ),

    TestKey(
        key="REPORTER_SAMPLES",
        description=(
            "reporter > summary > samples. SQL-backed project sample report. "
            "Verifies db_diagnostic surfaces when the connected DB has no data."
        ),
        queries=[
            Query(
                id="REPORTER_SAMPLES.q1",
                user_text="Put together an annual progress report for the Kamm project for 2024.",
                pass_criteria={
                    "console_contains": "reporter",
                    "files_emitted": [".json"],
                },
                notes="T6-equivalent — RPPR routing.",
            ),
            Query(
                id="REPORTER_SAMPLES.q2",
                user_text="How many samples did the BTC project upload in 2024?",
                pass_criteria={"console_contains": "samples"},
                notes="samples summary path.",
            ),
            Query(
                id="REPORTER_SAMPLES.q3",
                user_text="How many samples have been uploaded for IMPACT in 2025?",
                use_prod=False,
                pass_criteria={
                    "ui_reply_mentions": ["MYSQL_HOST_PROD", "doesn't exist"],
                },
                notes="Verifies the db_diagnostic footer renders when no rows + project missing.",
            ),
            Query(
                id="REPORTER_SAMPLES.q4",
                user_text="How many samples have been uploaded for IMPACT in 2025?",
                use_prod=True,
                pass_criteria={
                    "ui_reply_matches_re": r"Rows returned:\s*\d+",
                },
                notes="Same query but with PROD ticked — should hit fairdata.mit.edu and return non-zero.",
            ),
        ],
    ),

    TestKey(
        key="REPORTER_PROTOCOLS",
        description="reporter > summary > protocols (P.<LAB>-<YYMMDD>-<rest> title parsing).",
        queries=[
            Query(
                id="REPORTER_PROTOCOLS.q1",
                user_text="Show me protocols registered for the CGR project.",
                pass_criteria={"console_contains": "protocols"},
                notes="T7-equivalent.",
            ),
            Query(
                id="REPORTER_PROTOCOLS.q2",
                user_text="Which labs have the most protocols in BTC?",
                pass_criteria={"console_contains": "protocols"},
                notes="labs_table-driven answer.",
            ),
            Query(
                id="REPORTER_PROTOCOLS.q3",
                user_text="List 2024 protocols across all projects.",
                pass_criteria={"console_contains": "protocols"},
                notes="Cross-project / year-scoped.",
            ),
            Query(
                id="REPORTER_PROTOCOLS.q4",
                user_text="Show me protocols for the SUPERFAKE project.",
                pass_criteria={
                    "ui_reply_mentions": ["doesn't exist", "no protocols"],
                },
                notes="Unknown project — verifies graceful empty-result rendering.",
            ),
        ],
    ),

    TestKey(
        key="REPORTER_PUBLISHED",
        description="reporter > summary > published (Neo4j + SQL hybrid).",
        queries=[
            Query(
                id="REPORTER_PUBLISHED.q1",
                user_text="What's published from the IMPACT project?",
                use_prod=True,
                pass_criteria={"console_contains": "published"},
                notes="Verifies the Investigation→Study→Sample traversal joined with SQL titles.",
            ),
            Query(
                id="REPORTER_PUBLISHED.q2",
                user_text="Give me the published list for METNET.",
                use_prod=True,
                pass_criteria={"console_contains": "published"},
            ),
            Query(
                id="REPORTER_PUBLISHED.q3",
                user_text="Across all projects, what got published in 2024?",
                use_prod=True,
                pass_criteria={"console_contains": "published"},
            ),
            Query(
                id="REPORTER_PUBLISHED.q4",
                user_text="What's published for project XYZQ?",
                use_prod=True,
                pass_criteria={"ui_reply_matches_re": r"(no|zero|0)"},
                notes="Unknown project published lookup.",
            ),
        ],
    ),

    TestKey(
        key="REPORT_GEO",
        description=(
            "reporter > report_generation > GEO — produces GEO_template.xlsx. "
            "Verifies file artifact lands on disk + is downloadable from UI."
        ),
        queries=[
            Query(
                id="REPORT_GEO.q1",
                user_text="Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={
                    "console_contains": "GEO",
                    "files_emitted": [".xlsx"],
                    "ui_has_download_link": True,
                },
                notes="T8-equivalent.",
            ),
            Query(
                id="REPORT_GEO.q2",
                user_text="Generate a GEO submission for all D.SEQ samples in study GRI-001.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".xlsx"]},
                notes="Study-scoped GEO.",
            ),
            Query(
                id="REPORT_GEO.q3",
                user_text="Make a GEO export for sample D.SEQ-NOTREAL-1-PUB.",
                use_prod=True,
                pass_criteria={
                    "ui_reply_matches_re": r"(not found|no metadata|skipped)",
                },
                notes="Unknown UID — verifies graceful failure not a 500.",
            ),
            Query(
                id="REPORT_GEO.q4",
                user_text="GEO submission for D.SEQ-221031SHA-67-PUB",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".xlsx"]},
                notes="Single-UID — minimum-input edge.",
            ),
        ],
    ),

    TestKey(
        key="REPORT_SRA",
        description="reporter > report_generation > SRA — SRA_metadata.xlsx + biosample.xlsx.",
        queries=[
            Query(
                id="REPORT_SRA.q1",
                user_text="Build me an SRA submission for D.SEQ-230512FOR-288-PUB, D.SEQ-230512FOR-289-PUB.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".xlsx"]},
                notes="T9-equivalent.",
            ),
            Query(
                id="REPORT_SRA.q2",
                user_text="SRA for everything from the FOR lab in 2023.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".xlsx"]},
            ),
            Query(
                id="REPORT_SRA.q3",
                user_text="SRA submission for D.SEQ-MISSING-1-PUB.",
                use_prod=True,
                pass_criteria={"ui_reply_matches_re": r"(not found|skipped|no rows)"},
            ),
            Query(
                id="REPORT_SRA.q4",
                user_text="Build SRA from my last search.",
                setup_queries=["Find me D.SEQ samples from the FOR lab."],
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".xlsx"]},
                notes="Uses last_search bundle as the row source.",
            ),
        ],
    ),

    TestKey(
        key="REPORT_PRIDE",
        description="reporter > report_generation > PRIDE — pride.json output.",
        queries=[
            Query(
                id="REPORT_PRIDE.q1",
                user_text="Please create a PRIDE submission for the mass spec sample D.MS-220101LAB-1-PUB.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".json"]},
                notes="T10-equivalent.",
            ),
            Query(
                id="REPORT_PRIDE.q2",
                user_text="PRIDE export for all the proteomics samples from CGR.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"files_emitted": [".json"]},
            ),
            Query(
                id="REPORT_PRIDE.q3",
                user_text="PRIDE submission for D.MS-NOTREAL-1.",
                use_prod=True,
                pass_criteria={"ui_reply_matches_re": r"(not found|skipped)"},
            ),
            Query(
                id="REPORT_PRIDE.q4",
                user_text="Build a PRIDE submission for an RNA-seq sample.",
                use_prod=True,
                pass_criteria={
                    "ui_reply_matches_re": r"(not a proteomics|wrong sample type|mass spec)",
                },
                notes="Adversarial — wrong sample type for PRIDE.",
            ),
        ],
    ),

    TestKey(
        key="WIZARD_NFCORE",
        description=(
            "Multi-turn slot-fill nf-core wizard. Each query is itself a multi-turn "
            "scenario (1 'query' here = 1 scripted conversation). Covers happy path, "
            "rabbit-hole, off-topic passthrough, and the sequencing-data guardrail."
        ),
        queries=[
            Query(
                id="WIZARD_NFCORE.q1",
                user_text="__multi__:happy_path",   # special marker; runner expands via wizard_scripts.py
                use_prod=True,
                artifact_expected=True,
                pass_criteria={
                    "wizard_reached_confirm": True,
                    "files_emitted": ["samplesheet.csv"],
                },
                notes="rnaseq pipeline + last_search UIDs + 2 cohorts + 1 enrichment + build.",
            ),
            Query(
                id="WIZARD_NFCORE.q2",
                user_text="__multi__:rabbit_hole",
                use_prod=True,
                pass_criteria={
                    "wizard_stayed_on_step": True,
                    "ui_reply_mentions": ["bulk", "single-cell"],
                },
                notes="T15-equivalent — pipeline-question rabbit hole; wizard should explain, not advance.",
            ),
            Query(
                id="WIZARD_NFCORE.q3",
                user_text="__multi__:source_sample_lineage",
                use_prod=True,
                pass_criteria={
                    "ui_reply_mentions": ["D.SEQ", "downstream", "sequencing"],
                },
                notes="Picks NHP source UIDs — wizard must surface that they lack D.SEQ children.",
            ),
            Query(
                id="WIZARD_NFCORE.q4",
                user_text="__multi__:off_topic_passthrough",
                use_prod=True,
                pass_criteria={
                    "wizard_stayed_active": True,
                    "ui_reply_mentions": ["Back to your samplesheet"],
                },
                notes="T23-equivalent — off-topic answered via parser passthrough; wizard remains active.",
            ),
        ],
    ),

    TestKey(
        key="PLANNER",
        description=(
            "Planner pipeline: multi_parser → planner → executor → context_engineer "
            "→ evaluator. Toggle 'Planner' in the UI before sending."
        ),
        queries=[
            Query(
                id="PLANNER.q1",
                user_text="Find mice in the GBM study treated with NDMA.",
                pipeline="plan",
                pass_criteria={
                    "console_contains": "planner",
                    "files_emitted": [],  # plan_debug_<n>.json
                },
                notes="Single-step planner.",
            ),
            Query(
                id="PLANNER.q2",
                user_text="For mice in the GBM study, build a GEO submission.",
                pipeline="plan",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"console_contains": "planner"},
                notes="Multi-step: graph_query → report_generation.",
            ),
            Query(
                id="PLANNER.q3",
                user_text="Get all NHP samples with flow cytometry data and tell me how many are from BCG-vaccinated animals.",
                pipeline="plan",
                pass_criteria={"console_contains": "planner"},
                notes="Forces an intersect or follow-up coding_filter step.",
            ),
            Query(
                id="PLANNER.q4",
                user_text="Find me a sample.",
                pipeline="plan",
                pass_criteria={"ui_reply_mentions": ["narrow", "more specific"]},
                notes="Adversarial vague query — planner should ask for clarification, not bulk-export.",
            ),
        ],
    ),

    TestKey(
        key="GUARDRAILS",
        description=(
            "Negative-path coverage: unsupported, bulk-export, sequencing-missing. "
            "Each query must produce a graceful user-facing message, not a 500 or "
            "an empty bubble."
        ),
        queries=[
            Query(
                id="GUARDRAILS.q1",
                user_text="What is today's weather forecast in Boston?",
                pass_criteria={
                    "ui_reply_mentions": ["unsupported", "outside"],
                    "console_contains": "unsupported",
                },
                notes="T12-equivalent.",
            ),
            Query(
                id="GUARDRAILS.q2",
                user_text="Make me a bar chart of sample counts broken down by type across all projects.",
                pass_criteria={"ui_reply_mentions": ["chart", "unsupported"]},
                notes="T13-equivalent — figure/chart generation not supported.",
            ),
            Query(
                id="GUARDRAILS.q3",
                user_text="Download every sample's metadata.",
                pass_criteria={"console_contains": "_is_unscoped_bulk_export_request"},
                notes="Bulk-export guardrail.",
            ),
            Query(
                id="GUARDRAILS.q4",
                user_text="__multi__:wizard_no_sequencing",
                use_prod=True,
                pass_criteria={"ui_reply_mentions": ["sequencing", "D.SEQ"]},
                notes="Wizard sequencing-data guardrail (picks UIDs with no D.SEQ children, tries to advance).",
            ),
        ],
    ),

    # ── UI-only buckets (chat_frontend behavior, not chat_nextseek agents) ──

    TestKey(
        key="SESSIONS",
        description=(
            "Sidebar + session lifecycle: new chat, switch, rename, delete, "
            "hydration on reload. Pure UI — these don't drive chat_nextseek but "
            "verify the persistence layer the chat surfaces through."
        ),
        queries=[
            Query(
                id="SESSIONS.q1",
                user_text="__ui__:new_chat_then_send",
                pass_criteria={"db_chat_session_created": True},
                notes="Click '+ New chat', send 1 query, verify a new row appears in assistant_chat_session.",
            ),
            Query(
                id="SESSIONS.q2",
                user_text="__ui__:switch_session_hydrates_correctly",
                pass_criteria={"ui_messages_match_db_turns": True},
                notes="Switch sidebar selection, verify the loaded turns match results_history.",
            ),
            Query(
                id="SESSIONS.q3",
                user_text="__ui__:rename_session_persists",
                pass_criteria={"db_title_eq_new_title": True},
                notes="Rename via the icon, verify ChatSession.title in DB updated.",
            ),
            Query(
                id="SESSIONS.q4",
                user_text="__ui__:delete_session_removes_row",
                pass_criteria={"db_row_absent": True},
                notes="Delete via the icon, verify the row is gone from MySQL.",
            ),
        ],
    ),

    TestKey(
        key="PROD_TOGGLE",
        description=(
            "Admin-only PROD checkbox: visibility, dispatch, non-admin bypass "
            "attempt. Requires two test logins (admin + non-admin)."
        ),
        queries=[
            Query(
                id="PROD_TOGGLE.q1",
                user_text="__ui__:admin_sees_checkbox",
                pass_criteria={"ui_prod_checkbox_visible": True},
                notes="Logged in as admin user.",
            ),
            Query(
                id="PROD_TOGGLE.q2",
                user_text="__ui__:nonadmin_no_checkbox",
                pass_criteria={"ui_prod_checkbox_visible": False},
                notes="Logged in as non-admin user.",
            ),
            Query(
                id="PROD_TOGGLE.q3",
                user_text="How many samples for IMPACT in 2025?",
                use_prod=True,
                pass_criteria={
                    "console_contains": "fairdata.mit.edu",
                    "ui_reply_matches_re": r"Rows returned:\s*[1-9]",
                },
                notes="Admin + PROD checked — verify request body has use_prod=true and reporter hits prod DB.",
            ),
            Query(
                id="PROD_TOGGLE.q4",
                user_text="__ui__:nonadmin_forces_use_prod_via_api",
                pass_criteria={"backend_fell_back_to_dev_config": True},
                notes=(
                    "Hand-craft a POST with use_prod=true as a non-admin (Playwright route intercept). "
                    "Verify _select_chat_config falls back to NEXTSEEK_CHAT_CONFIG (dev), not PROD."
                ),
            ),
        ],
    ),

    TestKey(
        key="ARTIFACTS",
        description=(
            "Downloadable file persistence: emitted file shows in the bubble, "
            "is downloadable, and re-appears when the session is re-entered."
        ),
        queries=[
            Query(
                id="ARTIFACTS.q1",
                user_text="Build me a GEO Submission for D.SEQ-221031SHA-67-PUB.",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"ui_has_download_link": True, "download_succeeds": True},
            ),
            Query(
                id="ARTIFACTS.q2",
                user_text="__ui__:reenter_session_artifact_persists",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"ui_has_download_link_after_reentry": True},
                notes="Run GEO, switch away, switch back, confirm download link still present.",
            ),
            Query(
                id="ARTIFACTS.q3",
                user_text="__ui__:download_content_matches_disk",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"download_bytes_match_outputs_dir": True},
                notes="Sha-sum the downloaded blob, compare to outputs/<run>/files/report/...",
            ),
            Query(
                id="ARTIFACTS.q4",
                user_text="__ui__:multi_artifact_run",
                pipeline="plan",
                use_prod=True,
                artifact_expected=True,
                pass_criteria={"ui_download_count_gte": 2},
                notes="Planner step that emits multiple files (e.g. RPPR has samples + protocols + published JSONs).",
            ),
        ],
    ),

]


# ---------------------------------------------------------------------------
# Three-way consistency verification — UI ≡ console.txt ≡ DB row
# ---------------------------------------------------------------------------


def verify_logging_pipeline_alive() -> dict[str, Any]:
    """Smoke-check before any browser test:

      1. /app/logs/{django,seek,nextseek}.log all exist and are non-zero.
      2. The newest outputs/<ts>_demo/ dir is < 24h old AND has a console.txt.
      3. MySQL chat_session table reachable; at least one row exists.

    Returns a dict {ok: bool, details: {...}}.
    """
    raise NotImplementedError("STUB — implement via os.stat + docker compose exec mysql")


def fetch_console_for_run(run_dir: Path) -> str:
    """Read outputs/<run>/console.txt verbatim. Used by console_contains checks."""
    raise NotImplementedError


def fetch_session_row(session_id: str) -> dict[str, Any]:
    """Query MySQL for the assistant_chat_session row matching session_id.
    Returns the parsed row including extra_state.chat_log + results_history.
    Standalone-mode fallback: open the SQLite at SESSION_DB_PATH if MySQL fails.
    """
    raise NotImplementedError


def verify_three_way_consistency(
    *,
    ui_reply_text: str,
    console_text: str,
    db_row: dict[str, Any],
    expected_user_query: str,
) -> dict[str, Any]:
    """For the *latest* turn:

      • db_row['extra_state']['chat_log'][-1]['user_query'] == expected_user_query
      • db_row['extra_state']['chat_log'][-1]['assistant_reply'] reduces to
        the same plain text as the UI bubble (after markdown→plain).
      • console_text contains a recognizable trace for the same query
        (timestamp + endpoint + agent label).

    Returns {ok: bool, diffs: [...]} — the runner attaches this to the test
    result for the HTML report.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Playwright driver
# ---------------------------------------------------------------------------


def launch_browser(*, headed: bool, base_url: str):
    """Spin up Playwright (sync_playwright) with a fresh context. Return the
    page handle. Headless by default; --headed shows the window for debug."""
    raise NotImplementedError


def login(page, *, username: str, password: str) -> None:
    """Drive the auth flow (Basic auth header or session login). Idempotent."""
    raise NotImplementedError


def open_new_chat(page) -> str:
    """Click '+ New chat' in the sidebar, wait for the empty chat panel, and
    return the new session_id (read from the URL after the first send, or
    from /assistant/sessions/ list)."""
    raise NotImplementedError


def set_pipeline_toggle(page, *, pipeline: Literal["standard", "plan"]) -> None:
    """Click the Standard or Planner pill button before sending."""
    raise NotImplementedError


def set_prod_toggle(page, *, enabled: bool) -> None:
    """Tick / untick the admin-only PROD checkbox. Asserts visibility based
    on caller context (admin = visible, non-admin = absent)."""
    raise NotImplementedError


def send_query(page, user_text: str) -> dict[str, Any]:
    """Type into the textarea, press Enter, wait for the streaming response
    to finish (no more pending agent_started events). Return:

        {
          "rendered_text": "<final assistant bubble plain-text>",
          "stepper_events": [...],
          "download_buttons": [...],
          "duration_ms": ...,
        }
    """
    raise NotImplementedError


def find_latest_run_dir() -> Path:
    """Scan OUTPUTS_DIR for the most recently created subdir. Used to locate
    the console.txt + files/ for the query we just sent.

    Subtle: there's a race between send_query() returning and the orchestrator
    finalizing the dir contents. Helper should retry for up to ~5s waiting
    for ``console.txt`` to be non-empty + the file count to stabilize."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Pass-criteria evaluation
# ---------------------------------------------------------------------------


def evaluate_criteria(
    *,
    query: Query,
    ui_result: dict[str, Any],
    console_text: str,
    db_row: dict[str, Any],
    run_dir: Path,
) -> list[dict[str, Any]]:
    """For each ``field: spec`` in ``query.pass_criteria``, dispatch to the
    appropriate checker. Return a list of {field, ok, observed, expected}.

    Supported field families:
      ui_*                 → check ui_result.rendered_text / download_buttons
      console_*            → check console_text
      db_*, results_history_*  → check db_row
      files_emitted        → check run_dir/files/**
      ui_text_matches_db_reply → cross-source consistency
      wizard_*             → check db_row['extra_state']['nfcore_wizard'] state machine
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def pick_query(
    *,
    key_filter: str | None = None,
    query_id_filter: str | None = None,
    run_all_for_key: bool = False,
    full_suite: bool = False,
) -> list[Query]:
    """Resolve --key / --query / --all / --suite into a concrete list of
    Query objects to run. Random selection happens here when no filter is given.

      • No flags         → 1 random Query across the whole matrix
      • --key X          → 1 random Query inside key X
      • --key X --all    → all 4 queries in key X (in order)
      • --query KEY.qN   → that single query
      • --suite full     → every query (sequential, slow)
    """
    raise NotImplementedError


def run_query_e2e(query: Query, *, page, base_url: str) -> dict[str, Any]:
    """Execute one Query end-to-end:

      1. Open a new chat session (or re-use if query.setup_queries is non-empty
         AND we need turns to accumulate in one session).
      2. For each setup_query: send + wait.
      3. set_pipeline_toggle(query.pipeline); set_prod_toggle(query.use_prod).
      4. Send the actual user_text (or expand __multi__: / __ui__: scenarios
         via wizard_scripts.py / ui_scripts.py).
      5. Capture: rendered text, stepper events, download buttons, latest run_dir.
      6. Fetch console.txt + DB row.
      7. Run verify_three_way_consistency() and evaluate_criteria().
      8. Save artifacts (screenshot, trace.zip, ui_transcript.txt, console.txt
         excerpt, db_row.json, criteria_results.json) into the per-test out dir.

    Return a result dict that the HTML reporter consumes.
    """
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — argument parsing, suite resolution, browser lifecycle,
    HTML report generation. Returns process exit code (0 = all pass)."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Multi-turn scenario scripts (referenced by __multi__: and __ui__: markers)
# ---------------------------------------------------------------------------
#
# IMPLEMENTATION NOTE: keep the scripted scenarios in this same file (or in
# a sibling playwright_scenarios.py) so the matrix above stays declarative.
# Each scenario is a sequence of {action, payload, wait_for, assert} steps.

WIZARD_SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "happy_path": [
        # 1. Run a setup search so the wizard can use last_search.
        # 2. "Build me an nf-core samplesheet" → wizard intro w/ slot checklist.
        # 3. "let's do rnaseq" → pipeline locked.
        # 4. "use my last search" → uids loaded.
        # 5. "build it" → confirm step.
        # 6. "yes" → emit; assert samplesheet.csv in files/.
    ],
    "rabbit_hole": [
        # 1. Trigger wizard.
        # 2. "what's the difference between rnaseq and scrnaseq?" → wizard stays, explains.
        # 3. Assert step is still "builder", pipeline still None.
    ],
    "source_sample_lineage": [
        # 1. Setup: "Find me NHP samples from the Seder lab."
        # 2. Trigger wizard.
        # 3. "use my last search" → wizard loads NHP UIDs.
        # 4. "build it" → wizard refuses, surfaces D.SEQ-children prompt OR sequencing guardrail.
        # 5. Assert mentions "downstream" or "D.SEQ" or "sequencing data".
    ],
    "off_topic_passthrough": [
        # 1. Trigger wizard.
        # 2. "what is NDMA?" → wizard calls passthrough_to_parser, replies, stays.
        # 3. Assert wizard.active=True, reply mentions "Back to your samplesheet".
    ],
    "wizard_no_sequencing": [
        # 1. Setup: search for samples that we KNOW have no D.SEQ lineage.
        # 2. Trigger wizard, lock pipeline + uids.
        # 3. "build it" → advance gate triggers _check_sequencing_presence → blocked.
        # 4. Assert reply mentions "sequencing samples" + no transition to confirm.
    ],
}

UI_SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "new_chat_then_send": [...],
    "switch_session_hydrates_correctly": [...],
    "rename_session_persists": [...],
    "delete_session_removes_row": [...],
    "admin_sees_checkbox": [...],
    "nonadmin_no_checkbox": [...],
    "nonadmin_forces_use_prod_via_api": [...],
    "reenter_session_artifact_persists": [...],
    "download_content_matches_disk": [...],
    "multi_artifact_run": [...],
}


# ---------------------------------------------------------------------------
# Sanity-check: total query count + key coverage
# ---------------------------------------------------------------------------

def _sanity_check_matrix() -> None:
    """Run at import time — fail loudly if a key has != 4 queries or there
    are dup IDs. Cheap insurance against drift as the matrix grows."""
    seen_ids: set[str] = set()
    for k in TEST_MATRIX:
        assert len(k.queries) == 4, f"Key {k.key} has {len(k.queries)} queries (must be 4)"
        for q in k.queries:
            assert q.id not in seen_ids, f"Duplicate query id: {q.id}"
            assert q.id.startswith(k.key + "."), f"Query {q.id} doesn't belong to key {k.key}"
            seen_ids.add(q.id)


_sanity_check_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
