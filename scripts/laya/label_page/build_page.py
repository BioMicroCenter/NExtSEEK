"""Build the held-out labelling page (JevLevROUTING, unit U4): ONE static HTML file, no network, no database.

Usage: python -m scripts.laya.label_page.build_page DRAFT.jsonl OUT.html   (OUT outside the repo: it holds question text)
Shows the full chat as BAML sees it plus the question, never the condensed state, and never the family, entity,
or current route (blind labelling). The operator picks NS / CC / either / unrelated and exports JSON for ingest.py.
"""
from __future__ import annotations

import json
import sys

from scripts.laya.draft_heldout import assert_outside_git

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Held-out labelling</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--card:#f5f5f5;--acc:#0b5fff;--ok:#1a7f37}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15171a;--fg:#e8e8e8;--mut:#9aa;--card:#22262b;--acc:#6ea0ff;--ok:#4cc16a}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:10px 16px;border-bottom:1px solid var(--card);z-index:1}
main{padding:8px 16px 60px;max-width:760px;margin:auto}
.card{background:var(--card);border-radius:8px;padding:12px 14px;margin:14px 0}.card.done{border-left:4px solid var(--ok)}
.turn{margin:6px 0;padding-left:8px;border-left:2px solid var(--mut);white-space:pre-wrap;color:var(--mut);font-size:14px}
.q{font-weight:600;white-space:pre-wrap;margin:8px 0}.opts label{display:inline-block;margin:4px 14px 4px 0;cursor:pointer}
button{font:inherit;padding:6px 12px;margin-right:8px}textarea{width:100%;height:6em}
</style></head><body>
<header><b id="prog"></b> <button id="next">Next unlabelled</button> <button id="exp">Export labels</button>
<button id="clr">Clear all</button><div id="out" hidden><p>Copy this into a file and give it to ingest.py:</p><textarea id="ta" readonly></textarea></div></header>
<main id="list"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent), KEY = 'jevlev-heldout-labels-v1';
const OPTS = [['NS','NS (NextSEEK query)'],['CC','CC (container Claude Code)'],['either','Either is fine'],['unrelated','Unrelated']];
const label = {}; const $ = id => document.getElementById(id);
try { Object.assign(label, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) {}
const getState = () => ({labels: label, exported_at: new Date().toISOString()});
function save() { try { localStorage.setItem(KEY, JSON.stringify(label)); } catch (e) {} prog(); }
function prog() { const n = Object.keys(label).length; $('prog').textContent = n + ' / ' + DATA.length + ' labelled'; }
function el(t, c, x) { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
DATA.forEach((r, i) => {
  const c = el('div', 'card' + (label[r.hash] ? ' done' : '')); c.id = 'c' + i;
  c.append(el('small', 'turn', '#' + (i + 1) + ' of ' + DATA.length));
  (r.history || []).forEach(h => {
    c.append(el('div', 'turn', 'User: ' + h.user_message));
    if (h.assistant_reply) c.append(el('div', 'turn', 'Assistant: ' + h.assistant_reply));
    const meta = [h.router_choice && 'earlier routed to ' + h.router_choice, h.status === 'error' && 'that turn errored'].filter(Boolean).join(', ');
    if (meta) c.append(el('div', 'turn', '(' + meta + ')'));
  });
  c.append(el('div', 'q', r.query));
  const o = el('div', 'opts');
  OPTS.forEach(([v, t]) => { const l = el('label'), b = el('input'); b.type = 'radio'; b.name = 'r' + i; b.value = v; b.checked = label[r.hash] === v;
    b.onchange = () => { label[r.hash] = v; c.classList.add('done'); save(); }; l.append(b, ' ' + t); o.append(l); });
  c.append(o); $('list').append(c);
});
$('next').onclick = () => { const i = DATA.findIndex(r => !label[r.hash]); if (i >= 0) $('c' + i).scrollIntoView({behavior: 'smooth', block: 'center'}); };
$('exp').onclick = () => { const s = JSON.stringify(getState(), null, 1); $('ta').value = s; $('out').hidden = false; $('ta').select();
  try { const a = el('a'); a.href = URL.createObjectURL(new Blob([s], {type: 'application/json'})); a.download = 'heldout-labels.json'; a.click(); } catch (e) {} };
$('clr').onclick = () => { if (confirm('Clear every label?')) { Object.keys(label).forEach(k => delete label[k]); save(); location.reload(); } };
prog();
</script></body></html>
"""


def build(draft, out) -> int:
    assert_outside_git(out)
    rows = [json.loads(line) for line in open(draft) if line.strip()]
    short = {"nextseek_query": "NS", "container_cc": "CC", "unrelated": "unrelated"}
    keep = [{"hash": r["hash"], "query": r["query"],
             "history": [{**{k: h.get(k) for k in ("user_message", "assistant_reply", "status")},
                          "router_choice": short.get(h.get("router_choice"))} for h in r.get("history", [])]} for r in rows]
    keep.sort(key=lambda r: r["hash"])  # hash order mixes families, so no cluster gives the answer away
    data = json.dumps(keep).replace("<", "\\u003c")
    open(out, "w").write(TEMPLATE.replace("__DATA__", data))
    return len(keep)


if __name__ == "__main__":
    print(build(*sys.argv[1:3]), "rows ->", sys.argv[2])
