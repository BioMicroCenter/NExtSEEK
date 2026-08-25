# nf-core Launch Path Explainer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single self-contained HTML page that explains how a chat message becomes a job on Luria, marking every deliberate refusal and every place the system can be quietly wrong, with a per-block note system so comments made during a presentation can be recovered against the step they were made at.

**Architecture:** One hand-authored HTML file with inline CSS and inline JavaScript, no build step and no network dependencies. Content is static markup in run order. The notes layer is a small module that discovers anchorable blocks by a `data-note-anchor` attribute, persists a single JSON blob to `localStorage` under a uniquely namespaced key, and renders an inline editor plus a drawer, Markdown export and JSON backup/restore.

**Tech Stack:** HTML5, CSS custom properties (no framework), vanilla ES2020 JavaScript (no libraries, no modules, no bundler), inline SVG. Verification via the Claude Browser MCP tools against a real `file://` load.

**Spec:** `docs/2026-08-07-nfcore-launch-path-presentation-design.md`

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Deliverable path:** `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html`. Authored in place. There is no repo copy — the user explicitly rejected a two-copy arrangement.
- **The page is NOT in git.** It lives outside any repository. Consequence: tasks end with a **verification checkpoint**, not a commit. Do not `git add` the page. The only commits in this plan are to the plan file itself if it is amended.
- **Self-contained, absolutely.** No CDN, no web fonts, no external stylesheets, no `fetch`, no imports. System font stack only. If it does not work with the network cable pulled, it is wrong.
- **No build step.** No npm, no bundler, no preprocessor. The file as written is the file as shipped.
- **`localStorage` key MUST be exactly `nfcore-launch-path.notes.v1`.** Verified fact: all `file://` pages share one origin (`file://`), so a generic key would collide with any other local HTML file on the machine.
- **`localStorage` must be feature-detected, not assumed.** It works in the browser tested, but private-browsing modes and other browsers can throw on `setItem`. Failure must degrade to in-memory with a visible banner, never to silent data loss.
- **Anchor IDs are hand-written and stable.** Format `s<NN>-<slug>` (e.g. `s03-cp5-genome`). Never positional, never generated, never renumbered on edit.
- **Single theme, light.** No dark mode. It must survive a projector in a bright room.
- **Every factual claim traces to a real source.** The facts in this plan were read from the codebase and from `.claude/reports/2026-08-05-nfcore-catalog-31-and-first-new-luria-verification.json` during the design session. Do not invent figures, file paths, or defect descriptions. If a number cannot be sourced, leave it out and say so.
- **Footer must record the commit** the page was written against: `0ac6571` on branch `dev-v3-merge`.

### Deviation from standard TDD — read this before Task 1

This plan does not use a test runner, because a standalone HTML file outside any repository has none and adding one would violate the no-build-step and self-contained constraints.

The TDD rhythm is preserved honestly using browser-driven checks: for every task with logic, you **write the check and run it first, observe it fail, implement, then re-run and observe it pass**. Checks are exact `mcp__Claude_Browser__javascript_tool` expressions with stated expected output. Content-only tasks are verified by reading the rendered page.

`mcp__Claude_Browser__navigate` to a `file://` URL does produce a genuine `file:` origin — verified during planning. Pass `force: true` to force a real reload when testing persistence.

---

## File Structure

One file. The spec's self-contained requirement forbids splitting, so internal organisation carries the burden that separate files normally would. The file is laid out in this fixed order, with HTML comment banners marking each region so later tasks can locate their insertion point exactly:

```
<!doctype html>
<head>
  <title>, <meta>
  <style>            REGION: DESIGN TOKENS
  <style>            REGION: LAYOUT
  <style>            REGION: BLOCK VOCABULARY
  <style>            REGION: NOTES UI
<body>
  <header>           topbar: title, notes count button, storage banner
  <nav>              contents rail
  <main>
    <!-- REGION: SECTION 00 --> … through … <!-- REGION: SECTION 06 -->
  <aside>            notes drawer
  <footer>           commit provenance
  <script>           REGION: NOTES STORAGE
  <script>           REGION: NOTES UI WIRING
  <script>           REGION: EXPORT / IMPORT
```

Responsibilities:

| Region | Responsible for |
|---|---|
| DESIGN TOKENS | Colours, type scale, spacing. Nothing else references raw hex values. |
| LAYOUT | Topbar, rail, main column, drawer positioning, the spine. |
| BLOCK VOCABULARY | `.block`, `.gate`, `.warn`, `.ext`, `.tech`, `.substeps` — the recurring content elements. |
| NOTES UI | Affordance, editor, margin card, drawer, rail counts. |
| SECTION 00–06 | Static content. No logic. |
| NOTES STORAGE | Read/write/serialise. Knows nothing about the DOM. |
| NOTES UI WIRING | Binds storage to anchors. Knows nothing about serialisation formats. |
| EXPORT / IMPORT | Markdown and JSON. Reads storage, never the DOM. |

That three-way split of the JavaScript is the important decomposition: storage is testable without the page rendered, and export is testable without the editor working.

---

## Task 1: Skeleton, design system, and block vocabulary

Produces the visual system and the anchor contract every later task depends on. Ends with three stub blocks so the layout is provably real before content or notes exist.

**Files:**
- Create: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - **Anchor contract.** Any anchorable block carries `data-note-anchor="<id>"` and `data-note-title="<human label>"`. The id follows `s<NN>-<slug>`. The title is what appears in the drawer and the Markdown export, so it must read sensibly out of context (`§ 03 · Checkpoint 5 — Genome resolution`, not `Genome`).
  - **Block vocabulary** (CSS classes, used by every content task):
    - `.section` — a `<section>` for one `§`, carries `data-note-anchor`
    - `.block` — any anchorable sub-unit inside a section
    - `.substeps` — `<ol>` of the numbered sub-steps
    - `.gate` — a refusal. Renders a bar **across** the spine.
    - `.warn` — a silent continue. Renders a mark **beside** the spine.
    - `.ext` — "Adding a pipeline here" panel, neutral
    - `.tech` — a `<details>` collapsed technical panel
    - `.mono` — inline code identifier
  - CSS custom properties `--gate`, `--warn`, `--ink`, `--paper`, `--rule`, `--neutral`.

- [ ] **Step 1: Create the file with the complete skeleton**

Write this exactly. It is long but complete — there is nothing to fill in.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The launch path — from a chat message to a job on Luria</title>
<style>
/* ============ REGION: DESIGN TOKENS ============ */
:root {
  --paper:      #fcfcfa;
  --paper-sunk: #f2f2ee;
  --ink:        #14161a;
  --ink-soft:   #464b54;
  --ink-faint:  #767d88;
  --rule:       #d9dbd8;
  --rule-soft:  #e9eae7;
  --gate:       #1c5f7a;
  --gate-bg:    #eaf3f7;
  --warn:       #a8420d;
  --warn-bg:    #fbf0e9;
  --neutral:    #5d5f63;
  --neutral-bg: #f4f4f1;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
  --rail-w: 15rem;
  --measure: 42rem;
}
*, *::before, *::after { box-sizing: border-box; }

/* ============ REGION: LAYOUT ============ */
html, body { margin: 0; padding: 0; }
body {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 17px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.topbar {
  position: sticky; top: 0; z-index: 40;
  display: flex; align-items: center; gap: 1rem;
  padding: 0.7rem 1.5rem;
  background: rgba(252,252,250,0.94);
  backdrop-filter: blur(6px);
  border-bottom: 1px solid var(--rule);
}
.topbar h1 { font-size: 0.95rem; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.topbar .spacer { flex: 1; }
.btn {
  font: inherit; font-size: 0.82rem;
  padding: 0.32rem 0.7rem;
  border: 1px solid var(--rule);
  border-radius: 5px;
  background: var(--paper);
  color: var(--ink-soft);
  cursor: pointer;
}
.btn:hover { border-color: var(--ink-faint); color: var(--ink); }
.shell { display: flex; align-items: flex-start; gap: 2.5rem; padding: 2rem 1.5rem 6rem; }
.rail {
  position: sticky; top: 4rem;
  flex: 0 0 var(--rail-w); width: var(--rail-w);
  font-size: 0.84rem;
}
.rail ol { list-style: none; margin: 0; padding: 0; }
.rail a {
  display: flex; align-items: baseline; gap: 0.5rem;
  padding: 0.3rem 0.5rem;
  color: var(--ink-soft); text-decoration: none;
  border-left: 2px solid transparent;
}
.rail a:hover { color: var(--ink); background: var(--paper-sunk); }
.rail a.annotated { border-left-color: var(--gate); color: var(--ink); }
.rail .rail-num { font-family: var(--mono); font-size: 0.74rem; color: var(--ink-faint); }
.rail .rail-count {
  margin-left: auto; font-family: var(--mono); font-size: 0.7rem;
  color: var(--gate); opacity: 0; 
}
.rail a.annotated .rail-count { opacity: 1; }
main { flex: 1 1 auto; max-width: var(--measure); }

/* the spine */
.section { position: relative; padding-left: 2.5rem; margin: 0 0 4.5rem; }
.section::before {
  content: ""; position: absolute; left: 0.75rem; top: 0.4rem; bottom: -3rem;
  width: 2px; background: var(--rule);
}
.section:last-of-type::before { bottom: 0.5rem; }
.section > .sec-num {
  position: absolute; left: 0; top: 0;
  width: 1.75rem; text-align: center;
  font-family: var(--mono); font-size: 0.72rem; color: var(--ink-faint);
  background: var(--paper); padding: 0.15rem 0;
}
.section h2 { font-size: 1.5rem; line-height: 1.25; letter-spacing: -0.02em; margin: 0 0 0.9rem; }
.section h3 { font-size: 1.02rem; margin: 1.8rem 0 0.5rem; }
.lede { font-size: 1.06rem; color: var(--ink-soft); margin: 0 0 1.2rem; }
p { margin: 0 0 1rem; }
footer {
  border-top: 1px solid var(--rule); margin: 0 1.5rem;
  padding: 1.5rem 0 3rem;
  font-size: 0.8rem; color: var(--ink-faint);
}

/* ============ REGION: BLOCK VOCABULARY ============ */
.block { position: relative; margin: 0 0 1.4rem; }
.substeps { counter-reset: sub; list-style: none; margin: 0 0 1.3rem; padding: 0; }
.substeps li {
  counter-increment: sub; position: relative;
  padding-left: 2rem; margin-bottom: 0.5rem;
}
.substeps li::before {
  content: counter(sub); position: absolute; left: 0; top: 0.05rem;
  width: 1.35rem; height: 1.35rem; border-radius: 50%;
  background: var(--paper-sunk); color: var(--ink-faint);
  font-family: var(--mono); font-size: 0.7rem;
  display: flex; align-items: center; justify-content: center;
}
.gate, .warn, .ext { padding: 0.85rem 1rem; border-radius: 6px; margin: 0 0 1.3rem; }
.gate { background: var(--gate-bg); border-left: 3px solid var(--gate); }
.warn { background: var(--warn-bg); border-left: 3px solid var(--warn); }
.ext  { background: var(--neutral-bg); border-left: 3px solid var(--rule); }
.gate::before, .warn::before, .ext::before {
  display: block; font-family: var(--mono); font-size: 0.68rem;
  letter-spacing: 0.09em; text-transform: uppercase; margin-bottom: 0.3rem;
}
.gate::before { content: "Gate — it stops here"; color: var(--gate); }
.warn::before { content: "Warning — it keeps going"; color: var(--warn); }
.ext::before  { content: "Adding a pipeline here"; color: var(--neutral); }
/* gate crosses the spine, warning sits beside it — the whole visual argument */
.gate { margin-left: -1.9rem; padding-left: 2.9rem; }
.gate::after {
  content: ""; position: absolute; left: 0; width: 1.5rem; height: 3px;
  background: var(--gate); top: 1.25rem;
}
.gate { position: relative; }
.tech { margin: 0 0 1.3rem; border-top: 1px solid var(--rule-soft); }
.tech > summary {
  cursor: pointer; padding: 0.5rem 0;
  font-size: 0.85rem; color: var(--ink-faint); list-style: none;
}
.tech > summary::-webkit-details-marker { display: none; }
.tech > summary::before { content: "▸ "; }
.tech[open] > summary::before { content: "▾ "; }
.tech > summary:hover { color: var(--ink); }
.tech .tech-body { padding: 0 0 0.8rem; font-size: 0.92rem; color: var(--ink-soft); }
.mono, code { font-family: var(--mono); font-size: 0.88em; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; margin: 0 0 1.3rem; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--rule-soft); }
th { font-weight: 600; color: var(--ink-faint); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }

/* ============ REGION: NOTES UI ============ */
/* filled in by Task 2 */
</style>
</head>
<body>

<header class="topbar">
  <h1>The launch path</h1>
  <div class="spacer"></div>
  <button class="btn" id="notes-toggle" type="button">0 notes</button>
</header>

<div class="shell">
  <nav class="rail" aria-label="Contents">
    <ol id="rail-list">
      <li><a href="#s00"><span class="rail-num">00</span> Overview<span class="rail-count"></span></a></li>
      <li><a href="#s01"><span class="rail-num">01</span> Work out what to run on<span class="rail-count"></span></a></li>
      <li><a href="#s02"><span class="rail-num">02</span> Build the row set<span class="rail-count"></span></a></li>
      <li><a href="#s03"><span class="rail-num">03</span> Configure the run<span class="rail-count"></span></a></li>
      <li><a href="#s04"><span class="rail-num">04</span> Onto the cluster<span class="rail-count"></span></a></li>
      <li><a href="#s05"><span class="rail-num">05</span> What the user is told<span class="rail-count"></span></a></li>
      <li><a href="#s06"><span class="rail-num">06</span> How far this stretches<span class="rail-count"></span></a></li>
    </ol>
  </nav>

  <main>
    <!-- REGION: SECTION 00 -->
    <section class="section" id="s00" data-note-anchor="s00-overview" data-note-title="§ 00 · Overview">
      <span class="sec-num">00</span>
      <h2>Stub — overview</h2>
      <p class="lede">Replaced in Task 5.</p>
      <div class="block" data-note-anchor="s00-stub-block" data-note-title="§ 00 · Stub block">
        <p>A stub anchorable block, so Task 2 has something real to bind to.</p>
      </div>
      <div class="gate" data-note-anchor="s00-stub-gate" data-note-title="§ 00 · Stub gate">
        <p>A stub gate, proving the bar crosses the spine.</p>
      </div>
    </section>
    <!-- REGION: SECTION 01 -->
    <!-- REGION: SECTION 02 -->
    <!-- REGION: SECTION 03 -->
    <!-- REGION: SECTION 04 -->
    <!-- REGION: SECTION 05 -->
    <!-- REGION: SECTION 06 -->
  </main>
</div>

<footer>
  <p>Written against <code>0ac6571</code> on branch <code>dev-v3-merge</code>, 2026-08-07.
  This page is a written snapshot; it does not read the codebase.</p>
</footer>

<script>
/* ============ REGION: NOTES STORAGE ============ */
/* filled in by Task 2 */
</script>
<script>
/* ============ REGION: NOTES UI WIRING ============ */
/* filled in by Task 2 */
</script>
<script>
/* ============ REGION: EXPORT / IMPORT ============ */
/* filled in by Task 4 */
</script>
</body>
</html>
```

- [ ] **Step 2: Open it from a real `file://` URL**

Use `mcp__Claude_Browser__navigate` with:
`file:///path/to/nfcore-launch-path.html`

- [ ] **Step 3: Verify the origin is genuinely `file:` and the layout rendered**

Run with `mcp__Claude_Browser__javascript_tool`:

```js
JSON.stringify({
  protocol: location.protocol,
  anchors: document.querySelectorAll("[data-note-anchor]").length,
  railItems: document.querySelectorAll("#rail-list a").length,
  regions: document.body.parentNode.innerHTML.match(/REGION: SECTION/g).length,
  spineDrawn: getComputedStyle(document.querySelector(".section"), "::before").width
})
```

Expected: `protocol` is `"file:"`, `anchors` is `3`, `railItems` is `7`, `regions` is `7`, `spineDrawn` is `"2px"`.

If `protocol` is not `"file:"` the page was rendered as a snapshot rather than loaded — nothing after this point can be trusted. Re-navigate before continuing.

- [ ] **Step 4: Verify there are no console errors**

Run `mcp__Claude_Browser__read_console_messages` with `onlyErrors: true`.
Expected: empty.

- [ ] **Step 5: Verify it renders with no network**

Confirm by inspection that the file contains no `http://`, `https://`, `fetch(`, `<link rel="stylesheet"`, or `<script src=`:

```bash
grep -nE 'https?://|fetch\(|<link|<script src' ~/Documents/MIT/MeetingNotes/nfcore-launch-path.html
```

Expected: no matches. (The footer's `0ac6571` text contains no URL.)

- [ ] **Step 6: Verification checkpoint**

No commit — the page is outside git. Record: file created, `file:` origin confirmed, 4 anchors present, no console errors, no external references.

---

## Task 2: Notes storage core and the inline editor

The riskiest part of the build, done early against the stub blocks so it is proven before content exists.

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — REGION: NOTES UI (css), REGION: NOTES STORAGE, REGION: NOTES UI WIRING

**Interfaces:**
- Consumes: the anchor contract from Task 1 — every anchorable element has `data-note-anchor` and `data-note-title`.
- Produces the global `NOTES` object. Tasks 3 and 4 use exactly these names:
  - `NOTES.get(id)` → `{text, updatedAt}` or `null`
  - `NOTES.set(id, text)` → `void`. Whitespace-only text deletes the note.
  - `NOTES.remove(id)` → `void`
  - `NOTES.all()` → `{ [id]: {text, updatedAt} }` (a copy)
  - `NOTES.count()` → `number`
  - `NOTES.replaceAll(objOrNull)` → `void`, for restore
  - `NOTES.isMemoryOnly()` → `boolean`, true when `localStorage` was unusable
  - `NOTES.onChange(fn)` → registers a callback fired after every mutation

- [ ] **Step 1: Write the failing check**

Before implementing, run this in the browser to confirm the API does not yet exist:

```js
typeof window.NOTES
```

Expected: `"undefined"` — this is the failing state that Step 3 fixes.

- [ ] **Step 2: Implement the storage module**

Replace the line `/* filled in by Task 2 */` inside the **REGION: NOTES STORAGE** script with:

```js
window.NOTES = (function () {
  var KEY = "nfcore-launch-path.notes.v1";   // MUST be unique: all file:// pages share one origin
  var VERSION = 1;
  var memoryOnly = false;
  var memory = { version: VERSION, notes: {} };
  var listeners = [];

  function usable() {
    try {
      var p = KEY + ".probe";
      localStorage.setItem(p, "1");
      localStorage.removeItem(p);
      return true;
    } catch (e) {
      return false;
    }
  }
  memoryOnly = !usable();

  function read() {
    if (memoryOnly) return memory;
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) return { version: VERSION, notes: {} };
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || typeof parsed.notes !== "object" || !parsed.notes) {
        return { version: VERSION, notes: {} };
      }
      return { version: parsed.version || VERSION, notes: parsed.notes };
    } catch (e) {
      return { version: VERSION, notes: {} };
    }
  }

  function write(data) {
    if (memoryOnly) { memory = data; fire(); return; }
    try {
      localStorage.setItem(KEY, JSON.stringify(data));
    } catch (e) {
      memoryOnly = true;
      memory = data;
    }
    fire();
  }

  function fire() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](); } catch (e) { /* a bad listener must not break saving */ }
    }
  }

  return {
    get: function (id) {
      var n = read().notes[id];
      return n ? { text: n.text, updatedAt: n.updatedAt } : null;
    },
    set: function (id, text) {
      var data = read();
      if (!text || !String(text).trim()) {
        delete data.notes[id];
      } else {
        data.notes[id] = { text: String(text), updatedAt: new Date().toISOString() };
      }
      write(data);
    },
    remove: function (id) {
      var data = read();
      delete data.notes[id];
      write(data);
    },
    all: function () {
      return JSON.parse(JSON.stringify(read().notes));
    },
    count: function () {
      return Object.keys(read().notes).length;
    },
    replaceAll: function (obj) {
      write({ version: VERSION, notes: (obj && typeof obj === "object") ? obj : {} });
    },
    isMemoryOnly: function () { return memoryOnly; },
    onChange: function (fn) { if (typeof fn === "function") listeners.push(fn); }
  };
})();
```

- [ ] **Step 3: Re-run the check and confirm the storage API now works**

Reload the page (`mcp__Claude_Browser__navigate` with `force: true`), then run:

```js
NOTES.set("probe-a", "first note");
NOTES.set("probe-b", "   ");
JSON.stringify({
  api: typeof NOTES,
  a: NOTES.get("probe-a").text,
  bDeletedByBlank: NOTES.get("probe-b"),
  count: NOTES.count(),
  memoryOnly: NOTES.isMemoryOnly()
})
```

Expected: `api` is `"object"`, `a` is `"first note"`, `bDeletedByBlank` is `null`, `count` is `1`, `memoryOnly` is `false`.

- [ ] **Step 4: Verify persistence across a genuine reload**

Reload with `force: true`, then run:

```js
JSON.stringify({ survived: NOTES.get("probe-a"), count: NOTES.count() })
```

Expected: `survived.text` is `"first note"`, `count` is `1`.

Then clean up: `NOTES.replaceAll({}); NOTES.count()` → expected `0`.

- [ ] **Step 5: Add the notes UI styles**

Replace `/* filled in by Task 2 */` in the **REGION: NOTES UI** style block with:

```css
.note-add {
  position: absolute; right: -2.6rem; top: 0.1rem;
  width: 1.6rem; height: 1.6rem; padding: 0; line-height: 1;
  border: 1px solid var(--rule); border-radius: 50%;
  background: var(--paper); color: var(--ink-faint);
  font-size: 0.9rem; cursor: pointer;
  opacity: 0; transition: opacity 0.12s;
}
[data-note-anchor]:hover > .note-add,
.note-add:focus { opacity: 1; }
.note-card {
  margin: 0.6rem 0 0; padding: 0.6rem 0.75rem;
  background: #fffdf3; border: 1px solid #e6dfbe; border-left: 3px solid #c9a227;
  border-radius: 5px; font-size: 0.88rem; color: var(--ink-soft);
  white-space: pre-wrap;
}
.note-card .note-meta {
  display: flex; gap: 0.75rem; align-items: center;
  margin-top: 0.4rem; font-size: 0.72rem; color: var(--ink-faint);
}
.note-card .note-meta button {
  font: inherit; background: none; border: none; padding: 0;
  color: var(--ink-faint); text-decoration: underline; cursor: pointer;
}
.note-editor { margin: 0.6rem 0 0; }
.note-editor textarea {
  width: 100%; min-height: 5rem; padding: 0.55rem 0.7rem;
  font: inherit; font-size: 0.88rem; line-height: 1.5;
  border: 1px solid #c9a227; border-radius: 5px; background: #fffdf3;
  resize: vertical;
}
.note-editor .note-status {
  font-size: 0.72rem; color: var(--ink-faint); margin-top: 0.25rem; min-height: 1rem;
}
.storage-banner {
  background: var(--warn-bg); border-bottom: 1px solid var(--warn);
  color: var(--warn); padding: 0.5rem 1.5rem; font-size: 0.83rem;
}
```

- [ ] **Step 6: Implement the editor wiring**

Replace `/* filled in by Task 2 */` in the **REGION: NOTES UI WIRING** script with:

```js
(function () {
  var SAVE_DEBOUNCE_MS = 400;

  function anchors() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-note-anchor]"));
  }

  function render(el) {
    var id = el.getAttribute("data-note-anchor");
    var existing = el.querySelector(":scope > .note-card, :scope > .note-editor");
    if (existing) existing.remove();
    var note = NOTES.get(id);
    if (!note) return;
    var card = document.createElement("div");
    card.className = "note-card";
    card.textContent = note.text;
    var meta = document.createElement("div");
    meta.className = "note-meta";
    var when = document.createElement("span");
    when.textContent = new Date(note.updatedAt).toLocaleString();
    var edit = document.createElement("button");
    edit.type = "button"; edit.textContent = "edit";
    edit.addEventListener("click", function () { openEditor(el); });
    var del = document.createElement("button");
    del.type = "button"; del.textContent = "delete";
    del.addEventListener("click", function () { NOTES.remove(id); render(el); });
    meta.appendChild(when); meta.appendChild(edit); meta.appendChild(del);
    card.appendChild(meta);
    el.appendChild(card);
  }

  function openEditor(el) {
    var id = el.getAttribute("data-note-anchor");
    var card = el.querySelector(":scope > .note-card");
    if (card) card.remove();
    if (el.querySelector(":scope > .note-editor")) {
      el.querySelector(":scope > .note-editor textarea").focus();
      return;
    }
    var wrap = document.createElement("div");
    wrap.className = "note-editor";
    var ta = document.createElement("textarea");
    var current = NOTES.get(id);
    ta.value = current ? current.text : "";
    ta.setAttribute("aria-label", "Note on " + (el.getAttribute("data-note-title") || id));
    var status = document.createElement("div");
    status.className = "note-status";
    wrap.appendChild(ta); wrap.appendChild(status);
    el.appendChild(wrap);
    ta.focus();

    var timer = null;
    ta.addEventListener("input", function () {
      status.textContent = "saving…";
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        NOTES.set(id, ta.value);
        status.textContent = "saved";
      }, SAVE_DEBOUNCE_MS);
    });
    ta.addEventListener("blur", function () {
      if (timer) clearTimeout(timer);
      NOTES.set(id, ta.value);
      wrap.remove();
      render(el);
    });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { ta.blur(); }
    });
  }
  window.__openNoteEditor = openEditor;

  anchors().forEach(function (el) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "note-add";
    btn.textContent = "+";
    btn.title = "Add a note here";
    btn.setAttribute("aria-label", "Add a note on " + (el.getAttribute("data-note-title") || ""));
    btn.addEventListener("click", function (e) { e.preventDefault(); openEditor(el); });
    el.insertBefore(btn, el.firstChild);
    render(el);
  });

  if (NOTES.isMemoryOnly()) {
    var banner = document.createElement("div");
    banner.className = "storage-banner";
    banner.textContent = "This browser is not letting the page save notes. Notes will be kept " +
      "only until you close the tab — use Download .json before you finish.";
    document.body.insertBefore(banner, document.body.firstChild);
  }
})();
```

- [ ] **Step 7: Verify the editor round-trips through the DOM**

Reload with `force: true`, then run:

```js
(function(){
  var el = document.querySelector('[data-note-anchor="s00-stub-block"]');
  window.__openNoteEditor(el);
  var ta = el.querySelector(".note-editor textarea");
  ta.value = "typed during the talk";
  ta.dispatchEvent(new Event("input"));
  ta.blur();
  return JSON.stringify({
    stored: NOTES.get("s00-stub-block").text,
    cardShown: !!el.querySelector(".note-card"),
    cardText: el.querySelector(".note-card").firstChild.textContent,
    addButtons: document.querySelectorAll(".note-add").length
  });
})()
```

Expected: `stored` and `cardText` are `"typed during the talk"`, `cardShown` is `true`, `addButtons` is `3`.

- [ ] **Step 8: Verify it survives a reload and then clean up**

Reload with `force: true`, then:

```js
JSON.stringify({
  restoredOnLoad: document.querySelector('[data-note-anchor="s00-stub-block"] .note-card').firstChild.textContent,
  count: NOTES.count()
})
```

Expected: `restoredOnLoad` is `"typed during the talk"`, `count` is `1`.

Then: `NOTES.replaceAll({}); location.reload();`

- [ ] **Step 9: Verification checkpoint**

No commit. Record: storage API present, blank-text deletion works, persistence across reload confirmed, editor round-trip confirmed, `memoryOnly` false on this browser.

---

## Task 3: Rail counts, notes drawer, and the N shortcut

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — REGION: NOTES UI (css), REGION: NOTES UI WIRING (append), body (drawer element)

**Interfaces:**
- Consumes: `NOTES.all()`, `NOTES.count()`, `NOTES.onChange(fn)`, `window.__openNoteEditor(el)` from Task 2; the `data-note-title` attribute from Task 1.
- Produces: `window.__refreshNotesUI()` — recomputes the topbar count, rail counts and drawer contents. Task 4 calls it after a restore.

- [ ] **Step 1: Write the failing check**

```js
typeof window.__refreshNotesUI
```

Expected: `"undefined"`.

- [ ] **Step 2: Add the drawer markup**

Insert immediately before `<footer>`:

```html
<aside class="drawer" id="notes-drawer" hidden>
  <div class="drawer-head">
    <strong>Notes</strong>
    <div class="spacer"></div>
    <button class="btn" id="drawer-close" type="button">close</button>
  </div>
  <div id="drawer-body"></div>
  <div class="drawer-foot" id="drawer-foot"></div>
</aside>
```

- [ ] **Step 3: Add the drawer styles**

Append to the **REGION: NOTES UI** style block:

```css
.drawer {
  position: fixed; top: 0; right: 0; bottom: 0; width: 25rem; max-width: 92vw;
  z-index: 60; background: var(--paper);
  border-left: 1px solid var(--rule); box-shadow: -8px 0 24px rgba(0,0,0,0.06);
  display: flex; flex-direction: column;
}
.drawer[hidden] { display: none; }
.drawer-head {
  display: flex; align-items: center; gap: 0.75rem;
  padding: 0.75rem 1rem; border-bottom: 1px solid var(--rule);
}
.drawer-head .spacer { flex: 1; }
#drawer-body { flex: 1 1 auto; overflow-y: auto; padding: 0.75rem 1rem; }
.drawer-foot { border-top: 1px solid var(--rule); padding: 0.75rem 1rem; display: flex; flex-wrap: wrap; gap: 0.4rem; }
.drawer-item { margin: 0 0 1rem; padding-bottom: 0.85rem; border-bottom: 1px solid var(--rule-soft); }
.drawer-item .drawer-title {
  display: block; font-size: 0.76rem; color: var(--gate);
  background: none; border: none; padding: 0; margin: 0 0 0.25rem;
  font-family: var(--mono); cursor: pointer; text-align: left;
}
.drawer-item .drawer-title:hover { text-decoration: underline; }
.drawer-item .drawer-text { font-size: 0.88rem; white-space: pre-wrap; color: var(--ink-soft); }
.drawer-empty { color: var(--ink-faint); font-size: 0.88rem; }
.drawer-orphans { margin-top: 1.5rem; padding-top: 0.75rem; border-top: 2px solid var(--warn); }
.drawer-orphans h4 { margin: 0 0 0.5rem; font-size: 0.8rem; color: var(--warn); }
.note-flash { animation: noteflash 1.4s ease-out; }
@keyframes noteflash { from { background: #fff4c2; } to { background: transparent; } }
```

- [ ] **Step 4: Implement the wiring**

Append to the **REGION: NOTES UI WIRING** script (inside the file, as a new IIFE after the existing one):

```js
(function () {
  var toggle = document.getElementById("notes-toggle");
  var drawer = document.getElementById("notes-drawer");
  var body = document.getElementById("drawer-body");
  document.getElementById("drawer-close")
    .addEventListener("click", function () { drawer.hidden = true; });

  function anchorEl(id) { return document.querySelector('[data-note-anchor="' + id + '"]'); }

  function pageOrder() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-note-anchor]"))
      .map(function (el) { return el.getAttribute("data-note-anchor"); });
  }

  function refresh() {
    var all = NOTES.all();
    var ids = Object.keys(all);
    toggle.textContent = ids.length + (ids.length === 1 ? " note" : " notes");

    // rail counts, per section
    document.querySelectorAll("#rail-list a").forEach(function (a) {
      var sectionId = a.getAttribute("href").slice(1);
      var section = document.getElementById(sectionId);
      var n = 0;
      if (section) {
        if (all[section.getAttribute("data-note-anchor")]) n++;
        section.querySelectorAll("[data-note-anchor]").forEach(function (el) {
          if (all[el.getAttribute("data-note-anchor")]) n++;
        });
      }
      a.classList.toggle("annotated", n > 0);
      a.querySelector(".rail-count").textContent = n > 0 ? String(n) : "";
    });

    // drawer, in page order, orphans last
    body.innerHTML = "";
    var order = pageOrder();
    var live = order.filter(function (id) { return all[id]; });
    var orphans = ids.filter(function (id) { return order.indexOf(id) === -1; });

    if (!live.length && !orphans.length) {
      var empty = document.createElement("p");
      empty.className = "drawer-empty";
      empty.textContent = "No notes yet. Hover any block and press +, or press N to annotate whatever is on screen.";
      body.appendChild(empty);
    }

    live.forEach(function (id) { body.appendChild(item(id, all[id], false)); });

    if (orphans.length) {
      var wrap = document.createElement("div");
      wrap.className = "drawer-orphans";
      var h = document.createElement("h4");
      h.textContent = "Orphaned notes — the block they were attached to no longer exists";
      wrap.appendChild(h);
      orphans.forEach(function (id) { wrap.appendChild(item(id, all[id], true)); });
      body.appendChild(wrap);
    }
  }

  function item(id, note, isOrphan) {
    var el = anchorEl(id);
    var wrap = document.createElement("div");
    wrap.className = "drawer-item";
    var title = document.createElement("button");
    title.type = "button";
    title.className = "drawer-title";
    title.textContent = (el && el.getAttribute("data-note-title")) || note.title || id;
    if (isOrphan) {
      title.disabled = true;
    } else {
      title.addEventListener("click", function () {
        drawer.hidden = true;
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.remove("note-flash");
        void el.offsetWidth;
        el.classList.add("note-flash");
      });
    }
    var text = document.createElement("div");
    text.className = "drawer-text";
    text.textContent = note.text;
    wrap.appendChild(title); wrap.appendChild(text);
    return wrap;
  }

  toggle.addEventListener("click", function () {
    drawer.hidden = !drawer.hidden;
    if (!drawer.hidden) refresh();
  });

  // N annotates whatever block is nearest the middle of the viewport
  document.addEventListener("keydown", function (e) {
    if (e.key !== "n" && e.key !== "N") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var t = e.target;
    if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
    e.preventDefault();
    var mid = window.innerHeight / 2;
    var best = null, bestDist = Infinity;
    document.querySelectorAll("[data-note-anchor]").forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight) return;
      var d = Math.abs((r.top + r.bottom) / 2 - mid);
      if (d < bestDist) { bestDist = d; best = el; }
    });
    if (best) {
      best.scrollIntoView({ behavior: "smooth", block: "center" });
      window.__openNoteEditor(best);
    }
  });

  window.__refreshNotesUI = refresh;
  NOTES.onChange(refresh);
  refresh();
})();
```

- [ ] **Step 5: Verify counts, drawer and orphan grouping**

Reload with `force: true`, then run:

```js
(function(){
  NOTES.replaceAll({});
  NOTES.set("s00-stub-block", "note on a real block");
  NOTES.set("s99-does-not-exist", "note on a block that is gone");
  document.getElementById("notes-toggle").click();
  return JSON.stringify({
    toggleLabel: document.getElementById("notes-toggle").textContent,
    railAnnotated: document.querySelectorAll("#rail-list a.annotated").length,
    railCount: document.querySelector('#rail-list a[href="#s00"] .rail-count').textContent,
    liveItems: document.querySelectorAll("#drawer-body .drawer-item").length,
    orphanShown: !!document.querySelector(".drawer-orphans"),
    orphanCount: document.querySelectorAll(".drawer-orphans .drawer-item").length
  });
})()
```

Expected: `toggleLabel` is `"2 notes"`, `railAnnotated` is `1`, `railCount` is `"1"`, `liveItems` is `2`, `orphanShown` is `true`, `orphanCount` is `1`.

The orphan must appear — a note whose anchor disappeared is never dropped.

- [ ] **Step 6: Verify the N shortcut targets the block in view**

```js
(function(){
  NOTES.replaceAll({});
  document.getElementById("notes-drawer").hidden = true;
  document.querySelector('[data-note-anchor="s00-stub-gate"]').scrollIntoView({block:"center"});
  document.dispatchEvent(new KeyboardEvent("keydown", {key:"n", bubbles:true}));
  var open = document.querySelector(".note-editor");
  return JSON.stringify({
    editorOpened: !!open,
    onWhichAnchor: open ? open.parentElement.getAttribute("data-note-anchor") : null
  });
})()
```

Expected: `editorOpened` is `true`, `onWhichAnchor` is `"s00-stub-gate"`.

Then clean up: `NOTES.replaceAll({}); location.reload();`

- [ ] **Step 7: Verification checkpoint**

No commit. Record: topbar count live, rail counts per section, drawer lists in page order, orphans grouped and never lost, N targets the centred block.

---

## Task 4: Markdown export, JSON backup, JSON restore

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — drawer footer, REGION: EXPORT / IMPORT

**Interfaces:**
- Consumes: `NOTES.all()`, `NOTES.replaceAll(obj)`, `window.__refreshNotesUI()`.
- Produces: `window.__notesMarkdown()` → `string`; `window.__notesFilename(ext)` → `string`. Both are pure and directly checkable.

- [ ] **Step 1: Write the failing check**

```js
typeof window.__notesMarkdown
```

Expected: `"undefined"`.

- [ ] **Step 2: Add the export controls to the drawer footer**

Replace `<div class="drawer-foot" id="drawer-foot"></div>` with:

```html
<div class="drawer-foot" id="drawer-foot">
  <button class="btn" id="exp-copy" type="button">Copy as Markdown</button>
  <button class="btn" id="exp-md" type="button">Download .md</button>
  <button class="btn" id="exp-json" type="button">Download .json</button>
  <button class="btn" id="exp-restore" type="button">Restore from .json</button>
  <input type="file" id="exp-file" accept="application/json,.json" hidden>
  <p class="drawer-empty" style="width:100%;margin:0.4rem 0 0">
    Notes are stored in this browser on this machine. Clearing site data erases them.
    Download the .json to move them somewhere else.
  </p>
</div>
```

- [ ] **Step 3: Implement export and import**

Replace `/* filled in by Task 4 */` in the **REGION: EXPORT / IMPORT** script with:

```js
(function () {
  function stamp() {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function filename(ext) {
    return "nfcore-launch-path-notes-" + stamp() + "." + ext;
  }

  function markdown() {
    var all = NOTES.all();
    var order = Array.prototype.slice.call(document.querySelectorAll("[data-note-anchor]"));
    var lines = ["# Launch path — notes", "", "Taken " + stamp() + ".", ""];
    var written = {};
    order.forEach(function (el) {
      var id = el.getAttribute("data-note-anchor");
      if (!all[id]) return;
      lines.push("## " + (el.getAttribute("data-note-title") || id));
      lines.push("");
      lines.push(all[id].text);
      lines.push("");
      written[id] = true;
    });
    var orphans = Object.keys(all).filter(function (id) { return !written[id]; });
    if (orphans.length) {
      lines.push("## Orphaned notes");
      lines.push("");
      orphans.forEach(function (id) {
        lines.push("### " + id);
        lines.push("");
        lines.push(all[id].text);
        lines.push("");
      });
    }
    if (!Object.keys(all).length) { lines.push("_No notes were taken._", ""); }
    return lines.join("\n");
  }

  function download(text, name, mime) {
    var blob = new Blob([text], { type: mime });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  document.getElementById("exp-copy").addEventListener("click", function () {
    var text = markdown();
    var btn = this;
    var done = function () { btn.textContent = "Copied"; setTimeout(function(){ btn.textContent = "Copy as Markdown"; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text); done();
    }
  });

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) { /* clipboard blocked on file:// in some browsers */ }
    document.body.removeChild(ta);
  }

  document.getElementById("exp-md").addEventListener("click", function () {
    download(markdown(), filename("md"), "text/markdown");
  });

  document.getElementById("exp-json").addEventListener("click", function () {
    download(JSON.stringify({ version: 1, notes: NOTES.all() }, null, 2),
             filename("json"), "application/json");
  });

  var picker = document.getElementById("exp-file");
  document.getElementById("exp-restore").addEventListener("click", function () { picker.click(); });
  picker.addEventListener("change", function () {
    var f = picker.files && picker.files[0];
    if (!f) return;
    var reader = new FileReader();
    reader.onload = function () {
      var incoming;
      try {
        var parsed = JSON.parse(String(reader.result));
        incoming = (parsed && parsed.notes) ? parsed.notes : null;
      } catch (e) { incoming = null; }
      if (!incoming || typeof incoming !== "object") {
        alert("That file is not a notes backup — nothing was changed.");
        picker.value = "";
        return;
      }
      var existing = NOTES.count();
      if (existing > 0 && !confirm(
            "Restoring replaces the " + existing + " note(s) currently in this browser. Continue?")) {
        picker.value = "";
        return;
      }
      NOTES.replaceAll(incoming);
      window.__refreshNotesUI();
      document.querySelectorAll("[data-note-anchor]").forEach(function (el) {
        var card = el.querySelector(":scope > .note-card");
        if (card) card.remove();
      });
      location.reload();
    };
    reader.readAsText(f);
  });

  window.__notesMarkdown = markdown;
  window.__notesFilename = filename;
})();
```

- [ ] **Step 4: Verify the Markdown export shape**

Reload with `force: true`, then:

```js
(function(){
  NOTES.replaceAll({});
  NOTES.set("s00-stub-gate", "second in page order");
  NOTES.set("s00-overview", "first in page order");
  NOTES.set("s99-gone", "orphan");
  var md = window.__notesMarkdown();
  return JSON.stringify({
    filename: window.__notesFilename("md"),
    firstHeading: md.split("\n").filter(function(l){return l.indexOf("## ")===0;})[0],
    headingOrder: md.split("\n").filter(function(l){return l.indexOf("## ")===0;}),
    hasOrphanSection: md.indexOf("## Orphaned notes") > -1,
    orphanTextPresent: md.indexOf("orphan") > -1
  });
})()
```

Expected: `filename` is `"nfcore-launch-path-notes-<today>.md"`; `headingOrder` lists `§ 00 · Overview` **before** `§ 00 · Stub gate` (page order, not insertion order); `hasOrphanSection` and `orphanTextPresent` are both `true`.

Page order — not the order you typed them — is the whole point of the export.

- [ ] **Step 5: Verify the empty-notes case does not produce a broken document**

```js
(function(){ NOTES.replaceAll({}); var md = window.__notesMarkdown();
  return JSON.stringify({ hasNoNotesLine: md.indexOf("_No notes were taken._") > -1,
                          headings: (md.match(/^## /gm) || []).length }); })()
```

Expected: `hasNoNotesLine` is `true`, `headings` is `0`.

- [ ] **Step 6: Verify restore parsing rejects rubbish without destroying data**

The file picker cannot be driven from script, so check the parse-and-guard logic directly:

```js
(function(){
  NOTES.replaceAll({ "s00-overview": { text: "existing", updatedAt: new Date().toISOString() } });
  var before = NOTES.count();
  var bad = "not json at all";
  var parsed = null;
  try { var p = JSON.parse(bad); parsed = (p && p.notes) ? p.notes : null; } catch (e) { parsed = null; }
  return JSON.stringify({ parsedIsNull: parsed === null, countUnchanged: NOTES.count() === before });
})()
```

Expected: both `true`.

- [ ] **Step 7: Verify a real download and a real restore by hand**

This step is manual and cannot be scripted — do it and record the outcome honestly.

1. Open the drawer, add two notes on different blocks.
2. Click **Download .json**; confirm a file named `nfcore-launch-path-notes-<today>.json` arrives in the browser's download folder.
3. Run `NOTES.replaceAll({}); location.reload();` — confirm the page shows no notes.
4. Click **Restore from .json**, pick the downloaded file.
5. Confirm both notes reappear **on their original blocks** after the reload, and the topbar count is `2`.

If the browser blocks the download or the picker on `file://`, record that as a finding — it is a real limitation of the deliverable, not something to paper over.

- [ ] **Step 8: Clean up and verification checkpoint**

`NOTES.replaceAll({}); location.reload();`

No commit. Record the results of Steps 4–7, including anything that failed in Step 7.

---

## Task 5: Content — § 00 Overview, § 01 resolve_samples, § 02 write_samplesheet

From here the tasks are content. Every block must carry `data-note-anchor` and `data-note-title`; the notes system picks them up with no further wiring.

**Block vocabulary reminder (do not look this up elsewhere):** `.section` for a `§`; `.block` for an anchorable unit; `.substeps` for the numbered `<ol>`; `.gate` for a refusal; `.warn` for a silent continue; `.ext` for the "Adding a pipeline here" panel; `.tech` for a `<details>` technical panel with a `.tech-body` inside; `.mono` for identifiers. The `::before` labels on `.gate`, `.warn` and `.ext` are supplied by CSS — do **not** write "Gate —" into the markup.

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — replace the § 00 stub section, and fill REGION: SECTION 01 and REGION: SECTION 02

**Interfaces:**
- Consumes: block vocabulary and anchor contract from Task 1.
- Produces: anchors `s00-overview`, `s00-legend`, `s01-*`, `s02-*` for the drawer and export.

- [ ] **Step 1: Replace the entire § 00 stub section**

Replace the whole `<section class="section" id="s00" …>…</section>` block from Task 1 with:

```html
    <section class="section" id="s00" data-note-anchor="s00-overview" data-note-title="§ 00 · Overview">
      <span class="sec-num">00</span>
      <h2>From a sentence in a chat box<br>to a job queued on Luria.</h2>
      <p class="lede">Someone types "run RNA-seq on the liver samples from the macaque study."
      Some minutes later a job is sitting in the cluster queue. This page is every step in
      between, and every point where either a check or a person intervenes.</p>
      <p>Five tools run in order. Each one does a job the next one depends on, and each one
      can refuse. The third — <span class="mono">configure_run</span> — is where a
      conversation turns into two files a pipeline can actually be launched from, so it gets
      the most room here.</p>

      <div class="block" data-note-anchor="s00-legend" data-note-title="§ 00 · How to read the marks">
        <h3>Two kinds of problem, marked differently</h3>
        <p>The distinction this page is built around: some failures stop you, and some
        do not. Both are drawn against the vertical line running down the page.</p>
        <div class="gate" data-note-anchor="s00-legend-gate" data-note-title="§ 00 · Legend — gate">
          <p>The system stops and refuses. It is loud, it is safe, and it is working as
          designed. Drawn as a bar <strong>across</strong> the line — the path ends here
          until someone acts.</p>
        </div>
        <div class="warn" data-note-anchor="s00-legend-warn" data-note-title="§ 00 · Legend — warning">
          <p>The system keeps going, and can be quietly wrong. Drawn <strong>beside</strong>
          the line, because the path continues. These are the ones worth your attention:
          nothing errors, and the result still looks plausible.</p>
        </div>
        <div class="ext" data-note-anchor="s00-legend-ext" data-note-title="§ 00 · Legend — adding a pipeline">
          <p>Every stage also answers the same two questions: what must a new nf-core
          pipeline declare here, and what has actually gone wrong when someone got it wrong.
          Those answers are drawn from six real defects found the first time these pipelines
          were run on the cluster.</p>
        </div>
      </div>
    </section>
```

- [ ] **Step 2: Fill § 01**

Replace `<!-- REGION: SECTION 01 -->` with:

```html
    <!-- REGION: SECTION 01 -->
    <section class="section" id="s01" data-note-anchor="s01-section" data-note-title="§ 01 · Work out what to run on">
      <span class="sec-num">01</span>
      <h2>Work out what to run on.</h2>
      <p class="lede">The tool is <span class="mono">resolve_samples</span>. It turns names
      from the conversation into a concrete list of sequencing files, and works out what
      species they came from.</p>

      <div class="block" data-note-anchor="s01-substeps" data-note-title="§ 01 · The six sub-steps">
        <ol class="substeps">
          <li>Take the sample IDs or archive accessions the conversation produced.</li>
          <li>Walk down the sample family tree to the sequencing records at the bottom.</li>
          <li>Keep only the record types this particular pipeline accepts.</li>
          <li>Pull any archive accessions out of the metadata.</li>
          <li>Stash each record's full metadata, so file paths can be found later by their
          value rather than by guessing a field name.</li>
          <li>Vote on the species.</li>
        </ol>
      </div>

      <div class="block" data-note-anchor="s01-species" data-note-title="§ 01 · How species is detected">
        <h3>The species vote</h3>
        <p>It never looks for a field called "organism" — lab metadata is too inconsistent
        about naming for that to work. Instead it reads <em>every value</em> in the record
        and its ancestors, and asks of each one: is this a species I recognise? Every hit is
        a vote, and the most common answer wins.</p>
        <details class="tech">
          <summary>Show what "recognise" means</summary>
          <div class="tech-body">
            <p>A lookup table of fourteen strings mapping to five genomes, in
            <span class="mono">reference_bundles.json</span>: human and <span class="mono">homo
            sapiens</span> to GRCh38; mouse and <span class="mono">mus musculus</span> to
            GRCm39; four spellings of rhesus to Mmul_10; five of cynomolgus to Mfas6.0; and
            <span class="mono">human+mouse</span> to a combined bundle. Matching is
            case-insensitive and trims whitespace. That is the entire tolerance.</p>
          </div>
        </details>
      </div>

      <div class="gate" data-note-anchor="s01-gate-noleaves" data-note-title="§ 01 · Gate — nothing resolved">
        <p>If the walk finds no records of the right type, it does not silently return an
        empty list. It says which types this pipeline actually wants and suggests the ones
        you need may be the children or parents of what you named — because the assistant
        cannot see that filter, and without the explanation it will retry the same IDs
        forever.</p>
      </div>

      <div class="warn" data-note-anchor="s01-warn-species" data-note-title="§ 01 · Warning — the species table is literal">
        <p>Fourteen exact strings, no fuzzy matching, no inference.
        <span class="mono">M. musculus</span> does not match. <span class="mono">C57BL/6</span>
        does not match. A sample described only by its strain gets no species at all here —
        and what happens next is § 04's problem.</p>
      </div>

      <div class="ext" data-note-anchor="s01-ext" data-note-title="§ 01 · Adding a pipeline — resolve_samples">
        <p><strong>Declare:</strong> which record types and assay names count as this
        pipeline's input.</p>
        <p><strong>Nothing else.</strong> Species detection is completely generic — a new
        pipeline adds no entry for it.</p>
        <p><strong>Risk:</strong> get the types wrong and nothing resolves, which is why the
        explanation above exists.</p>
        <p><strong>Evidence it holds up:</strong> bamtofastq was the first pipeline whose
        inputs are analysis records rather than raw sequencing data — a genuinely different
        shape — and it needed no code change at all.</p>
      </div>
    </section>
```

- [ ] **Step 3: Fill § 02**

Replace `<!-- REGION: SECTION 02 -->` with:

```html
    <!-- REGION: SECTION 02 -->
    <section class="section" id="s02" data-note-anchor="s02-section" data-note-title="§ 02 · Build the row set">
      <span class="sec-num">02</span>
      <h2>Build the row set.</h2>
      <p class="lede">The tool is <span class="mono">write_samplesheet</span>. It turns the
      resolved records into the table the pipeline will read — one row per sample, with the
      column names that particular pipeline insists on.</p>

      <div class="block" data-note-anchor="s02-substeps" data-note-title="§ 02 · The sub-steps">
        <ol class="substeps">
          <li>Merge the resolved records into rows.</li>
          <li>Group them into cohorts if the conversation defined any — the cohort travels
          as a column on each row, not as separate files.</li>
          <li>Rename the columns for the pipelines that use their own names.</li>
          <li>Sanitise sample names where a pipeline is fussy about their format.</li>
          <li>Write <span class="mono">samplesheet.csv</span> and a notes file beside it.</li>
        </ol>
      </div>

      <div class="gate" data-note-anchor="s02-gate-invalidate" data-note-title="§ 02 · Gate — forgets any earlier config">
        <p>Building a sheet deliberately forgets any configuration that came before it. The
        run plan is dropped from the session, along with the pointers to
        <span class="mono">params.yml</span> and <span class="mono">launch.yml</span>.</p>
        <p>The files themselves stay on disk — but submitting reads the session, not the
        disk, so there is no way to send yesterday's settings with today's samples. The next
        stage has to run again before anything can be submitted at all.</p>
      </div>

      <div class="warn" data-note-anchor="s02-warn-renames" data-note-title="§ 02 · Warning — renames live in code">
        <p>The column renames are a hardcoded list, not a declared one. ampliseq wants
        <span class="mono">forwardReads</span>; detaxizer wants
        <span class="mono">short_reads_fastq_1</span>; two pipelines take alignments and have
        no read columns at all. Each new pipeline risks adding another entry, and the list
        lives somewhere a curator would not think to look.</p>
      </div>

      <div class="ext" data-note-anchor="s02-ext" data-note-title="§ 02 · Adding a pipeline — write_samplesheet">
        <p><strong>Declare:</strong> the required columns, and whether the pipeline eats
        reads or alignments.</p>
        <p><strong>This is where "just add data, no code" breaks.</strong> A pipeline that
        renames the read columns, or constrains the format of sample names, needs an edit to
        a hardcoded map in the emitter. That is a code change, by someone who knows where
        that map is.</p>
        <p><strong>Real defect, 2026-08-05:</strong> ampliseq rejected the sample names it
        was handed. Its naming rule lives in code and nobody had added it — so the run got
        all the way to the pipeline before failing on something a curator could not have
        declared.</p>
      </div>
    </section>
```

- [ ] **Step 4: Verify the anchors are registered and the sections render**

Reload with `force: true`, then:

```js
JSON.stringify({
  anchors: document.querySelectorAll("[data-note-anchor]").length,
  addButtons: document.querySelectorAll(".note-add").length,
  gates: document.querySelectorAll(".gate").length,
  warns: document.querySelectorAll(".warn").length,
  exts: document.querySelectorAll(".ext").length,
  techPanels: document.querySelectorAll(".tech").length,
  duplicateIds: (function(){
    var seen = {}, dupes = [];
    document.querySelectorAll("[data-note-anchor]").forEach(function(el){
      var id = el.getAttribute("data-note-anchor");
      if (seen[id]) dupes.push(id); seen[id] = 1;
    });
    return dupes;
  })(),
  missingTitles: Array.prototype.slice.call(document.querySelectorAll("[data-note-anchor]"))
    .filter(function(el){ return !el.getAttribute("data-note-title"); }).length
})
```

Expected: `anchors` is `16`, `addButtons` equals `anchors`, `gates` is `3`, `warns` is `3`, `exts` is `3`, `techPanels` is `1`, `duplicateIds` is `[]`, `missingTitles` is `0`.

A duplicate anchor id would make two blocks share one note. It must be empty.

- [ ] **Step 5: Verify the gate visually crosses the spine and the warning does not**

```js
(function(){
  var g = document.querySelector("#s01 .gate").getBoundingClientRect();
  var w = document.querySelector("#s01 .warn").getBoundingClientRect();
  var s = document.querySelector("#s01").getBoundingClientRect();
  return JSON.stringify({ gateStartsLeftOfWarn: g.left < w.left, sectionLeft: Math.round(s.left) });
})()
```

Expected: `gateStartsLeftOfWarn` is `true`. The gate reaches back across the spine; the warning sits inside the text column.

- [ ] **Step 6: Verification checkpoint**

No commit. Record: 14 anchors, no duplicate ids, all titled, gate/warning/extension counts correct, gate crosses the spine.

---

## Task 6: Content — § 03, the seven checkpoints

The centrepiece. Each checkpoint is its own anchorable block.

**Block vocabulary reminder (do not look this up elsewhere):** `.section` for a `§`; `.block` for an anchorable unit; `.substeps` for the numbered `<ol>`; `.gate` for a refusal; `.warn` for a silent continue; `.ext` for the "Adding a pipeline here" panel; `.tech` for a `<details>` technical panel with a `.tech-body` inside; `.mono` for identifiers. CSS supplies the "Gate —" / "Warning —" / "Adding a pipeline here" labels; do not write them into the markup.

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — REGION: SECTION 03

**Interfaces:**
- Consumes: block vocabulary and anchor contract from Task 1.
- Produces: anchors `s03-section`, `s03-cp1` … `s03-cp7`, `s03-warn-rawkey`, `s03-warn-enumonly`, `s03-warn-storeroot`, `s03-ext`.

- [ ] **Step 1: Fill § 03**

Replace `<!-- REGION: SECTION 03 -->` with:

```html
    <!-- REGION: SECTION 03 -->
    <section class="section" id="s03" data-note-anchor="s03-section" data-note-title="§ 03 · Configure the run">
      <span class="sec-num">03</span>
      <h2>Configure the run.<br>Seven checkpoints.</h2>
      <p class="lede">The tool is <span class="mono">configure_run</span>. Everything before
      it decided <em>what to run on</em>; everything after it <em>runs</em>. This is the step
      that decides <em>how</em> — and it is where a conversation becomes two files a cluster
      can execute.</p>
      <p>It writes almost nothing itself. It calls four other pieces of the system in a fixed
      order, and the order is the point: each check must pass before the next one is allowed
      to spend any effort.</p>

      <div class="block" data-note-anchor="s03-cp1" data-note-title="§ 03 · Checkpoint 1 — Is this a real pipeline?">
        <h3>1 · Is this a pipeline I know?</h3>
        <p>The name is checked against the catalog. If the assistant invented a plausible
        pipeline name, this is where that ends.</p>
        <div class="gate" data-note-anchor="s03-cp1-gate" data-note-title="§ 03 · Checkpoint 1 — gate">
          <p>Unknown name, no further work.</p>
        </div>
      </div>

      <div class="block" data-note-anchor="s03-cp2" data-note-title="§ 03 · Checkpoint 2 — Does a samplesheet exist?">
        <h3>2 · Has anyone built the sample list?</h3>
        <p>It looks for the samplesheet § 02 was supposed to write. Because § 02 drops any
        earlier configuration from the session whenever it rebuilds, arriving here always
        means the settings are about to be built fresh against the current rows.</p>
        <div class="gate" data-note-anchor="s03-cp2-gate" data-note-title="§ 03 · Checkpoint 2 — gate">
          <p>No sheet, no configuration — go back and build one first.</p>
        </div>
      </div>

      <div class="block" data-note-anchor="s03-cp3" data-note-title="§ 03 · Checkpoint 3 — Are the human-only answers valid?">
        <h3>3 · Are the values only a person could supply <em>sensible</em>?</h3>
        <p>A few pipelines need something that exists nowhere in the sample database and
        cannot be worked out from a file: a CRISPR guide sequence, a Hi-C digestion protocol,
        a set of PCR primers. Where such a value was given, it is checked against a pattern
        or a list of allowed answers.</p>
        <p>This catches the paste-level mistakes — a stray space in a sequence, an RNA letter
        where DNA was meant, a mode name that is not one of the real ones.</p>
        <div class="gate" data-note-anchor="s03-cp3-gate" data-note-title="§ 03 · Checkpoint 3 — gate">
          <p>The assistant is told to relay the problem and ask for a correction. It is
          explicitly forbidden from fixing the value itself.</p>
        </div>
      </div>

      <div class="block" data-note-anchor="s03-cp4" data-note-title="§ 03 · Checkpoint 4 — Is a human-only answer missing?">
        <h3>4 · Is anything only a person could supply still <em>blank</em>?</h3>
        <p>Smarter than a checklist, because some answers only become necessary depending on
        earlier ones. One pipeline needs a library only if you are running a screen. Another
        needs a digestion protocol only if you are <em>not</em> using the enzyme-free method.
        So the unanswered gate question is asked first, rather than demanding everything at
        once.</p>
        <p>When something is genuinely missing, the page the user sees is a written question:
        what the value is, what the options are, and an example to copy.</p>
        <div class="gate" data-note-anchor="s03-cp4-gate" data-note-title="§ 03 · Checkpoint 4 — the fail-closed gate">
          <p>It stops. The assistant relays the question and waits. It may not guess, may not
          use a placeholder, and may not carry on.</p>
          <p><strong>Why this is enforced in code and not in the assistant's instructions:</strong>
          an instruction can be forgotten twenty turns into a conversation. And it fails
          closed on purpose — a wrong guide sequence does not crash the run, it produces a
          perfectly normal-looking report with the wrong answer in it.</p>
        </div>
      </div>

      <div class="block" data-note-anchor="s03-cp5" data-note-title="§ 03 · Checkpoint 5 — Genome resolution">
        <h3>5 · Which genome?</h3>
        <p>§ 01 already voted on species. This checkpoint honours that unless someone has
        said otherwise — and an override is read three ways: it might name a reference bundle
        directly, it might name a species to look up, or it might be neither.</p>
        <p>Whatever wins is remembered, so configuring a second time without mentioning the
        genome again keeps the choice rather than reverting to the original guess.</p>
        <div class="warn" data-note-anchor="s03-warn-rawkey" data-note-title="§ 03 · Warning — the unchecked genome passthrough">
          <p>The third reading is an <strong>unchecked passthrough</strong>. Anything that
          matches neither a bundle nor a species is assumed to be a genome code the caller
          wants used verbatim, and is written into the settings file with nothing objecting.</p>
          <p>That exists so someone can ask for a specific build. It also means a stale or
          invented code goes straight through, and the error surfaces on the cluster rather
          than in the conversation. This is the single place where the assistant's guess is
          not re-checked.</p>
        </div>
      </div>

      <div class="block" data-note-anchor="s03-cp6" data-note-title="§ 03 · Checkpoint 6 — Allowlist and merge">
        <h3>6 · Assemble the settings</h3>
        <p>Three things happen. Any setting not on this pipeline's curated menu is rejected
        outright rather than passed along hopefully. Settings with a fixed list of choices are
        checked against it. Then three layers stack, each overriding the last: the pipeline's
        defaults, then the genome and reference paths, then anything specifically asked for.</p>
        <p>If any check fails, it returns <em>nothing</em> rather than a half-built set — you
        get the errors, never a partial configuration.</p>
        <details class="tech">
          <summary>Show what the merge produces</summary>
          <div class="tech-body">
            <p>It also reports where the genome came from, which matters more than it sounds:
            a real reference file already on the cluster, an explicitly configured path, or
            just a name that has to be fetched. A local file always wins, and the page the
            user is shown names the actual file.</p>
          </div>
        </details>
        <div class="warn" data-note-anchor="s03-warn-enumonly" data-note-title="§ 03 · Warning — only fixed-choice values are checked">
          <p>Only settings with a fixed list of options are validated. Numbers, switches and
          file paths pass straight through to the pipeline's own checking — so a typo'd path
          is not caught here, it is caught when the job dies.</p>
        </div>
        <div class="warn" data-note-anchor="s03-warn-storeroot" data-note-title="§ 03 · Warning — half the reference machinery is dormant">
          <p>The route that emits explicit reference file paths is switched off entirely today
          — the setting that turns it on is empty. Every run currently resolves to either a
          local cluster genome or a bare genome name. The code for the third option exists and
          has never been exercised.</p>
        </div>
      </div>

      <div class="block" data-note-anchor="s03-cp7" data-note-title="§ 03 · Checkpoint 7 — Write the files">
        <h3>7 · Write the two files</h3>
        <p>The settings become <span class="mono">params.yml</span>; a short entry naming the
        run, the pipeline and the pinned version becomes <span class="mono">launch.yml</span>.
        Nothing is uploaded anywhere — the cluster reads the short file and rebuilds the rest
        itself.</p>
        <p>Then the run plan is recorded. This is the only step that produces it, and the
        submit stage cannot fire without it — so there is no route from a conversation to a
        running job that skips any of the six checks above.</p>
      </div>

      <div class="ext" data-note-anchor="s03-ext" data-note-title="§ 03 · Adding a pipeline — configure_run">
        <p><strong>Declare:</strong> the menu of settings that may be touched, which reference
        files the pipeline wants, and any answers only a person can give.</p>
        <p><strong>This is the step that comes closest to being purely data</strong> — most
        pipelines are added here without touching a line of code.</p>
        <p><strong>But not all of them.</strong> crisprseq could not be added until someone
        built checkpoints 3 and 4 — the entire mechanism for asking a person a question and
        refusing to proceed without an answer. This step is data-only today because that
        machinery already exists. It exists because a pipeline demanded it.</p>
        <p><strong>And four of the six defects found on 2026-08-05 were curation errors at
        this step:</strong> detaxizer staged a 64 GB database despite a flag meant to skip it;
        mag and bacass were handed parameter combinations their own code rejects; ampliseq was
        missing its primer questions entirely, so a run launched without them; and
        seqinspector's default settings asked for an alignment index that does not exist
        here.</p>
        <p><strong>The cause, in the session's own words:</strong> do not trust a template's
        <span class="mono">skip_*</span> flags without reading the pipeline's actual gating
        code. That assumption was wrong four times in one session.</p>
      </div>
    </section>
```

- [ ] **Step 2: Verify § 03's structure**

Reload with `force: true`, then:

```js
(function(){
  var s = document.getElementById("s03");
  var ids = Array.prototype.slice.call(s.querySelectorAll("[data-note-anchor]"))
    .map(function(el){ return el.getAttribute("data-note-anchor"); });
  return JSON.stringify({
    checkpointHeadings: s.querySelectorAll("h3").length,
    gatesInS03: s.querySelectorAll(".gate").length,
    warnsInS03: s.querySelectorAll(".warn").length,
    extsInS03: s.querySelectorAll(".ext").length,
    hasAllCheckpoints: ["s03-cp1","s03-cp2","s03-cp3","s03-cp4","s03-cp5","s03-cp6","s03-cp7"]
      .every(function(id){ return ids.indexOf(id) > -1; }),
    totalAnchorsOnPage: document.querySelectorAll("[data-note-anchor]").length
  });
})()
```

Expected: `checkpointHeadings` is `7`, `gatesInS03` is `4`, `warnsInS03` is `3`, `extsInS03` is `1`, `hasAllCheckpoints` is `true`, `totalAnchorsOnPage` is `32`.

- [ ] **Step 3: Verify no duplicate anchor ids were introduced**

```js
(function(){
  var seen = {}, dupes = [];
  document.querySelectorAll("[data-note-anchor]").forEach(function(el){
    var id = el.getAttribute("data-note-anchor");
    if (seen[id]) dupes.push(id); seen[id] = 1;
  });
  return JSON.stringify({ dupes: dupes, railCountS03: (function(){
    document.getElementById("notes-toggle").click();
    NOTES.set("s03-cp4","test"); 
    var c = document.querySelector('#rail-list a[href="#s03"] .rail-count').textContent;
    NOTES.replaceAll({});
    return c;
  })() });
})()
```

Expected: `dupes` is `[]`, `railCountS03` is `"1"` — proving § 03's nested blocks are counted against § 03 in the rail.

- [ ] **Step 4: Verification checkpoint**

No commit. Record: 7 checkpoints present, 4 gates, 3 warnings, 27 anchors total, no duplicates, rail attribution correct.

---

## Task 7: Content — § 04, § 05, § 06

**Block vocabulary reminder (do not look this up elsewhere):** `.section` for a `§`; `.block` for an anchorable unit; `.substeps` for the numbered `<ol>`; `.gate` for a refusal; `.warn` for a silent continue; `.ext` for the "Adding a pipeline here" panel; `.tech` for a `<details>` technical panel with a `.tech-body` inside; `.mono` for identifiers. CSS supplies the label text on `.gate`, `.warn` and `.ext`.

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — REGION: SECTION 04, 05, 06

**Interfaces:**
- Consumes: block vocabulary and anchor contract from Task 1.
- Produces: anchors `s04-*`, `s05-*`, `s06-*`.

- [ ] **Step 1: Fill § 04**

Replace `<!-- REGION: SECTION 04 -->` with:

```html
    <!-- REGION: SECTION 04 -->
    <section class="section" id="s04" data-note-anchor="s04-section" data-note-title="§ 04 · Onto the cluster">
      <span class="sec-num">04</span>
      <h2>Onto the cluster.</h2>
      <p class="lede">The tool is <span class="mono">submit_to_luria</span>. It takes the two
      files and turns them into a job in the queue.</p>

      <div class="block" data-note-anchor="s04-substeps" data-note-title="§ 04 · The sub-steps">
        <ol class="substeps">
          <li>Carry the resolved genome through from § 03.</li>
          <li>Correct one flag that differs between the cluster's local reference files and
          the public ones — the local genomes are formatted differently, and the setting has
          to follow the genome.</li>
          <li>Write the job script and a config naming the local reference files.</li>
          <li>Copy four files across: the job script, the config, the settings, the sheet.</li>
          <li>Submit to the queue.</li>
        </ol>
      </div>

      <div class="warn" data-note-anchor="s04-warn-grch38" data-note-title="§ 04 · Warning — the silent human default">
        <p><strong>The most consequential mark on this page.</strong> If no genome was
        resolved anywhere — the metadata named no species this system recognises, and nobody
        overrode it — the submitter fills in the human genome and prints a warning telling
        you to verify the samples really are human.</p>
        <p>That warning goes into a job log. Nobody is reading a job log at the moment of
        submission. Unlike the missing-CRISPR-guide gate, this does not stop the run: mouse
        samples aligned against a human genome will produce results, and those results will
        look like results.</p>
      </div>

      <div class="ext" data-note-anchor="s04-ext" data-note-title="§ 04 · Adding a pipeline — submit_to_luria">
        <p><strong>Declare:</strong> the pinned version, and which reference flags this
        pipeline's own checker actually accepts.</p>
        <p><strong>Risk:</strong> get that list wrong and the run dies on the spot, because
        the checker aborts on any setting it does not recognise. Passing a gene-annotation
        flag to a pipeline that does not declare one kills it immediately — and fourteen of
        the thirty-one catalogued pipelines accept none of the three.</p>
        <p><strong>Real defect, 2026-08-05:</strong> the stage that fetches files before a run
        was filling the wrong column — across five pipelines at once.</p>
      </div>
    </section>
```

- [ ] **Step 2: Fill § 05**

Replace `<!-- REGION: SECTION 05 -->` with:

```html
    <!-- REGION: SECTION 05 -->
    <section class="section" id="s05" data-note-anchor="s05-section" data-note-title="§ 05 · What the user is told">
      <span class="sec-num">05</span>
      <h2>What you are actually told.</h2>
      <p class="lede">Before anything is submitted, the assistant is required to report back
      — and the instructions are specific about the reference genome, because that is the
      claim most worth checking.</p>

      <div class="block" data-note-anchor="s05-reporting" data-note-title="§ 05 · The three things it must say">
        <p>It must say which pipeline and version, how the samples were grouped, and — the
        important part — where the genome came from, in one of three ways:</p>
        <ol class="substeps">
          <li>A real reference file already on the cluster, <em>named</em>, so you can check it.</li>
          <li>No local file configured, so the run will fetch a public genome by name.</li>
          <li>References cannot be set at all for this combination — said plainly rather than
          worked around.</li>
        </ol>
        <p>Then it asks you to confirm or change something. It is explicitly told not to treat
        this as finished.</p>
      </div>

      <div class="ext" data-note-anchor="s05-ext" data-note-title="§ 05 · Adding a pipeline — nothing">
        <p><strong>Declare: nothing.</strong> This step is entirely generic.</p>
        <p>The empty answer is itself the point — it shows how much of the machinery a new
        pipeline genuinely does not touch.</p>
      </div>
    </section>
```

- [ ] **Step 3: Fill § 06**

Replace `<!-- REGION: SECTION 06 -->` with:

```html
    <!-- REGION: SECTION 06 -->
    <section class="section" id="s06" data-note-anchor="s06-section" data-note-title="§ 06 · How far this stretches">
      <span class="sec-num">06</span>
      <h2>How far this stretches.</h2>
      <p class="lede">The claim is that adding a pipeline costs two files and no code. Across
      the four stages that do real work, it holds at two of them, breaks at one, and has
      broken exactly once — for a single pipeline — at a third.</p>

      <div class="block" data-note-anchor="s06-verdict" data-note-title="§ 06 · Where the claim held and where it leaked">
        <table>
          <tr><th>Stage</th><th>What a new pipeline costs</th></tr>
          <tr><td>§ 01 resolve</td><td>Declared types only. Held.</td></tr>
          <tr><td>§ 02 samplesheet</td><td><strong>Leaked</strong> — renames and name rules live in code.</td></tr>
          <tr><td>§ 03 configure</td><td>Almost pure data — one pipeline forced a code change.</td></tr>
          <tr><td>§ 04 submit</td><td>Declared flags only — but wrong flags kill the run instantly.</td></tr>
        </table>
      </div>

      <div class="block" data-note-anchor="s06-numbers" data-note-title="§ 06 · The numbers">
        <h3>The numbers, and where each comes from</h3>
        <table>
          <tr><th>Figure</th><th>Value</th><th>Source</th></tr>
          <tr><td>nf-core pipelines in existence</td><td>138</td><td>2026-08-05 schema census</td></tr>
          <tr><td>…with no release to pin to</td><td>42</td><td>same</td></tr>
          <tr><td>…that could be assessed</td><td>96</td><td>schema census file</td></tr>
          <tr><td>…judged runnable from config alone</td><td>27</td><td>same</td></tr>
          <tr><td>Catalogued here</td><td>31</td><td>the curated pipeline files</td></tr>
          <tr><td>…needing an answer only a person can give</td><td>9</td><td>those files</td></tr>
          <tr><td>…declaring reference files</td><td>10</td><td>those files</td></tr>
          <tr><td>Genomes known / spellings accepted</td><td>5 / 14</td><td>the reference bundle registry</td></tr>
          <tr><td><strong>Ever run on the cluster</strong></td><td><strong>2</strong></td><td>2026-08-05 session report</td></tr>
        </table>
        <p>31 are catalogued rather than 27, under a rule set deliberately: having no matching
        data in the database is a label on the row, not a reason to reject the pipeline.</p>
      </div>

      <div class="warn" data-note-anchor="s06-warn-unverified" data-note-title="§ 06 · Warning — what 'catalogued' does and does not mean">
        <p>Thirty-one pipelines are catalogued. Two have ever run on the cluster: one
        completed, one launches and then fails partway through.</p>
        <p>The remaining twenty-nine are flexible in the sense that the machinery accepts them
        and produces plausible files. Whether those files survive contact with the cluster is
        untested.</p>
        <p>The fairest summary is the sentence from the session that first ran any of them:
        <strong>every pipeline examined closely that session had a defect in it</strong> — and
        six defects were found in total, none of which the unit tests had caught.</p>
      </div>
    </section>
```

- [ ] **Step 4: Verify the full page structure**

Reload with `force: true`, then:

```js
(function(){
  var seen = {}, dupes = [];
  document.querySelectorAll("[data-note-anchor]").forEach(function(el){
    var id = el.getAttribute("data-note-anchor");
    if (seen[id]) dupes.push(id); seen[id] = 1;
  });
  return JSON.stringify({
    sections: document.querySelectorAll("main .section").length,
    anchors: document.querySelectorAll("[data-note-anchor]").length,
    addButtons: document.querySelectorAll(".note-add").length,
    gates: document.querySelectorAll(".gate").length,
    warns: document.querySelectorAll(".warn").length,
    exts: document.querySelectorAll(".ext").length,
    dupes: dupes,
    missingTitles: Array.prototype.slice.call(document.querySelectorAll("[data-note-anchor]"))
      .filter(function(el){ return !el.getAttribute("data-note-title"); }).length,
    unfilledRegions: (document.documentElement.innerHTML.match(/filled in by Task/g) || []).length
  });
})()
```

Expected: `sections` is `7`, `anchors` is `43`, `addButtons` equals `anchors`, `gates` is `7`, `warns` is `8`, `exts` is `6`, `dupes` is `[]`, `missingTitles` is `0`, `unfilledRegions` is `0`.

`exts` being 6 confirms the spec's rule: a real extension panel on § 01–§ 05 (five of them), plus the one in § 00's legend that introduces what the panel is. None on § 06, because § 06 is the aggregate of all of them.

- [ ] **Step 5: Verify every rail link resolves to a real section**

```js
JSON.stringify(Array.prototype.slice.call(document.querySelectorAll("#rail-list a"))
  .map(function(a){ return { href: a.getAttribute("href"),
                             found: !!document.querySelector(a.getAttribute("href")) }; }))
```

Expected: all seven entries have `found: true`.

- [ ] **Step 6: Verification checkpoint**

No commit. Record: 7 sections, 35 anchors, 7 gates, 7 warnings, 5 extension panels, no duplicates, no unfilled regions, all rail links resolve.

---

## Task 8: The three diagrams

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — insert SVGs into § 00, § 03 and § 01; add figure styles to REGION: BLOCK VOCABULARY

**Interfaces:**
- Consumes: the design tokens `--gate`, `--warn`, `--ink-faint`, `--rule` from Task 1.
- Produces: `.fig` figure blocks, each anchorable.

- [ ] **Step 1: Add figure styles**

Append to the **REGION: BLOCK VOCABULARY** style block:

```css
.fig { margin: 0 0 1.6rem; }
.fig svg { width: 100%; height: auto; display: block; }
.fig figcaption { font-size: 0.78rem; color: var(--ink-faint); margin-top: 0.4rem; }
.fig .n-label { font-family: var(--mono); font-size: 9px; fill: var(--ink-faint); }
.fig .n-title { font-family: var(--mono); font-size: 11px; fill: var(--ink); }
.fig .n-box { fill: #fff; stroke: var(--rule); stroke-width: 1.5; }
.fig .n-gate { stroke: var(--gate); stroke-width: 2.5; }
.fig .n-warn { stroke: var(--warn); stroke-width: 2.5; stroke-dasharray: 4 3; }
.fig .n-flow { stroke: var(--ink-faint); stroke-width: 1.5; fill: none; }
```

- [ ] **Step 2: Diagram 1 — the five tools, into § 00**

Insert immediately before the closing `</section>` of § 00:

```html
      <figure class="fig block" data-note-anchor="s00-fig-loop" data-note-title="§ 00 · Diagram — the five tools">
        <svg viewBox="0 0 640 120" role="img" aria-label="Five tools in order: resolve samples, write samplesheet, configure run, submit, conclude">
          <g class="n-flow">
            <line x1="60" y1="52" x2="580" y2="52"></line>
          </g>
          <g>
            <rect class="n-box" x="16" y="36" width="88" height="32" rx="4"></rect>
            <text class="n-title" x="60" y="56" text-anchor="middle">resolve</text>
            <text class="n-label" x="60" y="84" text-anchor="middle">what to run on</text>
            <rect class="n-box" x="140" y="36" width="88" height="32" rx="4"></rect>
            <text class="n-title" x="184" y="56" text-anchor="middle">samplesheet</text>
            <text class="n-label" x="184" y="84" text-anchor="middle">the row set</text>
            <rect class="n-box n-gate" x="264" y="30" width="112" height="44" rx="4"></rect>
            <text class="n-title" x="320" y="50" text-anchor="middle">configure</text>
            <text class="n-label" x="320" y="64" text-anchor="middle">7 checkpoints</text>
            <text class="n-label" x="320" y="92" text-anchor="middle">params.yml + launch.yml</text>
            <rect class="n-box" x="412" y="36" width="88" height="32" rx="4"></rect>
            <text class="n-title" x="456" y="56" text-anchor="middle">submit</text>
            <text class="n-label" x="456" y="84" text-anchor="middle">sbatch</text>
            <rect class="n-box" x="536" y="36" width="88" height="32" rx="4"></rect>
            <text class="n-title" x="580" y="56" text-anchor="middle">conclude</text>
            <text class="n-label" x="580" y="84" text-anchor="middle">tell the user</text>
          </g>
          <text class="n-label" x="320" y="18" text-anchor="middle">the only step that writes the run plan</text>
        </svg>
        <figcaption>Five tools, in order. The third is outlined because it is the only one
        that produces the run plan — nothing can be submitted without it.</figcaption>
      </figure>
```

- [ ] **Step 3: Diagram 2 — the gauntlet, into § 03**

Insert immediately after the § 03 opening `<p>` about calling four pieces in a fixed order:

```html
      <figure class="fig block" data-note-anchor="s03-fig-gauntlet" data-note-title="§ 03 · Diagram — the seven checkpoints">
        <svg viewBox="0 0 640 250" role="img" aria-label="Seven checkpoints; four stop the run, three let it continue">
          <line class="n-flow" x1="80" y1="20" x2="80" y2="230"></line>
          <g>
            <line class="n-gate" x1="56" y1="40" x2="104" y2="40"></line>
            <text class="n-title" x="120" y="44">1 · known pipeline?</text>
            <text class="n-label" x="470" y="44">stops</text>
            <line class="n-gate" x1="56" y1="70" x2="104" y2="70"></line>
            <text class="n-title" x="120" y="74">2 · samplesheet exists?</text>
            <text class="n-label" x="470" y="74">stops</text>
            <line class="n-gate" x1="56" y1="100" x2="104" y2="100"></line>
            <text class="n-title" x="120" y="104">3 · supplied answers valid?</text>
            <text class="n-label" x="470" y="104">stops</text>
            <line class="n-gate" x1="56" y1="130" x2="104" y2="130"></line>
            <text class="n-title" x="120" y="134">4 · answers missing?</text>
            <text class="n-label" x="470" y="134">stops — asks you</text>
            <line class="n-warn" x1="80" y1="160" x2="150" y2="160"></line>
            <text class="n-title" x="166" y="164">5 · which genome?</text>
            <text class="n-label" x="470" y="164">continues, unchecked</text>
            <line class="n-warn" x1="80" y1="190" x2="150" y2="190"></line>
            <text class="n-title" x="166" y="194">6 · assemble settings</text>
            <text class="n-label" x="470" y="194">continues, part-checked</text>
            <circle cx="80" cy="220" r="5" fill="none" stroke="#767d88" stroke-width="1.5"></circle>
            <text class="n-title" x="120" y="224">7 · write the two files</text>
          </g>
        </svg>
        <figcaption>Four checkpoints stop the run. Two let it through and can be wrong —
        those are the two worth arguing about.</figcaption>
      </figure>
```

- [ ] **Step 4: Diagram 3 — species resolution, into § 01**

Insert immediately after the § 01 species `<div class="block">` closing tag:

```html
      <figure class="fig block" data-note-anchor="s01-fig-species" data-note-title="§ 01 · Diagram — how the genome is chosen">
        <svg viewBox="0 0 640 200" role="img" aria-label="Species resolution in three stages: table lookup, assistant inference, re-check">
          <rect class="n-box" x="16" y="20" width="170" height="52" rx="4"></rect>
          <text class="n-title" x="101" y="42" text-anchor="middle">1 · table lookup</text>
          <text class="n-label" x="101" y="58" text-anchor="middle">14 exact strings</text>

          <path class="n-flow" d="M186 46 H236" marker-end="url(#a1)"></path>
          <text class="n-label" x="211" y="38" text-anchor="middle">no match</text>

          <rect class="n-box" x="236" y="20" width="170" height="52" rx="4"></rect>
          <text class="n-title" x="321" y="42" text-anchor="middle">2 · the assistant</text>
          <text class="n-label" x="321" y="58" text-anchor="middle">infers from strain etc.</text>

          <path class="n-flow" d="M406 46 H456"></path>
          <rect class="n-box" x="456" y="20" width="170" height="52" rx="4"></rect>
          <text class="n-title" x="541" y="42" text-anchor="middle">3 · re-check</text>
          <text class="n-label" x="541" y="58" text-anchor="middle">same table</text>

          <path class="n-flow" d="M101 72 V120"></path>
          <text class="n-title" x="101" y="140" text-anchor="middle">genome set</text>
          <path class="n-flow" d="M541 72 V110"></path>
          <text class="n-label" x="541" y="126" text-anchor="middle">matched → genome set</text>
          <path class="n-warn" d="M600 72 V150"></path>
          <text class="n-label" x="600" y="168" text-anchor="end">no match → passed through unchecked</text>
          <line class="n-warn" x1="101" y1="160" x2="101" y2="185"></line>
          <text class="n-label" x="101" y="196" text-anchor="middle">nothing at all → human genome assumed at § 04</text>
        </svg>
        <figcaption>Two exits are checked. The dashed ones are not: an unrecognised genome
        name goes through verbatim, and no species at all becomes the human genome by
        default two stages later.</figcaption>
      </figure>
```

- [ ] **Step 5: Verify the diagrams render and did not break the anchor count**

Reload with `force: true`, then:

```js
JSON.stringify({
  figures: document.querySelectorAll("figure.fig").length,
  svgs: document.querySelectorAll("figure.fig svg").length,
  anchors: document.querySelectorAll("[data-note-anchor]").length,
  addButtons: document.querySelectorAll(".note-add").length,
  everySvgHasSize: Array.prototype.slice.call(document.querySelectorAll("figure.fig svg"))
    .every(function(s){ return s.getBoundingClientRect().width > 100; }),
  allLabelled: Array.prototype.slice.call(document.querySelectorAll("figure.fig svg"))
    .every(function(s){ return !!s.getAttribute("aria-label"); })
})
```

Expected: `figures` is `3`, `svgs` is `3`, `anchors` is `46`, `addButtons` is `46`, `everySvgHasSize` is `true`, `allLabelled` is `true`.

- [ ] **Step 6: Look at it**

Take a screenshot with `mcp__Claude_Browser__computer` (`action: "screenshot"`) and actually examine each diagram. Check specifically: no overlapping text, no text running outside its box, the dashed warning strokes visibly different from the solid gate strokes. Fix any overlap by adjusting coordinates — do not leave a diagram that is technically present but unreadable.

- [ ] **Step 7: Verification checkpoint**

No commit. Record: 3 diagrams present and sized, 38 anchors, visual inspection done and what it showed.

---

## Task 9: Full verification pass against the spec

The spec's § 9 lists seven checks. This task runs all of them on the finished page and reports honestly, including failures.

**Files:**
- Modify: `~/Documents/MIT/MeetingNotes/nfcore-launch-path.html` — only if a check fails

- [ ] **Step 1: Notes round-trip across a reload**

```js
(function(){
  NOTES.replaceAll({});
  NOTES.set("s03-cp5", "ask Taisha whether we ever pass a raw genome key");
  return JSON.stringify({ before: NOTES.get("s03-cp5").text });
})()
```

Reload with `force: true`, then:

```js
JSON.stringify({
  after: NOTES.get("s03-cp5").text,
  cardRendered: !!document.querySelector('[data-note-anchor="s03-cp5"] .note-card'),
  toggle: document.getElementById("notes-toggle").textContent
})
```

Expected: `after` matches, `cardRendered` is `true`, `toggle` is `"1 note"`.

- [ ] **Step 2: Markdown export names the right block**

```js
(function(){ var md = window.__notesMarkdown();
  return JSON.stringify({
    hasHeading: md.indexOf("## § 03 · Checkpoint 5 — Genome resolution") > -1,
    hasText: md.indexOf("ask Taisha") > -1 }); })()
```

Expected: both `true`.

- [ ] **Step 3: JSON backup and restore, by hand**

Download the `.json`, run `NOTES.replaceAll({}); location.reload();`, then restore from the file. Confirm the note reappears on `s03-cp5` and the count returns to 1. Record the result — including if the browser blocks the file picker on `file://`.

- [ ] **Step 4: N targets the block in view**

```js
(function(){
  document.getElementById("notes-drawer").hidden = true;
  document.querySelector('[data-note-anchor="s06-numbers"]').scrollIntoView({block:"center"});
  document.dispatchEvent(new KeyboardEvent("keydown", {key:"n", bubbles:true}));
  var open = document.querySelector(".note-editor");
  return JSON.stringify({ anchor: open ? open.parentElement.getAttribute("data-note-anchor") : null });
})()
```

Expected: `anchor` is `"s06-numbers"`.

- [ ] **Step 5: Rail counts update as notes are added and removed**

```js
(function(){
  NOTES.replaceAll({});
  var read = function(){ return Array.prototype.slice.call(document.querySelectorAll("#rail-list a"))
    .map(function(a){ return a.querySelector(".rail-count").textContent; }).join("|"); };
  var empty = read();
  NOTES.set("s01-species","a"); NOTES.set("s01-gate-noleaves","b"); NOTES.set("s06-numbers","c");
  var filled = read();
  NOTES.remove("s01-species");
  var after = read();
  NOTES.replaceAll({});
  return JSON.stringify({ empty: empty, filled: filled, after: after });
})()
```

Expected: `empty` is all blanks; `filled` shows `2` against § 01 and `1` against § 06; `after` shows `1` against § 01.

- [ ] **Step 6: A renamed anchor surfaces as orphaned, never lost**

```js
(function(){
  NOTES.replaceAll({});
  NOTES.set("s02-gate-invalidate", "this block is about to be renamed");
  var el = document.querySelector('[data-note-anchor="s02-gate-invalidate"]');
  el.setAttribute("data-note-anchor", "s02-gate-renamed");
  document.getElementById("notes-toggle").click();
  window.__refreshNotesUI();
  var out = { orphanSection: !!document.querySelector(".drawer-orphans"),
              orphanCount: document.querySelectorAll(".drawer-orphans .drawer-item").length,
              stillStored: !!NOTES.get("s02-gate-invalidate") };
  el.setAttribute("data-note-anchor", "s02-gate-invalidate");
  NOTES.replaceAll({});
  window.__refreshNotesUI();
  return JSON.stringify(out);
})()
```

Expected: `orphanSection` is `true`, `orphanCount` is `1`, `stillStored` is `true`.

- [ ] **Step 7: No console errors on a clean load**

Reload with `force: true`, then run `mcp__Claude_Browser__read_console_messages` with `onlyErrors: true`.
Expected: empty.

- [ ] **Step 8: Nothing external, and it is genuinely one file**

```bash
grep -nE 'https?://|fetch\(|<link|<script src|@import|url\(http' ~/Documents/MIT/MeetingNotes/nfcore-launch-path.html
```

Expected: no matches.

```bash
ls -la ~/Documents/MIT/MeetingNotes/
```

Expected: `nfcore-launch-path.html` present, and no sidecar `.css`/`.js`/asset files were created next to it.

- [ ] **Step 9: Confirm the storage key is exactly right**

```js
JSON.stringify({ keys: Object.keys(localStorage).filter(function(k){ return k.indexOf("nfcore") > -1; }) })
```

Expected: `["nfcore-launch-path.notes.v1"]` when at least one note exists, `[]` when none do. Any other key name is a bug — all `file://` pages share one origin, so a generic key would collide with other local files.

- [ ] **Step 10: Clean the page of test notes**

```js
NOTES.replaceAll({}); location.reload();
```

Confirm the topbar reads `0 notes` and no note cards are visible. The page must be handed over clean.

- [ ] **Step 11: Report**

Write a short plain-text summary of every check in Steps 1–10: what passed, what failed, and for anything that failed, what it means for using the page. Do not claim the page works without this list. If Step 3 or Step 8 revealed a browser limitation on `file://`, say so explicitly — the user needs to know before presenting from it.

---

## Self-Review

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| § 1 Purpose / audience (plain spine, collapsed technical) | Tasks 5–7 prose + `.tech` panels |
| § 2 Scope — full loop, run order, problems inline | Tasks 5, 6, 7 |
| § 3.1 Gate mark | Task 1 CSS, used Tasks 5–7 (7 gates) |
| § 3.2 Warning mark | Task 1 CSS, used Tasks 5–7 (7 warnings) |
| § 3.3 Extension panel, § 01–§ 05 only | Tasks 5–7, asserted in Task 7 Step 4 (`exts` = 5) |
| § 4 § 00 through § 06 content incl. numbers table | Tasks 5, 6, 7 |
| § 5.1 Anchors, stable hand-written ids | Task 1 contract; duplicate check in Tasks 5, 6, 7 |
| § 5.2 Hover affordance, visible cards, debounce, `N` | Tasks 2 and 3 |
| § 5.3 Rail counts | Task 3, re-verified Task 9 Step 5 |
| § 5.4 Drawer in page order | Task 3 |
| § 5.5 localStorage, version field, feature detection, banner | Task 2 |
| § 5.6 Markdown + JSON export, dated filenames | Task 4 |
| § 5.7 Orphan handling | Task 3, re-verified Task 9 Step 6 |
| § 6 Visual design, spine, two accents | Task 1 |
| § 6 Three diagrams | Task 8 |
| § 7 Mechanics, self-contained, no build | Global Constraints; asserted Task 1 Step 5 and Task 9 Step 8 |
| § 8 Commit footer `0ac6571` | Task 1 skeleton |
| § 9 Verification, all seven checks | Task 9 |
| § 10 Out of scope | Nothing in this plan builds search, print CSS, dark mode, sharing, or a server |

No gaps found.

**Placeholder scan:** The only "filled in by Task N" strings are intentional insertion markers inside the built file, each removed by the task that owns it, and Task 7 Step 4 asserts `unfilledRegions` is `0`. No TBDs, no "add error handling", no "similar to Task N" — the block vocabulary is restated in full in every content task.

**Type consistency:** `NOTES.get/set/remove/all/count/replaceAll/isMemoryOnly/onChange` are defined in Task 2 and used under exactly those names in Tasks 3, 4 and 9. `window.__openNoteEditor` (Task 2) is called in Task 3. `window.__refreshNotesUI` (Task 3) is called in Task 4 and Task 9 Step 6. `window.__notesMarkdown` / `window.__notesFilename` (Task 4) are called in Task 9 Step 2. `data-note-anchor` and `data-note-title` are spelled identically throughout.

**Anchor count arithmetic** — recounted against the markup each task actually specifies, not estimated:

| After task | Section | Anchors added | Running total |
|---|---|---|---|
| 1 | § 00 stubs (`s00-overview`, `s00-stub-block`, `s00-stub-gate`) | 3 | **3** |
| 5 | § 00 rebuilt (`overview`, `legend`, `legend-gate`, `legend-warn`, `legend-ext`) | +5 −3 | 5 |
| 5 | § 01 (`section`, `substeps`, `species`, `gate-noleaves`, `warn-species`, `ext`) | +6 | 11 |
| 5 | § 02 (`section`, `substeps`, `gate-invalidate`, `warn-renames`, `ext`) | +5 | **16** |
| 6 | § 03 (`section`, 7 checkpoints, 4 checkpoint gates, 3 warnings, `ext`) | +16 | **32** |
| 7 | § 04 (`section`, `substeps`, `warn-grch38`, `ext`) | +4 | 36 |
| 7 | § 05 (`section`, `reporting`, `ext`) | +3 | 39 |
| 7 | § 06 (`section`, `verdict`, `numbers`, `warn-unverified`) | +4 | **43** |
| 8 | three figures | +3 | **46** |

Mark totals on the finished page: 7 gates, 8 warnings, 6 extension panels (five real ones on § 01–§ 05, plus the legend's example in § 00).

If a count differs at execution time, recount against the markup actually written and correct the plan — do not edit the page to force the number.
