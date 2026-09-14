// Verify nfcore-eval-report.html actually renders.
//
// This exists because the page once shipped completely inert: an apostrophe
// inside a single-quoted JS string threw a SyntaxError that killed the whole
// script, and every section rendered empty. By eye that is indistinguishable
// from "the section has no data" — it was caught by a human clicking through,
// not by any check. So the page script gets executed against a stubbed DOM
// and every section is asserted non-empty.
//
//   node chat_nextseek/evals/check_report_page.js
//
// Exits non-zero on a syntax error, a thrown exception, or an empty section.

const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const PAGE = path.join(os.homedir(), 'Documents/MIT/MeetingNotes/nfcore-eval-report.html');
const html = fs.readFileSync(PAGE, 'utf8');

// --- pull the JSON data block and the page script out of the HTML
const dataMatch = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/);
if (!dataMatch) { console.error('FAIL: no data block found'); process.exit(1); }
let DATA;
try { DATA = JSON.parse(dataMatch[1]); }
catch (e) { console.error('FAIL: data block is not valid JSON —', e.message); process.exit(1); }

const scripts = [...html.matchAll(/<script(?![^>]*application\/json)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (!scripts.length) { console.error('FAIL: no page script found'); process.exit(1); }

// --- minimal DOM stub: enough for innerHTML assembly and appendChild
const store = new Map();
function mkEl(tag) {
  const e = {
    tagName: tag, className: '', _html: '', children: [], style: {}, dataset: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, setAttribute() {}, getAttribute() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  };
  Object.defineProperty(e, 'innerHTML', {
    get() { return this._html; },
    set(v) { this._html = String(v); },
  });
  Object.defineProperty(e, 'textContent', {
    get() { return this._text || ''; },
    set(v) { this._text = String(v); },
  });
  return e;
}
// Every id referenced in the markup gets a live node so getElementById never
// returns null — a null here would mask exactly the failure we are hunting.
for (const m of html.matchAll(/id="([^"]+)"/g)) store.set(m[1], mkEl('div'));

// The page bootstraps with JSON.parse(getElementById('data').textContent), so
// that one node must carry the real JSON or every script dies on an empty
// parse — a verifier failure that looks exactly like a page failure.
store.get('data').textContent = dataMatch[1];

const document = {
  getElementById: id => store.get(id) || null,
  createElement: mkEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  body: mkEl('body'),
};
const sandbox = {
  document, console,
  window: { addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) },
  IntersectionObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: fn => fn(),
  setTimeout, clearTimeout,
};
sandbox.window.document = document;
sandbox.globalThis = sandbox;

// --- execute
try {
  vm.createContext(sandbox);
  for (const [i, code] of scripts.entries()) {
    try { vm.runInContext(code, sandbox, { filename: `page-script-${i}.js` }); }
    catch (e) { console.error(`FAIL: script ${i} threw — ${e.name}: ${e.message}`); process.exit(1); }
  }
} catch (e) {
  console.error('FAIL: could not execute page —', e.message);
  process.exit(1);
}

// --- assert every content section actually got filled
// Only the sections measured under the CURRENT atlas survive. Everything else
// was purged rather than annotated, because a page that mixes atlas vintages is
// how three conclusions got reported today that did not survive rechecking.
const REQUIRED = ['meta', 'payload', 'steps', 'questions_eval', 'payload_arms', 'team_current', 'notes'];
let bad = 0;
for (const id of REQUIRED) {
  const node = store.get(id);
  if (!node) { console.error(`FAIL: #${id} not present in the markup`); bad++; continue; }
  const filled = (node._html && node._html.length > 0) || node.children.length > 0;
  if (!filled) { console.error(`FAIL: #${id} rendered empty`); bad++; continue; }
  const size = node._html ? node._html.length : `${node.children.length} children`;
  console.log(`  ok  #${id.padEnd(12)} ${size}`);
}

// --- content assertions: the numbers a reader will quote must be on the page
const qe = store.get('questions_eval')._html;
const arms = store.get('payload_arms')._html;
const team = store.get('team_current')._html;
const Q = DATA.questions || {};
const cases = Q.cases || [];
const PA = DATA.payload_arms || {};
const PCASES = PA.cases || [];
const ARMS_R2 = PA.arms_r2 || [];
const purged = ['team', 'datafit', 'datafit_rna', 'ablation', 'datafit_arms',
                'atlas_compare', 'all19', 'granuloma', 'macrophage'];

const checks = [
  [!!Q.cases, 'question-case data is present'],
  [cases.length > 0 && cases.every(c => (c.verdicts || []).length === Q.repeats),
   `every case carries all ${Q.repeats} repeats`],
  [qe.includes(Q.overall.n_correct + '/' + Q.overall.n_scored),
   `overall score is rendered (${Q.overall ? Q.overall.n_correct + '/' + Q.overall.n_scored : 'n/a'})`],
  // The baseline is the only thing that makes the score mean anything, so its
  // absence is a failure even though the page would still look complete.
  [qe.includes(Q.baseline.n_correct + '/' + Q.baseline.n_total),
   `ignore-the-question baseline is rendered (${Q.baseline.n_correct}/${Q.baseline.n_total})`],
  [/baseline is the number to read against/i.test(qe),
   'the page says what the baseline means'],
  [['matched', 'reframed', 'unsupported', 'ambiguous', 'output'].every(k => qe.includes(k)),
   'all five case classes appear'],
  // The output class exists to test the documentation specifically, so the
  // comparison must report it rather than only the totals.
  [!DATA.payload_arms || (DATA.payload_arms.arms || []).every(
     a => (a.per_class.output || {}).n_total > 0),
   'both arms report the output class'],
  [!DATA.payload_arms || /output/.test(arms), 'the output class is named in the comparison'],
  [cases.filter(c => !c.correct).every(c => qe.includes(c.question)),
   'every failing case is shown with its question'],
  // A purge that leaves the data behind is not a purge.
  [purged.every(k => !(k in DATA)),
   `no data from an earlier atlas survives (${purged.filter(k => k in DATA).join(', ') || 'clean'})`],
  [!/RNA-only data-fit|payload ablation|the earlier twelve-question/i.test(html.replace(/<!--.*?-->/gs, '')) ||
   !purged.some(k => k in DATA),
   'no purged section is still rendered'],

  // Payload arms: both must be present with their token costs, and the page
  // must not claim a winner the totals do not support.
  [!DATA.payload_arms || (DATA.payload_arms.arms || []).length === 2,
   'payload comparison carries both arms'],
  // The page formats numbers with toLocaleString; match that rather than
  // reaching for the page's own N, which does not exist in this scope.
  [!DATA.payload_arms || (DATA.payload_arms.arms || []).every(
     a => arms.includes(a.tokens.toLocaleString('en-US'))),
   'both arms show their token cost'],
  // A one-case gap must not be sold as a result: the page has to say so when
  // the arms are within noise, which is the failure mode this page has had all
  // day. A gap of two or more may be stated plainly.
  [!DATA.payload_arms ||
   Math.abs(DATA.payload_arms.arms[0].overall.n_correct -
            DATA.payload_arms.arms[1].overall.n_correct) > 1 ||
   /within noise/.test(arms),
   'a one-case difference is labelled as noise, not a win'],

  // The per-question table is the deliverable of this section: totals cannot
  // show WHICH questions the documentation changes, and a one-or-two case gap
  // is only readable per question. Every case must be in it, with both arms.
  [!DATA.payload_arms || PCASES.length === PA.arms[0].overall.n_total,
   `the per-question table covers all ${PCASES.length} cases`],
  [PCASES.every(c => arms.includes(c.question)),
   'every question is listed in the payload comparison'],
  [PCASES.every(c => c.full.length && c.cheap.length),
   'every case carries both payload arms'],
  // Per ROW, not per section: nearly every answer string also appears in some
  // other row, so a whole column can go missing and a section-wide substring
  // test still passes. It did, the first time this check was written.
  [PCASES.every(c => {
     const row = arms.split('<tr').filter(r => r.includes('>' + c.id + '<'));
     if (row.length !== 1) return false;
     const chips = (row[0].match(/class="chip/g) || []).length;
     return chips === c.full.length + c.cheap.length &&
       [].concat(c.full, c.cheap).every(
         x => row[0].includes((x.chosen || []).join(', ') || 'refused'));
   }),
   'each row shows what each payload actually answered'],
  // A second run exists for both arms; reporting only one is the n=1 failure
  // this page has already had to retract conclusions over.
  [!ARMS_R2.length || ARMS_R2.every(a => arms.includes(a.overall.n_correct + '/' + a.overall.n_scored)),
   `both runs of each arm are reported (${ARMS_R2.map(a => a.overall.n_correct + '/' + a.overall.n_scored).join(', ') || 'single run'})`],
  [!ARMS_R2.length || PCASES.every(c => c.full.length === 2 && c.cheap.length === 2),
   'every case shows both runs of both arms'],
  // Cases that move when nothing changes are the scale any payload difference
  // has to beat, so the page must say how many did rather than only totalling.
  [!ARMS_R2.length || !PCASES.some(c => c.moved) || /moved between runs/.test(arms),
   `run-to-run movement is marked (${PCASES.filter(c => c.moved).length} case(s) moved)`],

  // Team questions have no answer key, so stability is the only claim the page
  // may make about them — and it must match the data.
  [!DATA.team_current || (DATA.team_current.questions || []).every(q => q.answers.length === DATA.team_current.repeats),
   `every team question carries all ${DATA.team_current ? DATA.team_current.repeats : 0} repeats`],
  [!DATA.team_current || (DATA.team_current.n_unstable > 0) === /unstable:/.test(team),
   'instability is shown when present and not claimed when absent'],
  [!DATA.team_current || /no answer key here/i.test(team),
   'team section says it has no ground truth'],
  // Both payloads must be shown for every team question, and a differing
  // answer must be marked as differing -- the one that differs is the only
  // question in the set whose right answer is known.
  [!DATA.team_current || !DATA.team_current.has_cheap ||
   (DATA.team_current.questions || []).every(q => q.cheap_answers.length === DATA.team_current.repeats),
   'every team question carries both payload arms at full repeats'],
  // A differing answer must be called out in prose, not merely tinted: a row
  // colour is neither quotable nor accessible, and this particular difference
  // is the only checkable result in the set.
  [!DATA.team_current || !DATA.team_current.has_cheap ||
   (DATA.team_current.n_differing === 0) ===
     !/documentation is what changes this answer/.test(team),
   `differing team answers are called out (${DATA.team_current ? DATA.team_current.n_differing : 0} differ)`],
  [!DATA.team_current || !DATA.team_current.has_cheap ||
   (DATA.team_current.questions || []).every(q =>
     q.cheap_answers.every(a => team.includes(a)) && q.answers.every(a => team.includes(a))),
   'the table shows both arms\' actual answers'],
  [!DATA.team_current || !DATA.team_current.has_cheap ||
   team.includes(DATA.team_current.tokens_cheap.toLocaleString('en-US')),
   'the cheaper team payload states its token cost'],
];
for (const [pass, label] of checks) {
  if (!pass) { console.error(`FAIL: ${label}`); bad++; }
  else console.log(`  ok  ${label}`);
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nAll sections rendered and content checks passed.');
