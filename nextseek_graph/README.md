# `nextseek_graph/`

## What this is

The graph contract: one place that names the Neo4j sample graph. `schema.py` holds the schema versions, the labels,
the relationship types and their properties, the node properties, the Sample system properties, the seven
DERIVED_FROM label keys, and the constraint and index names, each in the group of the version that introduced it. It
also holds three pure rules: the sample type label (`type_label`, `is_type_label`, `TYPE_LABEL_PATTERN`) and the
version compare (`version_tuple`, `at_least`). [`docs/neo4j-schema.md`](../docs/neo4j-schema.md) stays the document
of record; `nextseek_api/tests/test_graph_sync_contract.py` keeps the two equal, and also every copy of a name that
cannot import this package.

## Rules

- Standard library only; nothing installed (`pyproject.toml` and `uv.lock` do not name it). It imports wherever the
  checkout root is on `sys.path`: every app process, the throwaway test lanes, GitHub CI and `ci/smoke`.
- No Cypher text. `nextseek_api/graph_sync/cypher.py` is the only author of a statement; it renders its schema DDL
  from the contract's constraint and index triples.
- No I/O at import, and `schema.py` imports nothing from this package, so it also loads alone by its file path.
- Sets are `frozenset`, ordered lists tuples, maps read-only `MappingProxyType` of frozensets.
- Names only, never policy. Which labels a caller who is not an admin may join, which relationships the scope prover
  allows and which Sample properties a caller may not read are explicit sets in their own modules, so a name a later
  version adds never joins a scope or guard rule by accident.

## Version groups

Every name sits in a group named for its version: `LABELS_V11`, `RELATIONSHIPS_V11`, `RELATIONSHIP_PATTERNS_V11`,
`SAMPLE_SYSTEM_PROPERTIES_V11` and `_V12`, `NODE_PROPERTIES_V11` and `_V12`, `ORPHAN_SAMPLE_PROPERTIES_V12`,
`UNIQUE_CONSTRAINTS_V11`, `RANGE_INDEXES_V11`. A version that adds nothing of a kind has no group of that kind.

A consumer names the groups it reads. The writer's DDL and gate G read the 1.1 constraint groups; the graph agent's
guard reads the 1.1 and 1.2 property groups. So a later version's names reach a consumer only through that
consumer's own edit.

## Who imports it

Every consumer that can: the writer, `nextseek_api/graph_sync/`; `nextseek_api/graph_search/query.py`;
`nextseek_api/services/graph_sync_status.py`; `scripts/graph_schema_fallback.py`; and `ci/smoke`, each with
`from nextseek_graph import schema`. chat_nextseek reaches it only through
`NessieAI/chat_nextseek/src/chat_nextseek/graph_contract.py`, which also loads `schema.py` by its path when the
checkout root is not importable (the evaluator's case). A non-editable or git install of chat_nextseek has no
checkout around it, so it finds the contract only where the checkout root is on `sys.path`.

## How a schema change lands

1. One commit writes the new version's tables into a new `## vX.Y` section of `docs/neo4j-schema.md` (without its
   `### Versioning` subsection), appends the version to `VERSIONS` and adds its groups with the `_V<next>` suffix.
   No existing group changes. The contract test reads the new section with no edit of its own.
2. Each consumer that should see the new names adds the new groups in its own commit, with its own tests.
3. One commit adds the section's `### Versioning` subsection and moves `SCHEMA_VERSION`; `graph_sync` then writes
   the new version. Until then the section is being built, and only the newest section may be in that state.
