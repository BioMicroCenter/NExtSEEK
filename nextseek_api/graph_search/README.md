# `nextseek_api/graph_search/`

## What this is

The engine behind `POST /nextseek_api/samples/graph_search/`: sample search answered from the Neo4j graph (schema
v1.1, [`docs/neo4j-schema.md`](../../docs/neo4j-schema.md)) instead of `advanced_search`'s full scan of MySQL. It
accepts `advanced_search`'s request body plus an optional `extensions` field (paired attribute filters, ranges and a
lineage condition), applies the same visibility rules, pages inside the database, and hydrates only the returned page
from MySQL. The contract, the matching rules and the declared differences from `advanced_search` are in the
[design](../../docs/superpowers/specs/2026-09-14-graph-search-poc-design.md), section 7; the build order is the
[plan](../../docs/superpowers/plans/2026-09-14-graph-search-poc.md).

The HTTP layer is a thin ViewSet, `GraphSearchViewSet`, in the `graph_search` module of
[`nextseek_api/services/`](../services/README.md); this package holds everything below it.

## Surface

The modules, one concern each; `git ls-files nextseek_api/graph_search` lists which have landed:

| Module | Holds |
|---|---|
| `scope.py` | `resolve_scope(user)`: superuser, or the caller's project ids read from MySQL membership |
| `query.py`, `lucene.py`, `text_query.py` | the pure query builder: validated request plus scope to one page statement and one count statement; fulltext escaping; the Sample Search page's query text parsed into a tree |
| `catalog_cache.py`, `hydrate.py` | the catalog read from the graph and cached; the page's rows read from MySQL by primary key |
| `service.py` | `search(...)` for the ViewSet and `all_ids(...)` for the parity harness |

Rules every module keeps:

- Scope is added by the query builder from the server-side `Scope`, never from the request or from generated Cypher.
- Every value is a query parameter; property names come from the catalog and are backtick-quoted.
- Queries run in `session.execute_read` with a timeout, and page with `ORDER BY s.id SKIP $skip LIMIT $limit`.

## What advanced_search returned for NOT, AND/OR, tags, Not Contain and True/False

The Sample Search page (`/seek/search/`) sent its Advanced box and phone form to `GET /seek/searchAdvanced/` and its
Simple box to `GET /seek/samples/searching/`. Both run `seek/sample/search.py::SampleSearchMixin.searchAdvanced`, and
`POST /nextseek_api/samples/advanced_search/` hands one `filter_searchText` string to the same Advanced path. These are
the rows that code returned, read from `seek/search.py::Search.designSearchPubmed`, `seek/sample/search.py`,
`seek/sample/queries.py` and `seek/dbtable_sampleattribute.py`, and checked by running the parser on each shape.

### The query text (Advanced box, phone form)

Two stages, both inside the caller's project scope.

1. **SQL.** The parser turns the text into a `WHERE` over `samples.json_metadata`, a TEXT column in
   `utf8mb4_unicode_ci`: every term is `json_metadata LIKE '%term%'`, case- and accent-insensitive, over the whole
   JSON text, so key names and JSON syntax (`null`, `true`) count as well as values.
   - Operators are the upper-case words `AND`, `OR` and `NOT` with a space on each side; a lower-case `and` is part
     of a term.
   - One operator per level: the whole text, or the inside of one pair of parentheses, holds exactly one operator,
     as in `a AND b` or `(a AND b) OR c`, which is what the Add button writes. There is no precedence. A level with
     two operators (`a OR b AND c`, `a AND b AND c`, `a AND NOT b`), a leading `NOT a` or a trailing `a NOT` is
     matched as one literal phrase (`LIKE '%a OR b AND c%'`), which in practice matches nothing.
   - `a NOT b` is `a AND json_metadata NOT LIKE '%b%'`: a sample whose JSON holds `b` anywhere, in a value or in a key
     name, is dropped. `NOT age` drops every sample whose type has an `Age` or a `Stage` attribute.
   - `term[TYPE]` is the term ANDed with `sample_type_id = <id>`. The tag is trimmed, upper-cased and cut at its
     first `_`, then looked up by title; no single match (an unknown title, `[]`, `[NA]`) gives id -1, which matches
     nothing. The tag stays outside a negation: `a NOT b[TIS]` is `a AND (NOT b AND type TIS)`. Text after `]` is
     dropped, and brackets that are not exactly one `[...]` pair are part of the term. `[TIS]` alone is every TIS
     sample.
2. **Python** (`_filterSamples_advanced`). Every term of the text, negated ones included, without its tag, empty
   ones dropped: a row is kept when any term matches any metadata value, PARTIAL as a case-insensitive substring,
   EXACT as a case-insensitive equality (values not trimmed). With no term left (`[TIS]`), every row is kept.

So a row came back when the `WHERE` held and one of the text's terms was in (PARTIAL) or equal to (EXACT) one of its
values. A negated term cannot be that term: stage 1 kept the row only because the term is nowhere in its JSON.

The parser also has defects, and these are the rows they gave:

- `NOT` before parentheses was dropped: `a NOT (b OR c)` ran as `a AND (b OR c)`. The Add button puts any phrase with
  a space in parentheses, so "Add with NOT" of `left lobe` wrote `lung NOT (left lobe)`, which returned the samples
  holding both.
- A level that is nothing but one pair of parentheses, `(lung AND granuloma)` or `((a AND b) AND c)`, becomes a
  placeholder that is then matched as the literal text `term_0_20`: nothing.
- Two parenthesized groups on one level, such as `((lung AND granuloma) OR liver) AND (left lobe)` (what the Add
  button writes once both sides hold a space), are substituted at stale string positions, so the lengths decide:
  `(a AND b) OR (c AND d)` and `(lung OR liver) AND (left lobe)` survive, while `(lung AND granuloma) AND
  (left lobe)` and `(a) AND (b)` turn to garbage that matches nothing.
- Unbalanced parentheses give an empty `WHERE` and no term: every sample in the caller's scope (every sample for a
  superuser).

### The Simple box's rules

With an attribute chosen (`searchType` FILTERING), the SQL keeps the chosen sample type inside the caller's scope;
`_filterSamples` then reads the attribute's value by exact key, applies the rule, and keeps a row only when the key is
present with a non-null value (`_highlightKeyValues` with the attribute). The rules a type offers are
`OPERATOR_SETS`: strings get Contain and Not Contain, attribute type 15 (Boolean) gets True and False.

- **Contain / Not Contain:** `From in str(value).strip()`, case-sensitive, and its negation. An empty From keeps
  every row with a value under Contain and none under Not Contain.
- **True:** `toBinaryTinyInt(value) == 1` (`dmac/conversion.py`): a boolean true, the integer 1, a float from 1 up
  to but not including 2 (`int()` truncates), a string `int()` reads as 1 (`"1"`, `"01"`, `" +1 "`, `"0_1"`), or
  `true` or `yes` in any case, trimmed.
- **False:** every other present value, `""`, `" "`, `0` and `no` included.

## How graph_search expresses them

### Not Contain, True and False: `extensions.where` operators

| Simple box rule | `where` item | Cypher over the stored property `p` |
|---|---|---|
| Contain | `op: "CONTAINS"`, `value` | `toString(p) CONTAINS $w` |
| Not Contain | `op: "NOT CONTAINS"`, `value` | `p IS NOT NULL AND NOT (toString(p) CONTAINS $w)` |
| True | `op: "IS TRUE"`, no value | `toBinaryTinyInt` as a `CASE` on the stored type: a boolean, an integer equal to 1, a float in [1, 2), or a string that is `[+]?(0_?)*1` or `true`/`yes` after Python's `strip()` (`$ws`) |
| False | `op: "IS FALSE"`, no value | `p IS NOT NULL AND NOT` the True test |

The string operators read `toString(p)` because a string attribute can hold a JSON number, which the graph stores as
a number. Where the rows can still differ from advanced_search's:

- A value stored as an empty string. The graph does not store empty values (schema v1.1 rule 2), so Contain with an
  empty From, Not Contain and False miss the samples whose value is exactly `""`; advanced_search kept them. A blank
  written as `" "` is stored and behaves the same in both.
- A string attribute holding a JSON boolean: Python's `str(True)` is `True`, Cypher's `toString(true)` is `true`.
- True reads only ASCII digits; Python's `int()` also reads other Unicode digits (`"１"`). A float `inf` made
  advanced_search raise; graph_search calls it false.
- The page trims From before sending it; the old page sent it as typed.

### NOT, AND/OR and tags: `extensions.query`

`extensions.query` takes the Advanced box's text. `text_query.parse` reads it and `query.py` matches it with
advanced_search's two stages, ANDed with the rest of the body (scope first, so a negation only ever narrows the
caller's visible set):

1. Each term holds when the sample's JSON text holds it: `toLower(s.search_text) CONTAINS $q` (the values), or
   `s.type IN $qk`, the sample types whose catalog attribute titles hold the term (the key names SEEK writes into
   every sample's `json_metadata`). A tag adds `s.type = $qt` outside the term's negation; a tag naming no sample
   type, found as advanced_search found it, makes the term `false`. The terms combine as the text says.
2. One positive term (under an even number of `NOT`s) must be in (PARTIAL) or equal to (EXACT) one of the values.
   It is left out when stage 1 already implies it (PARTIAL, and no term can hold through a key name alone).

Every shape advanced_search read correctly gives its rows, with the residual differences below. Where graph_search
reads the text differently, on purpose:

- **advanced_search's parser defects are not kept.** `a NOT (b OR c)` negates the group; a level that is one group,
  and two groups on one level, match what they say; unbalanced parentheses are a 422, not every sample.
- **Shapes advanced_search matched as a literal phrase** (so, in practice, nothing) are read as operators: the same
  operator repeated (`a AND b AND c`), `AND` with `NOT` (`a AND b NOT c`, `a AND NOT b`), a leading `NOT a`, and any
  whitespace (a newline too) around an operator. `OR` on one level with `AND` or `NOT` is a 422 that asks for
  parentheses, as is an operator with no term beside it, empty parentheses, or a term or group directly beside a
  group.

Residual differences, where the same text can still give other rows:

- The JSON text is more than the values and the catalog's key names: JSON syntax (`null`, `true`, `false`) and JSON
  escapes (a non-ASCII character or a quote stored escaped) are text to advanced_search's `LIKE`, and a term can
  run across a key and its value. The collation also folds accents (`é` is `e`), which `toLower` does not.
- A key name is read per sample type from the catalog, which also lists keys some samples of a type carry and others
  do not (`declared: false`); advanced_search read each sample's own keys.
- A term with `&`, `^` or `:` had extra rules in advanced_search's value stage (`_highlightKeyValues`): parts of the
  term could match on their own.
- advanced_search's value stage also counted negated terms; a negated term cannot hold on a row stage 1 kept, except
  when its only occurrence in the JSON text is escaped.
- `filter_searchText` does not change: its terms still match values only (design section 7, "key names do not
  count"), so `["lung", "organ"]` with `AND` and the query `lung AND organ` can differ on the samples whose only
  `organ` is a key name.

**What a broad negation costs.** Lucene lists the samples that hold a word, not those that lack one, so a negated term
never narrows the candidates. The source is the most selective of: the fulltext candidates of the positive terms (the
query's own, or the union its value stage needs), a sample type (`sampletype`, a `where` label, or the tags that bound
every match), and only then every `Sample` node, which is logged as a full scan. A text of nothing but negations and no
type (`NOT granuloma`) therefore reads the `search_text` of every sample, once for the page and once for the count,
under the 60 second timeout (504).

## Running and testing

Unit tests run in the Django unit lane (`ci/README.md` "Running and testing"), test files
`nextseek_api/tests/test_graph_search_*.py`. Parity against `advanced_search` runs in the throwaway lane
([`scripts/graph_search/README.md`](../../scripts/graph_search/README.md)).

## Depends on / depended on by

- Depends on the graph written by [`nextseek_api/graph_sync/`](../graph_sync/README.md), Django's `seek` connection
  for scope and hydration, the `neo4j` driver, and the request and response models in `nextseek_api/models.py`.
- Depended on by the ViewSet and by the lane's parity and benchmark scripts.
