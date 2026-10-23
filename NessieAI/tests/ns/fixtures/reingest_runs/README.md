# Reingest run fixtures

One folder per pipeline and release: `<pipeline>/<version>[-<variant>]/`. Each holds
what the stager would copy (pipeline_info/, the samplesheet, MultiQC tables),
`inventory.json` (run-relative paths and sizes, no contents), `launch_record.json`
and `snapshot.json` (the expected row shape). `test_fixture_runs.py` checks every
folder.

There are two ways to make one, and either is evidence for a rule (G-7 in
`NessieAI/ns/reingest_maps/README.md`, which says which source is preferred):

- **From nf-core's own published evidence**, with `scripts/reingest/import_evidence.py`:
  `snapshot` reads the release tag's nf-test snapshot (`tests/<name>.nf.test.snap`) and
  `conf/test.config`; `aws` lists nf-core's public AWS results for the release and
  downloads their MultiQC tables, params and software versions. It reads public URLs only
  and touches no cluster. The folder is `<version>` for a snapshot and
  `<version>-aws-<profile>` for AWS results. A snapshot gives no file sizes and no MultiQC
  values, so QC attributes are mapped from AWS results or a Nessie run, never a snapshot.
- **From a `-profile test` run launched through Nessie**, with
  `scripts/reingest/make_fixture.sh`. Only the run's owner starts it: it reaches the
  cluster. `launch_record.json` is a stub (empty cohort) to complete by hand from the
  PipelineRun.

Both build the fixture in a temporary folder and run `scripts/reingest/sanitize_fixture.py`
over it, which rewrites the run directory (and any extra prefixes) to `/RUN` and scans
every file. If any absolute path, email address or URL remains, or any file cannot be
read as UTF-8 text, it refuses and nothing is written into the repo.

`EVIDENCE.txt`, written by `import_evidence.py`, names where an imported fixture came
from (repository and tag, or bucket and prefix, without a URL scheme, which the sanitizer
refuses), the day it was read, and what the source lacks. A folder without it came from a
Nessie run. It is fixture bookkeeping: no map has to claim it.

The sanitizer refuses rather than edits, so before committing a fixture:

1. Blank params such as `email`, `email_on_fail`, `hook_url` and `multiqc_title`
   if present, in the params JSON and anywhere else they are echoed.
2. Review every file in the folder by hand. The scan catches paths, addresses and
   URLs, not lab sample names, user names or hostnames in free text.

Regenerate snapshots after an intended change:
`REINGEST_UPDATE_SNAPSHOTS=1` with the fixture test, then review the diff.

A folder with `SYNTHETIC.txt` was written by hand; replace it with a real one.
