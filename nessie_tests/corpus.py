from __future__ import annotations
import json
import random
import re
from pathlib import Path
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.catalog import load_catalog, Catalog, PassCriterion, Variant  # noqa: E402

_BASE_CATALOG = Path(__file__).resolve().parents[1] / "chat_nextseek" / "e2e" / "catalog.json"


def sha256_of(path) -> str:
    """Hex sha256 of a file's bytes. Used to pin what `corpus.json` was adopted from."""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _flatten(cat: Catalog, source_tag: str) -> list[Variant]:
    out: list[Variant] = []
    for fam in cat.families.values():
        for v in fam.variants:
            if source_tag not in v.tags:
                v.tags = [*v.tags, source_tag]
            out.append(v)
    return out


def load_base() -> list[Variant]:
    """The VENDORED catalog, straight from chat_nextseek.

    Not a corpus source any more -- ``corpus.json`` was adopted from it on
    2026-08-04 and is now the single source of truth. This survives for the
    drift test, which is the only thing that still has a reason to ask what
    upstream currently says.
    """
    return _flatten(load_catalog(_BASE_CATALOG), "base")


def load_overlay(path: Path) -> list[Variant]:
    """Read an overlay-shaped catalog file. NOT a corpus source since 2026-08-04.

    It has exactly ONE caller, `scripts/build_corpus.py`, which is the manual
    adoption tool you run when the vendored catalog moves -- it reads
    `overlay.json` and `retired.json` by design and forever. Deleting this with
    the rest of the three-file machinery would have left that tool raising
    AttributeError with no test to catch it, since nothing imports it any more.

    Nothing in a RUN calls this. `merged()` reads corpus.json.
    """
    return _flatten(load_catalog(path), "overlay")


_UNIFIED = Path(__file__).resolve().parent / "corpus.json"

# The keys that are nessie metadata rather than part of the e2e Variant schema.
# Stripped so the Variant body stays clean -- NOT because `Variant` would reject
# them. `Variant.model_config` is `{}`, so pydantic's default `extra="ignore"`
# applies and an unknown key is dropped in silence. That cuts the wrong way for
# anyone adding a metadata key: forget to list it here and `Variant` swallows it,
# while `variant_meta` (which returns only these keys) never surfaces it either.
# Both halves of the round trip stay quiet. THIS TUPLE IS THE ONE PLACE a new
# metadata key must be registered.
_META_KEYS = ("status", "origin", "is_bayesian", "hibayes_subtype",
              "expected_behavior", "artifact_expected", "artifact_kind", "retirement")


def _read_unified(path=None) -> dict:
    return json.loads(Path(path or _UNIFIED).read_text(encoding="utf-8"))


def _to_variants(payload: dict, *, include_retired: bool) -> list[Variant]:
    out: list[Variant] = []
    for fam_name, fam in payload["families"].items():
        for raw in fam["variants"]:
            if not include_retired and raw.get("status") != "active":
                continue
            body = {k: val for k, val in raw.items() if k not in _META_KEYS}
            # Declared family wins; the nesting key is only a fallback. 3
            # variants diverge in corpus.json (7 across the two source files; the
            # overlay's 4 are base-id overrides emitted under their base block,
            # which happens to match). `corpus.sample` buckets on v.family, so
            # taking the block name here changes every seeded case set.
            body["family"] = raw.get("family") or fam_name
            # `origin` becomes the source tag the rest of the harness already
            # reads off `tags`, so nothing downstream has to learn a new field.
            tag = raw.get("origin", "base")
            body["tags"] = [*body.get("tags", []), tag] if tag not in body.get("tags", []) \
                else list(body.get("tags", []))
            out.append(Variant.model_validate(body))
    return out


def load_unified(path=None) -> list[Variant]:
    """Active variants from the unified corpus, in file order."""
    return _to_variants(_read_unified(path), include_retired=False)


def load_all_definitions(path=None) -> list[Variant]:
    """Active AND retired. For tests that must inspect a retired definition."""
    return _to_variants(_read_unified(path), include_retired=True)


def variant_meta(path=None) -> dict[str, dict]:
    """id -> the nessie metadata block, for every definition including retired."""
    payload = _read_unified(path)
    return {raw["id"]: {k: raw.get(k) for k in _META_KEYS}
            for fam in payload["families"].values() for raw in fam["variants"]}


def merged_from_unified(path=None) -> list[Variant]:
    """`merged()` over the unified corpus. Same pipeline, one source.

    No base-versus-overlay merge step, because there is one definition per id.
    No retirement filter step either: `_to_variants` already excludes them.

    ONE parse. It used to read the ~1.4 MB file twice -- once for the policy
    blocks and once inside `load_unified` -- which was harmless while this was a
    test-only path and is not, now that it is the path every run takes.
    """
    payload = _read_unified(path)
    out = _to_variants(payload, include_retired=False)
    out = apply_criterion_rewrites(out, payload.get("criterion_rewrites", {}))
    out = apply_route_policy(out, payload.get("route_policy", {}))
    return apply_family_floor(out, payload.get("family_floor", {}))


def load_consistency_groups(path=None) -> list[dict]:
    return _read_unified(path).get("consistency_groups", [])


def load_family_floor(path=None) -> dict:
    """The per-family minimum OUTCOME assertion block."""
    return _read_unified(path).get("family_floor", {})


def apply_family_floor(variants: list[Variant], floor_spec: dict) -> list[Variant]:
    """Add each family's minimum outcome assertion to the LAST turn of its variants.

    Of 381 merged variants, 108 assert plan shape only and 194 assert only plumbing
    (``api_ok`` / ``neo4j_ok`` say a request COMPLETED, not that it returned
    anything). 79% of the corpus therefore cannot detect a wrong answer, and
    hand-editing 300 variants is not viable — so the floor is applied structurally.

    Two rules keep it from doing harm:

    - a floor criterion is added only when the last turn does not already assert that
      FIELD, so a deliberate bound (``row_count lte 20000``) is never overwritten and
      never contradicted by an added one
    - a variant tagged ``no_floor`` opts out entirely. That is required wherever ZERO
      is the correct answer — ``advanced.find_me_nhp_samples_from_study`` (GBM does
      not exist), ``graph.what_pbmcs_tissues_in_the_gbm`` (the operator confirmed the
      zero is honest) and ``tree.missing_uid`` (which asserts a 404).

    Only families listed in the spec get a floor; unsupported, writes_unsupported,
    nessie_route, system_question and refine_and_recall are absent by design. Refine
    and recall turns have no ``api_result_meta.row_count`` at all — that field exists
    only on new_search turns — so a row-count floor there would fail every one of them.
    """
    floors = (floor_spec or {}).get("floors") or {}
    skip_tag = (floor_spec or {}).get("exclude_tag", "no_floor")
    if not floors:
        return variants

    for v in variants:
        floor = floors.get(v.family)
        if not floor or skip_tag in v.tags or not v.turns:
            continue
        last = v.turns[-1]
        already = {c.field for c in last.pass_criteria}
        for crit in floor:
            if crit["field"] in already:
                continue
            last.pass_criteria.append(PassCriterion(**crit))
    return variants


def load_criterion_rewrites(path=None) -> dict:
    """Structural corrections applied to every matching criterion in the corpus."""
    return _read_unified(path).get("criterion_rewrites", {})


def apply_criterion_rewrites(variants: list[Variant], spec: dict) -> list[Variant]:
    """Correct `reporter_plan.project eq "<Name>"` across the whole corpus.

    Two verified facts make every one of these 30 criteria false-fail:

    - the reporter UPPERCASES the project it resolved. Task 813 came back "METNET"
      for a plan asserting ``eq "MetNet"``.
    - for a LAB scope it returns ``project: null`` entirely. Task 812 shows
      ``reporter_plan.project: null`` with ``reporter_context.lab_codes: ["KAM"]``,
      so ``eq "Kamm"`` can never hold — Kamm is a lab, not a project.

    So a lab name is rewritten to assert the resolved lab code, and every other name
    becomes a case-insensitive regex. Doing it here rather than as 30 hand-written
    per-variant overrides keeps the correction in one reviewable place.
    """
    labs = {k.upper(): v for k, v in (spec or {}).get("reporter_project_labs", {}).items()}
    if not spec or not spec.get("reporter_project_case_insensitive", False):
        return variants

    for v in variants:
        for t in v.turns:
            for i, c in enumerate(t.pass_criteria):
                if c.field != "reporter_plan.project" or c.op != "eq" or not isinstance(c.value, str):
                    continue
                code = labs.get(c.value.strip().upper())
                if code:
                    t.pass_criteria[i] = PassCriterion(
                        field="reporter_plan.reporter_context.lab_codes",
                        op="contains", value=code)
                else:
                    t.pass_criteria[i] = PassCriterion(
                        field="reporter_plan.project", op="matches_re",
                        value="(?i)" + re.escape(c.value.strip()))
    return variants


def load_route_policy(path=None) -> dict:
    """Which families are routed away from NS, and to where."""
    return _read_unified(path).get("route_policy", {})


def apply_route_policy(variants: list[Variant], spec: dict) -> list[Variant]:
    """Assert the ROUTE, not an NS-internal parser mode, wherever NS is not reached.

    23 variants across ``unsupported`` and ``writes_unsupported`` assert
    ``parser_plan.mode == "unsupported"``. When the router sends a turn to
    container_cc or unrelated, NS never runs and there is no parser plan, so the
    criterion resolves to None and fails unconditionally regardless of whether the
    product did the right thing. Three of the fifteen seed-0 failures were this.

    The policy those 23 encode is stale rather than wrong-in-detail: writes,
    exports and open-ended analysis are Container-CC's job. The corpus already
    blesses that in its route_gate family (route.cc_write_investigation,
    route.cc_open_ended_analysis) and a previous wave hand-converted three variants
    to exactly the shape this produces. This finishes the job in one place instead
    of 23 per-variant overrides.

    Per-variant ``overrides`` exist because the family is not uniform: weather is
    ``unrelated``, and a textbook-chemistry question is defensibly either, so it is
    asserted as an alternation rather than pinning an undecided policy.
    """
    families = (spec or {}).get("families") or {}
    overrides = (spec or {}).get("overrides") or {}
    drop = (spec or {}).get("drop_field")
    also = (spec or {}).get("also_assert") or []
    if not families and not overrides:
        return variants

    def guarantees_ns(rule) -> bool:
        """True when the rule asserts the turn definitely reaches NExtSEEK.

        `drop_field` exists because `parser_plan.mode` resolves to None on a turn
        that never reaches NS, so asserting it fails no matter what the product
        did. That reasoning only holds where NS is NOT guaranteed. Applying the
        drop blanket-fashion once the NS families joined the map would have
        stripped the field from 256 variants where it is real and observable —
        the corpus gaining a route assertion while losing a stronger one.
        """
        op, value = rule.get("op"), str(rule.get("value") or "")
        if op == "eq":
            return value == "nextseek_query"
        if op == "matches_re":
            # An alternation is safe only if every branch is NS.
            branches = [b for b in re.split(r"[|()]", value) if b.strip()]
            return bool(branches) and all(b.strip() == "nextseek_query" for b in branches)
        return False

    for v in variants:
        rule = overrides.get(v.id) or families.get(v.family)
        if not rule or not v.turns:
            continue
        if drop and not guarantees_ns(rule):
            for t in v.turns:
                t.pass_criteria = [c for c in t.pass_criteria if c.field != drop]
        first = v.turns[0]
        present = {c.field for c in first.pass_criteria}
        if "route" not in present:
            first.pass_criteria.append(
                PassCriterion(field="route", op=rule["op"], value=rule["value"]))
        # A route assertion alone cannot tell a working turn from a dead one. In
        # the 2026-07-29 run task 957 hit error_max_budget_usd, replied null, and
        # scored GREEN because `route eq container_cc` was the only criterion.
        # Added per FIELD, so the three hand-converted variants keep their own
        # stricter last_reply regexes instead of gaining a redundant nonempty.
        # ...but NOT on a route_gate case. runner.py drives those at case_tier
        # "route" whatever the run's tier, and http_driver stops polling the moment
        # route_decided arrives, so the turn is abandoned before any reply exists BY
        # DESIGN. Adding `last_reply nonempty` there makes the case unsatisfiable —
        # exactly the defect this block was written to remove. Observed live on
        # 2026-07-31: route.ns_plain_study_membership failed on main:last_reply while
        # asserting the correct route.
        if "route_gate" not in v.tags:
            for crit in also:
                if crit["field"] not in present:
                    first.pass_criteria.append(PassCriterion(**crit))
    return variants


def merged(path=None) -> list[Variant]:
    """The resolved active corpus: 283 variants, 314 turns.

    One source since 2026-08-04. The parameter is kept, and kept positional, so
    every existing `merged(OVERLAY)` call site still works during the cutover; it
    now names corpus.json rather than the overlay.
    """
    return merged_from_unified(path)


def load_case_file(path) -> tuple[list[str], list[Variant]]:
    """Parse a ``--cases`` file into (include_ids, inline variants).

    The file is CATALOG-SHAPED so a ``families`` block can be copy-pasted straight
    out of corpus.json and gets the same PassCriterion validation for free.

        {"include_ids": ["green.mus_ndma"],
         "families": {"manual": {"description": "...", "variants": [ ... ]}}}

    Either key may be omitted, but not both.

    Loaded through ``load_catalog`` rather than through the unified reader on
    purpose: a probe file carries no ``status`` / ``origin`` metadata, and
    running it through the unified reader would drop every variant in it for
    not being marked active.
    """
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    include = list(payload.get("include_ids") or [])
    # The "overlay" tag is what the harness has always stamped on an inline
    # probe variant; kept verbatim so a probe's tag set does not change under it.
    inline = _flatten(load_catalog(path), "overlay") if payload.get("families") else []
    return include, inline


def select_cases(variants: list[Variant], include_ids: list[str],
                 inline: list[Variant]) -> list[Variant]:
    """Resolve a case file to the exact list of variants to run, in FILE order.

    A seeded sample answers "is the corpus still healthy". It cannot answer "does
    THIS work" — reingestion, a harmonization conversation, cross-mode memory —
    because those questions are not in the corpus, and waiting for a seed to draw
    the cases you care about does not work: the 2026-07-28 run silently dropped
    three of the fixes it was meant to verify because seed 0 did not select them.

    File order is preserved rather than corpus order, because a probe file is a
    running order and usually wants a seed followed by its follow-up.

    Inline variants are returned AS WRITTEN — no family floor, no route policy. A
    hand-authored probe is a precise instrument, and bolting extra assertions onto
    questions someone wrote deliberately is the opposite of the point.
    """
    by_id = {v.id: v for v in variants}
    missing = [i for i in include_ids if i not in by_id]
    if missing:
        # Loud, because a typo would silently shrink a run that costs money.
        raise ValueError(
            f"--cases include_ids not found in the corpus: {missing}. "
            f"Check spelling against nessie_tests/corpus.json. A RETIRED id will "
            f"also land here: merged() returns active definitions only.")
    out = [by_id[i] for i in include_ids]
    out += inline
    if not out:
        raise ValueError("--cases file selected no cases: give include_ids, families, or both.")
    return out


def select(variants, *, scope: str = "all", family: str | None = None,
           variant_id: str | None = None) -> list[Variant]:
    out = list(variants)
    if variant_id:
        return [v for v in out if v.id == variant_id]
    if family:
        out = [v for v in out if v.family == family]
    if scope == "specific":
        out = [v for v in out if "route_gate" in v.tags]
    return out


def sample(variants, ratio: float, seed: int = 0) -> list:
    """Deterministically keep a per-family fraction of the variants.

    ratio >= 1.0 returns everything. Otherwise each family keeps
    max(1, round(len(family) * ratio)) variants, chosen with a seeded RNG so
    the same (ratio, seed) always yields the same subset.
    """
    if ratio >= 1.0:
        return list(variants)
    rng = random.Random(seed)
    by_family: dict[str, list] = {}
    for v in variants:
        by_family.setdefault(v.family, []).append(v)
    out: list = []
    for fam in sorted(by_family):
        vs = by_family[fam]
        k = max(1, round(len(vs) * ratio))
        out.extend(rng.sample(vs, min(k, len(vs))))
    return out
