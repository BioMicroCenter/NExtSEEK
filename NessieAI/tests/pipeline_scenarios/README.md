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
| `apply_rulings.py` | writes the owner's rulings from the review page into the story files (`python -m NessieAI.tests.pipeline_scenarios.apply_rulings <rulings.json> <yyyy-mm-dd>`); `test_apply_rulings.py` tests it |
| `run.py` | the paid in-process runner: the real router and pipeline agent on each ruled story, no submit tool, every outside answer recorded on a tape and scrubbed for a public repo. Without `--yes` it only prints what it would run and the estimate; a paid run needs the owner's go (usage in its docstring) |
| `report.py` | `write_report`: the HTML page of a run, totals and then every story by family |
| `test_run.py` | the free test of the runner and report, with a stubbed router and agent (no model, no stack) |
| `fingerprint.py` | `current()`: sha256 over everything that decides routing and pipeline choice (`INPUTS`); a tape is valid only under the fingerprint it was recorded with. `test_fingerprint.py` tests it |
| `record.py` | turns an accepted paid run into `recorded/<id>.json` (`python -m NessieAI.tests.pipeline_scenarios.record <run dir> [--ids a,b]`): refuses a run made under another fingerprint, skips stories the runner failed on, refuses any fixture holding an email, absolute path or private URL |
| `test_replay.py` | the free CI replay: each `recorded/*.json` is replayed against the current code and must end with the recorded grade and pipeline; a changed fingerprint, story or call fails it until a paid run re-records. Also replays a synthetic recording of a stubbed run, so the machinery is tested before any recording exists. With no recordings the replay test skips |

The three layers (the paid run, the CI replay, the chat end-to-end check) are added in later commits of the same
branch, and this README gains a row for each.
