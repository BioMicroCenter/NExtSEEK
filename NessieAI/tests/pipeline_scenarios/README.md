# NessieAI/tests/pipeline_scenarios/

One list of test stories checks routing and nf-core pipeline choice, as far as a built launch.

A story is a user's question about a cohort. It records the route the router must pick, the pipelines that
are acceptable, the pipelines that must never be offered, and whether a launch must be built. The owner rules on
every story. Only `accepted` or `changed` stories run.
An `off_topic` story with no acceptable pipelines is route-only: it needs no cohort, and it passes when the
route is right.

| File | What it is |
|---|---|
| `stories/<family>.json` | the stories |
| `stories.py` | loader, cohort resolution, format problems |
| `test_cohorts.json` | local-only test cohorts (`test::<name>`); real cohorts come from `NessieAI/chat_nextseek/evals/groundtruth_cohorts.json` |
| `test_story_format.py` | the free format test |
| `scorer.py` | `score`: grades one story's outcome PASS, PARTIAL or FAIL, with the reasons; `test_scorer.py` tests it |
| `scrub.py` | makes a tape safe for a public repository: keeps URLs on public sequence archives (`ARCHIVE_URLS`) and nf-core test data, rewrites every other URL to `<url>` (so no instance hostname is kept), rewrites the runner's own output folder (`OUTPUT_ROOTS`) to `/RUN`, and lists what must be refused (an email, an absolute path); `test_scrub.py` tests it |
| `tape.py` | records, then replays, every answer the pipeline tools get from outside the code under test; a call the tape did not see raises `TapeMismatch`, the signal to re-record; `test_tape.py` tests it |
| `apply_rulings.py` | writes the owner's rulings from the review page into the story files (`python -m NessieAI.tests.pipeline_scenarios.apply_rulings <rulings.json> <yyyy-mm-dd>`); `test_apply_rulings.py` tests it |
| `run.py` | the paid in-process runner: the real router and pipeline agent on each ruled story, no submit tool, every outside answer recorded on a tape. Recording only observes: the live run gets each answer with person fields redacted and nothing else changed (`live`), and the tape keeps the scrubbed copy (`store`). A value the tape must refuse never breaks the live run: the story finishes and is graded on its merits, and its row has no tape and says why in `unrecorded` (`summary.json` lists those stories). `error` is kept for real failures of the run. Without `--yes` it only prints what it would run and the estimate; a paid run needs the owner's go (usage in its docstring) |
| `report.py` | `write_report`: the HTML page of a run, totals and then every story by family |
| `test_run.py` | the free test of the runner and report, with a stubbed router and agent (no model, no stack) |
| `fingerprint.py` | `current()`: sha256 over everything that decides routing and pipeline choice (`INPUTS`); a tape is valid only under the fingerprint it was recorded with. `files()` is a sha256 per input and `changed()` names the ones that moved. `test_fingerprint.py` tests it |
| `record.py` | turns an accepted paid run into `recorded/<id>.json` (`python -m NessieAI.tests.pipeline_scenarios.record <run dir> [--ids a,b]`): refuses a run made under another fingerprint, skips stories the runner failed on, rewrites URLs outside the story as a tape's are, refuses any fixture holding an email or an absolute path |
| `recorded/` | the committed replay fixtures, one `<story id>.json` per recorded story. It does not exist until the first accepted paid run is recorded |
| `test_replay.py` | the free CI replay: each `recorded/*.json` is replayed against the current code and must end with the recorded grade, pipeline, selection and launch settings; a changed fingerprint, story or call fails it until a paid run re-records. Also replays a synthetic recording of a stubbed run, so the machinery is tested before any recording exists. With no recordings the replay test skips |
| `e2e.json` | the ruled story ids that also run through the real chat, one per family: bulk_rna, single_cell, dna_variants, chromatin, methylation, not_yet |
| `build_e2e.py` | `variants()` and `main()`: turns each `e2e.json` story into a multi-turn `pipeline_launch` variant (id `pipeline_scenarios.<story id>`, tag `pipeline_scenarios`, `requires_env` `NESSIE_PIPELINE_SCENARIOS_E2E`) in `NessieAI/tests/nessie_tests/corpus.json` (`python3 -m NessieAI.tests.pipeline_scenarios.build_e2e`). It rewrites only those variants, in the file's own layout. The build turn asks for the build and says not to submit; the turns stop at the confirm-to-submit question. The build turn asserts only the pipeline built; settings (`launch.must_match`) are checked by the in-process run only, because the chat snapshot carries no launch plan |
| `test_e2e_in_sync.py` | the free test that the corpus holds exactly the generated variants (never hand-edit them) and that every `e2e.json` story is ruled |

## The starting state

The first paid run has not been made, so `recorded/` does not exist and `test_replay.py` skips its
per-story replay with "no recorded stories yet". That is the expected starting state, not an error: the
replay machinery is still tested by its synthetic recording of a stubbed run. The skip ends when a paid run
is accepted and recorded (below). Ruled stories are the ones that run; `stories/*.json` shows each story's
`ruling`. The rulings were made through a review page and written back with `apply_rulings.py`; the commit
history records that the current rulings came from an independent reviewer agent, on the owner's instruction.

## Running the paid eval

Paid: it calls the real router and the real pipeline agent on each ruled story. Every submit path is
removed, so nothing launches. Each run needs the owner's go in chat.

```bash
# Dry run: prints the estimate and the stories it would run, then stops.
docker exec -w /app nextseek uv run --no-sync python -m NessieAI.tests.pipeline_scenarios.run
# The paid run.
docker exec -w /app nextseek uv run --no-sync python -m NessieAI.tests.pipeline_scenarios.run --yes
```

- `--ids a,b` runs those stories; `--family <name>` runs one family. Only ruled stories run, and only those
  whose cohort has UIDs on this stack.
- `--per-story-usd` is the per-story cost used for the printed estimate. Approve each run on that estimate.
- Each story's row records what it spent (`spend`: the router's calls, priced by the router itself, and the
  agent's, the loop's and the selection model's calls, priced by `turn_spend`, both on
  `NessieAI/chat_nextseek/model_prices.json`), and `summary.json` the run's total. `partial` means some call
  went unpriced or unseen, so the true cost is higher than `usd`.
- `--max-usd <n>` stops the run before the next story once that total reaches `n`; `summary.json` lists the
  stories it did not run in `not_run`. It counts only priced calls, and a story already started runs to its
  end, so a run can pass the cap by one story's cost.
- The run writes a run folder (`summary.json`, one file per story, the HTML report from `report.py`) to
  `--out`, which defaults to `outputs/<timestamp>_pipeline_scenarios`.
- `DEPLOYMENT.md` §1 says when a prod deploy needs an accepted run.
- The agent starts as production starts it on the nf-core report branch: with a parser plan naming the
  cohort's search (`run.parser_plan`), so the chat's earlier turns, a story's `history` among them, reach
  its first message.
- The first paid run is also the first measure of a tape's size, not yet known. A fetched nf-core schema is
  kept as a sha only when it is byte for byte a committed fixture; otherwise its bytes go on the tape as
  base64 (about 65 KB a schema). Check the size of `recorded/` before committing it.
- Unlike production, the agent sees the cohort's person fields (Scientist, Lab, PrepUser and the rest of
  `scrub.PERSON_NAMES`) as `REDACTED`. No story asks to group by them, so no grade depends on it.
- Run it on a stack built from a commit whose fingerprint inputs (`fingerprint.INPUTS`) equal the branch's:
  deploy the branch itself, not a `merge(local)` that also carries other branches' changes to those files.
  `record.py` refuses a run made under any other fingerprint, so a run on the wrong commit is paid for and
  cannot be recorded. `summary.json` and each story file keep a sha256 per input (`fingerprint_files`), so
  you can see which files differ.

## Re-recording

Run `record.py` on the host, from the worktree root, on the commit the paid run was made from:

```bash
python -m NessieAI.tests.pipeline_scenarios.record <run dir> [--ids a,b]
```

It writes `recorded/<id>.json` for each story and refuses (writing nothing) when:

- a fixture holds an email or an absolute path (URLs other than public archive and nf-core test data are
  rewritten to `<url>` first, never refused);
- the run was made under another fingerprint;
- the run had posterior routing on.

A story the runner failed on (`error`), or whose tape was refused (`unrecorded`), is skipped. Review the diff of `recorded/` before committing it.

CI demands a re-record whenever `fingerprint.current()` changes. `fingerprint.INPUTS` lists what it covers
(the router's BAML and code, the selection and agent code and prompts, the atlases, the param atlas, the
nf-core templates, the pinned schemas, the catalog, and the corpus families' projection); read that file
for the list. A change to any of these fails the replay until a paid re-record; the failure names the
files that changed, from the per-input hashes each fixture keeps (`fingerprint_files`). A change to a story's own
fields, or a tool call the tape never saw, fails it the same way.

## Adding a story

1. Draft it in `stories/<family>.json` with `ruling.state` `proposed`. `test_story_format.py` lists format
   problems; a story with a test cohort names `test::<cohort>`.
2. Put it on the review page for the owner's ruling: an Artifact whose database holds a "rulings"
   collection. (The page is private; it has no URL here.)
3. Write the rulings back with `apply_rulings.py` (usage above). Only `accepted` or `changed` stories run.
4. Make a paid run of just that story (`run --ids <id> --yes`), with the owner's go.
5. Record it with `record.py <run dir> --ids <id>`.

## Chat end-to-end

`e2e.json` names one ruled story per family, and `build_e2e.py` turns each into a `pipeline_launch` variant
in `NessieAI/tests/nessie_tests/corpus.json` (id `pipeline_scenarios.<story id>`). Paid, and each run needs
the owner's go.

The variants drive the real chat as far as a built launch, so they are fenced:

- Each one runs only with `NESSIE_PIPELINE_SCENARIOS_E2E` set (`requires_env`); without it the harness
  records it skipped, so a `--scope all` run never draws them by accident.
- Run them only on a stack whose Luria env is unset: no `LURIA*` variable (`LURIA_USER`, `LURIAKEY`, ...)
  in the `nextseek` container, so `docker exec nextseek env | grep -E '^LURIA'` prints nothing. Unsetting
  them means editing `docker/nextseek.env` and recreating the container, which needs the owner's go.
  Never on dev or prod. The build turn says
  "Build the run, but do not submit anything yet." and no turn confirms a submit, but a stack that cannot
  submit is the guard, not the wording.
- The cohorts they search exist only on the local stack (the `test::` cohorts and the local UIDs of the real
  ones), so on any other stack they would fail at the setup turn anyway.

The harness CLI has no `--ids`; run one story at a time:

```bash
docker exec -e NESSIE_PIPELINE_SCENARIOS_E2E=1 nextseek uv run manage.py nessie \
  --base-url http://localhost:8000 --tier full --scope all --variant pipeline_scenarios.<story id>
```

or pass `--cases <file>` for several. This checks the route, the pipeline chosen and, for `not_yet`, that
nothing was built. Launch settings (`must_match`) are checked by the in-process run only.

## Test cohorts

The `test::` cohorts in `test_cohorts.json` exist only on the local stack, in the project "Nessie test
cohorts". `scripts/pipeline_scenarios/make_test_cohorts.py` writes their upload workbooks, and
`scripts/pipeline_scenarios/README.md` says how to upload them. A fresh stack must upload them again and
re-record the UIDs in `test_cohorts.json`; a story whose cohort has no UIDs is skipped by the runner.
