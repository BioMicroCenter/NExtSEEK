# NessieAI/tests/pipeline_scenarios/

One list of test stories checks routing and nf-core pipeline choice, as far as a built launch.

A story is a user's question about a cohort. It records the route the router must pick, the pipelines that
are acceptable, the pipelines that must never be offered, and whether a launch must be built. The owner rules on
every story. Only `accepted` or `changed` stories run.

| File | What it is |
|---|---|
| `stories/<family>.json` | the stories |
| `stories.py` | loader, cohort resolution, format problems |
| `test_cohorts.json` | local-only test cohorts (`test::<name>`); real cohorts come from `NessieAI/chat_nextseek/evals/groundtruth_cohorts.json` |
| `test_story_format.py` | the free format test |

The three layers (the paid run, the CI replay, the chat end-to-end check) are added in later commits of the same
branch, and this README gains a row for each.
