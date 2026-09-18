# Working in `NessieAI/build_tools/`

## Invariants

- Never hand-edit text between a `PLAN005-GEN` BEGIN/END pair anywhere in the repo: the next `--write` overwrites that region from the registry, and until then `--check` reports the tree as stale (`NessieAI/build_tools/gen_op_surfaces/emit.py:221-241`).
- A marked file must carry exactly one BEGIN and one END, ordered and unnested; a duplicate or inverted pair raises `MarkerError`, which aborts the whole run rather than that one target (`NessieAI/build_tools/gen_op_surfaces/blocks.py:11-31`).
- The `NEXTSEEK-DOCS` region of `NessieAI/docker/cc-runtime/container/CLAUDE.md` must come through inventory generation byte-identical; the guard compares it before and after and refuses the render otherwise, which is the only thing keeping the two generators that share that one file from clobbering each other (`NessieAI/build_tools/gen_op_surfaces/claude_md.py:58-65`).
- No `PLAN005-GEN` marker may sit inside that docs block: the same guard refuses, because the docs ingester replaces everything between its own markers and would silently eat a nested op inventory (`NessieAI/build_tools/gen_op_surfaces/claude_md.py:49-55`, `NessieAI/build_tools/ingest_nextseek_docs/toc.py:79-81`).
- The named-context `COPY`s of the canonical context files (`CANONICAL_CONTEXT_FILES` in `NessieAI/build_tools/gen_op_surfaces/constants.py`) must stay the last writers of their in-image paths; add any later `COPY` landing on `/app/plugins/nextseek` and validation raises (`NessieAI/build_tools/gen_op_surfaces/docker_blocks.py:146-201`). Those files have one copy, in `NessieAI/chat_nextseek/src/chat_nextseek/context/`: the capabilities-copy emitter refuses a copy of any of them in the plugin tree, so `--check` and `--write` stop until it is deleted (`NessieAI/build_tools/gen_op_surfaces/docker_blocks.py:69-92`).
- Each Compose named context must resolve to its own tree inside the repo root: `chat_nextseek` to `NessieAI/chat_nextseek/`, `dmac_assistant_baml` to `NessieAI/dmac_assistant/baml_src/`. An absolute path, a `~`, a `..` segment or any other resolved target is refused, so a build cannot be pointed at an out-of-tree checkout or at a BAML copy (`NessieAI/build_tools/gen_op_surfaces/docker_blocks.py:204-249`).
- The Dockerfile's BAML `COPY` is hand-written, and must stay the only writer of `/app/baml_src/`, reading the `dmac_assistant_baml` named context; `validate_baml_context_copy` refuses a `COPY` from the build context, which would bring the retired cc-runtime mirror back.
- Generated paths are resolved through a guard that rejects absolute paths, `..` traversal and symlinks whose target leaves the root; without it a crafted target path would let a generator write outside the repository (`NessieAI/build_tools/gen_op_surfaces/paths.py:12-37`).
- Whatever these tools rewrite must be committed in the same change: the image `COPY`s the docs snapshot and the container `CLAUDE.md` straight out of the checkout (`NessieAI/docker/cc-runtime/Dockerfile:71-72`), so an uncommitted regeneration means the built image silently ships the previous content and no build step will refetch it.
- Plugin and Dockerfile locations come from `NessieAI/paths.py` through `constants.py`. Never hard-code a plugin path in a generator module.

## Landmines

- Neither graph file is baked into the plugin context: `min_graph_schema.json` stays the NS parser's routing prose (the CC routing rule is the plugin skill's, `NessieAI/docker/CLAUDE.md`), and the agent reads the live graph through the `nextseek-graph-schema` op instead of `neo4j_schema.json`.
- `--check` does not fail cleanly when `route_capabilities.json` cannot be built. `RouteCapabilitiesError` is a `ValueError`, not a `SystemExit`, so it escapes the CLI's handler and the operator gets a Python traceback and exit 1 instead of the `gen_op_surfaces failed:` message the module writes for every other error (`NessieAI/build_tools/gen_op_surfaces/__main__.py:49-64`).
- The docs ingester **deletes** before it writes: every `*.md` in the target directory except `README.md` is unlinked, so a hand-authored note dropped into `NessieAI/docker/cc-runtime/docs/nextseek/` is destroyed on the next successful run (`NessieAI/build_tools/ingest_nextseek_docs/__main__.py:64-68`).
- The ingester's own page validator has blocked a real run before: the port record shows it rejecting live GitBook pages on 2026-07-01 for not starting with an H1, after a successful HTTP 200 fetch, and the committed docs were taken from the pinned source clone instead (`NessieAI/docker/cc-runtime/PORT-EVIDENCE.json:23-26`, `NessieAI/build_tools/ingest_nextseek_docs/fetch.py:157-162`). A refresh attempted today may therefore abort with zero writes and leave the snapshot silently frozen. <!-- UNVERIFIED --> whether the current GitBook export clears that validator needs a live fetch of a third-party site, which this repository cannot establish.
- Both ingester output defaults are repo-relative paths, resolved against the current working directory (`NessieAI/build_tools/ingest_nextseek_docs/constants.py:18-21`). Run it from anywhere but the repo root and it silently creates a fresh `NessieAI/docker/cc-runtime/...` tree under your cwd and reports success.
- Three tests shell out to `git show` against a hardcoded revision (`NessieAI/tests/build_tools/unit/test_gen_op_surfaces_claude_md.py:70-71`, `NessieAI/build_tools/gen_op_surfaces/constants.py:80`). They fail with `CalledProcessError` in any environment where that object is unreachable (a shallow clone, or a container mount whose worktree gitdir points outside it), and the failure looks like a content drift rather than a missing history.
- The plugin validator's image is pinned to one digest, and the validator refuses any substitute rather than falling back (`NessieAI/build_tools/plan005_validate_plugins/validate.py:75-80`). A host without that image cannot run the docker half; `--skip-docker` runs only the identity checks.
- Regenerating surfaces is step 7 of a longer sequence, not a standalone action: the registry export (step 6) must run first, or the generators render an inventory that disagrees with the committed `ops.json` (`.claude/skills/add-cc-op/SKILL.md`).
- The Plan 005 protocol modules are frozen in `NessieAI/history/plan005/`. Never edit them there, and never re-import them from live code.

## Test command

See `NessieAI/tests/README.md` (the Django lane, with `NessieAI/tests/build_tools`). `NessieAI/build_tools/README.md` names the two causes of its known failures.

## See also

- See `NessieAI/build_tools/README.md` for the entry points, the target registry and the dependency edges.
- See `NessieAI/cc/README.md` for the registry these generators read.
- See `.claude/skills/add-cc-op/SKILL.md` for the full op-registration sequence.
- See `DEPLOYMENT.md` §10 for the container CLAUDE.md refresh rule.
- See `ci/pytest-baseline.txt` for the recorded known-failing set.
