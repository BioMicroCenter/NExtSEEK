# Chat frontend (Nessie chat UI)

## What this covers

The React app in `NessieAI/chat_frontend/` as the rest of the site sees it: how the Django page
mounts it, how it talks to the backend, which component owns which part of the screen, how it is
styled next to Bootstrap, how phones behave, and the rule that the built bundle is committed.

It does not cover what Nessie answers or how turns are routed (see `NessieAI/README.md`), the
Django sidebar shell around the page (see [shell.md](shell.md)), the Bootstrap and theme CSS (see
[styles.md](styles.md)), or how a rebuild reaches a box (see [ci-and-deploy.md](ci-and-deploy.md)).
Deep internals (every hook, every endpoint, test recipes) stay in
[`NessieAI/chat_frontend/README.md`](../../NessieAI/chat_frontend/README.md) and
[`NessieAI/chat_frontend/CLAUDE.md`](../../NessieAI/chat_frontend/CLAUDE.md), which also lists
the invariants that fail silently. Read those before a non-trivial change.

## How it works

The chat is a full page, not a floating panel. `/seek/assistant/` (`seek/urls.py`, route name
`assistant`, view `smartSearch` in `seek/views/search.py`; signed-in users only, and an inline check renders `error.html` rather than redirecting to login) renders
`seek/templates/smartSearch.html`. That template extends `base.html` and, in `{% block main %}`,
emits one empty `div#chat-assistant-root` (inline style `height: calc(100vh - 60px)`) plus the
`{% vite_assets "src/main.embedded.tsx" "js/chat_assistant" %}` tag. React then fills the div. The
page has no close control; it lives inside the normal sidebar layout.

```
base.html (sidebar + #main-wrapper + <main id="content" class="content"> + footer)
  smartSearch.html
    <meta name="chat-basename" content="/seek/assistant/">   (block extra_head)
    <div id="chat-assistant-root">                            (block main)
    {% vite_assets %}  -> reads static/js/chat_assistant/.vite/manifest.json
                          -> <link rel=stylesheet> + <script type=module>
  main.embedded.tsx -> createRoot(#chat-assistant-root) -> EmbeddedApp.tsx
```

What Django hands to the app is small:

| Channel | Detail |
|---|---|
| Meta tag `chat-basename` | read by `src/hooks/useChatRoute.ts`; used for `chat/<uuid>` deep links with `pushState` (no router library) |
| Mount id `chat-assistant-root` | looked up in `src/main.embedded.tsx`; also the scope of the CSS tokens in `src/index.embedded.css` |
| CSRF | `src/lib/services/sessionAuth.ts` (`SessionAuthService`) reads the `csrftoken` cookie and sends `X-CSRFToken`; all URLs are same-origin and relative; the WebSocket base comes from `window.location` |
| Identity | `GET /nextseek_api/assistant/me/` from `EmbeddedApp.tsx` (only the admin flag is used; it gates the admin controls, the server enforces the real check) |
| `?q=` | initial composer text, read in `src/components/ChatPanel/MessageInput.tsx`; `ci/smoke/test_flows.py` relies on it |

There are no `window.*` globals, data attributes or JSON script blobs.

`vite_assets` (`seek/templatetags/vite_assets.py`, function `_load_manifest`) looks up the
manifest in `STATICFILES_DIRS`, i.e. the image's copy of `static/`, not the collected volume. It
caches the manifest per process unless `DEBUG` is on. Because `static/` is baked into the image
(it is not bind-mounted), a new bundle only arrives with `./startup.sh rebuild`, which starts a
fresh process, so the cache matters only if files are ever copied into a running container. It is
not passed through `{% static %}`; Vite's own content hashes in the file names provide cache-busting.

### Transport

1. The composer sends `POST /nextseek_api/cc-assistant/query/async/` (JSON: query, mode, `use_prod`,
   then `session_id` or `force_new`, optional `force_route` and `max_turn_length_s`).
2. The app opens a WebSocket at `/ws/assistant/progress/<task>/` (`src/lib/services/chatApi.ts`,
   class `NextseekApiService`). If the socket cannot be used it falls back to polling
   `GET /nextseek_api/assistant/tasks/<task>/progress/` every 2 s, giving up after 30 minutes
   without a new event (constants `POLL_INTERVAL`, `POLL_SILENCE_LIMIT_MS`).
3. Events carry step progress (`route_decided`, `agent_started`, `agent_complete`, `cc_turn_meta`,
   `search_started`, `search_complete`) and end with `query_complete` or `query_error`. The handler
   switch is in `EmbeddedApp.tsx`.

The reply arrives whole in `query_complete`. There is no token streaming and no stop button; only
the step list (`ProcessingStepper`) moves while a turn runs. Other endpoints (sessions, bundles,
artifact downloads, uploads to Container-CC, whole-chat zip) are all in `chatApi.ts`; the full
list is in the chat README.

### Two shells, one source

| Shell | Entry | Used by |
|---|---|---|
| Embedded | `src/main.embedded.tsx` -> `src/EmbeddedApp.tsx` | what ships; the only entry Django loads |
| Standalone | `src/main.tsx` -> `App.tsx` -> `AppLayout.tsx` (+ `HeaderBar`, `useChatApi`, `useAuth`, Basic auth from `VITE_API_*`) | `npm run dev`, the mock Playwright project and the env-gated `real-standalone` project |

The two shells are hand-kept near-duplicates: the progress-event switch, the send handler and the
download handlers exist twice. A change confined to the standalone shell does not change the
deployed UI. A change to a progress handler must go into both shells (an earlier miss shipped a
real bug, recorded in `src/lib/sessionAdoption.ts`). The standalone dev server has no proxy, so
it only works against a remote `VITE_API_BASE_URL` with CORS.

## Inventory

### Stack

| Part | Version (from `package.json`) |
|---|---|
| React / React DOM | ^19.2 |
| Vite, `@vitejs/plugin-react` | ^7.2, ^5.1 |
| TypeScript | ~5.9 (`strict`, `noUnusedLocals`) |
| Tailwind | ^4.1 via `@tailwindcss/vite` (no `postcss` step needed) |
| UI primitives | shadcn-style files in `src/components/ui/` over 9 Radix packages; `lucide-react` icons |
| Markdown | `react-markdown` ^10, `remark-gfm`, `rehype-highlight` (highlight.js github theme, light only) |
| Spreadsheet | `xlsx` ^0.18.5, bundled as a lazy chunk but never loaded: its only user, `downloadSearchAsExcel` in `chatApi.ts`, has no caller |
| Not present | state library, router, data-fetching library, chart library, table library |

### Component map (all under `NessieAI/chat_frontend/src/`)

| Screen area | Files | Notes |
|---|---|---|
| Top bar (40 px) | `components/Layout/CompactToolbar.tsx` | toggle chat list, About, Debug |
| Saved chats rail | `components/Sessions/SessionSidebar.tsx`, `SessionListItem.tsx`, `NewChatButton.tsx`; hook `hooks/useSessions.ts` | 260 px wide, 48 px collapsed; collapse state in localStorage key `chat.sidebar.collapsed`, read in `EmbeddedApp.tsx` |
| Conversation column | `components/ChatPanel/ChatPanel.tsx` | stepper, message list, composer; wires suggestion chips to send |
| Message list | `ChatPanel/MessageList.tsx`; hook `hooks/useAutoScroll.ts` | empty state, stick-to-bottom scroll |
| One message | `ChatPanel/MessageBubble.tsx` | user and assistant bubbles (`max-w-[80%]`), system notices as a centred italic line, suggestion chips, "Search Details" toggle |
| Markdown body | `ChatPanel/MarkdownContent.tsx`; `lib/remark-uid-links.ts` | GFM tables, code highlighting, UID auto-links |
| Result tables and files | `ChatPanel/ReportArtifacts.tsx`; `chatApi.ts` download helpers | tables only (preview, per-table and "all tables" xlsx download); images and plots cannot be shown inline, only downloaded |
| Container-CC trace | `ChatPanel/CCActivityPanel.tsx` | inside Search Details |
| Live progress | `ChatPanel/ProcessingStepper.tsx`; `hooks/useProcessingState.ts` | per-mode step table |
| Composer | `ChatPanel/MessageInput.tsx`, `UploadControl.tsx` | Enter sends, Shift+Enter newline; upload goes to Container-CC |
| Debug sheet | `Layout/RightSidebar.tsx`, `DebugPanel/DebugPanel.tsx`, `Layout/{RouteOverrideSelect,ProdToggle,MaxTurnLengthInput}.tsx` | admin controls persist in localStorage |
| About dialog | `Layout/AboutDialog.tsx` | a test and the bundle guard quote its text |
| Standalone only | `Layout/HeaderBar.tsx` (dark toggle), `AppLayout.tsx`, `App.tsx` | not shipped |
| Dead | `Layout/LeftSidebar.tsx`, `TestRunner/TestCaseList.tsx` (imported only by the dead `LeftSidebar`), `ui/{card,label,tabs,tooltip}.tsx` | imported by nothing live; safe to delete after a grep |

The `data-testid` values are a contract with `ci/smoke/test_nessie.py`, pinned by
`src/components/__tests__/testIds.test.tsx`. Do not rename them.

### Build files

| File | Role |
|---|---|
| `vite.config.embedded.ts` | embedded build: single input `src/main.embedded.tsx`, `outDir` `../../static/js/chat_assistant`, `emptyOutDir: true`, `manifest: true`, `base` `/static/js/chat_assistant/` |
| `vite.config.ts` | standalone build and dev server, output to gitignored `dist/` |
| `src/index.embedded.css` | shipped CSS (see Styling) |
| `src/index.css` | standalone CSS only |
| `tailwind.config.js` | dead, see Styling |
| `components.json` | shadcn generator config (new-york, slate, CSS variables) |

## Styling

The embedded CSS imports only `tailwindcss/theme` and `tailwindcss/utilities`, not Tailwind's
preflight. That is what stops the chat from resetting Bootstrap for the rest of the page. The
cost runs the other way: inside the chat, Bootstrap's base styles for buttons, inputs, tables,
headings and links apply unless a utility class overrides them. Tailwind utilities are not
prefixed or scoped, so the shipped CSS defines global selectors such as `.border`, `.container`
and `.hidden`. The blast radius is one page, because only `smartSearch.html` loads this CSS.

| Topic | Where and what |
|---|---|
| Tokens | shadcn oklch variables (`--background`, `--primary`, `--border`, `--sidebar-*`, and so on) scoped to `#chat-assistant-root` in `src/index.embedded.css`; font sizes overridden with `clamp()` so the host `html` font size does not matter |
| Theme alignment | the chat palette is slate/oklch shadcn defaults, not the Django `--ns-*` tokens in `themes/NextSeek/static/css/nextseek.css`; accents and borders differ from the page around it. Inter is shared (loaded by `base.html`) |
| Dark mode | dark tokens exist (`#chat-assistant-root.dark` and `@custom-variant dark`), but nothing in the embedded shell ever adds the class. The only toggle is in standalone `HeaderBar.tsx` and it sets the class on `<html>`, the wrong node for the embedded scoping. The embedded chat is always light, and so is the Django theme |
| `tailwind.config.js` | ignored. It is CommonJS in an ES-module package and uses v3 `hsl(var(--x))` wrappers; Tailwind 4 reads it only through `@config`, and nothing in `src/` uses `@config`. Change tokens in the CSS file, not here |
| Code blocks | highlight.js github theme is imported in the CSS and stays light |

Theme and Bootstrap CSS rules for the page frame (the padded `<main class="content">`, the footer)
live in `themes/NextSeek/static/css/nextseek.css`; see [styles.md](styles.md).

## Phone behaviour

There is no responsive behaviour in the shipped shell: no layout responds to width. The only
breakpoint classes are cosmetic ones in the shadcn primitives (`ui/dialog.tsx`, `ui/input.tsx`,
`ui/sheet.tsx`, where the Debug sheet is `w-3/4 sm:max-w-sm`) and in standalone `HeaderBar.tsx`. The
embedded top bar (`CompactToolbar.tsx`) always shows its About and Debug labels. Confirmed on a
phone-sized viewport on 2026-09-30 (see [known-issues.md](known-issues.md#chat-frontend), UI-160).

| Issue | Where | Effect at 390 px |
|---|---|---|
| Fixed rail width | `SessionSidebar.tsx` (`w-[260px]`, expanded by default unless `chat.sidebar.collapsed` is `1`) | The Django sidebar collapses to a drawer below 992 px and `.content` padding drops to 0.875rem at 575 px and below (`nextseek.css` media queries), so the chat root is about 360 px wide. The 260 px rail leaves roughly 100 px for messages and composer until the user taps the toolbar toggle (48 px rail) |
| `100vh` | inline style in `smartSearch.html` | `vh` is the large viewport on mobile browsers, so the URL bar and soft keyboard are not subtracted and the composer can sit off screen. There is no `dvh`, `visualViewport` or safe-area handling |
| Double scroll | same inline height inside padded `<main>` plus the footer | the root is viewport height minus 60 px, then padding and footer add more, so the page scrolls as well as the chat (also on desktop; not checked in a browser) |
| 80% widths | bubbles, chips, Search Details in `MessageBubble.tsx`; upload list `max-w-[200px]` in `UploadControl.tsx` | fine on desktop, tight beside the rail |

A fix starts with a default-collapsed or overlay rail under about 768 px, `100dvh` on the root,
and a flex chain with `min-height: 0` (or a full-bleed page class) in `smartSearch.html`. There
is no mobile-viewport Playwright project to catch regressions.

## Build and commit rule

The Dockerfile has no Node stage: it installs no JavaScript toolchain and runs no bundler. The
image ships whatever is committed in `static/js/chat_assistant/` (hashed `main.embedded-*.js`, a
lazy `xlsx-*.js` chunk, `main-*.css`, and `.vite/manifest.json`, about 1.2 MB). A source change
without a rebuilt bundle deploys fine, renders fine, and shows the old UI.

```
cd NessieAI/chat_frontend
npm ci
npm run build:embedded      # tsc -b --noEmit, then vite build --config vite.config.embedded.ts
cd ../..                    # back to the repo root for git
git add NessieAI/chat_frontend/src/<changed files>   # commit 1: the source
git add static/js/chat_assistant/                    # commit 2: the rebuilt bundle (stages deletions too)
```

`emptyOutDir` wipes the old hashed files, so commit deletions too. The type check runs first and
blocks the build on type errors; lint is not part of it (`npm run lint`, the README notes it exits
1 on existing errors). The owner rule (`NessieAI/chat_frontend/CLAUDE.md` "Landmines") is two
commits, source then rebuilt bundle, and both must be pushed before any rebuild. Then rebuild the
app on the box (see [ci-and-deploy.md](ci-and-deploy.md) and the deploy table in `DEPLOYMENT.md`);
the rebuild recreates the container, which collects static at start.

Checks that guard it (all weak; none builds the bundle):

| Check | What it proves |
|---|---|
| `NessieAI/tests/build_tools/unit/test_committed_chat_bundle.py` | the manifest's entry file contains two specific recent source strings (the About dialog's "tries a second one" line and the Debug panel's `detail=` line). It misses any other source change, and it can only be satisfied by new strings if those two are edited later, so extend it when you add a guarded string |
| `ci/smoke/test_deploy_live.py` | on a live box: the served chat bundle bytes equal the committed files, and the chat page names the committed entry file |
| `ci/smoke/test_flows.py` | the Nessie page loads one bundle script tag, `?q=` hydrates the composer, and a send issues the request |

## Where to edit

Every row ends with the build-and-commit step above, unless the row is Django-side only.

| Task | Files and symbols | Easy to miss |
|---|---|---|
| Bubble colour, width, font | `ChatPanel/MessageBubble.tsx`; tokens and font scale in `src/index.embedded.css` | tokens are scoped to `#chat-assistant-root`; `tailwind.config.js` does nothing |
| Markdown, tables, code, links | `ChatPanel/MarkdownContent.tsx`, `lib/remark-uid-links.ts`, highlight import in the CSS | Bootstrap base styles leak in because preflight is off; check tables and links in the page, not just in dev |
| Result tables and downloads | `ChatPanel/ReportArtifacts.tsx`, download helpers in `chatApi.ts` | tables only; a new kind (image, plot) needs a new member of the `Artifact` union in `lib/types/chat.ts` and a renderer |
| Composer placeholder, keys, `?q=` | `ChatPanel/MessageInput.tsx`, `UploadControl.tsx` | smoke tests rely on `?q=` and the test ids |
| Progress step text and icons | `ChatPanel/ProcessingStepper.tsx`, `hooks/useProcessingState.ts` | steps differ per mode |
| Suggestion chips | `MessageBubble.tsx`, send wiring in `ChatPanel.tsx` | only the newest assistant reply shows chips |
| Saved chats rail (width, mobile) | `Sessions/SessionSidebar.tsx`, collapse state in `EmbeddedApp.tsx`, `Layout/CompactToolbar.tsx` | the default-collapsed state must also respect the stored key |
| Debug sheet and admin controls | `Layout/RightSidebar.tsx`, `DebugPanel/DebugPanel.tsx`, the three control files | gating in the client is cosmetic; the server enforces admin-only overrides |
| About text | `Layout/AboutDialog.tsx` | `components/__tests__/AboutDialog.test.tsx` and the bundle guard quote it |
| Page frame, height, padding around the chat | `seek/templates/smartSearch.html` (inline height), `themes/NextSeek/static/css/nextseek.css` (`.content`, `#main-wrapper`) | `seek/templates/` is baked into the image: `./startup.sh rebuild`. Only `themes/NextSeek/` is bind-mounted |
| New endpoint or event | `src/lib/services/chatApi.ts`, `src/lib/types/api.ts`, handler in both `EmbeddedApp.tsx` and `AppLayout.tsx` | add the backend route to `ci/routes.py` if it is a new URL |
| Mount id, basename, script tag | `src/main.embedded.tsx`, `src/hooks/useChatRoute.ts`, `smartSearch.html` | three places must agree |
| Dark mode | toggle on `#chat-assistant-root` (not `<html>`), theme the highlight.js import | the Django theme has no dark mode either, so decide both together |

## Gotchas

- Committed bundle: source edits alone change nothing in the browser. Commit the rebuilt `static/js/chat_assistant/` right after the source, including deleted old hashed files.
- Manifest source: `vite_assets` reads the manifest from the image's `static/`, not the collected volume, so a `collectstatic`-only exec never changes which bundle the page names. Ship a new bundle with a rebuild.
- nginx serves `/static/` with a 30-day `expires`; Vite's hashed names avoid stale bundles, but anything you reference by an unhashed name can stay stale in browsers for a month.
- The standalone shell is a trap: it runs in `npm run dev` and in the mock Playwright project, so passing e2e does not prove the shipped shell works.
- Django's `{% block main %}` holds the mount div; content outside blocks in `smartSearch.html` is dropped.
- Build the bundle with `npm` only (no Docker needed); rebuild the image on a box with enough memory, normally the dev box.
- Stale spots in `NessieAI/chat_frontend/README.md`: the line numbers it cites for `chatApi.ts` and `EmbeddedApp.tsx` have drifted (for example the xlsx dynamic import and the `new WebSocket` call), it says nothing about the missing dark mode, the missing responsive behaviour, the page-height interaction with `smartSearch.html`, the unused `tailwind.config.js`, or that `LeftSidebar` and `TestCaseList` are dead. It describes `TestRunner/` as live. Its claims about what each piece does are otherwise accurate; the `CLAUDE.md` line references to `EmbeddedApp.tsx` have drifted the same way.

## Tests

| Kind | Where | Run |
|---|---|---|
| Unit and component | about 37 `*.test.ts(x)` files under `src/` (vitest, jsdom, Testing Library), setup in `src/test/setup.ts` | `npm test` in `NessieAI/chat_frontend` |
| Browser, mocked | `e2e/*.spec.ts` (chat flow, sessions, debug panel, download, dark mode, UI; the test runner spec is skipped), against the standalone dev server | `npm run test:e2e` |
| Browser, real backend | `e2e/real-backend/` (env-gated, needs a login on a live box) | manual |
| Repo level | `test_committed_chat_bundle.py`, `ci/smoke/test_flows.py`, `ci/smoke/test_deploy_live.py`, `ci/smoke/test_nessie.py` (test ids) | see [ci-and-deploy.md](ci-and-deploy.md) |

Gaps: no mobile-viewport project, no accessibility checks, no visual regression. The dark-mode
spec only tests the standalone toggle. Accessibility holes found by reading source: no live region
for new replies or errors, focus is not returned to the composer after a send (the textarea is
disabled during a turn), and the Search Details toggle has no `aria-expanded`.

## Known issues

See [known-issues.md](known-issues.md#chat-frontend). The ones that matter most:

- No responsive behaviour: the 260 px rail and `100vh` make the chat hard to use on a phone.
- The shipped UI is only what is committed in `static/js/chat_assistant/`, guarded by a check that covers two strings, and the two shells duplicate their event handling by hand.
- Dark tokens ship but are never applied, and unprefixed Tailwind utilities plus no preflight let Bootstrap and the chat CSS affect each other.
