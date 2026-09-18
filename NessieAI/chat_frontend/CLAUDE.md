# Working in `NessieAI/chat_frontend/`

## Invariants

Break one of these and the failure is silent: a page that renders, returns HTTP
200, and does the wrong thing.

- **The committed bundle is the deliverable and the source is only its input.**
  The image ships whatever the embedded build last wrote to the path set at
  `NessieAI/chat_frontend/vite.config.embedded.ts:14`, because the root Dockerfile
  installs no JavaScript toolchain and runs no bundler (grepping it for `npm`,
  `node` and `vite` returns nothing), which is why the deploy table in
  `DEPLOYMENT.md` §3.2 makes committing the emitted assets part of the step.
  Editing TSX without re-running that build produces a deploy carrying your
  change in source and not in the browser.
- **Both entry points must keep working, and only one of them ships.** The
  embedded bundle is built from a single rollup input
  (`NessieAI/chat_frontend/vite.config.embedded.ts:18`) and Django loads that entry and no
  other (`seek/templates/smartSearch.html:8`), so a change confined to the
  standalone shell leaves the deployed UI untouched.
- **Any change to a progress handler goes into both shells.** The switch at
  `NessieAI/chat_frontend/src/EmbeddedApp.tsx:97-172` and its twin at
  `NessieAI/chat_frontend/src/AppLayout.tsx:92-170` are maintained by hand, and they have
  already drifted: the same adoption call reads the session id from the service
  instance in one (`NessieAI/chat_frontend/src/EmbeddedApp.tsx:155`) and from a hook
  accessor in the other (`NessieAI/chat_frontend/src/AppLayout.tsx:153`). The post-mortem
  at `NessieAI/chat_frontend/src/lib/sessionAdoption.ts:8-18` records the defect that
  shipped when three of four such sites were updated and one was not.
- **Both terminal events adopt the backend session, not just the successful
  one.** `NessieAI/chat_frontend/src/EmbeddedApp.tsx:155` and
  `NessieAI/chat_frontend/src/EmbeddedApp.tsx:169` both call it, and the comment at
  `NessieAI/chat_frontend/src/EmbeddedApp.tsx:165-168` states what dropping the error-path
  call costs: the next send asks for another new chat and a second empty session
  appears in the sidebar.
- **The client's admin gating is cosmetic and the server's is real.** The three
  overrides are withheld from non-admins in the shell
  (`NessieAI/chat_frontend/src/EmbeddedApp.tsx:194-196`) and the controls refuse to render
  (`NessieAI/chat_frontend/src/components/Layout/RouteOverrideSelect.tsx:17`), but the
  authority is `NessieAI/router/policy.py:104-112` for the route
  override and `NessieAI/cc/turn.py:302-303` for the turn
  clock. Never move a gate from the server into a component.
- **The progress transport must keep its fallback.** The WebSocket attempt is
  wrapped so that a failure to open drops to the two-second poll
  (`NessieAI/chat_frontend/src/lib/services/chatApi.ts:114-117`), and the deployment can
  legitimately run a WSGI server that cannot complete the handshake at all
  (`docker/scripts/entrypoint.sh:58-60`). Removing that catch makes every turn
  hang forever on such an instance. A socket that opens and then drops before the
  turn's final event hands over to the same poll, which resumes after the events the
  socket already delivered; once it has handed over the socket must deliver nothing
  more, or the answer reaches the user twice.
- **The embedded stylesheet must not pull in Tailwind's preflight.**
  `NessieAI/chat_frontend/src/index.embedded.css:1-2` imports only the theme and the
  utilities, where the standalone sheet takes the whole framework
  (`NessieAI/chat_frontend/src/index.css:1`), and every token is scoped to the mount node
  for the reason given at `NessieAI/chat_frontend/src/index.embedded.css:129-131`. Widening
  that import injects a global CSS reset into the surrounding Mezzanine page.
- **The mount id and the basename meta tag are a contract with a template you
  cannot see from here.** They are fixed at
  `NessieAI/chat_frontend/src/main.embedded.tsx:5` and
  `NessieAI/chat_frontend/src/hooks/useChatRoute.ts:4`, and satisfied at
  `seek/templates/smartSearch.html:7` and `seek/templates/smartSearch.html:4`.
  Renaming the id yields an unmounted app; dropping the meta tag silently
  reroots every deep link at the site root, because the reader falls back to a
  single slash (`NessieAI/chat_frontend/src/hooks/useChatRoute.ts:5`).
- **Never write a credential into a file here.** A committed script once carried a
  username and password; it was deleted, but the pair stays in public git history,
  so treat any such pair as compromised rather than as a fixture.
- **The `data-testid` values are a contract with the Nessie CI lane.**
  `ci/smoke/test_nessie.py` drives the built page by them (`chat-input`, `send-button`,
  `new-chat-button`, `upload-control`, `debug-panel`, `debug-entry` with `data-agent`,
  `json-download`, `metadata-download`, `message-bubble` with `data-role`,
  `artifact-download`, `session-item` with `data-session-id`), plus the `#route-override`
  id and the "Toggle debug panel" and "Saved chats" labels.
  `NessieAI/chat_frontend/src/components/__tests__/testIds.test.tsx` pins the ones the
  lane cannot reach without a bundle. Renaming one is a red lane on the next rebuild,
  not a compile error.

## Landmines

- **Every UI change is two commits, source then rebuilt bundle.** Commit only the
  source and nothing users load changes, because nothing rebuilds it for you.
  Commit only the bundle and the next build from unchanged source overwrites your
  work, because the output directory is wiped before each run
  (`NessieAI/chat_frontend/vite.config.embedded.ts:15`). `DEPLOYMENT.md` §3.2 folds the two
  halves into one deploy step for exactly this reason.
- **The two Vite scripts are not interchangeable and the wrong one looks
  successful.** `NessieAI/chat_frontend/package.json:8` emits into a directory that is
  ignored at `NessieAI/chat_frontend/.gitignore:2` and that nothing serves; only
  `NessieAI/chat_frontend/package.json:9` writes where the site reads
  (`NessieAI/chat_frontend/vite.config.embedded.ts:14`).
- **A missing or stale manifest entry renders nothing and reports no error.**
  `seek/templatetags/vite_assets.py:56-61` returns an empty string outside debug
  mode, so the page is an empty div at HTTP 200 with a clean console: the exact
  failure the assertion at `ci/smoke/test_flows.py:109-113` exists to catch.
- **The manifest is cached per process and only invalidated in debug mode**
  (`seek/templatetags/vite_assets.py:25-26`). A rebuilt bundle plus
  `collectstatic` is not enough on a long-running server: the old hashed
  filenames keep being emitted until the process restarts.
- **Three files must reach the static root, and only two are in the manifest.**
  `static/js/chat_assistant/.vite/manifest.json:7-16` names the entry script and
  its stylesheet; the SheetJS chunk is pulled at call time from the prefix at
  `NessieAI/chat_frontend/vite.config.embedded.ts:21`, so a partial copy fails only when a
  user clicks, never at page load.
- **That SheetJS chunk is dead weight, and it is the largest of the three
  assets.** It is named at `static/js/chat_assistant/.vite/manifest.json:2-3`.
  Grepping `NessieAI/chat_frontend/src` and `NessieAI/chat_frontend/e2e` for an `xlsx`
  import finds one, the dynamic call at
  `NessieAI/chat_frontend/src/lib/services/chatApi.ts:380`, and it sits inside a method
  nothing invokes: the same grep for `downloadSearchAsExcel` returns a single hit, the
  definition itself at `NessieAI/chat_frontend/src/lib/services/chatApi.ts:376`. The
  spreadsheet button users can actually see asks the server instead
  (`NessieAI/chat_frontend/src/components/ChatPanel/ReportArtifacts.tsx:232-243`).
- **The Basic-auth client is compiled into the embedded bundle even though the
  embedded shell never uses it.** `NessieAI/chat_frontend/src/EmbeddedApp.tsx:2` imports
  through the barrel, which re-exports two hooks that reach the module-scope
  singleton (`NessieAI/chat_frontend/src/hooks/index.ts:1`,
  `NessieAI/chat_frontend/src/hooks/index.ts:4`,
  `NessieAI/chat_frontend/src/lib/services/auth.ts:59`). The shipped entry chunk contains
  that class, and importing those hooks by path rather than through the barrel removes
  it entirely. Its three constructor defaults are build-time substitutions
  (`NessieAI/chat_frontend/src/lib/services/auth.ts:10-12`), and the shipped chunk carries
  empty-string literals where they stood, which is that substitution happening with no
  values set. Run the embedded build on a machine whose env file is filled in and a
  plaintext username and password are inlined into a file served to every browser and
  committed to this repository.
- **`tailwind.config.js` is loaded by nothing in the build.** Adding a font family and
  changing a breakpoint in it, then rebuilding, produces a byte-identical stylesheet
  with the same content hash. It declares itself CommonJS at
  `NessieAI/chat_frontend/tailwind.config.js:2` inside a package marked as ESM at
  `NessieAI/chat_frontend/package.json:5`, no stylesheet under `NessieAI/chat_frontend/src`
  carries an `@config` directive, and the only reference to its name anywhere in this
  directory is the shadcn CLI's own metadata at `NessieAI/chat_frontend/components.json:7`.
  Edit it and nothing changes.
- **The two real-backend specs point at a URL that no longer exists.**
  `NessieAI/chat_frontend/e2e/real-backend/test-case-1-embedded.spec.ts:7` and
  `NessieAI/chat_frontend/e2e/real-backend/artifact-downloads-embedded.spec.ts:6` target a
  `/seek/salt/` path, and grepping `seek/urls.py` for `salt` returns zero hits, so no
  route under that prefix serves it; the chat page is registered at `seek/urls.py:13`
  instead. They fail on navigation, not on an assertion.
- **Links this UI generates for protocol identifiers have no route behind them.**
  `NessieAI/chat_frontend/src/lib/remark-uid-links.ts:12` builds a `sop/uid=` path, but
  the only pattern beginning with `sop` in `seek/urls.py` is the query page at
  `seek/urls.py:93`; sample identifiers are fine, resolving to `seek/urls.py:37`.
  Clicking one of those links is a 404 for the user and a passing test for you,
  since `NessieAI/chat_frontend/src/lib/__tests__/remark-uid-links.test.ts:104` asserts the
  string and not the route.
- **The lint script does not pass.** `NessieAI/chat_frontend/package.json:10` runs it
  over everything and exits 1 on pre-existing errors across several files. Do not treat
  a clean lint as a merge gate here, and do not "fix" the whole file set inside a
  change that is meant to be small.
- **Both branches of the Playwright web-server ternary start the local dev
  server** (`NessieAI/chat_frontend/playwright.config.ts:70`): the mock project gets it
  with placeholder credentials and the real-backend projects get it plain. So
  the dev server declared at `NessieAI/chat_frontend/playwright.config.ts:5-9` is started
  and awaited on port 5173 even for the remote-target project at
  `NessieAI/chat_frontend/playwright.config.ts:57-65`, which never visits it. A machine
  that cannot bring that server up cannot run the remote project either.
- **The unit lane only sees `src/`.** The include glob is
  `NessieAI/chat_frontend/vitest.config.ts:16`; a test placed under `NessieAI/chat_frontend/e2e/`
  is silently outside it and runs in neither lane unless Playwright is invoked.
- **Do not reason about this UI from a live instance.** A deployment is cloned
  from a named branch (`DEPLOYMENT.md` §3) that can be behind this checkout,
  and what it serves is a bundle committed at that point, so a behaviour observed
  there is evidence about that commit and not about the source you are editing.

## Test command

See `NessieAI/tests/README.md` ("Chat panel unit", "Chat panel build", "Chat panel
browser"). Run them from this directory, never from the repository root: the root pytest
configuration (`pyproject.toml:146-148`) collects Python files and nothing else.

Before handing work over, also run the embedded build. Types are checked only by
the two build scripts, which each begin with a project-references compile
(`NessieAI/chat_frontend/package.json:8-9`); the unit lane does not type-check at all.

Two Python test modules reach this boundary, and neither tests its behaviour: one
asserts about its example env file
(`nextseek_api/tests/repo_guards/test_build_context_env_guard.py:107`), and one drives
the built page in a browser (`ci/smoke/test_flows.py:105-121`).

## See also

- See `NessieAI/chat_frontend/README.md` for the surface, the two entry points, every
  lane, and the full dependency chain in both directions.
- See `seek/templatetags/vite_assets.py:1-9` for the tag's own usage note.
- See `DEPLOYMENT.md` §3.2 for where this fits in the deploy runbook, including the
  `collectstatic` step it depends on.
- See `NessieAI/docs/architecture.md` "Anatomy of a turn" for the whole turn, from page load to progress
  transport.
- See `docs/UI.md` "Architecture Overview" for how this sits beside the server-rendered pages.
- See `NessieAI/router/README.md` for the route rules (overrides and sticky CC) that
  decide what these progress events describe.
