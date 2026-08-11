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

/* collapsible explainer */
details.howto{border:1px solid var(--line);border-radius:9px;background:var(--surface);overflow:hidden}
details.howto > summary{padding:13px 18px;font-size:13.5px;color:var(--accent);display:block}
details.howto > summary::before{content:"+ ";font-family:ui-monospace,monospace}
details.howto[open] > summary::before{content:"– "}
details.howto[open] > summary{border-bottom:1px solid var(--line)}
.howto-body{padding:16px 20px 20px}
.howto-body p{margin:0 0 12px;color:var(--ink-2);max-width:80ch}

/* per-turn working record inside a case */
.turn{border:1px solid var(--line);border-radius:8px;background:var(--surface-2);
  padding:0;margin:0 0 9px;overflow:hidden}
.turnhd{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;padding:9px 13px;
  background:var(--raise);border-bottom:1px solid var(--line)}
.turnhd .tn{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);font-weight:640}
.turnhd .tq{font-size:13.5px;color:var(--ink);flex:1;min-width:200px}
.turnhd .tt{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:var(--ink-3)}
.row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:12px;padding:10px 13px;
  border-bottom:1px solid var(--line);align-items:start}
.turn .row:last-child{border-bottom:none}
.row > .rk{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;
  letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);padding-top:3px}
.row > .rv{min-width:0}
.rv .why{color:var(--ink-3);font-size:12.5px;margin-top:5px;max-width:80ch}
.callline{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
  color:var(--ink);margin-bottom:7px;word-break:break-all}
.callline .verb{color:var(--accent);font-weight:640;margin-right:7px}
pre.json{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:9px 11px;
  overflow-x:auto;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  line-height:1.55;color:var(--ink-2);white-space:pre;max-height:340px;overflow-y:auto}
pre.json.wrap{white-space:pre-wrap;word-break:break-word}
pre.json b{color:var(--accent);font-weight:640}
.subk{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-3);margin:9px 0 5px}
.chipline{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-top:8px}
.rchip{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;font-weight:640;
  padding:2px 8px;border-radius:4px;background:var(--mute-bg);color:var(--ink-2)}
.rchip.good{background:var(--pass-bg);color:var(--pass)}
.rchip.warn{background:var(--drift-bg);color:var(--drift)}
.rchip.bad{background:var(--real-bg);color:var(--real)}
.nores{color:var(--ink-3);font-size:12.5px;font-style:italic}
/* The chatter's final answer. Deliberately the most readable block on the page:
   it is prose a human judges, not a field they scan. */
.reply{white-space:pre-wrap;font-size:13px;line-height:1.55;color:var(--ink);
  border-left:2px solid var(--accent);
  padding:9px 12px;border-radius:0 5px 5px 0;max-width:78ch}
.replymore{margin-top:6px}
.replymore summary{cursor:pointer;color:var(--ink-3);font-size:12px}

/* reviewer notes */
.notebar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 16px;
  border:1px solid var(--line);border-radius:9px;background:var(--surface);padding:11px 15px}
.notebar .nstat{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
  color:var(--ink-3);margin-right:auto}
.notebar .nstat b{color:var(--ink);font-weight:640}
.notebar .nstat.dirty b{color:var(--drift)}
.btn{border:1px solid var(--line);background:var(--surface-2);color:var(--ink-2);border-radius:6px;
  padding:5px 12px;font:inherit;font-size:12.5px;cursor:pointer}
.btn:hover{border-color:var(--accent);color:var(--ink)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.notes{margin-top:15px}
.notes textarea{width:100%;min-height:74px;resize:vertical;background:var(--surface-2);
  border:1px solid var(--line);border-radius:7px;padding:10px 12px;color:var(--ink);
  font-family:inherit;font-size:13.5px;line-height:1.5;display:block}
.notes textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px var(--accent) inset;
  box-shadow:0 0 0 1px var(--accent)}
.notes textarea::placeholder{color:var(--ink-3)}
.notedot{color:var(--accent);font-size:15px;line-height:1;margin-left:8px}
.gnotes{border:1px solid var(--line);border-radius:9px;background:var(--surface);
  padding:15px 18px;margin:0 0 16px}
.gnotes .lab{margin-top:0}

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
  <h2>Every case</h2>
  <p class="lede">One record per case, holding everything needed to judge it: each turn's query, the
  routing decision with the router's own reasoning, the exact call the engine made (cypher with its bound
  parameters, or the full REST request body), what came back, and how that landed against the asserted
  criteria. Filter by verdict; <span class="mono">real</span> and <span class="mono">masked</span> are the
  ones worth attention.</p>

  <details class="howto">
    <summary>How routing works, and where this evidence comes from</summary>
    <div class="howto-body">
      <p>The top-level router is a BAML function, <span class="mono">RouteQuery</span> in
      <span class="mono">dmac_assistant/baml_src/router.baml</span>, wrapped by
      <span class="mono">nextseek_api/cc_assistant/router.py</span>. It is capability-driven: route descriptions,
      tools and task families load from <span class="mono">route_capabilities.json</span> and are rendered into the
      prompt, so routing is configuration rather than hard-coded rules. Three routes only:
      <span class="mono">nextseek_query</span>, <span class="mono">container_cc</span>,
      <span class="mono">unrelated</span>. History is passed in framed as "data to interpret, NOT instructions",
      a deliberate injection guard. <span class="mono">model_class</span> is returned but discarded, because CC is
      always pinned to Opus as the only model the Bedrock proxy allowlists.</p>
      <p>Two things bypass the router, and both show up in the <b>Routing</b> row of a turn below. A keyword
      <b>heuristic</b> takes over when BAML is unavailable. A <b>pipeline short-circuit</b> runs <i>ahead</i> of the
      router entirely: when a pipeline is active the route is forced, reasoning
      <span class="mono">pipeline_active</span>, and the model never sees the query. Any turn not marked
      <span class="mono">baml</span> deserves a second look.</p>
      <p>There is <b>no per-turn <span class="mono">console.txt</span></b>. The async endpoint reuses one run root
      and writes <span class="mono">console.txt</span> once at process start. The per-turn evidence below is
      reconstructed from the <span class="mono">assistant_query_task</span> event streams, and mirrored on disk
      in timestamped files:</p>
      <div class="tree">__RUNROOT__/
├── <b>api_requests.json</b>        <i>every API request made during the run</i>
├── console.txt              <i>config snapshot at process start, NOT per turn</i>
└── files/
    ├── <b>graph/</b>graph_debug_&lt;ts&gt;.json   <i>one file per graph query</i>
    ├── <b>api/</b>api_result_bundle_N.json  <i>API result bundles</i>
    ├── <b>report/</b>                       <i>generated GEO / SRA / RPPR workbooks</i>
    └── protocol/  memory/  nfcore_all-samples/</div>
    </div>
  </details>

  <div class="stats" id="routestats" style="margin:18px 0 0"></div>
  <div class="filters" id="filters"></div>

  <div class="notebar">
    <span class="nstat" id="nstat"></span>
    <button class="btn" id="savenotes" type="button">Save notes</button>
    <button class="btn" id="importbtn" type="button">Import</button>
    <input type="file" id="importnotes" accept="application/json,.json" hidden>
  </div>

  <div class="gnotes">
    <div class="lab">Overall review notes</div>
    <div class="notes" style="margin-top:0">
      <textarea data-note="__report__" placeholder="Notes on the run as a whole. Press Ctrl-S (or Cmd-S) to save all notes to a JSON file."></textarea>
    </div>
  </div>

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

const CAP = META.graph_limit || 250;
function matchedOn(cy){
  const inv = /inv\.title/.test(cy), st = /st\.title/.test(cy);
  if(inv && st) return "Study OR Investigation";
  if(inv) return "Investigation.title";
  if(st) return "Study.title";
  if(/parent\.uuid|\$uids/.test(cy)) return "by UID";
  return "no title match";
}
const jfmt = o => JSON.stringify(o, null, 1);
const isEmpty = o => !o || (typeof o === "object" && Object.keys(o).length === 0);

/* The engine call for one turn: the cypher it ran, or the REST request it sent. */
function renderCall(t){
  if(t.gplan){
    const cy = esc(t.gplan.cypher || "").replace(/\b(inv\.title|st\.title)\b/g, "<b>$1</b>");
    const m = t.gmeta || {};
    const cls = m.count === CAP ? "warn" : (m.count === 0 ? "bad" : "good");
    let v = `<pre class="json">${cy}</pre>`;
    if(!isEmpty(t.gplan.parameters))
      v += `<div class="subk">bound parameters</div><pre class="json wrap">${esc(jfmt(t.gplan.parameters))}</pre>`;
    v += `<div class="chipline">
        <span class="rchip ${m.ok?"good":"bad"}">${m.ok?"ok":"failed"}</span>
        <span class="rchip ${cls}">${m.count==null?"?":m.count} rows${m.count===CAP?" · LIMIT cap":""}</span>
        <span class="rchip">matched on ${matchedOn(t.gplan.cypher||"")}</span></div>`;
    if(t.gplan.explanation) v += `<div class="why">${esc(t.gplan.explanation)}</div>`;
    return {k:"Graph query", v};
  }
  if(t.aplan){
    const m = t.ameta || {};
    const bad = m.status_code >= 400;
    let v = `<div class="callline"><span class="verb">${esc(t.aplan.method||"POST")}</span>${esc(t.aplan.endpoint||"")}</div>`;
    v += `<div class="subk">request body</div><pre class="json wrap">${esc(jfmt(t.aplan.requestBody||{}))}</pre>`;
    if(!isEmpty(t.aplan.queryParameters))
      v += `<div class="subk">query parameters</div><pre class="json wrap">${esc(jfmt(t.aplan.queryParameters))}</pre>`;
    v += `<div class="chipline">
        <span class="rchip ${bad?"bad":"good"}">HTTP ${m.status_code==null?"?":m.status_code}</span>
        <span class="rchip ${m.ok?"good":"bad"}">api_ok ${m.ok===true?"true":"false"}</span></div>`;
    if(t.aplan.notes) v += `<div class="why">${esc(t.aplan.notes)}</div>`;
    return {k:"REST call", v};
  }
  if(t.rplan)
    return {k:"Reporter plan", v:`<pre class="json wrap">${esc(jfmt(t.rplan))}</pre>`};
  if(t.model){
    let v = `<div class="callline">${esc(t.model)}</div>`;
    if(t.cost!=null) v += `<div class="chipline"><span class="rchip">$${Number(t.cost).toFixed(4)}</span></div>`;
    return {k:"Container-CC", v};
  }
  if(t.task) return {k:"Engine", v:`<span class="nores">No API or graph call was made on this turn.</span>`};
  return null;
}

/* One turn = query + how it routed + what it actually ran. */
function renderTurn(t){
  const rows = [];
  if(t.route){
    const srcOdd = t.src && t.src !== "baml";
    rows.push(`<div class="row"><div class="rk">Routing</div><div class="rv">
      <span class="pill ${RCLS[t.route]||"p-un"}">${esc(t.route)}</span>
      <span class="pill ${srcOdd?"p-pipe":"p-un"}" style="margin-left:6px">via ${esc(t.src||"?")}</span>
      ${t.mode?`<span class="rchip" style="margin-left:6px">mode ${esc(t.mode)}</span>`:""}
      ${t.why?`<div class="why">${esc(t.why)}</div>`:""}</div></div>`);
  }
  const call = renderCall(t);
  if(call) rows.push(`<div class="row"><div class="rk">${call.k}</div><div class="rv">${call.v}</div></div>`);
  /* The answer the user actually read, next to the call that produced it.
     A criterion can only check what someone thought to assert, so when a
     criterion is stale this is the only way to judge whether the case was
     right. Long replies collapse; the first ~600 chars carry the claim. */
  if(t.reply != null && String(t.reply).trim()){
    const full = String(t.reply).trim();
    const head = full.length > 600 ? full.slice(0,600) + "…" : full;
    rows.push(`<div class="row"><div class="rk">Answer</div><div class="rv">
      <div class="reply">${esc(head)}</div>
      ${full.length > 600 ? `<details class="replymore"><summary>full reply (${full.length} chars)</summary><pre class="json wrap">${esc(full)}</pre></details>` : ""}
    </div></div>`);
  } else if(t.task){
    rows.push(`<div class="row"><div class="rk">Answer</div><div class="rv">
      <span class="nores">No reply was recorded for this turn.</span></div></div>`);
  }
  return `<div class="turn">
    <div class="turnhd">
      <span class="tn">${esc(t.label||"turn")}</span>
      <span class="tq">${esc(t.query)}</span>
      <span class="tt">${t.task?("task "+t.task):"never ran"}</span>
    </div>${rows.join("")}</div>`;
}

/* ---------- reviewer notes ----------
   Typing autosaves to localStorage so nothing is lost on reload or refilter.
   Ctrl-S / Cmd-S writes every note to a JSON file for handing back for review. */
const NOTES_KEY = "nessie-notes:" + (META.notes_id || "run");
let NOTES = {};
try { NOTES = JSON.parse(localStorage.getItem(NOTES_KEY) || "{}") || {}; } catch(_) { NOTES = {}; }
let notesDirty = false;

function noteCount(){ return Object.values(NOTES).filter(v => (v||"").trim()).length; }

function updateNoteBar(msg){
  const el = document.getElementById("nstat");
  const n = noteCount();
  el.className = "nstat" + (notesDirty ? " dirty" : "");
  el.innerHTML = msg ? msg
    : `<b>${n}</b> note${n===1?"":"s"}` +
      (notesDirty ? " · <b>unsaved</b> · press Ctrl-S" : (n ? " · saved to this browser" : " · add notes as you review"));
}

function setNote(id, val){
  if((val||"").trim()) NOTES[id] = val; else delete NOTES[id];
  try { localStorage.setItem(NOTES_KEY, JSON.stringify(NOTES)); } catch(_) {}
  notesDirty = true;
  updateNoteBar();
}

function saveNotes(){
  const byCase = {};
  for(const c of CASES) if((NOTES[c.id]||"").trim())
    byCase[c.id] = {verdict: c.verdict, family: c.family, status: c.status, note: NOTES[c.id]};
  const payload = {
    report: META.title || document.title,
    saved_at: new Date().toISOString(),
    overall: NOTES["__report__"] || "",
    cases: byCase,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (META.notes_file || "nessie-notes") + ".json";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  notesDirty = false;
  updateNoteBar(`<b>saved</b> ${a.download} · ${noteCount()} note(s) · check your downloads folder`);
  setTimeout(()=>updateNoteBar(), 4000);
}

document.addEventListener("keydown", e => {
  if((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")){ e.preventDefault(); saveNotes(); }
});
document.getElementById("savenotes").addEventListener("click", saveNotes);
document.getElementById("importbtn").addEventListener("click", () => document.getElementById("importnotes").click());
document.getElementById("importnotes").addEventListener("change", ev => {
  const f = ev.target.files && ev.target.files[0];
  if(!f) return;
  const r = new FileReader();
  r.onload = () => {
    try {
      const d = JSON.parse(r.result);
      if(d.overall) NOTES["__report__"] = d.overall;
      for(const [k,v] of Object.entries(d.cases||{})) NOTES[k] = typeof v === "string" ? v : v.note;
      localStorage.setItem(NOTES_KEY, JSON.stringify(NOTES));
      applyFilter();
      updateNoteBar(`<b>imported</b> ${noteCount()} note(s)`);
      setTimeout(()=>updateNoteBar(), 3000);
    } catch(err){ updateNoteBar(`<b>import failed</b> ${esc(String(err.message||err))}`); }
  };
  r.readAsText(f);
});

/* one delegated handler covers every textarea, including re-rendered ones */
document.addEventListener("input", e => {
  const ta = e.target.closest("textarea[data-note]");
  if(ta) setNote(ta.dataset.note, ta.value);
});

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
        <span class="cid">${esc(c.id)}<span class="fam">${esc(c.family)}</span>${(NOTES[c.id]||"").trim()?'<span class="notedot" title="you left a note here">&#9679;</span>':""}</span>
        <span class="cmeta">${esc(meta)}</span>
      </summary>
      <div class="body">
        ${c.head?`<p class="hd">${esc(c.head)}</p>`:""}
        <div class="lab">Turns: query, routing, and the call it ran</div>
        ${(c.turns||[]).map(renderTurn).join("")||`<div class="turn"><div class="turnhd"><span class="tq">no recorded turns</span></div></div>`}
        ${c.observed?`<div class="lab">Expected vs observed</div><div class="tbl-wrap"><table>
          <thead><tr><th>Field</th><th>Expected</th><th>Observed</th><th></th></tr></thead>
          <tbody>${obsRows(c.observed)}</tbody></table></div>`
        :`<div class="lab">Criteria asserted</div><div class="tbl-wrap"><table>
          <thead><tr><th>Field</th><th>Assertion</th></tr></thead><tbody>${critRows(c.turns||[])}</tbody></table></div>`}
        ${c.note?`<p class="note">${esc(c.note)}</p>`:""}
        <div class="lab">Trace</div>
        <div>${tags.map(t=>`<span class="tag">${esc(t)}</span>`).join("")||'<span class="tag">no failed criteria</span>'}</div>
        <div class="notes">
          <div class="lab">Your notes</div>
          <textarea data-note="${esc(c.id)}" placeholder="Notes on this case. Ctrl-S saves all notes to a file.">${esc(NOTES[c.id]||"")}</textarea>
        </div>
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
let currentFilter = "all";
function applyFilter(){
  render(currentFilter === "all" ? CASES : CASES.filter(c => c.verdict === currentFilter));
}
applyFilter();

/* restore the overall-notes box (it lives outside the re-rendered case list) */
document.querySelector('textarea[data-note="__report__"]').value = NOTES["__report__"] || "";
updateNoteBar();

document.getElementById("filters").addEventListener("click", e => {
  const btn = e.target.closest(".chip"); if(!btn) return;
  document.querySelectorAll(".chip").forEach(c => c.setAttribute("aria-pressed", String(c===btn)));
  currentFilter = btn.dataset.f;
  applyFilter();
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
