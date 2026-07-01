# Wave 05 — Task 10 (artifact-download + transcript-recover)

**Merge commit:** `e83abc3` on cc-step3-ui-io

**Task branch:** task/10-artifact-endpoints → e83abc3

## Reproduction

```bash
cd /home/taishajo/work/NExtSEEK
git checkout cc-step3-ui-io  # at or after e83abc3
PYTHONPATH="$PWD:$PWD/dmac_assistant/src" uv run --no-project --with pytest --with pytest-cov \
  --with orjson --with pydantic --with zstandard --with 'baml-py==0.222.0' --with 'Django>=5.2,<6' \
  python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/ \
  --ignore=nextseek_api/cc_assistant/tests/test_cc_realstack.py
```

**Expected:** 214 passed, exit 0.

**Reviewer verdict:** PASS — cc_endpoint_guards resolve_artifact_path traversal guard, download_artifact + recover_transcript actions wired.
