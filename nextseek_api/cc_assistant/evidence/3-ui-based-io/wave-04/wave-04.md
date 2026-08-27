# Wave 04 — Task 9b (upload list)

**Merge commit:** `5df6ed1` on cc-step3-ui-io

**Task branch:** task/09b-upload-list → 5df6ed1

## Reproduction

```bash
cd /home/taishajo/work/NExtSEEK
git checkout cc-step3-ui-io  # at or after 5df6ed1
PYTHONPATH="$PWD:$PWD/dmac_assistant/src" uv run --no-project --with pytest --with pytest-cov \
  python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_cc_upload_list.py \
  --cov=nextseek_api.cc_assistant.cc_upload_list --cov-fail-under=95
rg 'url_path="upload/list"' nextseek_api/services/cc_assistant.py
```

**Expected:** 2 passed, cc_upload_list 100%, grep hit, exit 0.

**Reviewer verdict:** PASS — list_input_files helper + upload/list action owner-scoped.
