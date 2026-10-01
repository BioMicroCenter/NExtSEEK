# NExtSEEK architecture

How the pieces fit, on one page. Each part below is a line or two and a link to the doc that owns it;
this page states no detail those docs own. The picture is the file-level code graph
([`docs/graph/README.md`](docs/graph/README.md) says how it is made and refreshed).

![NExtSEEK code communities](docs/graph/architecture.svg)

Boxes are groups of files that import each other (named after the folder most of them live in); lines are
the heaviest links between non-test files. [`docs/graph/graph.html`](docs/graph/graph.html) shows every file.

## The parts

**Pages.** Server-rendered HTML from `seek/` (views and templates over SEEK's tables, through the legacy table
layer in `seek/` and `dmac/`), styled by the theme in `themes/`. The UI guide is
[`docs/ui/README.md`](docs/ui/README.md); folder detail is in [`seek/README.md`](seek/README.md),
[`dmac/README.md`](dmac/README.md) and [`themes/README.md`](themes/README.md). The site's own user docs are
[`themes/NextSeek/docs/README.md`](themes/NextSeek/docs/README.md).

**API.** `nextseek_api/` is the Django app behind every `/nextseek_api/` URL: native endpoints, SEEK proxy
endpoints, and the assistant's HTTP surface. It is the hub of the graph above. See
[`nextseek_api/README.md`](nextseek_api/README.md).

**Nessie.** All AI code is in `NessieAI/`: the router, the in-process NS engine (`NessieAI/chat_nextseek/`), the
per-turn Container-CC engine (`NessieAI/cc/`), the granular ops and op registry, HiBayes, the chat panel and the AI
images. Start at [`NessieAI/README.md`](NessieAI/README.md); the cross-boundary system map is
[`NessieAI/docs/architecture.md`](NessieAI/docs/architecture.md).

**Ingest pipelines.** Batch upload of sample workbooks, the attribute API and assay registration, each a
subpackage of `nextseek_api/` with its own README:
[`nextseek_api/batch_upload/README.md`](nextseek_api/batch_upload/README.md),
[`nextseek_api/attributes/README.md`](nextseek_api/attributes/README.md),
[`nextseek_api/assay_registration/README.md`](nextseek_api/assay_registration/README.md).

**Graph search and sync.** A Neo4j copy of the sample records that search reads, kept equal to MySQL by one
writer. What the graph holds: [`docs/neo4j-schema.md`](docs/neo4j-schema.md); the writer is
[`nextseek_api/graph_sync/README.md`](nextseek_api/graph_sync/README.md) and the reader
[`nextseek_api/graph_search/README.md`](nextseek_api/graph_search/README.md).

**Catalog context.** The hand-owned sample type, assay and project catalogs that Nessie reads:
[`context/README.md`](context/README.md).

**Storage.** SEEK's MySQL schema (SEEK owns it; NExtSEEK reads it and writes through SEEK or its own writers),
the `dmac` schema (NExtSEEK's own tables, Django models and migrations in `nextseek_api/`), Neo4j (derived from
the two, never a source of truth), and the file store. Topology, volumes and ports: `DEPLOYMENT.md` §0.

**Images and bring-up.** One Compose stack: the app image (Django, its Celery workers and the graph sync
loop), SEEK with its workers and Solr, MySQL, Neo4j, nginx, a message broker, and the AI images
(`NessieAI/docker/`). `./startup.sh` brings it up
([`startup/README.md`](startup/README.md)); deploys follow [`DEPLOYMENT.md`](DEPLOYMENT.md); CI is
[`ci/README.md`](ci/README.md).

## Known debts at the boundaries

Found by the code graph; each is a real import, not a graph artefact.

- Product code imports the test tree. The `nessie` management command
  (`nextseek_api/management/commands/nessie.py`) and `NessieAI/hibayes/human_grade_fit.py` import modules from
  `NessieAI/tests/nessie_tests/`, and the live router reads that folder's `corpus.json` through
  `NessieAI/paths.py`. That is why the app image must carry `NessieAI/tests/`.
- The NS engine package imports the Django app: `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py`
  imports `nextseek_api.assistant.excel_export`, so `chat_nextseek` is not the standalone package its
  `pyproject.toml` suggests.
- The Container-CC engine reaches the legacy table layer directly: `NessieAI/cc/cc_provision.py` imports
  `seek.seekdb.SeekDB` instead of going through `nextseek_api/`.

