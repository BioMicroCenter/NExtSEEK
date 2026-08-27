"""Hermetic tests for scripts/validate_viewset_conventions.py (no Django)."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate_viewset_conventions.py"
SKILL = REPO_ROOT / ".claude" / "skills" / "nextseek-viewset" / "SKILL.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_viewset_conventions", VALIDATOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vc = _load_validator()


GOOD_DESC = (
    "**SUMMARY:** List studies.\n\n"
    "**USE WHEN:** Browse studies.\n\n"
    "**ACCEPTS:** Pagination params.\n\n"
    "**RETURNS:** Paginated studies.\n\n"
    "**TRIGGER PHRASES:** list studies\n\n"
    "**EXAMPLES:**\n"
    "- 'List all studies'\n"
)


def _write_minimal_desc_files(root: Path) -> None:
    """Create the three scanned description modules with one valid constant each."""
    endpoint = root / "nextseek_api" / "endpoint_descriptions.py"
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    endpoint.write_text(f'GOOD_DESC = """{GOOD_DESC}"""\n', encoding="utf-8")
    evaluator = root / "nextseek_api" / "assistant" / "descriptions_evaluator.py"
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(f'EVAL_DESC = """{GOOD_DESC}"""\n', encoding="utf-8")
    cc = root / "nextseek_api" / "assistant" / "descriptions_cc.py"
    cc.write_text(f'CC_DESC = """{GOOD_DESC}"""\n', encoding="utf-8")


class TestScanContract:
    def test_assistant_descriptions_not_scanned(self):
        assert "nextseek_api/assistant/descriptions.py" not in vc.DESCRIPTION_FILES
        assert "nextseek_api/assistant/descriptions_cc.py" in vc.DESCRIPTION_FILES

    def test_description_schema_allowlist_removed(self):
        assert not hasattr(vc, "DESCRIPTION_SCHEMA_ALLOWLIST")

    def test_attribute_viewset_is_scanned(self):
        assert "nextseek_api/attributes/views.py" in vc.EXTEND_SCHEMA_SCAN_PATHS


class TestGrandfatherLockstep:
    def test_allowlists_derived_from_grandfather_ops(self):
        expected_ast = frozenset(
            (e.rel_path, e.func_name)
            for e in vc.GRANDFATHER_OPS
            if e.ast_missing_examples and e.rel_path and e.func_name
        )
        expected_inline = frozenset(
            (e.rel_path, e.func_name)
            for e in vc.GRANDFATHER_OPS
            if e.inline_description and e.rel_path and e.func_name
        )
        expected_schema = frozenset(
            op_id for e in vc.GRANDFATHER_OPS for op_id in e.operation_ids
        )
        assert vc.EXTEND_SCHEMA_EXAMPLES_ALLOWLIST == expected_ast
        assert vc.INLINE_DESCRIPTION_ALLOWLIST == expected_inline
        assert vc.SCHEMA_EXAMPLES_OPERATION_ID_ALLOWLIST == expected_schema

    def test_inline_allowlist_is_views_nhp_timeline_only(self):
        assert all(k[0] == "nextseek_api/views.py" for k in vc.INLINE_DESCRIPTION_ALLOWLIST)
        assert vc.INLINE_DESCRIPTION_ALLOWLIST == frozenset(
            {
                ("nextseek_api/views.py", "download"),
                ("nextseek_api/views.py", "events"),
                ("nextseek_api/views.py", "info"),
                ("nextseek_api/views.py", "retrieve_samples"),
                ("nextseek_api/views.py", "timeline"),
            }
        )

    def test_cc_assistant_not_on_ast_or_inline_allowlists(self):
        cc_keys = {k for k in vc.EXTEND_SCHEMA_EXAMPLES_ALLOWLIST if k[0].endswith("cc_assistant.py")}
        inline_cc = {k for k in vc.INLINE_DESCRIPTION_ALLOWLIST if k[0].endswith("cc_assistant.py")}
        assert cc_keys == set()
        assert inline_cc == set()


class TestValidateDescText:
    def test_valid_minimal(self):
        assert vc.validate_desc_text(GOOD_DESC) == []

    def test_optional_do_not_use_when(self):
        text = (
            "**SUMMARY:** X.\n\n"
            "**USE WHEN:** Y.\n\n"
            "**DO NOT USE WHEN:** Z.\n\n"
            "**ACCEPTS:** None.\n\n"
            "**RETURNS:** List.\n\n"
            "**TRIGGER PHRASES:** a\n\n"
            "**EXAMPLES:**\n- 'example'\n"
        )
        assert vc.validate_desc_text(text) == []

    def test_extra_middle_heading_before_trigger_phrases(self):
        text = (
            "**SUMMARY:** X.\n\n"
            "**USE WHEN:** Y.\n\n"
            "**ACCEPTS:** Files.\n\n"
            "**RETURNS:** Job id.\n\n"
            "**FILE SIZE LIMIT:** 200 MB max.\n\n"
            "**TRIGGER PHRASES:** batch upload\n\n"
            "**EXAMPLES:**\n- 'Upload spreadsheet'\n"
        )
        assert vc.validate_desc_text(text) == []

    def test_missing_summary(self):
        bad = GOOD_DESC.replace("**SUMMARY:**", "**OVERVIEW:**", 1)
        violations = vc.validate_desc_text(bad)
        assert any("SUMMARY" in v.message for v in violations)

    def test_missing_trigger_phrases(self):
        text = (
            "**SUMMARY:** X.\n\n"
            "**USE WHEN:** Y.\n\n"
            "**ACCEPTS:** None.\n\n"
            "**RETURNS:** List.\n\n"
            "**EXAMPLES:**\n- 'x'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("TRIGGER PHRASES" in v.message for v in violations)

    def test_empty_examples_section(self):
        text = (
            "**SUMMARY:** X.\n\n"
            "**USE WHEN:** Y.\n\n"
            "**ACCEPTS:** None.\n\n"
            "**RETURNS:** List.\n\n"
            "**TRIGGER PHRASES:** a\n\n"
            "**EXAMPLES:**\n\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("EXAMPLES" in v.message for v in violations)

    def test_examples_need_bullet(self):
        text = (
            "**SUMMARY:** X.\n\n"
            "**USE WHEN:** Y.\n\n"
            "**ACCEPTS:** None.\n\n"
            "**RETURNS:** List.\n\n"
            "**TRIGGER PHRASES:** a\n\n"
            "**EXAMPLES:**\nSee docs.\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("bullet" in v.message for v in violations)

    def test_no_headings(self):
        violations = vc.validate_desc_text("plain prose without headings")
        assert any("no **HEADING:**" in v.message for v in violations)

    def test_wrong_heading_order(self):
        text = (
            "**USE WHEN:** y\n\n"
            "**SUMMARY:** x\n\n"
            "**ACCEPTS:** a\n\n"
            "**RETURNS:** r\n\n"
            "**TRIGGER PHRASES:** t\n\n"
            "**EXAMPLES:**\n- 'e'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("expected heading 'SUMMARY'" in v.message for v in violations)

    def test_missing_accepts(self):
        text = (
            "**SUMMARY:** x\n\n"
            "**USE WHEN:** y\n\n"
            "**RETURNS:** r\n\n"
            "**TRIGGER PHRASES:** t\n\n"
            "**EXAMPLES:**\n- 'e'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("ACCEPTS" in v.message for v in violations)

    def test_empty_do_not_use_when(self):
        text = (
            "**SUMMARY:** x\n\n"
            "**USE WHEN:** y\n\n"
            "**DO NOT USE WHEN:**\n\n"
            "**ACCEPTS:** a\n\n"
            "**RETURNS:** r\n\n"
            "**TRIGGER PHRASES:** t\n\n"
            "**EXAMPLES:**\n- 'e'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("DO NOT USE WHEN" in v.message for v in violations)

    def test_empty_summary_body(self):
        text = (
            "**SUMMARY:**\n\n"
            "**USE WHEN:** y\n\n"
            "**ACCEPTS:** a\n\n"
            "**RETURNS:** r\n\n"
            "**TRIGGER PHRASES:** t\n\n"
            "**EXAMPLES:**\n- 'e'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("SUMMARY" in v.message and "empty" in v.message for v in violations)

    def test_empty_returns_body(self):
        text = (
            "**SUMMARY:** x\n\n"
            "**USE WHEN:** y\n\n"
            "**ACCEPTS:** a\n\n"
            "**RETURNS:**\n\n"
            "**TRIGGER PHRASES:** t\n\n"
            "**EXAMPLES:**\n- 'e'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("RETURNS" in v.message and "empty" in v.message for v in violations)

    def test_examples_heading_missing(self):
        text = (
            "**SUMMARY:** x\n\n"
            "**USE WHEN:** y\n\n"
            "**ACCEPTS:** a\n\n"
            "**RETURNS:** r\n\n"
            "**TRIGGER PHRASES:** t\n\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("EXAMPLES" in v.message for v in violations)


class TestDescriptionConstants:
    def test_all_desc_constants_parse(self):
        for rel in vc.DESCRIPTION_FILES:
            path = REPO_ROOT / rel
            names = [name for name, _ in vc._iter_desc_constants(path)]
            assert names, f"no *_DESC constants found in {rel}"

    def test_missing_returns_heading(self):
        text = (
            "**SUMMARY:** x\n\n"
            "**USE WHEN:** y\n\n"
            "**ACCEPTS:** a\n\n"
            "**TRIGGER PHRASES:** t\n\n"
            "**EXAMPLES:**\n- 'e'\n"
        )
        violations = vc.validate_desc_text(text)
        assert any("expected heading 'RETURNS'" in v.message for v in violations)

    def test_iter_desc_constants_joined_str(self, tmp_path):
        mod = tmp_path / "descriptions.py"
        mod.write_text('FOO_DESC = f"part1part2"\n', encoding="utf-8")
        pairs = list(vc._iter_desc_constants(mod))
        assert pairs == [("FOO_DESC", "part1part2")]

    def test_iter_desc_constants_tuple_literal_eval_string(self, tmp_path):
        mod = tmp_path / "descriptions.py"
        mod.write_text('FOO_DESC = ("only")\n', encoding="utf-8")
        pairs = list(vc._iter_desc_constants(mod))
        assert pairs == [("FOO_DESC", "only")]

    def test_iter_desc_constants_tuple_join(self, tmp_path):
        mod = tmp_path / "descriptions.py"
        mod.write_text('FOO_DESC = ("part1" "part2")\n', encoding="utf-8")
        pairs = list(vc._iter_desc_constants(mod))
        assert pairs == [("FOO_DESC", "part1part2")]

    def test_iter_desc_constants_tuple_fallback_walk(self, tmp_path):
        mod = tmp_path / "descriptions.py"
        mod.write_text('FOO_DESC = ("a", "b")\n', encoding="utf-8")
        pairs = list(vc._iter_desc_constants(mod))
        assert pairs == [("FOO_DESC", "ab")]

    def test_iter_desc_constants_skips_non_desc_assign(self, tmp_path):
        mod = tmp_path / "descriptions.py"
        mod.write_text('OTHER = "not a desc"\n', encoding="utf-8")
        assert list(vc._iter_desc_constants(mod)) == []

    def test_validate_missing_description_file(self, tmp_path):
        fake = tmp_path / "repo"
        fake.mkdir()
        violations = vc.validate_description_files(fake)
        assert any("missing" in v.message for v in violations)


class TestExtendSchemaDecorators:
    def test_repo_integration_clean(self):
        violations = vc.validate_repo(REPO_ROOT)
        assert violations == [], vc._format_violations(violations)

    def test_missing_examples_detected(self, tmp_path):
        root = tmp_path / "repo"
        services = root / "nextseek_api" / "services"
        services.mkdir(parents=True)
        (services / "synthetic.py").write_text(
            textwrap.dedent(
                """
                from drf_spectacular.utils import extend_schema

                class V:
                    @extend_schema(description=FOO_DESC)
                    def list(self, request):
                        pass
                """
            ),
            encoding="utf-8",
        )
        violations = vc.validate_extend_schema_decorators(root)
        assert any("missing or empty examples=" in v.message for v in violations)

    def test_inline_description_detected(self, tmp_path):
        root = tmp_path / "repo"
        services = root / "nextseek_api" / "services"
        services.mkdir(parents=True)
        (services / "synthetic.py").write_text(
            textwrap.dedent(
                """
                from drf_spectacular.utils import extend_schema, OpenApiExample

                class V:
                    @extend_schema(
                        description="inline",
                        examples=[OpenApiExample(name="x", value={})],
                    )
                    def list(self, request):
                        pass
                """
            ),
            encoding="utf-8",
        )
        violations = vc.validate_extend_schema_decorators(root)
        assert any("inline string" in v.message for v in violations)

    def test_syntax_error_reported(self, tmp_path):
        root = tmp_path / "repo"
        services = root / "nextseek_api" / "services"
        services.mkdir(parents=True)
        (services / "broken.py").write_text("def oops(:\n", encoding="utf-8")
        violations = vc.validate_extend_schema_decorators(root)
        assert any(v.kind == "parse" for v in violations)

    def test_grandfather_skips_examples_violation(self, tmp_path):
        key = ("nextseek_api/views.py", "download")
        assert key in vc.EXTEND_SCHEMA_EXAMPLES_ALLOWLIST
        root = tmp_path / "repo"
        views = root / "nextseek_api"
        views.mkdir(parents=True)
        (views / "views.py").write_text(
            textwrap.dedent(
                """
                from drf_spectacular.utils import extend_schema

                class V:
                    @extend_schema(description=FOO_DESC)
                    def download(self, request):
                        pass
                """
            ),
            encoding="utf-8",
        )
        violations = vc.validate_extend_schema_decorators(root)
        assert not any(v.location.endswith("download") for v in violations)

    def test_inline_grandfather_skips_violation(self, tmp_path):
        root = tmp_path / "repo"
        views = root / "nextseek_api"
        views.mkdir(parents=True)
        (views / "views.py").write_text(
            textwrap.dedent(
                """
                from drf_spectacular.utils import extend_schema, OpenApiExample

                class V:
                    @extend_schema(
                        description="legacy timeline inline",
                        examples=[OpenApiExample(name="x", value={})],
                    )
                    def timeline(self, request):
                        pass
                """
            ),
            encoding="utf-8",
        )
        violations = vc.validate_extend_schema_decorators(root)
        assert violations == []


class TestValidatorHelpers:
    def test_examples_nonempty_non_list(self):
        assert vc._examples_nonempty(ast.Constant(value=1)) is True

    def test_description_is_constant_none(self):
        assert vc._description_is_constant(None) is True


class TestSkillAndPointers:
    def test_skill_exists_with_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        assert text.startswith("---\n") and "name: nextseek-viewset" in text
        assert "validate_viewset_conventions.py" in text
        assert "endpoint_descriptions" in text
        assert "descriptions_cc.py" in text
        assert "IsDjangoSuperuser" in text
        assert "BasicAuthentication" in text
        assert "AdminSampleViewSet" in text
        assert "EvaluatorViewSet" in text
        assert "project-scop" in text.lower() or "project scope" in text.lower()
        assert "pydantic" in text.lower()

    def test_agents_md_points_at_skill(self):
        text = AGENTS_MD.read_text(encoding="utf-8")
        assert "nextseek-viewset" in text
        assert "validate_viewset_conventions.py" in text


class TestValidatorCli:
    def test_cli_success(self):
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout

    def test_cli_bad_repo_root(self):
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), "--repo-root", "/tmp"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2

    def test_main_returns_one_on_violations(self, tmp_path):
        root = tmp_path / "repo"
        _write_minimal_desc_files(root)
        bad = root / "nextseek_api" / "endpoint_descriptions.py"
        bad.write_text('BAD_DESC = "**SUMMARY:** only\\n"\n', encoding="utf-8")
        assert vc.main(["--repo-root", str(root)]) == 1

    def test_main_success_on_minimal_repo(self, tmp_path):
        root = tmp_path / "repo"
        _write_minimal_desc_files(root)
        assert vc.main(["--repo-root", str(root)]) == 0

    def test_main_entrypoint_real_repo(self):
        assert vc.main(["--repo-root", str(REPO_ROOT)]) == 0
        assert vc.main(["--repo-root", "/tmp"]) == 2

    def test_format_violations(self):
        v = vc.Violation("description", "loc", "msg")
        assert vc._format_violations([v]) == "[description] loc: msg"
