from pathlib import Path

from NessieAI.tests.pipeline_scenarios import fingerprint as fp

REPO = Path(__file__).resolve().parents[3]


def test_every_input_glob_matches_a_file():
    for pattern in fp.INPUTS:
        assert list(REPO.glob(pattern)), pattern


def test_the_fingerprint_is_stable_and_changes_with_an_input(tmp_path, monkeypatch):
    a = fp.current()
    assert a == fp.current() and len(a) == 64
    probe = tmp_path / "x.json"
    probe.write_text("1")
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "INPUTS", ("x.json",))
    b = fp.current()
    probe.write_text("2")
    assert fp.current() != b


def test_a_renamed_file_changes_the_fingerprint(tmp_path, monkeypatch):
    """The path is hashed with the bytes, so moving an input is a change too."""
    (tmp_path / "a.json").write_text("1")
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "INPUTS", ("*.json",))
    before = fp.current()
    (tmp_path / "a.json").rename(tmp_path / "b.json")
    assert fp.current() != before


def test_the_inputs_cover_what_the_model_sees():
    """The agent's tool schemas and next-step messages, its loop, and the catalog in its system prompt."""
    for name in ("pipeline/agent_tools.py", "pipeline/agent.py", "seqera/catalog.py"):
        assert f"NessieAI/chat_nextseek/src/chat_nextseek/{name}" in fp.INPUTS, name
