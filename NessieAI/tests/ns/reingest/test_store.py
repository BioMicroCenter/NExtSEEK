import json

from NessieAI.ns.reingest import manifest, store


def test_a_manifest_saved_before_upstream_matrix_was_removed_still_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_ROOT", str(tmp_path))
    data = manifest.RunManifest(run_dir="/net/cluster/runs/a").model_dump()
    data["upstream_matrix"] = {"path": "/x", "checksum": "c", "uid": None}
    (tmp_path / "abc123.json").write_text(json.dumps(data))
    loaded = store.load_manifest("abc123")
    assert loaded.run_dir == "/net/cluster/runs/a"
    assert "upstream_matrix" not in loaded.model_dump()
