# Step 7 (compose-native deploy) acceptance evidence

This directory is the committed home for **generated** Step 7 evidence
bundles, one per run, under `<run_id>/` (`preflight.json`, `meta.json`,
`forced_cc_result.json`, `proxy_log_window.txt`, `network_inspect.json`,
`plugin_ops_matrix.json`, ...). Bundles are produced by an actual run
against the real stack — see `step7_preflight_collector.py` and
`PLAN-7-compose-native-prod-deploy.md` (Tasks 1, 9, 10, 15) — never
hand-written.

## Markdown is never proof

No `.md` file in a bundle counts as evidence on its own. The validator
enforces this directly: `check_not_markdown_only_bundle` fails any bundle
whose files are ONLY Markdown. This README documents the directory; it is
not itself a piece of evidence for any run.

## How a bundle is verified

Every bundle under `<run_id>/` is checked by the zero-spend, reproducible
validator:

```
python -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir> [repo_root]
```

See `validate_step7_compose_deploy.py`'s module docstring and its `CHECKS`
list for the full evidence contract (SPEC-7 section 8).
