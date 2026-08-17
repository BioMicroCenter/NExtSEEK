"""The paired run's record.

Wraps `NessieManifestEntry` UNCHANGED rather than defining a parallel entry model,
so outage detection, cost accounting and the observation schema all apply here
without a second implementation that can drift from the first.
"""
from __future__ import annotations

import json
import os
import pathlib

from pydantic import BaseModel, ConfigDict, Field

from nessie_tests.manifest import NessieManifestEntry

# NOT "manifest.json" — that is what `runner.run_suite` already writes for a
# normal run (runner.py:412), and a paired manifest is a DIFFERENT SCHEMA that
# happens to be structurally compatible in the worst possible way. Sharing the
# name is silently destructive in both directions:
#
#   Reading — pydantic ignores extra keys and both `BayesManifest` fields have
#   defaults, so a normal `manifest.json` validates as an EMPTY `BayesManifest`
#   rather than raising. `completed_arms` then returns the empty set, `--resume`
#   concludes nothing has run, and the whole paired run is repaid — the exact
#   outcome `completed_arms` exists to prevent.
#
#   Writing — pairs are written as they complete, so the FIRST pair overwrites
#   the prior run's record. `load_manifest` on the result fails with 4 missing
#   required fields (`started_at`, `ended_at`, `tier`, `scope`) and that run's
#   `entries` are gone for good.
#
# A distinct filename makes the collision impossible instead of merely unlikely,
# which is worth more than the shared constant it costs. Pinned by
# `test_a_normal_run_directory_is_not_mistaken_for_a_resumable_paired_run`.
MANIFEST_NAME = "bayes_manifest.json"


class BayesPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    family: str
    hibayes_subtype: str | None = None
    # Either arm may be None: pairs are written as they complete so an interrupted
    # run can resume, and a half-written pair must round-trip rather than fail.
    ns: NessieManifestEntry | None = None
    cc: NessieManifestEntry | None = None


class BayesManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="bayes_manifest/v1")
    run_meta: dict = Field(default_factory=dict)
    pairs: list[BayesPair] = Field(default_factory=list)


def write_bayes_manifest(m: BayesManifest, out_dir) -> pathlib.Path:
    """Serialise to a sibling temp file and `os.replace` it into place.

    ATOMIC ON PURPOSE. `bayesian.run_paired` writes after every ARM, so a
    ~130-variant paired run rewrites this file ~260 times, and a plain
    `write_text` truncates before it writes. A Ctrl-C, an OOM kill or a full disk
    landing inside any one of those windows leaves half a JSON document, and
    `read_bayes_manifest` then raises on the whole file — destroying every
    completed arm recorded by the 259 writes that succeeded, which is precisely
    what writing per arm exists to protect.

    The temp file is a SIBLING so `os.replace` is a same-filesystem rename and
    therefore actually atomic; `/tmp` would silently degrade to a copy across a
    mount boundary. It carries the pid so two runs sharing an out_dir cannot
    consume each other's partial file.
    """
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_NAME
    tmp = out / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
    tmp.write_text(m.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_bayes_manifest(out_dir) -> BayesManifest | None:
    path = pathlib.Path(out_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    return BayesManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def completed_arms(m: BayesManifest) -> set[tuple[str, str]]:
    """`(variant_id, arm)` for every arm that produced an entry.

    This is what `--resume` skips. Keyed on the ARM rather than the pair, because
    a run interrupted between the NS and CC halves of one question must not repay
    for the NS half.
    """
    done: set[tuple[str, str]] = set()
    for p in m.pairs:
        if p.ns is not None:
            done.add((p.id, "ns"))
        if p.cc is not None:
            done.add((p.id, "cc"))
    return done
