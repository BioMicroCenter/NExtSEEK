'use strict';
//
// Runs the REAL core script of the graph search page against fixed inputs.
//
// seek/templates/pages/graphSearch_core.embed.html holds every decision the page
// makes that does not need a browser: the request body for each tab, which Simple
// tab operators graph_search can express, the paging URL, the UID link, and the
// timing and error lines. This file lifts that <script> block out verbatim, so an
// edit to the template is what runs here. The results print as JSON and are
// asserted by seek/tests/test_graph_search_js.py. A case that throws returns
// {error: ...} rather than killing the run.
//
// Usage: node seek/tests/js/graph_search_cases.js [caseName]
//

var fs = require('fs');
var path = require('path');

var FILE = path.join(__dirname, '..', '..', 'templates', 'pages', 'graphSearch_core.embed.html');

function load() {
  var html = fs.readFileSync(FILE, 'utf8');
  var blocks = html.match(/<script>[\s\S]*?<\/script>/g) || [];
  if (blocks.length !== 1) {
    throw new Error('expected exactly one <script> block in graphSearch_core.embed.html, found ' + blocks.length);
  }
  var body = blocks[0].replace(/^<script>/, '').replace(/<\/script>$/, '');
  var leftover = /\{[{%][\s\S]*?[}%]\}/.exec(body);
  if (leftover) {
    throw new Error('the core script holds a template expression: ' + leftover[0]);
  }
  return new Function(body + '\nreturn GraphSearchCore;')();
}

var C = load();

function run(fn) {
  try { return fn(); } catch (e) { return { thrown: String((e && e.message) || e) }; }
}

var cases = {
  advanced_two_terms_and_a_type: function () {
    return C.advancedBody({ terms: 'lung\ngranuloma', logic: 'AND', matchType: 'PARTIAL', sampletype: 'TIS' });
  },
  advanced_one_term_no_type: function () {
    return C.advancedBody({ terms: '  granuloma  \n\n', logic: 'OR', matchType: 'EXACT', sampletype: '' });
  },
  advanced_no_terms: function () {
    return C.advancedBody({ terms: ' \n ', logic: 'AND', matchType: 'PARTIAL', sampletype: 'TIS' });
  },
  simple_numeric_between: function () {
    return C.simpleBody({ sampletype: 'RNA', attribute: 'RIN', rule: 'Between', from: '7', to: '9', filterType: 'numeric' });
  },
  simple_date_before: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'SampleCreationDate', rule: 'Before', from: '12/31/2019', to: '', filterType: 'date' });
  },
  simple_string_contain: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'Contain', from: 'Lung', to: '', filterType: 'string' });
  },
  simple_numeric_not_equal: function () {
    return C.simpleBody({ sampletype: 'RNA', attribute: 'RIN', rule: 'Not Equal', from: '8.5', to: '', filterType: 'numeric' });
  },
  simple_no_filter: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'No Filter', from: '', to: '', filterType: 'string' });
  },
  simple_no_attribute: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: '', rule: '', from: '', to: '', filterType: '' });
  },
  simple_not_contain: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'Not Contain', from: 'Lung', to: '', filterType: 'string' });
  },
  simple_no_type: function () {
    return C.simpleBody({ sampletype: '', attribute: 'Organ', rule: 'Contain', from: 'Lung', to: '', filterType: 'string' });
  },
  simple_numeric_not_a_number: function () {
    return C.simpleBody({ sampletype: 'RNA', attribute: 'RIN', rule: 'Greater', from: 'high', to: '', filterType: 'numeric' });
  },
  simple_between_without_to: function () {
    return C.simpleBody({ sampletype: 'RNA', attribute: 'RIN', rule: 'Between', from: '7', to: '', filterType: 'numeric' });
  },
  simple_contain_without_value: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'Contain', from: '  ', to: '', filterType: 'string' });
  },
  rules_offered: function () {
    return {
      string: C.offeredRules('string', ['Contain', 'Not Contain', 'No Filter']),
      bool: C.offeredRules('bool', ['No Filter', 'True', 'False']),
      numeric: C.offeredRules('numeric', ['No Filter', 'Equal', 'Not Equal', 'Less', 'Greater', 'Between']),
      date: C.offeredRules('date', ['No Filter', 'Equal', 'Not Equal', 'Before', 'After', 'Between'])
    };
  },
  url_page_3: function () { return C.searchUrl(3, 100); },
  uid_link: function () { return C.uidLink('ignored', { id: 5, uuid: 'TIS-1<b>' }); },
  timing_with_debug: function () {
    return C.timingText(107412, 250.4, [{ debug: { cypher_ms: 60.3, count_ms: 9.7, hydrate_ms: 3.0, total_ms: 80.2 } }]);
  },
  timing_without_debug: function () { return C.timingText(1, 120, []); },
  error_from_envelope: function () {
    return C.errorText(422, { errors: [{ title: 'Invalid request', detail: 'attribute X is not on TIS' }] });
  },
  error_without_body: function () { return C.errorText(502, null); }
};

var out = {};
Object.keys(cases).forEach(function (k) { out[k] = run(cases[k]); });
var only = process.argv[2];
process.stdout.write(JSON.stringify(only ? out[only] : out, null, 2) + '\n');
