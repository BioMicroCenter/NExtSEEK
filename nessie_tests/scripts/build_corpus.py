"""One-shot migration: catalog.json + overlay.json + retired.json -> corpus.json.

Kept after the migration rather than deleted, and NOT because a test imports it --
after Task 3 nothing does. It is the manual adoption tool: when the vendored
`catalog.json` moves and `tests/test_catalog_drift.py` fails, you run this with
`--out /tmp/new.json` and diff that against `corpus.json` to see what upstream
changed before deciding what to adopt. That is a workflow, not a caller.

Ordering rules, which exist so the output is stable enough to diff:
  * families in the order catalog.json declares them, then overlay-only families
  * variants in catalog order within a family, then overlay-only variants
  * an overlay variant with a base id REPLACES the base one IN PLACE, keeping the
    base position (this mirrors `corpus.merged`, which keeps base ordering)
  * overlay-only variants are appended at the end of THEIR BLOCK. `corpus.merged`
    instead appends them at the end of the whole list, which no block-structured
    file can reproduce -- see KNOWN DIVERGENCE below

The output MIRRORS THE SOURCE NESTING rather than regrouping by declared family.
Operator ruling 2026-08-04: structure follows the source so global order is
reproducible. Each variant carries its own declared `family`, which is the
authoritative one -- the block it sits in is NOT.

KNOWN DIVERGENCE from `corpus.merged`, measured 2026-08-04. Full detail in
.superpowers/sdd/nessie-bayesian-plan-1-unified-corpus/task-1-report.md.
14 of the 17 overlay-only variants do land at the end of the whole list, because
their blocks (`nessie_route`, `nessie_green`, `nessie_repro`) exist only in the
overlay and so are emitted after every base block. The other 3 sit in a block that
ALSO exists in the base catalog -- `write.update_scientist_must_confirm_first` and
`write.delete_sample_must_confirm_first` under `writes_unsupported`, plus the
retired `advanced.find_me_nhp_samples_from_study_ns_graph` under `search_advanced`
-- so they land at the end of their block, mid-list.

This is not a fixable defect of this script. A JSON object cannot hold two blocks
of one name, and a block flattens to ONE contiguous run, so `writes_unsupported`
cannot supply both merged() indices 247-249 (its base members) and 281-282 (its
overlay-only members) with `refine_and_recall` occupying 250-274 in between.
While the nesting mirrors the source, ordered equivalence with `corpus.merged` is
unreachable for exactly 2 active variants. CONTENT equivalence is exact: compared
sorted by id, all 283 active variants match `corpus.merged` with zero diffs.

Blocks `graph_stale`, `reporting_artifacts` and `routing_graph` do not appear in
the output. Every variant in them overrides a base id, and the in-place rule above
pins those to their BASE position. Giving every override its own block would move
30 overrides out of base order -- ten times the divergence it would remove.
"""
from __future__ import annotations

import argparse
import json
import pathlib

from nessie_tests import corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_BLOCKS = ("criterion_rewrites", "route_policy", "family_floor", "consistency_groups")


def _variant_dict(v, *, origin: str, retirement: dict | None, raw: dict) -> dict:
    """One variant, definition first then metadata, so a diff reads naturally.

    ``raw`` is the source body, carried so hand-written annotation keys survive.
    `Variant` has six fields and pydantic's default `extra="ignore"` drops
    everything else in silence, so anything reconstructed from the model alone
    loses them. overlay.json carries 35 `_why` keys plus `_why_superseded_*` and
    dated notes, and two tests asserted on them -- including one checking that the
    DELETE case warns it can destroy a real sample, on a case targeting a real UID.
    Emitting a fixed key set silently deleted all of it.
    """
    annotations = {k: val for k, val in raw.items() if k.startswith("_")}
    return {
        **annotations,
        "id": v.id,
        # The DECLARED family, not the nesting key. `Variant.family` is read from
        # the variant BODY (chat_nextseek/e2e/catalog.py:24), and 7 variants
        # declare a family that differs from the block they sit in -- e.g.
        # routing.lab_ooc_kamm_count sits under `search_advanced` and declares
        # `routing_lab`. `corpus.sample()` buckets on v.family, so re-deriving it
        # from the nesting key silently moves those 7 into different sampling
        # buckets and changes every seeded case set.
        "family": v.family,
        "name": v.name,
        "tags": [t for t in v.tags if t not in ("base", "overlay")],
        "requires_env": list(v.requires_env),
        "turns": [
            {"label": t.label, "query": t.query,
             "pass_criteria": [c.model_dump(exclude_none=False) for c in t.pass_criteria]}
            for t in v.turns
        ],
        "status": "retired" if retirement else "active",
        "origin": origin,
        "is_bayesian": False,
        "hibayes_subtype": None,
        "expected_behavior": None,
        "artifact_expected": None,
        "artifact_kind": None,
        "retirement": retirement,
    }


def build(catalog_path, overlay_path, retired_path) -> dict:
    raw_catalog = json.loads(pathlib.Path(catalog_path).read_text(encoding="utf-8"))
    raw_overlay = json.loads(pathlib.Path(overlay_path).read_text(encoding="utf-8"))
    raw_retired = json.loads(pathlib.Path(retired_path).read_text(encoding="utf-8"))
    retirements = raw_retired.get("retired") or {}

    base = {v.id: v for v in corpus.load_base()}
    overlay = {v.id: v for v in corpus.load_overlay(pathlib.Path(overlay_path))}

    # The RAW source bodies, keyed by id. `_variant_dict` needs them because the
    # parsed `Variant` cannot carry a `_why`: the model has six fields and
    # pydantic's `extra="ignore"` drops the rest without a word.
    #
    # Indexed by ORIGIN rather than flattened. `{**base_raw, **overlay_raw}` would
    # also be correct -- overlay-wins is exactly the precedence `_emit` is called
    # with below -- but that is the same rule written twice, in two places that
    # can drift. Keying on the origin the caller already decided makes the body
    # come from the definition being emitted BY CONSTRUCTION. (The two disagree
    # on 28 ids, so getting the precedence backwards is not a hypothetical: it
    # would attach the base body's absent annotations to an overlay override.)
    raw_bodies = {
        "base": {v["id"]: v for f in raw_catalog["families"].values() for v in f["variants"]},
        "overlay": {v["id"]: v for f in raw_overlay["families"].values() for v in f["variants"]},
    }

    families: dict[str, dict] = {}

    def _emit(fam_name: str, description: str, variant, origin: str) -> None:
        fam = families.setdefault(fam_name, {"description": description, "variants": []})
        fam["variants"].append(
            _variant_dict(variant, origin=origin, retirement=retirements.get(variant.id),
                          raw=raw_bodies[origin][variant.id])
        )

    # Base families, in catalog order. An overlay variant with a matching id takes
    # the base one's place so ordering matches `merged()`.
    for fam_name, fam in raw_catalog["families"].items():
        for raw_v in fam["variants"]:
            vid = raw_v["id"]
            if vid in overlay:
                _emit(fam_name, fam.get("description", ""), overlay[vid], "overlay")
            else:
                _emit(fam_name, fam.get("description", ""), base[vid], "base")

    # Overlay-only variants, appended in overlay order.
    for fam_name, fam in raw_overlay["families"].items():
        for raw_v in fam["variants"]:
            vid = raw_v["id"]
            if vid not in base:
                _emit(fam_name, fam.get("description", ""), overlay[vid], "overlay")

    out = {
        "version": 2,
        "_note": (
            "The single source of truth for nessie_tests. Adopted from "
            "chat_nextseek/e2e/catalog.json, which is NOT edited and still serves its "
            "own ten readers. Retirement is a `status` flip, not a deletion."
        ),
        "provenance": {
            "adopted_from": "chat_nextseek/e2e/catalog.json",
            "catalog_sha256": corpus.sha256_of(catalog_path),
            "adopted_on": "2026-08-04",
        },
        "families": families,
    }
    for block in POLICY_BLOCKS:
        out[block] = raw_overlay.get(block, {} if block != "consistency_groups" else [])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nessie_tests.scripts.build_corpus")
    ap.add_argument("--catalog", type=pathlib.Path, default=corpus._BASE_CATALOG)
    ap.add_argument("--overlay", type=pathlib.Path, default=ROOT / "overlay.json")
    ap.add_argument("--retired", type=pathlib.Path, default=ROOT / "retired.json")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "corpus.json")
    a = ap.parse_args(argv)
    payload = build(a.catalog, a.overlay, a.retired)
    a.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(f["variants"]) for f in payload["families"].values())
    print(f"{a.out}: {len(payload['families'])} families, {n} variants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
