# nextseek_api/studies/sources/

The studies tool's three sources. Each reads its input into one set of (study, sample) associations that the planner
takes; the package README ([`../README.md`](../README.md)) says how a run goes.

| File | What |
|---|---|
| `sheet.py` | the curator sheet: one row per (study, sample) |
| `dev_export.py` | `--mode export` on the dev box: the dev graph's paper studies, written to a file a plan then reads anywhere |
| `graph_only.py` | the graph-only paper studies (a Study node with no `seek_study_id` and a DOI or PMID), read from the live graph |
| `matching.py` | the one place the three sources meet: a UID becomes a sample id, an investigation title becomes one SEEK investigation |
| `dev_investigations.json` | the dev graph's investigation names paired with SEEK's, for the dev export |
