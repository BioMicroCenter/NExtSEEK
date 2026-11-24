"""Production's definitions onto dev: the plan (pure) and its writes (through the paths that already exist).

``plan(prod, dev, hold)`` compares two ``dump.bundle`` results joined by code or title and returns the actions, plus
every difference it leaves on purpose (``expected``), so a second run after ``apply`` has no actions and its report
is the proof. Rules:
- Production's hand text and catalog rows are copied. Rows only dev has are kept and listed, never deleted.
- Ids are never copied (each box has its own). Derived context columns (clade, the possible fields, the four
  connection lists) are left to graph_sync's context step; SEEK's own columns (template index, accessor name,
  uploaded-template flag) and PID/ISA tag (the attribute API does not write them) are left as they are.
- A cell on the hold list keeps dev's value (newer work on dev, ruled item by item).
- A missing SEEK sample type is created through ``POST /nextseek_api/sample_types/`` as the operator, never by a
  table write (SEEK keeps a permission lookup per type): the plan prints the payload; its other attributes and its
  context row wait for the next run.
"""
from __future__ import annotations

import copy
import json
import re

ATTR_PATCHABLE = ("description", "required", "attribute_type", "is_title", "unit", "vocab", "linked_sample_type")
# The attribute API's field for each bundle column (the rest are spelled alike); required and is_title go as booleans.
API_FIELD = {"attribute_type": "sample_attribute_type", "vocab": "sample_controlled_vocab"}
# Changes the attribute API always refuses (planner.enforce_uid_protection): UID stays required and the title.
UID_LOCKED = ("required", "is_title")
ATTR_REFS = {"vocab": "seek.sample_controlled_vocabs", "unit": "seek.units", "linked_sample_type": "seek.sample_types"}
ST_HAND = ("name", "description", "Tags", "required_metadata", "standard_metadata", "repository_attributes",
           "sampletype_file_link")
ST_DERIVED = ("clade", "possible_metadata_fields", "parent_sampletypes", "child_sampletypes",
              "associated_assay_parents", "associated_assay_children")
ASSAY_HAND = ("Description", "Tags", "Alternative_Assay_Names", "Required_Parent_Sample_Types",
              "Optional_Parent_Sample_Types", "Children_Sample_Types", "Parent_Clade_Type", "Child_Clade_Type",
              "AssaySheet_Link", "AssociatedRepository", "Critical_Attributes", "Protocols_Phrases", "Protocols_UIDs")
KEYS = {"seek.sample_types": ("title",), "seek.sample_attributes": ("sample_type", "title"),
        "seek.sample_attribute_types": ("title",), "seek.sample_controlled_vocabs": ("title",),
        "seek.sample_controlled_vocab_terms": ("vocab", "label"), "seek.units": ("title", "symbol"),
        "dmac.sample_types_context": ("sample_type",), "dmac.assay_context": ("assay_name",),
        "dmac.internal_assays": ("internal_assay_title",), "dmac.clades": ("title",),
        "dmac.sample_types_clades": ("sample_type",), "dmac.sample_attributes_unique": ("sample_type", "field_name")}


def norm(v):
    """Equal for the sync: NULL and '' and surrounding spaces, 0/1 and booleans, JSON text by content."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "{[":
            try:
                return json.dumps(json.loads(s), sort_keys=True)
            except ValueError:
                pass
        return s or None
    return v


def fold(s) -> str:
    """A title as MySQL's _ci collation sees it: case and runs of spaces ignored."""
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def _rows(b, table):
    return b["tables"].get(table, [])


def _index(b, table):
    return {tuple(r.get(k) for k in KEYS[table]): r for r in _rows(b, table)}


def _held(hold):
    return {(h["table"], tuple(h["key"]), h.get("column", "*")) for h in hold or []}


def target_order(prod: list[str], dev: list[str], permute: bool = True) -> list[str]:
    """Dev's titles with the shared ones in production's order (each in a slot a shared title held, so dev-only
    titles keep theirs), then each production-only title right after its production predecessor. ``permute=False``
    keeps dev's order and only places the new titles."""
    shared = set(prod) & set(dev)
    it = iter([t for t in prod if t in shared])
    out = [next(it) if t in shared else t for t in dev] if permute else list(dev)
    for i, t in enumerate(prod):
        if t in out:
            continue
        prev = next((p for p in reversed(prod[:i]) if p in out), None)
        out.insert(out.index(prev) + 1 if prev is not None else 0, t)
    return out


def _ordered(attrs):
    """Titles in the attribute API's order: a positive position first, NULL or non-positive last, ties by row id
    (``_id``, this box's own)."""
    def key(a):
        pos = a.get("pos")
        good = isinstance(pos, int) and pos > 0
        return (not good, pos if good else 0, a.get("_id") or 0, a["title"])
    return [a["title"] for a in sorted(attrs, key=key)]


def _text(v):
    """Text as the box should hold it: stripped, blank as NULL (never ``norm``'s canonical JSON)."""
    return (v.strip() or None) if isinstance(v, str) else v


def _api_value(col, v):
    return bool(v) if col in ("required", "is_title") else _text(v) if col == "description" else v


def _list(v) -> list[str]:
    """A comma list as ``context_catalog.parse_list`` reads it."""
    return [x.strip() for x in str(v).split(",") if x.strip()] if v else []


def _numeric(title) -> bool:
    """The attribute API reads a title of only digits as a row id, so such a title cannot be named."""
    return isinstance(title, str) and title.strip().isdigit()


def duplicates(b) -> dict:
    """Keys more than one row of a table share on this box: the sync acts on none of them."""
    out = {}
    for table, key in KEYS.items():
        seen, dup = set(), set()
        for r in _rows(b, table):
            k = tuple(r.get(c) for c in key)
            (dup if k in seen else seen).add(k)
        if dup:
            out[table] = sorted((list(k) for k in dup), key=str)
    return out


TIERS = ("required_metadata", "standard_metadata")


def plan(prod: dict, dev: dict, hold: list[dict] | None = None) -> dict:
    held, used = _held(hold), set()

    def is_held(table, key, column="*"):
        for cand in ((table, tuple(key), "*"), (table, tuple(key), column)):
            if cand in held:
                used.add(cand)
                return True
        return False

    def hold_it(table, key, column="*"):
        out["held"].append({"table": table, "key": list(key), "column": column})

    out = {"seek_types": [], "attributes": {}, "internal_assays": [], "clades": [],
           "meanings": [], "context_sample_types": {"create": [], "update": {}},
           "context_assays": {"create": [], "update": {}},
           "waiting": {"attributes": {}, "context_sample_types": [], "clades": []}, "blocked": [], "held": [],
           "expected": {"dev_only": {}, "prod_only": {}, "derived_cells": [], "case_only": [], "seek_own": {},
                        "api_refuses": [], "duplicate_keys": {"prod": duplicates(prod), "dev": duplicates(dev)},
                        "seek_type_text": [], "tier_titles_missing": [], "unused_holds": []}}
    exp = out["expected"]
    for table in KEYS:
        p, d = _index(prod, table), _index(dev, table)
        only = sorted((list(k) for k in d.keys() - p.keys()), key=str)
        if only:
            exp["dev_only"][table] = only

    # --- SEEK sample types -----------------------------------------------------------------------------------
    pt, dt = _index(prod, "seek.sample_types"), _index(dev, "seek.sample_types")
    p_attrs, d_attrs = {}, {}
    for a in _rows(prod, "seek.sample_attributes"):
        p_attrs.setdefault(a["sample_type"], []).append(a)
    for a in _rows(dev, "seek.sample_attributes"):
        d_attrs.setdefault(a["sample_type"], []).append(a)
    dev_titles = {k[0] for k in dt}
    dev_fold = {}
    for t in dev_titles:
        dev_fold.setdefault(fold(t), []).append(t)
    # A type the attribute API cannot name on dev: two dev titles alike under _ci, or a title of only digits.
    unnamable = {t for ts in dev_fold.values() if len(ts) > 1 for t in ts} | {t for t in dev_titles if _numeric(t)}
    unnamable |= {k[0] for k in exp["duplicate_keys"]["dev"].get("seek.sample_types", [])}
    for (title,), row in sorted(pt.items()):
        if is_held("seek.sample_types", [title]):
            hold_it("seek.sample_types", [title])
            continue
        if (title,) not in dt:
            if fold(title) in dev_fold:
                exp["case_only"].append(["seek.sample_types", title, dev_fold[fold(title)][0]])
                continue
            ta = next((a for a in p_attrs.get(title, []) if a.get("is_title")), None)
            if ta is None or _numeric(title):
                out["blocked"].append({"table": "seek.sample_types", "key": [title], "reason":
                                       "no title attribute on production" if ta is None else "a title of only digits"})
                continue
            out["seek_types"].append({"title": title, "description": _text(row.get("description")),
                                      "title_attribute": {
                                          "title": ta["title"], "sample_attribute_type": ta["attribute_type"],
                                          "required": bool(ta["required"]), "description": _text(ta.get("description"))}})
            rest = len(p_attrs.get(title, [])) - 1
            if rest:
                out["waiting"]["attributes"][title] = rest
            continue
        if norm(row.get("description")) != norm(dt[(title,)].get("description")):
            # SEEK's own text: changed through SEEK (PATCH /nextseek_api/sample_types/<id>/), not by this command.
            exp["seek_type_text"].append({"title": title, "description": _text(row.get("description"))})
        for col in ("uploaded_template",):
            if norm(row.get(col)) != norm(dt[(title,)].get(col)):
                exp["seek_own"][col] = exp["seek_own"].get(col, 0) + 1

    # --- attributes --------------------------------------------------------------------------------------------
    present = {t: {k[0] for k in _index(dev, t)} for t in ATTR_REFS.values()}   # units: by title
    present["seek.sample_attribute_types"] = {k[0] for k in _index(dev, "seek.sample_attribute_types")}
    dup_types = {k[0] for side in ("prod", "dev") for k in exp["duplicate_keys"][side].get("seek.sample_attributes", [])}
    what = {"vocab": "vocabulary", "unit": "unit", "linked_sample_type": "sample type", "attribute_type": "attribute type"}

    def lacks(a, cols):
        out_ = [(c, a[c]) for c in cols if c in ATTR_REFS and a.get(c) and a[c] not in present[ATTR_REFS[c]]]
        if "attribute_type" in cols and a.get("attribute_type") not in present["seek.sample_attribute_types"]:
            out_.append(("attribute_type", a.get("attribute_type")))
        return "; ".join(f"needs the {what[c]} {v!r}, which dev lacks" for c, v in out_)

    will_exist = {}   # type -> the attribute titles it has once this run's creates land
    for t in sorted(set(p_attrs) & dev_titles):
        if is_held("seek.sample_types", [t]):
            continue
        if t in dup_types or t in unnamable:
            out["blocked"].append({"table": "seek.sample_attributes", "key": [t], "reason":
                                   "two attributes of this type share a title" if t in dup_types else
                                   "the attribute API cannot name this type on dev (a title of only digits, or "
                                   "another type alike but for case or spacing)"})
            continue
        pa = {a["title"]: a for a in p_attrs[t]}
        da = {a["title"]: a for a in d_attrs.get(t, [])}
        da_fold = {fold(x): x for x in da}
        patches, creates = [], []
        for title in sorted(set(pa) & set(da)):
            changes = {}
            for col in ATTR_PATCHABLE:
                if norm(pa[title].get(col)) == norm(da[title].get(col)):
                    continue
                if is_held("seek.sample_attributes", [t, title], col):
                    hold_it("seek.sample_attributes", [t, title], col)
                    continue
                if title == "UID" and col in UID_LOCKED:
                    exp["api_refuses"].append([t, title, col])
                    continue
                reason = lacks(pa[title], (col,))
                if reason or _numeric(title):
                    out["blocked"].append({"table": "seek.sample_attributes", "key": [t, title, col],
                                           "reason": reason or "a title of only digits"})
                    continue
                changes[API_FIELD.get(col, col)] = _api_value(col, pa[title].get(col))
            for col in ("template_column_index", "original_accessor_name", "pid", "isa_tag", "allow_cv_free_text"):
                if norm(pa[title].get(col)) != norm(da[title].get(col)):
                    exp["seek_own"][col] = exp["seek_own"].get(col, 0) + 1
            if changes:
                patches.append({"attribute": title, "changes": changes})
        dev_order = _ordered(d_attrs.get(t, []))
        creatable = []
        for title in _ordered(p_attrs[t]):
            if title in da:
                continue
            if fold(title) in da_fold:   # the API would refuse it as the same title (_ci)
                exp["case_only"].append(["seek.sample_attributes", f"{t}/{title}", f"{t}/{da_fold[fold(title)]}"])
                continue
            if is_held("seek.sample_attributes", [t, title]):
                hold_it("seek.sample_attributes", [t, title])
                continue
            reason = lacks(pa[title], ("attribute_type",) + tuple(ATTR_REFS)) or ("a title of only digits"
                                                                                   if _numeric(title) else "")
            if reason:
                out["blocked"].append({"table": "seek.sample_attributes", "key": [t, title], "reason": reason})
                continue
            creatable.append(title)
        will_exist[t] = set(da) | set(creatable)
        prod_order = [x for x in _ordered(p_attrs[t]) if x in da or x in creatable]
        order = target_order(prod_order, dev_order)
        existing_order = [x for x in order if x in da]
        reorder = existing_order != dev_order
        if reorder and any(is_held("seek.sample_attributes", [t, x], "pos") for x in da):
            out["held"].append({"table": "seek.sample_attributes", "key": [t], "column": "pos"})
            reorder, order = False, target_order(prod_order, dev_order, permute=False)
        if reorder:   # every attribute gets its place, in order: the API inserts one after another
            by = {p["attribute"]: p for p in patches}
            for i, x in enumerate(existing_order, 1):
                by.setdefault(x, {"attribute": x, "changes": {}})["changes"]["pos"] = i
            patches = [by[x] for x in existing_order]
        for title in creatable:
            a = pa[title]
            creates.append({"title": title, "sample_attribute_type": a["attribute_type"], "required": bool(a["required"]),
                            "is_title": bool(a.get("is_title")), "description": _text(a.get("description")),
                            "unit": a.get("unit"), "sample_controlled_vocab": a.get("vocab"),
                            "linked_sample_type": a.get("linked_sample_type"), "pos": order.index(title) + 1})
        creates.sort(key=lambda c: c["pos"])
        if patches or creates:
            out["attributes"][t] = {"patches": patches, "creates": creates, "order": order if reorder else None}

    # --- vocabularies and terms: no write path; listed --------------------------------------------------------
    for table in ("seek.sample_controlled_vocabs", "seek.sample_controlled_vocab_terms", "seek.units",
                  "seek.sample_attribute_types", "dmac.clades"):
        p, d = _index(prod, table), _index(dev, table)
        only = sorted((list(k) for k in p.keys() - d.keys()), key=str)
        if only:
            exp["prod_only"][table] = only

    # --- internal assays ---------------------------------------------------------------------------------------
    p_ia = [r["internal_assay_title"] for r in _rows(prod, "dmac.internal_assays")]
    d_ia = [r["internal_assay_title"] for r in _rows(dev, "dmac.internal_assays")]
    d_fold = {fold(x): x for x in d_ia}
    for title in sorted(set(p_ia) - set(d_ia)):
        if fold(title) in d_fold:
            exp["case_only"].append(["dmac.internal_assays", title, d_fold[fold(title)]])
        elif is_held("dmac.internal_assays", [title]):
            hold_it("dmac.internal_assays", [title])
        else:
            out["internal_assays"].append(title)
    if exp["case_only"]:
        kept = {c[2] for c in exp["case_only"] if c[0] == "dmac.internal_assays"}
        rest = [k for k in exp["dev_only"].get("dmac.internal_assays", []) if k[0] not in kept]
        exp["dev_only"]["dmac.internal_assays"] = rest
    ia_after = set(d_ia) | set(out["internal_assays"])

    # --- clades of sample types --------------------------------------------------------------------------------
    pc, dc = _index(prod, "dmac.sample_types_clades"), _index(dev, "dmac.sample_types_clades")
    for (t,), row in sorted(pc.items()):
        if row.get("clade") is None or norm(row.get("clade")) == norm((dc.get((t,)) or {}).get("clade")):
            continue
        if is_held("dmac.sample_types_clades", [t]):
            hold_it("dmac.sample_types_clades", [t])
        elif t not in dev_titles:
            out["waiting"]["clades"].append(t)
        else:
            out["clades"].append({"sample_type": t, "clade": row["clade"]})

    # --- attribute meanings ------------------------------------------------------------------------------------
    pm, dm = _index(prod, "dmac.sample_attributes_unique"), _index(dev, "dmac.sample_attributes_unique")
    for key, row in sorted(pm.items(), key=str):
        if key not in dm or norm(row.get("meaning")) != norm(dm[key].get("meaning")):
            if is_held("dmac.sample_attributes_unique", list(key)):
                hold_it("dmac.sample_attributes_unique", list(key))
                continue
            out["meanings"].append({"sample_type": key[0], "field_name": key[1], "meaning": row.get("meaning")})

    # --- context rows ------------------------------------------------------------------------------------------
    def tiers_here(code, row):
        """Production's tier lists without the titles this type will not have on dev (the page refuses those)."""
        out_ = {}
        have = will_exist.get(code)
        if have is not None:   # tier lists are stripped; SEEK keeps some titles with a trailing space
            have = {x.strip() for x in have}
        for c in TIERS:
            vals = _list(row.get(c))
            gone = [x for x in vals if have is not None and x not in have]
            if gone:
                exp["tier_titles_missing"].append([code, c, gone])
            out_[c] = [x for x in vals if x not in gone]
        return out_

    def context(table, key_col, hand, derived, bucket, exists):
        p, d = _index(prod, table), _index(dev, table)
        dup = {k[0] for side in ("prod", "dev") for k in exp["duplicate_keys"][side].get(table, [])}
        d_fold = {fold(k[0]): k[0] for k in d}
        for (code,), row in sorted(p.items()):
            if code in dup:
                out["blocked"].append({"table": table, "key": [code], "reason": "more than one row has this key"})
                continue
            tiers = tiers_here(code, row) if table == "dmac.sample_types_context" else {}
            if (code,) not in d:
                if fold(code) in d_fold:   # the Definitions page refuses a near-duplicate row
                    exp["case_only"].append([table, code, d_fold[fold(code)]])
                    continue
                if is_held(table, [code]):
                    hold_it(table, [code])
                    continue
                if not exists(code):
                    out["waiting"][bucket].append(code)
                    continue
                cells = {key_col: code, **{c: (", ".join(tiers[c]) if c in tiers else row[c]) for c in hand
                                           if norm(row.get(c)) is not None}}
                out[bucket]["create"].append(cells)
                continue
            cells = {}
            for c in hand:
                if c in tiers:
                    if tiers[c] == _list(d[(code,)].get(c)):
                        continue
                elif norm(row.get(c)) == norm(d[(code,)].get(c)):
                    continue
                if is_held(table, [code], c):
                    hold_it(table, [code], c)
                    continue
                # A NULL on production clears dev's cell: the page functions store a blank as NULL.
                cells[c] = ", ".join(tiers[c]) if c in tiers else (row.get(c) if row.get(c) is not None else "")
            for c in derived:
                if norm(row.get(c)) != norm(d[(code,)].get(c)):
                    exp["derived_cells"].append([table, code, c])
            if cells:
                out[bucket]["update"][code] = cells

    out["waiting"]["context_assays"] = []
    context("dmac.sample_types_context", "sample_type", ST_HAND, ST_DERIVED, "context_sample_types",
            lambda code: code in dev_titles)
    context("dmac.assay_context", "assay_name", ASSAY_HAND, (), "context_assays",
            lambda name: name in ia_after)
    exp["unused_holds"] = [h for h in hold or [] if (h["table"], tuple(h["key"]), h.get("column", "*")) not in used]
    out["counts"] = counts(out)
    return out


def counts(p: dict) -> dict:
    a = p["attributes"].values()
    return {"seek_types": len(p["seek_types"]),
            "attribute_creates": sum(len(x["creates"]) for x in a),
            "attribute_patches": sum(len(x["patches"]) for x in a),
            "attribute_types_touched": len(p["attributes"]), "internal_assays": len(p["internal_assays"]),
            "clades": len(p["clades"]), "meanings": len(p["meanings"]),
            "context_sample_types_create": len(p["context_sample_types"]["create"]),
            "context_sample_types_update": len(p["context_sample_types"]["update"]),
            "context_assays_create": len(p["context_assays"]["create"]),
            "context_assays_update": len(p["context_assays"]["update"])}


def action_count(p: dict) -> int:
    return sum(counts(p).values())


def simulate(p: dict, dev: dict) -> dict:
    """``dev`` as it reads after ``p`` has landed, in memory: the idempotence check's other half."""
    b = copy.deepcopy(dev)
    T = b["tables"]
    for t, work in p["attributes"].items():
        rows = {a["title"]: a for a in T["seek.sample_attributes"] if a["sample_type"] == t}
        working = _ordered(rows.values())
        back = {v: k for k, v in API_FIELD.items()}
        for patch in work["patches"]:      # as the attribute API applies them: one after another
            ch = {back.get(k, k): (int(v) if k in ("required", "is_title") else v) for k, v in patch["changes"].items()}
            if "pos" in ch:
                working.remove(patch["attribute"])
                working.insert(ch.pop("pos") - 1, patch["attribute"])
            rows[patch["attribute"]].update(ch)
        for c in work["creates"]:
            rows[c["title"]] = {"sample_type": t, "title": c["title"], "attribute_type": c["sample_attribute_type"],
                                "required": int(c["required"]), "is_title": int(c["is_title"]),
                                "description": c["description"], "unit": c["unit"],
                                "vocab": c["sample_controlled_vocab"], "linked_sample_type": c["linked_sample_type"]}
            T["seek.sample_attributes"].append(rows[c["title"]])
            working.insert(c["pos"] - 1, c["title"])
        for i, x in enumerate(working, 1):
            rows[x]["pos"] = i
    T["dmac.internal_assays"] += [{"internal_assay_title": x} for x in p["internal_assays"]]
    clades = {r["sample_type"]: r for r in T["dmac.sample_types_clades"]}
    for c in p["clades"]:
        clades.setdefault(c["sample_type"], T["dmac.sample_types_clades"].append(c) or c)["clade"] = c["clade"]
    m = {(r["sample_type"], r["field_name"]): r for r in T["dmac.sample_attributes_unique"]}
    for x in p["meanings"]:
        m.setdefault((x["sample_type"], x["field_name"]), T["dmac.sample_attributes_unique"].append(x) or x)["meaning"] = x["meaning"]
    for table, bucket, key in (("dmac.sample_types_context", "context_sample_types", "sample_type"),
                               ("dmac.assay_context", "context_assays", "assay_name")):
        rows = {r[key]: r for r in T[table]}
        T[table] += [dict(c) for c in p[bucket]["create"]]
        for code, cells in p[bucket]["update"].items():
            rows[code].update({c: (v or None) for c, v in cells.items()})
    return b
