"""Build the laya training view (SPEC s8). Code only; the output holds question text, so it is written OUTSIDE
every git repo (the script refuses an output path inside this repo).

Inputs (all read-only, none checked in):
  --turns PATH      a run's turns.json (list of {id, session, created, q, reply, route, src, status, attempted_route});
                    repeat the flag. Only runs whose box ran today's router prompt belong here (SPEC s8 Teacher).
  --corpus PATH     corpus.json at origin/dev: case-level route assertions are the truth.
  --caps PATH       route_capabilities.json: its example queries are the prompt-seen slice.
  --manifest PATH   held-out manifest (freeze_heldout.py's jsonl, one row with "hash" per line): its hashes are
                    removed, and so is every other row of a held-out family or entity (synthetic rows stay).
                    /dev/null gives the full pool that draft_heldout.py drafts from.
  --extra PATH      jsonl of {query, route, history?} written by the training agent (unrelated and counter-cases).
  --evidence PATH   optional route_example_evidence.json: single-engine paired evidence, truth only where no
                    assertion exists.
  --out PATH        training view jsonl.

Every row's history goes through router_context.build_history and laya_common.condense, the same two functions
a live turn uses (SPEC s4); the model-side text is therefore rebuilt, never stored.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from NessieAI.router.laya_common import condense, norm_text_hash  # noqa: E402
from NessieAI.router.router_context import build_history  # noqa: E402
import split  # noqa: E402  (U4's module; tests stub its functions)

ROUTES = ("nextseek_query", "container_cc", "unrelated")
TEACHER_FIELD = {"baml": "route", "pipeline": "route", "cc_unavailable": "route",
                 "followup": "attempted_route", "sticky": "attempted_route"}
# posterior, forced, heuristic, none, laya are not the LLM router's own answer (SPEC s8)
_ENT = [("NHP", r"\bnhp|macaca|monkey|primate|rhesus|cynomolgus"), ("MUS", r"\bmice\b|\bmouse\b|murine|\bmus\b"),
        ("HUMAN", r"\bhuman|patient|donor|\bhomo sapiens"), ("TCGA", r"tcga|luad|cancer genome"),
        ("SRP", r"\bsrp\b|superfund"), ("IMPACT", r"impact"), ("KAMM", r"kamm"), ("GRIFFITH", r"griffith"),
        ("METNET", r"metnet"), ("DOI_PMID", r"doi\b|pmid|10\.\d{4}/|pubmed|paper|publication"),
        ("PIPELINE", r"nf-core|nextflow|rnaseq|pipeline|alignment|fetchngs|scrnaseq"),
        ("SUBMISSION", r"\bgeo\b|\bsra\b|pride|submission"), ("FLOW_CYTEK", r"cytek|flow cytom|\bfacs\b|\bd\.flow"),
        ("SEQ", r"novaseq|sequenc|rna-?seq|d\.seq|chip-?seq"), ("IMAGING", r"imaging|image|microscop"),
        ("SOP_PROTOCOL", r"\bsop|protocol"),
        ("HOWTO_DOCS", r"how (do|can|to) i|how do you|upload|download|validate|spreadsheet|workbook"),
        ("WRITE", r"\bcreate\b|\bdelete\b|\bupdate\b|\bregister\b|\bedit\b"),
        ("SAMPLETYPE_CATALOG", r"sample type|assay|attribute|vocabular|investigation|project|study")]


def entity_of(query: str) -> str:
    q = query.lower()
    return next((n for n, p in _ENT if re.search(p, q)), "none")


def teacher_of(turn: dict) -> str | None:
    field = TEACHER_FIELD.get(turn.get("src"))
    route = turn.get(field) if field else None
    return route if route in ROUTES else None


def corpus_truth(corpus: dict) -> dict[str, dict]:
    """norm hash -> {route|None, either, family} from CASE-LEVEL route criteria of active turns only."""
    out = {}
    for fam, body in corpus["families"].items():
        for v in body["variants"]:
            if v.get("status", "active") != "active":
                continue
            for t in v["turns"]:
                for c in t.get("pass_criteria", []):
                    if c.get("field") != "route":
                        continue
                    val = str(c.get("value"))
                    if c["op"] == "eq" and val in ROUTES:
                        out[norm_text_hash(t["query"])] = {"route": val, "either": False, "family": v.get("family", fam)}
                    elif c["op"] == "matches_re" and "nextseek_query" in val and "container_cc" in val \
                            and "unrelated" not in val:
                        out[norm_text_hash(t["query"])] = {"route": None, "either": True, "family": v.get("family", fam)}
    return out


def corpus_family(corpus: dict) -> dict[str, str]:
    return {norm_text_hash(t["query"]): v.get("family", fam)
            for fam, b in corpus["families"].items() for v in b["variants"] for t in v["turns"]}


def evidence_truth(evidence: dict) -> dict[str, str]:
    out = {}
    for r in evidence.get("records", []):
        ns, cc = r["ns"]["success"], r["cc"]["success"]
        if ns != cc:
            out[norm_text_hash(r["query_text"])] = "nextseek_query" if ns else "container_cc"
    return out


def example_queries(caps: dict) -> dict[str, str]:
    """norm hash -> task family of every example query (the prompt-seen slice)."""
    return {norm_text_hash(q): tf["name"] for r in caps["routes"] for tf in r.get("task_families", [])
            for q in tf.get("example_queries", [])}


def _history(prior: list[dict]) -> list[dict]:
    """chat_log entries of the earlier turns of one session, the way a live turn stores them."""
    return [{"turn_id": i, "user_query": t["q"], "assistant_reply": t.get("reply"), "router_choice": t.get("route"),
             "status": "error" if t.get("status") == "error" else "completed"} for i, t in enumerate(prior, 1)]


def _view(hist) -> list[dict]:
    """The chat as BAML sees it, replies included, for the label page (SPEC s8); condense ignores the reply."""
    return [{"user_message": h.user_message, "assistant_reply": h.assistant_reply, "router_choice": h.router_choice,
             "status": h.status} for h in hist]


def build_rows(turns: list[dict], corpus: dict, caps: dict, manifest: set[str], extra: list[dict] = (),
               evidence: dict | None = None) -> list[dict]:
    truth, fam_of = corpus_truth(corpus), corpus_family(corpus)
    ev = evidence_truth(evidence or {})
    seen = example_queries(caps)
    sessions = collections.defaultdict(list)
    for t in turns:
        sessions[t.get("session")].append(t)
    # one candidate per (question, condensed state): its teacher votes across runs
    cands: dict[tuple, dict] = {}
    for sid, lst in sessions.items():
        lst.sort(key=lambda t: (t.get("created") or "", str(t.get("id") or "")))
        for i, t in enumerate(lst):
            hist = build_history(_history(lst[:i]))
            c = cands.setdefault((norm_text_hash(t["q"]), condense(t["q"], hist)),
                                 {"chat_id": str(sid), "query": t["q"], "history": _view(hist), "votes": collections.Counter()})
            r = teacher_of(t)
            if r:
                c["votes"][r] += 1
    for k, e in enumerate(extra):
        hist = build_history(_history(e.get("history") or []))
        cands.setdefault((norm_text_hash(e["query"]), condense(e["query"], hist), "extra"),
                         {"chat_id": f"extra-{k}", "query": e["query"], "history": _view(hist),
                          "votes": collections.Counter({e["route"]: 1}), "synthetic": True})
    rows = []
    for key, c in cands.items():
        h = key[0]
        if h in manifest:
            continue
        tr = truth.get(h)
        fam = (tr or {}).get("family") or fam_of.get(h) or seen.get(h) or ("synthetic" if c.get("synthetic") else "unlabelled")
        ent = entity_of(c["query"])
        if manifest and not c.get("synthetic") and split.heldout_bucket(fam, ent):
            continue  # a training view: a held-out family or entity never trains, rows added after the freeze too
        truth_route = tr["route"] if tr else ev.get(h)
        either = bool(tr and tr["either"])
        votes = c["votes"]
        teacher = truth_route if (tr and tr["route"]) else (votes.most_common(1)[0][0] if votes else None)
        if teacher is None and not either:
            continue                                   # no label of any kind
        if h in seen:
            sl = "prompt_seen"
            if split.heldout_bucket(fam, ent):         # a held-out family's example must not train
                continue
        else:
            sl = "calib" if split.calib_bucket(c["chat_id"]) else "train"
        soft = None
        if len(votes) > 1 and not (tr and tr["route"]):
            n = sum(votes.values())
            soft = {r: v / n for r, v in votes.items()}
        rows.append({"chat_id": c["chat_id"], "query": c["query"], "history": c["history"],
                     "teacher_route": teacher, "soft_teacher": soft, "truth_route": truth_route, "either": either,
                     "family": fam, "entity": ent, "slice": sl})
    return rows


def load_manifest_hashes(path) -> set[str]:
    return split.load_manifest(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--turns", action="append", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--caps", default=str(REPO / "NessieAI/dmac_assistant/build_context/route_capabilities.json"))
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--extra")
    ap.add_argument("--evidence")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = pathlib.Path(a.out).expanduser().resolve()
    if REPO in out.parents:
        sys.exit("refusing to write question text inside the public repo; use the training workspace")
    rd = lambda p: json.loads(pathlib.Path(p).expanduser().read_text(encoding="utf-8"))  # noqa: E731
    turns = [t for p in a.turns for t in rd(p)]
    extra = [json.loads(ln) for ln in pathlib.Path(a.extra).expanduser().read_text().splitlines() if ln.strip()] if a.extra else []
    rows = build_rows(turns, rd(a.corpus), rd(a.caps), load_manifest_hashes(a.manifest), extra,
                      rd(a.evidence) if a.evidence else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} rows:", dict(collections.Counter(r["slice"] for r in rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
