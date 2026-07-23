"""§4.B: NSTurnContext digest render + CLAUDE.md composition (pure functions)."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ns_digest
import ns_turn_context as ntc
from test_ns_turn_context import _bundle


def _ctx(**kw):
    c = ntc.from_bundle(_bundle(), session_id="s", turn_id=1)
    return c.model_copy(update=kw) if kw else c


def _ctx_with(turn_id: int):
    return ntc.from_bundle(_bundle(bid=turn_id), session_id="s", turn_id=turn_id)


def test_empty_contexts_render_empty_string():
    assert ns_digest.render_digest([]) == ""


def test_digest_renders_turn_facts_and_recall_pointer():
    md = ns_digest.render_digest([_ctx()])
    assert ns_digest.DIGEST_HEADER in md
    assert "turn 1" in md and "bundle 1" in md
    assert "mice treated with NDMA" in md
    assert "/admin/samples/retrieve" in md
    assert "total=222" in md and "rows=2" in md
    assert "uid, genotype" in md
    assert "nextseek-recall --turn 1" in md


def test_digest_no_rows_no_recall_pointer():
    md = ns_digest.render_digest([_ctx(full_result_available=False)])
    assert "nextseek-recall" not in md and "no raw rows available" in md


def test_digest_deterministic():
    assert ns_digest.render_digest([_ctx()]) == ns_digest.render_digest([_ctx()])


def test_digest_caps_at_max_turns_newest_kept():
    ctxs = [_ctx_with(turn_id=i) for i in range(1, 15)]
    md = ns_digest.render_digest(ctxs)
    assert "turn 14" in md and "turn 5" in md
    assert "turn 4" not in md
    assert f"showing the {ns_digest.DIGEST_MAX_TURNS} most recent" in md


def test_compose_both_digest_first():
    out = ns_digest.compose_turn_claude_md("DIGEST", "MEMORY")
    assert out == "DIGEST\n\nMEMORY"


def test_compose_digest_only():
    assert ns_digest.compose_turn_claude_md("DIGEST", "") == "DIGEST"


def test_compose_memory_only():
    assert ns_digest.compose_turn_claude_md("", "MEMORY") == "MEMORY"


def test_compose_neither_is_empty():
    assert ns_digest.compose_turn_claude_md("", "") == ""


def test_service_composes_via_pure_function_ast():
    src = (Path(__file__).resolve().parents[3]
           / "nextseek_api" / "services" / "cc_assistant.py").read_text()
    tree = ast.parse(src)
    compose_assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                       and isinstance(n.value, ast.Call)
                       and isinstance(n.value.func, ast.Attribute)
                       and n.value.func.attr == "compose_turn_claude_md"]
    assert compose_assigns, "compose_turn_claude_md is never called in the service"
    digest_assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and isinstance(n.value, ast.Call)
                      and isinstance(n.value.func, ast.Attribute)
                      and n.value.func.attr == "render_digest"]
    assert digest_assigns, "render_digest never called"
