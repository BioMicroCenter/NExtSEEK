"""OpenAPI v2 schema drift test — snapshot-pinned contract.

Regenerates schema via call_command (same process, inherits PYTHONHASHSEED from
test runner) under pinned @override_settings to neutralize local_settings.py
and other env-dependent factors. Diffs normalized YAML against committed
snapshot.
"""
import io
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.test import override_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "nextseek_api" / "openapi.v2.snapshot.yaml"


# Explicit allowlist — every path the v2 contract covers. Any drift (add/remove)
# that bypasses this set indicates scope change.
EXPECTED_V2_PATHS = {
    # ViewSets declare lookup_field='uid' → drf-spectacular emits {uid} params.
    # batch-upload actions use @action(detail=False, url_path="verb/(?P<job_id>...)")
    # → generated path is /batch-upload/verb/{job_id}/, not the other order.
    # data_files.download is detail=False url_path="download" (POST body carries ids).
    "/nextseek_api/samples/advanced_search/",           # task-02
    "/nextseek_api/samples/{uid}/",                     # task-03 retrieve/update/destroy
    "/nextseek_api/data_files/",                        # task-03 list/create
    "/nextseek_api/data_files/{uid}/",                  # task-03 retrieve/update
    "/nextseek_api/data_files/download/",               # task-03 download (detail=False, body carries ids)
    "/nextseek_api/assays/",                            # task-03 list/create
    "/nextseek_api/assays/{uid}/",                      # task-03
    "/nextseek_api/batch-upload/start/",                # task-03
    "/nextseek_api/batch-upload/status/{job_id}/",      # task-03 ownership
    "/nextseek_api/batch-upload/cancel/{job_id}/",      # task-03 ownership
    "/nextseek_api/batch-upload/summary/{job_id}/",     # task-03 ownership
    "/nextseek_api/batch-upload/",                      # task-03 list
    "/nextseek_api/evaluator/runs/",                    # task-02 verification
}


@pytest.fixture(autouse=True)
def preflight():
    """TDD-24: verify task-02/03 deliverables are present before generating."""
    import nextseek_api.helpers as h
    import nextseek_api.exception_handler as eh
    import nextseek_api.errors as err
    if not hasattr(h, "build_v2_list_envelope"):
        pytest.skip("task-02 not merged — build_v2_list_envelope absent")
    if not hasattr(eh, "handle_api_exception"):
        pytest.skip("task-01/03 not merged — handle_api_exception absent")
    if not hasattr(err, "translate_error_response_v2"):
        pytest.skip("task-03 not merged — translate_error_response_v2 absent")


def _generate_schema(api_version: str = "v2") -> dict:
    """Regenerate schema via call_command — same process as test runner."""
    buf = io.StringIO()
    # Pin settings that affect schema generation. USE_I18N is pinned to match
    # base settings (False) — see vet B-01.
    # Pin settings to match production values. Do NOT introduce new keys or
    # modify existing ones here — the goal is to neutralize local_settings.py
    # leaks, not to change SPECTACULAR_SETTINGS.
    pinned = dict(
        SPECTACULAR_SETTINGS={
            "TITLE": "NExtSEEK API",
            "VERSION": "0.1.0",
            "OAS_VERSION": "3.1.0",
            "PREPROCESSING_HOOKS": [
                "dmac.openapi_hooks.exclude_seek_paths",
                "dmac.openapi_hooks.swap_versioning_for_schema_gen",
            ],
            "POSTPROCESSING_HOOKS": [
                "dmac.openapi_hooks.restore_versioning_post_schema_gen",
            ],
        },
        LANGUAGE_CODE="en",
        TIME_ZONE="UTC",
        USE_I18N=False,
    )
    with override_settings(**pinned):
        call_command("spectacular", api_version=api_version, stdout=buf)
    return yaml.safe_load(buf.getvalue())


def _normalize_yaml(data: dict) -> str:
    """Canonical serialization: sort keys, sort parameters by name."""
    def _sort_parameters(obj):
        if isinstance(obj, dict):
            if "parameters" in obj and isinstance(obj["parameters"], list):
                obj["parameters"] = sorted(
                    obj["parameters"],
                    key=lambda p: (p.get("name", ""), p.get("in", "")),
                )
            return {k: _sort_parameters(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sort_parameters(x) for x in obj]
        return obj

    normalized = _sort_parameters(data)
    return yaml.safe_dump(normalized, sort_keys=True, default_flow_style=False)


@pytest.mark.django_db
class TestSchemaDrift:
    def test_snapshot_exists(self):
        assert SNAPSHOT_PATH.exists(), f"Snapshot missing: {SNAPSHOT_PATH}"

    def test_snapshot_is_valid_openapi_3_1(self):
        snapshot = yaml.safe_load(SNAPSHOT_PATH.read_text())
        assert snapshot.get("openapi", "").startswith("3.")
        assert "paths" in snapshot

    def test_snapshot_does_not_include_api_app_paths(self):
        snapshot = yaml.safe_load(SNAPSHOT_PATH.read_text())
        api_paths = [p for p in snapshot["paths"] if p.startswith("/api/")]
        assert api_paths == [], f"Legacy api_app paths leaked: {api_paths}"

    def test_snapshot_does_not_include_seek_paths(self):
        snapshot = yaml.safe_load(SNAPSHOT_PATH.read_text())
        seek_paths = [p for p in snapshot["paths"] if p.startswith("/seek/")]
        assert seek_paths == []

    def test_snapshot_includes_every_expected_v2_path(self):
        snapshot = yaml.safe_load(SNAPSHOT_PATH.read_text())
        actual = set(snapshot["paths"].keys())
        missing = EXPECTED_V2_PATHS - actual
        assert missing == set(), f"Expected v2 paths absent from snapshot: {missing}"

    def test_snapshot_matches_live_schema_generation(self):
        snapshot = yaml.safe_load(SNAPSHOT_PATH.read_text())
        live = _generate_schema()
        snap_norm = _normalize_yaml(snapshot)
        live_norm = _normalize_yaml(live)
        assert snap_norm == live_norm, "Snapshot drift — regenerate via Step 5 command"

    def test_every_touched_endpoint_has_at_least_one_example(self):
        snapshot = yaml.safe_load(SNAPSHOT_PATH.read_text())

        def _has_response_examples(op):
            # drf-spectacular nests examples under responses.{status}.content.{media}.examples
            for resp in (op.get("responses") or {}).values():
                for media in (resp.get("content") or {}).values():
                    if "examples" in media:
                        return True
            return False

        def _has_request_examples(op):
            for media in ((op.get("requestBody") or {}).get("content") or {}).values():
                if "examples" in media:
                    return True
            return False

        for path in EXPECTED_V2_PATHS:
            path_data = snapshot["paths"].get(path, {})
            assert path_data, f"Path {path} has no operations"
            for method, op in path_data.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                assert _has_response_examples(op) or _has_request_examples(op), (
                    f"{method.upper()} {path} has no OpenApiExample on request or response"
                )
