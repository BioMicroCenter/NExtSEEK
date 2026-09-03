# `build_tools/`

## What this is

Four independent command-line tool groups that produce **committed-but-generated**
files elsewhere in the repo. It is not a library. Measured 2026-09-03, exactly one
Python import of `build_tools` exists anywhere outside this directory, and it is a
test module (`nextseek_api/assistant/tests/test_route_capabilities.py:16`), so no
production code path runs any of this. The tools are run by hand, by a documented
skill, or by the pytest suite.

The directory holds 61 files as of 2026-09-03, three of them this document, its
CLAUDE.md and their citation list; 52 are Python and 24 of those sit under
`build_tools/tests/` (all counted by a `find` over `build_tools`). It carries
no build system of its own: a `find` over `build_tools` for `pyproject.toml`,
`uv.lock` or `Makefile` returns nothing, so every entry point runs on the root
project's environment.

Two groups are live generators. `gen_op_surfaces` renders 12 generated targets from
the Container-CC operation registry (`build_tools/gen_op_surfaces/emit.py:217-235`),
and `ingest_nextseek_docs` refreshes the vendored NExtSEEK user-docs snapshot from
GitBook (`build_tools/ingest_nextseek_docs/constants.py:9-12`). The five `plan005_*`
modules, `build_tools/plan005_signoffs/` and `build_tools/schemas/` are a frozen
evidence protocol for one historical plan, pinned to a base commit
(`build_tools/plan005_closeout.py:21`) and to two image digests
(`build_tools/plan005_closeout.py:15-20`).

`ingest_nextseek_docs` came from the dmac-assistant repository. The cc-runtime port
record pins source commit `a429f137`
(`docker/cc-runtime/PORT-EVIDENCE.json:2-4`) and names
`python -m build_tools.ingest_nextseek_docs` as the upstream entry point it invoked
from that clone (`docker/cc-runtime/PORT-EVIDENCE.json:20-22`); the same commit pins
the image port (`docker/cc-runtime/Dockerfile:4-7`). The copy here carries
NExtSEEK-specific default output paths
(`build_tools/ingest_nextseek_docs/constants.py:14-15`) and two helpers for GitBook's
2026-07 export format, one for a leading llms.txt banner
(`build_tools/ingest_nextseek_docs/fetch.py:94-95`) and one for a repeated trailing
agent-instructions block (`build_tools/ingest_nextseek_docs/fetch.py:114-115`).

`gen_op_surfaces` is the **generator** end of a contract the operation registry
documents from the consumer end. See `nextseek_api/cc_assistant/README.md` for what
each of its five generator modules produces.

## Surface

The surface here is **command-line entry points plus the file sets each reads and
writes**, not a public Python API: nothing outside the directory imports these
modules except one test, so an importer list would describe nothing. What follows is
therefore grouped by entry point, with the inputs and the outputs named.

### `python -m build_tools.gen_op_surfaces`

`--check` and `--write` are mutually exclusive and one is required; `--root` defaults
to the repo root two levels above the module and `--tmpdir` steers check-mode
rendering (`build_tools/gen_op_surfaces/__main__.py:14-41`). `--check` renders every
target into a temporary directory and byte-compares the committed file
(`build_tools/gen_op_surfaces/emit.py:261-295`); `--write` writes only the targets
whose bytes differ (`build_tools/gen_op_surfaces/emit.py:298-314`). Exit codes are 0
for no change, 1 for error, 2 for changes written
(`build_tools/gen_op_surfaces/constants.py:4-6`).

The target registry (`build_tools/gen_op_surfaces/emit.py:217-235`) resolved to 12
targets on this tree on 2026-09-03: 2 whole-file and 10 marked blocks over 8 files.

| Generated target | Kind | Emitter |
|---|---|---|
| `dmac_assistant/build_context/route_capabilities.json` | whole file | `build_tools/gen_op_surfaces/route_capabilities.py:326-327` |
| `docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md` | whole file | `build_tools/gen_op_surfaces/emit.py:81-88` |
| `docker/cc-runtime/Dockerfile` plugin `COPY`, plugin `PATH`, capabilities `COPY` | 3 blocks | `build_tools/gen_op_surfaces/docker_blocks.py:37-62` |
| `docker-compose.yml` additional build contexts | 1 block | `build_tools/gen_op_surfaces/docker_blocks.py:65-70` |
| `docker/cc-runtime/container/CLAUDE.md` plugin, skill and operation inventories | 3 blocks | `build_tools/gen_op_surfaces/claude_md.py:144-188` |
| each plugin `commands/*.md` carrying the command-ops markers | 1 block each | `build_tools/gen_op_surfaces/commands.py:34-64` |
| each installed `SKILL.md` | 1 block each | `build_tools/gen_op_surfaces/skills.py:105-144` |

Marked-block targets are discovered from disk rather than hardcoded: command docs by
scanning each plugin's `commands/*.md` for the marker pair
(`build_tools/gen_op_surfaces/commands.py:67-88`), skills from the install oracle's
own discovery (`build_tools/gen_op_surfaces/skills.py:147-167`), and the Dockerfile,
Compose and container-`CLAUDE.md` blocks only when both markers are already present
in the file (`build_tools/gen_op_surfaces/emit.py:127-214`).

Two path constants define the capabilities contract: the canonical document under
`chat_nextseek` and the baked copy under the plugin tree
(`build_tools/gen_op_surfaces/constants.py:8-13`). The emitter for the baked copy is
a straight byte read of the canonical, with no parsing or rewriting
(`build_tools/gen_op_surfaces/emit.py:81-88`). The image, however, does not consume
the baked copy: the Dockerfile first copies the whole plugin directory and then
overwrites that one file from a named `chat_nextseek` build context
(`docker/cc-runtime/Dockerfile:50-55`), which the generator both emits
(`build_tools/gen_op_surfaces/docker_blocks.py:56-62`) and validates as the final
writer of that in-image path
(`build_tools/gen_op_surfaces/docker_blocks.py:133-159`).

Because targets are rendered in sorted order by path
(`build_tools/gen_op_surfaces/emit.py:235`), `dmac_assistant/...` sorts ahead of
`docker/...`, so `route_capabilities.json` is rendered first and its own
byte-identity precondition on the capabilities pair
(`build_tools/gen_op_surfaces/route_capabilities.py:229-232`) is what a stale baked
copy trips, before the whole-file comparison at
`build_tools/gen_op_surfaces/emit.py:288-295` is ever reached.

Every marked block is rewritten in place between its markers only, after a check
that exactly one well-ordered, non-nested marker pair exists
(`build_tools/gen_op_surfaces/blocks.py:9-50`). Path resolution rejects absolute
paths, `..` traversal and symlinks pointing outside the root
(`build_tools/gen_op_surfaces/paths.py:12-42`).

### `python -m build_tools.ingest_nextseek_docs`

Fetches the Koch Institute GitBook site index and its Markdown pages
(`build_tools/ingest_nextseek_docs/fetch.py:43-91`), refetching until two attempts
hash identically, up to three tries, and aborting without writes if they never agree
(`build_tools/ingest_nextseek_docs/__main__.py:101-145`). It writes numbered section
files and a `README.md` into `docker/cc-runtime/docs/nextseek/`, replaces the
`NEXTSEEK-DOCS` marked block inside `docker/cc-runtime/container/CLAUDE.md`, and
stores the content hash (`build_tools/ingest_nextseek_docs/__main__.py:64-81`). The
committed snapshot held 10 numbered section files plus a README when that directory
was listed on 2026-09-03, and the README carries a do-not-edit banner naming this
tool (`docker/cc-runtime/docs/nextseek/README.md:3`). `--force`, `--doc-url`, `--docs-dir` and `--claude-md-path` are the flags
(`build_tools/ingest_nextseek_docs/__main__.py:189-209`); exit codes match the other
generator, 0 / 1 / 2 (`build_tools/ingest_nextseek_docs/__main__.py:32-34`). The
`CLAUDE.md` rewrite is atomic through a temp file and `os.replace`
(`build_tools/ingest_nextseek_docs/toc.py:83-90`).

### `python -m build_tools.plan005_validate_plugins`

Hashes each installed plugin tree and runs `claude plugin validate --strict` inside a
network-disabled container against a read-only bind mount of the plugin directory
(`build_tools/plan005_validate_plugins/docker_runner.py:16-44`). `--repo-root` is
required, and `--skip-docker` reduces it to the local identity checks
(`build_tools/plan005_validate_plugins/__main__.py:19-50`). The validator image is
pinned to one digest and any substitute is refused
(`build_tools/plan005_validate_plugins/validate.py:73-78`).

### The Plan 005 evidence protocol

Five modules that together record, gate and close out one historical plan.
`build_tools/plan005_closeout.py:40-57` fixes the 16 ordered record IDs;
`build_tools/plan005_closeout.py:643-654` exposes the `protocol`, `preflight`,
`finalize` and `verify` stages, whose control logic lives in
`build_tools/plan005_closeout_control.py:1`. `build_tools/plan005_record.py:1`
records one command's evidence without a shell, refusing secret-bearing argv
(`build_tools/plan005_record.py:79`). `build_tools/plan005_baseline.py:1`
materializes the exact base-commit blobs outside the repo.
`build_tools/plan005_gate.py:1` is the coverage / JUnit / pragma gate; it maps
changed test files onto five named CI lanes
(`build_tools/plan005_gate.py:210-220`) and enforces a floor of 95 percent combined
branch-enabled coverage (`build_tools/plan005_closeout.py:24`, applied at
`build_tools/plan005_gate.py:362-369`). `build_tools/schemas/plan005-closeout.schema.json:9-13`
pins the record list to exactly 16 entries, and
`build_tools/plan005_signoffs/` held five approval records when listed on
2026-09-03, each binding its own artifact bytes by sha256
(`build_tools/plan005_signoffs/task-09-compose-dockerfile.json:7-11`).

## Running and testing

One lane, and it is genuinely runnable. `build_tools/tests/` holds 20 `test_*.py`
modules under `build_tools/tests/unit/`, `build_tools/tests/integration/` and the
directory root (counted 2026-09-03). The `integration` name is not a network lane:
its source-contract test monkeypatches the fetcher
(`build_tools/tests/integration/test_markdown_source_contract.py:44`), as does every
other test that touches GitBook (`build_tools/tests/unit/test_fetch.py:36`).

Run it against a checkout with the repo mounted read-only, which is also how the
project's own no-write oracle exercises the generators
(`build_tools/tests/unit/test_gen_op_surfaces.py:272`, asserting at
`build_tools/tests/unit/test_gen_op_surfaces.py:308-315`):

```sh
docker run --rm -v "$PWD":/src:ro -w /src -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint sh nextseek-nextseek:latest \
  -c '/app/.venv/bin/python -m pytest build_tools -q'
```

Real result, 2026-09-03, on this worktree: **13 failed, 236 passed, 1 skipped in
3.24s**. All 13 are already recorded as known-failing at `ci/pytest-baseline.txt:25-37`.
By cause: 8 trace to the stale baked capabilities copy, 3 to three tests that shell
out to `git show` against a pinned revision
(`build_tools/tests/unit/test_gen_op_surfaces_claude_md.py:66-67`) which the
container's mount cannot reach, and 2 to a sign-off whose recorded sha256 for
`docker-compose.yml` no longer matches the file
(`build_tools/plan005_closeout_control.py:496-500`).

A host `uv run pytest` is not an option here: `uv sync` fails building `mysqlclient`
on this box, which is why the command above bypasses it and uses the interpreter
already inside the application image.

The Plan 005 docker lanes are (not run). They need two image digests
(`build_tools/plan005_closeout.py:15-20`) that `docker image inspect` reports as
absent from this host (checked 2026-09-03), plus an evidence tree under a path that
does not exist here (`build_tools/plan005_closeout.py:25`).

CI runs this directory by path string inside the informational pytest job
(`.github/workflows/ci-pytest.yml:63`), which is `continue-on-error`
(`.github/workflows/ci-pytest.yml:46`) and is scored by diffing against the committed
baseline rather than by requiring green.

## Depends on / depended on by

**Depends on.** Import edges out of this directory, plus the files and binaries the
tools read by path:

- `nextseek_api.cc_assistant.op_registry` is the data source for every generated op
  surface, imported at module scope by five modules here:
  `build_tools/gen_op_surfaces/commands.py:7-9`,
  `build_tools/gen_op_surfaces/skills.py:11-13`,
  `build_tools/gen_op_surfaces/claude_md.py:16-21`,
  `build_tools/gen_op_surfaces/docker_blocks.py:12-17` and
  `build_tools/gen_op_surfaces/route_capabilities.py:8-26`.
- `build_tools/plan005_validate_plugins/validate.py:9-17` is the sixth importer of
  that registry, taking the install oracle and the plugin-identity loader.
- `nessie_tests` is imported at module scope by the route-capabilities generator for
  its corpus loader, exporter and fingerprint
  (`build_tools/gen_op_surfaces/route_capabilities.py:27-29`), and the corpus file
  itself is read by relative path (`build_tools/gen_op_surfaces/route_capabilities.py:39`).
- `dmac_assistant.router.capabilities` is imported lazily inside a function, so the
  generator round-trips its own output through the real consumer loader before
  returning it (`build_tools/gen_op_surfaces/route_capabilities.py:306-323`).
- `httpx` is imported at module scope by the fetcher
  (`build_tools/ingest_nextseek_docs/fetch.py:10`) but is declared nowhere in the
  repo-root `pyproject.toml`; a grep for `httpx` over that one file returns no match,
  and the only declaration in the tree is the vendored router's
  (`dmac_assistant/pyproject.toml:21`).
- Django is **not** a dependency, even though every generator reads a Django app's
  registry. The module reaching furthest outside this directory holds 8 module-scope
  imports of other packages, more than any other file here and all of them the
  registry or `nessie_tests`
  (`build_tools/gen_op_surfaces/route_capabilities.py:4-29`, counted 2026-09-03); no
  line beginning with `import django` or `from django` exists in any file under
  `build_tools`, and importing
  `build_tools.gen_op_surfaces.route_capabilities` leaves `django` out of
  `sys.modules` (both searches run 2026-09-03).
- External binaries. `git` is invoked as a subprocess by the gate
  (`build_tools/plan005_gate.py:147-155`), the baseline materializer
  (`build_tools/plan005_baseline.py:31-41`) and the closeout control
  (`build_tools/plan005_closeout_control.py:59-68`); `docker` by the plugin validator
  (`build_tools/plan005_validate_plugins/docker_runner.py:24-44`).
- Load-bearing input directories the tools READ, not scratch they write:
  `docker/cc-runtime/build_context/plugins/` is the root every discovery walks, and
  six modules each hold their own copy of that one string with no other value
  anywhere under `build_tools` (`build_tools/gen_op_surfaces/commands.py:11`,
  `build_tools/gen_op_surfaces/skills.py:15`,
  `build_tools/gen_op_surfaces/claude_md.py:23`,
  `build_tools/gen_op_surfaces/docker_blocks.py:19`,
  `build_tools/gen_op_surfaces/route_capabilities.py:40`,
  `build_tools/plan005_validate_plugins/validate.py:19`).
  `build_tools/plan005_signoffs/` is globbed for approval records
  (`build_tools/plan005_closeout_control.py:485`). Move either and the affected
  generator emits an empty block or refuses to close out.

**Depended on by**, derived 2026-09-03 from a repo-wide grep for `build_tools`
filtered with the no-leading-dot form (305 raw hits, 169 after filtering; the
`^\./build_tools/` form filters nothing on this host and would have left all 305):

- Python imports: exactly one, and it is a test —
  `nextseek_api/assistant/tests/test_route_capabilities.py:16` and
  `nextseek_api/assistant/tests/test_route_capabilities.py:21` import the constants
  and the route-capabilities generator directly.
- By path string, not by import: `build_tools/plan005_gate.py:26-28` pins three test
  modules living outside this directory as named CI lanes, two of them under
  `nextseek_api/cc_assistant/tests/`.
- Documented workflow: `.claude/skills/add-cc-op/SKILL.md:76-83` makes
  `gen_op_surfaces --write` then `--check` step 7 of adding a Container-CC operation,
  and `.claude/skills/add-cc-op/SKILL.md:87-89` requires the `--check` run against a
  read-only repo mount.
- Deployment runbook: `DEPLOYMENT.md:612-613` names
  `python -m build_tools.ingest_nextseek_docs` as the only sanctioned way to refresh
  the container `CLAUDE.md`.
- CI: `.github/workflows/ci-pytest.yml:63` runs this directory's tests by path
  string, and `ci/pytest-baseline.txt:10` repeats that command in its own header.
- Generated-file provenance: `docker/cc-runtime/docs/nextseek/README.md:3` carries a
  do-not-edit banner naming `build_tools/ingest_nextseek_docs`.

What a hit is **NOT**. The 169 filtered hits are dominated by two groups that are not
dependency edges at all. `evidence/plan018-v4-9-owned-surface.json:492` and hundreds
of sibling entries are a file inventory recording paths under this directory, not
code that calls anything here. The `CITATIONS.txt` files of already-documented
boundaries list modules here as sources they cited, for example
`nextseek_api/cc_assistant/CITATIONS.txt:71`; that is a documentation reference, the
reverse of a code edge. Also excluded: `docker/cc-runtime/pyproject.toml:58-60`
mentions `build_tools` only to record that its coverage scope was removed from a
different project's pytest options.

See `build_tools/CLAUDE.md` for the invariants, the live drift and the traps.
