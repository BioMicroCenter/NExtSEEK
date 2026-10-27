"""Stage 4 checks 1–5 (spec §4.2) on plain dicts, standard library only.

The same file runs on the server (verify.py) and, as a byte-identical copy, inside the curation container
(bin/_paper_curation_check.py, behind `nextseek-curation-check`), where there is no pydantic. Keep it free of
package imports; test_curation_check holds it to the standard library and a parity test holds the copy equal.
Inputs are the JSON forms of curation.json, evidence.json, context.json and rulebook.json.
"""
from __future__ import annotations

import re
import unicodedata

UPSTREAM_PREFIX = "node:"
VALUE_RULE_KINDS = ("default", "precedence")
#: Checked against the paper itself by check 4, never against cited evidence.
PAPER_ID_ATTRIBUTES = ("DOI", "PMID")
REAGENT_TYPES = frozenset({"AB", "CHM", "ABP"})
STRUCTURAL = frozenset({"shape", "node_unknown", "parent_unknown", "cycle"})
_PLACEHOLDER = re.compile(r"^\s*(?:\*{3}.*|tbd|todo|unknown|n/?a|none|null|-+|\?+)\s*$", re.I)
_METHODS_ID = re.compile(r"^E:methods:p\d+$")


def _f(code, detail, *, severity="error", row_key=None, node_id=None, attribute=None, rule=None) -> dict:
    return {"code": code, "severity": severity, "detail": detail, "row_key": row_key, "node_id": node_id,
            "attribute": attribute, "rule": rule}


def squash(text: str) -> str:
    """Letters, digits and dots only, case-folded and NFKC-normalised: light normalisation (spec §4.2 check 1)."""
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum() or ch == ".")


_TOKEN = re.compile(r"[^\W_]+(?:\.[^\W_]+)*")


def _tokens(text: str) -> list[str]:
    """Words, case-folded and NFKC-normalised, a plural reduced to its singular ("weeks" reads as "week")."""
    words = _TOKEN.findall(unicodedata.normalize("NFKC", str(text or "")).casefold())
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]


def _in_text(value: str, texts: list[str]) -> bool:
    """The value's words appear, in order and side by side, in one of the texts: "male" is not in "female"."""
    wanted = _tokens(value)
    if not wanted:
        return False
    n = len(wanted)
    return any(hay[i:i + n] == wanted for hay in map(_tokens, texts) for i in range(len(hay) - n + 1))


# ---- shape ------------------------------------------------------------------------------------------------------

_LIST_KEYS = ("tree", "rows", "excluded", "protocols", "decisions", "gaps", "errata")


def shape_findings(curation: dict) -> list[dict]:
    """The structure curation_models.CurationArtifact demands, enough to run the other checks safely."""
    if not isinstance(curation, dict):
        return [_f("shape", "curation.json is not an object")]
    out = []
    for key in _LIST_KEYS:
        if key in ("tree", "rows") and key not in curation:
            out.append(_f("shape", f"curation.json has no {key!r}"))
        elif key in curation and not isinstance(curation[key], list):
            out.append(_f("shape", f"{key!r} is not a list"))
    if out:
        return out
    for node in curation["tree"]:
        if not isinstance(node, dict) or not all(isinstance(node.get(k), str) for k in ("node_id", "sample_type")):
            out.append(_f("shape", "a tree node lacks node_id or sample_type"))
        elif not all(isinstance(p, dict) and isinstance(p.get("node_id"), str) and isinstance(p.get("assay"), str)
                     for p in node.get("parents") or []):
            out.append(_f("shape", "a parent lacks node_id or assay", node_id=node["node_id"]))
    for row in curation["rows"]:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("row_key", "node_id")):
            out.append(_f("shape", "a row lacks row_key or node_id"))
            continue
        values = row.get("values") or {}
        if not isinstance(values, dict):
            out.append(_f("shape", "values is not an object", row_key=row["row_key"]))
            continue
        for attr, cell in values.items():
            ok = isinstance(cell, dict) and (("value" in cell) != ("blank" in cell))
            if not ok:
                out.append(_f("shape", "a cell is neither a value nor a blank", row_key=row["row_key"], attribute=attr))
    return out


# ---- structure --------------------------------------------------------------------------------------------------

def _structure(cur: dict) -> list[dict]:
    nodes = {n["node_id"]: n for n in cur["tree"]}
    out = []
    for row in cur["rows"]:
        if row["node_id"] not in nodes:
            out.append(_f("node_unknown", f"row's node {row['node_id']!r} is not in the tree", row_key=row["row_key"]))
    for node in cur["tree"]:
        for parent in node.get("parents") or []:
            if parent["node_id"] not in nodes:
                out.append(_f("parent_unknown", f"parent {parent['node_id']!r} is not in the tree",
                              node_id=node["node_id"]))
    state: dict[str, int] = {}

    def visit(nid: str) -> bool:  # True when a cycle is reachable from nid
        if state.get(nid) == 1:
            return True
        if state.get(nid) == 2 or nid not in nodes:
            return False
        state[nid] = 1
        cyclic = any(visit(p["node_id"]) for p in nodes[nid].get("parents") or [])
        state[nid] = 2
        return cyclic

    for nid in nodes:
        if visit(nid):
            out.append(_f("cycle", "the tree's parent links loop", node_id=nid))
            break
    return out


# ---- check 1: values and their evidence -------------------------------------------------------------------------

def _values(cur: dict, items: dict, rules: dict) -> list[dict]:
    decisions = {d["id"]: d for d in cur.get("decisions") or []}
    out = []
    for row in cur["rows"]:
        for attr, cell in (row.get("values") or {}).items():
            if "blank" in cell or attr in PAPER_ID_ATTRIBUTES:
                continue
            where = {"row_key": row["row_key"], "attribute": attr}
            cited = list(cell.get("evidence") or [])
            missing = [e for e in cited if e not in items]
            if missing:
                out.append(_f("evidence_unresolved", f"cites {', '.join(missing[:3])}", **where))
                continue
            value = str(cell.get("value", ""))
            if cell.get("decision"):
                dec = decisions.get(cell["decision"])
                if dec is None:
                    out.append(_f("decision_unknown", f"decision {cell['decision']} is not in decisions", **where))
                    continue
                refs = [r for r in dec.get("refs") or [] if r in items]
                if not _in_text(value, [dec.get("choice", "")] + [items[r] for r in refs]):
                    out.append(_f("value_not_in_sources", "not in its decision's choice or cited text", **where))
                continue
            if cell.get("rule"):
                rule = rules.get(cell["rule"])
                if rule is None:
                    out.append(_f("rule_unknown", f"rule {cell['rule']} is not in the rulebook", **where))
                    continue
                if rule["kind"] not in VALUE_RULE_KINDS:
                    out.append(_f("rule_not_a_value_rule", f"{rule['id']} is a {rule['kind']} rule", **where))
                    continue
            if not _in_text(value, [items[e] for e in cited]):
                out.append(_f("value_not_in_sources", "the value is not in its cited text", **where))
    return out


# ---- check 2: attributes, edges, assays -------------------------------------------------------------------------

def _types(context: dict) -> dict:
    return {t["code"]: t for t in context.get("sample_types") or []}


def _attributes(cur: dict, context: dict) -> list[dict]:
    types, nodes = _types(context), {n["node_id"]: n for n in cur["tree"]}
    out = []
    for row in cur["rows"]:
        node = nodes.get(row["node_id"])
        if node is None:
            continue
        known = {a["title"] for a in (types.get(node["sample_type"]) or {}).get("attributes") or []}
        for attr in row.get("values") or {}:
            if attr not in known:
                out.append(_f("attribute_unknown", f"{node['sample_type']} has no attribute {attr!r}",
                              row_key=row["row_key"], attribute=attr))
    return out


def _edges(cur: dict, context: dict) -> list[dict]:
    types, nodes = _types(context), {n["node_id"]: n for n in cur["tree"]}
    assays = set(context.get("house_assays") or [])
    out = []
    for node in cur["tree"]:
        allowed = set((types.get(node["sample_type"]) or {}).get("parents") or [])
        for parent in node.get("parents") or []:
            up = nodes.get(parent["node_id"])
            if up is not None and up["sample_type"] not in allowed:
                out.append(_f("edge_not_allowed", f"{node['sample_type']} may not derive from {up['sample_type']}",
                              node_id=node["node_id"]))
            if parent["assay"] not in assays:
                out.append(_f("assay_unknown", f"{parent['assay']!r} is not a house assay", node_id=node["node_id"]))
    return out


# ---- check 3: every skeleton row placed -------------------------------------------------------------------------

def _coverage(cur: dict, evidence: dict) -> list[dict]:
    skeleton = {r["row_key"] for r in evidence.get("skeleton") or []}
    placed = {r["row_key"] for r in cur["rows"]} | {e["row_key"] for e in cur.get("excluded") or []}
    out = [_f("skeleton_uncovered", "a deposit row is neither placed nor excluded", row_key=k)
           for k in sorted(skeleton - placed)]
    for row in cur["rows"]:
        if row["row_key"] not in skeleton and not row["row_key"].startswith(UPSTREAM_PREFIX):
            out.append(_f("row_key_unknown", "not a skeleton row and not a node: row", row_key=row["row_key"]))
    return out


# ---- check 4: DOI and PMID --------------------------------------------------------------------------------------

def _doi(cur: dict, evidence: dict) -> list[dict]:
    paper = evidence.get("paper") or {}
    own = {"DOI": squash(paper.get("doi") or ""), "PMID": squash(paper.get("pmid") or "")}
    out = []
    for row in cur["rows"]:
        for attr in ("DOI", "PMID"):
            cell = (row.get("values") or {}).get(attr)
            if not cell or "blank" in cell:
                continue
            if row.get("reuse_uid") or not own[attr] or squash(cell.get("value", "")) != own[attr]:
                out.append(_f("doi_misplaced", f"{attr} on a row this paper did not produce, or not the paper's own",
                              row_key=row["row_key"], attribute=attr))
    return out


def _reuse(cur: dict, items: dict) -> list[dict]:
    return [_f("reuse_without_evidence", "reuse_uid with no resolving reuse_evidence", severity="warn",
               row_key=r["row_key"])
            for r in cur["rows"] if r.get("reuse_uid") and not any(e in items for e in r.get("reuse_evidence") or [])]


# ---- check 5: constraint rules ----------------------------------------------------------------------------------

def _c_values_cite_evidence(ctx):
    return [f for f in _values(ctx["curation"], ctx["items"], ctx["rules"])
            if f["code"] in ("evidence_unresolved", "value_not_in_sources")]


def _c_no_placeholder(ctx):
    return [_f("constraint", "a placeholder in a cell", row_key=r["row_key"], attribute=a)
            for r in ctx["curation"]["rows"] for a, c in (r.get("values") or {}).items()
            if "value" in c and (_PLACEHOLDER.match(str(c["value"])) or "placeholder" in str(c["value"]).casefold())]


def _c_mus_not_parent_of_cel(ctx):
    nodes = {n["node_id"]: n for n in ctx["curation"]["tree"]}
    return [_f("constraint", "CEL directly under MUS", node_id=n["node_id"]) for n in ctx["curation"]["tree"]
            if n["sample_type"] == "CEL"
            and any((nodes.get(p["node_id"]) or {}).get("sample_type") == "MUS" for p in n.get("parents") or [])]


def _c_no_reagent_nodes(ctx):
    return [_f("constraint", f"{n['sample_type']} is a reagent, not a sample", node_id=n["node_id"])
            for n in ctx["curation"]["tree"] if n["sample_type"] in REAGENT_TYPES]


def _c_analysis_needs_file(ctx):
    types, skeleton = _types(ctx["context"]), {r["row_key"]: r for r in ctx["evidence"].get("skeleton") or []}
    nodes = {n["node_id"]: n for n in ctx["curation"]["tree"]}
    out = []
    for row in ctx["curation"]["rows"]:
        node = nodes.get(row["node_id"]) or {}
        if (types.get(node.get("sample_type")) or {}).get("clade") != "Analyzed":
            continue
        sk = skeleton.get(row["row_key"])
        if sk is None or sk.get("kind") not in ("analysis", "file") or not sk.get("files"):
            out.append(_f("constraint", "an analysis row with no deposited analysis file", row_key=row["row_key"]))
    return out


def _c_doi_on_own_rows(ctx):
    return _doi(ctx["curation"], ctx["evidence"])


def _c_attributes_exist(ctx):
    return _attributes(ctx["curation"], ctx["context"])


def _c_protocols_cite_methods(ctx):
    out = []
    for p in ctx["curation"].get("protocols") or []:
        bad = [i for i in p.get("paragraphs") or [] if i not in ctx["items"] or not _METHODS_ID.match(i)]
        if bad or not p.get("paragraphs"):
            out.append(_f("constraint", f"protocol {p.get('title')!r} cites {bad[:3] or 'no paragraph'}"))
    return out


def _c_rows_have_raw_file(ctx):
    skeleton = {r["row_key"]: r for r in ctx["evidence"].get("skeleton") or []}
    return [_f("constraint", "a data row with no deposited raw file", row_key=r["row_key"])
            for r in ctx["curation"]["rows"] if r["row_key"] in skeleton
            and not (skeleton[r["row_key"]].get("kind") == "raw" and skeleton[r["row_key"]].get("files"))]


CONSTRAINT_CHECKS = {
    "values_cite_evidence": _c_values_cite_evidence,
    "no_placeholder": _c_no_placeholder,
    "mus_not_parent_of_cel": _c_mus_not_parent_of_cel,
    "no_reagent_nodes": _c_no_reagent_nodes,
    "analysis_needs_file": _c_analysis_needs_file,
    "doi_on_own_rows": _c_doi_on_own_rows,
    "attributes_exist": _c_attributes_exist,
    "protocols_cite_methods": _c_protocols_cite_methods,
    "rows_have_raw_file": _c_rows_have_raw_file,
}


def _applicable(rule: dict, project_id, sample_types: set, assays: set) -> bool:
    """rulebook.applicable, repeated here because this module may import nothing from the package."""
    kind, _, value = rule["scope"].partition(":")
    if kind == "global":
        return True
    if kind == "project":
        return project_id is not None and int(value) == project_id
    if kind == "sample_type":
        return value in sample_types
    return any(value.casefold() in a.casefold() for a in assays)


def _constraints(ctx: dict, project_id) -> list[dict]:
    cur = ctx["curation"]
    types = {n["sample_type"] for n in cur["tree"]}
    assays = {p["assay"] for n in cur["tree"] for p in n.get("parents") or []}
    out = []
    for rule in ctx["rulebook"].get("rules") or []:
        if not rule.get("check") or not _applicable(rule, project_id, types, assays):
            continue
        for f in CONSTRAINT_CHECKS[rule["check"]](ctx):
            out.append({**f, "code": "constraint", "rule": rule["id"],
                        "detail": f"{rule['id']}: {f['detail']}"})
    return out


def check_all(curation: dict, evidence: dict, context: dict, rulebook: dict, *, project_id) -> list[dict]:
    shape = shape_findings(curation)
    if shape:
        return shape
    items = {i["id"]: i["text"] for i in evidence.get("items") or []}
    rules = {r["id"]: r for r in rulebook.get("rules") or []}
    structural = _structure(curation)
    if structural:
        return structural
    ctx = {"curation": curation, "evidence": evidence, "context": context, "rulebook": rulebook,
           "items": items, "rules": rules}
    return (_values(curation, items, rules) + _attributes(curation, context) + _edges(curation, context)
            + _coverage(curation, evidence) + _doi(curation, evidence) + _reuse(curation, items)
            + _constraints(ctx, project_id))
