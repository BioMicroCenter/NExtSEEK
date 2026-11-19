# scripts/pipeline_scenarios/cohorts

One definition per local-only test cohort, named as in
`NessieAI/tests/pipeline_scenarios/test_cohorts.json`. Each file is
`{"name", "description", "levels": [{"sample_type", "rows": [<json_metadata>...]}]}`, with the
source level first and last the sample type the pipeline reads (`D.SEQ`, or `A.SCXP`, `A.ASM` or
`D.PSEQ`: any `accepted_leaf_sample_types` of a catalog pipeline). A row's `Parent` is the `Name` of a row in an earlier level.
`make_test_cohorts.py` (one folder up) renders each level as an upload workbook; the rules for
the metadata are in [the parent README](../README.md). The definition test is
`test_every_test_cohort_has_a_definition_with_a_parent_chain` in
`NessieAI/tests/pipeline_scenarios/test_story_format.py`.
