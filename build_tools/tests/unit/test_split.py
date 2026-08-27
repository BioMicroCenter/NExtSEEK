"""Unit tests for build_tools.ingest_nextseek_docs.split."""
from __future__ import annotations

from build_tools.ingest_nextseek_docs.split import Section, split_by_h1


def test_split_empty_returns_empty_list() -> None:
    assert split_by_h1("") == []


def test_split_whitespace_only_returns_empty_list() -> None:
    assert split_by_h1("   \n\n  \n") == []


def test_split_single_h1_fields() -> None:
    md = "# Welcome\n\nIntro paragraph for welcome.\n\nMore content.\n"
    result = split_by_h1(md)
    assert len(result) == 1
    s = result[0]
    assert s.ordinal == 1
    assert s.title == "Welcome"
    assert s.slug == "welcome"
    assert "Intro paragraph for welcome." in s.body
    assert s.body.startswith("# Welcome")
    assert s.description == "Intro paragraph for welcome."


def test_split_three_h1s_have_contiguous_ordinals() -> None:
    md = "# A\n\nA body.\n# B\n\nB body.\n# C\n\nC body.\n"
    result = split_by_h1(md)
    assert [s.ordinal for s in result] == [1, 2, 3]
    assert [s.title for s in result] == ["A", "B", "C"]
    assert [s.slug for s in result] == ["a", "b", "c"]


def test_split_slug_collision_appends_numeric_suffix() -> None:
    md = "# Hello World!\n\nFirst.\n# Hello World\n\nSecond.\n# Hello World?\n\nThird.\n"
    result = split_by_h1(md)
    assert [s.slug for s in result] == [
        "hello-world",
        "hello-world-2",
        "hello-world-3",
    ]


def test_split_section_without_body_uses_fallback_description() -> None:
    md = "# Overview\n## Subsection\nContent here.\n# Next Section\n\nReal intro.\n"
    result = split_by_h1(md)
    assert result[0].description == "(section overview)"
    assert result[1].description == "Real intro."


def test_split_ignores_h1_inside_fenced_code_block() -> None:
    md = (
        "# Real Heading\n\n"
        "Intro.\n\n"
        "```bash\n"
        "# this is a shell comment\n"
        "echo hi\n"
        "```\n\n"
        "More content after fence.\n"
    )
    result = split_by_h1(md)
    assert len(result) == 1, (
        f"fence-aware split should yield 1 section, got {len(result)}: "
        f"{[s.title for s in result]}"
    )
    assert result[0].title == "Real Heading"
    assert "# this is a shell comment" in result[0].body


def test_split_description_truncates_at_word_boundary() -> None:
    long_para = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
        "nu xi omicron pi rho sigma tau upsilon phi chi psi omega one two "
        "three four five six seven eight nine ten."
    )
    md = f"# Long Title\n\n{long_para}\n"
    result = split_by_h1(md)
    desc = result[0].description
    assert len(desc) <= 141
    assert desc.endswith("…"), f"expected ellipsis, got: {desc!r}"
    assert " " in desc or desc == "…"
    stripped = desc.rstrip("…").rstrip()
    assert long_para.startswith(stripped), (
        "truncated description must be a prefix of the original paragraph"
    )


def test_split_description_under_140_chars_not_truncated() -> None:
    md = "# Short\n\nShort paragraph.\n"
    result = split_by_h1(md)
    assert result[0].description == "Short paragraph."
    assert not result[0].description.endswith("…")


def test_split_slug_strips_nonalnum_runs() -> None:
    md = "# Foo -- Bar\n\nBody.\n"
    result = split_by_h1(md)
    assert result[0].slug == "foo-bar"


def test_split_slug_falls_back_to_section_for_punctuation_only_title() -> None:
    md = "# !!!\n\nBody.\n"
    result = split_by_h1(md)
    assert result[0].slug == "section"


def test_split_ignores_h2_h3_as_section_boundaries() -> None:
    md = "# Root\n\nRoot intro.\n\n## Sub\n\nSub body.\n\n### Subsub\n\nMore.\n"
    result = split_by_h1(md)
    assert len(result) == 1
    assert "## Sub" in result[0].body
    assert "### Subsub" in result[0].body


def test_split_returns_section_dataclass_instances() -> None:
    result = split_by_h1("# A\n\nBody.\n")
    assert isinstance(result[0], Section)


def test_split_description_without_word_boundary_truncates_hard() -> None:
    long_token = "a" * 200
    md = f"# Long Token\n\n{long_token}\n"
    result = split_by_h1(md)
    assert result[0].description == ("a" * 140) + "…"
