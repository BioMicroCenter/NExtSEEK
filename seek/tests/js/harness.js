'use strict';
//
// Runs the REAL inline script from seek/templates/templatesList.html.
//
// The picker's behaviour lives entirely in that <script> block, and clicking
// through it by hand missed three defects that this harness reproduces in
// milliseconds. Nothing here is a reimplementation: the script body is lifted
// out of the template verbatim and handed a stub document (dom.js). If someone
// edits the template, this harness runs the edit.
//

var fs = require('fs');
var path = require('path');
var createDocument = require('./dom').createDocument;

var TEMPLATE = path.join(__dirname, '..', '..', 'templates', 'templatesList.html');

// seek/views.py passes template_catalog.MAX_SUGGESTIONS through to the page.
// The harness has no Django, so the one template expression inside the script
// is substituted here; any other one is a hard error rather than a silent
// syntax error inside new Function().
var MAX_SUGGESTIONS = 12;

function extractScript(html) {
  var blocks = html.match(/<script>[\s\S]*?<\/script>/g) || [];
  if (blocks.length !== 1) {
    throw new Error('expected exactly one <script> block in templatesList.html, found '
      + blocks.length);
  }
  var body = blocks[0]
    .replace(/^<script>/, '')
    .replace(/<\/script>$/, '')
    .replace(/\{\{\s*max_suggestions\s*\}\}/g, String(MAX_SUGGESTIONS));

  var leftover = /\{[{%][\s\S]*?[}%]\}/.exec(body);
  if (leftover) {
    throw new Error('the script block grew a template expression the harness does not '
      + 'substitute: ' + leftover[0]);
  }
  return body;
}

function buildPage(doc, spec) {
  var root = doc.body;

  function el(tag, className, id) {
    var node = doc.createElement(tag);
    if (className) { node.className = className; }
    if (id) { doc.register(id, node); }
    return node;
  }

  var form = el('form', null, 'tpl-form');
  root.appendChild(form);

  var selbar = el('div', 'tpl-selbar', 'tpl-selbar');
  form.appendChild(selbar);
  var label = el('span', 'tpl-selbar-label');
  label.appendChild(el('span', null, 'tpl-count'));
  selbar.appendChild(label);
  selbar.appendChild(el('span', null, 'tpl-chips'));
  var search = el('input', 'tpl-search', 'tpl-search');
  search.setAttribute('type', 'search');
  selbar.appendChild(search);
  var actions = el('span', 'tpl-actions');
  actions.appendChild(el('button', null, 'tpl-clear'));
  var submit = el('button', null, 'tpl-submit');
  submit.disabled = true;
  actions.appendChild(submit);
  selbar.appendChild(actions);

  var sugg = el('div', 'tpl-sugg', 'tpl-sugg');
  sugg.hidden = true;
  sugg.appendChild(el('span', null, 'tpl-sugg-items'));
  sugg.appendChild(el('button', null, 'tpl-add-all'));
  form.appendChild(sugg);

  var req = el('div', 'tpl-req', 'tpl-req');
  req.hidden = true;
  req.appendChild(el('span', null, 'tpl-req-items'));
  form.appendChild(req);

  // One .tpl-group block per group key, in catalog order -- the order the
  // checkboxes appear in is the order selected() reports, and more than one
  // defect below depends on it.
  var groups = {};
  spec.types.forEach(function (type) {
    var key = type.group || '';
    if (!groups[key]) {
      var block = el('div', 'tpl-group');
      block.setAttribute('data-group', key);
      var grid = el('div', 'tpl-grid');
      block.appendChild(grid);
      form.appendChild(block);
      groups[key] = grid;
    }
    var item = el('label', 'tpl-item');
    item.setAttribute('data-code', type.code);
    item.setAttribute('data-name', String(type.name).toLowerCase());
    var box = el('input');
    box.setAttribute('type', 'checkbox');
    box.setAttribute('name', 'codes');
    box.value = type.code;
    box.setAttribute('data-group', key);
    box.setAttribute('data-name', type.name);
    item.appendChild(box);
    groups[key].appendChild(item);
  });

  function island(id, payload) {
    var node = el('script', null, id);
    node.textContent = JSON.stringify(payload);
    root.appendChild(node);
  }
  island('tpl-children-data', spec.children || {});
  island('tpl-meta-data', spec.meta || {});
  island('tpl-requirements-data', spec.requires || {});
  island('tpl-companions-data', spec.companions || {});
}

function boxes(doc) {
  return doc.getElementById('tpl-form').querySelectorAll('input[name="codes"]');
}

function boxFor(doc, code) {
  var found = boxes(doc).filter(function (b) { return b.value === code; })[0];
  if (!found) { throw new Error('no checkbox for ' + code); }
  return found;
}

function stripCross(text) {
  return String(text).replace(/\s*✕\s*$/, '');
}

function createPicker(spec) {
  var doc = createDocument();
  buildPage(doc, spec);
  var script = extractScript(fs.readFileSync(TEMPLATE, 'utf8'));
  // The template's IIFE takes `document` from scope; give it ours.
  new Function('document', script)(doc);

  var api = {
    doc: doc,

    // --- driving ------------------------------------------------------
    // A user click on a checkbox: the box flips and the browser fires
    // 'change'. Script-set .checked fires nothing, which is exactly the
    // asymmetry several of these defects live in.
    tick: function (code) {
      var box = boxFor(doc, code);
      box.checked = true;
      box.dispatchEvent({ type: 'change' });
      return api;
    },
    untick: function (code) {
      var box = boxFor(doc, code);
      box.checked = false;
      box.dispatchEvent({ type: 'change' });
      return api;
    },
    // Clicking the chip's own ✕, which goes through the script's own handler
    // and fires no 'change' event -- a different code path from untick().
    removeChip: function (code) {
      var chip = doc.getElementById('tpl-chips').children.filter(function (el) {
        return stripCross(el.textContent) === code;
      })[0];
      if (!chip) { throw new Error('no chip for ' + code); }
      chip.dispatchEvent({ type: 'click' });
      return api;
    },
    clickSuggestion: function (code) {
      var hit = doc.getElementById('tpl-sugg-items').children.filter(function (el) {
        return el.textContent.indexOf('+ ' + code) === 0;
      })[0];
      if (!hit) { throw new Error('no suggestion for ' + code); }
      hit.dispatchEvent({ type: 'click' });
      return api;
    },
    // "+ PARENT" inside the prompt strip's line for CHILD.
    clickPromptOption: function (child, parent) {
      var line = doc.getElementById('tpl-req-items').children.filter(function (el) {
        return el.textContent.indexOf(child + ' needs') === 0;
      })[0];
      if (!line) { throw new Error('no prompt line for ' + child); }
      var option = line.children.filter(function (el) {
        return stripCross(el.textContent) === '+ ' + parent;
      })[0];
      if (!option) { throw new Error('no ' + parent + ' option on ' + child); }
      option.dispatchEvent({ type: 'click' });
      return api;
    },
    addAll: function () {
      doc.getElementById('tpl-add-all').dispatchEvent({ type: 'click' });
      return api;
    },
    clear: function () {
      doc.getElementById('tpl-clear').dispatchEvent({ type: 'click' });
      return api;
    },
    search: function (query) {
      var field = doc.getElementById('tpl-search');
      field.value = query;
      field.dispatchEvent({ type: 'input' });
      return api;
    },

    // --- reading back -------------------------------------------------
    selected: function () {
      return boxes(doc).filter(function (b) { return b.checked; })
        .map(function (b) { return b.value; });
    },
    chips: function () {
      return doc.getElementById('tpl-chips').children.map(function (el) {
        return {
          code: stripCross(el.textContent),
          text: el.textContent,
          title: el.title,
          classes: el.className.split(' ').filter(Boolean),
          group: el.getAttribute('data-group'),
        };
      });
    },
    count: function () { return doc.getElementById('tpl-count').textContent; },
    submitDisabled: function () { return doc.getElementById('tpl-submit').disabled; },
    prompts: function () {
      return {
        hidden: doc.getElementById('tpl-req').hidden,
        needs: doc.getElementById('tpl-req-items').children.map(function (line) {
          return {
            text: line.textContent,
            child: line.childNodes.filter(function (n) { return n.nodeType === 3; })
              .map(function (n) { return n.data; }).join('').split(' needs')[0],
            options: line.children.map(function (el) {
              return el.textContent.replace(/^\+\s*/, '');
            }),
          };
        }),
      };
    },
    suggestions: function () {
      return {
        hidden: doc.getElementById('tpl-sugg').hidden,
        codes: doc.getElementById('tpl-sugg-items').children.map(function (el) {
          return el.children[0].textContent.replace(/^\+\s*/, '');
        }),
      };
    },
    // Which catalog rows the search leaves on screen.
    visibleCodes: function () {
      return doc.querySelectorAll('.tpl-item')
        .filter(function (item) { return !item.hidden; })
        .map(function (item) { return item.getAttribute('data-code'); });
    },
    searchValue: function () { return doc.getElementById('tpl-search').value; },
  };
  return api;
}

module.exports = { createPicker: createPicker, extractScript: extractScript };
