# Reingest run fixtures

One folder per pipeline and release: `<pipeline>/<version>[-<variant>]/`. Each holds
what the stager would copy (pipeline_info/, the samplesheet, MultiQC tables),
`inventory.json` (run-relative paths and sizes, no contents), `launch_record.json`
and `snapshot.json` (the expected row shape). `test_fixture_runs.py` checks every
folder.

Make one with `scripts/reingest/make_fixture.sh` from a `-profile test` run
launched through Nessie. It builds the fixture in a temporary folder, rewrites
the run directory and any extra prefixes you give it to `/RUN`, and scans every
file. If any absolute path remains, or any file cannot be read as UTF-8 text, it
refuses and writes nothing into the repo. `launch_record.json` is a stub (empty
cohort) to complete by hand from the PipelineRun.

Regenerate snapshots after an intended change:
`REINGEST_UPDATE_SNAPSHOTS=1` with the fixture test, then review the diff.

A folder with `SYNTHETIC.txt` was written by hand; replace it with a real one.
