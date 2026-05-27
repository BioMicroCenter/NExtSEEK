#!/usr/bin/env python3
"""
MCP server for chat_nextseek.

Exposes three surface areas:
  Resources — context files (catalogs, schemas, capabilities doc)
  Prompts   — agent system prompts (read-only, inspectable)
  Tools     — run_query, run_query_plan, extract_entities, lookup_resource

Run with:
    uv run mcp_server.py               # stdio transport (default, for Claude Desktop)
    uv run mcp_server.py --transport sse --port 8765

Register in Claude Desktop ~/.config/claude/claude_desktop_config.json:
    {
      "mcpServers": {
        "chat-nextseek": {
          "command": "uv",
          "args": ["run", "/path/to/chat_nextseek/mcp_server.py"],
          "cwd": "/path/to/chat_nextseek"
        }
      }
    }
"""

import contextlib
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


@contextlib.contextmanager
def _quiet_stdout():
    """Redirect stdout to stderr during tool execution.

    The MCP stdio transport uses stdout for protocol messages.  Agent code
    emits many print() calls; without this redirect they corrupt the channel.
    """
    old = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old

from chat_nextseek.config import ChatConfig
from chat_nextseek.session import SessionState
from chat_nextseek.agents import entity_agent as _entity_agent
from chat_nextseek.helpers import tool_nextseek_api_request
from chat_nextseek.orchestrator import (
    run_query as _run_query,
    run_query_plan as _run_query_plan,
)

# ── MCP app ───────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "chat-nextseek",
    instructions="""
Multi-agent system that converts natural language queries into NExtSEEK REST API
calls and/or Neo4j Cypher queries.

## Tool routing guide

Use this decision tree to pick the right tool:

1. **lookup_resource** — use FIRST for any question about:
   - What a sample type or assay is, its attributes, metadata fields, aliases,
     parents, or description  (e.g. "what is D.SEQ?", "attributes of RNA Sample",
     "metadata fields for D.SEQ", "what assay codes exist?")
   - What API endpoints are available or what they do
   - Neo4j schema, graph relationships, or protocol vocabulary
   - System capabilities (what can NExtSEEK do?)
   Always prefer lookup_resource over run_query for definitional / schema / catalog
   questions — it is instant and does not spin up the full agent pipeline.

2. **run_query** — use for questions that require LIVE DATA from NExtSEEK, e.g.:
   - Find / search / retrieve actual samples, assays, or studies
   - Follow-up questions about a previous search result
   - Questions requiring graph traversal across real records (e.g. "samples in study X")
   - Reporter / GEO submission generation for specific sample UIDs

3. **run_query_plan** — use instead of run_query when the question is explicitly
   multi-step or when output of one data source must scope another, e.g.:
   - "Find TIS samples connected to liver tissue then pull their sequencing data"
   - Any query where graph UIDs need to filter an API call or vice versa

4. **extract_entities** — use to debug entity resolution only:
   - "What does the system resolve 'RNA-seq' to?"
   - Checking catalog matches before a real query

5. **get_endpoint_schema** — use before direct_api_request to inspect required fields.

6. **direct_api_request** — use for raw API calls when you already know the endpoint
   and request body (e.g. after get_endpoint_schema).

## Quick patterns
| Question type                              | Tool              |
|--------------------------------------------|-------------------|
| "What is / what are the attributes of X?"  | lookup_resource   |
| "What metadata does X have?"               | lookup_resource   |
| "What assays / endpoints exist?"           | lookup_resource   |
| "What can NExtSEEK do?"                    | lookup_resource   |
| "Find / show me / retrieve samples …"      | run_query         |
| "How many X are in study Y?"               | run_query         |
| "Build a GEO submission for …"             | run_query         |
| "Find X then use those to find Y"          | run_query_plan    |
| "What does 'brain organoid' resolve to?"   | extract_entities  |
""",
)

# ── Lazy config ───────────────────────────────────────────────────────────────

_config: ChatConfig | None = None


def _cfg() -> ChatConfig:
    global _config
    if _config is None:
        _config = ChatConfig()
    return _config


# ── Minimal in-memory session (MCP calls are stateless) ───────────────────────

class _DictSession(SessionState):
    def __init__(self) -> None:
        self._d: dict = {"results_history": []}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __getitem__(self, key):
        return self._d[key]

    def __setitem__(self, key, value):
        self._d[key] = value


# ── Resources — context files ─────────────────────────────────────────────────

_CONTEXT_MAP = {
    "capabilities":     "capabilities.md",
    "endpoints":        "min_api_endpoints_enriched.json",
    "graph-schema":     "min_graph_schema.json",
    "neo4j-schema":     "neo4j_schema.json",
    "assay-connections": "neo4j_assay-sample-conn.json",
    "protocol-schema":  "neo4j_protocol_schema.json",
    "sampletypes":      "sampletypes_db.json",
    "assays":           "assays_db.json",
}


@mcp.resource("nextseek://context/{name}")
def context_resource(name: str) -> str:
    """
    Serve a project context file by logical name.

    Available names: capabilities, endpoints, graph-schema, neo4j-schema,
    assay-connections, protocol-schema, sampletypes, assays.
    """
    filename = _CONTEXT_MAP.get(name)
    if not filename:
        raise ValueError(
            f"Unknown context resource '{name}'. "
            f"Available: {', '.join(_CONTEXT_MAP)}"
        )
    path = Path(_cfg().CONTEXT_DIR) / filename
    return path.read_text()


# ── Prompts — agent system prompts ────────────────────────────────────────────

@mcp.prompt()
def entity_agent() -> str:
    """System prompt for the entity extraction agent (sampletypes, assays, keywords)."""
    return _cfg().ENTITY_SYSTEM_PROMPT


@mcp.prompt()
def parser_agent() -> str:
    """Canonical parser prompt used by the standard parser compatibility wrapper."""
    return _cfg().MULTI_PARSER_SYSTEM_PROMPT


@mcp.prompt()
def multi_parser_agent() -> str:
    """System prompt for the multi-path parser (all viable execution paths)."""
    return _cfg().MULTI_PARSER_SYSTEM_PROMPT


@mcp.prompt()
def api_agent() -> str:
    """System prompt for the API agent (endpoint + request body builder)."""
    return _cfg().API_AGENT_SYSTEM_PROMPT


@mcp.prompt()
def chatter_agent() -> str:
    """System prompt for the chatter agent (narrative answer generation)."""
    return _cfg().CHATTER_SYSTEM_PROMPT


@mcp.prompt()
def memory_agent() -> str:
    """System prompt for the memory agent (follow-up Q&A from saved results)."""
    return _cfg().MEMORY_SYSTEM_PROMPT


@mcp.prompt()
def graph_agent() -> str:
    """System prompt for the graph agent (Cypher query generation)."""
    return _cfg().GRAPH_AGENT_SYSTEM_PROMPT


@mcp.prompt()
def reporter_agent() -> str:
    """System prompt for the reporter agent (project/date-range extraction)."""
    return _cfg().REPORTER_SYSTEM_PROMPT


@mcp.prompt()
def report_writer_agent() -> str:
    """System prompt for the report writer agent (GEO / generic report JSON)."""
    return _cfg().REPORT_WRITER_SYSTEM_PROMPT


@mcp.prompt()
def system_agent() -> str:
    """System prompt for the system agent (capabilities / entity / search meta-queries)."""
    return _cfg().SYSTEM_AGENT_SYSTEM_PROMPT


@mcp.prompt()
def planner_agent() -> str:
    """System prompt for the planner agent (Opus + thinking; multi-step plan)."""
    return _cfg().PLANNER_SYSTEM_PROMPT


@mcp.prompt()
def context_engineer() -> str:
    """System prompt for the context engineer (Python snippet generation between plan steps)."""
    return _cfg().CONTEXT_ENGINEER_SYSTEM_PROMPT


@mcp.prompt()
def evaluator() -> str:
    """System prompt for the final planner evaluator."""
    return _cfg().EVALUATOR_V1_SYSTEM_PROMPT


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def run_query(query: str) -> str:
    """
    Run a natural language query through the full multi-agent pipeline.

    USE THIS for live data questions: finding/searching/retrieving actual samples,
    assays, or studies; follow-up questions on previous results; graph traversals
    over real records; GEO/report generation for specific UIDs.

    DO NOT use this for definitional or catalog questions ("what is D.SEQ?",
    "what metadata does X have?", "what endpoints exist?") — use lookup_resource
    instead, which is instant and does not spin up the agent pipeline.

    Pipeline: entity extraction → parser (mode + endpoint) → mode branch
    (API search / reporter SQL / graph Cypher / memory follow-up / system meta)
    → chatter narrative.
    """
    with _quiet_stdout():
        result = _run_query(_DictSession(), _cfg(), query)
    return result["reply"]


@mcp.tool()
def run_query_plan(query: str) -> str:
    """
    Run a natural language query through the planner pipeline (multi-step capable).

    USE THIS when the query explicitly chains data sources — e.g. "find X then use
    those results to find Y", or when graph UIDs from Neo4j need to scope an API
    filter (or vice versa).  Single-step plans are valid too.

    For straightforward single-source queries, prefer run_query (faster).
    For catalog/schema/definitional questions, use lookup_resource (instant).

    Pipeline: entity → multi-parser → Planner (Opus + high thinking) →
    executor loop (graph / search / reporter / memory steps) → plan chatter.
    """
    with _quiet_stdout():
        result = _run_query_plan(_DictSession(), _cfg(), query)
    return result["reply"]


@mcp.tool()
def extract_entities(query: str) -> str:
    """
    Run only the entity extraction agent against the query.

    Returns a JSON object with:
      sampletypes  — list of {code, name} matched from the catalog
      assays       — list of {code, name} matched from the catalog
      keywords     — list of free-text keywords not mapped to catalog entries

    Useful for debugging what the system resolves before routing.
    """
    with _quiet_stdout():
        result = _entity_agent(_cfg(), query)
    return result.model_dump_json(indent=2)


def _contains_term(obj, term: str) -> bool:
    """Recursively check whether any string value in obj contains term."""
    if isinstance(obj, str):
        return term in obj.lower()
    if isinstance(obj, dict):
        return any(_contains_term(v, term) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_term(item, term) for item in obj)
    return False


@mcp.tool()
def lookup_resource(resource: str, query: str) -> str:
    """
    Search a static context resource for entries matching a query string.

    USE THIS (instead of run_query) for any definitional or schema question:
      - "What is D.SEQ?" / "What metadata attributes does D.SEQ have?"
        → lookup_resource("sampletypes", "D.SEQ")
      - "What assays are available?" / "Tell me about RNA-seq"
        → lookup_resource("assays", "RNA-seq")
      - "What API endpoints exist?" / "What does the advanced_search endpoint do?"
        → lookup_resource("endpoints", "advanced_search")
      - "What can NExtSEEK do?" / "What are the system capabilities?"
        → lookup_resource("capabilities", "graph")
      - "What graph relationships exist?" / "What is DERIVED_FROM?"
        → lookup_resource("neo4j-schema", "DERIVED_FROM")

    This is instant — it does NOT call any LLM or agent pipeline.

    resource — one of:
      sampletypes       full sampletype catalog (code, name, description, parents, aliases …)
      assays            full assay catalog (code, name, assay_type, platform …)
      endpoints         API endpoint catalog (path, method, description, intent_patterns …)
      graph-schema      minimal Neo4j schema used for parser routing
      neo4j-schema      full Neo4j schema (node labels, relationship types, properties)
      assay-connections assay → parent_type → child_type DERIVED_FROM mappings
      protocol-schema   protocol title vocabulary
      capabilities      user-facing capabilities reference doc (markdown, line-based search)

    For JSON list resources (sampletypes, assays, endpoints): returns all top-level items
    where any string value anywhere in the item contains the query.

    For JSON dict resources (graph-schema, neo4j-schema, assay-connections, protocol-schema):
    returns all top-level keys whose subtree contains the query.

    For text resources (capabilities): returns matching lines with 2 lines of context.
    """
    import json as _json

    filename = _CONTEXT_MAP.get(resource)
    if not filename:
        raise ValueError(
            f"Unknown resource '{resource}'. "
            f"Available: {', '.join(_CONTEXT_MAP)}"
        )

    path = Path(_cfg().CONTEXT_DIR) / filename
    raw = path.read_text()
    term = query.lower()

    # ── Markdown / plain text ─────────────────────────────────────────────────
    if filename.endswith(".md"):
        lines = raw.splitlines()
        results, seen = [], set()
        for i, line in enumerate(lines):
            if term in line.lower():
                block = lines[max(0, i - 2):i + 3]
                key = "\n".join(block)
                if key not in seen:
                    seen.add(key)
                    results.append({"line": i + 1, "context": block})
        return _json.dumps({"found": len(results), "results": results}, indent=2)

    # ── JSON ──────────────────────────────────────────────────────────────────
    data = _json.loads(raw)

    if isinstance(data, list):
        matches = [item for item in data if _contains_term(item, term)]
        return _json.dumps({"found": len(matches), "results": matches}, indent=2, default=str)

    if isinstance(data, dict):
        matches = {k: v for k, v in data.items() if _contains_term(v, term)}
        return _json.dumps({"found": len(matches), "results": matches}, indent=2, default=str)

    return _json.dumps({"found": 0, "results": []})


@mcp.tool()
def get_endpoint_schema(endpoint: str) -> str:
    """
    Return the field schema for a NExtSEEK API endpoint.

    Use this before calling direct_api_request to find out what request body
    fields and query parameters the endpoint accepts, their types, and which
    are required.

    endpoint should be the path as listed in the endpoints resource, e.g.:
      /nextseek_api/samples/advanced_search/
      /nextseek_api/admin/samples/retrieve/

    Returns the schema dict, or a not-found message if the endpoint is unknown.
    """
    import json as _json
    schema = _cfg().get_schema_for_endpoint(endpoint)
    if schema is None:
        return _json.dumps({"found": False, "endpoint": endpoint})
    return _json.dumps({"found": True, "endpoint": endpoint, "schema": schema}, indent=2, default=str)


@mcp.tool()
def direct_api_request(endpoint: str, method: str, body: dict | None = None, query_params: dict | None = None) -> str:
    """
    Call the NExtSEEK REST API directly and return the raw response.

    Use lookup_resource("endpoints", ...) to find available endpoints and their
    intent patterns. Use get_endpoint_schema(endpoint) to inspect required fields
    before constructing the request body.

    The request is validated against the API schema before being sent —
    invalid bodies are rejected with a descriptive error rather than forwarded.

    endpoint     — API path, e.g. /nextseek_api/samples/advanced_search/
    method       — HTTP method: GET, POST, PUT, PATCH
    body         — JSON request body (for POST/PUT)
    query_params — URL query parameters (page_size defaults to 1000)

    Returns {ok, status_code, url, data, ...} from the live API.
    """
    import json as _json
    with _quiet_stdout():
        result = tool_nextseek_api_request(
            _cfg(),
            endpoint=endpoint,
            method=method,
            requestBody=body or {},
            queryParameters=query_params,
        )
    return _json.dumps(result, indent=2, default=str)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
