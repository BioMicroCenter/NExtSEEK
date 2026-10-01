# docs/graph/

A file-level picture of the code: which tracked files import, call or subclass which, grouped
into named communities. It is generated, so do not edit these files by hand.

| File | What it is |
|---|---|
| `architecture.svg` | the communities as boxes with their file counts, and lines for the heaviest links between non-test files |
| `graph.html` | an interactive view of every file: colour by community, size by number of linked files, click a file to list its links, click a community in the legend to hide it. Open it in a browser; it loads vis-network from unpkg (the version graphify itself uses) |
| `graph-files.json` | the data: `communities` (name, file count, hub files), `files` as `[path, community index, linked files]`, `edges` as `[file index, file index, weight]` |

## How it is made

1. graphify (the `graphifyy` package on PyPI) extracts a function-level graph from the code
   (code only, AST only, no LLM) into `graphify-out/` at the repo root. That folder is gitignored and
   large; it never gets committed.
2. `scripts/graph_files.py` collapses it to files and writes the three files here. Its docstring
   holds the rules: which edges count, which false resolutions it drops (graphify resolves a bare
   `import csv` or a call to `Path(...)` by name to whichever repo file defines that name), how the
   communities are found (Louvain) and how they get their plain names (the `AREAS` table).

Left out of the input: vendored and built trees (`static/admin`, `static/grappelli`,
`static/filebrowser`, `static/mezzanine`, `static/css`, every jquery-easyui copy, the committed
chat bundle `static/js/chat_assistant`, vendored bootstrap scripts), `node_modules`, `.venv`,
`NessieAI/history`, `docs/archive`, `startup/seed`, `migrations` folders, and runtime folders
(`filestore`, `logs`, `outputs`, the root `schema_rag/` data).

## Regenerate

From the repo root, after the code changed:

```bash
/graphify . --update            # in Claude Code; refreshes graphify-out/graph.json
python3 scripts/graph_files.py  # stdlib only; rewrites the three files here
```

A first build (no `graphify-out/` yet) is `/graphify .` with the exclusions above. Commit the
three files together; the commit they describe is printed in `graph-files.json` and in the
title of the svg and the page.

## Reading it

- Community names come from folders, but membership comes from the links: a test file sits with
  the code it exercises, and a small helper sits with its heaviest user. So a community's file
  count is not its folder's file count.
- Edge weight is the number of import, call and inherit references between two files, not a
  measure of importance. Only links backed by at least one real import are kept.
