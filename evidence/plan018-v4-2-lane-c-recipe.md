# Plan 018 V4-2 Lane C recipe (OPS §3.4a mirror)

Authoritative for product-seam tests; canonical copy also in `/home/taishajo/work/OPS-TESTING-HARNESSES.md` §3.4a.

```bash
docker run --rm \
  -v /home/taishajo/work/NExtSEEK-plan018:/repo -w /repo \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -e PYTHONPATH=/repo:/repo/dmac_assistant/src \
  -e GCP_API_KEY=lane-test \
  nextseek-nextseek:latest \
  uv run --project /app --no-sync python -m pytest \
    nextseek_api/cc_assistant/tests/test_route_override.py \
    nextseek_api/cc_assistant/tests/test_ns_run_root_event.py \
    nextseek_api/cc_assistant/tests/test_decide_route_sticky_cc.py \
    nextseek_api/cc_assistant/tests/test_decide_route_pipeline_gate.py \
    nextseek_api/cc_assistant/tests/test_v4_2_force_route_http.py \
    nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py -q
```

Settings shim: `dmac/test_settings.py` (SQLite + assistant constants + minimal SEEK/NEO4J stubs).
