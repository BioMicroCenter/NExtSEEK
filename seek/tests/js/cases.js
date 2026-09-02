'use strict';
//
// Scenarios run against the real picker script. Each case returns a plain
// object; the whole set is printed as JSON on stdout and asserted from
// seek/tests/test_templates_js.py. A case that throws returns {error: ...}
// instead, so a crash inside the picker reads as a failed assertion rather
// than a dead harness.
//
// Usage: node seek/tests/js/cases.js [caseName]
//

var createPicker = require('./harness').createPicker;

// A stand-in catalog shaped like the live one: the real chains
// A.ALN -> D.SEQ -> DNA -> {BAC,TIS,RNA}, the real disjunction PAV -> NHP|PAT,
// and the real shared parent NHP (CEX and PAV both name it).
var TYPES = [
  { code: 'TIS', group: '', name: 'Tissue' },
  { code: 'BAC', group: '', name: 'Bacterial isolate' },
  { code: 'RNA', group: '', name: 'RNA' },
  { code: 'DNA', group: '', name: 'DNA' },
  { code: 'NHP', group: '', name: 'Non-human primate' },
  { code: 'PAT', group: '', name: 'Patient' },
  { code: 'MUS', group: '', name: 'Mouse' },
  // PAV is listed before CEX on purpose. selected() reports checkbox order,
  // so this is what lets one pass push PAV's unmet requirement *before* CEX's
  // auto-add of NHP satisfies it -- the stale-repaint case (C1).
  { code: 'PAV', group: '', name: 'Patient visit' },
  { code: 'CEX', group: '', name: 'Cell extract' },
  { code: 'OOC', group: '', name: 'Organ on chip' },
  { code: 'D.SEQ', group: 'D.', name: 'Sequencing data' },
  { code: 'D.FLOW', group: 'D.', name: 'Flow cytometry data' },
  { code: 'A.ALN', group: 'A.', name: 'Alignment' },
];

var REQUIRES = {
  DNA: { add: ['BAC', 'TIS', 'RNA'], assays: ['DNA Extraction'] },
  'D.SEQ': { add: ['DNA'], assays: ['Short Read Sequencing'] },
  'A.ALN': { add: ['D.SEQ'], assays: ['Alignment'] },
  PAV: { add: ['NHP', 'PAT'], assays: ['Patient Visit'] },
  CEX: { add: ['NHP'], assays: ['Tissue Collection'] },
};

// Enough of the children map to drive the suggestion strip and "add all".
var CHILDREN = {
  TIS: ['PAV', 'CEX', 'D.SEQ', 'A.ALN'],
  DNA: ['D.SEQ'],
  NHP: ['PAV', 'CEX'],
};

function meta() {
  var out = {};
  TYPES.forEach(function (t) { out[t.code] = { name: t.name, group: t.group }; });
  return out;
}

// Companions run the other way: the trigger is a PARENT, and add[0] is the one
// child that dominates what it produces. Real shares from the live instance.
var COMPANIONS = {
  NHP: { add: ['PAV'], assays: ['Patient Visit'] },      // 82%
  PAT: { add: ['PAV'], assays: ['Patient Visit'] },      // 100%
  BAC: { add: ['DNA'], assays: ['Bacterial Extraction'] }, // 99%
  DNA: { add: ['D.SEQ'], assays: ['Short Read Sequencing'] }, // 98%
  MUS: { add: ['TIS'], assays: ['Tissue Collection'] },  // 96%
};

function picker(extraRequires, extraCompanions) {
  var requires = {};
  Object.keys(REQUIRES).forEach(function (k) { requires[k] = REQUIRES[k]; });
  Object.keys(extraRequires || {}).forEach(function (k) {
    requires[k] = extraRequires[k];
  });
  var companions = {};
  Object.keys(COMPANIONS).forEach(function (k) { companions[k] = COMPANIONS[k]; });
  Object.keys(extraCompanions || {}).forEach(function (k) {
    companions[k] = extraCompanions[k];
  });
  return createPicker({
    types: TYPES,
    children: CHILDREN,
    meta: meta(),
    requires: requires,
    companions: companions,
  });
}

var CASES = {

  // ---- baseline: the harness really is driving the picker ----------------

  chips_track_the_selection: function () {
    var p = picker();
    p.tick('TIS');
    return {
      selected: p.selected(),
      chips: p.chips().map(function (c) { return c.code; }),
      count: p.count(),
      submitDisabled: p.submitDisabled(),
      groups: p.chips().map(function (c) { return c.group; }),
    };
  },

  suggestions_mirror_the_children_map: function () {
    var p = picker();
    p.tick('TIS');
    return p.suggestions();
  },

  search_filters_the_catalog: function () {
    var p = picker();
    p.search('flow');
    return { visible: p.visibleCodes() };
  },

  clear_resets_everything: function () {
    var p = picker();
    p.tick('D.SEQ');
    p.clear();
    return {
      selected: p.selected(),
      chips: p.chips(),
      prompts: p.prompts(),
      count: p.count(),
      submitDisabled: p.submitDisabled(),
    };
  },

  // ---- C1: the outer frame must not repaint from a stale `needs` --------

  // Ticking D.SEQ auto-adds DNA, and DNA has an unmet requirement of its own.
  // That prompt is computed by the re-entrant render() and must survive the
  // outer frame unwinding.
  c1_the_auto_added_parents_own_prompt_survives: function () {
    var p = picker();
    p.tick('D.SEQ');
    return {
      selected: p.selected(),
      prompts: p.prompts(),
      chips: p.chips().map(function (c) {
        return { code: c.code, title: c.title, classes: c.classes };
      }),
    };
  },

  // "add all" puts several types in play in one pass. PAV's requirement is
  // pushed while NHP is still unchecked, then CEX's auto-add checks NHP; the
  // strip must not end up advertising a requirement that is already met.
  c1_no_prompt_for_a_requirement_the_same_pass_satisfied: function () {
    var p = picker();
    p.tick('TIS');
    p.addAll();
    return {
      selected: p.selected(),
      prompts: p.prompts(),
    };
  },

  // ---- C2: an auto-added chip is removable -----------------------------

  c2_the_chip_x_removes_an_auto_added_parent: function () {
    var p = picker();
    p.tick('D.SEQ');
    p.removeChip('DNA');
    return { selected: p.selected(), prompts: p.prompts() };
  },

  c2_unticking_removes_an_auto_added_parent: function () {
    var p = picker();
    p.tick('D.SEQ');
    p.untick('DNA');
    return { selected: p.selected(), prompts: p.prompts() };
  },

  // The decline is scoped to the child that caused the auto-add: drop the
  // child and pick it again and the parent comes back.
  c2_the_decline_dies_with_the_child: function () {
    var p = picker();
    p.tick('D.SEQ');
    p.removeChip('DNA');
    var declined = p.selected();
    p.untick('D.SEQ');
    p.tick('D.SEQ');
    return { afterDecline: declined, afterRepick: p.selected() };
  },

  // The strip still offers the declined parent, and taking the offer works.
  c2_the_prompt_can_put_a_declined_parent_back: function () {
    var p = picker();
    p.tick('D.SEQ');
    p.removeChip('DNA');
    p.clickPromptOption('D.SEQ', 'DNA');
    return {
      selected: p.selected(),
      chips: p.chips().map(function (c) { return { code: c.code, title: c.title }; }),
    };
  },

  // ---- I1: termination must be structural, not data-dependent ----------

  // A requirement naming a code no checkbox carries used to recurse until the
  // stack blew, taking the whole picker IIFE with it.
  i1_a_requirement_naming_an_unknown_code_is_survivable: function () {
    var p = picker({ 'D.FLOW': { add: ['NOPE'], assays: [] } });
    p.tick('D.FLOW');
    var afterTick = {
      selected: p.selected(),
      prompts: p.prompts(),
      chips: p.chips().map(function (c) { return c.code; }),
    };
    // ... and the rest of the picker still works afterwards.
    p.search('tis');
    afterTick.visibleAfterSearch = p.visibleCodes();
    p.tick('TIS');
    afterTick.selectedAfterSearch = p.selected();
    return afterTick;
  },

  // ---- I2: cleanup follows the chain, and respects other children ------

  i2_unticking_the_head_of_a_chain_takes_the_whole_chain: function () {
    var p = picker();
    p.tick('A.ALN');
    var held = p.selected();
    p.untick('A.ALN');
    return {
      afterTick: held,
      afterUntick: p.selected(),
      chips: p.chips().map(function (c) { return { code: c.code, title: c.title }; }),
    };
  },

  i2_the_chip_x_also_follows_the_chain: function () {
    var p = picker();
    p.tick('A.ALN');
    p.removeChip('A.ALN');
    return { selected: p.selected() };
  },

  // NHP is auto-added for CEX, then PAV -- which also names NHP -- is picked.
  // Dropping CEX must not take NHP away from PAV.
  i2_a_shared_parent_survives_while_another_child_needs_it: function () {
    var p = picker();
    p.tick('CEX');
    p.tick('PAV');
    p.untick('CEX');
    return {
      selected: p.selected(),
      prompts: p.prompts(),
      chips: p.chips().map(function (c) { return { code: c.code, title: c.title }; }),
    };
  },

  // Nothing else needs NHP, so dropping CEX does take it away.
  i2_a_parent_no_one_needs_is_dropped: function () {
    var p = picker();
    p.tick('CEX');
    p.untick('CEX');
    return { selected: p.selected() };
  },

  // A type the user ticked themselves is never cleaned up behind their back.
  i2_a_hand_picked_parent_is_never_removed: function () {
    var p = picker();
    p.tick('NHP');
    p.tick('CEX');
    p.untick('CEX');
    return { selected: p.selected() };
  },
  // ---- companions: the opposite direction, one hop only -------------------

  comp_picking_a_subject_brings_the_visit: function () {
    // The case that motivated companions. NHP -> PAV is 82%.
    var p = picker();
    p.tick('NHP');
    return { selected: p.selected(), chips: p.chips() };
  },

  comp_a_companion_satisfies_the_requirement_it_implies: function () {
    // PAV requires one of NHP/PAT. Arriving as NHP's companion, that is
    // already met, so the prompt strip must stay silent.
    var p = picker();
    p.tick('NHP');
    return { selected: p.selected(), prompts: p.prompts() };
  },

  comp_stops_after_one_hop: function () {
    // BAC -> DNA is a 99% companion and DNA -> D.SEQ is 98%, but a companion
    // does not trigger its own: two hops of 80% confidence is 64%.
    var p = picker();
    p.tick('BAC');
    return { selected: p.selected() };
  },

  comp_requirements_still_chain_though: function () {
    // The asymmetry is deliberate: a requirement is a fact about what an
    // upload may look like, so following it is always right.
    var p = picker();
    p.tick('A.ALN');
    return { selected: p.selected() };
  },

  comp_a_companion_chip_is_removable_and_stays_removed: function () {
    var p = picker();
    p.tick('NHP');
    p.removeChip('PAV');
    return { selected: p.selected() };
  },

  comp_the_companion_goes_when_its_trigger_does: function () {
    var p = picker();
    p.tick('NHP');
    p.untick('NHP');
    return { selected: p.selected() };
  },

  comp_a_hand_picked_type_is_never_taken_away: function () {
    // PAV was chosen first, so unticking NHP must not remove it.
    var p = picker();
    p.tick('PAV');
    p.tick('NHP');
    p.untick('NHP');
    return { selected: p.selected() };
  },

  comp_a_trigger_with_no_companion_adds_nothing: function () {
    // TIS derives eleven things, none dominant, so it predicts nothing.
    var p = picker();
    p.tick('TIS');
    return { selected: p.selected() };
  },

  comp_a_companion_naming_an_unknown_code_is_survivable: function () {
    // The guard is enforced in Python; the page must not hang without it.
    var p = picker({}, { OOC: { add: ['NOPE'], assays: [] } });
    p.tick('OOC');
    return { selected: p.selected() };
  },

  comp_a_requirement_and_a_companion_cannot_prop_each_other_up: function () {
    // CEX requires NHP; NHP's companion is PAV; PAV requires NHP again. Undo
    // the only real pick and each of NHP and PAV can name the other as its
    // reason to stay. Reachability from a hand-picked root has no such
    // fixpoint, which is why prune() marks and sweeps.
    var p = picker();
    p.tick('CEX');
    p.untick('CEX');
    return { selected: p.selected() };
  },

  comp_ticking_an_added_type_claims_it_as_your_own: function () {
    // PAV is already on screen as NHP's companion. Ticking it yourself must
    // register, or removing NHP would take your own pick with it.
    var p = picker();
    p.tick('NHP');
    p.tick('PAV');
    p.untick('NHP');
    return { selected: p.selected() };
  },

};

function main() {
  var only = process.argv[2];
  var names = only ? [only] : Object.keys(CASES);
  var results = {};
  names.forEach(function (name) {
    if (!CASES[name]) { throw new Error('no such case: ' + name); }
    try {
      results[name] = CASES[name]();
    } catch (err) {
      results[name] = { error: String((err && err.message) || err) };
    }
  });
  process.stdout.write(JSON.stringify(results, null, 2) + '\n');
}

main();
