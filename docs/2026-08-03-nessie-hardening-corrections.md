# Corrections to the hardening spec, plans and handoffs

**Date:** 2026-08-03
**Applies to:** `2026-08-03-nessie-hardening-design.md`, the three `-plan-*.md`
and the three `-handoff-*.md` files.

The plans are left as written, because they are the record of what was actually
executed. Everything below is a claim in those documents that turned out to be
**false at HEAD**, found during execution and verified before being recorded here.
A future reader should trust this file over the originals.

All three executing agents were instructed to verify rather than comply, and every
item below was caught that way. That instruction is the reason this list is a set
of corrections rather than a set of defects.

---

## 1. Two acceptance criteria were unachievable as specified

Both are the same underlying mistake, made twice: **assuming a failing criterion
was floor-derived when it was inline.** `apply_family_floor` only *adds* a
criterion when the field is absent from the last turn, so it can never relax an
inline one.

| document | claim | reality |
|---|---|---|
| design §6 C1, plan 1 task 4 | "the three `advanced.*` cases satisfy the floor **without editing the cases**" | Only `advanced.find_me_nhp_samples_from_study_2` had a floor-derived `api_ok`. The other two carry inline `parser_plan.mode` / `api_plan.endpoint` / `api_ok` and needed overlay overrides (shipped as `7340492`). |
| plan 1 task 7 | flipping `green.refine_recall`'s inline route to `container_cc` makes the case green | It does not. Family `nessie_green` is not in the floor spec and all six criteria are inline, so the flip converts one route failure into four REST-criteria failures. The case needed its assertions rewritten for the route it actually takes (`a67c4f0` + `7b75334`). |

Plan 1 **task 5's motivating example is wrong for the same reason**: `green.refine_recall`
is not in a floored family, so the CC-skip could never fix it. Task 5's real measured
payoff is one variant, `tree.then_ask_about`.

## 2. "Only the `unrelated` route is free" is false

Plan 1 lines 198 and 202, and the same claim repeated in conversation.

`cc_router.decide` → `_baml_decision` makes an LLM call on **every** turn
(`router.py:161`), and `route_decided` is emitted at `cc_assistant.py:347` **before**
the `ROUTE_UNRELATED` check at `:352`. So `unrelated` skips the answering turn but not
the router that decided to skip it. It is the **cheapest** route, not a zero.

Corrected in three in-bounds places and pinned by
`test_an_unrelated_gate_is_unmeasured_rather_than_free`.

## 3. The host test lane recipe does not work in a fresh worktree

Given in all three handoffs as:

```bash
cd chat_nextseek && uv run --no-project --with pytest --with pydantic --with requests \
  python -m pytest tests/<sel> -q
```

It runs **only where `chat_nextseek/.venv` already exists**, which is true in the
primary clone and false in every worktree cut from it. The command silently borrowed
a prebuilt venv. In a fresh checkout it aborts with
`ModuleNotFoundError: No module named 'chat_nextseek'`; the working form needs
`PYTHONPATH=src` and roughly a dozen more `--with` deps.

Agents 2 and 3 hit this independently. Working invocations are recorded in their
ledgers under `.superpowers/sdd/<plan>/progress.md`.

**The `nessie_tests` lane recipe is correct** and was verified in a worktree:

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q
```

...but its stated diagnostic tell is now dead. Handoff 1 says a missing
`beautifulsoup4` shows up as "12 phantom failures". The suite has grown from 160 to
813, so the count is different (431 at the time it was measured). Use the presence of
`ModuleNotFoundError: bs4` in the recorded reason, not a failure count.

## 3b. A "correction" that was itself wrong

**Retracted 2026-08-04.** While this work was being planned, `nessie-orientation.md`'s
claim that "both `probe-2026-07-29*.json` raise `ValueError` on load" was reported to
the operator as stale and untrue. **The orientation document was right.** The claim
never reached this file, but it was acted on in conversation, so it is recorded here.

Both probes do raise:

```
ValueError: --cases include_ids not found in the corpus: ['repro.cypher_uid_dot']
```

The mistake was in how it was checked. `corpus.load_case_file()` parses the file and
does **not** resolve ids, so all three probes "load" fine. Resolution happens in
`corpus.select_cases(variants, include_ids, inline)`, which is where the raise comes
from. The original check called `select_cases` with keyword arguments that do not
match its signature, got a `TypeError`, and never retried — then reported the files
as healthy.

`repro.cypher_uid_dot` was retired in the issue-#35 pass, and no test covers
`include_ids` resolution, so nothing caught it.

**`probes/probe-cc-2026-07-31.json` is unaffected** and resolves to its 13 cases. It
carries no `include_ids` at all, only inline variants. The CC probe run is not blocked.

Found by an independent review of `nessie_tests` on 2026-08-04, recorded in
`.claude/reports/2026-08-04-nessie-tests-hardening-review.json`. The lesson is the
same one this file already documents: a check that fails for the wrong reason is not
a check. A `TypeError` from a bad call signature says nothing about the thing being
tested.

## 4. Citation errors

| document | claim | reality |
|---|---|---|
| handoff 3 | `write_gate.py` is imported **only** by `nextseek_api/assistant/granular.py:24` | Also `nextseek_api/services/assistant.py:90`. The finding stands — it is still off the `run_query` path — but the citation was incomplete. Caused by piping a `grep` through `head`. |
| plan 2 task 4 | `nextseek_api/cc_assistant/tests/test_route_capabilities.py` exists | It does not. The real file is `nextseek_api/assistant/tests/test_route_capabilities.py`. |
| plan 1 tasks 4/5 | `evaluate.py:117-119` is `build_observed_debug` | That range is inside `augment_debug`, which is the correct seam. |
| plan 2 task 5 | `docker-compose.yml` defines 9 services | It defines 10. Root `CLAUDE.md`'s 7 was also wrong. |
| plan 2 task 5 | `chat_nextseek/CLAUDE.md:42` carries the retired Tower content | Line 42 does not; the real occurrence is elsewhere in the file. |
| plan 2 task 5 | write "280 active variants, measured by `corpus.merged()`" into the `chat_nextseek` docs | Wrong twice. `corpus.merged()` is a `nessie_tests` function, and `chat_nextseek` is a standalone repo that does not ship with the harness, so its docs cannot reference it. A local drift-proof measure was used instead. |

## 5. A suggestion that was correctly declined

I proposed pinning the `4wk` spelling in `green.refine_recall`'s reply assertions.
Agent 1 checked the known-correct reply, found it never contains that string, and
declined with evidence. The shipped assertions pin the two genuine UIDs instead.

---

## What this list is evidence of

Ten corrections came out of plan 2 alone, two of them blocking. The pattern is
consistent: **line numbers, file paths and counts asserted without verification**,
and one test lane documented from a run that only succeeded because of an artifact
in the authoring clone.

None of it reached the code, because every agent was told that catching a false
claim in its brief outranked complying with it. If these plans are reused as a
template, keep that instruction — it is doing more work than any individual task
description.
