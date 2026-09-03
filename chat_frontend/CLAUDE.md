# Working in `chat_frontend/`

## Invariants

Break one of these and the failure is silent: a page that renders, returns HTTP
200, and does the wrong thing.

- **The committed bundle is the deliverable and the source is only its input.**
  The image ships whatever the embedded build last wrote to the path set at
  `chat_frontend/vite.config.embedded.ts:14`, because the root Dockerfile
  installs no JavaScript toolchain and runs no bundler — grepping its 28 lines
  for `npm`, `node` and `vite` returns nothing, which is why the deploy table at
  `DEPLOYMENT.md:286` makes committing the emitted assets part of the step.
  Editing TSX without re-running that build produces a deploy carrying your
  change in source and not in the browser.
- **Both entry points must keep working, and only one of them ships.** The
  embedded bundle is built from a single rollup input
  (`chat_frontend/vite.config.embedded.ts:18`) and Django loads that entry and no
  other (`seek/templates/smartSearch.html:8`), so a change confined to the
  standalone shell leaves the deployed UI untouched.
- **Any change to a progress handler goes into both shells.** The switch at
  `chat_frontend/src/EmbeddedApp.tsx:97-172` and its twin at
  `chat_frontend/src/AppLayout.tsx:92-170` are maintained by hand, and they have
  already drifted: the same adoption call reads the session id from the service
  instance in one (`chat_frontend/src/EmbeddedApp.tsx:155`) and from a hook
  accessor in the other (`chat_frontend/src/AppLayout.tsx:153`). The post-mortem
  at `chat_frontend/src/lib/sessionAdoption.ts:8-18` records the defect that
  shipped when three of four such sites were updated and one was not.
- **Both terminal events adopt the backend session, not just the successful
  one.** `chat_frontend/src/EmbeddedApp.tsx:155` and
  `chat_frontend/src/EmbeddedApp.tsx:169` both call it, and the comment at
  `chat_frontend/src/EmbeddedApp.tsx:165-168` states what dropping the error-path
  call costs: the next send asks for another new chat and a second empty session
  appears in the sidebar.
- **The client's admin gating is cosmetic and the server's is real.** The three
  overrides are withheld from non-admins in the shell
  (`chat_frontend/src/EmbeddedApp.tsx:194-196`) and the controls refuse to render
  (`chat_frontend/src/components/Layout/RouteOverrideSelect.tsx:17`), but the
  authority is `nextseek_api/services/cc_assistant.py:372-374` for the route
  override and `nextseek_api/services/cc_assistant.py:518-519` for the turn
  clock. Never move a gate from the server into a component.
- **The progress transport must keep its fallback.** The WebSocket attempt is
  wrapped so that a failure to open drops to the two-second poll
  (`chat_frontend/src/lib/services/chatApi.ts:114-117`), and the deployment can
  legitimately run a WSGI server that cannot complete the handshake at all
  (`docker/scripts/entrypoint.sh:58-60`). Removing that catch makes every turn
  hang forever on such an instance.
- **The embedded stylesheet must not pull in Tailwind's preflight.**
  `chat_frontend/src/index.embedded.css:1-2` imports only the theme and the
  utilities, where the standalone sheet takes the whole framework
  (`chat_frontend/src/index.css:1`), and every token is scoped to the mount node
  for the reason given at `chat_frontend/src/index.embedded.css:129-131`. Widening
  that import injects a global CSS reset into the surrounding Mezzanine page.
- **The mount id and the basename meta tag are a contract with a template you
  cannot see from here.** They are fixed at
  `chat_frontend/src/main.embedded.tsx:5` and
  `chat_frontend/src/hooks/useChatRoute.ts:4`, and satisfied at
  `seek/templates/smartSearch.html:7` and `seek/templates/smartSearch.html:4`.
  Renaming the id yields an unmounted app; dropping the meta tag silently
  reroots every deep link at the site root, because the reader falls back to a
  single slash (`chat_frontend/src/hooks/useChatRoute.ts:5`).

## Landmines

- **Every UI change is two commits, source then rebuilt bundle.** Commit only the
  source and nothing users load changes, because nothing rebuilds it for you.
  Commit only the bundle and the next build from unchanged source overwrites your
  work, because the output directory is wiped before each run
  (`chat_frontend/vite.config.embedded.ts:15`). `DEPLOYMENT.md:286` folds the two
  halves into one deploy step for exactly this reason.
- **The two Vite scripts are not interchangeable and the wrong one looks
  successful.** `chat_frontend/package.json:8` emits into a directory that is
  ignored at `chat_frontend/.gitignore:2` and that nothing serves; only
  `chat_frontend/package.json:9` writes where the site reads
  (`chat_frontend/vite.config.embedded.ts:14`).
- **A missing or stale manifest entry renders nothing and reports no error.**
  `seek/templatetags/vite_assets.py:56-61` returns an empty string outside debug
  mode, so the page is an empty div at HTTP 200 with a clean console — the exact
  failure the assertion at `ci/smoke/test_flows.py:109-113` exists to catch.
- **The manifest is cached per process and only invalidated in debug mode**
  (`seek/templatetags/vite_assets.py:25-26`). A rebuilt bundle plus
  `collectstatic` is not enough on a long-running server: the old hashed
  filenames keep being emitted until the process restarts.
- **Three files must reach the static root, and only two are in the manifest.**
  `static/js/chat_assistant/.vite/manifest.json:7-16` names the entry script and
  its stylesheet; the SheetJS chunk is pulled at call time from the prefix at
  `chat_frontend/vite.config.embedded.ts:21`, so a partial copy fails only when a
  user clicks, never at page load.
- **That SheetJS chunk is dead weight, and it is the largest of the three
  assets.** It is named at `static/js/chat_assistant/.vite/manifest.json:2-3` and
  measured 429 kB on 2026-09-03. Grepping `chat_frontend/src` and
  `chat_frontend/e2e` for an `xlsx` import finds one, the dynamic call at
  `chat_frontend/src/lib/services/chatApi.ts:373`, and it sits inside a method
  nothing invokes: the same grep across those two trees and the two top-level
  `.mjs` scripts for `downloadSearchAsExcel` returns a single hit, the definition
  itself at `chat_frontend/src/lib/services/chatApi.ts:369`. The spreadsheet
  button users can actually see asks the server instead
  (`chat_frontend/src/components/ChatPanel/ReportArtifacts.tsx:232-243`).
- **The Basic-auth client is compiled into the embedded bundle even though the
  embedded shell never uses it.** `chat_frontend/src/EmbeddedApp.tsx:2` imports
  through the barrel, which re-exports two hooks that reach the module-scope
  singleton (`chat_frontend/src/hooks/index.ts:1`,
  `chat_frontend/src/hooks/index.ts:4`,
  `chat_frontend/src/lib/services/auth.ts:59`). Measured 2026-09-03: the shipped
  entry chunk contains that class, and importing those hooks by path rather than
  through the barrel removes it entirely. Its three constructor defaults are
  build-time substitutions (`chat_frontend/src/lib/services/auth.ts:10-12`), and
  the shipped chunk carries empty-string literals where they stood, which is that
  substitution happening with no values set. Run the embedded build on a machine
  whose env file is filled in and a plaintext username and password are inlined
  into a file served to every browser and committed to this repository.
- **`tailwind.config.js` is loaded by nothing in the build.** Measured
  2026-09-03: adding a font family and changing a breakpoint in it, then
  rebuilding, produced a byte-identical stylesheet with the same content hash. It
  declares itself CommonJS at `chat_frontend/tailwind.config.js:2` inside a
  package marked as ESM at `chat_frontend/package.json:5`, no stylesheet under
  `chat_frontend/src` carries an `@config` directive, and the only reference to
  its name anywhere in this directory is the shadcn CLI's own metadata at
  `chat_frontend/components.json:7`. Edit it and nothing changes.
- **Three real-backend harnesses point at a URL that no longer exists.**
  `chat_frontend/e2e/real-backend/test-case-1-embedded.spec.ts:7` and
  `chat_frontend/test-artifact-e2e.mjs:11` target a `/seek/salt/` path, and
  grepping `seek/urls.py` for `salt` returns zero hits, so no route under that
  prefix serves it; the chat page is registered at `seek/urls.py:13` instead.
  They will fail on navigation, not on an assertion.
- **`chat_frontend/verify.mjs:16-17` has a username and password written into
  it**, aimed at the named host at `chat_frontend/verify.mjs:3`. Treat that pair
  as compromised rather than as a fixture, and never add another.
- **Links this UI generates for protocol identifiers have no route behind them.**
  `chat_frontend/src/lib/remark-uid-links.ts:12` builds a `sop/uid=` path, but
  the only pattern beginning with `sop` in `seek/urls.py` is the query page at
  `seek/urls.py:93`; sample identifiers are fine, resolving to `seek/urls.py:37`.
  Clicking one of those links is a 404 for the user and a passing test for you,
  since `chat_frontend/src/lib/__tests__/remark-uid-links.test.ts:104` asserts the
  string and not the route.
- **The lint script does not pass.** `chat_frontend/package.json:10` runs it
  over everything; measured 2026-09-03 it reported 9 errors and 2 warnings and
  exited 1, across ten files. Do not treat a clean lint as a merge gate here, and
  do not "fix" the whole file set inside a change that is meant to be small.
- **`chat_frontend/reports/` is nine committed screenshots plus two hand-built
  HTML pages that nothing reads** — a recursive grep of the worktree for the
  string `chat_frontend/reports`, excluding that directory itself, returns no
  hits at all. It is a snapshot of a past run, and it still records the dead URL
  above at `chat_frontend/reports/test-report.html:406`.
- **The Playwright web-server ternary has identical branches**
  (`chat_frontend/playwright.config.ts:54`), so the local dev server declared at
  `chat_frontend/playwright.config.ts:5-9` is started and awaited on port 5173
  even for the remote-target project at
  `chat_frontend/playwright.config.ts:41-49`, which never visits it. A machine
  that cannot bring that server up cannot run the remote project either.
- **The unit lane only sees `src/`.** The include glob is
  `chat_frontend/vitest.config.ts:16`; a test placed under `chat_frontend/e2e/`
  is silently outside it and runs in neither lane unless Playwright is invoked.
- **Do not reason about this UI from a live instance.** A deployment is cloned
  from a named branch (`DEPLOYMENT.md:126-127`) that can be behind this checkout,
  and what it serves is a bundle committed at that point, so a behaviour observed
  there is evidence about that commit and not about the source you are editing.

## Test command

```
cd chat_frontend && npm ci && npm run test
```

On 2026-09-03 this took 4.10 seconds and reported 165 passed across 27 files,
with nothing failing; the install step added 563 packages beforehand. Run it
from this directory, never from the repository root: the entire root pytest
configuration is the two keys at `pyproject.toml:146-148`, which collect Python
files and nothing else. Three Python test modules reach this boundary at all,
and none of them tests its behaviour: two assert about its file paths
(`nextseek_api/cc_assistant/tests/test_build_context_env_guard.py:103` and
`scripts/test_plan018_v4_9_owned_surface.py:133`) and one drives the built page
in a browser (`ci/smoke/test_flows.py:105-121`). Grepping every Python file in
the worktree for `chat_frontend` and for `chat_assistant` turns up no others.

Before handing work over, also run the embedded build. Types are checked only by
the two build scripts, which each begin with a project-references compile
(`chat_frontend/package.json:8-9`); the unit lane does not type-check at all.

## See also

- See `chat_frontend/README.md` for the surface, the two entry points, every
  lane including the ones that were not run, and the full dependency chain in
  both directions.
- See `seek/templatetags/vite_assets.py:1-9` for the tag's own usage note.
- See `DEPLOYMENT.md:286` for where this fits in the deploy runbook, and
  `DEPLOYMENT.md:285` for the static-asset step it depends on.
- See `architecture.md:56-75` for the whole turn, from page load to progress
  transport.
- See `UI.md:37-78` for how this sits beside the server-rendered pages.
- See the repository root `CLAUDE.md` for the router, the two engines and the
  sticky-route rule that decides what these progress events describe.
