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


def _violations_for_source(source: str, rel_path: str = "nextseek_api/services/synthetic.py") -> list:
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "extend_schema"
            ):
                continue
            key = (rel_path, node.name)
            examples = vc._extend_schema_keyword(dec, "examples")
            if not vc._examples_nonempty(examples) and key not in vc.EXTEND_SCHEMA_EXAMPLES_ALLOWLIST:
                violations.append(key)
            description = vc._extend_schema_keyword(dec, "description")
            if (
                description is not None
                and not vc._description_is_constant(description)
                and key not in vc.INLINE_DESCRIPTION_ALLOWLIST
            ):
                violations.append(("inline", key))
    return violations


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


class TestExtendSchemaAst:
    def test_missing_examples_detected(self):
        src = textwrap.dedent(
            """
            from drf_spectacular.utils import extend_schema

            class V:
                @extend_schema(description=FOO_DESC)
                def list(self, request):
                    pass
            """
        )
        hits = _violations_for_source(src)
        assert ("nextseek_api/services/synthetic.py", "list") in hits

    def test_examples_present_passes(self):
        src = textwrap.dedent(
            """
            from drf_spectacular.utils import extend_schema, OpenApiExample

            class V:
                @extend_schema(
                    description=FOO_DESC,
                    examples=[OpenApiExample(name="x", value={})],
                )
                def list(self, request):
                    pass
            """
        )
        assert _violations_for_source(src) == []

    def test_inline_description_detected(self):
        src = textwrap.dedent(
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
        )
        hits = _violations_for_source(src)
        assert ("inline", ("nextseek_api/services/synthetic.py", "list")) in hits

    def test_repo_integration_clean(self):
        violations = vc.validate_repo(REPO_ROOT)
        assert violations == [], vc._format_violations(violations)


class TestDescriptionConstants:
    def test_all_desc_constants_parse(self):
        for rel in vc.DESCRIPTION_FILES:
            path = REPO_ROOT / rel
            names = [name for name, _ in vc._iter_desc_constants(path)]
            assert names, f"no *_DESC constants found in {rel}"


class TestSkillAndPointers:
    def test_skill_exists_with_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        assert text.startswith("---\n") and "name: nextseek-viewset" in text
        assert "validate_viewset_conventions.py" in text
        assert "endpoint_descriptions" in text
        assert "IsDjangoSuperuser" in text
        assert "BasicAuthentication" in text
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

    def test_main_reports_violations(self, tmp_path):
        bad_desc = REPO_ROOT / "nextseek_api" / "endpoint_descriptions.py"
        fake_root = tmp_path / "repo"
        (fake_root / "nextseek_api").mkdir(parents=True)
        (fake_root / "nextseek_api" / "endpoint_descriptions.py").write_text(
            'BAD_DESC = "**SUMMARY:** x\\n"\n',
            encoding="utf-8",
        )
        # Point validator at fake tree with one broken description file only
        violations = vc.validate_description_files(fake_root)
        assert violations
        assert vc._format_violations(violations).startswith("[description]")


class TestValidatorHelpers:
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

    def test_iter_desc_constants_tuple_join(self, tmp_path):
        mod = tmp_path / "descriptions.py"
        mod.write_text(
            'FOO_DESC = ("part1" "part2")\n',
            encoding="utf-8",
        )
        pairs = list(vc._iter_desc_constants(mod))
        assert pairs == [("FOO_DESC", "part1part2")]

    def test_examples_nonempty_non_list(self):
        assert vc._examples_nonempty(ast.Constant(value=1)) is True

    def test_description_is_constant_none(self):
        assert vc._description_is_constant(None) is True

    def test_validate_missing_description_file(self, tmp_path):
        fake = tmp_path / "repo"
        fake.mkdir()
        violations = vc.validate_description_files(fake)
        assert any("missing" in v.message for v in violations)

    def test_validate_extend_schema_syntax_error(self, tmp_path):
        fake = tmp_path / "repo"
        services = fake / "nextseek_api" / "services"
        services.mkdir(parents=True)
        bad = services / "broken.py"
        bad.write_text("def oops(:\n", encoding="utf-8")
        violations = vc.validate_extend_schema_decorators(fake)
        assert any(v.kind == "parse" for v in violations)

    def test_grandfather_allowlist_skips_examples_violation(self):
        key = next(iter(vc.EXTEND_SCHEMA_EXAMPLES_ALLOWLIST))
        rel_path, func_name = key
        src = textwrap.dedent(
            f"""
            from drf_spectacular.utils import extend_schema

            class V:
                @extend_schema(description=FOO_DESC)
                def {func_name}(self, request):
                    pass
            """
        )
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                dec = node.decorator_list[0]
                examples = vc._extend_schema_keyword(dec, "examples")
                assert not vc._examples_nonempty(examples)
                assert key in vc.EXTEND_SCHEMA_EXAMPLES_ALLOWLIST

    def test_main_entrypoint(self):
        assert vc.main(["--repo-root", str(REPO_ROOT)]) == 0
        assert vc.main(["--repo-root", "/tmp"]) == 2
