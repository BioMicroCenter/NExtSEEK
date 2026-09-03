# Cross-route shared memory: what I changed and why

**Branch:** `dev-260718` · **Commit:** `96b1d4a` · **Date:** 2026-07-22

## TL;DR

Conversational follow-ups that referred to a previous turn's results (e.g. "counts
of sex and species of **those** monkeys") were losing their referent and returning
the wrong answer. Root cause: the memory you built lives in the chat_nextseek
parser (the within-NS classifier), but the **top-level route selector is
stateless**, so a follow-up gets routed to Container-CC with no idea what "those"
means. I wired a compact conversation-history block into both the top-level router
and the CC turn, reusing your unified chat_log. Fix is pure Python, tested (19
passing), and takes effect on the next rebuild.

## The bug (session `aa93d142`)

| Turn | Query | Route | Result |
|---|---|---|---|
| 1 | "Find me sequencing data associated with non human primates" | NS `new_search` | **139** D.SEQ records |
| 2 | "Give me the unique counts of sex and species of all of **those** monkeys" | **container_cc** | **408** (all NHP) |

Turn 2 should have scoped to the monkeys behind the 139 records; instead it
re-queried every NHP.

## Root cause: the memory was one layer too low

There are two routing levels:

1. **Top-level dmac router** (`cc_router.decide(query)`, `dmac_assistant`) picks
   `nextseek_query` / `container_cc` / `unrelated`. **Stateless** — it only ever
   sees the current query string. A tree-wide search confirmed nothing passes
   conversation history into it.
2. **chat_nextseek parser** (`agents/parser.py`) picks the NS *mode*
   (`new_search` / `ask_about_last_results` / ...). **Memory-aware** — it calls
   `history_block(session)`. The chatter gets it too.

Your memory (`chat_memory.history_block` / `recent_turns` / `resolve_bundle_for_recall`)
is wired into level 2. So follow-ups that *stay* in NS resolve correctly. But the
stateless level-1 router diverts an analytical follow-up like turn 2 to CC, which
never reaches your memory-aware parser, and the CC turn only staged prior *CC*
transcripts (turn 1 was NS), so it had no referent.

**On your commit history:** I looked for a commit wiring memory into the top-level
router and found none, and no half-finished attempt. The top-level dmac router was
added separately in the additive `dmac_assistant` integration (`b6f304a`), after
your memory work, and never inherited the history. So this reads as a **layering
gap, not a mistake** — "the router" your memory serves is the chat_nextseek parser,
which became the *second* level once the dmac selector was bolted on above it.

## The fix (reuses your unified chat_log)

Your turn-id work (`8091ac1` / `20b61e1` / `8f5479a`) made `extra_state.chat_log`
span both NS and CC turns. I build one compact block from it and inject it in two
places:

- **`nextseek_api/cc_assistant/cc_history.py` (new)** —
  `build_conversation_history(chat_log)` returns a few lines of prior turns
  (`user_query` + result count/UIDs, or reply preview). `cc_prompt_with_history()`
  prefixes a CC prompt with it.
- **`nextseek_api/cc_assistant/router.py`** — `decide(query, history=None)`. When
  history is present, the query handed to the BAML router is augmented with it plus
  a steer: a follow-up referring to earlier results routes to `container_cc`. The
  keyword heuristic fallback still sees the raw query. (No BAML schema change, so
  no client regeneration coupling.)
- **`nextseek_api/services/cc_assistant.py`** — `_start_task` builds the history
  once from the unified chat_log and (a) passes it to the router and (b) prefixes
  the CC turn's prompt with it. So whichever route a follow-up lands on now has the
  context.

The two halves reinforce each other: the router steers follow-ups to CC, and CC now
has the cross-route memory to resolve them, so `139 -> 408` resolves either way.

## Direction

This matches the "route memory-style questions to CC" direction (issue #9): the
router now steers follow-ups to CC, and CC carries the memory. The chat_nextseek
`ask_about_last_results` path still works and is untouched; retiring it is #9's
scope, once this is validated live.

## Files changed

```
nextseek_api/cc_assistant/cc_history.py                     (new)
nextseek_api/cc_assistant/router.py                         decide(query, history)
nextseek_api/services/cc_assistant.py                       build + wire history
nextseek_api/cc_assistant/tests/test_cc_history.py          (new, 6)
nextseek_api/cc_assistant/tests/test_router_history.py      (new, 3)
nextseek_api/cc_assistant/tests/test_decide_route_pipeline_gate.py  (assert history threads)
```

## Validate

Pure Python, no BAML regeneration. Rebuild `nextseek`, then in the chat UI run the
two-turn case above: turn 2 should resolve "those monkeys" to the turn-1 result set
instead of all 408. The router's `route_decided` reasoning (now surfaced in the
Debug panel) will show it treating turn 2 as a follow-up.

## Open questions for you

- The router steer lives in the augmented query text (pragmatic, no BAML client
  regen). A cleaner version adds a `conversation_history` field to `RouterInput` in
  `router.baml`. Worth doing when we next touch the router schema.
- Strictly forcing *every* follow-up to CC and retiring the NS
  `ask_about_last_results` path is issue #9 — deliberately left separate.
