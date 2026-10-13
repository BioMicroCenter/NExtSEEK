"""The embedding model pins the app image bakes (docker/models/, #28) and their verifier.

Stdlib only; no network, no model. verify_models.py is loaded by path because docker/
is not a package.
"""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = REPO_ROOT / "docker" / "models"
MANIFESTS = sorted(MODELS.glob("*.manifest.json"))


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_models", MODELS / "verify_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vm = _load_verifier()


def test_both_models_are_pinned():
    repos = {json.loads(p.read_text())["repo_id"] for p in MANIFESTS}
    assert repos == {"BAAI/bge-small-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2"}


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
def test_a_manifest_pins_a_commit_and_a_hash_per_file(path):
    manifest = json.loads(path.read_text())
    assert re.fullmatch(r"[^/\s]+/[^/\s]+", manifest["repo_id"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["revision"]), "a commit, never a branch such as main"
    assert manifest["files"]
    for rel, digest in manifest["files"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), rel


def _cache(tmp_path, files):
    """A hub-layout cache holding exactly `files`, and the manifest that pins it."""
    revision = "a" * 40
    model = tmp_path / "models--org--model"
    snapshot = model / "snapshots" / revision
    for rel, data in files.items():
        (snapshot / rel).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / rel).write_bytes(data)
    (model / "refs").mkdir(parents=True)
    (model / "refs" / "main").write_text(revision)
    manifest = {"repo_id": "org/model", "revision": revision,
                "files": {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}}
    return snapshot, manifest


FILES = {"config.json": b"{}", "1_Pooling/config.json": b'{"pooling": 1}'}


def test_verify_passes_on_an_exact_cache(tmp_path):
    _snapshot, manifest = _cache(tmp_path, FILES)
    assert vm.verify(str(tmp_path), manifest) == []


def test_verify_names_a_changed_byte(tmp_path):
    snapshot, manifest = _cache(tmp_path, FILES)
    (snapshot / "1_Pooling" / "config.json").write_bytes(b'{"pooling": 2}')
    problems = vm.verify(str(tmp_path), manifest)
    assert len(problems) == 1 and "hash mismatch" in problems[0]
    assert str(snapshot / "1_Pooling" / "config.json") in problems[0]


def test_verify_names_a_missing_file(tmp_path):
    snapshot, manifest = _cache(tmp_path, FILES)
    (snapshot / "config.json").unlink()
    problems = vm.verify(str(tmp_path), manifest)
    assert len(problems) == 1 and "missing file" in problems[0]
    assert str(snapshot / "config.json") in problems[0]


def test_verify_names_an_extra_file(tmp_path):
    snapshot, manifest = _cache(tmp_path, FILES)
    (snapshot / "pytorch_model.bin").write_bytes(b"x")
    problems = vm.verify(str(tmp_path), manifest)
    assert len(problems) == 1 and "extra file" in problems[0]
    assert str(snapshot / "pytorch_model.bin") in problems[0]


def test_verify_names_a_ref_that_is_not_the_pin(tmp_path):
    _snapshot, manifest = _cache(tmp_path, FILES)
    ref = tmp_path / "models--org--model" / "refs" / "main"
    ref.write_text("b" * 40)
    problems = vm.verify(str(tmp_path), manifest)
    assert len(problems) == 1 and "ref mismatch" in problems[0] and str(ref) in problems[0]


def test_verify_names_a_missing_snapshot(tmp_path):
    _snapshot, manifest = _cache(tmp_path, FILES)
    problems = vm.verify(str(tmp_path / "elsewhere"), manifest)
    assert len(problems) == 1 and "snapshot dir missing" in problems[0]


def test_the_cli_exits_1_on_a_bad_cache_and_0_on_a_good_one(tmp_path, capsys):
    snapshot, manifest = _cache(tmp_path / "cache", FILES)
    manifest_path = tmp_path / "m.manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    assert vm.main(["verify_models.py", str(tmp_path / "cache"), str(manifest_path)]) == 0
    (snapshot / "config.json").write_bytes(b"[]")
    assert vm.main(["verify_models.py", str(tmp_path / "cache"), str(manifest_path)]) == 1
    assert "hash mismatch" in capsys.readouterr().err


def test_the_dev_provisioning_script_reads_the_moved_bge_pin_and_the_one_verifier():
    script = (REPO_ROOT / "startup" / "dev" / "provision_embedding_model.sh").read_text()
    assert 'MANIFEST="${REPO_ROOT}/docker/models/bge-small-en-v1.5.manifest.json"' in script
    assert 'VERIFIER="${REPO_ROOT}/docker/models/verify_models.py"' in script
    assert (MODELS / "bge-small-en-v1.5.manifest.json").is_file()
