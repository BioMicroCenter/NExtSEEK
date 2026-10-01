# `NessieAI/schema_rag/`

## What this is

A library that turns an OpenAPI document into a searchable, per-session DuckDB file, then
answers a natural-language question with the endpoints most like it. Its tests are in
`NessieAI/tests/schema_rag/`.

It is not a Django app. A grep over every file here for `django.db`, `models.Model`,
`AppConfig` or `migrations` returns nothing, so there is no ORM model, no migration and no
app config to register. Two modules read Django settings at module scope
(`NessieAI/schema_rag/session.py:20`, `NessieAI/schema_rag/service.py:20`); the
third Django consumer imports lazily inside functions and swallows every failure so it
stays importable with settings unconfigured
(`NessieAI/schema_rag/schema_processor.py:40-45`,
`NessieAI/schema_rag/schema_processor.py:120-126`). The HTTP surface belongs to a
sibling: `nextseek_api/urls.py:35` registers the ViewSet defined at
`nextseek_api/services/schema_rag.py:43`.

**Being imported repairs a model in the parent package.** `nextseek_api/models.py:2266`
declares `RetrieveResponse` with two forward references written as strings
(`nextseek_api/models.py:2270`, `nextseek_api/models.py:2273`). The classes they name are
imported only under a type-checking guard (`nextseek_api/models.py:2166-2167`), so at run
time they are absent from that module. The repair is the tail of this package's
initializer: `NessieAI/schema_rag/__init__.py:18` imports `_rebuild_schema_rag_models`
and `NessieAI/schema_rag/__init__.py:19` calls it, whereupon that helper's own local
import supplies both classes (`nextseek_api/models.py:2294`) and rebuilds the model
(`nextseek_api/models.py:2300`). If that local import fails, the helper raises an
`ImportError` naming `NessieAI.schema_rag.models` instead of swallowing it
(`nextseek_api/models.py:2295-2299`), so a moved or broken models module fails at import
time rather than on the first retrieve call. Importing any module here runs
that initializer, which is why the live path arms it via
`nextseek_api/services/schema_rag.py:27`. Drop those two lines and `RetrieveResponse`
keeps its unresolved forward references. The import of `nextseek_api.models` is one of
the allowed back-edges in `NessieAI/CLAUDE.md` "Boundary"; this unit has no `CLAUDE.md` of
its own, so that file carries its rules.

## Surface

An ordinary Python package. The surface is the two module-level functions the ViewSet
calls, plus the modules behind them; the edge in both directions is an import, derived
below by grepping every `.py` file in the tree for an import line naming this package and,
in the other direction, by reading every import line in these nine files. One edge is not
an import at all and is named separately: a settings key and the directory on disk it
points at.

**Two entry points.** `NessieAI/schema_rag/service.py:278` builds a session from a
schema URL; `NessieAI/schema_rag/service.py:700` searches one. Everything else here is
reached through those two.

| Module | What it holds |
|---|---|
| `NessieAI/schema_rag/service.py` | the two entry points, the lazily cached embedder (`NessieAI/schema_rag/service.py:134`), the flattener (`NessieAI/schema_rag/service.py:191`), the three scoring passes (`NessieAI/schema_rag/service.py:493`, `NessieAI/schema_rag/service.py:553`, `NessieAI/schema_rag/service.py:613`) and the method/tag pre-filter (`NessieAI/schema_rag/service.py:94`) |
| `NessieAI/schema_rag/schema_processor.py` | fetch, parse, `$ref` resolution and simplification, in one class opened at `NessieAI/schema_rag/schema_processor.py:261`, plus four module-level origin helpers |
| `NessieAI/schema_rag/db.py` | the `endpoints` table and its four loaders (`NessieAI/schema_rag/db.py:174`, `NessieAI/schema_rag/db.py:211`, `NessieAI/schema_rag/db.py:266`, `NessieAI/schema_rag/db.py:311`) |
| `NessieAI/schema_rag/session.py` | session id, file path, creation, load, delete and the expiry sweep (`NessieAI/schema_rag/session.py:188`) |
| `NessieAI/schema_rag/models.py` | three internal pydantic models, each refusing unknown keys (`NessieAI/schema_rag/models.py:29`, `NessieAI/schema_rag/models.py:89`, `NessieAI/schema_rag/models.py:159`), and the two embedding-text builders |
| `NessieAI/schema_rag/errors.py` | five symbolic codes (`NessieAI/schema_rag/errors.py:16-20`), their user-facing templates (`NessieAI/schema_rag/errors.py:26-32`) and one exception class (`NessieAI/schema_rag/errors.py:56`) |
| `NessieAI/schema_rag/__init__.py` | three re-exports and the rebuild described above |

**Ingestion**, in seven steps set out at `NessieAI/schema_rag/service.py:282-289`:
sweep expired files, fetch and resolve the document, flatten its operations, refuse a
document over the ceiling, create the session, write the rows, answer. Each operation
becomes one row; an operation with no `operationId` gets one synthesized from its method
and path (`NessieAI/schema_rag/service.py:231-233`), a per-endpoint exception only
skips that endpoint (`NessieAI/schema_rag/service.py:262-268`), and any path matching
a configured pattern is dropped before parsing
(`NessieAI/schema_rag/service.py:212-214`), which is how the two endpoints of this
feature stay out of their own index (`dmac/settings.py:502-504`).

**Retrieval** embeds the query once (`NessieAI/schema_rag/service.py:784`) and then
runs at most three scoring passes, each a strictly richer text for the same endpoints:
identifier and description only, then the same plus example strings, then parameters and
the simplified request body as well. A pass runs only when its predecessor yielded nothing
above the threshold (`NessieAI/schema_rag/service.py:805-825`). Response bodies are
stored but deliberately never embedded
(`NessieAI/schema_rag/models.py:96`,`NessieAI/schema_rag/models.py:140`). Scores
are dot products (`NessieAI/schema_rag/service.py:478`) of vectors the embedder has
already divided by their own norms (`NessieAI/schema_rag/service.py:173-176`), which
is cosine similarity, lifted by a fixed bonus for each caller-supplied term found in the text
(`NessieAI/schema_rag/service.py:436-447`). A request naming a `schema_url` whose
session is missing or expired ingests it first
(`NessieAI/schema_rag/service.py:744-751`), so first use needs no prior call.

**Storage.** One DuckDB file per session, named for the session id under one shared
directory (`NessieAI/schema_rag/session.py:45`), holding `session_meta` and
`endpoints`. Reads open the file read-only (`NessieAI/schema_rag/db.py:189`); writes
bulk-load through a pandas frame for the reason given at
`NessieAI/schema_rag/db.py:109`.

**Self-schema.** A URL naming this instance's own schema route is answered from an
in-process generator instead of over HTTP (`NessieAI/schema_rag/schema_processor.py:299-305`),
because that route requires authentication (`nextseek_api/urls.py:65`), which the fetch
path's own comment gives as the reason no credential can be attached
(`NessieAI/schema_rag/schema_processor.py:294-298`), and because generation returns
None rather than raising so the HTTP path still stands
(`NessieAI/schema_rag/schema_processor.py:189-192`). Recognition demands the whole
origin and the path (`NessieAI/schema_rag/schema_processor.py:178-180`). Separately, a
URL on one of this app's public hostnames has its scheme and authority swapped for
`NEXTSEEK_INTERNAL_BASE_URL` before the fetch
(`NessieAI/schema_rag/schema_processor.py:221`), and the value the session records is
still the one the caller sent (`NessieAI/schema_rag/service.py:362`).

## Running and testing

Every test is in `NessieAI/tests/schema_rag/`, and the commands are the Django lane in
`NessieAI/tests/README.md` (its `schema_rag` bullet). There are two lanes.

**The hermetic lane** is every module there except the live one. It wants no live
database and no network, but it does want two things a fresh container will not hand
it: a populated embedding-model cache, and `HF_HUB_OFFLINE=1`. Without them the
embedder reaches for Hugging Face, and on a container with networking off those tests
fail.

Both causes are visible in the repository. The sentence-transformers cache is resolved
under `BASE_DIR` (`dmac/settings.py:500-501`), so relocating the checkout inside a
container aims it at a directory `dmac/settings.py:508` has just created empty; and
even with a populated cache in reach, the loader still opens an HTTP conversation with
Hugging Face unless `HF_HUB_OFFLINE` is set. The supported lane already encodes both
(`startup/dev/run_full_test_lane.sh:41-49`), fails fast instead of downloading
(`startup/dev/run_full_test_lane.sh:196-204`), and points at
`startup/dev/provision_embedding_model.sh:196` for populating the cache once per checkout.
Supplied, the hermetic selection goes green, and the only tests still not executed are
the live ones.

**The live lane** is `NessieAI/tests/schema_rag/test_schema_rag_live.py`. Its tests fetch
the real FAIRDOM SEEK document (`NessieAI/tests/schema_rag/test_schema_rag_live.py:39`)
and are skipped unless `RUN_SCHEMA_RAG_LIVE=1` is set
(`NessieAI/tests/schema_rag/test_schema_rag_live.py:41`,
`NessieAI/tests/schema_rag/test_schema_rag_live.py:45`), so a hermetic run skips them at
no cost. They need outbound egress to fairdomhub.org. Their hermetic counterpart serves a
vendored fixture instead, as their own docstring explains
(`NessieAI/tests/schema_rag/test_schema_rag_live.py:4-8`).

## Depends on / depended on by

Depends on, outside this directory:

- Eight third-party distributions besides Django, found by reading every module-scope
  import line in the non-test modules here, all declared at the repo root: `duckdb` (`pyproject.toml:36`), `jsonref` (`pyproject.toml:56`), `numpy`
  (`pyproject.toml:68`), `pandas` (`pyproject.toml:74`), `pydantic` (`pyproject.toml:80`),
  `PyYAML` (`pyproject.toml:88`), `requests` (`pyproject.toml:92`) and
  `sentence-transformers` (`pyproject.toml:101`).
- `nextseek_api/models.py`, in both directions at once: this package imports seven public
  request and response models from it (`NessieAI/schema_rag/service.py:24-32`) while
  that module names two classes of this one (`nextseek_api/models.py:2071`,
  `nextseek_api/models.py:2190`).
- Seven settings keys defined at `dmac/settings.py:494-504`, of which the DuckDB directory
  is load-bearing input as well as output: `NessieAI/schema_rag/session.py:45` joins
  every session path onto it and `NessieAI/schema_rag/service.py:675-681` scans it whole to
  match a schema URL, so moving it strands every live session.
- Django settings and URL resolution, reached lazily so the modules stay importable alone:
  `NessieAI/schema_rag/schema_processor.py:138-140` reverses this app's schema route
  by name, which `nextseek_api/urls.py:65` declares under the namespace set at
  `nextseek_api/urls.py:7`.
- `drf_spectacular`, imported inside the generator function rather than at module scope
  (`NessieAI/schema_rag/schema_processor.py:195`), so its absence downgrades to an HTTP
  fetch instead of an import error.
- Four environment variables read directly rather than through settings
  (`NessieAI/schema_rag/schema_processor.py:113-119`). The app service takes its
  environment from a rendered file (`docker-compose.yml:17-19`) whose template sets three
  of the four (`startup/templates/nextseek.env.template:11`,
  `startup/templates/nextseek.env.template:21` and
  `startup/templates/nextseek.env.template:27`), so the internal-URL rewrite is armed on a
  default install. The fourth, `NEXTSEEK_PROD_URL`, is assigned nowhere in this repo: a
  tree-wide grep for that name returns only reader lines under `NessieAI/chat_nextseek/`,
  such as `NessieAI/chat_nextseek/cli.py:90`, and no line that sets it.

Depended on by. Derived from a repo-wide grep for `schema_rag` restricted to lines that
begin an import, then a second unrestricted pass over the same tree for the name in
strings and configuration. Test modules that merely exercise this code are omitted; the
two that constrain it are kept.

- `nextseek_api/services/schema_rag.py:27` and `nextseek_api/services/schema_rag.py:28` are
  the only module-scope imports of this package from outside it in non-test code; the one
  other non-test importer is `nextseek_api/models.py`, listed above, and both of its
  imports sit inside a guard or a function body. That ViewSet module is itself pulled in
  unguarded by the re-export hub at `nextseek_api/views.py:60`, so an import failure here
  takes the whole URL prefix down rather than these two routes.
- `NessieAI/tests/cc/test_cc_context_drift_guard.py:566-568` pins this
  directory's `service.py` and two of its function names as literal strings, then parses
  the file with `ast` to assert the retrieval entry point still calls the ingestion one
  (`NessieAI/tests/cc/test_cc_context_drift_guard.py:612-626`).
- `nextseek_api/tests/test_models_coverage.py:469-492` (`TestRebuildSchemaRagModels`) is
  the only test of the rebuild helper: it checks that the rebuild runs, that
  `RetrieveResponse` validates afterwards, and that a missing `NessieAI.schema_rag.models`
  raises `ImportError`.
- `ci/routes.py:828-832` declares the retrieval route to the CI registry and records in its
  own note that the route answers 200 whatever happens.
- `startup/dev/provision_embedding_model.sh:196` and
  `scripts/attribute_api_test.sh:215-221` both write the cache directory this package
  reads, the second after deleting a copy of it that the caller cannot write.
- What a hit here is NOT. `nextseek_api/services/assistant.py:542` defines a `delete_session`
  that is a chat ViewSet action and has no relation to
  `NessieAI/schema_rag/session.py:172`. The `min_api_endpoints.json` catalog and
  `NessieAI/tests/nessie_tests/FAMILIES.json` carry this feature's URL paths as data for an agent, not as
  a code edge.
- Excluded deliberately: the hermetic test modules in `NessieAI/tests/schema_rag/`,
  which import this package the ordinary way (`NessieAI/tests/schema_rag/test_schema_rag_unit.py:23-32`
  is typical) but constrain nothing beyond their own assertions.
