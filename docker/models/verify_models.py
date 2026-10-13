"""Check a Hugging Face hub-layout cache against the pinned model manifests (#28).

usage: verify_models.py [--load] <cache-dir> <manifest.json>...

Every pinned file must be there with its sha256, nothing else may sit in the pinned
snapshot, and refs/main must name the pinned commit (the loaders resolve "main"
through it). Standard library only, so the Dockerfile's models stage and the dev
test lanes can run it without the app's venv.

--load also loads each model offline with the app's own sentence-transformers and
encodes one string. The app image runs it right after `uv sync`, so a model the
image's libraries cannot load fails the build before anything restarts.
Exit 0 when every manifest passes, 1 otherwise, 2 on a usage error.
"""

import hashlib
import json
import os
import sys


def load_manifest(path):
    with open(path, "rb") as fh:
        return json.load(fh)


def model_dir(cache, repo_id):
    return os.path.join(cache, "models--" + repo_id.replace("/", "--"))


def verify(cache, manifest):
    """The problems found, each naming a path; empty when the cache matches exactly."""
    mdir = model_dir(cache, manifest["repo_id"])
    snapshot = os.path.join(mdir, "snapshots", manifest["revision"])
    if not os.path.isdir(snapshot):
        return ["snapshot dir missing: " + snapshot]
    problems = []
    ref = os.path.join(mdir, "refs", "main")
    try:
        with open(ref) as fh:
            pinned = fh.read().strip()
        if pinned != manifest["revision"]:
            problems.append(f"ref mismatch: {ref} contains {pinned!r}, manifest revision is {manifest['revision']!r}")
    except OSError:
        problems.append("ref missing: " + ref)
    for rel, want in sorted(manifest["files"].items()):
        path = os.path.join(snapshot, rel)
        if not os.path.isfile(path):  # a missing file or a broken symlink into blobs/
            problems.append("missing file: " + path)
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != want:
            problems.append(f"hash mismatch: {path} (got {digest.hexdigest()}, manifest {want})")
    pinned_files = {os.path.normpath(rel) for rel in manifest["files"]}
    for root, _dirs, names in os.walk(snapshot):
        for name in names:
            full = os.path.join(root, name)
            if os.path.normpath(os.path.relpath(full, snapshot)) not in pinned_files:
                problems.append("extra file not in manifest: " + full)
    return problems


def load(cache, manifest):
    """Load the model from `cache` without the network and encode one string; return the vector width."""
    os.environ["HF_HUB_OFFLINE"] = "1"  # read when huggingface_hub is first imported
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(manifest["repo_id"], cache_folder=cache, local_files_only=True)
    vector = model.encode(["embedding model check"])
    if vector.ndim != 2 or vector.shape[0] != 1 or vector.shape[1] == 0:
        raise RuntimeError(f"{manifest['repo_id']} returned a vector of shape {vector.shape}")
    return vector.shape[1]


def main(argv):
    args = argv[1:]
    do_load = args[:1] == ["--load"]
    if do_load:
        args = args[1:]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cache, manifest_paths = args[0], args[1:]
    failed = False
    for path in manifest_paths:
        manifest = load_manifest(path)
        problems = verify(cache, manifest)
        for problem in problems:
            print("VERIFY FAIL: " + problem, file=sys.stderr)
        if problems:
            failed = True
            continue
        print(f"OK {manifest['repo_id']}@{manifest['revision']} ({len(manifest['files'])} files)")
        if do_load:
            print(f"LOADED {manifest['repo_id']} offline: {load(cache, manifest)}-wide vector")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
