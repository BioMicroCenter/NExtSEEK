# nf-core launch path — presentable explainer page

**Date:** 2026-08-07
**Branch:** `dev-v3-merge` (written against `0ac6571`)
**Deliverable:** `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — one
self-contained HTML file, built **outside** this repo and not version-controlled.
This spec is the maintained record; the page is a meeting artifact that lives with
the other material it will be presented alongside.

---

## 1. Purpose

A page that explains how a sentence typed into the NExtSEEK chat box becomes a job
queued on Luria, showing every step of that path, where the system deliberately
stops, where it can quietly be wrong, and what adding a new nf-core pipeline costs
at each step.

It is presented live to a mixed room and also read afterwards on its own. During a
presentation the reader must be able to attach a note to any block on the page and
recover those notes later, so a discussion is not lost when the tab closes.

**Audience:** mixed. Non-engineers (PIs, curators) who care that the genome is right
and the run will not waste cluster time, alongside engineers who could open
`agent_tools.py` themselves. The plain-English spine must stand alone; every
technical detail sits behind a collapsed panel that the engineers open and everyone
else ignores.

---

## 2. Scope

Covered: the full five-tool loop (`resolve_samples` → `write_samplesheet` →
`configure_run` → `submit_to_luria` → `conclude`), with `configure_run` broken open
into its seven checkpoints, plus the data layer beneath it (`seqera/catalog.py` and
the 31 curated files in `reports/templates/nfcore/`).

Organised in **run order**. Problems are marked where they occur, not collected into
a separate gap-analysis section.

---

## 3. Recurring elements

Three elements repeat throughout the page. They must be visually distinct from each
other and used consistently.

### 3.1 Gate mark

The system stops and refuses. Loud, safe, working as designed.
Drawn as a bar across the spine — the path stops here.
Example: no CRISPR guide supplied, so `configure_run` refuses to build and asks.

### 3.2 Warning mark

The system continues and can be quietly wrong.
Drawn as a mark *beside* the spine — the path continues, which is the point.
Example: no species matched, so GRCh38 is assumed and the run proceeds.

### 3.3 "Adding a pipeline here" panel

One per pipeline-stage section (§ 01 through § 05), neutral-toned and set off by a
rule so it reads as an aside rather than a third alarm. § 00 introduces it in the
legend; § 06 is the aggregate of all of them and carries no panel of its own. Always
answers the same two questions in the same order:

1. What must a new pipeline declare at this step?
2. What has actually gone wrong when someone got it wrong?

Warning marks may appear *inside* these panels, so the mark vocabulary stays
consistent across the page.

---

## 4. Page structure

Sticky contents rail on the left; seven `§`-numbered sections in the body, in run
order.

### § 00 · Overview

The claim in one paragraph: a sentence typed into a chat box becomes a job queued on
Luria, and this page shows every place a check or a human intervenes. Carries the
legend for the gate mark, the warning mark, and the extensibility panel.

### § 01 · Work out what to run on — `resolve_samples`

Six sub-steps: take the UIDs or accessions from the conversation → walk the lineage
down to sequencing leaves → filter to the types this pipeline accepts → pull out
archive accessions → stash each leaf's full metadata so FASTQ paths can be found by
value later → vote on species.

- **Gate:** zero leaves resolved triggers an explanation of the sample-type mismatch
  rather than a silent retry of the same UIDs.
- **Warning:** the species vote matches fourteen exact strings, case-insensitive and
  whitespace-trimmed, with no fuzzy matching. `C57BL/6` does not match.
- **Adding a pipeline:** declare which sample types and assay names count as this
  pipeline's input. Nothing else — species detection is fully generic and needs no
  per-pipeline entry. Wrong types means zero leaves and a retry loop; the mismatch
  hint exists to stop that. This step held up well: bamtofastq was the first pipeline
  whose inputs are analysis records rather than raw sequencing data, and it needed no
  code change.

### § 02 · Build the row set — `write_samplesheet`

Merge rows, group into cohorts, rename columns for the pipelines that insist on their
own names, sanitise sample IDs where required, write `samplesheet.csv`.

- **Gate:** it deliberately forgets any configuration built before it — the run
  plan and the paths to `params.yml` / `launch.yml` are dropped from the session,
  so a stale config can never be submitted against fresh rows. (The files
  themselves stay on disk and are overwritten next time; it is the *reference*
  that is dropped, and submitting reads the session, not the disk.)
- **Warning:** the per-pipeline column renames are hardcoded in `emitter.py`, and each
  new pipeline risks adding another.
- **Adding a pipeline:** declare the required columns and whether the input is reads
  or alignments. **This is where the "data only, no code" claim breaks** — a pipeline
  that renames FASTQ columns or constrains sample-name format requires an edit to a
  hardcoded map in `emitter.py`. Real defect, 2026-08-05: ampliseq rejected the sample
  names it was handed, because its naming rule lives in code and had not been added.

### § 03 · Configure the run — `configure_run` (centrepiece)

Seven checkpoints, each its own block with its own sub-steps, branching off a nested
spine.

1. **Known pipeline?** — gate. Catches a hallucinated pipeline name.
2. **Samplesheet exists?** — gate. Sends the agent back to `write_samplesheet`.
3. **Are the supplied human-only values valid?** — gate. Regex and enum checks on
   what was given; the agent is told to ask for a correction, not to fix it itself.
4. **Are any human-only values missing?** — gate. Conditional: one answer can decide
   whether another is even required, so the wizard asks for the gating value first.
   Produces the plain-English question (definition, allowed values, example) and stops.
   Fail-closed on purpose: a wrong guide sequence does not error, it silently produces
   a wrong result.
5. **Genome resolution.** Three-way interpretation of an override: known bundle,
   known species alias, or neither. **Warning:** the third branch is an unchecked
   passthrough — a stale or invented genome key goes straight into `params.yml`, and
   the error surfaces on the cluster rather than in the conversation.
6. **Allowlist and merge.** Rejects any setting not on the pipeline's curated menu;
   validates fixed-choice values; merges curated defaults ← reference params ← agent
   overrides; reports where the genome came from. **Warning:** only fixed-choice
   values are actually validated — a typo'd path passes through. **Warning:** the
   explicit reference-path route is switched off entirely today (`store_root` is
   null), so every run resolves to a local Luria genome file or a bare iGenomes name.
7. **Write the files.** `params.yml` and `launch.yml`, then record state.

- **Adding a pipeline:** declare the settings menu, the reference files wanted, and
  any human-only answers. This is the step that comes *closest* to being purely data —
  most pipelines are added here without touching code. But not all: crisprseq could
  not be added until someone built checkpoints 3 and 4, the whole mechanism for asking
  a person a question and refusing to proceed without an answer (`c85fbae`). The step
  is data-only today because that machinery already exists, and it exists because a
  pipeline demanded it.
  And **four** of the six defects found on 2026-08-05 were curation errors at this
  step: detaxizer staged a 64 GB database despite a flag meant to skip it; mag and
  bacass were handed parameter combinations their own pipelines reject; ampliseq was
  missing its primer questions entirely, so the run launched without them; seqinspector's
  default settings asked for an alignment index that does not exist here. The session's
  own guidance names the cause — do not trust a template's `skip_*` flags without
  reading the pipeline's actual gating code, an assumption that was wrong four times.

### § 04 · Onto the cluster — `submit_to_luria`

Threads the resolved genome through, corrects the GENCODE flag for local references,
renders `run.sh` and `luria.config`, copies four files over and calls `sbatch`.

- **Warning (the most consequential on the page):** with no genome resolved it fills
  in GRCh38 and prints a warning to a log nobody reads live. Unlike the human-answer
  gates, this does not stop the run.
- **Adding a pipeline:** declare the pinned version and which of `--genome` /
  `--fasta` / `--gtf` the pipeline's schema actually accepts. Get that list wrong and
  the run dies immediately, because the schema checker aborts on any parameter it does
  not recognise. Real defect: the file-fetching pre-stage was filling the wrong column,
  across five pipelines at once.

### § 05 · What the user is told — `conclude`

What the assistant is instructed to report about reference status before anyone
confirms a launch.

- **Adding a pipeline:** nothing. This step is entirely generic — the panel appears
  and says so, because an empty answer here is itself the point.

### § 06 · How far this stretches

The reckoning, not a repeat of the inline marks. The "two files, no code" claim holds
at two of the four stages (§ 01 and § 04, both of which need only a declaration),
breaks at the samplesheet step (§ 02, where renames and name rules live in code), and
has broken exactly once — for a single pipeline — at `configure_run` (§ 03).

Numbers, with their sources named on the page:

| Figure | Value | Source |
|---|---|---|
| nf-core repos in existence | 138 | 2026-08-05 schema census |
| ...with no release to pin | 42 | same |
| ...assessable | 96 | `docs/nfcore-schema-census-2026-08-05.json` |
| ...judged config-only | 27 | same |
| Catalogued here | 31 | `reports/templates/nfcore/*.json` |
| ...needing a human-only answer | 9 | `required_user_params` in those files |
| ...declaring reference files | 10 | `reference_resources` in those files |
| Genomes known / spellings accepted | 5 / 14 | `reference_bundles.json` |
| Ever run on the cluster | 2 | 2026-08-05 session report |

Closes on the sentence from that session report: every pipeline examined closely that
session had a defect in it.

---

## 5. Notes system

### 5.1 Anchors

An anchor on every block: each of the 7 sections, each of the 7 `configure_run`
checkpoints, each gate and warning mark, and each "Adding a pipeline here" panel. The
count follows from the content rather than being a target — on the structure in § 4
that lands around 30.

Every anchor carries a **hand-written stable ID** — `s03-cp5-genome`,
`s02-warn-column-renames`. IDs must never be positional or generated, because the page
will be edited and positional IDs would silently reattach notes to the wrong content.

### 5.2 Behaviour

- An empty anchor shows nothing until hover, then a small note affordance appears at
  the block's edge. The page stays clean when projected.
- A filled note is always visible as a small card in the margin, so annotated blocks
  are identifiable at a glance.
- Clicking either opens an inline textarea. Saves as you type, debounced, with a quiet
  "saved" acknowledgement.
- **`N`** opens a note on whichever block is currently centred in the viewport, so a
  comment can be captured mid-sentence without hunting for a button.

### 5.3 Contents rail

Each section in the rail shows a count of the notes it contains, and annotated
sections are visually distinct from un-annotated ones — so at the end of a session it
is visible where the discussion actually concentrated.

### 5.4 Notes panel

A top-bar button reading "N notes" opens a drawer listing every note in page order,
each under the title of the block it is attached to, each clickable to jump back to
that spot. This is the "what was commented at what step" view.

### 5.5 Storage

`localStorage`, one JSON blob keyed to this page, with a version field for future
migration.

```
{ "version": 1,
  "notes": { "<anchorId>": { "text": "...", "updatedAt": "<ISO 8601>" } } }
```

No server, no login, works from a `file://` URL with no network. The limitation is
stated on the page next to the export controls, not buried: notes live in this browser
on this machine, and clearing site data erases them.

### 5.6 Export and restore

- **Copy as Markdown** / **Download .md** — a dated document with each note under its
  section and block heading, in page order. Paste-ready for an email, an issue, or a
  handoff report.
- **Download .json** / **Restore from .json** — moves notes between machines, survives
  a browser reset, and lets the page be handed to a colleague with the comments intact.

Both downloads default to a dated, self-describing filename —
`nfcore-launch-path-notes-YYYY-MM-DD.md` / `.json`. A page cannot choose its own save
location; downloads land wherever the browser is configured to put them, so the
filename is the only affordance available for filing them into `MeetingNotes`
afterwards.

### 5.7 Orphan handling

If the page is later edited and a note's anchor disappears, the note is never silently
dropped. It moves to an "Orphaned notes" group at the bottom of the drawer with its
original block title preserved, to be moved or deleted deliberately.

Two tabs open on the same page is last-write-wins. Not worth solving; noted here so it
is a known behaviour rather than a surprise.

---

## 6. Visual design

Deliberately unlike `NessieAI/chat_nextseek/agent_atlas.html` so the two are not confused.
Where the atlas is warm cream, serif, and hand-drawn, this is a cool technical report:
near-white ground, ink-black text, a geometric sans for headings and body, monospace
reserved strictly for real identifiers (`configure_run`, `params.yml`, `GRCm39`).

Light rather than dark, because it must survive a projector in a bright room.

**The organising visual is a literal spine** — a vertical rule down the left of the
content column with each step's number on it. Sections connect to it; the seven
checkpoints branch off a nested spine of their own. Gates draw as a bar *across* the
spine; warnings draw as a mark *beside* it. That single distinction carries most of
the explanatory weight on the page.

Two accents only: one cool for gates, one warm for warnings. Extensibility panels are
neutral.

### Diagrams

Three hand-authored inline SVGs, no library:

1. **The five-tool spine end to end** — the orientation map.
2. **The seven checkpoints as a gauntlet** — which exits are refusals, which are
   silent continues.
3. **Species resolution in three stages** — table lookup, LLM fallback, re-check —
   with the unchecked branch drawn explicitly, because it is the subtlest point on the
   page and prose alone will not land it.

---

## 7. Mechanics

- One self-contained file at `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html`,
  outside this repo and outside git.
- No build step, no dependencies, no CDN, no web fonts. System font stack only, so it
  opens by double-clicking and works with no network. This matters more than usual
  here: the file sits alone in a notes folder with no repo, no server and no tooling
  around it, so it must be complete on its own or it is nothing.
- Single theme. It is a document, not an app.

Because the page is not version-controlled, the commit footer in § 8 is the only
record of which state of the code it describes. Any future edit must update it.

---

## 8. Accuracy discipline

Every factual claim traces to a file, a curated JSON, or the 2026-08-05 session report
(`.claude/reports/2026-08-05-nfcore-catalog-31-and-first-new-luria-verification.json`).
Derived counts name their source on the page. A footer records the commit the page was
written against (`0ac6571`), so a future reader can judge how stale it may be.

The page is a **written snapshot**. It does not read the codebase at runtime.

---

## 9. Verification before handover

Each checked in the browser and reported honestly, including anything that fails:

1. Notes round-trip: write a note → reload → it survived.
2. Export the Markdown; download the JSON; clear storage; restore from the JSON; the
   notes return on the correct blocks.
3. `N` targets the block actually centred in the viewport.
4. Contents-rail counts update as notes are added and removed.
5. Renaming an anchor surfaces its note as orphaned rather than losing it.
6. No console errors.
7. The whole page behaves from a `file://` URL with no network.

---

## 10. Out of scope

Search, print stylesheet, dark mode, multi-user or shared notes, any server component,
and any live generation from source.
