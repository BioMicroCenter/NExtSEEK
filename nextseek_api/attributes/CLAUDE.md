# Working in `nextseek_api/attributes/`

## Invariants

Each of these is load-bearing for correctness or for data integrity in SEEK's own
schema. Breaking one is a defect, not a refactor.

- **Planning is write-free and must stay so.** The planner's contract forbids any
  definition, metadata or default-database write, any job creation, any dispatch and
  any lock (`nextseek_api/attributes/planner.py:4-6`). The same code path renders a
  `dry_run` preview (`nextseek_api/attributes/service.py:189-192`), so a write added
  here would fire on a request the caller was told changes nothing.
- **SEEK and the default database are never inside one transaction.** The adapter
  refuses any alias but `seek` (`nextseek_api/attributes/executor.py:476-478`) and the
  audit write happens only after that block has exited
  (`nextseek_api/attributes/executor.py:158-159`). Moving the audit CAS inside the
  SEEK block lets a MySQL rollback erase the record that SEEK rows were committed, and
  recovery then replays a mutation that already happened.
- **The physical fingerprint is rechecked under the lock, not at planning time.**
  `nextseek_api/attributes/executor.py:138-140` re-reads the ordered definition set
  after `lock_type` and aborts on any difference. Without that recheck two plans built
  against the same stale snapshot both write, and the later one silently discards the
  earlier one's definitions.
- **Every claim and every state change is a compare-and-set over the full six-field
  token.** The job claim matches owner, generation, lease version and state version
  together (`nextseek_api/attributes/jobs.py:399-411`), and progress and terminal
  writes re-assert the same tuple
  (`nextseek_api/attributes/jobs.py:462-467`). Dropping one predicate lets a
  redelivered Celery message and a recovery scan hold the same job at once and
  overwrite each other's terminal result.
- **A live lease is never stolen.** Only an expired one is re-claimable
  (`nextseek_api/attributes/jobs.py:413-424`), and a partition claim that is still live
  under the same job waits instead of forcing
  (`nextseek_api/attributes/jobs.py:506-511`). Stealing an unexpired lease puts two
  executors on one sample type.
- **The heartbeat must acknowledge once before any SEEK work opens.**
  `nextseek_api/attributes/jobs.py:310` blocks on the first renewal, which raises if the
  CAS token is already gone (`nextseek_api/attributes/jobs.py:155-160`). Skipping it
  lets a worker whose lease was already recovered open a SEEK transaction it no longer
  owns.
- **Cancellation is checked only between sample types.** The boundary read sits before
  the next type is claimed (`nextseek_api/attributes/jobs.py:316-321`). Adding a
  cancellation check inside a type would abandon a partially applied definition set
  with no fingerprint that matches either the before or the after state.
- **Submitted titles are never trimmed, case-folded or normalized in Python.** The
  grammar classifies and stops (`nextseek_api/attributes/resolver.py:47-49`) and the
  real collation is queried from the live schema
  (`nextseek_api/attributes/repository.py:825-833`). Normalizing in Python collapses
  two titles the database considers distinct, and the mutation then edits the wrong
  attribute.
- **No SQL statement may scale its parameter list with the submitted identifier count.**
  Every bulk lookup is chunked at a fixed cap
  (`nextseek_api/attributes/repository.py:105`), and the gateway raises rather than
  issue a statement above the frozen bound
  (`nextseek_api/attributes/repository.py:515-516`). An unbounded `IN (...)` here is
  the exact pattern this layer was built to remove.
- **A request process must never publish to the queue.** Only the dispatcher loop does
  (`nextseek_api/attributes/management/commands/dispatch_attribute_outbox.py:1-3`), and
  a publish failure returns the row to `pending` rather than losing it
  (`nextseek_api/attributes/jobs.py:190-191`). Publishing from the request would emit a
  message for a job whose creating transaction can still roll back.
- **Nothing here may grow an `AppConfig` or a `migrations` package.** Both models pin
  themselves to the parent app label (`nextseek_api/attributes/models_db.py:206`).
  Giving this subpackage its own app identity makes Django plan a second, duplicate
  copy of the same tables under a new label.

## Landmines

- **The three attribute runtimes are behind a compose profile.** `profiles: [attributes]`
  gates the worker, the dispatcher and the recovery scheduler
  (`docker-compose.yml:339`, `docker-compose.yml:373`, `docker-compose.yml:408`), and
  `startup/lib/rebuild_policy.py:65-70` adds them to the rebuild cohort only when
  `COMPOSE_PROFILES` in the process environment names `attributes`
  (`startup/lib/rebuild_policy.py:35-36`). Without it exported, an asynchronous
  mutation returns its `202` and is then never executed: the outbox row stays
  `pending` and the status endpoint reports `queued` forever
  (`nextseek_api/attributes/service.py:254`).
- **Worse than absent is stale.** Because the three services carry
  `restart: unless-stopped` (`docker-compose.yml:352`) and share the app image, a
  rebuild run without the profile leaves them up on the previous image, executing
  mutations with old code while the web container runs new code.
- **`DEPLOYMENT.md:284` describes that rebuild as unconditional for these runtimes**
  and conditions only the assay-registration worker on an exported profile, so an
  operator following it will believe all three were refreshed when they were not. The
  cohort description at `DEPLOYMENT.md:268-269` reads the same way.
- **The documented sole write surface for job rows is dead code.**
  `nextseek_api/attributes/models_db.py:373-375` states that product code must not call
  `objects.create`, but the live creation path does exactly that
  (`nextseek_api/attributes/jobs.py:565`). A grep for `AttributeMutationAuditStore` over
  every `*.py` in the worktree matches no caller outside tests: only its own module,
  a comment at `nextseek_api/attributes/planner.py:537`, and seven test modules under
  this boundary's `tests/`. So the envelope-provenance checks it performs — the
  submitted-request hash link at `nextseek_api/attributes/models_db.py:406` among them
  — never run on a real request.
- **Production modules carry 16 armed fault-injection points.** Counted 2026-09-03 by
  grepping for statement-position `attribute_fault(` calls outside
  `nextseek_api/attributes/faults.py`: seven in `nextseek_api/attributes/jobs.py:188`
  onward and nine in `nextseek_api/attributes/executor.py:142` onward. They are inert
  only because one environment variable is unset
  (`nextseek_api/attributes/faults.py:50-52`). Setting
  `ATTRIBUTE_TEST_FAULT_CONTROL` in a real container arms real mid-mutation aborts.
- **One test hardcodes an absolute path into another developer's home directory.**
  `nextseek_api/attributes/tests/test_openapi.py:9` points the machine schema contract
  at a path under `/home/taishajo`, so that case cannot pass anywhere else, and its
  failure is not a regression in this package.
- **Six unit cases in `nextseek_api/attributes/tests/test_executor.py` fail on this
  branch with no database involved.** The kernel indexes `outcome["counts"]` at
  `nextseek_api/attributes/executor.py:155` while the suite's own double returns
  `{"status": "succeeded"}` (`nextseek_api/attributes/tests/test_executor.py:99`).
  Treat the executor's unit lane as red until that double is updated; a change you make
  to the kernel will not be caught by a suite that is already failing there.
- **The tests directory's `conftest.py` makes collection itself heavy.** It registers
  two plugin modules (`nextseek_api/attributes/tests/conftest.py:1-4`), and one of them
  reaches `MySQLdb` and `kombu` at module scope
  (`nextseek_api/attributes/tests/real_boundary.py:17-21`), so even the pure-Python
  cases cannot be collected in an environment without those installed.
- **`--no-migrations` breaks one test by construction.**
  `nextseek_api/attributes/tests/test_physical_safeguards_db.py:56` asserts the
  migration node is present in the loader's on-disk graph, which is empty when
  migrations are disabled, so that red is the flag you chose and not a defect you
  introduced; drop the flag if you actually want to exercise the migration.
- **`dmac/attribute_performance_settings.py:36` imports a module that does not exist.**
  A find for any file named `performance_worker_telemetry*` anywhere in the worktree
  returns nothing, and a grep for that name across every `*.py` matches only that one
  import line. Setting `ATTRIBUTE_WORKER_TELEMETRY_RESULTS` under that settings module
  therefore fails at settings import, before Django starts.
- **`IsSeekAdmin` still exists but no longer gates anything.** A grep for the name over
  every `*.py` in the worktree matches four files: its definition at
  `nextseek_api/attributes/auth.py:229`, a comment at
  `nextseek_api/attributes/views.py:122`, and two of this boundary's own test modules.
  No production code references it. The live admin population is Django's `is_superuser`
  (`nextseek_api/attributes/views.py:127-130`), and the two populations are not nested
  (`nextseek_api/attributes/views.py:120-126`), so restoring the SEEK-role class would
  silently change who can write.
- **`can_cancel_job` deliberately omits an admin check.** Re-adding one there would AND
  a second, different admin population onto a gate that is already applied, silently
  defeating it (`nextseek_api/attributes/auth.py:212-216`).
- **The Celery broker is a SQLite file on a volume, and `./startup.sh reset` deletes it.**
  `DEPLOYMENT.md:68-74` records that image rollback tags and the registry hold no copy
  of that volume, so a published but unconsumed message cannot be recovered from
  anywhere after a reset.
- **Deleting a shim under `nextseek_api/management/commands/` silently removes a
  command.** Django never scans this subpackage's own commands directory, so nothing
  here would fail to import; instead the compose service that invokes the command by
  name (`docker-compose.yml:385`) dies on an unknown command. See
  `nextseek_api/attributes/README.md` for all three shims and why they exist.

## Test command

Run the whole directory inside the live container, which supplies the database grant
and the secrets:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run --no-sync python -m pytest nextseek_api/attributes/tests/ --no-migrations -q'
```

Run 2026-09-03 against the image the local stack is running: 12 failed, 484 passed,
360 errors, in 26.06s. All 360 errors are the same missing environment variable in
fixture setup, and the failures break down as six in the executor unit module, two in
the OpenAPI module, and one each in the physical-safeguards, metadata-benchmark,
performance-metadata and real-boundary-contract modules. Note that the container runs
the image's baked copy: comparing per-file MD5 sums showed every production module in
this worktree is byte-identical to the container's, and two test modules
(`nextseek_api/attributes/tests/test_runtime_healthcheck.py:1` and
`nextseek_api/attributes/tests/test_tasks_worker.py:1`) are not.

To reach the real-MySQL cases you must supply the disposable-database environment
first. See `nextseek_api/attributes/README.md` for exactly which variables and what
kind of account.

## See also

- See `nextseek_api/attributes/README.md` for what each module does, how a mutation is
  planned and executed, and the dependency map in both directions.
- See `DEPLOYMENT.md:42` for the service table row covering these three runtimes.
- See `docker-compose.yml:334-429` for the three service definitions in full.
- See `nextseek_api/attributes/planner.py:10-31` for the eight-method repository
  protocol the planner requires.
- See `nextseek_api/attributes/executor.py:796-818` for the adjudicated claim rules.
- See the repo-root `CLAUDE.md` for the stack layout and the supported build commands.
