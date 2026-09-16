"""Server-side manifest cache, keyed by run_dir + content digest.

build-upload-xlsx takes a manifest_id, not values: CC sends decisions and the
server fills every number from its own copy. A measured value therefore never
round-trips through the model, and cannot be altered by it.
"""
from __future__ import annotations

import hashlib
import json
import os

_ROOT = os.environ.get("NEXTSEEK_MANIFEST_DIR") or os.path.join(
    os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs", "manifests")


def save_manifest(run_manifest) -> str:
    payload = json.dumps(run_manifest.model_dump(), sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    os.makedirs(_ROOT, exist_ok=True)
    with open(os.path.join(_ROOT, f"{digest}.json"), "w", encoding="utf-8") as handle:
        handle.write(payload)
    return digest


def load_manifest(manifest_id: str):
    from NessieAI.ns.reingest.manifest import RunManifest
    if not manifest_id.isalnum():
        raise ValueError(f"bad manifest_id: {manifest_id!r}")
    path = os.path.join(_ROOT, f"{manifest_id}.json")
    with open(path, encoding="utf-8") as handle:
        return RunManifest.model_validate_json(handle.read())
