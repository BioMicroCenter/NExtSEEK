# Plan 2 — Provider resilience & routing continuity

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` or
> `superpowers:subagent-driven-development`. Steps use `- [ ]` checkboxes. Read
> `docs/2026-08-03-nessie-hardening-handoff-2-resilience-routing.md` **first**.

**Design:** `docs/2026-08-03-nessie-hardening-design.md` (tracks A1, B1, D1-capabilities, G)
**Branch:** cut a worktree from `dev-v3-merge`
**Owns, and nothing else:**

```
nextseek_api/services/cc_assistant.py              A1
nextseek_api/cc_assistant/tests/                   A1 tests
chat_nextseek/src/chat_nextseek/schemas/schema_helper.py   B1
chat_nextseek/tests/                               B1 tests
dmac_assistant/build_context/route_capabilities.json       D1
CLAUDE.md, UI.md, dmac_assistant/README.md,
chat_nextseek/CLAUDE.md, chat_nextseek/README.md   G
```

**Goal:** two product fixes. Make the conversation stay in one engine, and make the
provider fallback actually fall back. The second is the single highest-value fix in the
whole hardening effort: it caused ten of the eighteen failures in the last live run.

---

## Global constraints

- **`chat_nextseek/` is a vendored snapshot. Edit it IN PLACE.** Do **not** run
  `startup/scripts/sync_chat_nextseek.sh`. The canonical repo at
  `/home/cdemu/code/chat_nextseek` is on branch `cd-dev` and has **213 file differences**
  from this snapshot; syncing would revert the v3-merge anchoring. Editing in place is the
  established practice on this branch — see `2ef28d8`, `f5a13c9`, `1053303`, all of which
  are ordinary conventional commits touching `chat_nextseek/` directly.
- **Do not touch `nessie_tests/`.** It belongs to plan 1 in full. If a change here needs a
  corpus edit, note it in your completion note and leave it.
- **Two test lanes.** Both verified working on 2026-08-03:

  *Host lane* (for `chat_nextseek/`, fast, no container):
  ```bash
  cd chat_nextseek && uv run --no-project --with pytest --with pydantic --with requests \
    python -m pytest tests/<sel> -q -p no:cacheprovider
  ```

  *Container lane* (for `nextseek_api/`, needs the local stack up):
  ```bash
  docker cp nextseek_api/services/cc_assistant.py nextseek:/app/nextseek_api/services/cc_assistant.py
  docker cp nextseek_api/cc_assistant nextseek:/app/nextseek_api/
  docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
    sh -c 'cd /app && uv run pytest nextseek_api/cc_assistant/tests/<sel> --no-migrations -q -p no:cacheprovider'
  ```
  **Always copy the source alongside the tests.** Copying only tests runs them against the
  deployed image's older source and produces failures that are pure version skew.
- **Commit after every task.** Conventional commits; scopes `router`, `llm`, `docs`.
  Do not push.

---

## Task 1 — Baseline, and fix the one stale test

The container lane currently reports **29 passed, 1 failed** across
`test_decide_route_pipeline_gate.py`, `test_route_override.py`, `test_router_heuristic.py`
and `test_router_context.py`. The failure is real but stale, and it is in the exact file
you are about to extend.

`test_active_pipeline_forces_ns` monkeypatches `cc_router.decide` with a local `_boom()`
that takes only `query`. `_decide_route` now calls `cc_router.decide(req.query,
history=history)` (`cc_assistant.py:237`), so the stub raises
`TypeError: _boom() got an unexpected keyword argument 'history'`. The test was not updated
when history plumbing landed.

- [ ] Run both lanes and record the counts.
- [ ] Fix the `_boom` stub signature to accept `history=None`. Do not change the assertion;
      the test's intent is correct and should stay.
- [ ] Container lane now **30 passed, 0 failed**. That is your baseline.
- [ ] Commit: `test(router): update the pipeline-gate stub for the history parameter`.

---

## Task 2 — B1: make the Bedrock fallback chain reachable

**Problem.** Ten of eighteen seed-6 reds were one Bedrock outage reported as
`All provider fallbacks exhausted — agent 'parser': ServiceUnavailableException (reached
max retries: 4)`. The fallback machinery exists and cannot fire for Bedrock.

**Root cause, verified.** Two vocabularies that look the same and are not.

`_FALLBACK_CHAINS` (`chat_nextseek/src/chat_nextseek/schemas/schema_helper.py:26-33`) is
keyed on the **catalog** provider vocabulary:

```python
("default", "gcp"):  [...],
("default", "anth"): [...],
```

The lookup uses the **client** vocabulary:

```python
failed_provider = getattr(target_client, "provider", None)   # :256
_FALLBACK_CHAINS.get((catalog_key, failed_provider), [])     # :47
```

Client class attributes are `openai` (`llm_clients.py:89`), `gcp` (`:173`),
`anthropic` (`:291`), `bedrock` (`:440`). Only `gcp` coincides with a chain key. A 503 from
`BedrockClient` looks up `("default", "bedrock")`, gets `[]`, and raises `LLMFatalError`.

`agent_model_catalog.json`'s `default` profile routes `parser`, `report_writer`,
`report_coder` and `multi_parser` to `us.anthropic.claude-opus-4-7` with
`provider: "anth"`, which `build_llm_client` resolves to `BedrockClient`
(`llm_clients.py:777-781`). So the fallback is dead precisely on the agents whose failure
kills the whole turn. `config.py:1100` states the split without noticing it:
*"Keys mirror the catalog 'provider' field: 'gcp', 'anth', 'oai'."*

**Design choice.** Normalise at the lookup site. Do **not** rename either vocabulary:
`config.LLM_CLIENTS` is keyed by the catalog vocabulary (`config.py:1094`), and
`LLMError.provider` is surfaced to users and to logs.

- [ ] Write the failing test in a new `chat_nextseek/tests/test_llm_fallback_chain.py`:
      a stub client with `provider = "bedrock"`, under `catalog_key = "default"`, for agent
      `parser`, must yield a **non-empty** fallback list. Assert it is empty today, then
      make it pass. Use the existing `chat_nextseek/tests/test_llm_clients_*.py` files as
      the style reference.
- [ ] Add to `schema_helper.py`:
      ```python
      _CLIENT_TO_CATALOG_PROVIDER = {
          "bedrock": "anth", "anthropic": "anth", "gcp": "gcp", "openai": "oai",
      }

      def _catalog_provider(client) -> str:
          raw = getattr(client, "provider", None) or ""
          return _CLIENT_TO_CATALOG_PROVIDER.get(raw, raw)
      ```
      and use it where `failed_provider` is computed at `:256`.
- [ ] Test: `_catalog_provider` maps all four shipped client classes correctly, and passes
      an unknown provider through unchanged rather than swallowing it.
- [ ] Test: the existing **GCP** fallback path is unchanged. This is a regression guard —
      `gcp` is the one value that worked by coincidence and it must keep working.
- [ ] Test: the first fallback returned for a failed Bedrock `parser` is a GCP client, per
      `("default", "anth"): ["gcp:current", "anth:lite", "gcp:lite"]`.
- [ ] **Anti-drift test.** Assert that every distinct `provider` class attribute defined in
      `llm_clients.py` has an entry in `_CLIENT_TO_CATALOG_PROVIDER`. Discover them by
      introspection, not by hardcoding a list, so a new client class cannot silently
      reintroduce this bug.
- [ ] Leave `oai` chains absent. `_FALLBACK_CHAINS` has no `oai` keys and no shipped
      deployment is OpenAI-primary. Note it in the completion note; do not invent chains.
- [ ] Host lane green. Commit: `fix(llm): make the Bedrock 503 fallback chain reachable`.

---

## Task 3 — A1: sticky container_cc

**Problem.** The router classifies each turn independently, so a conversation that starts
in CC gets pulled back to NS mid-thread. Observed in seed 6: `green.refine_recall`'s seed
*"Find samples from a 4 week study."* routed `container_cc` and produced the best answer in
the run; its follow-up *"Just the 4 week ones."* routed `nextseek_query` and then failed
because no NS bundle existed to refine. The conversation broke in the middle.

**The rule, as chosen by the operator.** If the previous turn in this chat routed
`container_cc` and completed, stay in `container_cc`. Nothing more. A predicate-based
variant that tried to let obvious catalog lookups break out was designed, measured and
**rejected as over-complex**. Do not reintroduce it.

**Accepted consequence.** Because the guard fires on every subsequent turn, a chat that
reaches CC stays in CC until a new chat or an admin `force_route`. This is deliberate.

- [ ] Write failing tests in a new
      `nextseek_api/cc_assistant/tests/test_decide_route_sticky_cc.py`, modelled on
      `test_decide_route_pipeline_gate.py`:
      - prior turn `container_cc` + `status="completed"` + router says NS → CC,
        `source == "sticky"`
      - prior turn `container_cc` + `status="error"` → router's decision stands
        (a failed CC turn must not trap the chat)
      - empty history → router's decision stands
      - prior turn `nextseek_query` → router's decision stands
      - router says `container_cc` already → returned unchanged, `source` stays `baml`
      - router says `unrelated` → returned unchanged. **The guard must only ever convert
        NS to CC**, never touch `unrelated`.
      - `force_route` beats sticky (both `"ns"` and `"cc"`)
      - `pipeline_active` beats sticky
- [ ] Implement in `_decide_route` (`nextseek_api/services/cc_assistant.py:203`), **after**
      the router call and **after** the existing `pipeline_agent` check:
      ```python
      if decision.route == cc_router.ROUTE_NS and _prev_route_was_cc(history):
          return cc_router.RouteDecision(
              route=cc_router.ROUTE_CC, model_class="opus",
              model_id=cc_router._resolve_cc_model_id(),
              reasoning=f"sticky_cc; router said ns ({decision.reasoning})",
              source="sticky")
      ```
      with `_prev_route_was_cc(history)` returning
      `bool(history) and history[-1].router_choice == "container_cc"
       and history[-1].status == "completed"`.
- [ ] Order matters and has a documented reason. The wizard's first implementation
      short-circuited **before** the router and captured every following turn — a plain
      sample search got answered by the samplesheet builder. See the comment at
      `cc_assistant.py:245-252`. Put a comment on your guard pointing at that precedent so
      the next person does not "simplify" it back.
- [ ] Wrap the guard so it can never break routing: on any exception, fall through to the
      router's decision. This matches the file's existing posture (`router.py` is explicit
      that routing must never crash).
- [ ] Verify `history` is populated at the call site (`cc_assistant.py:342`) and carries
      `router_choice` — it is built by `router_context.build_history` from
      `extra_state["chat_log"]`, capped at the last 5 turns.
- [ ] Container lane green. Commit: `feat(router): keep a conversation in container_cc once it starts there`.

---

## Task 4 — D1: correct two routing calls in `route_capabilities.json`

Both are operator rulings. The corpus is right and the router's capability text is what
needs changing. **Fix capabilities, not the corpus** — capabilities *cause* routing
(the BAML router reads this file into its prompt at runtime), the corpus only *asserts* it.

- [ ] `sys.what_sample_types_are_most_com` — *"What sample types are most common?"* routed
      `container_cc`. The router's own recorded reasoning: *"a statistical aggregation and
      analysis rather than a simple metadata lookup ... falls under open_ended_analysis"*.
      Operator ruling: **this should be nextseek_query, a system question.** Narrow
      `open_ended_analysis` so a group-by-and-rank over the catalog is not its territory,
      and/or widen `system_or_catalog_question` to claim frequency and distribution
      questions about the catalog itself.
- [ ] `sys.who_is_the_current_user` — routed `container_cc`, reasoning *"asking about the
      current environment or authenticated session; best handled by the container where the
      agent can run shell commands"*. Operator ruling: **NS.** Note the supporting evidence:
      when it did route to CC it answered from context anyway and *offered* to read
      `NEXTSEEK_USERNAME` rather than reading it, so the container bought nothing. Adjust
      whichever family claims environment questions.
- [ ] **Do not** move a corpus question into `example_queries`. Anything appearing there
      becomes a few-shot example, and testing it afterwards measures literal recall rather
      than routing. Three real questions were already burned this way.
- [ ] `nextseek_api/cc_assistant/tests/test_route_capabilities.py` exists and derives its
      allowed-tools list from the bin directory. Run it; extend it if you add a family.
- [ ] These are prompt changes, so they are only provable by a live run. State plainly in
      the completion note that they are **unverified until the post-merge run**, and do not
      claim otherwise.
- [ ] Commit: `fix(router): route catalog frequency and identity questions to NExtSEEK`.

---

## Task 5 — Documentation owned by this plan

- [ ] Root `CLAUDE.md`: does not mention Nessie at all — no router, no CC path, no
      `nessie_tests/`, no `dmac_assistant/`. Its service table lists 7 containers;
      `docker-compose.yml` defines 9. Add a short assistant section and correct the table.
- [ ] Root `CLAUDE.md` frontend row: it conflates `npm run build` (emits
      `chat_frontend/dist`, gitignored and referenced by nothing) with
      `npm run build:embedded` (what the site actually serves). Also worth stating that the
      built bundle is **committed** and there is no build step in the Dockerfile, so every
      UI change is a two-step commit.
- [ ] `chat_nextseek/CLAUDE.md:42` and `chat_nextseek/README.md:236-257`: both still
      document the retired Seqera Tower launch path. `pipeline/agent_tools.py:218` says
      verbatim that Tower is retired and Luria is the only exposed target. Also document the
      sixth wizard tool, `handoff`, which lets the agent abandon a stuck wizard.
- [ ] Corpus counts: `chat_nextseek/CLAUDE.md:22` and `README.md:119` say 362,
      `catalog.json` holds 366, older docs say 381/447 turns. The live corpus is **280**
      active variants — but plan 1 is adding write cases, so state the number as "measured
      by `corpus.merged()`" and give the command rather than freezing a number that drifts.
- [ ] `UI.md:140` points at `/seek/salt/`; `seek/urls.py:13` defines only `^assistant/`.
- [ ] `dmac_assistant/README.md`: overstates usage — `streamjson` is never imported
      (`nextseek_api/cc_assistant/translate.py:17` reimplements it and says so) — and its
      deployment section describes host bind mounts `docker-compose.yml` no longer uses.
- [ ] Commit: `docs: correct assistant, Tower, corpus-count and UI drift`.

---

## Task 6 — Final verification

- [ ] Host lane green (`chat_nextseek/tests/`), count recorded.
- [ ] Container lane green (`nextseek_api/cc_assistant/tests/`), count recorded, and it
      must be **30 passed** plus your new tests.
- [ ] Confirm you touched **no** file under `nessie_tests/` and none of plan 3's files
      (`helpers/tools/nextseek_api.py`, `context/min_api_endpoints_enriched.json`,
      `prompts/parser_core_routing.txt`, `assistant/consumers.py`,
      `chat_nextseek/.../orchestrator.py`, `nextseek_api/views.py`).
      `git diff --name-only dev-v3-merge...HEAD` is the check.
- [ ] Completion note listing: what is unverified until a live run (task 4 entirely), and
      anything you could not do.
- [ ] **Do not merge. Do not push.**

---

## What this plan deliberately does not do

- Does not add OpenAI fallback chains. Out of scope until a deployment needs them.
- Does not change `_FALLBACK_CHAINS`' contents, only how it is looked up.
- Does not touch `docker/nextseek.env` (gitignored) or the CC turn timeout. Post-merge.
- Does not reintroduce the rejected `is_clearly_ns` predicate under any name.
