# Spec: Step 2 — Multi-user provisioning (project-stratified per-user directories)

**Date:** 2026-06-29
**Tracker:** `integration-plan.json` step **2** ("Multi-user provisioning")
**Status:** design, awaiting user review → writing-plans
**Builds on:** Step **1b** (per-session `.claude` persistence + `--resume`) and **1c**
(cross-session memory). This step **moves the host paths 1b/1c read/write** (per-change
sign-off required) — it is NOT purely additive.

---

## 1. Problem

CC provisioning is hardcoded to a single dev user. `projects_for()`
(`cc_config.py:70-85`) returns Dropbox folder names from a static map
(`_DEFAULT_USER_PROJECTS = {"demo": ["example-project"]}`, `cc_config.py:42`), and the
per-user mount sources are keyed only by `<user_id>` (flat), e.g.
`scratch_root/<user>`, `cc_state_root/<user>/<session>` (`cc_engine.py:398,402`). To put
this in prod (keynote 2026-07-14) the assistant must serve **real, isolated users**, with
files **shared within a project** but **private across users**.

Per the user's locked design (HANDOFF-2026-06-26 §F-6 / §H.1), **Dropbox is deferred**
(tracker step 3d) and replaced by per-user directories. Step 2 builds the **multi-user
identity + project-stratified directory isolation primitive**; the UI upload/download and
the `raw`/`artifacts` split are **Step 3**.

## 2. Goal / success criteria

- Per-CC-turn, the agent sees **only**: the requesting user's **own** private directory and
  the **shared** directory of the user's project — both **read-only**. CC writes only
  `scratch/` (unchanged invariant).
- **Cross-user isolation is absolute:** two users in the **same** project share `shared/`
  but can never read each other's `<user>/` space; two users in **different** projects are
  fully isolated (different `shared/` too).
- **Project membership is resolved per logged-in user** from SEEK (not a static map).
- **One project per user now**; the layout + resolution are structured so **multi-project
  is a later add, not a rewrite**.
- **No regression** to 1b `--resume`, 1c memory, the NS route, or OI-3 (zero-creds agent).

## 3. Target layout

Everything nests under the user's project, on a **single consolidated host root**
(decisions D3, D5):

```
<DMAC_USER_ROOT>/<projectID-slug>/
├── shared/                     → RO -> /data/shared (ALL users in the project)
└── <user>/
    ├── input/                  → RO -> /data/input  (private; empty in Step 2, uploads = Step 3)
    ├── scratch/                → RW -> /data/scratch (the ONLY dir CC writes)
    ├── cc-state/<session>/     → RW -> Claude home   (1b resume + 1c memory)
    └── output/                 → published artifacts  (reported via path-mappings)
```

- `<projectID-slug>` = `"{seek_project_id}-{slug(title)}"`. The **SEEK id is the stable
  key**; the slug is cosmetic. Because we own this root (no pre-existing Dropbox folder to
  match), the slug only needs to be **deterministic**, not a match.
- **Single root (D5):** the four separate host roots in `CCPaths`
  (dropbox/scratch/cc-state/output, `cc_config.py:45-67`) collapse into one
  `DMAC_USER_ROOT`, with the subdirs above. This is a larger `CCPaths`/compose-bind change
  than additive nesting — see §7 (migration) and §12 (deploy).
- **Container mounts (D1):** the RO inputs land at **`/data/input`** (private) and
  **`/data/shared`** (project) — replacing today's flat `dropbox_root/<project>` →
  `/data/projects/<project>` mount (`cc_engine.py:394-396`). `/data/scratch` (RW), Claude
  home, and `/data/output` keep their container paths.

## 4. Project resolution

A new **`resolve_user_project(request_or_creds)`** (host-side, Django process):

1. Reuse the credential the CC flow already resolves — `api_user, api_pass` from
   `_resolve_credentials(request)` (`services/cc_assistant.py:138-142,159`) — to construct a
   **credentialed** `SeekDB(None, api_user, api_pass)`. (Credentialed is essential:
   `SeekAPI` authenticates by HTTP Basic `curl -u user:pass` (`seekapi.py:18-24`), so the
   bare `SeekDB(None,None,None)` at `views.py:596` is NOT user-scoped and must not be copied.)
2. `getCurrentUser()['data']['relationships']['projects']['data']` → the user's project
   resource ids (the established membership pattern: `seek/views.py:1247,1345`).
3. **Single-project now:** take `projects[0]`; resolve its title via the existing
   `SeekDB.__getProjectName(id)` → `attributes.title` (`seekdb.py:343-348`) — exposed through
   a small public accessor (the name-mangled private must not be reached across the boundary).
4. Return a typed `ProjectIdentity { id: str, title: str, slug: str }`. Internally the
   resolver computes the **list**; Step 2 consumes `[0]`, so multi-project later is additive.

**Fallback (D4 — hybrid):** distinguish the two failure modes:
- **User has no SEEK project** (resolution succeeded, membership empty) → a **per-user
  personal namespace** `personal-<user>` (a synthetic `ProjectIdentity`, still fully
  isolated, no `shared/` peers). The user can still use CC.
- **SEEK call failed / outage** (resolution could not complete) → **fail-closed**: reject the
  turn with a clear message. We never guess a project, so work is never misattributed to the
  wrong/absent project. The personal namespace is **only** for a confirmed-empty membership,
  not for an unknown one.

## 5. Title → slug (dedicated helper)

A standalone **`slugify_project(title: str) -> str`** (the user explicitly wants this as a
helper, not inline). **Strict filesystem-safe rule (D2):**

```
lower-case the title
replace every run of non-[a-z0-9] characters with a single "-"
strip leading/trailing "-"
```

So `"Liver Tox (NDMA) study"` → `liver-tox-ndma-study`. Deterministic and robust to case,
spaces, punctuation, and unicode (transliterate/drop).

- Lives in one module, **unit-tested in isolation**, reused anywhere a project dir name is
  formed (no drift across call sites).
- The dir name is **`{id}-{slug}`**, so the SEEK id guarantees uniqueness/stability; the slug
  is purely cosmetic, which is why a strict-but-lossy normalization is safe (a degenerate
  slug, e.g. empty, still yields a valid unique dir via the id prefix).

## 6. Mount construction (`cc_engine`)

Rework `_build_volumes` (`cc_engine.py:380-409`) and the per-turn dir-spawn in `run_cc_turn`
(`cc_engine.py:444-485`) to the nested layout:

- **Inputs (RO):** `<root>/<project>/<user>/input` → **`/data/input`**;
  `<root>/<project>/shared` → **`/data/shared`**. Replaces the `for project in projects` flat
  Dropbox loop (`cc_engine.py:394-396`).
- **Scratch (RW):** `<root>/<project>/<user>/scratch` → `/data/scratch`.
- **CC-state (RW):** `<root>/<project>/<user>/cc-state/<session>` → Claude home.
- **Output:** `<root>/<project>/<user>/output` → path-mappings host_root (`cc_engine.py:489-494`).
- **1c memory mounts** (rendered `CLAUDE.md` + RO transcripts) ride along, repointed to the
  nested cc-state location.

**Security (preserve the existing precondition):** `_build_volumes` interpolates path
segments **without** re-validating (`cc_engine.py:388-391`), so **every** new segment is
validated *before* interpolation. The `<project>` segment goes through `_validate_project`
(`cc_engine.py:166+`, traversal guard, allows spaces/`-`/`_`); `<user>` keeps the strict
`_validate_user_id` (`149-163`); `<session>` keeps its guard (`470`). The dir-spawn `mkdir`
+ best-effort `chmod 0o777` lifecycle (`452-480`) moves to the nested paths, still created
host-side via the nextseek-container mount **before** the sibling CC container starts.

## 7. The 1b/1c path migration (regression-critical)

Nesting `scratch/` and `cc-state/` changes paths **1b resume + 1c memory depend on**:

- 1b/1c read/write `cc_state_root/<user>/<session>` and render `_memory/<user>/<session>`
  (`cc_engine.py:402,471`; `services/cc_assistant.py:270-275`). After this step those become
  `…/<project>/<user>/cc-state/<session>` and `…/<project>/<user>/…/_memory/…`.
- **All these call sites move in lockstep**, behind a single path-builder so no caller
  hardcodes the old shape.
- **Single-root consolidation (D5)** compounds this: the move is not just inserting a
  `<project>` segment but repointing all four `CCPaths` roots onto one `DMAC_USER_ROOT` with
  subdirs. The compose binds + `nextseek.env` change too (§12).
- **Re-verification is mandatory** (not optional): after the move, re-run the 1b resume A/B
  and the 1c cross-session-memory live checks (per their evidence docs) to prove no
  regression. This is the part that needs **per-change sign-off** and explicit diffs.
- **Existing dev on-disk state (D3 — wipe):** delete the `demo` user's current flat dirs and
  let the nested layout recreate them; **no migration code**. Acceptable because it's the
  dev instance with no real user data; the prior 1b/1c live evidence is already captured.
  Re-verify 1b/1c fresh after the move (above).

## 8. Isolation (the acceptance bar)

The hard boundary is **cross-user**; `shared/` is the only intentional same-project overlap.

1. **App layer (already true):** session reads are
   `ChatSession.objects.filter(user=request.user)` — a user only ever resolves their own
   sessions (1c §8.1).
2. **Mount layer:** every bind **source** is keyed by `<project>/<user>`; the private
   `<user>/` spaces are **never** cross-mounted; `shared/` is the same source for every member
   of one project. Private input + shared are **RO**; only `scratch/` is RW.
3. **Validation:** `<project>`/`<user>`/`<session>` all guarded before interpolation (§6).
4. **OI-3 preserved:** the SEEK resolution runs **host-side** in the trusted Django process
   using the user's own login creds; **no new credential enters the agent**; the agent stays
   zero-creds, Bedrock-only-via-proxy, `dmac-cc-net`.

## 9. Edge cases

- **User with no SEEK project** → `personal-<user>` namespace; **SEEK outage** → fail-closed
  reject (D4, §4).
- **`demo` dev user** → must resolve a project (real SEEK membership, else `personal-demo`) so
  the dev instance keeps working post-migration.
- **Project title with spaces / punctuation** → slug helper handles (§5); id prefix keeps the
  dir unique regardless.
- **Multi-project user (admin)** → out of scope now; resolver computes the list, consumer
  uses `[0]`, so enabling later is additive (§13).
- **Two users, same project, concurrent turns** → independent containers; shared RO source is
  safe to mount into both; private spaces disjoint.
- **Shared-folder provenance / contents** → not defined here; for Step 2 the dir simply
  exists and mounts RO (may be empty). Who populates it is **Step 3 / future** (§13).

## 10. Testing (TDD-first)

Hermetic units (the box can't run the Django test-DB runner — `seek_db_user` lacks `CREATE`;
use the `uv run --no-project --with pytest … --noconftest` pattern as in 1a/1b/1c):

- **slugify_project:** title→slug rule; spaces, punctuation, already-slugged, empty.
- **Project dir name:** `{id}-{slug}` formation; deterministic.
- **resolve_user_project:** with a **stubbed SeekDB** (no network) returning a fixture
  membership → correct `ProjectIdentity`; `[0]` selection; no-project + outage → fallback
  per D4 (§4): empty membership → `personal-<user>`, outage → fail-closed reject; asserts the
  **credentialed** construction (never `SeekDB(None,None,None)`).
- **Mount construction:** nested sources for input(RO)/shared(RO)/scratch(RW)/cc-state(RW)/
  output; container mount points unchanged; both inputs `mode: "ro"`.
- **Isolation:** two users same project → identical `shared/` source, **disjoint** `<user>/`
  sources; two users different project → fully disjoint incl. `shared/`; malicious
  `project`/`user`/`session` rejected before interpolation.
- **Path-builder migration:** the single builder yields the nested 1b/1c paths; old flat
  shape no longer produced anywhere (grep-guard test).

Live verification (forced-CC, ≤ $2 cap, UI Playwright per standing preference):
**two distinct logins**, same project → each turn mounts only that user's `input/` + the
shared `shared/`; cross-read attempt fails; **then** re-run the **1b resume** and **1c
memory** live checks at the new nested paths to prove no regression.

## 11. Resolved decisions (locked 2026-06-29, user-selected)

- **D1 — Container mounts:** new **`/data/input`** (private) + **`/data/shared`** (project)
  for the RO inputs; `/data/scratch`, Claude home, `/data/output` unchanged. (§3, §6)
- **D2 — Slug rule:** strict filesystem-safe (lowercase, collapse non-alnum → `-`, trim);
  cosmetic since the dir is `{id}-{slug}`. (§5)
- **D3 — Dev state:** wipe the `demo` flat dirs and recreate nested; no migration code;
  re-verify 1b/1c fresh. (§7)
- **D4 — Fallback:** hybrid — confirmed-empty membership → `personal-<user>` namespace;
  SEEK outage/failure → fail-closed reject. (§4, §9)
- **D5 — Host root:** consolidate the four `CCPaths` roots into one `DMAC_USER_ROOT` with
  subdirs; larger `CCPaths`/compose-bind change (not additive nesting). (§3, §7, §12)

## 12. Deployment

- **Single root (D5):** replace the four `DMAC_HOST_*_ROOT` / `DMAC_*_MOUNT` vars
  (`cc_config.py:45-67`) with one **`DMAC_USER_ROOT`** (host) + its container mount, plus the
  new `/data/input` + `/data/shared` binds (D1). The compose file + the gitignored
  `nextseek.env` change accordingly; the **prod host must carry the new bind + env** (it does
  not ride git) — call this out in the deploy diff.
- Optional knob for the personal-namespace prefix (default `personal-`, D4).
- Same Step-0 procedure under **per-change sign-off**: snapshot `:pre-step2` →
  fast-forward the SA build-context clone → rebuild → recreate (`--no-deps nextseek` via the
  SA `docker:cli` helper). The 1b/1c path migration (§7) ships as **its own reviewed diffs**
  with the resume + memory re-verification attached.

## 13. Out of scope (Step 2)

- **UI upload/download**, the **`raw`/`artifacts`** split of bundled scratch, and the
  **agent-activity panel** — all Step 3 (§F-6 / §H.1).
- **Dropbox removal** — Step 3d (Step 2 simply stops relying on it: real users get no Dropbox
  mount today, and the new model doesn't add one).
- **Multi-project / admin routing** — resolver already computes the list; consumer uses `[0]`.
- **Shared-folder population/management** — the dir exists + mounts RO here; contents are
  future.
