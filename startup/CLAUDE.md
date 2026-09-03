# Working in `startup/`

## Invariants

Each of these is load-bearing for bootstrap, for a deploy, or for a live database.
Breaking one is a regression, not a refactor.

- **`mysqlclient` must never become a dependency of this project.** The reason is
  recorded beside the driver pin at `startup/pyproject.toml:13-22`, and the code keeps
  it optional at runtime by trying `MySQLdb` and falling back to the declared pure-Python
  driver (`startup/steps/schema_fixups.py:898-912`). Adding the C extension makes
  `./startup.sh` fail to bootstrap on a machine with no compiler and no MySQL headers,
  which is exactly the machine this isolation exists to serve.
- **This project never imports `ci/`.** The three profile names are restated in place
  with that reason attached (`startup/cli.py:40-44`), as is the credential path
  (`startup/steps/doctor.py:10-14`), and the suite itself is launched as a subprocess
  (`startup/ci/runner.py:1-5`). An import would pull pytest, requests and playwright
  into the bootstrap environment and undo the invariant above.
- **A command body must be a delegate, or take real values.** Calling an
  `@app.command()` function directly in Python leaves any unpassed parameter as a
  `typer.models.OptionInfo`, which is truthy; the guard at `startup/cli.py:64-83` raises
  on one. That exact leak once made `reset` skip every seed over freshly-wiped volumes
  (`startup/cli.py:69-71`).
- **`reset` passes every install parameter explicitly** (`startup/cli.py:497-507`),
  including `no_seed=False` and the carried-over CI profile. Omit one and it arrives as
  the truthy sentinel above, on a stack whose volumes have just been dropped.
- **Runtime-service selection accumulates across compose profiles, never chooses**
  (`startup/lib/rebuild_policy.py:65-70`). An early return per profile would leave one
  worker on its old container under `restart: unless-stopped`, which is the failure the
  docstring at `startup/lib/rebuild_policy.py:59-63` describes as looking healthy.
- **An absent or empty `ci_profile` resolves to the narrowest value, `prod`** — at the
  install default (`startup/cli.py:46-47`), at the runner
  (`startup/ci/runner.py:54`) and in the diagnostic (`startup/steps/doctor.py:31-36`).
  Defaulting the other way would let a machine nobody configured run write routes.
- **The off-box baseline push can never fail a deploy.** The contract is stated at
  `startup/steps/registry_push.py:8-12` and enforced by the blanket handler wrapping the
  whole step (`startup/cli.py:616-628`). A registry outage must not strand a rebuilt
  stack mid-deploy.
- **Managed-index DDL stays opt-in.** `startup/steps/schema_fixups.py:980-993` spells
  out what unconditional application costs: a stock install aborting after the seeds are
  in and the containers are up, on real data, with no remediation path.
- **A hand-filled Bedrock token is never reset to empty by a re-run**
  (`startup/steps/config.py:190-194`). The precedence is operator environment, then the
  existing file, then empty.
- **No broad exception handler is permitted on the index readiness, apply or reverse
  path** (`startup/steps/schema_fixups.py:27-29`). A swallowed connectivity error would
  be reported as an absent index, and the next step would try to create one that is
  already there.
- **The repo-root `.env` is written from a fixed key allowlist**
  (`startup/steps/config.py:230-239`), for the reason given at
  `startup/steps/config.py:213-214`: a secret sitting in the instance's environment
  would otherwise be persisted into a file that lives in the working tree.

## Landmines

- **Schema fixups are applied by `install` and by nothing else.** `apply_all` has
  exactly one call site in the CLI, `startup/cli.py:288`, inside the install body; a
  grep for `schema_fixups` across `startup/cli.py`, `startup/lib/` and `startup/steps/`
  returns no other invocation, and the rebuild command at `startup/cli.py:511-657`
  contains none. Nothing reports the gap either: the eight post-install health checks
  (`startup/steps/validate.py:214-223`) and the diagnostic
  (`startup/steps/doctor.py:79-111`) never look at a fixup table. So a table added to
  the registry never reaches a box that is only ever rebuilt, arrives from no seed dump,
  and produces no error — the feature is silently dark until somebody runs `install` or
  the destructive `reset`.
- **The tests here import a plugin that most hosts cannot load.**
  `startup/tests/conftest.py:7` registers `nextseek_api.attributes.tests.attribute_fixtures`,
  which reaches `MySQLdb` at module scope through
  `nextseek_api/attributes/tests/real_boundary.py:17`. A plain `pytest startup/tests/`
  dies in plugin import before collecting anything, which reads as a broken suite rather
  than a missing host package. See the section below for the invocation that works.
- **Two tests fail wherever they run, and both are test-side.**
  `startup/tests/test_schema_fixups_tables.py:98` queues three canned replies for a
  registry that now holds five entries (`startup/steps/schema_fixups.py:109-152`), each
  consuming one reply at `startup/steps/schema_fixups.py:226`, so the fourth gets an
  empty string and `startup/steps/schema_fixups.py:171` raises `IndexError`. And
  `startup/tests/test_schema_fixups_coverage.py:213-218` stubs only the column pass while
  `apply_all` runs the table pass first (`startup/steps/schema_fixups.py:1002`), against a
  literal `/repo` that does not exist. Do not read either failure as a regression you
  introduced.
- **A named developer's home directory is hardcoded** at
  `startup/steps/schema_fixups.py:72`. Blast radius measured for this directory on
  2026-09-03: zero test failures, because the one test that touches the constant
  monkeypatches it to a nonexistent path (`startup/tests/test_schema_fixups_coverage.py:108-110`).
  The damage is silent instead. `startup/steps/schema_fixups.py:567-571` returns `None`
  whenever that file is absent, so on every other machine the telemetry record's
  `base_sha` field (`startup/steps/schema_fixups.py:663`) is null and the record cannot be
  tied back to a source revision. Its neighbour at `startup/steps/schema_fixups.py:665`
  has the same shape, falling back to a hardcoded image digest
  (`startup/steps/schema_fixups.py:71`) that no reader can have built.
- **The DatabaseError lookup has no pure-Python fallback**, unlike the driver lookup:
  `startup/steps/schema_fixups.py:47-56` imports from `MySQLdb` unconditionally. Python
  evaluates an `except` expression only when something is raised, so on a host with only
  the fallback driver and the managed-index flag on, the first database error inside
  `startup/steps/schema_fixups.py:530-535` or `startup/steps/schema_fixups.py:716-723`
  surfaces as an `ImportError` that hides the error it was meant to handle.
- **A second checkout installed with no `--instance` silently attaches the first one's
  volumes.** The instance name defaults to the checkout directory's own basename
  (`startup/lib/instance.py:46-50`), the prefix is empty whenever the name equals that
  basename (`startup/cli.py:144`), and the compose project then resolves to plain
  `nextseek` (`startup/cli.py:165`). Both trees end up driving the same unprefixed
  external volumes, so a `reset` in one destroys the other's data.
- **Re-running `install` rotates the Django secret key.** A fresh key is minted on every
  call (`startup/steps/config.py:123`) and written into the settings overlay
  (`startup/steps/config.py:165-170`), invalidating every live session. That is why the
  diagnostic tells operators to hand-edit the state file instead
  (`startup/steps/doctor.py:34-35`).
- **One misspelled key in the hand-edited state file breaks every command.**
  `startup/lib/instance.py:63-68` splats the JSON straight into the dataclass with no
  guard, so an unexpected field raises `TypeError` inside `doctor`, `rebuild` and
  `reset` alike — including the very command an operator would run to find out what went
  wrong.
- **Template rendering silently tolerates an unknown placeholder.**
  `startup/steps/config.py:146` uses `safe_substitute`, which leaves the literal `${…}`
  in the output file rather than raising. That is why reading a value back has to sniff
  for the residue (`startup/steps/config.py:63-65`); a rendered env file can look
  complete and still contain an uninterpolated token.
- **The vendoring script deletes.** `startup/scripts/sync_chat_nextseek.sh:36-45` runs
  `rsync -a --delete` into the vendored `chat_nextseek/` tree, so any uncommitted local
  edit there is gone with no prompt and no backup.
- **The frozen full-lane script's own header contradicts the pin it enforces.**
  `startup/dev/run_full_test_lane.sh:38-40` names one image and one digest; the constants
  actually checked are a different image and a different digest
  (`startup/dev/run_full_test_lane.sh:106-107`), and the preflight compares against those
  (`startup/dev/run_full_test_lane.sh:146`). Trusting the header wastes a build.
- **The Django settings overlay exists twice, and nothing keeps the copies in step.**
  Install renders `dmac/local_settings.py` from the template at
  `startup/steps/config.py:166`, while the full lane bind-mounts its own file over that
  same path (`startup/dev/run_full_test_lane.sh:114` and
  `startup/dev/run_full_test_lane.sh:240`). The two are byte-identical today, verified
  2026-09-03 by running `cmp startup/dev/lane_local_settings.py
  startup/templates/local_settings.py.template`, which reported no difference; no test
  compares them. Edit one and the lane silently exercises a different settings shape
  from the one every install produces.
- **A skipped managed index prints as a normal green line.** `startup/cli.py:289`
  selects the warning style only for `applied` and `constraints reset`, so the `skipped`
  status every index returns when `NEXTSEEK_APPLY_MANAGED_INDEXES` is unset
  (`startup/steps/schema_fixups.py:1004-1012`) is rendered with the ok style. An operator
  watching an install sees green and concludes the indexes were created. The flag is
  read at `startup/steps/schema_fixups.py:995` and is true only for `1/true/yes/on`;
  its own docstring calls it opt-in, default off (`startup/steps/schema_fixups.py:979-994`).
- **Three of the eight DDL files in `startup/seed/sql/` are wired to nothing.** A
  recursive grep of the worktree for the three basenames
  `sample_attributes_description.sql`, `sample_attributes_unique_data.sql` and
  `ROLLBACK_sample_attributes_description.sql`, excluding `.git/`, `node_modules/`,
  `.venv/` and this pair's own files, matches nothing outside `startup/seed/sql/`: no
  fixup entry lists them (`startup/steps/schema_fixups.py:109-152`), no test reads them
  and no script applies them. They are hand-applied or unused.
- **The default credentials are committed, not generated.** `startup/steps/config.py:120-122`
  hardcodes the MySQL root password, the MySQL user password and the Neo4j password into
  every rendered install, and only the Django key is random. An install exposed beyond
  localhost with these untouched is open.
- **`--source-tree` accepts exactly one revision.** `startup/lib/deploy_source.py:44-49`
  refuses any tree whose `HEAD` is not that tree's own `origin/dev`, so a release tag, a
  hotfix branch or a commit one ahead is rejected however clean it is, and the
  remediation printed at `startup/cli.py:571-574` only ever tells you to fast-forward to
  `origin/dev`. There is no flag to deploy anything else from a separate source.
- **`dump-db --source` does nothing.** The option is declared at `startup/cli.py:782` and
  its only use is the banner at `startup/cli.py:794`; the source is whatever
  `startup/seed/regenerate/dump-source.env` holds, checked at `startup/cli.py:788-792`.
  Passing `--source prod` does not point the dump at production, and does not warn.
- **The root project's note about how this CLI runs is out of date.**
  `pyproject.toml:154-157` says it runs `uv run --no-project --with typer --with rich`,
  which would give it neither the neo4j driver nor the MySQL one; the wrapper actually
  execs `uv run --project startup` (`startup.sh:18`). Follow the note by hand and the
  seed and fixup phases fail on a missing import.
- **Seed loading is not streamed.** `startup/steps/seed.py:83` reads the whole gzip into
  memory and decompresses it there before handing the bytes to the container's stdin, so
  peak memory tracks the largest uncompressed dump rather than a buffer.

## Test command

```
cd startup && uv run --project . --group test python -m pytest tests/ -q \
  -p no:nextseek_api.attributes.tests.attribute_fixtures \
  --ignore=tests/test_schema_fixups.py
```

Both suppressions are required and neither is optional bookkeeping: the `-p no:` clears
the conftest plugin, and the `--ignore` clears the module-scope driver import. Dropping
the `--ignore` alone on 2026-09-03 stopped the run at collection with `1 error in 0.19s`.
The `cd startup` is not decoration: `--project .` resolves against the working
directory, so the command picks the wrong project from anywhere else. See
`startup/README.md` for the counts this lane produced, the coverage variant, and what the
two remaining suppression-free lanes need.

## See also

- See `startup/README.md` for the subcommands, the nine install phases, the data
  payload, and the dependency map in both directions.
- See `startup/seed/README.md` for the shipped dumps and the S3-hosted filestore archive.
- See `ci/README.md` and `ci/smoke/README.md` for the smoke suite this CLI subprocesses,
  including the profile-narrowing rule enforced at `ci/smoke/conftest.py:129-169`.
- See `DEPLOYMENT.md` for the deploy runbook the rebuild and registry-push steps
  implement.
- See the repo-root `CLAUDE.md` for the stack layout and the supported entry points.
- See `startup/ATTRIBUTE-INDEX-FIXUPS.md` for the managed-index reference. It was
  refreshed on 2026-09-03 against source; the inversion this file previously warned
  about was corrected in five places, so it is now citable as current.
