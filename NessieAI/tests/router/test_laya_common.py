"""JevLevROUTING U3: condense and the shared hash/temperature helpers (SPEC s4, s6.2, s8)."""
import hashlib
import math
import pathlib

from NessieAI.router import laya_common as lc
from NessieAI.router.router_context import HistoryTurn


def _t(i, msg, choice="nextseek_query", status="completed", reply="SECRET REPLY"):
    return HistoryTurn(position=i, user_message=msg, assistant_reply=reply,
                       router_choice=choice, status=status, error=None,
                       result_count=99, sample_uids=["D.SEQ-1"])


def test_condense_empty_history_is_one_line():
    assert lc.condense("find  mice\n now", []) == "Current message: find mice now"


def test_condense_one_turn_has_line_two_only():
    out = lc.condense("q", [_t(1, "prev  msg", "container_cc", "error")])
    assert out == "Current message: q\nPrevious message (container_cc, error): prev msg"


def test_condense_many_turns_lists_older_routes_oldest_first():
    h = [_t(1, "a", "unrelated"), _t(2, "b", "container_cc"), _t(3, "c", "nextseek_query")]
    out = lc.condense("q", h).split("\n")
    assert out[1] == "Previous message (nextseek_query, completed): c"
    assert out[2] == "Earlier routes in this chat: unrelated, container_cc"
    assert len(out) == 3


def test_condense_caps_and_no_reply_text():
    out = lc.condense("x" * 2000, [_t(1, "y" * 500)])
    cur, prev = out.split("\n")
    assert cur == "Current message: " + "x" * 1200
    assert prev.endswith(": " + "y" * 300)
    assert "SECRET" not in out and "D.SEQ" not in out and "99" not in out


def test_condense_is_deterministic_and_missing_choice():
    h = [_t(1, "a", None)]
    assert lc.condense("q", h) == lc.condense("q", h)
    assert "(none, completed)" in lc.condense("q", h)


def test_norm_text_hash():
    want = hashlib.sha256(b"find mice now").hexdigest()
    assert lc.norm_text_hash("  Find   MICE\nnow ") == want


def test_options_hash_depends_only_on_rendered_texts():
    a = {"options": [{"key": "a", "text": "one"}, {"key": "b", "text": "two"}]}
    b = {"options": [{"key": "a", "text": "one"}, {"key": "b", "text": "two"}], "source_hashes": {"x": "y"}}
    c = {"options": [{"key": "a", "text": "one"}, {"key": "b", "text": "twp"}]}
    assert lc.options_hash(a) == lc.options_hash(b) != lc.options_hash(c)
    assert len(lc.options_hash(a)) == 64


def test_apply_temperature():
    p = {"a": 0.7, "b": 0.2, "c": 0.1}
    assert lc.apply_temperature(p, 1.0) == {k: v for k, v in p.items()} or all(
        math.isclose(lc.apply_temperature(p, 1.0)[k], p[k]) for k in p)
    hot = lc.apply_temperature(p, 2.0)
    assert math.isclose(sum(hot.values()), 1.0)
    assert hot["a"] < 0.7 and hot["c"] > 0.1
    root = [0.7 ** 0.5, 0.2 ** 0.5, 0.1 ** 0.5]
    assert math.isclose(hot["a"], root[0] / sum(root))
    assert lc.apply_temperature({"a": 1.0, "b": 0.0}, 0.5) == {"a": 1.0, "b": 0.0}


def _tree(root: pathlib.Path, baml="B"):
    d = root / "NessieAI/dmac_assistant"
    (d / "baml_src").mkdir(parents=True)
    (d / "build_context").mkdir(parents=True)
    (d / "baml_src/router.baml").write_text(baml)
    (d / "baml_src/clients.baml").write_text("C")
    (d / "build_context/route_capabilities.json").write_text("{}")
    return root


def test_prompt_hash_tracks_each_file_and_the_followup_rule(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    h1 = lc.prompt_hash(root)
    assert h1 == lc.prompt_hash(root) and len(h1) == 64
    (root / "NessieAI/dmac_assistant/baml_src/router.baml").write_text("B2")
    assert lc.prompt_hash(root) != h1
    monkeypatch.setenv("NESSIE_FOLLOWUP_ROUTING", "cc")
    cc = lc.prompt_hash(root)
    monkeypatch.setenv("NESSIE_FOLLOWUP_ROUTING", "split")
    assert lc.prompt_hash(root) != cc


def test_prompt_hash_default_root_is_the_repo():
    assert len(lc.prompt_hash()) == 64


def test_no_torch_or_laya_imported():
    import subprocess, sys
    code = "import sys; import NessieAI.router.laya_common; print([m for m in ('torch','laya') if m in sys.modules])"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(pathlib.Path(__file__).resolve().parents[3]))
    assert r.stdout.strip() == "[]", r.stderr
