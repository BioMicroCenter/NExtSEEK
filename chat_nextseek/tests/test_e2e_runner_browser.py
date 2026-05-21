"""run_main Phase 2 dispatch tests."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _catalog_with_one_tagged(tmp_path):
    cat = {
        "version": "1.0",
        "families": {
            "search_advanced": {
                "description": "test",
                "variants": [
                    {"family": "search_advanced", "id": "a.untagged", "name": "u", "tags": [],
                     "turns": [{"label": "main", "query": "x", "pass_criteria": []}]},
                    {"family": "search_advanced", "id": "a.tagged", "name": "t", "tags": ["playwright"],
                     "turns": [{"label": "main", "query": "y", "pass_criteria": []}]},
                ],
            },
        },
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(cat))
    return p


def test_run_main_dispatches_tagged_to_browser(tmp_path):
    from e2e import runner

    cat_path = _catalog_with_one_tagged(tmp_path)
    out_root = tmp_path / "outputs"

    def _stub_cli(variant, config, out_dir, **kw):
        return {"id": variant.id, "family": variant.family, "status": "passed",
                "elapsed_s": 1.0, "failed_criteria": [], "turn_results": []}

    def _stub_browser(variant, config, out_dir, **kw):
        return {"id": variant.id, "family": variant.family, "status": "passed",
                "elapsed_s": 2.0, "failed_criteria": [], "turn_results": []}

    with patch("e2e.runner.run_variant", side_effect=_stub_cli) as cli, \
         patch("e2e.runner._probe_ui_reachable", return_value=True), \
         patch("e2e.playwright.runner.run_variant_browser", side_effect=_stub_browser) as br, \
         patch("chat_nextseek.config.ChatConfig"):
        rc = runner.run_main(cat_path, ratio=1.0, seed=1, out_root=out_root,
                             pace_seconds=0)

    # CLI runs both variants; browser runs only the tagged one
    cli_ids = [c.args[0].id for c in cli.call_args_list]
    br_ids = [c.args[0].id for c in br.call_args_list]
    assert sorted(cli_ids) == ["a.tagged", "a.untagged"]
    assert br_ids == ["a.tagged"]
    assert rc == 0


def test_run_main_skips_browser_when_probe_fails(tmp_path):
    from e2e import runner

    cat_path = _catalog_with_one_tagged(tmp_path)
    out_root = tmp_path / "outputs"

    def _stub_cli(variant, config, out_dir, **kw):
        return {"id": variant.id, "family": variant.family, "status": "passed",
                "elapsed_s": 1.0, "failed_criteria": [], "turn_results": []}

    with patch("e2e.runner.run_variant", side_effect=_stub_cli), \
         patch("e2e.runner._probe_ui_reachable", return_value=False), \
         patch("e2e.playwright.runner.run_variant_browser") as br, \
         patch("chat_nextseek.config.ChatConfig"):
        rc = runner.run_main(cat_path, ratio=1.0, seed=1, out_root=out_root, pace_seconds=0)

    # Browser runner not called when probe fails
    br.assert_not_called()
    # Manifest should record a 'skipped' entry for the tagged variant
    runs = sorted(out_root.glob("e2e_*"))
    manifest = json.loads((runs[-1] / "manifest.json").read_text())
    statuses = {e["id"]: e["status"] for e in manifest["variants"]}
    assert statuses["a.tagged.browser"] == "skipped"
    assert rc == 0  # Skip != fail


def test_run_main_skip_playwright_flag(tmp_path):
    from e2e import runner

    cat_path = _catalog_with_one_tagged(tmp_path)
    out_root = tmp_path / "outputs"

    def _stub_cli(variant, config, out_dir, **kw):
        return {"id": variant.id, "family": variant.family, "status": "passed",
                "elapsed_s": 1.0, "failed_criteria": [], "turn_results": []}

    with patch("e2e.runner.run_variant", side_effect=_stub_cli), \
         patch("e2e.runner._probe_ui_reachable") as probe, \
         patch("e2e.playwright.runner.run_variant_browser") as br, \
         patch("chat_nextseek.config.ChatConfig"):
        rc = runner.run_main(cat_path, ratio=1.0, seed=1, out_root=out_root,
                             pace_seconds=0, skip_playwright=True)

    probe.assert_not_called()
    br.assert_not_called()
    assert rc == 0


def test_run_main_skip_cli_flag_runs_only_browser(tmp_path):
    from e2e import runner

    cat_path = _catalog_with_one_tagged(tmp_path)
    out_root = tmp_path / "outputs"

    def _stub_browser(variant, config, out_dir, **kw):
        return {"id": variant.id, "family": variant.family, "status": "passed",
                "elapsed_s": 2.0, "failed_criteria": [], "turn_results": []}

    with patch("e2e.runner.run_variant") as cli, \
         patch("e2e.runner._probe_ui_reachable", return_value=True), \
         patch("e2e.playwright.runner.run_variant_browser", side_effect=_stub_browser) as br, \
         patch("chat_nextseek.config.ChatConfig"):
        rc = runner.run_main(cat_path, ratio=1.0, seed=1, out_root=out_root,
                             pace_seconds=0, skip_cli=True)

    cli.assert_not_called()
    br_ids = [c.args[0].id for c in br.call_args_list]
    assert br_ids == ["a.tagged"]
    assert rc == 0
