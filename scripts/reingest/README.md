# `scripts/reingest/`

## What this is

Admin-only tools for testing nf-core reingest without a live harvest, and for making
the run fixtures the reingest tests read. Nothing here runs in the stack or in CI:
every script is started by hand, by the owner of the run.

A manifest built from a local copy has no launch record, so it is test-only: only a
superuser can build workbooks from it, and `upload-reingest` refuses them
(`NessieAI/ns/CLAUDE.md`).

| File | Does |
|---|---|
| `fetch_run.sh` | copies only what the harvest reads from one results folder on the cluster into a local folder; the files the harvest only lists become empty placeholders. Its patterns come from the reingest code at `REINGEST_REF` (default `HEAD`) |
| `folder_to_manifest.sh` | turns a local results folder into a test-only manifest in the running stack, through the same stage script and harvest code a real run uses, and prints its `manifest_id` |
| `make_fixture.sh` | builds a committed fixture under `NessieAI/tests/ns/fixtures/reingest_runs/` from a `-profile test` run launched through Nessie, by way of `fetch_run.sh` and `sanitize_fixture.py` |
| `import_evidence.py` | builds a fixture folder from nf-core's own published evidence (a release's nf-test snapshot, or its public AWS test results) instead of a cluster run; reads public URLs only and touches no cluster, so an agent may run it. Run `sanitize_fixture.py` on its output. Tested by `NessieAI/tests/ns/reingest/test_import_evidence.py` |
| `sanitize_fixture.py` | rewrites cluster prefixes to `/RUN` and refuses a fixture that still holds an absolute path, an email address, a URL or a file that is not UTF-8 text. It refuses rather than edits, so a refusal writes nothing |

## Rules

- `fetch_run.sh` and `make_fixture.sh` reach the cluster over ssh. Only the owner of the
  run starts them, never an agent or an automated job. Agents may syntax-check them
  (`bash -n`) and nothing more.
- A fixture goes into a public repository. Follow the review steps in
  [`NessieAI/tests/ns/fixtures/reingest_runs/README.md`](../../NessieAI/tests/ns/fixtures/reingest_runs/README.md)
  before committing one.
- `sanitize_fixture.py` and `import_evidence.py` are tested by
  `NessieAI/tests/ns/reingest/test_sanitize_fixture.py` and `test_import_evidence.py`;
  the shell scripts have no tests.
