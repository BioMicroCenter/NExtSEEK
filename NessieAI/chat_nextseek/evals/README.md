# `NessieAI/chat_nextseek/evals/`

## What this is

Hand-run evaluations of nf-core pipeline selection: question cases, RNA selection, data fit,
payload ablation and protocol surveys. They are not part of any test lane and nothing imports them.

Their outputs are built from production metadata and protocol text. They stay out of git:
`.gitignore` excludes `prod_digests*.json`, `demo-output-*/` and `team_questions.json` here. Never commit them.

## Surface

| Group | Files |
|---|---|
| Selection evals | the pipeline-scenario stories (`NessieAI/tests/pipeline_scenarios/README.md`), `run_datafit_eval.py`, `run_payload_ablation.py`, `run_team_questions.py` |
| Cohort and digest builders | `build_prod_digests.py`, `fdh_digest.py`, `groundtruth_cohorts.json`, `measure_digest_cost.py`, `build_seqtype_counterfactual.py` |
| Surveys of production data | `survey_primary_data.py`, `survey_prod_protocols.py`, `survey_seqtype_labels.py`, `protocol_prose.py` |
| Demos and the report page | `demo_cohort_real.py`, `demo_dump_context.py`, `ask_pipeline.py`, `update_report_page.py`, `check_report_page.js` |

## Running

Each script's module docstring says what it measures and gives its command. They run in the app
container and call a model, so they cost money. For example:

```bash
docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/ask_pipeline.py --cohort 240910LAU::A.GEX -q "Run a splicing analysis on these mice"
```

Read the docstring first: several scripts need a production digest that you build with `build_prod_digests.py`.

## Depends on / depended on by

Uses the engine in `NessieAI/chat_nextseek/` (`NessieAI/chat_nextseek/README.md`). The tests that
guard the story files are `NessieAI/tests/pipeline_scenarios/test_story_format.py` and
`NessieAI/tests/chat_nextseek/test_nfcore_atlas.py`.
