# Wave 03 — Task 9 (upload endpoint + Celery)

**Merge commit:** `ebfc737` on cc-step3-ui-io

**Task branch:** task/09-upload → ebfc737

## Reproduction

```bash
cd /home/taishajo/work/NExtSEEK
git checkout cc-step3-ui-io  # at or after ebfc737
PYTHONPATH="$PWD:$PWD/dmac_assistant/src" uv run --no-project --with pytest --with pytest-cov \
  python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_upload_validate.py \
  --cov=nextseek_api.cc_assistant.cc_upload_validate --cov-fail-under=95
```

**Expected:** 9 passed, cc_upload_validate 100%, exit 0.

**Reviewer verdict:** PASS — validator ≥95%, Celery task registered, upload + upload_status actions wired, shutil.move for cross-device staging.
