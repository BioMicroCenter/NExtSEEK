---
name: paper-curation
description: >
  Curate one published paper into NExtSEEK sample rows for a background paper-reingest run. Use only when the
  prompt says this is a paper reingest curation run with inputs in /data/input. Never use it in a chat turn.
---

# Paper curation

You turn one published paper into a sample tree and rows, citing evidence for every value. Nobody answers
questions during this run: decide by the rules, record each decision, and report what you could not decide as a
gap. A curator reviews everything once at the end, so being explicit beats being complete.

## Inputs (read-only) and output

- `/data/input/paper.json`: the paper's identifiers, title, authors and full text, as sections of paragraphs.
- `/data/input/evidence.json`: every quotable item with a stable ID (`E:methods:p3`, `E:cap:fig2`,
  `E:si:<file>!<cell>`, `E:geo:GSM123:characteristics.age`, `E:file:<repo>:<id>`), the deposits, the
  `skeleton` rows (one per deposited data record) and the gaps acquisition found.
- `/data/input/context.json`: the target project (inferred, maybe none), the live sample types with their
  attributes and allowed parents, the house assays, precedent studies, existing records that match this paper,
  the lab code.
- `/data/input/rulebook.json`: the rules. Cite a rule by its id.
- `/data/input/previous_curation.json` and `/data/input/findings.json`: only on a second attempt; fix what the
  findings name and keep the rest.
- Write exactly one file: `/data/scratch/curation.json`.

You have no internet and no NExtSEEK access. Your only tools are reading files and these two:

- `nextseek-evidence-search --query "<words>"` lists the items whose text holds every word; `--id <ID>` prints one item in full.
- `nextseek-curation-check` runs the server's own checks on `/data/scratch/curation.json` and prints `{errors, warnings, findings}`.

## Method, in order

1. Read the Methods, the Data Availability statement, the figure captions, the supplement items and the
   deposits before you build anything (R-DEPOSIT-FIRST). List the experiments the paper reports.
2. Choose sample types by technology, from `context.json` `sample_types` only. Follow a precedent study only
   when it used the same platform (R-TYPE-BY-TECHNOLOGY). Never invent a type or an attribute (R-NO-NEW-ATTRIBUTES).
3. Build the tree over the skeleton rows. Each data node lists the skeleton rows it covers. Upstream tiers
   (MUS, TIS, CEL, PAT ...) are nodes too: animals give MUS, then TIS, then CEL, never MUS straight to CEL
   (R-TIER-MUS-TIS-CEL). Every parent link names an assay from `house_assays`, and the child's type must list
   the parent's type in its `parents`. Reagents (AB, CHM, ABP) are attributes, not nodes
   (R-REAGENTS-ARE-ATTRIBUTES). An instrument technique is the assay on an edge (R-TECHNIQUE-ON-EDGE). An
   analysis node needs a deposited analysis file (R-ANALYSIS-NEEDS-FILE).
4. Rows: every skeleton row is either a row (its `row_key` is the skeleton's) or in `excluded` with a reason and,
   where one applies, a rule. Rows for upstream nodes use keys `node:<node_id>:<n>`, one per animal, tissue or
   culture the paper distinguishes.
5. Reuse before minting (R-REUSE-BEFORE-MINT): when `context.json` `existing` shows a record for a row, set
   `reuse_uid` and cite the evidence of the match in `reuse_evidence`.
6. Fill attributes, values verbatim (R-VERBATIM). Each filled cell is
   `{"value": "...", "evidence": ["<ID>", ...]}` and the value's words must appear in the cited text. A value you
   infer instead of quote ("Mus musculus" from "C57BL/6J mice") carries `"decision": "D-<n>"`, and that decision's
   `choice` states the value and the reasoning, citing the items in `refs`. A cell with no source is
   `{"blank": "not_in_sources"}`; sources that disagree give `{"blank": "conflict"}` plus a decision naming
   the winner by R-PRECEDENCE. Never a placeholder (R-NO-PLACEHOLDER), never a guess (R-NO-ASSUME).
7. DOI and PMID go only on rows this paper produced, never on a reused row, and only the paper's own
   (R-DOI-OWN-ROWS). They need no evidence.
8. Protocols: for each edge assay with Methods text, one protocol whose `paragraphs` are the `E:methods:p<n>`
   IDs it is copied from, verbatim (R-PROTOCOL-VERBATIM). No Methods text, no protocol: record a gap instead.
9. Decisions: every choice a curator could question gets `{id, topic, choice, basis: rule|precedent|evidence, refs}`.
   A precedent cites its study id. Gaps and errata are notes `{topic, detail, refs}`. Never a question for the PI
   (R-NO-PI-QUESTIONS).
10. Run `nextseek-curation-check`. Fix every error it lists; read the warnings. Repeat until it reports no
    errors, at most five times. Then write the file a last time and reply with one line: `curation.json written`.

## The output shape

```json
{
  "tree": [{"node_id": "mus", "sample_type": "MUS", "label": "C57BL/6J mice", "evidence": ["E:methods:p1"]},
           {"node_id": "seq", "sample_type": "D.SEQ", "label": "liver RNA-seq", "skeleton_rows": ["geo:GSM1"],
            "parents": [{"node_id": "liver", "assay": "<a house assay>"}]}],
  "rows": [{"row_key": "node:mus:1", "node_id": "mus",
            "values": {"Sex": {"value": "female", "evidence": ["E:methods:p1"]},
                       "Age": {"blank": "not_in_sources"}}}],
  "excluded": [{"row_key": "geo:GSM9", "reason": "input control, no sample of its own", "rule": null}],
  "protocols": [{"assay": "<a house assay>", "title": "RNA extraction", "paragraphs": ["E:methods:p4"]}],
  "decisions": [{"id": "D-1", "topic": "organism", "choice": "Organism is Mus musculus: all mice are C57BL/6J.",
                 "basis": "evidence", "refs": ["E:methods:p1"]}],
  "gaps": [{"topic": "protocol", "detail": "no Methods text for the imaging assay", "refs": []}],
  "errata": []
}
```

<!-- BEGIN PLAN005-GEN:skill-ops -->
<!-- END PLAN005-GEN:skill-ops -->
