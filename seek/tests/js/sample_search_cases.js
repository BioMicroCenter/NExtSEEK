'use strict';
//
// Runs the REAL core script of the Sample Search page against fixed inputs.
//
// seek/templates/pages/sampleSearch_core.embed.html holds every decision the page
// makes that does not need a browser: the body each search box sends to
// POST /nextseek_api/samples/graph_search/ (the Simple box's rules, the Advanced
// box's query text), which Simple rules graph_search can express, the paging URL,
// and the UID link and Attribute:Value cells the grids show. This file lifts that
// <script> block out verbatim, so an edit to the template is what runs here. The
// results print as JSON and are asserted by seek/tests/test_sample_search_js.py.
// A case that throws returns {thrown: ...} rather than killing the run.
//
// Usage: node seek/tests/js/sample_search_cases.js [caseName]
//

var fs = require('fs');
var path = require('path');

var FILE = path.join(__dirname, '..', '..', 'templates', 'pages', 'sampleSearch_core.embed.html');

function load() {
  var html = fs.readFileSync(FILE, 'utf8');
  var blocks = html.match(/<script>[\s\S]*?<\/script>/g) || [];
  if (blocks.length !== 1) {
    throw new Error('expected exactly one <script> block in sampleSearch_core.embed.html, found ' + blocks.length);
  }
  var body = blocks[0].replace(/^<script>/, '').replace(/<\/script>$/, '');
  var leftover = /\{[{%][\s\S]*?[}%]\}/.exec(body);
  if (leftover) {
    throw new Error('the core script holds a template expression: ' + leftover[0]);
  }
  return new Function(body + '\nreturn SampleSearchCore;')();
}

var C = load();

function run(fn) {
  try { return fn(); } catch (e) { return { thrown: String((e && e.message) || e) }; }
}

var TYPES = [
  { id: 26, title: 'TIS', group: 'Experimental type' },
  { id: 11, title: 'D.SEQ', group: 'Data type' }
];

function query(text, matchType, sampletype) {
  return C.queryBody({ text: text, matchType: matchType || 'PARTIAL', sampletype: sampletype || '' });
}

var cases = {
  // ---- the Advanced box: its query text as graph_search's extensions.query ----
  query_one_term: function () { return query('  granuloma  '); },
  query_and_terms: function () { return query('lung AND granuloma'); },
  query_or_terms_exact: function () { return query('lung OR granuloma', 'EXACT'); },
  query_as_search_add_builds_it: function () {
    // searchAdd() wraps a phrase in parentheses and the text so far when it holds a space.
    return query('((lung AND left lobe) AND granuloma)');
  },
  query_not: function () { return query('lung NOT granuloma'); },
  query_not_a_phrase: function () { return query('lung NOT (left lobe)'); },
  query_mixed_logic: function () { return query('(lung AND granuloma) OR liver'); },
  query_leading_not: function () { return query('NOT(granuloma)'); },
  query_tags_of_two_types: function () { return query('lung[TIS] AND reads[D.SEQ]'); },
  query_or_partly_tagged: function () { return query('lung[TIS] OR granuloma'); },
  query_only_a_tag: function () { return query('[TIS]'); },
  query_brackets_that_are_not_a_tag: function () { return query('lung[a][b] OR x]'); },
  query_operators_on_new_lines: function () { return query('lung\nAND\tliver'); },
  query_empty: function () { return query('  '); },
  query_lower_case_and_is_part_of_the_term: function () { return query('salt and pepper'); },
  query_with_the_chosen_type: function () { return query('lung', 'PARTIAL', 'TIS'); },
  query_chosen_type_and_another_tag: function () { return query('lung[TIS]', 'PARTIAL', 'D.SEQ'); },
  query_text_graph_search_cannot_read: function () { return query('a OR b AND c'); },

  // ---- the Simple box: one sample type, one attribute, one rule ----
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
  simple_no_filter_ignores_the_value: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'No Filter', from: 'lung', to: '', filterType: 'string' });
  },
  simple_attribute_none: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'none', rule: '', from: '', to: '', filterType: '' });
  },
  simple_no_attribute: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: '', rule: '', from: '', to: '', filterType: '' });
  },
  simple_attribute_none_with_value: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'none', rule: '', from: '  Lung ', to: '', filterType: '' });
  },
  simple_not_contain: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'Not Contain', from: ' Lung ', to: '', filterType: 'string' });
  },
  simple_not_contain_without_value: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'Not Contain', from: '', to: '', filterType: 'string' });
  },
  simple_true: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Viable', rule: 'True', from: 'ignored', to: '', filterType: 'bool' });
  },
  simple_false: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Viable', rule: 'False', from: '', to: '', filterType: 'bool' });
  },
  simple_unknown_rule: function () {
    return C.simpleBody({ sampletype: 'TIS', attribute: 'Organ', rule: 'Sounds Like', from: 'Lung', to: '', filterType: 'string' });
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
      string: C.offeredRules(['Contain', 'Not Contain', 'No Filter']),
      bool: C.offeredRules(['No Filter', 'True', 'False']),
      unknown: C.offeredRules(['No Filter', 'Sounds Like', 'Contain']),
      numeric: C.offeredRules(['No Filter', 'Equal', 'Not Equal', 'Less', 'Greater', 'Between']),
      date: C.offeredRules(['No Filter', 'Equal', 'Not Equal', 'Before', 'After', 'Between'])
    };
  },
  type_title_of_a_chosen_id: function () {
    return [C.typeTitle(TYPES, '11'), C.typeTitle(TYPES, 26), C.typeTitle(TYPES, '0'), C.typeTitle(TYPES, ''),
            C.typeTitle(TYPES, 'lung'), C.typeTitle(null, '26')];
  },

  // ---- paging, rows and cells ----
  url_page_3: function () { return C.searchUrl(3, 100); },
  uid_link: function () { return C.uidLink({ id: 5, uuid: 'TIS-1<b>' }); },
  cells_for_terms: function () {
    return C.attributeValueHtml({ Organ: 'Left Lung', Notes: 'lung & liver', Empty: null, Count: 3 },
                                { terms: ['lung'], matchType: 'PARTIAL', attribute: null });
  },
  cells_for_exact_terms: function () {
    return C.attributeValueHtml({ Organ: 'Lung', Site: 'lung lobe' },
                                { terms: ['LUNG'], matchType: 'EXACT', attribute: null });
  },
  cells_for_an_attribute: function () {
    return C.attributeValueHtml({ Organ: 'Lung', RIN: '8.1' }, { terms: [], matchType: null, attribute: 'RIN' });
  },
  cells_for_a_type_only_search: function () {
    return C.attributeValueHtml({ Organ: 'Lung' }, { terms: [], matchType: null, attribute: null });
  },
  cells_escape_the_metadata: function () {
    return C.attributeValueHtml({ '<k>': '<img src=x>' }, { terms: ['img'], matchType: 'PARTIAL', attribute: null });
  },
  rows_prepared: function () {
    return C.prepareRows([{ id: 7, uuid: 'TIS-2', sample_type: 'TIS', assays: 'A<1>', first_name: 'D&D', title: 'T',
                            json_metadata: { Organ: 'Lung' }, attributeValue: '' }],
                         { terms: ['lung'], matchType: 'PARTIAL', attribute: null });
  },
  rows_prepared_without_rows: function () { return C.prepareRows(null, { terms: [] }); },

  // ---- what the page says when graph_search refuses ----
  error_from_envelope: function () {
    return C.errorText(422, { errors: [{ title: 'Invalid request', detail: 'attribute X is not on TIS' }] });
  },
  error_from_scope: function () {
    return C.errorText(403, { errors: [{ title: 'Cannot determine project scope for this caller' }] });
  },
  error_without_body: function () { return C.errorText(502, null); }
};

var out = {};
Object.keys(cases).forEach(function (k) { out[k] = run(cases[k]); });
var only = process.argv[2];
process.stdout.write(JSON.stringify(only ? out[only] : out, null, 2) + '\n');
