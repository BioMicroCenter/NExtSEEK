"""Render docs/nessie-question-set-2026-08-06.md from qset.json + corpus.json.

The doc is GENERATED from the same file the corpus edit is generated from, so the
table a reviewer reads and the questions that would run cannot drift apart.
"""
import json, pathlib, sys, collections, csv

S = pathlib.Path("/tmp/claude-1000/-home-cdemu-code-dmac-docker/"
                 "7c6b89bb-13b7-48d6-8ccd-7b0eda6e02a0/scratchpad")
spec = json.loads((S / "qset.json").read_text())
corpus = json.loads(pathlib.Path("nessie_tests/corpus.json").read_text())
byid = {v["id"]: v for b in corpus["families"].values() for v in b["variants"]}
doc = spec["doc"]

REF = pathlib.Path("/home/cdemu/Desktop/nessie-grading-reference-2026-08-06")
ns = {r["query_id"]: r for r in csv.DictReader(open(REF / "ns_graded_answers.csv"))}
cc = {r["query_id"]: r for r in csv.DictReader(open(REF / "cc_graded_answers_PRIMED.csv"))}

BLOCKS = [
    ("Retrieval and question shape", [
        "sample_search", "sample_retrieve", "catalog_browse", "graph_traversal",
        "lineage_tree", "vocabulary_resolution", "system_capability_question",
        "followup_over_results", "search_refinement", "retrieval_path_selection"]),
    ("Reporting and delivery", [
        "project_summary_report", "submission_package", "artifact_delivery"]),
    ("Pipeline and write", [
        "pipeline_launch", "pipeline_output_reingest", "batch_upload_preparation",
        "harmonization", "entity_write", "writes_unsupported"]),
    ("System, session and safety", [
        "unsupported", "cc_sandbox_contract", "cross_session_memory",
        "session_lifecycle", "turn_limits_and_failure", "turn_delivery_and_trace"]),
]

MODE_LABEL = {"keep": "kept", "select": "kept", "reword": "reworded", "new": "new"}


def esc(t):
    return t.replace("|", "\\|").replace("\n", " ")


def crits(v):
    out = []
    for t in v["turns"]:
        for c in t["pass_criteria"]:
            if c["field"] == "last_reply" or str(c["field"]).startswith("api_artifact."):
                val = c.get("value")
                out.append("`%s` %s `%s`" % (c["field"], c["op"],
                                             (str(val)[:120] if val else "")))
    return "<br>".join(out) or "—"


lines = []
W = lines.append

byfam = collections.defaultdict(list)
for vid, d in doc.items():
    byfam[d["family"]].append(vid)

sel = [v for v in byid.values() if v.get("is_bayesian") and v["status"] == "active"]
assert len(sel) == len(doc), (len(sel), len(doc))
n_keep = sum(1 for d in doc.values() if d["mode"] in ("keep", "select"))
n_reword = sum(1 for d in doc.values() if d["mode"] == "reword")
n_new = sum(1 for d in doc.values() if d["mode"] == "new")
graded_keep = [vid for vid, d in doc.items() if d["mode"] in ("keep", "select") and vid in ns]
turns = sum(len(byid[vid]["turns"]) for vid in doc)

W("# NExtSEEK paired study — the 2026-08-06 question set")
W("")
W("**149 questions, 24 task families, every one asserting a verified value on the reply.**")
W("Nothing here has been run. This document exists to be argued with BEFORE a single")
W("paid turn is spent.")
W("")
W("The corpus edit is staged in `nessie_tests/corpus.json` on this branch and the unit")
W("suite is green (**1215 passed, 28 skipped**; baseline 1216/28 — see §9). Reverting is")
W("`git checkout nessie_tests/corpus.json`.")
W("")
W("---")
W("")
W("## 1. What this set is for, and what changed")
W("")
W("| | 2026-08-06 run | this set |")
W("|---|---|---|")
W("| selected variants | 152 | **149** |")
W("| turns driven per engine | 187 | **%d** |" % turns)
W("| task families covered | 21 | **24** |")
W("| distinct first-turn questions | 111 of 127 | **149 of 149** |")
W("| distinct questions across ALL turns | 167 of 187 | **%d of %d** |" % (turns, turns))
W("| variants asserting a real value on the reply | 42 of 152 | **149 of 149** |")
W("| worst repeated seed | 15 variants | **0** |")
W("")
W("Three findings from the last run drove every decision below.")
W("")
W("**Under forcing, only `last_reply` survives on a CC arm.** `evaluate.py` keeps a")
W("closed allowlist — `last_reply`, `api_artifact.*`, `bundle.*` — and skips everything")
W("else when the harness picked the engine. 83 of the 152 selected variants asserted")
W("*only* NS internals, so on the CC half of each pair they measured nothing. Every")
W("question in this set asserts a value on the reply; the checker in §9 enforces it.")
W("")
W("**Duplicate seeds spent real money on nothing.** Fifteen selected variants opened")
W("with a spelling of *Find me mice treated with NDMA*, four with *Find all NHP samples")
W("in the database*. This set has **149 distinct normalised queries across 149**")
W("variants — no two can prime each other even if the memory layer ever regresses.")
W("")
W("**A question whose lookup fails must FAIL.** `report.build_a_pride_deposit_for_d_ms`")
W("pointed at two UIDs that exist in neither store; NExtSEEK 404'd, both engines")
W("produced an empty skeleton, and both arms were graded PASS, because the criteria")
W("were satisfied by the failure. Every assertion here pins a value that can only")
W("appear if the work succeeded.")
W("")
W("---")
W("")
W("## 2. Per-family targets, and why")
W("")
W("The distribution is weighted by **observed user traffic**, not by family symmetry.")
W("`docs/nessie-adhoc-question-inventory.md` records 101 distinct questions really asked")
W("against the dev box. Their themes:")
W("")
W("| theme in the ad-hoc log | share | families in the old selection |")
W("|---|---|---|")
W("| search / scientist-attribute search | 56% | 20 + 17 + 5 |")
W("| **harmonization** | **13%** | **3** |")
W("| **reingest / upload sheet** | **13%** | **3 + 0** |")
W("| reporting / submission | 5% | 19 |")
W("| lineage / traversal | 5% | 10 |")
W("")
W("So harmonization and the reingest/upload-sheet path were each an eighth of real")
W("usage and a fiftieth of the study, while submission_package had four times the")
W("coverage its traffic justifies. That is the single biggest change here.")
W("")
W("| family | 2026-08-06 | **this set** | why |")
W("|---|---|---|---|")

RATIONALE = {
 "sample_search": (20, "the largest real-traffic block. 15 NDMA paraphrases collapse to 1; the freed budget buys 4 new traps and 3 new attribute axes."),
 "graph_traversal": (18, "17 kept questions were all on the NHP/MUS/Impact axis. Trimmed and hardened: the parent-side/child-side pair (139 vs 1,608) and the cross-store CEL split are new."),
 "lineage_tree": (12, "3 variants asked the same thing about NHP-220630FLY-5-PUB. Now 11 distinct shapes including multi-parent, whole-graph structure and a NOT-related pair."),
 "sample_retrieve": (6, "+1 for the title-is-not-the-UID case, which nothing covered and which a graph-only route cannot answer."),
 "vocabulary_resolution": (5, "the corpus's best-shaped family. +2 for the D.*/A.* convention and the mTB species-vs-strain correction."),
 "followup_over_results": (17, "10 of 17 shared the NDMA seed. Rebuilt as 9 two-turn scripts with 9 DISTINCT seeds and 9 different follow-up intents."),
 "search_refinement": (9, "4 of 9 shared both a seed and a refinement. Rebuilt around substitution refinements, which need no new ground truth and are what users actually type."),
 "catalog_browse": (3, "+3: the clade taxonomy, the people-vs-scientists gap, and the SOP test artifacts."),
 "system_capability_question": (8, "trimmed of two overlapping capability questions; the rest gain real values."),
 "retrieval_path_selection": (4, "unchanged in size; one member repointed off a number another family owns."),
 "project_summary_report": (9, "2 phantom-project variants retired (CGR). The Kamm-as-a-project question is KEPT because it is real traffic and the correction is the answer."),
 "submission_package": (10, "cut to match 5% of traffic. Every survivor asserts a real accession, so an empty skeleton fails."),
 "artifact_delivery": (1, "+1 so the family is not a single observation; both assert the host-path translation."),
 "pipeline_launch": (7, "the two `submit` variants are gone (see §6). Launch behaviour is still measured, up to but not through submission."),
 "pipeline_output_reingest": (0, "**newly covered.** 13% of real traffic and never once measured. Both run directories were verified to still exist on Luria."),
 "batch_upload_preparation": (3, "+2, including the operator's own genotype-key message from the ad-hoc log."),
 "harmonization": (3, "**+5.** The most-asked real question in the log ('normalize these genotype terms', 11 times) had zero coverage. Also the first KEY-level case."),
 "entity_write": (0, "**newly covered, safely.** Three formulations that withhold consent; see §6."),
 "writes_unsupported": (5, "unchanged. The refuse/must-not-refuse pair is the point."),
 "unsupported": (7, "3 bar-chart paraphrases collapse to 1, which now asserts the count table rather than a refusal."),
 "cc_sandbox_contract": (2, "+2: the network boundary and a credential-leak guard."),
 "cross_session_memory": (0, "**newly covered**, for what forcing can reach only; see §6."),
 "session_lifecycle": (0, "**newly covered.** Identity and the impersonation gate are both answerable on the reply."),
 "turn_limits_and_failure": (1, "+1: an unbounded request that the engine must bound itself."),
 "turn_delivery_and_trace": (1, "+1: narrate-then-answer, so a trace with no answer and an answer with no trace each fail."),
}
order = [f for _, fams in BLOCKS for f in fams]
for fam in order:
    before, why = RATIONALE[fam]
    W("| `%s` | %d | **%d** | %s |" % (fam, before, len(byfam[fam]), why))
W("| **TOTAL** | **152** | **149** | |")
W("")
W("**Excluded, with reasons.** `engine_routing` — asserting a route the harness itself")
W("forced is tautological; standing decision, not overturned. `route_overrides` —")
W("`force_route` precedence and sticky-CC are exactly what `--bayesian` overrides on")
W("every turn, so nothing about them is observable in this mode. ")
W("`turn_evaluation_and_retry` — four admin-only REST endpoints and a CLI; there is no")
W("chat turn that reaches it. All three stay reviewable in the free `route` tier.")
W("")
W("---")
W("")
W("## 3. How ground truth was established")
W("")
W("Every value below was measured **this session** against the live local stack, and")
W("the query is recorded on the variant in `corpus.json` as well as in the tables in")
W("§5. Nothing was taken from a prior document without re-running it — two things that")
W("look settled turned out not to be.")
W("")
W("- **MySQL**: `docker exec seek-mysql mysql -uroot -p… -N -B -e '<SQL>'`, schema")
W("  `seek_production`. All sample attributes live in `samples.json_metadata`;")
W("  extract with `JSON_UNQUOTE(JSON_EXTRACT(json_metadata,'$.Attr'))`.")
W("- **Neo4j**: `docker exec neo4j cypher-shell -u neo4j -p… --format plain '<CYPHER>'`.")
W("- **Luria**: `ssh luria 'ls …'` for the two reingest questions, read-only.")
W("")
W("### Four structural facts that shaped the questions")
W("")
W("**1. `samples.title` is not the UID for 1,402 rows.** The authoritative UID is")
W("`json_metadata.$.UID`, which is unique across all 50,887 rows. `samples.uuid` also")
W("holds it, and matches the JSON on 50,789. This is a whole class of question nothing")
W("in the corpus tested; `retrieve.title_is_not_the_uid` now does.")
W("")
W("**2. The two stores disagree, and it matters per question.** Keyed on the canonical")
W("UID: 50,789 in both, 98 MySQL-only (97 `CEL-260305GRI-*` + `CEL-TEST`), 100")
W("graph-only (97 `CEL-260317BMC-*` + three `U1`/`U2`/`U3` fixtures). Keyed on the")
W("numeric id the difference is 4 rows. The one place it changes an answer is CEL in")
W("Impact — **MySQL 318, Neo4j 79** — because 239 CEL rows carry no `IN_STUDY` edge.")
W("Every other type matches exactly. `graph.cel_in_impact_store_split` asks it on")
W("purpose and accepts either number.")
W("")
W("**3. Most natural-language counts are genuinely two-valued.** 43.8% of the declared")
W("schema is never populated, 93.6% of it is untyped free text, and **no attribute")
W("anywhere references a controlled vocabulary** — six vocabularies exist and zero")
W("attributes point at one. So `Lung`/`lung`, `Amplicon`/`AMPLICON`, `tif`/`TIF`/`.tif`")
W("and `Immport`/`ImmPort` all coexist. Where the strict and the natural reading differ,")
W("this set does one of two things and says which: **pin the scope in the question**")
W("(`harmon.organ_lung_case_split` now says *counting both capitalisations*), or")
W("**accept both readings and record them here** so a grader is not guessing.")
W("")
W("**4. `samples.created_at` is worthless for dates.** All 50,887 rows load as")
W("2026-01-27 within a 54-second window. Real dates are the UID date code or")
W("`SampleCreationDate` (populated on 57.9%). No question in this set rests on")
W("`created_at`, and `path.put_together_a_summary_of_the_sa` tests whether the engine")
W("notices.")
W("")
W("### Where ground truth could NOT be established — stated, not invented")
W("")
W("1. **TIS+CEL in Impact.** Neo4j 10,683, MySQL 10,922. `refrec.memory_unique_types`")
W("   asserts the sample TYPES, which both stores agree on, and no count.")
W("2. **'How many samples are in the database'.** 50,887 or 50,889 depending on the")
W("   store. The assertion is `50,?88[0-9]`, which the previous corpus already used.")
W("3. **The number of distinct scientists.** 113 real strings, 114 if the 182 JSON-null")
W("   rows count. `cat.people_versus_scientists` accepts either.")
W("4. **Lab-code → PI name.** Not stored anywhere; the codes are derived in application")
W("   code. No question was written that needs the mapping.")
W("")
W("---")
W("")
W("## 4. The three rules every question obeys")
W("")
W("These are enforced by a checker, not by good intentions (`§9`).")
W("")
W("1. **No two selected variants share a normalised query string** — 0 collisions")
W("   across 149 variants and %d turns. No two can prime each other." % turns)
W("2. **Every selected variant asserts something substantive on `last_reply`** (or on")
W("   `api_artifact.*`, the other field that survives forcing) — 149 of 149.")
W("3. **No two questions rest on the same number.** Two deliberate exceptions, both")
W("   documented inline: `195` (`advanced.basic_ndma` owns the NDMA count;")
W("   `path.actually_hang_on_find_me_the_mic` reuses it because the *interrupt* is the")
W("   point and 195 is the only right answer) and `50,88x` (a global count, and a")
W("   bounded alternative inside `limits.bound_an_unbounded_request`).")
W("")
W("---")
W("")
W("## 5. The questions")
W("")
W("`kept` = the text is byte-identical to the corpus, so the existing human grade for")
W("that id stays a valid reference. `reworded` = the id is preserved but the text")
W("changed, so the old grade is a comparison baseline, not a pre-fill. `new` = new id.")
W("")

for block, fams in BLOCKS:
    W("### %s" % block)
    W("")
    for fam in fams:
        ids = byfam[fam]
        W("#### `%s` — %d" % (fam, len(ids)))
        W("")
        W("| id | status | question (all turns) | asserted ground truth | how verified | what it tests |")
        W("|---|---|---|---|---|---|")
        for vid in ids:
            d = doc[vid]
            v = byid[vid]
            q = "<br>".join("*(%s)* %s" % (t["label"], esc(t["query"])) for t in v["turns"])
            W("| `%s` | %s | %s | %s | %s | %s |" % (
                vid, MODE_LABEL[d["mode"]], q, esc(d["gt"]), esc(d["how"]), esc(d["tests"])))
        W("")

W("---")
W("")
W("## 6. The three hazards, and what was done about each")
W("")
W("### `entity_write` — a paid run could MUTATE the database")
W("")
W("This is not theoretical. `seek_production.investigations` has **AUTO_INCREMENT 13")
W("with a maximum id of 8**, so five investigations were created and removed at some")
W("point. The write path is gated three ways (`nextseek-api-write` requires both")
W("`--parser-plan` and `--confirmed-write`; the runner re-checks; the skill requires a")
W("confirmation) but the CC container runs under `--permission-mode auto`, and the")
W("plugin's own SKILL.md says *\"treat L1 as defense-in-depth, not as a guarantee\"*.")
W("")
W("So the family is covered by three questions that **withhold consent by construction**:")
W("")
W("- `write.before_we_change_anything_what_a` — pure schema read. Mutates nothing.")
W("- `write.dry_run_scientist_merge` — *\"Don't apply anything yet — just tell me how")
W("  many rows that would change.\"* Answer is 379. Carries a negative guard that fails")
W("  the arm if the reply claims the merge happened.")
W("- `write.set_up_a_new_investigation_calle` — **reworded** from *\"Set up a new")
W("  investigation called NESSIE-PROBE-DELETEME\"*, which creates a real row if it")
W("  fires, to *\"Register a new mouse sample with strain C57BL/6 and sex Male\"*, which")
W("  cannot complete because MUS's three required attributes (Name, UID, Scientist) are")
W("  all absent. Same negative guard.")
W("")
W("`write.yes_go_ahead` (*\"Yes, go ahead.\"* as a lone turn, consenting to nothing) is")
W("**deselected**: it is the one phrasing that could plausibly push an engine through")
W("the `--confirmed-write` gate.")
W("")
W("**Operator call:** residual risk is not zero. If you want it to be, drop these three")
W("ids from the selection and the set is 146. Otherwise a `mysqldump` of")
W("`seek_production.investigations` and `sample_attributes` before the run makes any")
W("mutation reversible and detectable.")
W("")
W("### `pipeline_launch` — a paid run could put a real job on MIT Luria")
W("")
W("`PIPELINE_LAUNCH_MODE=LURIA` and a populated `LURIAKEY` are both live in")
W("`docker/nextseek.env`, and `submit_to_luria` ssh+sbatches. In the 2026-08-06 run")
W("neither `submit` turn actually launched — but both refused for question-specific")
W("reasons (a degenerate single-group cohort; a UID that does not exist), not because")
W("anything stopped them.")
W("")
W("Both `submit` variants are **deselected**. `pipeline.describe_before_submitting`")
W("replaces them: it asks for the full cohort and reference genome, says *do not submit")
W("anything*, and carries a negative guard that fails if the reply claims a job was")
W("queued. `tests/test_unified_corpus.py::test_no_selected_variant_ends_on_a_bare_submit_turn`")
W("now enforces this over the whole selection.")
W("")
W("### `cross_session_memory` — half of it is unreachable under forcing")
W("")
W("`--bayesian` pins both arms of a variant to one engine for all its turns, so the")
W("CC→NS cross-*engine* recall behind issues #36/#37/#38 cannot be reached in this mode")
W("at all. Three questions cover what CAN be reached, and the limit is recorded on the")
W("variants: within-chat file recall, within-chat number recall, and — directly")
W("regression-testing `eca15f6` — *\"What do you remember from my previous chat")
W("sessions?\"*, whose correct answer under `fresh_session` is *nothing*.")
W("")
W("---")
W("")
W("## 7. What to keep from the existing corpus")
W("")

kept_ids = sorted(vid for vid, d in doc.items() if d["mode"] in ("keep", "select"))
reword_ids = sorted(vid for vid, d in doc.items() if d["mode"] == "reword")
new_ids = sorted(vid for vid, d in doc.items() if d["mode"] == "new")
W("Of the 471 definitions in `corpus.json`:")
W("")
W("| disposition | count | what it means for the grades |")
W("|---|---|---|")
W("| **kept, text unchanged** | **%d** | the existing `<id>::<arm>` human grade stays a valid reference for that id. %d of them were graded in the 2026-08-06 run (so %d NS grades and %d CC grades line up). |"
  % (n_keep, len(graded_keep), len(graded_keep), len(graded_keep)))
W("| **reworded, id preserved** | **%d** | the old grade is a comparison baseline, not a pre-fill. Every one records why the text changed. |" % n_reword)
W("| **new ids** | **%d** | no prior grade. |" % n_new)
W("| **deselected** (`is_bayesian: false`, still ACTIVE) | **%d** | good questions that do not earn a paid arm in *this* study. Every one records a written reason. |" % len(spec["deselect"]))
W("| **retired** (`status: retired`, definition kept) | **%d** | should never run again. |" % len(spec["retire"]))
W("")
W("A note on what grade reuse actually buys. A re-run produces a NEW answer, so an")
W("imported grade is not a pre-fill — it is the per-question handle that lets the next")
W("report be diffed against this one, and it tells the grader what the answer looked")
W("like last time. That is worth having, which is why %d of the 149 keep their text" % n_keep)
W("byte-for-byte; the builder refuses to run if any of them has drifted.")
W("")
W("### Retired — %d (`status` flip, definition kept, full retirement record)" % len(spec["retire"]))
W("")
for vid, reason in sorted(spec["retire"].items()):
    W("- **`%s`** — %s" % (vid, reason))
W("")
W("### Reworded — %d (id preserved, text changed)" % n_reword)
W("")
for vid in reword_ids:
    W("- **`%s`** — %s" % (vid, doc[vid]["tests"].split(". ")[0] + "."))
W("")
W("### Deselected — %d" % len(spec["deselect"]))
W("")
W("Still active for the free `route` tier; out of the paid selection. Grouped by why:")
W("")
grouped = collections.defaultdict(list)
for vid, r in spec["deselect"].items():
    key = ("safety: a paid run could mutate or launch something"
           if "MUTATE" in r or "consents to nothing" in r or "live `submit`" in r or "--confirmed-write" in r
           else "incoherent, or a premise that is false"
           if ("incoherent" in r or "premise" in r or "does not exist" in r
               or "conflat" in r or "never established" in r or "anticipate" in r)
           else "duplicate seed or duplicate intent"
           if ("duplicate" in r or "paraphrase" in r or "same seed" in r or "twin" in r
               or "spelling variant" in r or "five-way" in r or "four-way" in r
               or "three-way" in r or "same UID" in r or "Replaced by" in r
               or "already" in r or "same investigation" in r or "same two UIDs" in r)
           else "no settled ground truth, or three defensible answers"
           if ("ground truth" in r or "defensible" in r or "readings" in r
               or "unsettled" in r or "WRONG" in r or "wrong" in r or "trap" in r)
           else "coverage traded for something better")
    grouped[key].append(vid)
for key in sorted(grouped):
    W("**%s (%d):** %s" % (key, len(grouped[key]),
                           ", ".join("`%s`" % v for v in sorted(grouped[key]))))
    W("")
W("The full per-id reason is on each variant as `_deselected_2026_08_06_qset`.")
W("")
W("---")
W("")
W("## 8. Cost and time")
W("")
W("Measured over the 2026-08-06 run, not estimated: 151 CC arms with an observed cost,")
W("**$36.30 total, mean $0.2388, median $0.239, max $0.5454**. NS arms report $0.00.")
W("Latency: CC mean 90.2s (median 80, max 391), NS mean 38.2s (median 24, max 623).")
W("")
W("| | this set |")
W("|---|---|")
W("| variants | 149 |")
W("| arms (149 x 2 engines) | 298 |")
W("| turns per engine | %d |" % turns)
W("| **CC cost** | **~$35.60** (149 x $0.2388) |")
W("| NS cost | $0.00 |")
W("| **wall clock, serial** | **~5.3 hours** (149 x 90.2s + 149 x 38.2s) |")
W("")
W("Two caveats, pulling in opposite directions. The observed mean is over a set that")
W("was 23%% multi-turn; this one is %d%%, which should make it slightly CHEAPER per" %
  round(100 * sum(1 for vid in doc if len(byid[vid]["turns"]) > 1) / len(doc)))
W("variant. Against that, several of the new questions are harder — a whole-attribute")
W("harmonization pass or a Luria directory listing is more work than a single count —")
W("and the two `pipeline_output_reingest` questions SSH to MIT Luria, which is slow and")
W("can fail for reasons that have nothing to do with either engine. Budget **$40**.")
W("")
W("Suggested `--max-usd 45`.")
W("")
W("---")
W("")
W("## 9. Verification")
W("")
W("```bash")
W("uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \\")
W("  python -m pytest nessie_tests/tests -q -p no:cacheprovider")
W("```")
W("")
W("**Before: 1216 passed, 28 skipped. After: 1215 passed, 28 skipped.**")
W("")
W("39 tests failed on the first run after the corpus edit. Every one was a")
W("*measurement* of the corpus — the suite is built that way on purpose — and each was")
W("updated with the new figure and the reason it moved:")
W("")
W("| measurement | before | after |")
W("|---|---|---|")
W("| curated definitions | 408 | 470 |")
W("| curated active variants | 308 | 365 |")
W("| curated active turns | 343 | 413 |")
W("| retired definitions | 100 | 105 |")
W("| atlas (unreviewed) variants | 63 | 59 |")
W("| overlay-tagged | 72 | 134 |")
W("| route criteria injected | 273 | 305 |")
W("| variants carrying a route criterion | 288 | 320 |")
W("| floored variants | 210 | 226 |")
W("| floor injections (outcome/report/truncation) | 153/57/48 | 168/58/52 |")
W("| families with no settled route expectation | 8 | 12 |")
W("| green under the all-CC simulation | 13 of 308 | **3 of 365** |")
W("")
W("That last row is the headline. The simulation asks how many variants would still")
W("pass if every turn ran `container_cc`. It collapsed from 13 to 3 because a variant")
W("that asserts an ANSWER no longer passes merely because the engine said something.")
W("The three survivors assert nothing but plan shape and are all deselected.")
W("")
W("**Six tests changed in SUBSTANCE, not in number.** Each is argued in its own")
W("docstring:")
W("")
W("- `test_every_negative_guard_is_dotall` checked for a literal `(?s)` prefix and")
W("  rejected `(?is)` — dotall *and* ignorecase, strictly stronger. Now checks the")
W("  compiled flags, which also catches a global flag written anywhere but the start")
W("  of a pattern (a hard error in Python 3.11+).")
W("- `test_bayesian_selection_takes_the_whole_refine_and_recall_family` asserted the")
W("  family ran WHOLE. That held while *whole* and *distinct* were the same thing; ten")
W("  of its seventeen members opened with one seed. Renamed")
W("  `test_every_deselected_refine_and_recall_member_records_why` — the original intent")
W("  (nothing leaves by accident) survives as a written-reason requirement.")
W("- `test_bayesian_selection_includes_the_two_job_launching_pipeline_cases` PINNED the")
W("  two `submit` variants so nobody could quietly drop them without reopening the")
W("  decision. The decision was reopened and reversed; it is now")
W("  `test_no_selected_variant_ends_on_a_bare_submit_turn`, enforcing the opposite over")
W("  the whole selection.")
W("- `test_an_honest_negative_is_still_accepted` demanded the floor accept *\"No samples")
W("  of that type are in project IMPACT\"* for `advanced.find_me_d_seq_samples_in_proje`.")
W("  That is not an honest negative — the answer is 1,858 — so the entry was removed")
W("  and the test now parametrizes off the mapping rather than a parallel tuple.")
W("- `test_copying_a_block_brings_retired_cases_and_their_tags` anchored on the string")
W("  *\"GBM does not exist\"*, which made it about which family happened to be largest.")
W("  Now picks on the property its own assertion needs.")
W("- `_ADDED_2026_08_06` in `test_floor_ops.py` matched two exact keys; it now matches")
W("  by prefix, so 58 new variants cannot silently fold themselves into a measurement")
W("  that is explicitly about the corpus as it stood before them.")
W("")
W("### The selection's own checker")
W("")
W("```")
W("[1] duplicate normalised queries among selected: 0")
W("[2] selected variants with NO substantive reply/artifact assertion: 0")
W("[3] repeated ground-truth numbers: 2 (both documented in §4)")
W("```")
W("")
W("---")
W("")
W("## 10. Open items for the operator")
W("")
W("1. **`entity_write` residual risk** (§6). Accept, or drop 3 questions and run 146.")
W("2. **`unsup.domain_chemistry`** — should the assistant answer textbook chemistry?")
W("   Still unruled. The assertion accepts either a correct answer or a scope refusal,")
W("   so the run produces evidence without pre-judging it.")
W("3. **`vocab.mtb_is_a_species_not_a_strain`** — in the 2026-08-06 run both arms of the")
W("   *M. tuberculosis* question were lost to a model usage-policy refusal, which is")
W("   neither a bad question nor a product defect (ANN-8, still open). Kept because the")
W("   vocabulary correction is worth measuring; drop one line to remove the risk.")
W("4. **`write.*_must_confirm_first` are still filed under `writes_unsupported`** while")
W("   their names expect confirm-then-write. This set does not move them — it asserts")
W("   the one thing both readings agree on, that nothing may be claimed to have")
W("   happened without confirmation. Moving them changes their route policy and their")
W("   HiBayes subtype, and depends on whether \"NExtSEEK writes are read-only\" is still")
W("   policy now that CC can stage them.")
W("5. **Routing is still not measured by this study and cannot be.** If routing quality")
W("   matters, it needs a mode that leaves the router live and RECORDS the engine")
W("   instead of dictating it.")
W("6. **59 atlas variants remain unread.** They run in the free tiers and are excluded")
W("   from every measurement. Worth a pass before a third study.")
W("")

pathlib.Path("docs/nessie-question-set-2026-08-06.md").write_text("\n".join(lines) + "\n",
                                                                 encoding="utf-8")
print("wrote docs/nessie-question-set-2026-08-06.md",
      len(lines), "lines;", n_keep, "kept /", n_reword, "reworded /", n_new, "new;",
      len(graded_keep), "kept ids carry a 2026-08-06 grade")
