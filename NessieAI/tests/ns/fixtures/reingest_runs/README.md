# Reingest run fixtures

One folder per pipeline and release: `<pipeline>/<version>[-<variant>]/`. Each holds
what the stager would copy (pipeline_info/, the samplesheet, MultiQC tables),
`inventory.json` (run-relative paths and sizes, no contents), `launch_record.json`
and `snapshot.json` (the expected row shape). `test_fixture_runs.py` checks every
folder.

Make one with `scripts/reingest/make_fixture.sh` from a `-profile test` run
launched through Nessie. Every cluster path is rewritten to `/RUN/...`, and the
script refuses to write a fixture that still holds another absolute path.

Regenerate snapshots after an intended change:
`REINGEST_UPDATE_SNAPSHOTS=1` with the fixture test, then review the diff.

A folder with `SYNTHETIC.txt` was written by hand; replace it with a real one.
