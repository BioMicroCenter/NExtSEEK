<title>__TITLE__</title>
<style>
/* House palette, taken unchanged from output-skill/templates/report.html.tpl so
   the two reports read as one family. Only the layout below it is new. */
:root{
  --ground:#F4F6F8; --surface:#FFFFFF; --surface-2:#E9EEF3; --raise:#FFFFFF;
  --ink:#131A22; --ink-2:#45525E; --ink-3:#6D7B88; --line:#D5DDE5;
  --accent:#3B5BA5;
  --pass:#2F7A52; --drift:#8A6410; --real:#A5352C; --policy:#63499A; --mute:#6D7B88;
  --pass-bg:#E4F1EA; --drift-bg:#F7EEDA; --real-bg:#F8E5E3; --policy-bg:#EDE7F7; --mute-bg:#E9EEF3;
  --shadow:0 1px 2px rgba(19,26,34,.06),0 4px 14px rgba(19,26,34,.05);
}
@media (prefers-color-scheme:dark){
  :root{
    --ground:#0E1319; --surface:#151C24; --surface-2:#1C242D; --raise:#1A222B;
    --ink:#E4EAF0; --ink-2:#A7B5C1; --ink-3:#778593; --line:#2A343E;
    --accent:#88A4E6;
    --pass:#63C08D; --drift:#DCA850; --real:#F0857A; --policy:#B79EE6; --mute:#778593;
    --pass-bg:#16281F; --drift-bg:#2B2317; --real-bg:#2E1B19; --policy-bg:#221C31; --mute-bg:#1C242D;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 14px rgba(0,0,0,.3);
  }
}
:root[data-theme="light"]{
  --ground:#F4F6F8; --surface:#FFFFFF; --surface-2:#E9EEF3; --raise:#FFFFFF;
  --ink:#131A22; --ink-2:#45525E; --ink-3:#6D7B88; --line:#D5DDE5;
  --accent:#3B5BA5;
  --pass:#2F7A52; --drift:#8A6410; --real:#A5352C; --policy:#63499A; --mute:#6D7B88;
  --pass-bg:#E4F1EA; --drift-bg:#F7EEDA; --real-bg:#F8E5E3; --policy-bg:#EDE7F7; --mute-bg:#E9EEF3;
  --shadow:0 1px 2px rgba(19,26,34,.06),0 4px 14px rgba(19,26,34,.05);
}
:root[data-theme="dark"]{
  --ground:#0E1319; --surface:#151C24; --surface-2:#1C242D; --raise:#1A222B;
  --ink:#E4EAF0; --ink-2:#A7B5C1; --ink-3:#778593; --line:#2A343E;
  --accent:#88A4E6;
  --pass:#63C08D; --drift:#DCA850; --real:#F0857A; --policy:#B79EE6; --mute:#778593;
  --pass-bg:#16281F; --drift-bg:#2B2317; --real-bg:#2E1B19; --policy-bg:#221C31; --mute-bg:#1C242D;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 14px rgba(0,0,0,.3);
}

*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
/* WIDER than the sibling report's 1120px on purpose: this one carries two full
   transcripts side by side, and at 1120px each arm gets ~500px and every reply
   turns into a ribbon. */
.wrap{max-width:1560px;margin:0 auto;padding:0 24px 120px}

.mast{padding:44px 0 22px;border-bottom:1px solid var(--line)}
.eyebrow{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin:0 0 14px}
h1{font-size:clamp(26px,3.2vw,34px);line-height:1.14;letter-spacing:-.02em;margin:0 0 12px;
  font-weight:680;text-wrap:balance;max-width:26ch}
.sub{color:var(--ink-2);max-width:74ch;margin:0 0 18px}
.runline{display:flex;flex-wrap:wrap;gap:8px}
.runline code{background:var(--surface-2);border:1px solid var(--line);border-radius:5px;
  padding:3px 8px;font-size:12.5px;color:var(--ink-2);
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}

h2{font-size:13px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);
  margin:0 0 4px;font-weight:640;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.sect{padding-top:40px}
.sect > .lede{color:var(--ink-2);max-width:76ch;margin:0 0 20px}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:18px 0 0}
.stat{background:var(--surface);padding:16px 18px}
.stat .n{font-size:30px;font-weight:660;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1}
.stat .l{font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);margin-top:5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.n.pass{color:var(--pass)} .n.real{color:var(--real)} .n.drift{color:var(--drift)}
.n.policy{color:var(--policy)} .n.mute{color:var(--mute)}

.exnote{margin-top:18px;border:1px solid var(--line);border-left:3px solid var(--mute);
  border-radius:0 9px 9px 0;background:var(--surface);padding:14px 18px;color:var(--ink-2);
  max-width:96ch;font-size:14px}
.exnote.loud{border-left-color:var(--drift);background:var(--drift-bg)}
.exnote b{color:var(--ink);font-weight:640}

details.howto{border:1px solid var(--line);border-radius:9px;background:var(--surface);overflow:hidden;
  margin-top:20px}
details.howto > summary{padding:13px 18px;font-size:13.5px;color:var(--accent);display:block;cursor:pointer}
details.howto > summary::before{content:"+ ";font-family:ui-monospace,monospace}
details.howto[open] > summary::before{content:"– "}
details.howto[open] > summary{border-bottom:1px solid var(--line)}
.howto-body{padding:16px 20px 20px}
.howto-body p{margin:0 0 12px;color:var(--ink-2);max-width:86ch}
.howto-body p:last-child{margin:0}
kbd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:1px 6px;
  background:var(--surface-2);color:var(--ink)}

/* ---- the sticky grading bar ------------------------------------------- */
.gradebar{position:sticky;top:0;z-index:20;background:var(--ground);
  border-bottom:1px solid var(--line);padding:12px 0;margin:0 0 18px;
  display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.prog{flex:0 0 220px;height:8px;border-radius:5px;background:var(--surface-2);
  border:1px solid var(--line);overflow:hidden}
.prog span{display:block;height:100%;background:var(--pass);width:0}
.pstat{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;
  color:var(--ink-2);font-variant-numeric:tabular-nums}
.pstat b{color:var(--ink);font-weight:660}
.pstat.done b{color:var(--pass)}
.spacer{margin-left:auto}
.btn{border:1px solid var(--line);background:var(--surface-2);color:var(--ink-2);border-radius:6px;
  padding:5px 12px;font:inherit;font-size:12.5px;cursor:pointer}
.btn:hover:not(:disabled){border-color:var(--accent);color:var(--ink)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn:focus-visible,.chip:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.chip{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);border-radius:999px;
  padding:5px 12px;font-size:12.5px;cursor:pointer;font-family:inherit;
  font-variant-numeric:tabular-nums}
.chip:hover{border-color:var(--accent);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--ink);color:var(--ground);border-color:var(--ink)}

/* ---- one question -------------------------------------------------------
   THREE columns: the question and its metadata, then the two arms side by side.
   `align-items:stretch` is explicit and load-bearing: a nested grid whose rows
   shrink to fit leaves the shorter arm floating against a taller neighbour, and
   the grade controls then sit at different heights on every card. `min-width:0`
   on every child is the other half of the same rule -- without it a long mono
   token in a trace line widens its column and the page scrolls sideways. */
.pairs{display:grid;gap:14px}
.pair{border:1px solid var(--line);border-radius:10px;background:var(--line);overflow:hidden;
  box-shadow:var(--shadow)}
.phead{background:var(--surface);padding:10px 15px;display:flex;flex-wrap:wrap;gap:12px;
  align-items:baseline;border-bottom:1px solid var(--line)}
.phead .pid{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;color:var(--ink)}
.phead .pfam{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  color:var(--ink-3)}
.phead .pn{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  color:var(--ink-3);margin-left:auto;font-variant-numeric:tabular-nums}
.pgrid{display:grid;grid-template-columns:minmax(230px,300px) minmax(0,1fr) minmax(0,1fr);
  gap:1px;align-items:stretch}
.pgrid > *{background:var(--surface);min-width:0;display:flex;flex-direction:column}
.pq{padding:14px 16px;gap:0}
.pq .lab{margin-top:0}
.qtext{font-size:14px;color:var(--ink);border-left:2px solid var(--accent);
  padding:7px 11px;border-radius:0 5px 5px 0;background:var(--surface-2);margin:0 0 8px}
.qtext b{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);font-weight:600;
  margin-bottom:3px}
.lab{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);margin:14px 0 6px}
.tag{display:inline-block;background:var(--surface-2);border:1px solid var(--line);border-radius:4px;
  padding:1px 7px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;
  color:var(--ink-2);margin:0 6px 6px 0}

/* ---- one arm ---------------------------------------------------------- */
.arm{padding:14px 16px}
.arm.focus{box-shadow:inset 0 0 0 2px var(--accent)}
.armhd{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:9px}
.pill{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;font-weight:640;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.p-ns{background:var(--mute-bg);color:var(--ink-2)}
.p-cc{background:var(--policy-bg);color:var(--policy)}
.rchip{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;font-weight:600;
  padding:2px 8px;border-radius:4px;background:var(--mute-bg);color:var(--ink-2)}
.rchip.good{background:var(--pass-bg);color:var(--pass)}
.rchip.warn{background:var(--drift-bg);color:var(--drift)}
.rchip.bad{background:var(--real-bg);color:var(--real)}
/* THE 78ch CLAMP IS DELIBERATELY GONE. The sibling report puts one reply in a
   full-width column, where 78ch is a readable measure; here the same rule leaves
   a half-width column two thirds empty and the reply, the one thing being
   graded, becomes the narrowest block on the page. */
.reply{white-space:pre-wrap;font-size:13.5px;line-height:1.58;color:var(--ink);
  border-left:2px solid var(--accent);background:var(--surface-2);
  padding:10px 13px;border-radius:0 6px 6px 0;max-width:none;overflow-wrap:anywhere}
.nores{color:var(--ink-3);font-size:12.5px;font-style:italic}
details.more{margin-top:9px;border:1px solid var(--line);border-radius:7px;background:var(--surface-2)}
details.more > summary{cursor:pointer;padding:7px 11px;font-size:12px;color:var(--ink-3);
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
details.more[open] > summary{border-bottom:1px solid var(--line)}
.more-body{padding:10px 12px;max-height:420px;overflow:auto}
.turn{border:1px solid var(--line);border-radius:6px;background:var(--surface);margin:0 0 8px;
  overflow:hidden}
.turn:last-child{margin-bottom:0}
.turnhd{padding:6px 10px;background:var(--raise);border-bottom:1px solid var(--line);
  font-size:12.5px;color:var(--ink-2)}
.turnhd b{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);margin-right:8px;font-weight:640}
.ev{display:grid;grid-template-columns:minmax(0,140px) minmax(0,1fr);gap:8px;
  padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  border-bottom:1px solid var(--line);align-items:start}
.turn .ev:last-child{border-bottom:none}
.ev .en{color:var(--accent);overflow:hidden;text-overflow:ellipsis}
.ev .ed{color:var(--ink-2);min-width:0;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:12px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
th{text-align:left;font-weight:620;color:var(--ink-3);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;padding:5px 9px 5px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:6px 9px 6px 0;border-bottom:1px solid var(--line);color:var(--ink-2);
  vertical-align:top;overflow-wrap:anywhere}
td.k{color:var(--ink)}
tr.f td.k{color:var(--real)}
td.st{white-space:nowrap;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase}
.ok{color:var(--pass)} .bad{color:var(--real)}
ul.files{margin:0;padding-left:18px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:11.5px;color:var(--ink-2)}

/* ---- grading ----------------------------------------------------------
   `margin-top:auto` inside the flex column pins this to the bottom of the arm,
   so the two arms' controls line up however uneven the transcripts above are. */
.grade{margin-top:auto;padding-top:12px}
.grow{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.gbtn{border:1px solid var(--line);background:var(--surface-2);color:var(--ink-2);border-radius:6px;
  padding:6px 14px;font:inherit;font-size:13px;cursor:pointer;font-weight:600}
.gbtn:hover{border-color:var(--accent);color:var(--ink)}
.gbtn[aria-pressed="true"][data-g="pass"]{background:var(--pass-bg);color:var(--pass);border-color:var(--pass)}
.gbtn[aria-pressed="true"][data-g="fail"]{background:var(--real-bg);color:var(--real);border-color:var(--real)}
.gbtn .kb{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  color:var(--ink-3);margin-left:7px}
.gnote{width:100%;margin-top:8px;min-height:52px;resize:vertical;background:var(--surface-2);
  border:1px solid var(--line);border-radius:6px;padding:8px 10px;color:var(--ink);
  font-family:inherit;font-size:13px;line-height:1.5}
.gnote:focus{outline:none;border-color:var(--accent)}
.gts{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  color:var(--ink-3);margin-left:auto}

/* ---- excluded ---------------------------------------------------------
   Shown, banded, and NOT gradable. Hiding it would make a run that failed to
   observe 40 arms look like a complete pass. */
/* `.norun` is NOT `.excluded`: an arm that never ran is a half-written pair (a
   paid run persists arms as they complete so it can resume), not an arm the
   export rejected. Same muted treatment, different fact. */
.arm.excluded,.arm.norun{background:var(--mute-bg)}
.exband{border:1px solid var(--drift);border-left-width:3px;border-radius:0 7px 7px 0;
  background:var(--drift-bg);padding:9px 12px;margin-top:auto}
.exband .cause{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--drift);font-weight:660}
.exband p{margin:5px 0 0;font-size:12.5px;color:var(--ink-2)}

/* ---- the reveal -------------------------------------------------------
   Never painted by the server. This box is empty markup until the JS fills it,
   and the JS fills it only for a row that already carries a human grade. */
.verdict{margin-top:9px;border:1px dashed var(--line);border-radius:7px;padding:9px 12px;
  background:var(--surface-2)}
.verdict .vh{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);margin-bottom:5px}
.verdict.agree{border-style:solid;border-color:var(--pass)}
.verdict.clash{border-style:solid;border-color:var(--real);background:var(--real-bg)}
.blindnote{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;
  color:var(--ink-3);margin-top:9px}

.empty{padding:26px;text-align:center;color:var(--ink-3);border:1px dashed var(--line);border-radius:9px}
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--ink);
  color:var(--ground);padding:9px 16px;border-radius:8px;font-size:13px;z-index:60;
  box-shadow:var(--shadow)}
@media (max-width:1000px){
  .pgrid{grid-template-columns:minmax(0,1fr)}
  .phead .pn{margin-left:0}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>

<div class="wrap">
<header class="mast">
  <p class="eyebrow">nessie paired run · blind grading</p>
  <h1>Grade every answer before you see what the model thought</h1>
  <p class="sub">One question per card, both engines side by side. Grade each arm pass or fail on
  its own merits. The LLM grader's verdict is already in this file and stays hidden for a row
  until you have graded that row, because seeing it first would make your agreement with it
  worthless.</p>
  <div class="runline" id="runline"></div>
</header>

<section class="sect">
  <h2>This pass</h2>
  <div class="stats" id="stats"></div>
  <div id="exnote"></div>

  <details class="howto">
    <summary>How to grade this, and why it is blind</summary>
    <div class="howto-body">
      <p>Every answer is graded twice: once by the LLM grader in <span class="mono">dmac-assistant</span>'s
      Stage C, once by you. The two are then compared, which is the only way to say whether the LLM
      grader can be trusted on this corpus. That comparison only means something if your grade is
      independent, so this page keeps Stage C's verdict out of sight until the row already carries
      yours. It appears the moment you grade, so a disagreement is still visible while the
      transcript is in front of you.</p>
      <p><b>Keyboard.</b> <kbd>j</kbd> and <kbd>k</kbd> move between arms, <kbd>1</kbd> grades the
      focused arm a pass, <kbd>2</kbd> a fail, <kbd>n</kbd> opens its note, <kbd>Esc</kbd> leaves the
      note. Three hundred grades by mouse is its own failure mode. Every grade is timestamped, so a
      late-pass drift in standards is at least measurable afterwards.</p>
      <p><b>Ungradable arms.</b> An arm whose provider chain went down, whose request was never
      issued, or which blew the deadline without answering is banded and carries no grade controls:
      the runtime CSVs exclude it, so the LLM never graded it either, and a grade you spend on it is
      a grade that reaches nothing. It is still shown, because a page that hid them would look like
      a complete pass over a run that never observed them.</p>
      <p><b>Saving.</b> Grades autosave to this browser as you go, keyed to this run alone. Press
      <kbd>Ctrl</kbd>+<kbd>S</kbd> or use Download to write <span class="mono">grades.json</span>,
      which is what <span class="mono">merge_grades.py</span> reads.</p>
    </div>
  </details>
</section>

<section class="sect">
  <h2>Every question, both arms</h2>
  <div class="gradebar">
    <div class="prog"><span id="progbar"></span></div>
    <span class="pstat" id="pstat"></span>
    <span class="spacer"></span>
    <span id="filters"></span>
    <button class="btn" id="importbtn" type="button">Import</button>
    <input type="file" id="importfile" accept="application/json,.json" hidden>
    <button class="btn" id="revealall" type="button" disabled>Reveal all</button>
    <button class="btn primary" id="download" type="button">Download grades.json</button>
  </div>
  <div class="pairs" id="pairs"></div>
  <div class="empty" id="empty" hidden>Nothing matches that filter.</div>
</section>
</div>

<script>
const META  = __META__;
const PAIRS = __PAIRS__;
/* Stage C's verdicts. Present in the file, deliberately not rendered: see
   BLIND_UNTIL_GRADED below. Empty when the grader has not run yet, which is the
   normal state during the human pass. */
const LLM   = __LLM__;

/* The grades.json contract, written down once and READ by the key builder below
   so the page cannot drift from `merge_grades.py`. `sep` and `arms` are pinned
   in test_bayes_report.py against export.stage_b_query_id, which is also what
   Stage C keys its own output by. */
const GRADE_CONTRACT = {"sep":"::","arms":["ns","cc"],"values":["pass","fail"],
                        "fields":["grade","note","ts"]};
const KEYS = {"next":"j","prev":"k","pass":"1","fail":"2","note":"n"};

/* THE WHOLE POINT OF THE PAGE. A row's verdict is rendered only once that row
   carries a human grade. Flipping this to false would inflate agreement by
   anchoring and make the disagreement set meaningless, which is the one number
   the paired design exists to produce. */
const BLIND_UNTIL_GRADED = true;

const ARM_LABEL = {ns:"NExtSEEK", cc:"Container-CC"};
const esc = s => String(s==null?"":s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const keyOf = (id, arm) => id + GRADE_CONTRACT.sep + arm;

/* ---------- state ---------- */
const GRADES_KEY = "nessie-bayes-grades:" + (META.run_id || "run");
let GRADES = {};
try { GRADES = JSON.parse(localStorage.getItem(GRADES_KEY) || "{}") || {}; } catch(_) { GRADES = {}; }

/* Every gradable arm, in page order. The excluded ones are absent: they are the
   arms `export._exclusion` kept out of the CSVs, so nothing downstream would
   ever read a grade for one. */
const SLOTS = [];
for(const p of PAIRS) for(const arm of GRADE_CONTRACT.arms){
  const a = p[arm];
  if(a && !a.excluded) SLOTS.push({id:p.id, arm});
}
let focusAt = 0, revealed = false, filter = "all";

const gradeOf = s => GRADES[keyOf(s.id, s.arm)];
const gradedCount = () => SLOTS.filter(s => {
  const g = gradeOf(s);
  return g && GRADE_CONTRACT.values.includes(g.grade);
}).length;
const complete = () => SLOTS.length > 0 && gradedCount() === SLOTS.length;

function save(){
  try { localStorage.setItem(GRADES_KEY, JSON.stringify(GRADES)); } catch(_) {}
}

/* ---------- masthead ---------- */
document.getElementById("runline").innerHTML =
  (META.runline||[]).map(c => `<code>${esc(c)}</code>`).join("");

function renderStats(){
  const tiles = [
    {n: META.pairs, label: "questions"},
    {n: META.arms, label: "arms run"},
    {n: META.gradable, label: "gradable", tone: "pass"},
    {n: META.excluded, label: "ungradable", tone: META.excluded ? "drift" : "mute"},
    {n: gradedCount(), label: "graded so far", tone: complete() ? "pass" : "mute"},
  ];
  for(const [cause, n] of Object.entries(META.excluded_by_cause||{}))
    tiles.push({n, label: cause.replace(/_/g," "), tone: "drift"});
  if(revealed){
    let agree = 0, judged = 0;
    for(const s of SLOTS){
      const m = llmSuccess(s); if(m === null) continue;
      judged++; if(m === (gradeOf(s).grade === "pass")) agree++;
    }
    tiles.push({n: judged ? `${Math.round(100*agree/judged)}%` : "n/a",
                label: "grader agreement", tone: "policy"});
  }
  document.getElementById("stats").innerHTML = tiles.map(t =>
    `<div class="stat"><div class="n ${t.tone||""}">${esc(t.n)}</div>` +
    `<div class="l">${esc(t.label)}</div></div>`).join("");

  /* When a lot of arms could not be graded, that IS the run's result and not a
     footnote about this page: a family that times out on every variant is the
     headline number. Stated in words rather than left to be inferred from the
     gap between two tiles. */
  const note = document.getElementById("exnote");
  if(!META.excluded){ note.innerHTML = ""; return; }
  const share = Math.round(100 * META.excluded / Math.max(1, META.arms));
  const causes = Object.entries(META.excluded_by_cause||{})
    .map(([c, n]) => `<b>${n} ${esc(c.replace(/_/g," "))}</b>`).join(", ");
  note.innerHTML = `<div class="exnote${share >= 10 ? " loud" : ""}">
    <b>${META.excluded} of ${META.arms} arms (${share}%) cannot be graded</b>: ${causes}.
    In none of them did the engine answer the question it was asked, so the runtime
    CSVs exclude them and the LLM grader never saw them either. They are shown below,
    banded, without grade controls. Read that count as a result about the run rather
    than as a gap in this pass.</div>`;
}

/* ---------- the verdict, and the gate in front of it ---------- */
const SUCCESS_OUTCOMES = ["FullySatisfied","AppropriateClarification","AppropriateBoundary"];

function llmSuccess(slot){
  const g = gradeOf(slot); if(!g) return null;
  const v = LLM[keyOf(slot.id, slot.arm)];
  if(!v || v.outcome == null || v.outcome === "NotAssessable") return null;
  return SUCCESS_OUTCOMES.includes(v.outcome);
}

/* The ONLY path by which a verdict reaches the page. It returns "" for a row
   with no human grade, so nothing can be painted before one exists. */
function verdictHTML(id, arm){
  const g = GRADES[keyOf(id, arm)];
  if(BLIND_UNTIL_GRADED && !(g && GRADE_CONTRACT.values.includes(g.grade)))
    return `<div class="blindnote">LLM verdict hidden until you grade this arm</div>`;
  const v = LLM[keyOf(id, arm)];
  if(!v) return `<div class="blindnote">no LLM verdict for this arm yet</div>`;
  const machine = v.outcome === "NotAssessable" || v.outcome == null
    ? null : SUCCESS_OUTCOMES.includes(v.outcome);
  const human = g.grade === "pass";
  const cls = machine === null ? "" : (machine === human ? "agree" : "clash");
  const bits = [`<span class="rchip">${esc(v.outcome)}</span>`];
  if(v.usefulness_score != null)
    bits.push(`<span class="rchip">usefulness ${esc(v.usefulness_score)}</span>`);
  if(v.primary_issue) bits.push(`<span class="rchip warn">${esc(v.primary_issue)}</span>`);
  if(machine !== null)
    bits.push(`<span class="rchip ${machine===human?"good":"bad"}">` +
              `${machine===human?"agrees with you":"disagrees with you"}</span>`);
  return `<div class="verdict ${cls}"><div class="vh">LLM grader</div>` +
         `<div class="grow">${bits.join("")}</div></div>`;
}

/* ---------- one arm ---------- */
function traceHTML(a){
  if(!a.trace.length) return `<span class="nores">No task row was collected for this arm.</span>`;
  return a.trace.map(t => `<div class="turn">
      <div class="turnhd"><b>${esc(t.label)}</b>${esc(t.query || "(query not in the corpus)")}</div>
      ${t.events.map(e => `<div class="ev"><div class="en">${esc(e.e)}${e.s?" · "+esc(e.s):""}</div>` +
        `<div class="ed">${esc(e.d)}</div></div>`).join("") ||
        `<div class="ev"><div class="en">—</div><div class="ed">no progress events</div></div>`}
      ${t.dropped ? `<div class="ev"><div class="en">…</div><div class="ed">${t.dropped} further events not shown</div></div>` : ""}
    </div>`).join("");
}

function obsHTML(a){
  if(!a.observations.length)
    return `<span class="nores">No criterion survived evaluation on this arm` +
           (a.skipped_criteria ? ` (${a.skipped_criteria} were unevaluable)` : "") + `.</span>`;
  return `<table><thead><tr><th>Field</th><th>Expected</th><th>Observed</th><th></th></tr></thead><tbody>` +
    a.observations.map(o => `<tr class="${o.passed?"":"f"}"><td class="k">${esc(o.turn)}:${esc(o.field)}</td>` +
      `<td>${esc(o.op)} ${esc(o.expected)}</td><td>${esc(o.observed)}</td>` +
      `<td class="st ${o.passed?"ok":"bad"}">${o.passed?"pass":"fail"}</td></tr>`).join("") +
    `</tbody></table>` +
    (a.skipped_criteria ? `<div class="blindnote">${a.skipped_criteria} further criterion(s) were unevaluable and are not shown</div>` : "");
}

function filesHTML(a){
  const art = a.artifacts;
  const head = `<span class="rchip ${art.observed?(art.count?"good":""):"warn"}">` +
    `${art.count} artifact${art.count===1?"":"s"}${art.observed?"":" · not observed"}</span>`;
  const list = art.paths.length
    ? `<ul class="files">${art.paths.map(p=>`<li>${esc(p)}</li>`).join("")}` +
      `${art.more?`<li>… ${art.more} more</li>`:""}</ul>`
    : `<div class="blindnote">${esc(art.reason || "nothing was collected for this arm")}</div>`;
  return `<div class="grow">${head}</div>${list}`;
}

function armHTML(p, arm){
  const a = p[arm];
  if(!a) return `<div class="arm norun"><div class="armhd">` +
    `<span class="pill p-${arm}">${ARM_LABEL[arm]}</span></div>` +
    `<span class="nores">This arm never ran. The paired manifest records the pair ` +
    `half-written, which is what an interrupted run looks like.</span></div>`;

  const k = keyOf(p.id, arm);
  const g = GRADES[k];
  const chips = [
    `<span class="pill p-${arm}">${ARM_LABEL[arm]}</span>`,
    `<span class="rchip ${a.status==="passed"?"good":(a.status==="failed"?"bad":"warn")}">${esc(a.status)}</span>`,
  ];
  if(a.elapsed != null) chips.push(`<span class="rchip">${Number(a.elapsed).toFixed(1)}s</span>`);
  chips.push(`<span class="rchip">${a.cost==null?"cost not observed":"$"+Number(a.cost).toFixed(4)}</span>`);
  if(a.turns > 1) chips.push(`<span class="rchip">${a.turns} turns</span>`);
  if(a.route) chips.push(`<span class="rchip">${esc(a.route)}</span>`);

  const body = `
    <div class="armhd">${chips.join("")}</div>
    <div class="lab">Final answer</div>
    ${a.reply ? `<div class="reply">${esc(a.reply)}</div>`
              : `<span class="nores">No reply was recorded for this arm.</span>`}
    <details class="more"><summary>per-turn trace</summary><div class="more-body">${traceHTML(a)}</div></details>
    <details class="more"><summary>artifacts</summary><div class="more-body">${filesHTML(a)}</div></details>
    <details class="more"><summary>criterion observations</summary><div class="more-body">${obsHTML(a)}</div></details>`;

  if(a.excluded){
    return `<div class="arm excluded" data-key="${esc(k)}">${body}
      <div class="exband">
        <div class="cause">ungradable · ${esc(a.excluded.cause)}</div>
        <p>${esc(a.excluded.reason)}</p>
        <p>Excluded from the runtime CSVs, so the LLM did not grade it either. Do not spend a grade here.</p>
      </div></div>`;
  }
  return `<div class="arm" data-key="${esc(k)}" data-id="${esc(p.id)}" data-arm="${arm}">${body}
    <div class="grade">
      <div class="grow">
        ${GRADE_CONTRACT.values.map(v => `<button class="gbtn" data-g="${v}" type="button"
           aria-pressed="${g && g.grade===v ? "true":"false"}">${v}<span class="kb">${
           v==="pass"?KEYS.pass:KEYS.fail}</span></button>`).join("")}
        <span class="gts">${g && g.ts ? esc(g.ts.replace("T"," ").slice(0,19)) : ""}</span>
      </div>
      <textarea class="gnote" placeholder="Optional note. Why did it pass or fail?">${esc((g&&g.note)||"")}</textarea>
      ${verdictHTML(p.id, arm)}
    </div></div>`;
}

/* ---------- the list ---------- */
function visible(){
  if(filter === "all") return PAIRS;
  return PAIRS.filter(p => GRADE_CONTRACT.arms.some(arm => {
    const a = p[arm]; if(!a) return false;
    if(filter === "ungradable") return !!a.excluded;
    if(a.excluded) return false;
    const g = GRADES[keyOf(p.id, arm)];
    const has = !!(g && GRADE_CONTRACT.values.includes(g.grade));
    if(filter === "ungraded") return !has;
    if(filter === "graded")   return has;
    if(filter === "clash"){
      const s = {id:p.id, arm}, m = llmSuccess(s);
      return m !== null && m !== (g.grade === "pass");
    }
    return true;
  }));
}

function render(){
  const list = visible();
  document.getElementById("pairs").innerHTML = list.map(p => `
    <article class="pair" data-pair="${esc(p.id)}">
      <div class="phead">
        <span class="pid">${esc(p.id)}</span>
        <span class="pfam">${esc(p.family)}${p.subtype?" · "+esc(p.subtype):""}</span>
        <span class="pn">${p.turns.length||1} turn${(p.turns.length||1)===1?"":"s"}</span>
      </div>
      <div class="pgrid">
        <div class="pq">
          <div class="lab">Question</div>
          ${p.turns.length
            ? p.turns.map(t=>`<div class="qtext"><b>${esc(t.label)}</b>${esc(t.query)}</div>`).join("")
            : `<div class="qtext"><b>query</b><span class="nores">not found in the corpus</span></div>`}
          <div class="lab">Grade both arms on the answer, not on the engine</div>
          <div>${[p.family, p.subtype].filter(Boolean).map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</div>
        </div>
        ${armHTML(p, "ns")}
        ${armHTML(p, "cc")}
      </div>
    </article>`).join("");
  document.getElementById("empty").hidden = list.length > 0;
  paintFocus();
  renderBar();
  renderStats();
}

function renderBar(){
  const n = gradedCount(), total = SLOTS.length;
  document.getElementById("progbar").style.width = total ? (100*n/total) + "%" : "0";
  const el = document.getElementById("pstat");
  el.className = "pstat" + (complete() ? " done" : "");
  el.innerHTML = `<b>${n}</b> of <b>${total}</b> gradable arms graded` +
    (META.excluded ? ` · ${META.excluded} ungradable` : "");
  const rv = document.getElementById("revealall");
  rv.disabled = !complete() || !META.has_llm;
  rv.textContent = revealed ? "Revealed" : "Reveal all";
  rv.title = META.has_llm
    ? (complete() ? "" : "finish the pass first: revealing early anchors the grades that are left")
    : "no stage_c.json in this build";
  const FILTERS = [["all","All"],["ungraded","Ungraded"],["graded","Graded"],
                   ["ungradable","Ungradable"]].concat(revealed ? [["clash","Disagreements"]] : []);
  document.getElementById("filters").innerHTML = FILTERS.map(([k,l]) =>
    `<button class="chip" data-f="${k}" aria-pressed="${filter===k}">${l}</button>`).join(" ");
}

/* ---------- focus + keyboard ---------- */
function paintFocus(){
  document.querySelectorAll(".arm.focus").forEach(e => e.classList.remove("focus"));
  const s = SLOTS[focusAt]; if(!s) return;
  const el = document.querySelector(`.arm[data-key="${CSS.escape(keyOf(s.id, s.arm))}"]`);
  if(el) el.classList.add("focus");
}

function moveFocus(delta, {scroll=true} = {}){
  if(!SLOTS.length) return;
  focusAt = (focusAt + delta + SLOTS.length) % SLOTS.length;
  paintFocus();
  if(!scroll) return;
  const s = SLOTS[focusAt];
  const el = document.querySelector(`.arm[data-key="${CSS.escape(keyOf(s.id, s.arm))}"]`);
  if(el) el.scrollIntoView({block:"center", behavior:"smooth"});
}

function setGrade(id, arm, value){
  const k = keyOf(id, arm);
  const prev = GRADES[k];
  if(prev && prev.grade === value) delete GRADES[k];
  else GRADES[k] = {grade: value, note: (prev && prev.note) || "", ts: new Date().toISOString()};
  save();
  render();
}

function setNote(id, arm, text){
  const k = keyOf(id, arm);
  const prev = GRADES[k];
  /* A note without a grade is still a record of the pass, and losing it because
     the grade came second would be worse than an ungraded row in grades.json --
     `merge_grades` names ungraded rows and refuses, so nothing slips through. */
  GRADES[k] = {grade: (prev && prev.grade) || "", note: text,
               ts: (prev && prev.ts) || new Date().toISOString()};
  if(!text && !(prev && prev.grade)) delete GRADES[k];
  save();
}

document.addEventListener("keydown", e => {
  if((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")){
    e.preventDefault(); download(); return;
  }
  const inField = e.target.matches("textarea,input");
  if(e.key === "Escape" && inField){ e.target.blur(); return; }
  if(inField || e.ctrlKey || e.metaKey || e.altKey) return;
  const s = SLOTS[focusAt];
  if(e.key === KEYS.next){ e.preventDefault(); moveFocus(1); }
  else if(e.key === KEYS.prev){ e.preventDefault(); moveFocus(-1); }
  else if(e.key === KEYS.pass && s){ e.preventDefault(); setGrade(s.id, s.arm, "pass"); moveFocus(1); }
  else if(e.key === KEYS.fail && s){ e.preventDefault(); setGrade(s.id, s.arm, "fail"); moveFocus(1); }
  else if(e.key === KEYS.note && s){
    e.preventDefault();
    const el = document.querySelector(`.arm[data-key="${CSS.escape(keyOf(s.id, s.arm))}"] .gnote`);
    if(el){ el.scrollIntoView({block:"center"}); el.focus(); }
  }
});

document.getElementById("pairs").addEventListener("click", e => {
  const btn = e.target.closest(".gbtn"); if(!btn) return;
  const arm = btn.closest(".arm");
  const at = SLOTS.findIndex(s => keyOf(s.id, s.arm) === arm.dataset.key);
  if(at >= 0) focusAt = at;
  setGrade(arm.dataset.id, arm.dataset.arm, btn.dataset.g);
});
document.getElementById("pairs").addEventListener("input", e => {
  const ta = e.target.closest(".gnote"); if(!ta) return;
  const arm = ta.closest(".arm");
  setNote(arm.dataset.id, arm.dataset.arm, ta.value);
});
document.getElementById("filters").addEventListener("click", e => {
  const btn = e.target.closest(".chip"); if(!btn) return;
  filter = btn.dataset.f;
  render();
});
document.getElementById("revealall").addEventListener("click", () => {
  if(!complete()) return;   /* belt and braces: the button is also disabled */
  revealed = true;
  render();
  toast("Revealed. Filter by Disagreements to see where you and the grader part company.");
});

/* ---------- grades.json ----------
   EXACTLY {"<id>::<arm>": {"grade","note","ts"}}. `merge_grades.py` indexes this
   file by that key and reads `.grade`; a wrapper object or a renamed field
   silently produces "every row is ungraded" and aborts the merge. */
function payload(){
  const out = {};
  for(const s of SLOTS){
    const g = GRADES[keyOf(s.id, s.arm)];
    if(g && GRADE_CONTRACT.values.includes(g.grade)) out[keyOf(s.id, s.arm)] = g;
  }
  return out;
}

function download(){
  const data = payload();
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (META.grades_file || "grades") + ".json";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  const n = Object.keys(data).length;
  toast(n === SLOTS.length
    ? `Saved ${n} grades. merge_grades.py will accept this.`
    : `Saved ${n} of ${SLOTS.length} grades. merge_grades.py will refuse an incomplete pass.`);
}
document.getElementById("download").addEventListener("click", download);

document.getElementById("importbtn").addEventListener("click",
  () => document.getElementById("importfile").click());
document.getElementById("importfile").addEventListener("change", ev => {
  const f = ev.target.files && ev.target.files[0]; if(!f) return;
  const r = new FileReader();
  r.onload = () => {
    try {
      const d = JSON.parse(r.result);
      let n = 0;
      for(const [k, v] of Object.entries(d)){
        if(v && typeof v === "object" && GRADE_CONTRACT.values.includes(v.grade)){
          GRADES[k] = v; n++;
        }
      }
      save(); render();
      toast(`Imported ${n} grade(s).`);
    } catch(err){ toast("Import failed: " + (err.message || err)); }
  };
  r.readAsText(f);
});

let toastTimer = null;
function toast(msg){
  let el = document.querySelector(".toast");
  if(!el){ el = document.createElement("div"); el.className = "toast"; document.body.appendChild(el); }
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 4200);
}

render();
</script>
