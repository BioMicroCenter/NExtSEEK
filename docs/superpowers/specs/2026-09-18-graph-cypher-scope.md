# Project scope on the graph agent's Cypher (design)

- Date: 2026-09-18
- Branch: `feat/graph-cypher-scope`, cut from `dev-graph` at `10bea6d6`
- Status: design, not built. Implements task 7.1 of the Nessie plan, the only deployment gate left on the graph path.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Supersedes: section 8.1 ("Stage A1") of [`2026-09-15-graph-search-nessie-design.md`](2026-09-15-graph-search-nessie-design.md)
  wherever the two differ.
- Builds on: `docs/neo4j-schema.md` ("v1.1", "v1.2") and `nextseek_api/graph_search/README.md`.

## 1. Goal and acceptance

The graph now carries every sample's metadata on its node, and the graph agent's Cypher reaches Neo4j with no
project filter: `tool_neo4j_query(config, cypher, parameters)` refuses writes and nothing else, and
`helpers/tools/neo4j.py` never mentions `project_ids`. Under a superuser the gap does not show; under anyone else,
every graph answer reads every project.

After this change, for a caller who is not a superuser, every statement that reaches Neo4j through the tool either
touches only nodes the caller may see, or is not run and the question is answered by `graph_search` instead.

**Acceptance (the operator's):** a real non-member sees zero foreign rows, and a write is refused. The live check
(a non-superuser test account that belongs to two projects, against a superuser account, after a rebuild) is the
operator's step. This build proves the same property on a throwaway Neo4j holding a synthetic two-project graph
(section 11.2), through the real tool path.

## 2. What the code does today (verified in this tree)

### 2.1 Every site that runs Cypher

| Site | What runs | Caller-controlled? |
|---|---|---|
| `orchestrator.py:447` `_run_followup_agent._run_query` | graph agent Cypher for a follow-up | yes |
| `orchestrator.py:527` `_execute_graph_turn` | graph agent Cypher, first try | yes |
| `orchestrator.py:576` `_execute_graph_turn` | graph agent Cypher, retries | yes |
| `agents/planner/tools.py:67`, `:75` | graph agent Cypher in legacy plan mode, first try and one retry | yes |
| `NessieAI/ns/granular.py:72-94` `_graph` | graph agent Cypher for the CC `graph` op, through the injected `neo4j_exec` (the view never injects one, so it is `tool_neo4j_query`) | yes |
| `reports/runners.py:148` `_neo4j_investigation_sample_uuids` | templated: investigation to study to sample, date filters | template, values are parameters |
| `reports/runners.py:692` `run_project_published_report` | templated: same traversal, a title hint and date filters | template, values are parameters |
| `scripts/graph_search/nessie_venue_check.py:956` | a fixed probe through the tool (operator's venue check) | no |
| `graph_catalog.py:271-280` `_read` | fixed catalog statements (`META`, `INDEX`, `GUARD`, `TYPES_ADMIN`, `VOCAB_*`) in READ transactions; output is rendered into the agent's context | no |
| `config.py:1643` `_connect_neo4j` | nothing: the method has no caller | no |

Every caller-controlled statement already funnels through `tool_neo4j_query`, so enforcing there covers all of them.
The Django side (`nextseek_api/graph_search/`, `graph_sync/`, `services/entity_tree.py`, `sampletype_connections.py`,
`sample_types.py`, `sample_workbook.py`, `views.py`, `seek/sample/trees.py`, the management commands) runs its own
fixed Cypher behind its own endpoint authorization and never runs model-written text; it is out of scope except for
`graph_search`'s lineage predicate (section 7.3).

### 2.2 Every entry point that makes a per-request config and can reach the tool

| Entry point | How the config copy is made today | Reaches |
|---|---|---|
| `nextseek_api/services/assistant.py` `AssistantViewSet.query` (SSE) | `_select_chat_config`, then `NessieAI/ns/turn.py::run_sse_pipeline` calls `run_query` / `run_query_plan`, whose `_identity_gate` (`orchestrator.py:183`) makes the copy | graph turn, follow-up, planner, reports |
| `AssistantViewSet.query_async` | same, through `run_async_pipeline` (also `run_pipeline_launch`) | same |
| `AssistantViewSet._run_granular_op` | `_granular_chat_config` (`assistant.py:230`) copies and sets the credentials | `graph` op, `graph-schema` op, `report` op |
| `nextseek_api/services/cc_assistant.py` `CCAssistantViewSet._start_task` | `NessieAI/cc/turn.py::start_task`, which wraps the evaluation copies (`_eval_config`) and calls `run_query` / `run_query_plan` for the NS route | same as `query` |
| The CC container's ops | come back over HTTP to `_run_granular_op` with the user's own credentials | the granular row above |
| `nextseek_api/services/evaluator.py` retry | `NessieAI/ns/retry.py::run_retry` calls `run_query` / `run_query_plan` on `settings.NEXTSEEK_CHAT_CONFIG` | same as `query` |
| `NessieAI/chat_nextseek/cli.py` `cmd_query` (`:246`), `cmd_query_plan` (`:284`) | builds its own `ChatConfig`, no credentials | same |
| `NessieAI/chat_nextseek/mcp_server.py` `_cfg()` (`:122`) | one process-wide `ChatConfig`; also calls `graph_catalog.get_snapshot` directly (`:163`) | same, plus the catalog |
| `NessieAI/chat_nextseek/app.py` (`:18`) | its own `ChatConfig` | same |
| `chat_nextseek/evaluator/runner.py` (`:180`), `evaluator/demo/server.py` (`:346`) | their own `ChatConfig` | same |
| `scripts/graph_search/nessie_venue_check.py` | its own config, calls the tool and the catalog directly | tool, catalog |

The management command `nessie` and both evaluation harnesses drive HTTP as a named user, so they arrive through
the Django rows above. Nothing else constructs a config that reaches Cypher.

### 2.3 Where the scope already exists

`nextseek_api/graph_search/scope.py::resolve_scope(user)` returns `Scope(is_admin, person_id, project_ids)`: a
superuser is admin, `is_staff` is not, a non-admin's projects come from MySQL membership, and an empty set means
"sees nothing". `graph_search` applies it as `_SCOPE_MATCH = "any(p IN s.project_ids WHERE p IN $projects)"`
(`query.py:59`). Per-request switches already ride on the config copy (`FORCE_PARSER_MODE`,
`EXTRA_ALLOWED_PROCEDURES`), and `NessieAI/tests/api/test_nessie_boundaries.py` forbids `NessieAI/ns` and
`NessieAI/cc` from importing `nextseek_api.graph_search`, so the scope must be resolved in the ViewSets and handed in
as plain data.

### 2.4 Two facts that shape the prover

1. **The existing scanner is a finder, not a recognizer.** `agents/graph.py::_scan` collects what its regexes match
   and skips the rest, which is right for repair hints (a miss costs a hint). A proof must fail on what it does not
   recognize. `_NODE_PATTERN_RE` (`graph.py:351`) does not match a node pattern with an inline `WHERE`, or a property
   map containing braces, so a scope proof built on it would leave such a node unscoped.
2. **`mask_cypher` and Cypher disagree about backticked names.** `cypher_text.mask_cypher` treats a backslash inside
   a backticked name as an escape; Cypher does not (a backticked name ends at the first unpaired backtick, and a
   doubled backtick is a literal one). Measured on this tree: text after a backticked name ending in a backslash is
   blanked up to the next backtick, so `write_clause` does not see a clause that Neo4j does. The READ transaction
   still stops a write, but a proof that trusted the mask for "where is code" could be walked past.

So the prover has its own lexer that follows Cypher's rules, and a recognizer that accepts only the grammar in
section 5.3 and refuses everything else. `_scan` is reused where it is sound: as a test oracle (section 11.1). The
tool keeps calling `write_clause` first, unchanged, and `mask_cypher` gets the backtick fix (unit `prover`).

## 3. Decisions

### 3.1 The operator's (fixed; not reopened here)

1. The graph agent keeps writing free Cypher for everyone. For a non-superuser, the server injects the project
   scope and a deterministic prover checks, in code, that every node the query can touch is scoped. What the prover
   cannot prove is refused, and the question falls back to `graph_search` with a note in the reply. Superusers are
   unchanged.
2. The scope clause is `graph_search`'s, `any(p IN s.project_ids WHERE p IN $projects)`: one definition, or two
   pinned equal by a test. `chat_nextseek` never imports `nextseek_api`.
3. Lineage stops at the caller's project edge: every Sample-capable variable, every node on a variable-length path
   and every fulltext hit is scoped, so a parent in a project the caller cannot see is invisible.
4. A non-superuser's schema context carries no cross-project values or counts (names, types and structure stay),
   in the graph agent's catalog rendering and in the CC `nextseek-graph-schema` op.
5. Fail closed everywhere: no scope refuses; the scope is server-supplied plain data `{is_admin, project_ids}` from
   `resolve_scope` on the per-request config copy; an empty project set sees nothing; `is_staff` is not admin;
   single-operator surfaces run as admin only by an explicit opt-in.
6. The scope parameter name is reserved: a model-supplied parameter with that name is refused, never merged.
7. Every statement still runs in `execute_read`; the debug payload records the Cypher that actually ran and the
   scope decision.

### 3.2 Taken by this spec

| # | Decision | Why |
|---|---|---|
| S1 | The prover is a complete recognizer with its own Cypher lexer, not a regex finder (2.4). Output is the input plus insertions only | a proof must fail on the unrecognized; insert-only output makes "remove the insertions, get the input back" a testable property |
| S2 | Scope clause text: `SCOPE_CLAUSE_TEMPLATE = "any({element} IN {var}.project_ids WHERE {element} IN ${param})"`, rendered with generated names; an api-lane test renders it with `p`, `s`, `projects` and asserts equality with `graph_search`'s `_SCOPE_MATCH` | decision 2 without an import; generated names cannot collide with the model's `p` |
| S3 | Reserved names: parameter `__scope_projects`; every identifier the prover generates starts with `__scope`; any `__scope` name in the model's text or parameter keys refuses (case-insensitive) | decision 6; double-underscore identifiers already run on this Neo4j (`_probe_total` aliases `__total`) |
| S4 | Node policy by label, per table 5.4: Sample-capable nodes get the clause; `Project` gets `id IN $__scope_projects`; `Study`, `Investigation` and `Person` must be joined by a relationship pattern to a node that is visible or already bound; `SampleType`, `Attribute`, `GraphMeta`, `OrphanSample` and unknown labels refuse | decision 1 ("every node the query can touch is scoped") for nodes that carry no `project_ids`; catalog nodes carry cross-project statistics (decision 4) |
| S5 | A Study, Investigation or Person joined to a visible node is shown whole | it is the container of something the caller may see; recorded as an accepted residual (section 14) |
| S6 | `parent_titles` and `parent_title_hashes` are hidden for non-admins: refused in every property position, and stripped from any whole node in the result | they are computed from the parent's own metadata, which may belong to a foreign project (decision 3) |
| S7 | A refusal is final for the turn: no agent retry on a scope refusal; the NS graph turn falls back to `graph_search` through the existing REST branch | decision 1; a retry is a paid call that can only produce another unprovable query |
| S8 | Redaction lives in `graph_catalog.get_snapshot` / `get_type_details`, keyed on the config's scope, so `agents/graph.py` and `graph_context.py` change by zero lines | two unmerged branches edit those files |
| S9 | `graph_search`'s lineage extension scopes every node on its path for non-admins (7.3) | the fallback target must honour decision 3 too |
| S10 | Single-operator opt-in: `CHAT_NEXTSEEK_GRAPH_ADMIN=1` in the process environment, or `--graph-admin` on the CLI. Without it those surfaces carry no scope: graph queries refuse and fall back, and the catalog is redacted | decision 5; nothing in a served process reads the variable |

## 4. GraphScope: the data and where it is set

### 4.1 The type (new module `NessieAI/chat_nextseek/src/chat_nextseek/graph_scope.py`)

```python
SCOPE_ATTR = "GRAPH_SCOPE"            # the attribute on a per-request ChatConfig copy
SCOPE_PARAM = "__scope_projects"      # the one parameter the server binds; reserved
RESERVED_PREFIX = "__scope"           # every generated name; reserved in model text and parameter keys
OPERATOR_OPT_IN_ENV = "CHAT_NEXTSEEK_GRAPH_ADMIN"
HIDDEN_SAMPLE_PROPERTIES = frozenset({"parent_titles", "parent_title_hashes"})


@dataclass(frozen=True)
class GraphScope:
    """Who is asking: an unscoped admin, or a caller limited to project_ids. Empty project_ids sees nothing."""

    is_admin: bool
    project_ids: tuple[int, ...] = ()
    source: str = "request"           # "request", "cli", "mcp", "app", "evaluator", "venue-check", "test"

    @classmethod
    def admin(cls, source: str) -> "GraphScope": ...
    @classmethod
    def for_projects(cls, project_ids: Iterable[int], source: str = "request") -> "GraphScope": ...
    @classmethod
    def from_plain(cls, data: Mapping[str, Any], source: str = "request") -> "GraphScope":
        """{"is_admin": bool, "project_ids": [int, ...]} as resolve_scope's caller builds it.
        Raises ValueError unless is_admin is a real bool and every id a real int (not a bool); an admin's ids are
        dropped; ids are sorted and de-duplicated."""


def scope_of(config: Any) -> GraphScope | None:
    """The GraphScope on this config, or None when the attribute is absent or is anything but a GraphScope
    (a MagicMock config, a dict, a string). None means refuse."""

def with_scope(config: Any, scope: GraphScope | None) -> Any:
    """A shallow copy of config carrying scope. Never mutates config. None is stored and refuses."""

def sees_all(config: Any) -> bool:
    """True only when scope_of(config) is an admin scope. The catalog's redaction switch."""

def operator_scope_from_env(source: str, environ: Mapping[str, str] | None = None) -> GraphScope | None:
    """Single-operator surfaces only: GraphScope.admin(source) when OPERATOR_OPT_IN_ENV is exactly "1", else None."""
```

`GraphScope` is plain data: two fields and a label, frozen, no behaviour beyond validation. It is shared by
reference across `copy.copy(config)`, which is safe because it is immutable.

### 4.2 Where it is set, per entry point

| Entry point | Change |
|---|---|
| `nextseek_api/graph_search/scope.py` | new `plain_scope(user) -> Optional[dict]`: `resolve_scope(user)` as `{"is_admin", "project_ids"}`; `None` (logged) on `ScopeUnavailable` or any database error |
| `AssistantViewSet.query`, `query_async` | `graph_scope = plain_scope(request.user)` in the request thread; passed to `run_sse_pipeline` / `run_async_pipeline` as `graph_scope=` |
| `NessieAI/ns/turn.py` `run_sse_pipeline`, `run_async_pipeline` | new keyword `graph_scope=None`, passed to `run_query`, `run_query_plan`, `run_pipeline_launch` |
| `AssistantViewSet._granular_chat_config` | after the credential copy: `cfg = with_scope(cfg, GraphScope.from_plain(p) if p else None)` with `p = plain_scope(request.user)`; a malformed value stores `None` |
| `CCAssistantViewSet._start_task` | resolves `plain_scope(request.user)`, passes `graph_scope=` to `start_task` |
| `NessieAI/cc/turn.py` `start_task` | new keyword `graph_scope=None`, passed to `run_query` / `run_query_plan` on the NS route |
| `nextseek_api/services/evaluator.py` retry | resolves, passes `graph_scope=` to `run_retry` |
| `NessieAI/ns/retry.py` `run_retry` | new keyword `graph_scope=None`, passed through |
| `orchestrator.py` `run_query`, `run_query_plan`, `run_pipeline_launch` | new keyword `graph_scope` (default: a module sentinel `_UNSET`), handed to `_identity_gate` |
| `orchestrator.py` `_identity_gate` | when `graph_scope` is not `_UNSET`: make the copy if it has not already, and set `GRAPH_SCOPE` to `graph_scope` if it is a `GraphScope`, `GraphScope.from_plain(graph_scope)` if it is a mapping, else `None` (a `ValueError` stores `None` and logs). `_UNSET` leaves the config's own value: that is how single-operator surfaces set theirs |
| `cli.py` `cmd_query`, `cmd_query_plan` | `--graph-admin` flag; `config = with_scope(config, GraphScope.admin("cli") if flag else operator_scope_from_env("cli"))`; without either, one stderr line says graph queries will fall back and the catalog is redacted |
| `mcp_server.py` `_cfg()` | `with_scope(ChatConfig(), operator_scope_from_env("mcp"))` |
| `app.py`, `evaluator/runner.py`, `evaluator/demo/server.py` | same, with their own `source` |
| `scripts/graph_search/nessie_venue_check.py` | same (`"venue-check"`); `nessie_venue.sh check` exports `CHAT_NEXTSEEK_GRAPH_ADMIN=1` for that step only |

The Django singletons (`settings.NEXTSEEK_CHAT_CONFIG`, `NEXTSEEK_CHAT_CONFIG_PROD`) never carry a scope; a test pins
`scope_of(...) is None` for both. The PROD config is chosen only for a superuser (`_select_chat_config`), whose scope
is admin, so no non-admin ever reads the PROD graph.

### 4.3 Fail-closed rules, in one place

- No attribute, a non-`GraphScope` value, or `None`: the tool refuses before a driver opens, and the catalog redacts.
- `plain_scope` failure: `None`, so the same.
- A non-admin with no projects: the prover runs and binds `[]`, so every scoped node is invisible and the query
  returns nothing of anyone's.
- `is_staff`: never read. `resolve_scope` keys on `is_superuser is True` alone.
- A caller that forgets `graph_scope=`: `run_sse_pipeline`, `run_async_pipeline`, `run_retry` and `start_task`
  default to `None`, which refuses; only single-operator surfaces use `_UNSET`, and only their own configs carry a
  scope.

## 5. The prover (new module `NessieAI/chat_nextseek/src/chat_nextseek/cypher_scope.py`)

### 5.1 Interface

```python
SCOPE_CLAUSE_TEMPLATE = "any({element} IN {var}.project_ids WHERE {element} IN ${param})"
PROJECT_CLAUSE_TEMPLATE = "{var}.id IN ${param}"
PATH_CLAUSE_TEMPLATE = "all({node} IN nodes({path}) WHERE " \
                       "any({element} IN {node}.project_ids WHERE {element} IN ${param}))"
MAX_CYPHER_CHARS = 20_000
MAX_NESTING = 32


@dataclass(frozen=True)
class Scoped:
    cypher: str                        # the statement to run: the input plus insertions only
    parameters: dict[str, Any]         # the input's parameters, plus {SCOPE_PARAM: list(project_ids)} for a non-admin
    decision: Literal["admin", "proven"]
    injected: tuple[str, ...] = ()     # one line per predicate added, e.g. "s: sample clause", "__scope_path1: every node"
    joined: tuple[str, ...] = ()       # one line per joined node, e.g. "st (Study): joined to s"


@dataclass(frozen=True)
class Refused:
    codes: tuple[str, ...]             # section 5.8, in order found
    reasons: tuple[str, ...]           # one short neutral sentence each, with line and column
    decision: Literal["refused"] = "refused"


def scope_cypher(cypher: str, parameters: Mapping[str, Any] | None, scope: GraphScope) -> Scoped | Refused:
    """Pure. Admin: the reserved-parameter check only, then Scoped(cypher unchanged, parameters copied, "admin").
    Non-admin: lex, recognize, inject; any construct outside section 5.3 refuses."""


def strip_hidden(value: Any) -> Any:
    """For a non-admin's result rows: every graph Node (anything with labels and element_id and items()) becomes a
    plain dict of its properties minus HIDDEN_SAMPLE_PROPERTIES; a Relationship becomes a dict of its properties;
    a Path becomes {"nodes": [...], "relationships": [...]}; lists, tuples and dicts are walked. Other values pass."""
```

### 5.2 How it works

1. **Size and parameters.** Over `MAX_CYPHER_CHARS` refuses (`too_long`). A parameter key starting with
   `RESERVED_PREFIX` refuses (`reserved_parameter`) for every caller, admin included. Admin returns here.
2. **Lex** the text by Cypher's rules into tokens with offsets: plain names (`[A-Za-z_][A-Za-z0-9_]*`), backticked
   names (end at the first unpaired backtick, a doubled backtick is a literal one; a backslash inside refuses,
   `lexer`), string literals (single or double quoted, backslash escapes), parameters (`$name` or `$digits`; a
   backticked parameter refuses), numbers, and punctuation. Line and block comments are skipped. Any other
   character outside a literal or comment (non-ASCII letters, non-ASCII whitespace, stray symbols), or an
   unterminated literal, backticked name or comment, refuses (`lexer`). Keywords are matched case-insensitively.
3. **Recognize** with a recursive-descent parser over the tokens (5.3), tracking the names in scope: variables bound
   by patterns, `YIELD`, `UNWIND ... AS`, `WITH` projections and aliases, and the locals of comprehensions,
   quantifiers and `reduce`. Every token must be consumed by a production; an identifier in an expression must be a
   name in scope, a keyword of the grammar, a function in the allowlist, a property key after `.`, a map key, or a
   label after `:`. The character pairs `->`, `<-` and `--` anywhere in an expression refuse
   (`pattern_expression`). Nesting deeper than `MAX_NESTING` refuses (`too_deep`).
4. **Classify** each new node binding by table 5.4 and each relationship by its type; prove joined nodes (5.4).
5. **Inject** by inserting text at offsets (5.6), applied right to left. Generated names are numbered in order of
   appearance and never reused within one statement.

Every refusal is collected, not the first only, so the debug payload shows all the reasons. The function never
raises on any input: an internal error is itself a refusal (`internal`), and a test fuzzes for it.

### 5.3 The grammar it accepts (non-admin)

```
statement      := part                                  ; no UNION, no CYPHER / EXPLAIN / PROFILE prefix
part           := clause* return_clause                 ; must end with RETURN
clause         := match_clause | fulltext_clause | unwind_clause | with_clause
match_clause   := ["OPTIONAL"] "MATCH" pattern_list ["WHERE" expr]
fulltext_clause:= "CALL" "db.index.fulltext.queryNodes" "(" string "," (param | string) ")"
                  "YIELD" yield_item ("," yield_item)* ["WHERE" expr]
yield_item     := ("node" | "score") ["AS" name]        ; node is required; string must be 'sample_search_text'
unwind_clause  := "UNWIND" expr "AS" name
with_clause    := "WITH" ["DISTINCT"] projection [order] [skip] [limit] ["WHERE" expr]
return_clause  := "RETURN" ["DISTINCT"] projection [order] [skip] [limit]
projection     := "*" | item ("," item)*   ;  item := expr ["AS" name]
order          := "ORDER" "BY" expr ["ASC"|"DESC"|"ASCENDING"|"DESCENDING"] ("," ...)*
skip / limit   := "SKIP" expr / "LIMIT" expr

pattern_list   := path ("," path)*
path           := [name "="] node (rel node)*
node           := "(" [name] [labels] [map] ")"          ; no inline WHERE
labels         := ":" label ((":" | "&") label)*         ; plain or backticked names only
rel            := ("-" | "<-") "[" [name] [":" type] [hops] [map] "]" ("-" | "->")   ; exactly one type
hops           := "*" [int] [".." [int]]                  ; DERIVED_FROM only

expr           := or ; or := xor ("OR" xor)* ; xor := and ("XOR" and)* ; and := not ("AND" not)*
not            := "NOT"* comparison
comparison     := additive ( cmp_op additive | "IS" ["NOT"] "NULL" | "IN" additive
                  | "STARTS" "WITH" additive | "ENDS" "WITH" additive | "CONTAINS" additive | "=~" additive )*
additive       := multiplicative (("+" | "-") multiplicative)*
multiplicative := power (("*" | "/" | "%") power)* ; power := unary ("^" unary)*
unary          := ["-" | "+"] postfix                    ; one sign at most
postfix        := atom ( "." prop | "[" int "]" | "[" [int] ".." [int] "]" | map_projection )*
atom           := literal | param | "(" expr ")" | list | comprehension | map | case
                | "count" "(" "*" ")" | call | quantifier | reduce | subquery | name [label_test]
label_test     := ":" label ((":" | "&" | "|") ["!"] label)*   ; a predicate, binds nothing
call           := function "(" ["DISTINCT"] [expr ("," expr)*] ")"   ; function in the allowlist
quantifier     := ("any" | "all" | "none" | "single") "(" name "IN" expr ["WHERE" expr] ")"
reduce         := "reduce" "(" name "=" expr "," name "IN" expr "|" expr ")"
comprehension  := "[" name "IN" expr ["WHERE" expr] ["|" expr] "]"
map_projection := "{" ( "." prop | key ":" expr | name ) ("," ...)* "}"   ; no ".*"
case           := "CASE" [expr] ("WHEN" expr "THEN" expr)+ ["ELSE" expr] "END"
subquery       := ("EXISTS" | "COUNT") "{" ( pattern_list ["WHERE" expr]
                                            | ("MATCH" pattern_list ["WHERE" expr])+ ) "}"
```

Subscripts take integer literals only (optionally negative): `nodes(p)[0]` and `split(s.uuid, '-')[1]` pass,
`s[$k]` and `s[k]` refuse (`dynamic_property`). Relationship-shaped text in an expression cannot parse as an
expression, and step 3 of 5.2 refuses the arrow pairs outright as well.

**The function allowlist** (`FUNCTION_ALLOWLIST`, case-insensitive, pinned by a test): the aggregates (`avg`,
`collect`, `count`, `max`, `min`, `percentileCont`, `percentileDisc`, `stDev`, `stDevP`, `sum`); the scalar and list
functions `coalesce`, `elementId`, `endNode`, `head`, `id`, `isEmpty`, `isNaN`, `keys`, `labels`, `last`, `length`,
`nodes`, `nullIf`, `range`, `relationships`, `reverse`, `size`, `startNode`, `tail`, `type`, `valueType`, the
`to*`/`to*OrNull`/`to*List` conversions; the math functions; the string functions (`left`, `right`, `ltrim`,
`rtrim`, `trim`, `btrim`, `lower`, `upper`, `toLower`, `toUpper`, `replace`, `split`, `substring`, `normalize`,
`char_length`, `character_length`); the temporal constructors and their namespaced forms (`date.truncate`,
`datetime.fromepoch`, `duration.between`, ...); and, when APOC is loaded, only the pure families `apoc.text.*`,
`apoc.coll.*`, `apoc.number.*`, `apoc.math.*`, `apoc.date.*`, `apoc.temporal.*`. Not allowed: `properties`, any
`db.*`, `graph.*` or `vector.*` function, every other `apoc.*` function, the removed `exists()` function, and
anything else (`function_not_allowed`).

### 5.4 Node and relationship policy

| Node binding | Kind | What the prover does |
|---|---|---|
| every label is `Sample` or a `T_` label | sample | the scope clause on it |
| no label, as an endpoint of a `DERIVED_FROM` pattern | sample | the scope clause (both ends of `DERIVED_FROM` are samples or orphans; an orphan without `project_ids` is invisible) |
| exactly `Project` | project | `{var}.id IN $__scope_projects` |
| exactly one of `Study`, `Investigation`, `Person` | joined | no predicate; must be joined (below), else `unjoined_node` |
| `SampleType`, `Attribute`, `GraphMeta`, `OrphanSample`, any label not named here, or labels of two kinds together | none | `label_not_allowed` |
| no label anywhere else | none | `unlabelled_node` |
| a name already in scope, written as `(name)` or with labels | reference | nothing: every bound value is already visible (invariant below) |

**Joined.** Within one `MATCH`, `OPTIONAL MATCH` or subquery pattern list, draw a graph whose vertices are the node
bindings and references and whose edges are the relationship patterns. A joined-kind node is proven when its
connected component holds a sample or project binding of this clause, or a reference to a name already in scope.
Every row then pairs it with a visible node through a real relationship: a study that holds a visible sample, the
investigation of such a study, a person who is a member of one of the caller's projects. Commas alone do not join
(a cartesian product with an unrelated study refuses).

**The invariant the proof rests on.** A node or relationship value can enter a row only through a pattern binding
(scoped or proven by this table), a fulltext `YIELD node` (scoped), or an expression over values already in rows
(the allowlist holds no function that fetches or walks the graph; `startNode`, `endNode`, `nodes` and
`relationships` return elements of matched patterns, which are scoped). Parameters are JSON and cannot hold nodes.
So every bound name is visible, which is why a reference needs no predicate and why aliases (`WITH s AS t`,
`collect`, `UNWIND nodes(p)`, `CASE`) need no tracking beyond names in scope.

| Relationship | Allowed |
|---|---|
| `DERIVED_FROM` | fixed or variable length; any direction |
| `IN_STUDY`, `IN_INVESTIGATION`, `IN_PROJECT`, `MEMBER_OF` | fixed length only |
| untyped, a type alternation, `OF_TYPE`, `HAS_ATTRIBUTE`, `USED_IN`, anything else | `relationship_type` |
| variable length on any other type, or a path part that mixes a variable-length `DERIVED_FROM` with other types | `variable_length` |

### 5.5 Construct decisions

| Construct | Decision |
|---|---|
| unlabelled node patterns | accepted only as a `DERIVED_FROM` endpoint (scoped as a sample) or as a reference; refused elsewhere |
| fulltext `YIELD` | accepted for `db.index.fulltext.queryNodes` with the literal index `sample_search_text` and exactly two arguments, when `YIELD` names `node`; the clause is added to `YIELD`'s `WHERE`. `YIELD *`, a `YIELD` without `node`, a third (options) argument, another index or a parameterised index name refuse (`fulltext_form`) |
| `CALL { }` and `CALL (x) { }` subqueries | refused (`call_subquery`) |
| pattern comprehensions and pattern predicates in `WHERE` | refused (`pattern_expression`); the agent is taught `EXISTS { }` |
| `EXISTS { }` and `COUNT { }` | accepted in both forms (a bare pattern list with optional `WHERE`, or one or more `MATCH ... WHERE`), scoped like a `MATCH`; the bare form is rewritten to the `MATCH` form so it can carry a `WHERE`. No `OPTIONAL MATCH`, `WITH`, `UNWIND`, `RETURN`, `UNION` or `CALL` inside |
| `COLLECT { }` | refused (`collect_subquery`) |
| path functions `nodes()`, `relationships()`, `length()` | accepted on a path the prover scoped; a path with a variable-length part gets the path clause over every node |
| `shortestPath`, `allShortestPaths`, `SHORTEST`, `ANY`, quantified path patterns, match modes | refused (`path_selector`) |
| `OPTIONAL MATCH` | accepted; the scope joins the optional pattern's `WHERE`, so a foreign node does not match and reads as null |
| `UNION`, `UNION ALL` | refused (`union`) |
| `WITH` and alias chains | accepted: scope attaches at the binding, so what `WITH` carries is already scoped |
| procedures a variant allows (`apoc.path.subgraphNodes`, `spanningTree`, `expandConfig`) | refused for non-admins (`procedure`): expansion walks before any predicate can run. Variants are superuser-only today, so no non-admin is taught them |
| any other `CALL` of a procedure | refused (`procedure`); `write_clause` refuses it earlier anyway |
| label expressions with `\|`, `!`, `%` or a dynamic label in a pattern | refused (`label_expression`); in a `WHERE` label test `\|` and `!` are allowed, since a test binds nothing |
| inline `WHERE` in a node or relationship pattern | refused (`inline_where`) |
| `properties()`, a map projection `.*`, dynamic subscripts | refused (`whole_properties`, `dynamic_property`) |
| a hidden property in a property position (`.parent_titles`, `{.parent_titles}`, a pattern map key) | refused (`hidden_property`); an output alias of that name is fine |
| `CYPHER`, `EXPLAIN`, `PROFILE` prefixes | refused (`query_prefix`) |
| `FILTER`, `LET`, `NEXT`, `WHEN`, `FINISH` and any clause not in 5.3 | refused (`syntax`) |
| write clauses | refused before the prover by `write_clause`, unchanged |

### 5.6 What is injected

Templates render with `param = SCOPE_PARAM` and fresh names `__scope_p<k>` (clause element), `__scope_m<k>` (path
node), `__scope_n<k>` (a name given to an anonymous node that needs a predicate) and `__scope_path<k>`.

| Where | Insertion |
|---|---|
| a `MATCH` / `OPTIONAL MATCH` with new sample or project bindings | its `WHERE` becomes `WHERE (<model's predicate>) AND <clauses>`; with no `WHERE`, ` WHERE <clauses>` goes after the pattern list |
| a path part with a variable-length relationship | `__scope_path<k> = ` before it (or the model's own path name), and the path clause over `nodes(...)`; nodes inside that part get no separate clause |
| an anonymous node that needs a predicate | a generated name inside its parentheses |
| a bare `EXISTS { pattern }` / `COUNT { pattern }` | `MATCH ` after the brace, then its `WHERE` as above |
| a fulltext `YIELD` | its `WHERE` as above, on the node's name (`node` when not aliased) |

Examples (inserted text is everything that starts with `__scope`, plus the `(`, `) AND`, `MATCH` and `WHERE` that
carry it):

```
-- in
MATCH (s:T_SLD) WHERE EXISTS { (s)-[:DERIVED_FROM*1..12]->(:T_MUS) } RETURN count(s) AS n
-- out
MATCH (s:T_SLD) WHERE (EXISTS { MATCH __scope_path1 = (s)-[:DERIVED_FROM*1..12]->(:T_MUS)
  WHERE all(__scope_m1 IN nodes(__scope_path1) WHERE any(__scope_p1 IN __scope_m1.project_ids
  WHERE __scope_p1 IN $__scope_projects)) }) AND any(__scope_p2 IN s.project_ids WHERE __scope_p2 IN $__scope_projects)
RETURN count(s) AS n

-- in
MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
WHERE toLower(st.title) CONTAINS toLower($project)
   OR EXISTS { MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation) WHERE toLower(inv.title) CONTAINS toLower($project) }
RETURN count(DISTINCT s) AS n
-- out: WHERE (<the model's predicate>) AND any(__scope_p1 IN s.project_ids WHERE __scope_p1 IN $__scope_projects)
-- joined: "st (Study): joined to s", "inv (Investigation): joined to st"

-- in
MATCH (s:T_TIS {uuid: $uid}) OPTIONAL MATCH (s)-[:DERIVED_FROM]->(parent:Sample) RETURN s.uuid, parent.uuid
-- out: the MATCH gets the clause on s; the OPTIONAL MATCH gets " WHERE any(... parent.project_ids ...)",
-- so a parent in a foreign project comes back as null

-- in
CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s, score WHERE s:T_MUS
RETURN s.uuid AS uuid ORDER BY score DESC LIMIT 5000
-- out: ... YIELD node AS s, score WHERE (s:T_MUS) AND any(__scope_p1 IN s.project_ids WHERE ...)
```

`tool_neo4j_query`'s total probe (`_probe_total`) wraps the statement that ran, so the probe is scoped too, and its
`CALL () { }` is the tool's own text, never the model's.

### 5.7 Hidden properties and the result sanitizer

Name refusal covers every way to reach `parent_titles` by name (property access, map projection item, pattern map
key); refusing `properties()`, `.*`, dynamic subscripts and every graph-reading function covers the ways around the
name. A whole node can still be returned (`RETURN s`, `collect(s)`, `CASE ... END`), and the agent's own whole-node
guard sits in `agents/graph.py`, which the report runners and the CC op do not pass through. So the tool runs
`strip_hidden` over a non-admin's rows before returning them. Admin rows are untouched.

### 5.8 Refusal codes

`too_long`, `too_deep`, `lexer`, `syntax`, `internal`, `reserved_name`, `reserved_parameter`, `query_prefix`,
`union`, `call_subquery`, `collect_subquery`, `procedure`, `fulltext_form`, `pattern_expression`, `inline_where`,
`label_not_allowed`, `label_expression`, `unlabelled_node`, `unjoined_node`, `relationship_type`,
`variable_length`, `path_selector`, `hidden_property`, `dynamic_property`, `whole_properties`,
`function_not_allowed`. The tool adds `no_scope` (no scope on the config). A write is not a scope refusal: it keeps
today's refusal text and today's retry behaviour, and its `scope` reads `{"decision": "not_checked", "codes":
["write"]}`.

## 6. Enforcement in `tool_neo4j_query`

### 6.1 Order

1. `write_clause` (unchanged). A write is refused before anything else and never opens a driver.
2. `scope = graph_scope.scope_of(config)`; `None` refuses with `no_scope`, before a driver opens.
3. `outcome = cypher_scope.scope_cypher(cypher, parameters, scope)`; a `Refused` returns without opening a driver.
4. The existing path, unchanged, on `outcome.cypher` and `outcome.parameters`: the driver, `execute_read` with the
   timeout, the trailing-limit split and total probe.
5. For a non-admin, `strip_hidden` over the records.

The signature stays `tool_neo4j_query(config, cypher, parameters=None) -> dict` (the portable contract,
`NessieAI/tests/chat_nextseek/test_portable_contract.py`).

### 6.2 Result fields

Added to both the success and the failure dict:

| Field | Value |
|---|---|
| `cypher` | the statement that ran (after injection); on a refusal, the submitted text |
| `submitted_cypher` | the text the caller passed, always |
| `parameters` | what ran, including `__scope_projects` for a non-admin (the caller's own ids) |
| `scope` | `decision` (`"admin"`, `"proven"`, `"refused"`, or `"not_checked"` for a write), `source`, `project_ids` (non-admin only), `injected`, `joined`, `codes`, `reasons` |

A refusal is `{"ok": False, "error": <text>, "data": None, "cypher": ..., "submitted_cypher": ..., "scope": {...}}`
with the error text `SCOPE_REFUSED` ("This graph query could not be confirmed to stay within your projects, so it
was not run.") or `NO_SCOPE_REFUSED` ("No project scope is set for this request, so no graph query can run."), then
the reasons. New helper beside `matched_nothing`: `is_scope_refusal(result) -> bool`, true exactly when
`result["scope"]["decision"] == "refused"` (so never for a write).

## 7. The fallback to `graph_search`

### 7.1 The NS graph turn

In `_execute_graph_turn` (`orchestrator.py:491`):

- The generate-execute-read loop breaks on a scope refusal before any retry: a refused first attempt makes no
  agent call; a refused retry after a Cypher error ends the loop with the refusal; a refused retry after a proven
  zero keeps the proven zero, as any failed retry does today.
- When the final result is a scope refusal, the function records the debug fields (section 9) and returns a
  `GraphScopeFallback(codes, reasons, submitted_cypher, attempts)` (a frozen dataclass in `orchestrator.py`) instead
  of calling the chatter and emitting.

In `run_query`, both call sites (the `graph_query` branch and the graph-origin refine) become:

```python
outcome = _execute_graph_turn(...)
if not isinstance(outcome, GraphScopeFallback):
    return outcome
plan = plan.model_copy(update={"mode": "new_search", "target_endpoint": GRAPH_SEARCH_ENDPOINT})
mode = "new_search"
scope_notes = [SCOPE_FALLBACK_NOTE]
# fall through to the REST branch that follows
```

`GRAPH_SEARCH_ENDPOINT = "/nextseek_api/samples/graph_search/"`, already on the API tool's read-POST allowlist
(`helpers/tools/nextseek_api.py:38-43`, task 7.2). The REST branch then runs as it does for any search: the API
agent builds the body against `graph_search`'s live OpenAPI schema (`config.get_schema_for_endpoint`, loaded from
`/nextseek_api/schema/`), the tool posts it with the caller's own credentials, and `graph_search` scopes it on the
server. The REST branch's chatter call passes `query_notes=scope_notes`, and after the chatter the orchestrator
appends one fixed line to the reply, so the disclosure does not depend on the model:

- `SCOPE_FALLBACK_NOTE` (to the chatter): "The graph query written for this question could not be confirmed to stay
  within the user's projects, so it was not run. This answer comes from the project-scoped sample search instead.
  Say so, and say which conditions of the question that search could not apply."
- `SCOPE_FALLBACK_FOOTER` (appended): "Note: this answer comes from the project-scoped sample search, because the
  graph query for it could not be confirmed to stay within your projects. That search cannot express every
  condition a graph query can."

The fallback's API agent and chatter calls are the same paid calls a REST turn makes; the refused query cost no
extra model call.

### 7.2 Other callers

| Caller | On a scope refusal |
|---|---|
| follow-up `run_new_query` seam | returns `{"ok": False, "error": <refusal text>}` as it does for any failure; the follow-up agent answers from the stored result |
| planner graph tool (`agents/planner/tools.py`) | no retry; returns the refusal as the step's error with one added sentence naming `graph_search` as the search to use (legacy plan mode, no automatic fallback) |
| CC `graph` op (`ns/granular.py::_graph`) | returns `{"plan", "result"}` as today; `result` carries `scope`, and its error text names `/nextseek_api/samples/graph_search/` through `nextseek-api-read`, so the CC agent can make the fallback itself |
| report runners | their two statements pass the prover (section 10); a refusal would surface as today's Neo4j error |

### 7.3 `graph_search`'s lineage predicate

`nextseek_api/graph_search/query.py::_LINEAGE_PATTERNS` scopes `s` but not the nodes on its `EXISTS` path. For a
non-admin scope, `_lineage` emits the path form instead, scoping every node on it with the same clause:

```
EXISTS { MATCH lineage_path = (s)<-[:DERIVED_FROM*1..{hops}]-(:{label})
         WHERE all(n IN nodes(lineage_path) WHERE any(q IN n.project_ids WHERE q IN $projects)) }
```

(the ancestor form reverses the arrow). An admin's statement is byte-identical to today's, so the existing query
tests and the parity harness (which runs as admin) do not move.

## 8. Redaction for non-superusers

`graph_catalog.get_snapshot(config)` and `graph_catalog.get_type_details(config, titles)` return redacted copies
(`dataclasses.replace`, never mutating the cache) unless `graph_scope.sees_all(config)`:

| Field | Non-admin |
|---|---|
| `TypeIndexRow.sample_count`, `TypeDetail.sample_count` | `None` (renders as "sample count unknown") |
| `AttributeRow.sample_count` | `None` (no `n=`; attributes then list by title, since fill order is itself cross-project) |
| `AttributeRow.top_values`, `top_counts` | `()` |
| `AttributeRow.num_min`, `num_max`, `date_min`, `date_max` | `None` |
| names, labels, clades, summaries, curated parents and children, value types, meanings, `declared`, `attributes_with_values`, `never_filled`, the guard map, `catalog_hash`, `schema_version`, `synced_at` | unchanged: names, types and structure |
| `Vocabulary` (investigation, project and study titles, published studies, assay and protocol titles, assay-sample connections) | unchanged: names and structure |

Both consumers read through these getters with the per-request config: the graph agent's `live_catalog_context` and
`graph_schema_snapshot` (the CC `nextseek-graph-schema` op) in `agents/graph.py`, and `mcp_server.py`. Neither file
changes. The committed fallback files (`neo4j_schema.json`, `neo4j_protocol_schema.json`,
`neo4j_assay-sample-conn.json`) carry no counts or values, so the fallback context needs nothing.

## 9. Debug payload

| Field | Where | Content |
|---|---|---|
| `debug.graph_result` | unchanged copy of the result minus `data` | now includes `cypher` (ran), `submitted_cypher`, `scope` |
| `debug.graph_attempts[i]` | per attempt | adds `executed_cypher` and `scope_decision` |
| `debug.graph_scope` | the final attempt | the result's `scope` dict |
| `debug.graph_scope_fallback` | only when the turn fell back | `{"endpoint", "codes", "reasons", "submitted_cypher"}` |
| graph debug file (`_write_graph_debug`) | `neo4j_output` | adds `cypher` (ran) and `scope` |
| granular `graph` op | `result` | the tool's dict, so the same fields |

## 10. Templated Cypher in code

| Statement | Outcome |
|---|---|
| `reports/runners.py:142-146`: `(inv:Investigation)<-[:IN_INVESTIGATION]-(study:Study)<-[:IN_STUDY]-(s:Sample)` with `toLower`, `substring(split(s.uuid, '-')[1], ...)` | passes the prover: `s` gets the clause, `study` is joined to `s`, `inv` to `study` |
| `reports/runners.py:684-688`: the same traversal returning `s.uuid`, `study.title`, `s.type` | passes the same way |
| `nessie_venue_check.py` `TOOL_PROBE` | runs under the venue check's explicit admin opt-in |
| `graph_catalog.py` statements | system reads for context, not through the tool; their output is redacted per section 8 |
| `graph_search/query.py` | carries the scope explicitly (`_SCOPE_MATCH`), and after 7.3 on its lineage path too |

The prover unit adds a capture test that runs each report builder with the tool patched and asserts
`scope_cypher` proves exactly what it would run.

## 11. Test plan

Python tests run only in the throwaway lane over an exported tree (`run_lanes.sh`, through `heavy.sh`). Compare
failure sets with the `10bea6d6` baseline (ai 158, api 57) by the documented grep; a set of 0 means collection died.

### 11.1 Prover unit tests (ai lane, pure)

- **Taught shapes are accepted.** Every Cypher shape the two graph agent prompts teach (the default and v2),
  completed with a `RETURN`, as literal strings in the test module; each must be `Scoped` with the expected
  `injected` and `joined` lines. The one taught shape that reads the catalog (`MATCH (a:Attribute) ...`) is on an
  explicit expected-refusal list (decision 4).
- **Refusal table.** One case per row of 5.4 and 5.5 with its expected code, and each again hidden in a comment,
  a string literal, a backticked name, a nested `EXISTS`, and after a `WITH` alias chain (only the real ones refuse).
- **Injection goldens** for about a dozen statements, exact output text.
- **Properties:** deleting the inserted spans reproduces the input byte for byte; every generated name starts with
  `__scope`; output parameters equal input plus `{__scope_projects: [...]}`; an admin gets the input back unchanged;
  a reserved parameter refuses for admin and non-admin; an empty project set injects `[]`; `scope_cypher` never
  raises (seeded mutation fuzz over the accepted corpus: token deletions, insertions and swaps; every mutant is
  `Scoped` or `Refused`).
- **`_scan` as an oracle.** For every accepted corpus statement, every variable `agents.graph._scan` reports as a
  Sample is covered by an injected clause or a path clause in the output.
- **Allowlist pin** for `FUNCTION_ALLOWLIST` and the label and relationship tables.
- **`strip_hidden`** on duck-typed Node, Relationship and Path values nested in lists and maps.
- **`mask_cypher`**: a backticked name ending in a backslash is one name, and the text after it is scanned.
- **Report capture** (section 10).
- **Clause pin (api lane):** `SCOPE_CLAUSE_TEMPLATE.format(element="p", var="s", param="projects")` equals
  `nextseek_api.graph_search.query._SCOPE_MATCH`.

### 11.2 The throwaway-Neo4j lane

`NessieAI/tests/chat_nextseek/graph_scope/lane.sh <exported tree> <outdir>`, run through `heavy.sh`:

1. Exits 0 with `SKIP` when `docker info` fails.
2. Creates a uniquely named network and starts the locally present `neo4j:latest` (`--pull never`, the image the
   stack runs) detached, with a unique name, `--memory 1536m`, a 512m heap and a 128m page cache, a random password
   and no plugins. A `trap` removes the container and the network on every exit.
3. Waits (bounded) for Bolt, then runs pytest on `NessieAI/tests/chat_nextseek/graph_scope/` in the app image on
   that network (`--memory 1536m`, read-only tree, the `schema_rag` directories pre-created), with
   `GRAPH_SCOPE_NEO4J_URI` and `GRAPH_SCOPE_NEO4J_PASSWORD` set. Without those variables every test in the folder
   skips, so the ordinary ai lane (network `none`) reports them skipped.

**The fixture** (`conftest.py`, loaded fresh per caller scope with one write transaction through the lane's own
driver, plus the `sample_search_text` fulltext index): three projects; samples of four types in project 1 only,
project 2 only, both, project 3 only, with `[]`, and with no `project_ids`; `DERIVED_FROM` chains inside a project,
a visible child of a foreign parent, and a foreign sample between two visible ones; orphans with and without
`project_ids`; studies with visible, foreign and mixed samples, their investigations (`project_id` and
`IN_PROJECT`), people with `MEMBER_OF`; catalog `SampleType` and `Attribute` nodes with statistics; a `GraphMeta`
node; `parent_titles` on a visible child naming its foreign parent. Every value only a foreign reader may see
carries one distinctive marker token.

**Callers:** `{1, 3}`, `{2}`, `{}` and admin.

**Arms:** the prover arm (`scope_cypher`, then the lane driver's `execute_read`) and the tool arm
(`tool_neo4j_query` with a config carrying the `GraphScope`). The tool arm needs plumbing; the prover unit commits
it and runs only the prover arm, plumbing runs both.

**Assertions**, for every statement in the battery (the taught corpus, the report statements, the refusal table,
and about 300 statements from a seeded generator over the fixture schema):

- *Differential oracle.* Record each accepted statement's rows for the caller; then delete every Sample and
  OrphanSample the caller cannot see and every Project outside the scope, run the original statement as admin, and
  require the same multiset of rows (fulltext `score` columns excluded).
- *Marker.* No accepted statement's rows, serialized, contain the marker token.
- *Refusals.* Every refused statement's codes equal its expected codes (generator statements may refuse freely).
- *Admin.* Every statement through the tool as admin runs exactly the submitted text and returns what the driver
  returns directly.
- *Writes.* A battery of writes (every write clause, `LOAD CSV`, `FOREACH`, batched `IN TRANSACTIONS`, a write
  procedure, and a write hidden after a backticked name ending in a backslash) is refused through the tool for
  every caller, admin included, and node and relationship counts are unchanged after the whole battery.

Plumbing adds one module to the folder for 7.3: `graph_search`'s built statements with a lineage extension, run for
each caller, under the same oracle.

### 11.3 Redact tests (ai lane)

Getters over a stubbed reader: no scope, a `MagicMock` config and a non-admin scope all get `None` counts, empty top
values and no ranges; admin gets the stored values; an admin call after a non-admin call still gets full values (the
cache is not mutated); the rendered context for a non-admin contains no `n=`, `values:` or `range`; the
`graph-schema` op for a non-admin config returns a schema with none of them.

### 11.4 Plumbing tests (ai and api lanes)

- The tool: no scope, a `MagicMock` config and `None` refuse without opening a driver; a write is refused before the
  scope check; admin runs the submitted text; non-admin runs the injected text with the scope parameter; the total
  probe wraps the injected text; `strip_hidden` runs for non-admins only; refusal dicts carry every field of 6.2.
- The orchestrator (agents and tools stubbed, no model call): a refused first attempt calls the graph agent once and
  falls back; the fallback plan targets `graph_search` with mode `new_search`; the reply ends with
  `SCOPE_FALLBACK_FOOTER`; the chatter receives `SCOPE_FALLBACK_NOTE`; the refine path falls back the same way; a
  proven query never falls back; a write refusal keeps today's retry and never falls back; the debug fields of
  section 9 are present.
- Entry points: each ViewSet hands `plain_scope(request.user)` down (patched); `_granular_chat_config` sets it;
  a superuser is admin, an `is_staff`-only user is not; `ScopeUnavailable` gives `None`; both Django singletons carry
  no scope; the granular view never passes `neo4j_exec`; the CLI without the flag has no scope and with it is admin;
  `CHAT_NEXTSEEK_GRAPH_ADMIN=1` works for MCP, `app.py` and the evaluator and nothing else reads it;
  `GRAPH_SEARCH_ENDPOINT` is on the API tool's read-POST allowlist.
- `NessieAI/tests/api/test_nessie_boundaries.py` stays green (no new back-edge).
- `graph_search`: admin lineage text unchanged; non-admin lineage text scoped (`nextseek_api/tests/test_graph_search_query.py`).
- Existing tests that call the tool with a bare config (`test_neo4j_read_mode.py`, `test_neo4j_total_probe.py`,
  `test_variant_procedures.py`) give their configs an admin `GraphScope`, since what they test is not scope.

### 11.5 Baseline

Each unit reports its ai and api failure-set sizes against the `10bea6d6` baseline and every new or fixed item.
New failures block the unit.

### 11.6 The operator's live acceptance (after a rebuild; not run by this build)

As the non-superuser test account, through the chat panel: a count of a sample type held only by a project the
account is not in (zero, or the fallback note with zero); a lineage question whose chain crosses into such a
project (stops at the edge); a keyword present only in such a project (no hits); a request to create or delete a
node (refused). Then the same questions as a superuser (unchanged answers). The debug panel shows `graph_scope`
and, for a fallback, `graph_scope_fallback`.

## 12. The build: three units

File-disjoint. `prover` commits `graph_scope.py` alone first, so `redact` and `plumbing` can start from that commit;
`plumbing` is finished on top of all of `prover`. Stage files by name; conventional commits with module scopes.

### 12.1 `prover`

Owns: `NessieAI/chat_nextseek/src/chat_nextseek/graph_scope.py` (new), `NessieAI/chat_nextseek/src/chat_nextseek/cypher_scope.py`
(new), `NessieAI/chat_nextseek/src/chat_nextseek/cypher_text.py` (the `mask_cypher` backtick rule only),
`NessieAI/tests/chat_nextseek/test_graph_scope.py`, `test_cypher_scope_accept.py`, `test_cypher_scope_refuse.py`,
`test_cypher_scope_inject.py`, `test_cypher_scope_fuzz.py`, `test_mask_backtick.py` (all new, under
`NessieAI/tests/chat_nextseek/`), `NessieAI/tests/chat_nextseek/graph_scope/` (new: `__init__.py`, `conftest.py`,
`fixture_graph.py`, `battery.py`, `generator.py`, `test_scope_neo4j_lane.py`, `lane.sh`),
`nextseek_api/tests/test_graph_scope_clause_pin.py` (new), and a "Graph scope lane" section in
`NessieAI/tests/README.md`.

Brief: build sections 4.1, 5 and 11.1 to 11.2 test-first. The prover is pure (no driver, no Django, no import of
`agents/`, `graph_context`, `config` or `nextseek_api`). Write the lexer and the recursive-descent recognizer for
exactly the grammar of 5.3; anything else refuses with a code from 5.8, never an exception. Inject by insertions
only, with fresh `__scope` names. Fix `mask_cypher` so a backticked name ends at the first unpaired backtick and a
backslash inside it is literal; change nothing else in `cypher_text.py`. Build the lane: fixture, battery, seeded
generator, differential oracle, marker check, both arms (the tool arm will fail until `plumbing` lands; run the
prover arm with `-k prover`). Run the ai and api lanes and the lane through `heavy.sh`, and report failure-set diffs.
Do not touch prompts, `agents/graph.py` or `graph_context.py`.

### 12.2 `redact`

Owns: `NessieAI/chat_nextseek/src/chat_nextseek/graph_catalog.py`, `NessieAI/tests/chat_nextseek/test_graph_catalog.py`
(fixtures that assert counts gain an admin scope), `NessieAI/tests/chat_nextseek/test_graph_catalog_redaction.py`
(new), `NessieAI/tests/ns/test_graph_schema_redaction.py` (new).

Brief: start from `prover`'s first commit (`graph_scope.py`). Make `get_snapshot` and `get_type_details` return
the redacted copies of section 8 unless `graph_scope.sees_all(config)`; never mutate cached objects; leave
`get_vocabulary`, the guard map and every name untouched. Do not edit `agents/graph.py`, `graph_context.py`,
`mcp_server.py` or any prompt: they already read through the getters. Tests per 11.3. Run the ai lane and report
the failure-set diff.

### 12.3 `plumbing`

Owns: `NessieAI/chat_nextseek/src/chat_nextseek/helpers/tools/neo4j.py`, `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py`,
`NessieAI/chat_nextseek/src/chat_nextseek/agents/planner/tools.py`, `NessieAI/chat_nextseek/cli.py`,
`NessieAI/chat_nextseek/mcp_server.py`, `NessieAI/chat_nextseek/app.py`,
`NessieAI/chat_nextseek/src/chat_nextseek/evaluator/runner.py`, `NessieAI/chat_nextseek/src/chat_nextseek/evaluator/demo/server.py`,
`NessieAI/ns/turn.py`, `NessieAI/ns/retry.py`, `NessieAI/ns/granular.py`, `NessieAI/cc/turn.py`,
`nextseek_api/graph_search/scope.py`, `nextseek_api/graph_search/query.py`, `nextseek_api/graph_search/README.md`,
`nextseek_api/services/assistant.py`, `nextseek_api/services/cc_assistant.py`, `nextseek_api/services/evaluator.py`,
`scripts/graph_search/nessie_venue_check.py`, `scripts/graph_search/nessie_venue.sh`,
`NessieAI/chat_nextseek/CLAUDE.md` (one invariant bullet), `.gitignore` (the negation line that tracks this spec,
which was force-added) and `docs/INDEX.md` (its row); tests: `NessieAI/tests/chat_nextseek/test_neo4j_read_mode.py`,
`test_neo4j_total_probe.py`, `test_variant_procedures.py` (configs only), and new
`NessieAI/tests/chat_nextseek/test_neo4j_scope_enforcement.py`, `test_graph_scope_fallback.py`,
`test_single_operator_scope.py`, `NessieAI/tests/ns/test_granular_graph_scope.py`,
`NessieAI/tests/chat_nextseek/graph_scope/test_graph_search_lineage_lane.py`,
`nextseek_api/tests/test_graph_scope_entry_points.py`, plus added cases in `nextseek_api/tests/test_graph_search_query.py`.

Brief: built on all of `prover`. Implement sections 4.2, 4.3, 6, 7, 9 and the entry-point half of 11.4. Resolve the
scope only in the ViewSets (`plain_scope`) and pass plain data down; `NessieAI/ns` and `NessieAI/cc` must not import
`nextseek_api.graph_search` (the boundary test). Keep `tool_neo4j_query`'s signature. In `orchestrator.py`, add the
`graph_scope` keyword, the `_identity_gate` change, the retry-loop break, `GraphScopeFallback`, the fall-through into
the REST branch and the two note constants; keep the edit to the REST branch to the chatter's `query_notes` and the
footer. No edit to `agents/graph.py` or `graph_context.py`. Run the ai and api lanes and the whole graph-scope lane
(both arms plus the lineage module) through `heavy.sh`; report failure-set diffs and list the harness accounts that
are not superusers (their graph answers change from this commit on).

## 13. Out of scope

- Prompt changes (another chat owns `prompts/**`). The agent is not told about the scope; it keeps writing Cypher
  as taught, and a refused shape falls back.
- An agent repair round on a scope refusal (a possible later refinement, measured first).
- The report runners' MySQL reads (section 14).
- APOC on any box beyond the operator's workstation.
- A rebuild, a deploy, and the live acceptance run (the operator's).

## 14. Risks and open items

1. **Planner cost of the path clause on the real graph is unmeasured.** Neo4j normally evaluates an
   `all(n IN nodes(p) WHERE ...)` predicate during a variable-length expansion, but the fixture is too small to show
   it. The 60 s tool timeout bounds the worst case; the live acceptance run should record graph-turn timings for
   the non-superuser against the superuser.
2. **Refusal rate.** Every refusal costs a fallback to a search that cannot express lineage, studies or typed
   comparisons as well as Cypher can. The lane generator's acceptance ratio and the debug field `graph_scope` give
   the first numbers; if the rate is high, the next step is an agent repair round fed the refusal reasons.
3. **Accepted residuals (by rule):** a visible sample's own `project_ids` names the foreign projects it also belongs
   to; a Study or Investigation joined to a visible sample is shown whole even when another project owns it;
   fulltext scores use corpus-wide term statistics; query timing still varies with foreign volume.
4. **The report runners read MySQL directly** (`reports/runners.py` project and protocol reports query
   `seek_production` tables with no caller scope). This design scopes their Cypher only; the SQL half is a separate
   exposure and needs its own task.
5. **Harness accounts.** Any evaluation or CI account that is not a superuser sees scoped answers and fallbacks after
   this lands; `plumbing` lists them so no measurement compares across the change by accident.
6. **The CC agent must make its own fallback** on a refused `graph` op; its guidance lives in the plugin tree, which
   this design does not edit. The refusal text names the endpoint, but the agent's behaviour is unverified until
   the live run.
7. **`graph_search` lineage change (7.3)** alters non-admin lineage results. That is intended (decision 3), but no
   live comparison exists yet.
