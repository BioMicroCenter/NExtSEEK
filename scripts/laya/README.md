# scripts/laya: the laya router's data and evaluation tools (JevLevROUTING)

Code only. Nothing here holds question text; the training view, the held-out files and the reports are written
outside every git repo (each script refuses a path inside any git repo). No script here downloads weights or calls
a service.

| Script | Does | Reads | Writes |
|---|---|---|---|
| `build_options.py` | option texts from `route_capabilities.json` and `router.baml` (SPEC s3), budget check that fails rather than cuts; `--check` is the drift test | the two source files | `NessieAI/router/laya_options.json` |
| `build_dataset.py` | the training view: teacher rules, case-level truth, prompt-seen slice, manifest and held-out removal, calib split (SPEC s8) | run `turns.json` files, `corpus.json`, held-out manifest, optional extra and evidence files | a jsonl outside the repo |
| `fit_calibration.py` | temperature by NLL, coverage curve, lowest threshold with agreement >= 98% | scored calibration-slice rows | the calibration file `laya_calibration.json` beside `NessieAI/router/laya.py` (first written in phase C) |
| `evaluate.py` | `report.json` and HTML, Wilson intervals, slices, zero-shot next to fine-tune, the three bars | scored rows, the calibration file | a folder outside the repo |
| `train.sh` | laya CPU fine-tune pinned to `laya==0.3.25` and the sidecar lock's torch, offline; written, not run | the training view | a checkpoint folder (written once) |
| `split.py` | held-out and calibration buckets, manifest loader | | |
| `draft_heldout.py` | the held-out draft: held families and entities by chat, prompt-seen rows out, the top-up counts | a pool, or `build_dataset.py --manifest /dev/null`'s view | `draft.jsonl`, `topup.json` outside the repo |
| `label_page/` | the operator's blind labelling page and the ingest of its export ([its README](label_page/README.md)) | the draft | a labelled draft outside the repo |
| `freeze_heldout.py` | freezes the labelled draft, operator's go only: 0444, never overwritten, a change is a new version | the labelled draft | `heldout-v1.jsonl` and its text-free manifest outside the repo |

Shared pure helpers (`condense`, `options_hash`, `prompt_hash`, `apply_temperature`, `norm_text_hash`) live in
`NessieAI/router/laya_common.py`; they import neither torch nor laya.

Scored-row format (what a scoring run writes for `fit_calibration.py` and `evaluate.py`): `probabilities` (the
sidecar's unrounded output), `teacher_route`, `truth_route`, `either`, `slice`, `family`, `entity`,
`history`, `negation`, `followup_cc`, `latency_ms`, `baml_s`, `baml_routes`. See each script's docstring.

The sidecar's wire contract is pinned by `NessieAI/tests/router/test_laya_integration.py` (the real client
against the real wrapper on loopback).

## Running the sidecar

`laya-router` sits behind the compose profile `laya`, so a plain `up -d` and `./startup.sh rebuild` never start,
rebuild or restart it. Its checkpoint folder is `docker/laya/models/<revision>/` (gitignored) and its only secret
is `LAYA_API_KEY` in `docker/laya.env`.

- Start: `LAYA_REVISION=<revision> docker compose --profile laya up -d laya-router`
- Rebuild after a `docker/laya/` change: `docker compose --profile laya build laya-router`, then the start line
- Stop: `docker compose --profile laya stop laya-router`
- Logs: `docker compose logs laya-router`

`LAYA_REVISION` is a compose variable, read from the shell or the project's `.env`, never from `docker/laya.env`.
Pass it on every `--profile laya up`, or put the line in the project's `.env`: without it compose recreates the
sidecar on `unset`, which the wrapper refuses, so it crash-loops under `restart: unless-stopped`.

Tests: `NessieAI/tests/router/test_laya_*.py`, tiny made-up fixtures only.
