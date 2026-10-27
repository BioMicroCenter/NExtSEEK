import json
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
    monkeypatch.setattr(fp, "corpus_projection", lambda: {})
    b = fp.current()
    probe.write_text("2")
    assert fp.current() != b


def test_a_renamed_file_changes_the_fingerprint(tmp_path, monkeypatch):
    """The path is hashed with the bytes, so moving an input is a change too."""
    (tmp_path / "a.json").write_text("1")
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "INPUTS", ("*.json",))
    monkeypatch.setattr(fp, "corpus_projection", lambda: {})
    before = fp.current()
    (tmp_path / "a.json").rename(tmp_path / "b.json")
    assert fp.current() != before


def test_the_inputs_cover_what_the_model_sees():
    """The agent's tool schemas and next-step messages, its loop, and the catalog in its system prompt."""
    for name in ("pipeline/agent_tools.py", "pipeline/agent.py", "seqera/catalog.py"):
        assert f"NessieAI/chat_nextseek/src/chat_nextseek/{name}" in fp.INPUTS, name


def test_the_inputs_cover_the_routing_code_and_the_selection_payload_code():
    chat = "NessieAI/chat_nextseek/src/chat_nextseek/"
    for path in ("NessieAI/router/router.py", "NessieAI/router/router_context.py", "NessieAI/router/laya.py",
                 "NessieAI/router/laya_common.py", "NessieAI/router/laya_options.json", "NessieAI/router/policy.py",
                 "NessieAI/router/posterior_selector.py", "NessieAI/router/followup.py",
                 "NessieAI/router/family_labels.py", "NessieAI/dmac_assistant/baml_src/clients.baml",
                 "NessieAI/dmac_assistant/baml_src/router.baml",
                 "NessieAI/dmac_assistant/build_context/route_capabilities.json",
                 chat + "pipeline/sample_digest.py", chat + "reports/metadata.py", chat + "reports/protocols.py",
                 chat + "seqera/nfcore_atlas.py", chat + "seqera/nfcore_schema.py",
                 chat + "seqera/pipeline_params.py", chat + "seqera/schema_check.py"):
        assert path in fp.INPUTS, path


def _corpus(tmp_path, monkeypatch, **payload):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "INPUTS", ())
    monkeypatch.setattr(fp, "CORPUS", "corpus.json")
    return path


BASE = {"taxonomy_version": "v1", "families": {"b": {"description": "B"}, "a": {"description": "A"}},
        "variants": [{"id": "x", "question": "q"}]}


def test_the_corpus_is_fingerprinted_by_what_the_router_reads_not_by_the_whole_file(tmp_path, monkeypatch):
    path = _corpus(tmp_path, monkeypatch, **BASE)
    before = fp.current()
    path.write_text(json.dumps({**BASE, "variants": [{"id": "y", "question": "another"}]}))
    assert fp.current() == before                       # variants are the harness's, not the router's
    path.write_text(json.dumps({**BASE, "families": {"a": {"description": "A"}, "b": {"description": "B"}}}))
    assert fp.current() == before                       # key order is not a change
    path.write_text(json.dumps({**BASE, "families": {"b": {"description": "B!"}, "a": {"description": "A"}}}))
    assert fp.current() != before                       # a description is
    path.write_text(json.dumps({**BASE, "taxonomy_version": "v2"}))
    assert fp.current() != before                       # so is the taxonomy version
    path.write_text(json.dumps({**BASE, "families": {"a": {"description": "A"}}}))
    assert fp.current() != before                       # and the family set


def test_the_projection_is_what_family_labels_reads():
    from NessieAI.router.family_labels import corpus_snapshot
    snap = corpus_snapshot(REPO / fp.CORPUS)
    got = fp.corpus_projection()
    assert got["taxonomy_version"] == snap.taxonomy_version
    assert got["families"] == {name: snap.descriptions[name] for name in snap.families}
    assert list(got["families"]) == sorted(got["families"])


def test_files_hashes_each_input_and_the_corpus_projection(tmp_path, monkeypatch):
    (tmp_path / "a.json").write_text("1")
    (tmp_path / "b.json").write_text("2")
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "INPUTS", ("*.json",))
    monkeypatch.setattr(fp, "corpus_projection", lambda: {"families": {}})
    files = fp.files()
    assert set(files) == {"a.json", "b.json", fp.CORPUS_KEY} and all(len(v) == 64 for v in files.values())
    assert files == fp.files()


def test_changed_names_every_edited_added_and_removed_input(tmp_path, monkeypatch):
    for name in ("a.json", "b.json", "c.json"):
        (tmp_path / name).write_text(name)
    monkeypatch.setattr(fp, "REPO", tmp_path)
    monkeypatch.setattr(fp, "INPUTS", ("*.json",))
    monkeypatch.setattr(fp, "corpus_projection", lambda: {})
    recorded = fp.files()
    assert fp.changed(recorded) == []
    (tmp_path / "a.json").write_text("edited")
    (tmp_path / "b.json").unlink()
    (tmp_path / "d.json").write_text("new")
    assert fp.changed(recorded) == ["a.json", "b.json", "d.json"]
    assert fp.changed({}) == sorted(fp.files())            # a recording without per-file hashes: all unknown


def test_the_real_inputs_have_per_file_hashes():
    files = fp.files()
    assert fp.CORPUS_KEY in files
    assert "NessieAI/chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py" in files
