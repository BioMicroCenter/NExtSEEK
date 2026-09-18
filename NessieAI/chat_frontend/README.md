# `NessieAI/chat_frontend/`

## What this is

The React chat UI for the NExtSEEK assistant, and a boundary with no Python in
it at all: a find for files named `*.py` anywhere beneath this directory,
`node_modules` excluded, returns nothing. What it holds instead is TypeScript and
TSX, most of it under `NessieAI/chat_frontend/src/`. React 19 and Vite 7 are the pinned majors
(`NessieAI/chat_frontend/package.json:32-33`, `NessieAI/chat_frontend/package.json:64`), styling is
Tailwind v4 (`NessieAI/chat_frontend/package.json:61`) over shadcn/ui primitives generated
with the config at `NessieAI/chat_frontend/components.json:2-12`.

One codebase, two entry points, and they are not variants of each other:

- **Standalone**, for local development only. `NessieAI/chat_frontend/src/main.tsx:6-9`
  mounts `App` into the `#root` div of `NessieAI/chat_frontend/index.html:16`, and Vite
  serves it on port 5173 (`NessieAI/chat_frontend/vite.config.ts:13-15`). It authenticates
  with HTTP Basic from three build-time environment variables
  (`NessieAI/chat_frontend/.env.example:1-3`) against a remote deployment.
- **Embedded**, the one users actually load.
  `NessieAI/chat_frontend/src/main.embedded.tsx:5-8` mounts `EmbeddedApp` into
  `#chat-assistant-root`, the div Django renders at
  `seek/templates/smartSearch.html:7`. It authenticates with the ambient Django
  session cookie plus a CSRF header and speaks to relative URLs
  (`NessieAI/chat_frontend/src/lib/services/sessionAuth.ts:8-22`).

Both shells drive the same two-engine assistant endpoint and render the same
progress-event vocabulary, so nothing here is per-route: a turn routed to the
deterministic pipeline and a turn routed to the sandboxed agent arrive as the
same envelope (`NessieAI/chat_frontend/src/lib/types/api.ts:8-20`).

The two shells are near-duplicates by construction:
`NessieAI/chat_frontend/src/EmbeddedApp.tsx:28` and `NessieAI/chat_frontend/src/AppLayout.tsx:31`
each own their own copy of the progress switch, the send handler and the download
handlers. The commentary at `NessieAI/chat_frontend/src/lib/sessionAdoption.ts:8-18`
records what that cost once.

## Surface

"Surface" here is not a set of importable modules: nothing outside this
directory imports TypeScript from it, by the search recorded in the dependency
section below. It is the set of **exported symbols** (components,
hooks, services, types), the **build outputs**, and the **DOM and URL contract**
the host Django page must satisfy. Cited lines are `export` sites and config
values rather than imports.

### Structure

```
NessieAI/chat_frontend/src/
├── App.tsx, AppLayout.tsx     # the standalone shell (local development only)
├── EmbeddedApp.tsx            # the embedded shell, the one Django loads
├── main.tsx                   # standalone entry
├── main.embedded.tsx          # embedded entry
├── index.css                  # standalone styles
├── index.embedded.css         # embedded styles, scoped to the mount node
├── components/
│   ├── ChatPanel/             # transcript, composer, stepper, artifact list, upload control
│   ├── DebugPanel/            # the admin Debug panel
│   ├── Layout/                # toolbar, sidebars and the admin controls
│   ├── Sessions/              # session list, rename, delete
│   ├── TestRunner/            # the test-case runner
│   ├── __tests__/             # shared component tests
│   └── ui/                    # shadcn/ui primitives
├── hooks/                     # useProcessingState, useSessions, useMessages, useChatRoute and the rest
├── lib/                       # services/ (chatApi.ts and the auth strategies), types/, utils/, the admin toggles
└── test/                      # vitest setup
```

### Build outputs and their configuration

| Script | Config | Emits |
|---|---|---|
| `npm run build:embedded` (`NessieAI/chat_frontend/package.json:9`) | `NessieAI/chat_frontend/vite.config.embedded.ts:13-21` | `static/js/chat_assistant/`, URL-prefixed `/static/js/chat_assistant/` |
| `npm run build` (`NessieAI/chat_frontend/package.json:8`) | `NessieAI/chat_frontend/vite.config.ts:6-12` | `NessieAI/chat_frontend/dist`, ignored at `NessieAI/chat_frontend/.gitignore:2` |
| `npm run dev` (`NessieAI/chat_frontend/package.json:7`) | `NessieAI/chat_frontend/vite.config.ts:13-15` | nothing on disk |

The embedded build writes three asset files plus a manifest. Two of them are
named in that manifest (`static/js/chat_assistant/.vite/manifest.json:7-16`); the
third, a 429 kB SheetJS chunk, exists only because of the dynamic import at
`NessieAI/chat_frontend/src/lib/services/chatApi.ts:380` and is fetched at call time from
the `base` prefix set at `NessieAI/chat_frontend/vite.config.embedded.ts:21`.

### Host page contract

- The mount node id is hard-coded on both sides:
  `NessieAI/chat_frontend/src/main.embedded.tsx:5` and `seek/templates/smartSearch.html:7`.
- Deep links are rooted at a `<meta>` tag read at
  `NessieAI/chat_frontend/src/hooks/useChatRoute.ts:3-8` and written at
  `seek/templates/smartSearch.html:4`; the router recognises only the
  `chat/<id>` shape beneath it (`NessieAI/chat_frontend/src/hooks/useChatRoute.ts:15`).
- The composer pre-fills from a `q` query parameter
  (`NessieAI/chat_frontend/src/components/ChatPanel/MessageInput.tsx:18-26`).
- The embedded stylesheet imports Tailwind's theme and utilities but not its
  preflight reset (`NessieAI/chat_frontend/src/index.embedded.css:1-2`, against the full
  import at `NessieAI/chat_frontend/src/index.css:1`), and scopes every design token to
  the mount node (`NessieAI/chat_frontend/src/index.embedded.css:7`,
  `NessieAI/chat_frontend/src/index.embedded.css:129-132`).

### Services, hooks and state

`NextseekApiService` (`NessieAI/chat_frontend/src/lib/services/chatApi.ts:27`) is the
single HTTP/WebSocket client. It takes an auth strategy through the three-method
interface at `NessieAI/chat_frontend/src/lib/services/authTypes.ts:1-5`. Grepping
`NessieAI/chat_frontend/src` for classes declaring that interface returns three: the
session strategy above, the Basic-auth one at
`NessieAI/chat_frontend/src/lib/services/auth.ts:4-17`, and a stub used only by the tests
(`NessieAI/chat_frontend/src/lib/services/__tests__/chatApi.sessions.test.ts:7`).

| Concern | Where |
|---|---|
| Submit a turn, then stream or poll | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:39` |
| Progress WebSocket | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:110` |
| Two-second HTTP poll fallback | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:169-215` |
| Session list, rename, delete, rehydrate | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:319-374` |
| Bundle and artifact downloads | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:235-300` |
| The whole chat as one zip (the Debug sheet's "All files") | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:438` |
| Agent file upload and its job poll | `NessieAI/chat_frontend/src/lib/services/chatApi.ts:388-410` |

Eight hooks plus a barrel live in `NessieAI/chat_frontend/src/hooks/`. The load-bearing
ones are `useProcessingState` (`NessieAI/chat_frontend/src/hooks/useProcessingState.ts:164`),
which owns the per-mode stepper table at
`NessieAI/chat_frontend/src/hooks/useProcessingState.ts:11-51` and a separate dynamic mode
for agent turns (`NessieAI/chat_frontend/src/hooks/useProcessingState.ts:258-281`);
`useSessions` (`NessieAI/chat_frontend/src/hooks/useSessions.ts:32`); `useMessages`
(`NessieAI/chat_frontend/src/hooks/useMessages.ts:31`), whose `hydrateFromTurns`
(`NessieAI/chat_frontend/src/hooks/useMessages.ts:66`) replays a stored conversation; and
`useChatRoute` (`NessieAI/chat_frontend/src/hooks/useChatRoute.ts:31`).

Three admin-only controls persist to `localStorage` and are read at send time:
route override (`NessieAI/chat_frontend/src/lib/forceRoute.ts:6-8`), production-database
toggle (`NessieAI/chat_frontend/src/lib/useProd.ts:7`) and per-turn wall clock
(`NessieAI/chat_frontend/src/lib/maxTurnLength.ts:7`). They render only for admins
(`NessieAI/chat_frontend/src/components/Layout/RouteOverrideSelect.tsx:17`,
`NessieAI/chat_frontend/src/components/Layout/ProdToggle.tsx:14`,
`NessieAI/chat_frontend/src/components/Layout/MaxTurnLengthInput.tsx:16`) and are mounted
together in the Debug sheet (`NessieAI/chat_frontend/src/components/Layout/RightSidebar.tsx:52-54`).

Components sit in six directories under `NessieAI/chat_frontend/src/components/`, beside
a seventh holding shared tests: `ChatPanel/` (the transcript, composer, stepper,
artifact list and upload control,
`NessieAI/chat_frontend/src/components/ChatPanel/ChatPanel.tsx:18`), `Layout/`
(`NessieAI/chat_frontend/src/components/Layout/CompactToolbar.tsx:9`), `Sessions/`
(`NessieAI/chat_frontend/src/components/Sessions/SessionSidebar.tsx:17`), `DebugPanel/`
(`NessieAI/chat_frontend/src/components/DebugPanel/DebugPanel.tsx:15`), `TestRunner/`
(`NessieAI/chat_frontend/src/components/TestRunner/TestCaseList.tsx:11`), and `ui/`, which
holds 13 shadcn primitives.

Two pieces of pure logic carry real domain knowledge: the remark plugin that
turns bare sample and SOP identifiers into links
(`NessieAI/chat_frontend/src/lib/remark-uid-links.ts:19-20`), and the terminal-event
session adoption at `NessieAI/chat_frontend/src/lib/sessionAdoption.ts:26-32`.

## Running and testing

The tests stay in this package, because Node resolves imports from the importing file
and `NessieAI/chat_frontend/package.json` is the only `node_modules` root. The commands
for the unit, build and browser lanes are in `NessieAI/tests/README.md` ("Chat panel
unit", "Chat panel build", "Chat panel browser"); all of them run from this directory
after `npm ci`.

**Unit lane: vitest, jsdom, no backend.** `npm run test`
(`NessieAI/chat_frontend/package.json:12`) over the include glob at
`NessieAI/chat_frontend/vitest.config.ts:16`. This is the lane to run before any commit.

**Build lane.** `npm run build:embedded` type-checks with a project-references
build and then bundles (`NessieAI/chat_frontend/package.json:9`). On an unchanged source
tree the emitted files are byte-identical to the ones committed under
`static/js/chat_assistant/assets/`, content hashes included, which is the quick check that
the committed bundle matches its source.

**Lint.** `npm run lint` (`NessieAI/chat_frontend/package.json:10`) over the flat
config at `NessieAI/chat_frontend/eslint.config.js:8-9` exits 1 on pre-existing errors,
and the build does not depend on it (`NessieAI/chat_frontend/CLAUDE.md`).

**Browser lanes: Playwright.** `npm run test:e2e`
(`NessieAI/chat_frontend/package.json:15`). The default `mock` project stubs the REST and
WebSocket surfaces (`NessieAI/chat_frontend/e2e/fixtures/ws-mock.ts:1-6`) and excludes the
real-backend directory (`NessieAI/chat_frontend/playwright.config.ts:42`); the two
real-backend projects appear only when an environment flag is set
(`NessieAI/chat_frontend/playwright.config.ts:44-68`) and each spec self-skips otherwise
(`NessieAI/chat_frontend/e2e/real-backend/test-case-1-embedded.spec.ts:4`). The mock
project needs Playwright's browser binaries downloaded, and the real-backend projects
additionally need a reachable deployed instance with a login that works.

The mock project needs no `.env`. The standalone shell keeps its chat input
disabled until `VITE_API_BASE_URL`, `VITE_API_USER` and `VITE_API_PASS` are all
set (`NessieAI/chat_frontend/src/hooks/useAuth.ts`), so
`NessieAI/chat_frontend/playwright.config.ts` starts the dev server for it with
placeholder values (`http://mock.invalid`, `mock`, `mock`). Every request is mocked, so
no real value is needed, and these override a real `.env` for the run. A dev server
already listening on port 5173 is reused as it was started, so stop yours first or give
it the same three values. The two test-runner specs skip themselves under this project.

**Coverage.** The merge recipe at `NessieAI/chat_frontend/Makefile:3-8` combines both
Playwright and vitest output, so it has the same browser-binary requirement.

## Depends on / depended on by

### Depends on

- Its own npm dependency set, declared at `NessieAI/chat_frontend/package.json:18-66`,
  resolved through the lockfile format fixed at
  `NessieAI/chat_frontend/package-lock.json:4`, and installed into a tree that is ignored
  at `NessieAI/chat_frontend/.gitignore:1`.
- A set of Django HTTP endpoints rather than a set of modules. The turn is
  submitted to the two-engine route at
  `NessieAI/chat_frontend/src/lib/services/chatApi.ts:78`, progress is polled from the
  older assistant route at `NessieAI/chat_frontend/src/lib/services/chatApi.ts:181`,
  admin status comes from `NessieAI/chat_frontend/src/EmbeddedApp.tsx:89`, and sessions,
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
proves nothing. A recursive grep of the whole checkout for the literal string
`chat_frontend`, with `node_modules` and the git directory excluded, finds prose
mentions, paths inside JSON or Markdown inventories, and path strings in Python, and
not one import statement. The real chain is:

- `NessieAI/chat_frontend/vite.config.embedded.ts:14` writes the assets into the repo's
  own `static/` tree, and `NessieAI/chat_frontend/vite.config.embedded.ts:16` sets the
  manifest that makes the hashed names discoverable.
- Those emitted files are tracked, not ignored: no line in `.gitignore` or in
  `NessieAI/chat_frontend/.gitignore` names `static`, `js` or `chat_assistant`, the only
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
  deploy row written around that arrangement is in `DEPLOYMENT.md` §3.2.

Consumers of the running UI, as opposed to the source:

- `ci/smoke/test_flows.py:105-121` loads the page, asserts exactly one bundle
  script tag whose name matches the emitted entry, and proves the bundle is
  executing by checking the composer hydrated from the query parameter.
- `ci/smoke/test_flows.py:128-142` intercepts the outgoing request and asserts
  the body this client sends.

Excluded from the list above, on the basis that they are references to paths
rather than dependencies on behaviour: the per-file path inventories in
`NessieAI/history/plan018/evidence/plan018-v4-9-owned-surface.json:1062-1067` and
`NessieAI/tests/nessie_tests/FAMILIES.json:3703-3708`, the ownership prefix at
`NessieAI/history/plan018/scripts/plan018_v4_9_owned_surface.py:216` (both frozen with
plan018), the build-context assertion naming this directory's example env file at
`nextseek_api/tests/repo_guards/test_build_context_env_guard.py:107`, and the
superseded plan and review documents under `NessieAI/history/cc/archive/`.

Prose descriptions of this boundary live at `docs/UI.md` "Architecture Overview",
`NessieAI/docs/architecture.md` "Front door: page, auth, submit, progress" and `DEPLOYMENT.md` §3.2.

See `NessieAI/chat_frontend/CLAUDE.md` for the invariants this arrangement rests on and
the traps in it.
