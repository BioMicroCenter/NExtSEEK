# nextseek_api/paper_reingest/: what will bite you

## Invariants

- **The attached PDF is read only through `host.read_input`.** It validates the file name, opens with
  `O_NOFOLLOW` and refuses a file over `MAX_PDF_BYTES`; the folder is the caller's own CC input folder.
  Reading a path any other way turns a file name typed in chat into a read of any file the app can open.
- **Only the superuser gate (`is_superuser`) and the env flag open the op.** Both checks live in
  `NessieAI/ns/paper/start.py`, because the op never calls the write gate. `is_staff` is true for every SEEK login
  and must never stand in for it. The CLI refuses a non-superuser the same way.
- **A source outage pauses a run; it never fails one until the retries are spent.** `run_acquire` turns
  `SourceUnavailable` into `paused`, and only `retries_done >= max_retries` turns it into `failed`. Any other
  exception fails the run at once. Every path out of `run_acquire` must leave the run in a state other than
  `acquiring`.
- **The curation container holds no turn pass and no login** (user ruling 2026-10-27). `launcher._body` builds its
  environment with `api_user=None` and `turn_pass=None`; everything the agent reads is staged into
  `/data/input`, and its only tools are the local `nextseek-evidence-search` and `nextseek-curation-check`. Giving it
  a pass would mean holding a superuser's SEEK password for the length of a run.
- **Only the web loop starts curation containers.** `run_paper_curation` runs in web mode only, the one service with
  `NEXTSEEK_SPAWNER_URL` and its token; the worker never gets spawner access (`test_compose_web_service.py`). Stage 2
  runs in the worker and stops at `curation_pending` for that reason.
- **A worker, the operator and the boot sweep move a run only with `service.transition`.** It is one UPDATE filtered
  on the states the move is from, so the writer that lands second changes nothing: a run cancelled while stage 1
  runs stays cancelled, and a second message for a claimed run is skipped. `set_state` writes whatever the row holds
  and is only for code that owns the row outright (tests, a run no one else can see yet).
- **One unfinished run per paper, whoever starts it, and the database enforces it.** `live_key` is
  `models_db.paper_key` (the DOI case folded, else the input) while the run is in `LIVE_STATES` and not superseded,
  NULL otherwise, under a unique index (NULLs never collide). `PaperReingestRun.save` and `transition` keep it in
  step; an `.update()` that changes `state` or `superseded_by` must set it too. `create_run` turns the index's refusal
  into `RunAlreadyLive`, which the op answers as `already_running`. A later piece that supersedes a run must finish
  the old run (free its key) before it creates the new one, in one transaction.
- **A run row exists only with its `paper.json`.** `create_run` writes the artifact before it saves the row, and
  the op queues stage 1 only after the row is committed (`host.start_session`, `transaction.on_commit`). A
  queue that refuses the task fails the run at stage `queue` (`host.queue_or_fail`) instead of leaving it
  `queued` with no task behind it.
- **Nothing here writes to SEEK or the graph.** The run row and the files under `NEXTSEEK_PAPER_REINGEST_DIR` are
  the only writes. The engine package never imports Django or `nextseek_api` (`NessieAI/CLAUDE.md`).

## Landmines

- The operator command is in `nextseek_api/management/commands/`, not here: `paper_reingest` is not an installed
  app, so a `management/` folder in this package is never found.
- `manage.py paper_reingest start --inline` runs stage 1 in the calling process with no retries, so run it
  where the network is open and expect a failure, not a pause, on an outage.
- The status and export commands print counts and write files; do not make them print the paper or a deposit
  to the terminal.
- **A run cut by a restart is put right only at boot.** `manage.py sweep_paper_reingest_runs` (the entrypoint, before
  the paper worker starts) fails every `acquiring` run at `acquire` and queues `queued` and `paused` runs again. Between
  boots a lost queue message still leaves a run `queued`, blocking a new start of the paper (`already_running`); the
  operator recovers it with `manage.py paper_reingest cancel <run_id>` or `requeue <run_id>` (README "The command"). A
  re-queued `paused` run starts a fresh retry budget, and if its old message survived it can run twice; the claim keeps
  the two from overlapping.
- The PDF cap in `host.py` and the launch deadline (the op's 60 s in `NessieAI/ns/op_limits.py` less
  `ANSWER_MARGIN_S`) are tied to the op's limit; change them together.

## Test command

```
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --memory 3g --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -e GCP_API_KEY=dummy -e CATALOG_FILE=/work/NessieAI/chat_nextseek/agent_model_catalog.json \
  -v "$PWD":/src:ro -w / nextseek-nextseek:latest bash -lc \
  "cp -a /src /work && cd /work && cp startup/dev/lane_local_settings.py dmac/local_settings.py && \
   /app/.venv/bin/python -m pytest nextseek_api/paper_reingest/tests -q -p no:cacheprovider"
```

The engine's tests are the host lane in `NessieAI/ns/paper/README.md`.
