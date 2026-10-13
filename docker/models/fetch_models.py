"""The app image's models stage (#28): download every pinned model, then verify it.

usage: fetch_models.py <dir holding *.manifest.json and verify_models.py> <cache-dir>

Needs the network and huggingface-hub (the Dockerfile pins its version on the RUN
line). Only the manifest's files are fetched, at the pinned commit, into a
hub-layout cache. A fetch by commit writes no refs/main, and the loaders resolve
"main" through it, so this writes it.
"""

import glob
import os
import sys

from huggingface_hub import snapshot_download

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_models  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, cache = argv[1], argv[2]
    manifests = sorted(glob.glob(os.path.join(src, "*.manifest.json")))
    if not manifests:
        print(f"no *.manifest.json in {src}", file=sys.stderr)
        return 2
    for path in manifests:
        manifest = verify_models.load_manifest(path)
        snapshot_download(manifest["repo_id"], revision=manifest["revision"], cache_dir=cache,
                          allow_patterns=sorted(manifest["files"]))
        ref = os.path.join(verify_models.model_dir(cache, manifest["repo_id"]), "refs", "main")
        os.makedirs(os.path.dirname(ref), exist_ok=True)
        with open(ref, "w") as fh:
            fh.write(manifest["revision"])
    return verify_models.main(["verify_models.py", cache, *manifests])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
