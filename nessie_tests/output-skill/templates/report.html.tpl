<title>__TITLE__</title>
<style>
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
.wrap{max-width:1120px;margin:0 auto;padding:0 24px 96px}

.mast{padding:56px 0 28px;border-bottom:1px solid var(--line)}
.eyebrow{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin:0 0 14px}
h1{font-size:clamp(28px,4vw,40px);line-height:1.12;letter-spacing:-.02em;margin:0 0 14px;
  font-weight:680;text-wrap:balance;max-width:20ch}
.sub{color:var(--ink-2);max-width:68ch;margin:0 0 20px}
.runline{display:flex;flex-wrap:wrap;gap:8px}
.runline code{background:var(--surface-2);border:1px solid var(--line);border-radius:5px;
  padding:3px 8px;font-size:12.5px;color:var(--ink-2);
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}

h2{font-size:13px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);
  margin:0 0 4px;font-weight:640;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.sect{padding-top:52px}
.sect > .lede{color:var(--ink-2);max-width:70ch;margin:0 0 22px}
h3.sh{font-size:17px;margin:30px 0 6px;letter-spacing:-.01em}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:18px 0 0}
.stat{background:var(--surface);padding:16px 18px}
.stat .n{font-size:30px;font-weight:660;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1}
.stat .l{font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);margin-top:5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.n.pass{color:var(--pass)} .n.real{color:var(--real)} .n.drift{color:var(--drift)}
.n.policy{color:var(--policy)} .n.mute{color:var(--mute)}

.reframe{margin-top:26px;border:1px solid var(--line);border-radius:10px;background:var(--surface);
  padding:20px 22px;box-shadow:var(--shadow)}
.reframe p{margin:0 0 14px;color:var(--ink-2);max-width:72ch}
.bar{display:flex;height:12px;border-radius:6px;overflow:hidden;border:1px solid var(--line)}
.bar span{display:block}
.barkey{display:flex;flex-wrap:wrap;gap:18px;margin-top:12px;font-size:12.5px;color:var(--ink-2)}
.barkey b{font-weight:620;color:var(--ink)}
.dot{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:7px;vertical-align:baseline}

.finds{display:grid;gap:14px}
.find{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--sev,var(--mute));
  border-radius:9px;padding:18px 20px;box-shadow:var(--shadow)}
.find.real{--sev:var(--real)} .find.drift{--sev:var(--drift)} .find.policy{--sev:var(--policy)}
.find h3{margin:0 0 8px;font-size:16.5px;font-weight:640;letter-spacing:-.01em}
.find p{margin:0 0 10px;color:var(--ink-2);max-width:76ch}
.find p:last-child{margin-bottom:0}
.ev{background:var(--surface-2);border:1px solid var(--line);border-radius:7px;padding:11px 13px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;color:var(--ink-2);
  overflow-x:auto;white-space:pre-wrap;word-break:break-word;margin:10px 0 0}

.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 18px;
  position:sticky;top:0;background:var(--ground);padding:14px 0;z-index:5;border-bottom:1px solid var(--line)}
.chip{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);border-radius:999px;
  padding:6px 13px;font-size:12.5px;cursor:pointer;font-family:inherit;
  font-variant-numeric:tabular-nums;transition:background .12s,color .12s,border-color .12s}
.chip:hover{border-color:var(--accent);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--ink);color:var(--ground);border-color:var(--ink)}
.chip:focus-visible,summary:focus-visible,.tool:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tool{margin-left:auto;background:none;border:none;color:var(--accent);font:inherit;font-size:12.5px;
  cursor:pointer;padding:6px 4px;text-decoration:underline;text-underline-offset:3px}

.cases{display:grid;gap:7px}
details.case{background:var(--surface);border:1px solid var(--line);border-radius:9px;overflow:hidden}
details.case[open]{box-shadow:var(--shadow)}
summary{list-style:none;cursor:pointer;padding:11px 15px;display:grid;
  grid-template-columns:82px minmax(0,1fr) auto;gap:14px;align-items:center}
summary::-webkit-details-marker{display:none}
summary:hover{background:var(--surface-2)}
.vchip{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  letter-spacing:.07em;text-transform:uppercase;padding:3px 0;border-radius:4px;text-align:center;font-weight:640}
.v-pass{background:var(--pass-bg);color:var(--pass)}
.v-real{background:var(--real-bg);color:var(--real)}
.v-drift{background:var(--drift-bg);color:var(--drift)}
.v-policy{background:var(--policy-bg);color:var(--policy)}
.v-masked{background:var(--pass-bg);color:var(--drift)}
.v-notrun{background:var(--mute-bg);color:var(--mute)}
.cid{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;
  color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cid .fam{color:var(--ink-3);font-size:11.5px;margin-left:9px}
.cmeta{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  color:var(--ink-3);white-space:nowrap;font-variant-numeric:tabular-nums}
.body{padding:4px 15px 17px;border-top:1px solid var(--line)}
.hd{font-weight:620;margin:13px 0 9px;color:var(--ink);max-width:76ch}
.lab{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);margin:15px 0 7px}
.q{background:var(--surface-2);border:1px solid var(--line);border-left:2px solid var(--accent);
  border-radius:0 6px 6px 0;padding:9px 12px;margin:0 0 7px;font-size:13.5px;color:var(--ink-2)}
.q b{color:var(--ink-3);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;margin-right:9px;font-weight:600}
.tbl-wrap{overflow-x:auto;margin:0}
table{border-collapse:collapse;width:100%;font-size:12.5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
th{text-align:left;font-weight:620;color:var(--ink-3);font-size:10.5px;letter-spacing:.08em;
  text-transform:uppercase;padding:6px 11px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 11px 7px 0;border-bottom:1px solid var(--line);color:var(--ink-2);
  vertical-align:top;word-break:break-word}
td.k{color:var(--ink);white-space:nowrap}
tr.f td.k{color:var(--real)}
td.st{white-space:nowrap;font-size:11px;letter-spacing:.06em;text-transform:uppercase}
.ok{color:var(--pass)} .bad{color:var(--real)} .inf{color:var(--accent)}
.note{margin:13px 0 0;color:var(--ink-2);font-size:14px;max-width:78ch}
.tag{display:inline-block;background:var(--surface-2);border:1px solid var(--line);border-radius:4px;
  padding:1px 7px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;
  color:var(--ink-2);margin-right:6px}
.empty{padding:26px;text-align:center;color:var(--ink-3);border:1px dashed var(--line);border-radius:9px}

.scroller{max-height:440px;overflow:auto;border:1px solid var(--line);border-radius:9px;background:var(--surface)}
.scroller table{font-size:12.5px}
.scroller th{position:sticky;top:0;background:var(--surface-2);padding:9px 14px;z-index:1;border-bottom:1px solid var(--line)}
.scroller td{padding:7px 14px}
.scroller td.why{font-family:ui-sans-serif,system-ui,sans-serif;font-size:12.5px;color:var(--ink-3);min-width:280px}
.pill{display:inline-block;padding:1px 7px;border-radius:4px;font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;font-weight:640}
.p-ns{background:var(--mute-bg);color:var(--ink-2)}
.p-cc{background:var(--policy-bg);color:var(--policy)}
.p-un{background:var(--surface-2);color:var(--ink-3)}
.p-pipe{background:var(--real-bg);color:var(--real)}
.cyq{border:1px solid var(--line);border-radius:9px;background:var(--surface);padding:14px 16px;margin:0 0 9px}
.cyq.cap{border-left:3px solid var(--drift)}
.cyq.zero{border-left:3px solid var(--real)}
.cyhead{display:flex;flex-wrap:wrap;gap:11px;align-items:baseline;margin-bottom:9px}
.cyhead .qt{font-weight:620;color:var(--ink)}
.cyhead .ct{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  color:var(--ink-3);font-variant-numeric:tabular-nums}
.cnt{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:660;font-size:12px}
.cnt.cap{color:var(--drift)} .cnt.zero{color:var(--real)} .cnt.ok{color:var(--pass)}
pre.cy{margin:0;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;
  overflow-x:auto;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  line-height:1.6;color:var(--ink-2);white-space:pre-wrap;word-break:break-word}
pre.cy b{color:var(--accent);font-weight:640}
.where{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:18px 20px;
  box-shadow:var(--shadow);margin-bottom:22px}
.where p{margin:0 0 10px;color:var(--ink-2);max-width:76ch}
.where p:last-child{margin:0}
.tree{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--ink-2);
  background:var(--surface-2);border:1px solid var(--line);border-radius:7px;padding:12px 14px;
  overflow-x:auto;white-space:pre;margin:12px 0 0;line-height:1.65}
.tree i{color:var(--ink-3);font-style:normal}
.tree b{color:var(--accent);font-weight:600}

.wide{overflow-x:auto;border:1px solid var(--line);border-radius:9px;background:var(--surface)}
.wide table{font-size:13px}
.wide th{padding:11px 16px;background:var(--surface-2)}
.wide td{padding:9px 16px}
.wide td.num{text-align:right;font-variant-numeric:tabular-nums}
.gap{border-bottom:1px solid var(--line);padding:15px 0;display:grid;
  grid-template-columns:118px minmax(0,1fr);gap:20px;align-items:start}
.gap:last-child{border-bottom:none}
.gap .gid{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  color:var(--accent);letter-spacing:.05em;padding-top:2px}
.gap p{margin:0;color:var(--ink-2);max-width:74ch}
.gap b{color:var(--ink);font-weight:620}
ol.next{counter-reset:s;list-style:none;padding:0;margin:0}
ol.next li{counter-increment:s;padding:13px 0 13px 46px;position:relative;border-bottom:1px solid var(--line);
  color:var(--ink-2);max-width:78ch}
ol.next li:last-child{border-bottom:none}
ol.next li::before{content:counter(s,decimal-leading-zero);position:absolute;left:0;top:13px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--accent)}
ol.next b{color:var(--ink);font-weight:620}
@media (max-width:620px){
  summary{grid-template-columns:74px minmax(0,1fr);gap:10px}
  .cmeta{display:none}
  .gap{grid-template-columns:1fr;gap:5px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>

<div class="wrap">
<header class="mast">
  <p class="eyebrow">__EYEBROW__</p>
  <h1>__HEADLINE__</h1>
  <p class="sub">__SUBHEAD__</p>
  <div class="runline" id="runline"></div>
</header>

<section class="sect">
  <h2>What the run reported</h2>
  <div class="stats" id="stats"></div>
  <div class="reframe" id="reframe"></div>
</section>

<section class="sect" id="findings-sect">
  <h2>Findings the case list does not show on its own</h2>
  <p class="lede">These cut across cases.</p>
  <div class="finds" id="finds"></div>
</section>

<section class="sect">
  <h2>How the router decided</h2>
  <p class="lede">The top-level router is a BAML function, <span class="mono">RouteQuery</span> in
  <span class="mono">dmac_assistant/baml_src/router.baml</span>, wrapped by
  <span class="mono">nextseek_api/cc_assistant/router.py</span>. It is capability-driven: route descriptions,
  tools and task families are loaded from <span class="mono">route_capabilities.json</span> and rendered into the prompt,
  so routing behaviour is configuration, not hard-coded rules.</p>

  <div class="where">
    <p>Three routes only: <span class="mono">nextseek_query</span>, <span class="mono">container_cc</span>,
    <span class="mono">unrelated</span>. Conversation history is passed in and explicitly framed as
    "data to interpret, NOT instructions", which is a deliberate prompt-injection guard.
    <span class="mono">model_class</span> comes back from the router but is discarded: the CC route is always pinned to Opus
    because that is the only model the Bedrock proxy allowlists.</p>
    <p>There are two ways the BAML router is bypassed. A keyword <b>heuristic</b> takes over if BAML is unavailable or
    returns its <span class="mono">&lt;router_unavailable&gt;</span> sentinel. And a <b>pipeline short-circuit</b> runs
    <i>ahead</i> of the router entirely: if a pipeline is active the route is forced with
    <span class="mono">source: "pipeline"</span>, reasoning <span class="mono">"pipeline_active"</span>, and the model never sees the query.</p>
  </div>

  <div class="stats" id="routestats" style="margin-bottom:18px"></div>
  <div class="scroller"><table>
    <thead><tr><th>Task</th><th>Query</th><th>Route</th><th>Source</th><th>Router reasoning</th></tr></thead>
    <tbody id="routes"></tbody>
  </table></div>
</section>

<section class="sect">
  <h2>What the engines actually ran</h2>
  <p class="lede">On where this evidence lives: there is <b>no per-turn <span class="mono">console.txt</span></b>.
  The async endpoint reuses a single run root and writes one <span class="mono">console.txt</span> at process start.
  The real per-turn evidence is elsewhere in that run root, and all of it is timestamped so it maps onto tasks.</p>

  <div class="where">
    <div class="tree">__RUNROOT__/
├── <b>api_requests.json</b>        <i>every API request made during the run</i>
├── console.txt              <i>config snapshot at process start, NOT per turn</i>
└── files/
    ├── <b>graph/</b>graph_debug_&lt;ts&gt;.json   <i>one file per graph query</i>
    ├── <b>api/</b>api_result_bundle_N.json  <i>API result bundles</i>
    ├── <b>report/</b>                       <i>generated GEO / SRA / RPPR workbooks</i>
    └── protocol/  memory/  nfcore_all-samples/</div>
    <p style="margin-top:14px">Each <span class="mono">graph_debug_&lt;ts&gt;.json</span> carries the user query, entity output,
    parser output and the cypher with its results. The timestamps line up 1:1 with the graph turns.</p>
  </div>

  <h3 class="sh">Every cypher query in the run</h3>
  <p class="lede">Sorted as they ran. The <b>Matched on</b> column shows which graph node the phrase
  "study X" resolved to, which is where result-set instability tends to originate.</p>
  <div id="cyphers"></div>

  <h3 class="sh">Every REST call in the run</h3>
  <p class="lede" id="restlede"></p>
  <div class="scroller"><table>
    <thead><tr><th>Task</th><th>Query</th><th>Method</th><th>Endpoint</th><th>Status</th></tr></thead>
    <tbody id="rest"></tbody>
  </table></div>
</section>

<section class="sect">
  <h2>Every case</h2>
  <p class="lede">Expandable to the exact criteria and what the system actually produced.
  Filter by verdict; <span class="mono">real</span> and <span class="mono">masked</span> are the ones worth attention.</p>
  <div class="filters" id="filters"></div>
  <div class="cases" id="cases"></div>
  <div class="empty" id="empty" hidden>No cases match that filter.</div>
</section>

<section class="sect" id="cov-sect">
  <h2>What this run did not cover</h2>
  <p class="lede" id="covlede"></p>
  <div class="wide"><table>
    <thead><tr><th>Family</th><th class="num">In corpus</th><th class="num">Ran</th><th class="num">Coverage</th></tr></thead>
    <tbody id="cov"></tbody>
  </table></div>
</section>

<section class="sect" id="gaps-sect">
  <h2>Harness gaps this triage exposed</h2>
  <p class="lede">Not product bugs. Reasons the run under-reports or mis-reports.</p>
  <div id="gaps"></div>
</section>

<section class="sect" id="next-sect">
  <h2>Where this points next</h2>
  <ol class="next" id="next"></ol>
</section>
</div>

<script>
const META     = __META__;
const CASES    = __CASES__;
const TURNS    = __TURNS__;
const FINDINGS = __FINDINGS__;
const COVERAGE = __COVERAGE__;
const GAPS     = __GAPS__;
const NEXT     = __NEXT__;

const esc = s => String(s==null?"":s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const VERDICT_LABEL = {pass:"pass",real:"real",drift:"drift",policy:"policy",masked:"masked",notrun:"not run"};
const ORDER = {real:0,masked:1,policy:2,drift:3,notrun:4,pass:5};

/* ---------- masthead + stats ---------- */
document.getElementById("runline").innerHTML =
  (META.runline||[]).map(c => `<code>${esc(c)}</code>`).join("");
document.getElementById("stats").innerHTML =
  (META.stats||[]).map(s => `<div class="stat"><div class="n ${s.tone||""}">${esc(s.n)}</div>`+
    `<div class="l">${esc(s.label)}</div></div>`).join("");

/* ---------- verdict reframe bar ---------- */
const tally = {};
for(const c of CASES) tally[c.verdict] = (tally[c.verdict]||0)+1;
const SEG = [["real","5"],["drift","4"],["policy","3"]].map(([k]) => k);
const segTotal = SEG.reduce((a,k)=>a+(tally[k]||0),0) || 1;
document.getElementById("reframe").innerHTML =
  `<p>${META.reframe||""}</p>
   <div class="bar">${SEG.map(k=>`<span style="background:var(--${k});width:${100*(tally[k]||0)/segTotal}%"></span>`).join("")}</div>
   <div class="barkey">
     <span><i class="dot" style="background:var(--real)"></i><b>${tally.real||0} real</b> product defects</span>
     <span><i class="dot" style="background:var(--drift)"></i><b>${tally.drift||0} drift</b> stale or unevaluable assertions</span>
     <span><i class="dot" style="background:var(--policy)"></i><b>${tally.policy||0} policy</b> routing decisions to make</span>
   </div>`;

/* ---------- findings ---------- */
const fh = document.getElementById("finds");
if(FINDINGS.length){
  fh.innerHTML = FINDINGS.map(f => `<article class="find ${f.severity||"real"}">
    <h3>${esc(f.title)}</h3>
    ${(f.body||[]).map(p=>`<p>${p}</p>`).join("")}
    ${f.evidence?`<div class="ev">${esc(f.evidence)}</div>`:""}
  </article>`).join("");
} else { document.getElementById("findings-sect").hidden = true; }

/* ---------- router table ---------- */
const RCLS = {nextseek_query:"p-ns", container_cc:"p-cc", unrelated:"p-un"};
const rcount = {}, scount = {};
for(const t of TURNS){ rcount[t.route]=(rcount[t.route]||0)+1; scount[t.src]=(scount[t.src]||0)+1; }
document.getElementById("routestats").innerHTML = [
  {n:scount.baml||0,      label:"by BAML"},
  {n:scount.pipeline||0,  label:"by pipeline", tone:(scount.pipeline?"real":"mute")},
  {n:scount.heuristic||0, label:"heuristic",   tone:"mute"},
  {n:rcount.nextseek_query||0, label:"to NExtSEEK"},
  {n:rcount.container_cc||0,   label:"to Container-CC", tone:"policy"},
  {n:rcount.unrelated||0,      label:"unrelated", tone:"mute"},
].map(s=>`<div class="stat"><div class="n ${s.tone||""}">${s.n}</div><div class="l">${s.label}</div></div>`).join("");

document.getElementById("routes").innerHTML = TURNS.map(t => {
  const pipe = t.src === "pipeline";
  return `<tr><td class="mono">${esc(t.id)}</td><td>${esc((t.q||"").slice(0,52))}</td>`+
   `<td><span class="pill ${RCLS[t.route]||"p-un"}">${esc(t.route||"none")}</span></td>`+
   `<td>${pipe?'<span class="pill p-pipe">pipeline</span>':'<span class="mono" style="font-size:11.5px">'+esc(t.src||"?")+'</span>'}</td>`+
   `<td class="why">${esc((t.why||"").slice(0,150))}</td></tr>`;
}).join("");

/* ---------- cypher ---------- */
function matchedOn(cy){
  const inv = /inv\.title/.test(cy), st = /st\.title/.test(cy);
  if(inv && st) return "Study OR Investigation";
  if(inv) return "Investigation.title";
  if(st) return "Study.title";
  if(/parent\.uuid|\$uids/.test(cy)) return "by UID";
  return "no title match";
}
const CAP = META.graph_limit || 250;
document.getElementById("cyphers").innerHTML = TURNS.filter(t => t.cypher).map(t => {
  const c = t.cnt;
  const cls  = c === CAP ? "cap" : (c === 0 ? "zero" : "");
  const ccls = c === CAP ? "cap" : (c === 0 ? "zero" : "ok");
  const cy = esc(t.cypher).replace(/\b(inv\.title|st\.title)\b/g, "<b>$1</b>");
  return `<div class="cyq ${cls}">
    <div class="cyhead">
      <span class="qt">${esc(t.q)}</span>
      <span class="ct">task ${esc(t.id)}</span>
      <span class="ct">matched on: ${matchedOn(t.cypher)}</span>
      <span class="cnt ${ccls}">${c} rows${c===CAP?" (LIMIT cap)":""}</span>
    </div><pre class="cy">${cy}</pre></div>`;
}).join("");

/* ---------- rest ---------- */
const restRows = TURNS.filter(t => t.ep);
const restBad  = restRows.filter(t => t.code >= 400).length;
document.getElementById("restlede").textContent =
  `${restRows.length} calls, ${restRows.length - restBad} returned 2xx.`;
document.getElementById("rest").innerHTML = restRows.map(t => {
  const bad = t.code >= 400;
  return `<tr><td class="mono">${esc(t.id)}</td><td>${esc((t.q||"").slice(0,40))}</td>`+
   `<td class="mono">${/sample-tree/.test(t.ep||"") ? "GET":"POST"}</td>`+
   `<td class="mono" style="font-size:11.5px">${esc(t.ep)}</td>`+
   `<td class="mono ${bad?"bad":"ok"}">${t.code==null?"-":t.code}</td></tr>`;
}).join("");

/* ---------- cases ---------- */
function critRows(turns){
  let out = "";
  for(const t of turns) for(const c of t.criteria){
    const v = c.v===undefined||c.v===null ? "" : " " + JSON.stringify(c.v);
    out += `<tr><td class="k">${esc(t.label)}:${esc(c.f)}</td><td>${esc(c.op)}${esc(v)}</td></tr>`;
  }
  return out || `<tr><td colspan="2">no criteria</td></tr>`;
}
function obsRows(obs){
  return obs.map(o => {
    const cls = o[3]==="ok" ? "ok" : (o[3]==="info" ? "inf" : "bad");
    const word = o[3]==="ok" ? "pass" : (o[3]==="info" ? "info" : "fail");
    return `<tr class="${o[3]==='fail'?'f':''}"><td class="k">${esc(o[0])}</td><td>${esc(o[1])}</td>`+
           `<td>${esc(o[2])}</td><td class="st ${cls}">${word}</td></tr>`;
  }).join("");
}
function render(list){
  document.getElementById("cases").innerHTML = list.map(c => {
    const meta = [c.route||"no route", c.engine||"", c.elapsed!=null?c.elapsed+"s":""].filter(Boolean).join("  ·  ");
    const tags = [];
    if(c.task) tags.push(`task ${c.task}`);
    if(c.gcount!=null) tags.push(`graph count ${c.gcount}${c.gcount===CAP?" (cap)":""}`);
    if(c.xfail) tags.push("known_fail");
    if(c.failed && c.failed.length) tags.push(c.failed.join(", "));
    return `<details class="case" data-v="${c.verdict}">
      <summary>
        <span class="vchip v-${c.verdict}">${VERDICT_LABEL[c.verdict]||c.verdict}</span>
        <span class="cid">${esc(c.id)}<span class="fam">${esc(c.family)}</span></span>
        <span class="cmeta">${esc(meta)}</span>
      </summary>
      <div class="body">
        ${c.head?`<p class="hd">${esc(c.head)}</p>`:""}
        <div class="lab">Query</div>
        ${(c.turns||[]).map(t=>`<p class="q"><b>${esc(t.label)}</b>${esc(t.query)}</p>`).join("")||`<p class="q">(consistency group)</p>`}
        ${c.observed?`<div class="lab">Expected vs observed</div><div class="tbl-wrap"><table>
          <thead><tr><th>Field</th><th>Expected</th><th>Observed</th><th></th></tr></thead>
          <tbody>${obsRows(c.observed)}</tbody></table></div>`
        :`<div class="lab">Criteria asserted</div><div class="tbl-wrap"><table>
          <thead><tr><th>Field</th><th>Assertion</th></tr></thead><tbody>${critRows(c.turns||[])}</tbody></table></div>`}
        ${c.note?`<p class="note">${esc(c.note)}</p>`:""}
        <div class="lab">Trace</div>
        <div>${tags.map(t=>`<span class="tag">${esc(t)}</span>`).join("")||'<span class="tag">no failed criteria</span>'}</div>
      </div>
    </details>`;
  }).join("");
  document.getElementById("empty").hidden = list.length > 0;
}
CASES.sort((a,b)=> ((ORDER[a.verdict]??9)-(ORDER[b.verdict]??9)) || a.id.localeCompare(b.id));

const FILTERS = [["all","All"],["real","Real"],["drift","Drift"],["policy","Policy"],
                 ["masked","Masked pass"],["notrun","Not run"],["pass","Passed"]];
document.getElementById("filters").innerHTML =
  FILTERS.filter(([k]) => k==="all" || tally[k])
    .map(([k,l],i)=>`<button class="chip" data-f="${k}" aria-pressed="${i===0}">${l} ${k==="all"?CASES.length:tally[k]}</button>`)
    .join("") + `<button class="tool" id="toggle">Expand all</button>`;
render(CASES);

document.getElementById("filters").addEventListener("click", e => {
  const btn = e.target.closest(".chip"); if(!btn) return;
  document.querySelectorAll(".chip").forEach(c => c.setAttribute("aria-pressed", String(c===btn)));
  render(btn.dataset.f==="all" ? CASES : CASES.filter(c => c.verdict===btn.dataset.f));
  document.getElementById("toggle").textContent = "Expand all";
});
document.getElementById("toggle").addEventListener("click", e => {
  const open = e.target.textContent === "Expand all";
  document.querySelectorAll("details.case").forEach(d => d.open = open);
  e.target.textContent = open ? "Collapse all" : "Expand all";
});

/* ---------- coverage / gaps / next ---------- */
const covTot = COVERAGE.reduce((a,r)=>[a[0]+r[1], a[1]+r[2]], [0,0]);
document.getElementById("covlede").textContent = META.coverage_lede ||
  `The sample touched ${COVERAGE.length} families. ${covTot[1]} of ${covTot[0]} variants ran.`;
document.getElementById("cov").innerHTML = COVERAGE.map(r =>
  `<tr><td class="mono">${esc(r[0])}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td>`+
  `<td class="num">${(100*r[2]/r[1]).toFixed(0)}%</td></tr>`).join("") +
  `<tr><td class="mono"><b>total</b></td><td class="num"><b>${covTot[0]}</b></td>`+
  `<td class="num"><b>${covTot[1]}</b></td><td class="num"><b>${(100*covTot[1]/covTot[0]).toFixed(1)}%</b></td></tr>`;

if(GAPS.length){
  document.getElementById("gaps").innerHTML = GAPS.map(g =>
    `<div class="gap"><div class="gid">${esc(g.id)}</div><p>${g.text}</p></div>`).join("");
} else { document.getElementById("gaps-sect").hidden = true; }

if(NEXT.length){
  document.getElementById("next").innerHTML = NEXT.map(n => `<li>${n}</li>`).join("");
} else { document.getElementById("next-sect").hidden = true; }
</script>
