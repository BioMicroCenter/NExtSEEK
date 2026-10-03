# scripts/laya: the laya router's data and evaluation tools (JevLevROUTING)

Code only. Nothing here holds question text; the training view and held-out reports are written outside every
git repo (the scripts refuse a path inside this one). No script here downloads weights or calls a service.

| Script | Does | Reads | Writes |
|---|---|---|---|
| `build_options.py` | option texts from `route_capabilities.json` and `router.baml` (SPEC s3), budget check that fails rather than cuts; `--check` is the drift test | the two source files | `NessieAI/router/laya_options.json` |
| `build_dataset.py` | the training view: teacher rules, case-level truth, prompt-seen slice, manifest removal, calib split (SPEC s8) | run `turns.json` files, `corpus.json`, held-out manifest, optional extra and evidence files | a jsonl outside the repo |
| `fit_calibration.py` | temperature by NLL, coverage curve, lowest threshold with agreement >= 98% | scored calibration-slice rows | `NessieAI/router/laya_calibration.json` |
| `evaluate.py` | `report.json` and HTML, Wilson intervals, slices, zero-shot next to fine-tune, the three bars | scored rows, the calibration file | a folder outside the repo |
| `train.sh` | laya CPU fine-tune pinned to `laya==0.3.25`, offline; written, not run | the training view | a checkpoint folder (written once) |
| `split.py` | held-out and calibration buckets, manifest loader (owner: the labelling unit) | | |

Shared pure helpers (`condense`, `options_hash`, `prompt_hash`, `apply_temperature`, `norm_text_hash`) live in
`NessieAI/router/laya_common.py`; they import neither torch nor laya.

Scored-row format (what a scoring run writes for `fit_calibration.py` and `evaluate.py`): `probabilities` (the
sidecar's unrounded output), `teacher_route`, `truth_route`, `either`, `slice`, `family`, `entity`,
`history`, `negation`, `followup_cc`, `latency_ms`, `baml_s`, `baml_routes`. See each script's docstring.

Tests: `NessieAI/tests/router/test_laya_*.py`, tiny made-up fixtures only.
