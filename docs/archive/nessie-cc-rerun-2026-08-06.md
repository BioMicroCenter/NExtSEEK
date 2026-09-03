# CC-only rerun of the 127-variant study — staged and ready

**Why:** every CC arm of both 2026-08-06 paired runs was primed with a rendered
`~/.claude/CLAUDE.md` distilled from up to 5 **other CC arms of the same run**.
NS arms got no equivalent, so the paired comparison was not like-for-like. Fixed
in `eca15f6`; this reruns the CC halves under the fix.

**Status: STAGED, NOT RUN.** Everything below is prepared. Nothing has been spent.

---

## 1. What was contaminated, and what was not

`_session_metas` filters `ChatSession.objects.filter(user=user)`
(`cc_assistant.py:169`) — **user-scoped, not session-scoped**, so `force_new`
never covered it. Verified directly on the volume, not inferred: the file staged
for `sandbox.can_you_pull_together_the_sequen::cc` is headed *"Cross-session
memory (auto-generated)"* and carries other cases' work — the nf-core samplesheet
failure, `/data/scratch/cohort_notes.txt` with "195 UIDs of NDMA-treated mouse
samples", the amplicon search.

**NS arms were never given a memory file.** Their results and their human grades
are clean and are NOT being repaid for.

The sharpest single instance: `sandbox.can_you_pull_together_the_sequen` asks for
"that study we talked about" — a question with no legitimate referent. NS
correctly said it had nothing stored (graded **fail**); CC answered from five
other cases' results (graded **pass**).

---

## 2. What is staged

`nessie_bayes_full_cc/` — the original `nessie_bayes_full/` is **untouched** and
remains the graded record.

| file | what it is |
|---|---|
| `bayes_manifest.json` | the 127 pairs with every `cc` entry blanked and every `ns` entry kept |
| `grades_ns_only.json` | 127 NS grades. The 127 CC grades are deliberately dropped — they grade the primed answers |

Verified through the real reader, not by inspection:
`completed_arms` returns **ns 127 (skipped), cc 0 (so 127 will be run)**.

`--resume` is built for exactly this. `completed_arms` is keyed on the ARM —
*"a run interrupted between the NS and CC halves of one question must not repay
for the NS half"* — and `run_paired` seeds `pairs` from the prior manifest
(`bayesian.py:169`) and reuses each prior pair (`:207`), so the NS halves ride
through untouched.

---

## 3. The corpus has to go back first

`--resume` refuses onto a changed corpus, and `corpus.json` has moved since that
run (127 selected → 152, from the additive pass). The original state is exactly
recoverable and was checked:

```
run recorded:              94434747b51834fa0083127a12e7e1bf6effd42b879aeabd77eccbc2186402a0
git show a8fc358:...       94434747b51834fa0083127a12e7e1bf6effd42b879aeabd77eccbc2186402a0   ✅ match
current corpus.json        7f77a1489afc415a1861081cd915e1c1f88182ff8228417046b5e5950c3bfcc8   ✗
```

---

## 4. Run it

```bash
cd /home/cdemu/code/dmac/docker/dev-v3-merge

# 1. put the corpus back to exactly what that run was selected from
git show a8fc358:nessie_tests/corpus.json > nessie_tests/corpus.json
sha256sum nessie_tests/corpus.json   # must be 94434747b51834fa...

# 2. rerun the CC halves only. ~127 arms, ~$30, ~3h
uv run --no-project --with requests --with pydantic --with beautifulsoup4 \
  python -m nessie_tests --bayesian --resume \
  --base-url http://localhost:8000 \
  --user demo --password demopassword \
  --out ./nessie_bayes_full_cc \
  --max-usd 45

# 3. restore the corpus. The 152-variant selection is the committed state.
git checkout nessie_tests/corpus.json
```

The fix rides automatically: `http_driver.drive()` now sends
`fresh_session: True` on every turn. It needs no flag and cannot be forgotten —
`QueryRequest` is `extra="forbid"` (`models_api.py:24`), so a removed or renamed
field 422s rather than being silently dropped, and every preflight turn goes
through `drive()`.

**Do not run this while another session is using the local stack.** Both drive
the same `demo` user, and a concurrent CC turn would land in the memory window
this rerun exists to empty.

### Then grade and merge

```bash
# collect + export + report
uv run --no-project --with zstandard --with requests --with pydantic --with beautifulsoup4 \
  python -m nessie_tests.collect --run ./nessie_bayes_full_cc
uv run --no-project --with zstandard --with requests --with pydantic --with beautifulsoup4 \
  python -m nessie_tests.export --run ./nessie_bayes_full_cc
uv run --no-project --with zstandard --with requests --with pydantic --with beautifulsoup4 \
  python nessie_tests/output-skill-bayesian/scripts/build_bayes_report.py \
    --run ./nessie_bayes_full_cc --out ./nessie_bayes_full_cc/report_bayes.html
```

Open the report from disk (`file://…`), **import `grades_ns_only.json`**, and
grade only the 127 CC arms — the NS half arrives already graded. Then:

```bash
python nessie_tests/output_skill_bayesian/merge_grades.py \
  --run ./nessie_bayes_full_cc --grades ./nessie_bayes_full_cc/grades.json

python nessie_tests/scripts/human_functional_rows.py \
  --run ./nessie_bayes_full_cc --run ./nessie_bayes_delta \
  --out ./nessie_bayes_study
```

Grade on ONE browser origin throughout: grades live in `localStorage`, and
`file://` and `http://localhost:PORT` are different origins.

---

## 5. What this rerun will answer

The delta run is **not** covered here and is still primed; rerun it the same way
(`nessie_bayes_delta`, fingerprint `309ca580…`, ~$6.57 / 25 arms) if you want the
21-family study clean too.

Keeping the original `nessie_bayes_full/` makes the interesting comparison
possible: the same 127 questions, same engine, primed vs fresh. That measures the
cross-session memory feature itself — which is a real CC capability NS lacks, and
worth quantifying rather than only subtracting.

Expected from the existing analysis: CC's overall margin narrows but holds
(+9 → about +7), and the claim that does **not** survive is CC's edge on
reference-resolution and refine-and-recall, where the priming concentrated. Five
`refrec` variants were handed *"Out of the 408 NHP samples, 46 samples are
CD8-depleted"* before being asked it.

---

## 6. Two follow-ups this surfaced

- **Product, low severity:** `render_memory` (`cc_memory.py:88-95`) lists
  `<mount>/<sid>.jsonl` for every session in the window including ones with no
  staged file, so the memory block routinely hands the agent read-only paths that
  do not exist. Harmless in these runs (0 arms followed one).
- **Corpus:** the `refrec` family's paraphrase duplication is what turned a
  10-session memory window into an answer key. Even with `fresh_session`,
  near-duplicate questions within one run remain a hazard for any future
  memory-on evaluation.
