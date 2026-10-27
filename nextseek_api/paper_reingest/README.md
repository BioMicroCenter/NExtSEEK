# nextseek_api/paper_reingest/

The Django half of paper reingest: the run row, the Celery tasks that run stages 1 and 2, the web loop that runs
the curation agent and stage 4, the seams the `paper-reingest-start` op runs with, and the operator commands. The engine (identify, launch check,
fetcher, deposit adapters, evidence, skeleton) is [`NessieAI/ns/paper/`](../../NessieAI/ns/paper/README.md); read its README for the
artifacts and the evidence IDs.
The invariants and landmines are in [CLAUDE.md](CLAUDE.md).

## Surface

| File | What it does |
|---|---|
| `models_db.py` | `PaperReingestRun`, table `paper_reingest_run` under the `nextseek_api` app label (migrations `0034_paper_reingest_run`, `0035_paper_reingest_run_live_key`, `0036_paper_reingest_run_curation_states`). One row per run: the owner, the paper's ids and title, `mode`, `state`, the failure stage and reason, `artifacts` (artifact name to content-addressed key), `attempts` (stage 1's tries), `curation_attempts` (the web loop's claims), `superseded_by` and `live_key` (unique while the run is unfinished); `LIVE_STATES` and `paper_key` |
| `service.py` | `create_run` (writes `paper.json` before the row exists; raises `RunAlreadyLive` when another run of the paper is unfinished), `enqueue_acquire`, `enqueue_context`, `set_state`, `transition` (a move that happens only from the states named), `unfinished_run_for` |
| `tasks.py` | `acquire_task` and `run_acquire` (stage 1), `after_acquire` (queues stage 2), `context_task` and `run_context` (stage 2), all on the `paper_reingest` queue |
| `context_sources.py` | the reads stage 2 needs, as `ContextSeams`: live sample types with their attributes and parents, house assays, projects with their PI text, members and institutions, project export rows, precedent studies. Each is strict: a database that does not answer raises `ContextSourceDown` |
| `launcher.py` | stages 3 and 4: stages the agent's inputs into the CC users volume, starts it through `cc-spawner` (no turn pass, no login), reads `curation.json`, runs stage 4, bounces errors back once, saves `curation.json` and `verified.json` |
| `host.py` | the seams `paper-reingest-start` runs with: `enabled` (the flag), `read_input` (the attached PDF), the launch fetcher and its deadline, `start_session` |
| `lookups.py` | `doi_project_ids_strict`: SEEK projects whose samples carry this DOI. An outage raises; it is never read as "not in NExtSEEK" |
| `tests/` | the Django-lane tests for all of the above |

The operator command is `manage.py paper_reingest`, in `nextseek_api/management/commands/paper_reingest.py`
(Django finds commands only under an installed app's `management/`).

## The run and its states

`PaperReingestRun.state` is one of `queued`, `acquiring`, `context`, `contextualizing`, `curation_pending`,
`curating`, `verifying`, `building`, `ready`, `applying`, `applied`, `paused`, `failed`, `superseded` and
`partially_applied`. Pieces 1 and 2 use those up to `building`, plus `paused` and `failed`:

```
queued -> acquiring -> context          (stage 1 done; evidence.json is saved)
              |
              +-> paused -> acquiring   (a source did not answer; the task retries later)
              +-> failed                (anything else, or the retries ran out)
queued -> failed                        (stage 1 could not be queued: failed_stage queue)
acquiring -> failed                     (a restart cut stage 1: the boot sweep, failed_stage acquire)
context -> contextualizing -> curation_pending    (stage 2, the worker: context.json is saved)
contextualizing -> context              (NExtSEEK's database did not answer; the task retries later)
contextualizing -> failed               (the retries ran out, anything else, or a restart: failed_stage context)
curation_pending -> curating -> verifying -> building   (stages 3 and 4, the web loop: curation.json, verified.json)
curating -> curation_pending            (cc-spawner unreachable, the web container stopped, or the claim went stale)
curating | verifying -> failed          (failed_stage curate: no usable curation.json twice, or the claims ran out;
                                         failed_stage verify: a structural problem stage 4 cannot repair)
any live state -> failed                (the operator cancelled it: failed_stage cancelled)
```

`LIVE_STATES` are the states a second start of the same paper is refused in (`already_running`), by any curator:
the paper is its DOI, case folded, or the input as given when it has none, and a unique `live_key` makes the
database refuse a second unfinished row even when two starts race. A run
that reaches `building` waits there: sheets, the review and apply arrive in pieces 3 and 4. Until the review op
exists, read a run with the command below.

## The task and the queue

`acquire_task` is the Celery task `paper_reingest.acquire`, routed to the queue `paper_reingest` and served
by its own worker with concurrency 1 (`docker/scripts/entrypoint.sh`, `DEPLOYMENT.md` "Paper reingest
worker"). `run_acquire` is the body. It claims the run (`queued` or `paused` to `acquiring`, one conditional
update that also counts the attempt), loads `paper.json`, calls `acquire`, saves `evidence.json` and sets `context`
only if the run is still `acquiring`: a run cancelled meanwhile stays cancelled, and a second message is skipped. A `SourceUnavailable` sets
`paused` and the task retries after `PAPER_REINGEST_PAUSE_S`; once `PAPER_REINGEST_PAUSE_RETRIES` retries are
spent the run fails at stage `acquire`. Any other exception fails the run at once, with its class and message
as the reason.

Stage 2 is `context_task` (`paper_reingest.context`), queued by `after_acquire` once stage 1 has set `context`.
`run_context` claims the run (`context` to `contextualizing`), builds `context.json` through
`context_sources.context_seams()` and leaves `curation_pending`. A `ContextSourceDown` puts the run back to
`context` and the task retries on the same schedule as a pause; past the retries it fails at stage `context`.

## The curation loop

Stages 3 and 4 run in the **web** container, the one service that reaches `cc-spawner`; the worker never gets
spawner access. `manage.py run_paper_curation` starts from `docker/scripts/entrypoint.sh` in web mode only when
`NEXTSEEK_PAPER_REINGEST=1`, and restarts itself after a crash. Each tick:

- does nothing while the web drain flag (`$NEXTSEEK_TURN_DIR/.draining`) is set;
- sweeps stale curations: a run held in `curating` or `verifying` longer than two launches plus 15 minutes lost its
  web container, so its containers are stopped and it goes back to `curation_pending`, or fails once
  `curation_attempts` reaches `MAX_LAUNCH_ATTEMPTS` (3);
- does nothing while any run is `curating` or `verifying`: one curation at a time per instance;
- otherwise claims the oldest `curation_pending` run and runs `launcher.curate` to the end.

`curate` stages `paper.json`, `evidence.json`, `context.json` and `rulebook.json` into
`paper-reingest/<run_id>/input` on the CC users volume (`/data/input` in the container, read-only) and starts the
agent with project dirname `paper-reingest` and the run id as its user id, so the spawner's existing contract gives
each run its own folders. The agent writes `/data/scratch/curation.json`. Errors bounce back once, with
`previous_curation.json` and `findings.json` staged beside the inputs; what still fails is blanked, dropped or held
by `verify.verify`. On SIGTERM (the drain) the container is stopped and the run goes back to `curation_pending` for
the next web container. The model cost is logged to `logs/django.log`, not stored.

## The op's seams

`paper-reingest-start` (`NessieAI/ns/paper/start.py`) takes a `session` object and touches nothing else.
`host.start_session` builds it from the request: the user, the flag check, the launch seams (the fetcher with
one deadline for the whole check, the names of the PDFs in the caller's input folder, the PDF reader and the
DOI lookup), the unfinished-run lookup, `create_run` (which records the caller's running Container-CC chat),
`enqueue_acquire` (queued only after the run row is committed) and `time_left`. If the queue cannot take
stage 1, `host.queue_or_fail` fails the run at stage `queue` and the op refuses with `queue_unavailable`; when the
enqueue waits for a commit, the run is still failed but the op has already answered. The op's contract is in
`nextseek_api/assistant/CONTRACT.md`; its route is in `docs/endpoint-authorization-register.md`.

## The command

```
manage.py paper_reingest start --user <username> --paper <pmid|pmcid|doi|link> [--project N] [--inline]
manage.py paper_reingest status <run_id>
manage.py paper_reingest export <run_id> paper|evidence|context|curation|verified --out <path>
manage.py paper_reingest cancel <run_id>
manage.py paper_reingest requeue <run_id>
manage.py sweep_paper_reingest_runs
manage.py run_paper_curation [--once] [--interval S]
```

- `start` needs an existing superuser and runs the launch check first. `--paper` takes a PMID, PMCID, DOI or
  article link; an attached PDF cannot be named on the command line. Without `--inline` it queues stage 1 on the
  worker. With `--inline` it runs stage 1 in the same process (no retries: a source outage fails the run), which is
  how a golden paper's `evidence.json` is captured.
- `status` prints one line (`<run_id>  <state>  <mode>  attempts=<n>  <title>`), the failure reason if any, and the
  evidence counts once stage 1 is done, the chosen project, lab code and match counts once stage 2 is done, and the
  row, held, gap, finding and UID counts once stage 4 is done.
- `export` writes one artifact (`paper`, `evidence`, `context`, `curation` or `verified`) to a file you name.
- `start` refuses a paper with an unfinished run, whoever started it, and a queue that refuses stage 1 fails the run at
  stage `queue` and the command exits with an error, as the op does.
- `cancel` sets a run in any live state (`LIVE_STATES`) to `failed`, with `failed_stage` `cancelled` and the reason
  `cancelled by operator`. It refuses a run that is already finished or failed, including one that finished while the
  command ran.
- `requeue` puts a `queued` or `paused` run back to `queued` and queues stage 1 again. Use it for a run whose message
  was lost. An `acquiring` run is refused, since a worker may still be running it: the boot sweep fails one a restart
  cut, and a stuck one is cancelled and started again. Any other state is refused. If the queue is down the run is
  left `queued`.

- `sweep_paper_reingest_runs` runs once at boot, from `docker/scripts/entrypoint.sh`, before the paper worker starts.
  It fails every `acquiring` run at stage `acquire` and every `contextualizing` run at stage `context` ("interrupted
  by a server restart"), queues stage 1 again for `queued` and `paused` runs and stage 2 for `context` runs. The
  curation states belong to the web loop, which sweeps its own. A failure of it is logged and the boot goes on.
- `run_paper_curation` is the web loop above; `--once` runs one tick.

These commands print counts and ids, never the paper's text or a deposit's content.

## Environment

| Variable | Default | Does |
|---|---|---|
| `NEXTSEEK_PAPER_REINGEST` | `0` | `1` lets a superuser start a run; otherwise the op answers `feature_off`. Set it in `nextseek.env` |
| `NEXTSEEK_PAPER_REINGEST_DIR` | `<NEXTSEEK_OUTPUTS_DIR>/paper_reingest` | where each run's artifacts live (one folder per run) |
| `PAPER_REINGEST_PAUSE_S` | `600` | seconds a paused run waits before stage 1 tries again |
| `PAPER_REINGEST_PAUSE_RETRIES` | `12` | retries before a paused run fails at `acquire` |

| `PAPER_REINGEST_CURATION_TIMEOUT_S` | `1800` | seconds one curation agent launch may take before the loop stops it |
| `PAPER_REINGEST_CURATION_MAX_TURNS` | `200` | agent turns one launch may take (at most `NEXTSEEK_CC_MAX_TURNS_CEILING`, 300) |
| `PAPER_REINGEST_CURATION_BUDGET_USD` | `15` | model spend cap for one launch (at most 100) |

A bad value for the numeric ones logs a warning and falls back to the default; it never stops the app starting.

## Running and testing

The Django lane, as `ci/README.md` "Running and testing" gives it, with
`PATHS="nextseek_api/paper_reingest/tests NessieAI/tests/ns NessieAI/tests/api/test_nessie_boundaries.py"`.
The engine's own tests are the host lane in `NessieAI/ns/paper/README.md`.

## Depends on / depended on by

- Depends on `NessieAI/ns/paper/` (the engine), `NessieAI/cc` (the input folder, the upload name check, the agent
  environment and the `cc-spawner` client), `dmac_assistant.router.models` (the agent's model ids),
  `nextseek_api.assistant.chat_turns` and `nextseek_api.services.assistant` (both lazily, from `host.py`), the
  shared Celery app in `nextseek_api/batch_upload/celery_app.py`, SEEK's tables (read only, from `lookups.py` and
  `context_sources.py`), and `nextseek_api.services` (`context_catalog`, `reingest_lookups`, `project_export`).
- Called by `NessieAI/ns/granular.py` and `nextseek_api/services/assistant.py` (the op) and by the operator command.
