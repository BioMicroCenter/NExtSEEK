# `chat_frontend/`

## What this is

The React chat UI for the NExtSEEK assistant, and a boundary with no Python in
it at all: a find for files named `*.py` anywhere beneath this directory,
`node_modules` excluded, returns nothing. What it holds instead is 113
TypeScript and TSX files as of 2026-09-03, 97 of them under
`chat_frontend/src/`, counted the same way. React 19 and Vite 7 are the pinned majors
(`chat_frontend/package.json:32-33`, `chat_frontend/package.json:64`), styling is
Tailwind v4 (`chat_frontend/package.json:61`) over shadcn/ui primitives generated
with the config at `chat_frontend/components.json:2-12`.

One codebase, two entry points, and they are not variants of each other:

- **Standalone**, for local development only. `chat_frontend/src/main.tsx:6-9`
  mounts `App` into the `#root` div of `chat_frontend/index.html:16`, and Vite
  serves it on port 5173 (`chat_frontend/vite.config.ts:13-15`). It authenticates
  with HTTP Basic from three build-time environment variables
  (`chat_frontend/.env.example:1-3`) against a remote deployment.
- **Embedded**, the one users actually load.
  `chat_frontend/src/main.embedded.tsx:5-8` mounts `EmbeddedApp` into
  `#chat-assistant-root`, the div Django renders at
  `seek/templates/smartSearch.html:7`. It authenticates with the ambient Django
  session cookie plus a CSRF header and speaks to relative URLs
  (`chat_frontend/src/lib/services/sessionAuth.ts:8-22`).

Both shells drive the same two-engine assistant endpoint and render the same
progress-event vocabulary, so nothing here is per-route: a turn routed to the
deterministic pipeline and a turn routed to the sandboxed agent arrive as the
same envelope (`chat_frontend/src/lib/types/api.ts:8-20`).

The two shells are near-duplicates by construction —
`chat_frontend/src/EmbeddedApp.tsx:28` and `chat_frontend/src/AppLayout.tsx:31`
each own their own copy of the progress switch, the send handler and the download
handlers. The commentary at `chat_frontend/src/lib/sessionAdoption.ts:8-18`
records what that cost once.

## Surface

"Surface" here is not a set of importable modules — nothing outside this
directory imports TypeScript from it, by the search recorded in the dependency
section below. It is the set of **exported symbols** (components,
hooks, services, types), the **build outputs**, and the **DOM and URL contract**
the host Django page must satisfy. Cited lines are `export` sites and config
values rather than imports.

### Build outputs and their configuration

| Script | Config | Emits |
|---|---|---|
| `npm run build:embedded` (`chat_frontend/package.json:9`) | `chat_frontend/vite.config.embedded.ts:13-21` | `static/js/chat_assistant/`, URL-prefixed `/static/js/chat_assistant/` |
| `npm run build` (`chat_frontend/package.json:8`) | `chat_frontend/vite.config.ts:6-12` | `chat_frontend/dist`, ignored at `chat_frontend/.gitignore:2` |
| `npm run dev` (`chat_frontend/package.json:7`) | `chat_frontend/vite.config.ts:13-15` | nothing on disk |

The embedded build writes three asset files plus a manifest. Two of them are
named in that manifest (`static/js/chat_assistant/.vite/manifest.json:7-16`); the
third, a 429 kB SheetJS chunk, exists only because of the dynamic import at
`chat_frontend/src/lib/services/chatApi.ts:373` and is fetched at call time from
the `base` prefix set at `chat_frontend/vite.config.embedded.ts:21`.

### Host page contract

- The mount node id is hard-coded on both sides:
  `chat_frontend/src/main.embedded.tsx:5` and `seek/templates/smartSearch.html:7`.
- Deep links are rooted at a `<meta>` tag read at
  `chat_frontend/src/hooks/useChatRoute.ts:3-8` and written at
  `seek/templates/smartSearch.html:4`; the router recognises only the
  `chat/<id>` shape beneath it (`chat_frontend/src/hooks/useChatRoute.ts:15`).
- The composer pre-fills from a `q` query parameter
  (`chat_frontend/src/components/ChatPanel/MessageInput.tsx:18-26`).
- The embedded stylesheet imports Tailwind's theme and utilities but not its
  preflight reset (`chat_frontend/src/index.embedded.css:1-2`, against the full
  import at `chat_frontend/src/index.css:1`), and scopes every design token to
  the mount node (`chat_frontend/src/index.embedded.css:7`,
  `chat_frontend/src/index.embedded.css:129-132`).

### Services, hooks and state

`NextseekApiService` (`chat_frontend/src/lib/services/chatApi.ts:27`) is the
single HTTP/WebSocket client. It takes an auth strategy through the three-method
interface at `chat_frontend/src/lib/services/authTypes.ts:1-5`. Grepping
`chat_frontend/src` for classes declaring that interface returns three: the
session strategy above, the Basic-auth one at
`chat_frontend/src/lib/services/auth.ts:4-17`, and a stub used only by the tests
(`chat_frontend/src/lib/services/__tests__/chatApi.sessions.test.ts:7`).

| Concern | Where |
|---|---|
| Submit a turn, then stream or poll | `chat_frontend/src/lib/services/chatApi.ts:39` |
| Progress WebSocket | `chat_frontend/src/lib/services/chatApi.ts:110` |
| Two-second HTTP poll fallback | `chat_frontend/src/lib/services/chatApi.ts:169-215` |
| Session list, rename, delete, rehydrate | `chat_frontend/src/lib/services/chatApi.ts:312-367` |
| Bundle and artifact downloads | `chat_frontend/src/lib/services/chatApi.ts:235-293` |
| Agent file upload and its job poll | `chat_frontend/src/lib/services/chatApi.ts:381-403` |

Eight hooks plus a barrel live in `chat_frontend/src/hooks/`. The load-bearing
ones are `useProcessingState` (`chat_frontend/src/hooks/useProcessingState.ts:164`),
which owns the per-mode stepper table at
`chat_frontend/src/hooks/useProcessingState.ts:11-51` and a separate dynamic mode
for agent turns (`chat_frontend/src/hooks/useProcessingState.ts:258-281`);
`useSessions` (`chat_frontend/src/hooks/useSessions.ts:32`); `useMessages`
(`chat_frontend/src/hooks/useMessages.ts:31`), whose `hydrateFromTurns`
(`chat_frontend/src/hooks/useMessages.ts:66`) replays a stored conversation; and
`useChatRoute` (`chat_frontend/src/hooks/useChatRoute.ts:31`).

Three admin-only controls persist to `localStorage` and are read at send time:
route override (`chat_frontend/src/lib/forceRoute.ts:6-8`), production-database
toggle (`chat_frontend/src/lib/useProd.ts:7`) and per-turn wall clock
(`chat_frontend/src/lib/maxTurnLength.ts:7`). They render only for admins
(`chat_frontend/src/components/Layout/RouteOverrideSelect.tsx:17`,
`chat_frontend/src/components/Layout/ProdToggle.tsx:14`,
`chat_frontend/src/components/Layout/MaxTurnLengthInput.tsx:16`) and are mounted
together in the Debug sheet (`chat_frontend/src/components/Layout/RightSidebar.tsx:43-45`).

Components sit in six directories under `chat_frontend/src/components/`, beside
a seventh holding shared tests: `ChatPanel/` (the transcript, composer, stepper,
artifact list and upload control —
`chat_frontend/src/components/ChatPanel/ChatPanel.tsx:18`), `Layout/`
(`chat_frontend/src/components/Layout/CompactToolbar.tsx:9`), `Sessions/`
(`chat_frontend/src/components/Sessions/SessionSidebar.tsx:17`), `DebugPanel/`
(`chat_frontend/src/components/DebugPanel/DebugPanel.tsx:15`), `TestRunner/`
(`chat_frontend/src/components/TestRunner/TestCaseList.tsx:11`), and `ui/`, which
holds 13 shadcn primitives.

Two pieces of pure logic carry real domain knowledge: the remark plugin that
turns bare sample and SOP identifiers into links
(`chat_frontend/src/lib/remark-uid-links.ts:19-20`), and the terminal-event
session adoption at `chat_frontend/src/lib/sessionAdoption.ts:26-32`.

## Running and testing

Everything below runs from `chat_frontend/`, after `npm ci`, which installed 563
packages on 2026-09-03.

**Unit lane — vitest, jsdom, no backend.** `npm run test`
(`chat_frontend/package.json:12`) over the include glob at
`chat_frontend/vitest.config.ts:16`. Run on 2026-09-03: 27 test files, 165
passed, 0 failed, 4.10s. This is the lane to run before any commit.

**Build lane.** `npm run build:embedded` type-checks with a project-references
build and then bundles (`chat_frontend/package.json:9`). Run on 2026-09-03: exit
0, 2592 modules transformed in 2.86s, and the three emitted files were
byte-identical to the ones committed under `static/js/chat_assistant/assets/`,
content hashes included.

**Lint lane.** `npm run lint` (`chat_frontend/package.json:10`) over the flat
config at `chat_frontend/eslint.config.js:8-9`. Run on 2026-09-03: 11 problems,
9 errors and 2 warnings, exit code 1. The errors are pre-existing and the build
does not depend on this lane.

**Browser lanes — Playwright.** `npm run test:e2e`
(`chat_frontend/package.json:15`). The default `mock` project stubs the REST and
WebSocket surfaces (`chat_frontend/e2e/fixtures/ws-mock.ts:1-6`) and excludes the
real-backend directory (`chat_frontend/playwright.config.ts:26`); the two
real-backend projects appear only when an environment flag is set
(`chat_frontend/playwright.config.ts:28-52`) and each spec self-skips otherwise
(`chat_frontend/e2e/real-backend/test-case-1-embedded.spec.ts:4`). (not run) —
the mock project needs Playwright's browser binaries downloaded, and the
real-backend projects additionally need a reachable deployed instance with a
login that works.

**Coverage.** The merge recipe at `chat_frontend/Makefile:3-8` combines both
Playwright and vitest output. (not run) — same browser-binary requirement.

## Depends on / depended on by

### Depends on

- Its own npm dependency set, declared at `chat_frontend/package.json:18-66`,
  resolved through the lockfile format fixed at
  `chat_frontend/package-lock.json:4`, and installed into a tree that is ignored
  at `chat_frontend/.gitignore:1`.
- A set of Django HTTP endpoints rather than a set of modules. The turn is
  submitted to the two-engine route at
  `chat_frontend/src/lib/services/chatApi.ts:78`, progress is polled from the
  older assistant route at `chat_frontend/src/lib/services/chatApi.ts:181`,
  admin status comes from `chat_frontend/src/EmbeddedApp.tsx:89`, and sessions,
  bundles, artifacts and uploads come from the paths listed in the Surface table
  above.
- The host template's DOM, described in the Surface section and rendered by
  `seek/templates/smartSearch.html:1-9`.
- The Django view and URL that serve that template: `seek/urls.py:13` under the
  prefix mounted at `dmac/urls.py:27`, gated on authentication at
  `seek/views/search.py:110-114`.

### Depended on by

The outbound edge is a **committed build artifact**, not an import, so the
importer search that fits a Python package finds nothing here and its emptiness
proves nothing. Measured on 2026-09-03: a recursive grep of the whole worktree
for the literal string `chat_frontend`, with `node_modules` and the git
directory excluded, returns 130 hits, none of them inside this directory itself,
and every one is a prose mention, a path inside a JSON or Markdown inventory, or
a path string in Python. Not one is an import statement. The real chain is:

- `chat_frontend/vite.config.embedded.ts:14` writes the assets into the repo's
  own `static/` tree, and `chat_frontend/vite.config.embedded.ts:16` sets the
  manifest that makes the hashed names discoverable.
- Those emitted files are tracked, not ignored: no line in `.gitignore` or in
  `chat_frontend/.gitignore` names `static`, `js` or `chat_assistant`, the only
  match for those words across both files being a comment at `.gitignore:150`.
- `seek/templatetags/vite_assets.py:23-42` resolves that manifest by walking the
  configured source directories (`dmac/settings.py:88-91`), and
  `seek/templatetags/vite_assets.py:63-77` renders the stylesheet link and the
  module script from it.
- `seek/templates/smartSearch.html:8` renders it, and grepping every `.html`
  file in the worktree for the tag name finds it invoked nowhere else; the only
  other hit is the load statement two lines above it.
- `manage.py collectstatic` copies the tracked files to the root named at
  `dmac/settings.py:81`, which is what the web server actually serves; the URL
  prefix the tag builds comes from `dmac/settings.py:85`.
- The image build ingests the repository wholesale, so the emitted assets travel
  into the image as ordinary files rather than as something produced there; the
  deploy row written around that arrangement is `DEPLOYMENT.md:286`.

Consumers of the running UI, as opposed to the source:

- `ci/smoke/test_flows.py:105-121` loads the page, asserts exactly one bundle
  script tag whose name matches the emitted entry, and proves the bundle is
  executing by checking the composer hydrated from the query parameter.
- `ci/smoke/test_flows.py:128-142` intercepts the outgoing request and asserts
  the body this client sends.

Excluded from the list above, on the basis that they are references to paths
rather than dependencies on behaviour: the per-file path inventories in
`evidence/plan018-v4-9-owned-surface.json:1062-1067` and
`nessie_tests/FAMILIES.json:3703-3708`, the ownership prefix at
`scripts/plan018_v4_9_owned_surface.py:216`, the build-context assertion naming
this directory's example env file at
`nextseek_api/cc_assistant/tests/test_build_context_env_guard.py:103`, and the
superseded plan and review documents under `nextseek_api/cc_assistant/`.

Prose descriptions of this boundary live at `UI.md:37-78`,
`architecture.md:61-65` and `DEPLOYMENT.md:286`.

See `chat_frontend/CLAUDE.md` for the invariants this arrangement rests on and
the traps in it.
