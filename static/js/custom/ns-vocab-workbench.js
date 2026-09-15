/* Tier-grouped association workbench.
 *
 * Domain-agnostic on purpose: everything specific arrives through `config`.
 * The file must never mention a particular entity type — see the design spec's
 * boundary test. Ranking is the server's job; this renders and posts.
 */

var NS_TIER_ORDER = ['exact', 'precedent', 'fuzzy', 'conflict', 'none'];

// changeCount() sentinel: the grid's state bag could not be read at all, which
// is a different thing from "there are no pending changes".
var NS_UNREADABLE = -1;

// Field name of the workbench's synthetic per-row selection column. No row
// carries this key, and the column deliberately declares no easyui `editor` —
// the checkbox is markup a formatter emits, so closeOpenEditor()'s
// "exactly ONE editable column" PRECONDITION is untouched by it.
//
// easyui's own `checkbox: true` column is not used: it cannot be suppressed for
// individual rows, so every synthetic tier-header row would grow one, and it
// carries its own click handling that would fight this module's.
var NS_WB_SELECT_FIELD = '_nsSelect';

// The consumer page's "Accept selected (N)" toolbar button, and the count span
// inside it. Addressed by id, the same convention updateCount() uses for
// '#ns-wb-count': one workbench instance per page.
var NS_WB_SELECT_BTN = '#ns-wb-accept-selected';
var NS_WB_SELECT_COUNT = '#ns-wb-selected-count';

var NS_TIER_LABEL = {
  exact: 'Exact match',
  precedent: 'Precedent',
  fuzzy: 'Fuzzy',
  conflict: 'Conflict',
  none: 'No candidate'
};

// Live workbench instances, keyed by an internally minted id. The tier group
// header controls are rendered with inline onclick attributes (see
// titleFormatter) and so need a module-level way back to their instance —
// the same "this file deliberately exports globals" pattern as nsAcceptPost /
// nsRemovePost below.
//
// Why inline handlers and not event delegation: both consumer pages load
// easyui/datagrid-detailview.js and set `view: detailview`. That plugin's
// bindEvents() rebinds the grid body's click handler and ends with an
// unconditional stopPropagation:
//
//     body.unbind('click').bind('click', function(e){
//       ... clickHandler(e); ... e.stopPropagation();
//     });                       // datagrid-detailview.js, bindEvents()
//
// `body` is dc.body1 + dc.body2, both descendants of datagrid('getPanel'), and
// a group header is an ordinary tr.datagrid-row — so a click on a header
// control is consumed there and never bubbles to a handler delegated from the
// panel. An inline onclick lives on the target element itself, so it runs
// before any ancestor handler can stop propagation.
//
// That ancestor-stopPropagation argument is the whole reason. A delegated
// binding on dc.body2 would in fact have survived — bindEvents() opens with
// `if (state.ss.bindDetailEvents){return;}`, so its `body.unbind('click')`
// runs once and not on every render, and stopPropagation() does not suppress
// sibling handlers already bound to the same element. It is only the *panel*
// delegation that cannot work.
//
// CSP dependency: inline onclick attributes require `script-src
// 'unsafe-inline'`. Nothing sets a Content-Security-Policy today — no CSP
// middleware in the Django settings, no CSP add_header in the nginx config —
// so these fire. If deployment hardening later introduces one, the failure is
// silent: the header controls simply stop responding, with no console error.
// The fix at that point is to move these two entry points to a delegated
// binding on dc.body2 (see the paragraph above — that route does work).
var NS_WB_REGISTRY = {};
var NS_WB_SEQ = 0;

// One-time latch for nsWbClickCell's lookup-failure warning below, so a page
// stuck in the fallback path logs once rather than once per click.
var NS_WB_FALLBACK_WARNED = false;

// Inline-onclick entry point: toggle a tier group's collapsed state.
// Arguments are read off the clicked element's data-* attributes rather than
// interpolated into the attribute as JS string literals, so nothing
// server-supplied is ever parsed as code.
function nsWbToggleGroup(el, event) {
  // Stop the click before detailview's body handler turns it into an
  // onClickCell on a synthetic header row (and before the row gets selected).
  if (event && event.stopPropagation) { event.stopPropagation(); }
  var wb = NS_WB_REGISTRY[$(el).attr('data-wb')];
  if (wb) { wb.toggleGroup($(el).attr('data-tier')); }
}

// Inline-onclick entry point: bulk-accept every suggestion in one tier group.
function nsWbAcceptTier(el, event) {
  // The control sits inside the collapse target; without this the same click
  // would also toggle the group.
  if (event && event.stopPropagation) { event.stopPropagation(); }
  var wb = NS_WB_REGISTRY[$(el).attr('data-wb')];
  if (wb) { wb.acceptTier($(el).attr('data-tier')); }
}

// Inline-onclick entry point: tick or untick one row's selection checkbox.
// Same data-* discipline as the group-header handlers above — the entity id is
// read off the clicked element, never interpolated into the attribute as a JS
// string literal.
function nsWbToggleRow(el, event) {
  // Without this the click reaches the grid body, where datagrid-custom.js's
  // onClickCell would select the row and open its required combobox editor.
  if (event && event.stopPropagation) { event.stopPropagation(); }
  var wb = NS_WB_REGISTRY[$(el).attr('data-wb')];
  if (wb) { wb.toggleRow($(el).attr('data-id'), !!el.checked); }
}

// Inline-onclick entry point: accept the suggestion on every ticked row.
// The toolbar button is an ordinary easyui-linkbutton on the consumer page, so
// this is reached exactly the way the existing toolbar onclicks are; the
// instance is found through the data-wb attribute the constructor stamps on it.
function nsWbAcceptSelected(el, event) {
  if (event && event.stopPropagation) { event.stopPropagation(); }
  var wb = NS_WB_REGISTRY[$(el).attr('data-wb')];
  if (wb) { wb.acceptSelected(); }
}

// The live workbench whose grid is the DOM element `el`, or null.
//
// easyui invokes onClickCell with `this` set to the grid's original <table>
// element: the row-click handler resolves it as
// `$(t).closest("div.datagrid-view").children(".datagrid-f")[0]` (min: `_6b1`),
// and `datagrid-f` is the class easyui adds to the very element the plugin was
// called on (min: `$(_685).addClass("datagrid-f")`). So it is the same node as
// `config.grid[0]`, and identity comparison is exact — no id or selector round
// trip, and a nested grid inside a row detail resolves to itself rather than to
// its host, because `_6b1` is scoped by the enclosing datagrid-view.
function nsWbForGrid(el) {
  for (var key in NS_WB_REGISTRY) {
    if (!NS_WB_REGISTRY.hasOwnProperty(key)) { continue; }
    var wb = NS_WB_REGISTRY[key];
    if (wb && wb.ownsGrid && wb.ownsGrid(el)) { return wb; }
  }
  return null;
}

// Drop-in onClickCell for a workbench grid. Two differences from
// datagrid-custom.js's onClickCell (datagrid-custom.js:211), which stays
// frozen:
//
//  1. It refuses to open an editor on a synthetic group header row. Without
//     that, clicking a header row's editable column opens a combobox on a
//     non-data row.
//  2. It delegates ONLY when the clicked cell is this workbench's editor
//     column. The shared handler calls beginEdit(index), and easyui's beginEdit
//     builds editors for *every* editable column of the row regardless of which
//     cell was clicked (min: `_76d` -> `_770`, which walks the row's cells). On
//     an association grid the one editable column is a `required` combobox, so
//     clicking a title, an id, or the detailview expander's padding just to
//     read the row armed an editor that was invalid the instant it opened —
//     which is what made closeOpenEditor()'s "invalid but empty" branch a
//     routine occurrence rather than an edge case.
//
// Every other cell selects the row (visual feedback) and stops there. Note that
// this deliberately does NOT call endEditing() on the way past: an editor the
// curator has open on another row survives browsing, and is closed at the two
// moments that actually matter — render() and acceptRows(), both via
// closeOpenEditor().
function nsWbClickCell(index, field) {
  // The selection column is not an editor column, and a click anywhere in it
  // must not edit. The checkbox itself stops propagation before this runs;
  // this covers the padding either side of it. Returning early only skips
  // OUR code — easyui's own click handling still runs afterwards and selects
  // the row regardless, per the configured selection mode.
  if (field === NS_WB_SELECT_FIELD) { return; }
  var dg = $(this);
  var rows = dg.datagrid('getRows') || [];
  var row = rows[index];
  if (row && row._nsHeader) { return; }

  // Which column carries this grid's editor? Unknown when no workbench is
  // registered for the grid (a page that wired nsWbClickCell without
  // constructing one) or when the instance declares no vocabIdField. Either way
  // fall back to today's behaviour rather than silently disabling editing
  // altogether: a page that half-works beats one where nothing can be mapped.
  var wb = nsWbForGrid(this);
  var editorField = (wb && wb.editorField) ? wb.editorField() : null;
  if (!editorField) {
    if (!NS_WB_FALLBACK_WARNED) {
      NS_WB_FALLBACK_WARNED = true;
      console.warn('ns-vocab-workbench: nsWbClickCell could not resolve ' +
        (wb ? 'an editorField (no vocabIdField configured)' : 'a workbench instance') +
        ' for this grid; falling back to plain onClickCell for every cell.');
    }
    return onClickCell.call(this, index, field);
  }
  if (field === editorField) {
    return onClickCell.call(this, index, field);
  }

  // Select only: decline to edit and return. easyui's own click handling
  // (`_6c3`, which runs after onClickCell returns) performs the actual
  // selection itself, branching on the grid's configured selection mode —
  // there is nothing left for this branch to do.
}

// Index of the row `dg` currently has an editor open on, or -1.
//
// Derived from the grid's own DOM rather than from datagrid-custom.js's
// `editIndex`, which is a single module-level global shared by every grid on
// the page (datagrid-custom.js:200) and so may be pointing at a different
// grid's row. easyui's own finder is used because its "editing" branch is
// scoped with child combinators —
// `(body1|body2).find(">table>tbody>tr.datagrid-row-editing")` — so a nested
// grid inside a row detail could never be mistaken for this one. With frozen
// columns present (detailview injects an expander column) the same logical row
// appears in both bodies carrying the same datagrid-row-index, hence first().
function nsOpenEditIndex(dg) {
  try {
    var tr = dg.datagrid('options').finder.getTr(dg[0], '', 'editing');
    if (!tr || !tr.length) { return -1; }
    var idx = parseInt(tr.first().attr('datagrid-row-index'), 10);
    return isNaN(idx) ? -1 : idx;
  } catch (e) { return -1; }
}

// Keep datagrid-custom.js's shared `editIndex` in step, but only when it
// actually names the row just closed. Blanking it unconditionally is what
// orphans the *other* grid's open editor.
function nsReleaseEditIndex(idx) {
  if (window.editIndex === idx) { window.editIndex = undefined; }
}

// endEditing(), restricted to the grid that actually owns the open edit.
//
// The plain endEditing() cannot tell: handed a grid that is not the one with
// the open editor it validates a row that carries no datagrid-row-editing
// class, which easyui answers `true` to unconditionally (min: `_772` opens
// with `if(!tr.hasClass("datagrid-row-editing")){return true;}`), then calls
// endEdit, which is a no-op for the same reason (min: `_773`, same test), and
// finally blanks `editIndex` — leaving the real editor open but untracked, so
// the next Save commits nothing and reports "No changes to save."
function nsEndEditingOwned(dg) {
  var idx = nsOpenEditIndex(dg);
  if (idx < 0) { return true; }
  if (!dg.datagrid('validateRow', idx)) { return false; }
  dg.datagrid('endEdit', idx);
  nsReleaseEditIndex(idx);
  return true;
}

function nsPostJson(url, records, csrfToken) {
  return $.ajax({
    url: url,
    type: 'POST',
    contentType: 'application/json',
    headers: { 'X-CSRFToken': csrfToken },
    data: JSON.stringify({ records: records })
  });
}

// POST-JSON drop-in replacements for datagrid-custom.js's accept()/removeit()
// toolbar wrappers, for the admin endpoints that have moved to the
// {status, msg, updated, errors} envelope. datagrid-custom.js's own
// saveSelectedIntoDB/deleteSelectedFromDB stay GET-based on purpose (other
// pages still depend on that) — these are additive, not a replacement of it.
//
// Both close out any in-progress cell edit before reading the grid, exactly as
// accept() does — but through nsEndEditingOwned() rather than through
// datagrid-custom.js's endEditing(), so a Save on one grid cannot silently
// orphan an editor open on the other grid on the same page.

// Mirrors accept(dg, url): collects the datagrid's own pending edits
// (dg.datagrid('getChanges')) — so a conflict/none-tier row only reaches
// this at all if the curator explicitly edited it via the combobox editor —
// confirms, POSTs, and accepts the changes into the grid on a clean save.
function nsAcceptPost(dg, url, csrfToken) {
  if (!nsEndEditingOwned(dg)) { return; }
  var records = dg.datagrid('getChanges');
  if (!records.length) {
    alert('No changes to save.');
    return;
  }
  var msg = 'You have entered ' + records.length +
    ' records for saving. Do you really want to save them ?';
  $.messager.confirm('Saving record', msg, function (r) {
    if (!r) { return; }
    nsPostJson(url, records, csrfToken)
      .done(function (data) {
        if (!data.status) {
          $.messager.alert('Not saved',
                           nsEscapeHtml(data.msg) + '<br>' + nsEscapeHtml(JSON.stringify(data.errors)),
                           'error');
          return;
        }
        if (data.errors && data.errors.length) {
          // Partial success. Do NOT auto-reload — that would wipe the only
          // report of which rows failed before it can be read.
          $.messager.alert('Partly saved',
                           nsEscapeHtml(data.msg) + '<br>' + nsEscapeHtml(JSON.stringify(data.errors)),
                           'warning');
          return;
        }
        dg.datagrid('acceptChanges');
        $.messager.show({ title: 'Saved', msg: nsEscapeHtml(data.msg) });
        window.location.reload();
      })
      .fail(function (xhr) {
        $.messager.alert('Not saved', 'Request failed: ' + xhr.status, 'error');
      });
  });
}

// Mirrors removeit(dg, url): collects the datagrid's current selection
// (dg.datagrid('getSelections')), confirms, POSTs, and reloads on a clean
// delete.
function nsRemovePost(dg, url, csrfToken) {
  var records = dg.datagrid('getSelections');
  if (!records.length) {
    alert('You have not selected any row for deletion!');
    return;
  }
  var msg = 'You have selected ' + records.length +
    ' records for deletion. Do you really want to delete them ? ';
  $.messager.confirm('Deleting record', msg, function (r) {
    if (!r) { return; }
    nsPostJson(url, records, csrfToken)
      .done(function (data) {
        if (!data.status) {
          $.messager.alert('Not deleted',
                           nsEscapeHtml(data.msg) + '<br>' + nsEscapeHtml(JSON.stringify(data.errors)),
                           'error');
          return;
        }
        if (data.errors && data.errors.length) {
          // Partial success. Do NOT auto-reload — that would wipe the only
          // report of which rows failed before it can be read.
          $.messager.alert('Partly deleted',
                           nsEscapeHtml(data.msg) + '<br>' + nsEscapeHtml(JSON.stringify(data.errors)),
                           'warning');
          return;
        }
        $.messager.show({ title: 'Deleted', msg: nsEscapeHtml(data.msg) });
        window.location.reload();
      })
      .fail(function (xhr) {
        $.messager.alert('Not deleted', 'Request failed: ' + xhr.status, 'error');
      });
  });
}

function nsVocabWorkbench(config) {
  // Identifies this instance to the inline group-header handlers. Minted here,
  // never derived from data, so it is safe in an HTML attribute.
  var instanceId = 'nswb' + (++NS_WB_SEQ);
  var suggestions = {};
  var collapsed = {};
  // Ticked rows, keyed by entity id (the string idOf() returns) rather than by
  // grid row index or DOM node. render() calls loadData(), which rebuilds the
  // whole body, and collapsing a tier group removes its rows from the grid
  // entirely — a selection held in the DOM would not survive either.
  var selected = {};
  // Set when the suggestions endpoint could not be reached or returned
  // status: 0 — distinct from "the resolver looked and found nothing".
  // Read by updateCount() (the count-line notice) and suggestedFormatter()
  // (per-row "unavailable" vs "no candidate"). See loadSuggestions().
  var suggestionsFailed = false;

  function idOf(row) { return String(row[config.entityIdField]); }

  // All candidates the resolver returned for this row (always at least one
  // when present). Most tiers carry exactly one; the conflict tier is the
  // deliberate exception — see candidatesOf() callers below.
  function candidatesOf(row) {
    return suggestions[idOf(row)] || [];
  }

  function tierOf(row) {
    if (row[config.vocabIdField]) { return 'mapped'; }
    var found = candidatesOf(row);
    if (!found.length) { return 'none'; }
    var tier = found[0].tier;
    // An unrecognised tier must still surface as a visible row rather than
    // vanish: layout() buckets by this return value, and the emit loop only
    // walks NS_TIER_ORDER + 'mapped', so any other key would be silently
    // dropped from the grid while updateCount() (which counts independently)
    // still counted it. Falling back to 'none' keeps the row reachable.
    return NS_TIER_LABEL.hasOwnProperty(tier) ? tier : 'none';
  }

  // Single "best" candidate, for the tiers where that concept is meaningful
  // (exact/precedent/fuzzy accept actions). Never call this for a conflict
  // row's presentation — that would silently pick a side of a disagreement
  // the resolver flagged on purpose. Use candidatesOf() there instead.
  function candidateOf(row) {
    var found = candidatesOf(row);
    return found.length ? found[0] : null;
  }

  // May this row be accepted in bulk from a checkbox?
  //
  // Deliberately the same exclusion set the tier-header control uses — no
  // synthetic header row, no already-mapped row, no conflict row and no
  // no-candidate row — plus the requirement that the single candidate actually
  // carries an id to write. tierOf() folds any unrecognised tier string to
  // 'none', so a malformed candidate such as {vocabulary_id: 42, tier: 'typo'}
  // is refused here even though its id would otherwise be writable.
  function isSelectable(row) {
    if (!row || row._nsHeader) { return false; }
    var tier = tierOf(row);
    if (tier === 'mapped' || tier === 'conflict' || tier === 'none') { return false; }
    var candidate = candidateOf(row);
    return !!(candidate && candidate.vocabulary_id);
  }

  // The row carrying this entity id, or null. Looked up in config.rows, not in
  // the grid: a collapsed tier group's rows are not in the grid at all.
  function rowById(id) {
    var wanted = String(id);
    var found = null;
    (config.rows || []).forEach(function (r) {
      if (idOf(r) === wanted) { found = r; }
    });
    return found;
  }

  // Every ticked row that is *still* safe to write, re-derived from scratch on
  // each call. A row can stop being selectable after it was ticked — an inline
  // combobox edit sets vocabIdField, which makes tierOf() report 'mapped' —
  // and this is what keeps both the count and the write in step with that.
  function selectedRows() {
    return (config.rows || []).filter(function (r) {
      return selected[idOf(r)] && isSelectable(r);
    });
  }

  // Rows regrouped into tier order, each group preceded by a synthetic header
  // row. easyui has no grouping view, so the header is a row the formatter
  // renders differently — cheaper and less fragile than a grouping plugin.
  function layout() {
    var buckets = {};
    NS_TIER_ORDER.concat(['mapped']).forEach(function (t) { buckets[t] = []; });
    (config.rows || []).forEach(function (row) {
      var tier = tierOf(row);
      (buckets[tier] = buckets[tier] || []).push(row);
    });

    var out = [];
    NS_TIER_ORDER.concat(['mapped']).forEach(function (tier) {
      var group = buckets[tier] || [];
      if (!group.length) { return; }
      // _nsCount is the true group size (the tier badge: "how many rows are
      // in this tier"). _nsAcceptableCount is how many of them isSelectable()
      // — the same predicate acceptRows() itself applies — would actually let
      // through a bulk accept (the button: "how many I can accept"). They can
      // diverge: a candidate can carry a tier without a vocabulary_id. See
      // titleFormatter's "Accept all N" button below.
      out.push({
        _nsHeader: true,
        _nsTier: tier,
        _nsCount: group.length,
        _nsAcceptableCount: group.filter(isSelectable).length
      });
      if (!collapsed[tier]) { out = out.concat(group); }
    });
    return out;
  }

  // easyui's own state bag for this grid, or null if it cannot be read.
  function gridState() {
    try { return $.data(config.grid[0], 'datagrid') || null; } catch (e) { return null; }
  }

  // Number of pending changes, or NS_UNREADABLE when the grid's own state bag
  // could not be read at all. The two are deliberately distinguishable: with
  // the state bag gone, getChanges() throws while reading .insertedRows, and
  // reporting that as 0 would send render() straight on into loadData(), which
  // throws for the same reason — past the very guard written to catch it.
  function changeCount() {
    try { return (config.grid.datagrid('getChanges') || []).length; } catch (e) { return NS_UNREADABLE; }
  }

  // Does the row at `index` already carry work easyui has recorded? An open
  // editor's typing is not tracked until endEdit commits it (min: `_773` is
  // what pushes to updatedRows), so this is true only for a row that was
  // committed earlier and re-opened, or one appendRow() inserted (min: `_7a5`
  // pushes to insertedRows immediately). Identity comparison is right: both
  // arrays hold the row objects themselves.
  function rowHasTrackedWork(index) {
    var st = gridState();
    // Cannot tell — assume the row holds work rather than discard it.
    if (!st) { return true; }
    var row = (config.grid.datagrid('getRows') || [])[index];
    if (!row) { return false; }
    return ($.isArray(st.updatedRows) && $.inArray(row, st.updatedRows) >= 0) ||
           ($.isArray(st.insertedRows) && $.inArray(row, st.insertedRows) >= 0);
  }

  // Close this grid's open editor, if it has one. Returns false only when the
  // open row is invalid *and* holds work worth keeping — the one case where
  // regrouping must be refused.
  function closeOpenEditor() {
    // Nothing open in *this* grid: leave the shared `editIndex` alone. See
    // nsEndEditingOwned() for why touching it here would orphan an editor the
    // other grid on the page has open.
    var idx = nsOpenEditIndex(config.grid);
    if (idx < 0) { return true; }

    if (nsEndEditingOwned(config.grid)) { return true; }

    // Invalid. The mapping editor is declared required, and easyui's beginEdit
    // creates editors for *every* editable column of the row it is given (min:
    // `_76d` -> `_770`, which walks the row's cells), so merely clicking a
    // read-only-looking cell opens the required editor empty; validateRow then
    // forces a validatebox pass over it (min: `_772` calls
    // `vbox.validatebox("validate")` before counting `.validatebox-invalid`)
    // and answers false. That state means the curator entered nothing, so
    // there is no work to lose: cancel the edit and let the regroup proceed,
    // rather than trapping them behind an alert whose only other exit —
    // Cancel — would discard every pending change on the grid.
    // PRECONDITION: each workbench grid has exactly ONE editable column, so an
    // invalid row is necessarily an empty one and cancelling cannot lose typed
    // input. rowHasTrackedWork() asks only whether the row was already
    // committed — it does not read open editors. If a second editable column is
    // ever added (a free-text note, say), a curator could type into it, leave
    // the required combobox empty, and have that text discarded here silently.
    // Add a check of the open editors' values against the row before doing so.
    if (rowHasTrackedWork(idx)) { return false; }
    config.grid.datagrid('cancelEdit', idx);
    nsReleaseEditIndex(idx);
    return true;
  }

  // Returns true when the table was regrouped, false when the attempt was
  // refused. toggleGroup() relies on that to decide whether its collapsed[]
  // flip stands.
  function render() {
    // Close any open editor first: loadData() rebuilds the body DOM, which
    // would strand it (and leave datagrid-custom.js's editIndex pointing at a
    // row that has since moved). A valid editor is committed, so the edit
    // becomes a tracked change that the preservation below carries across.
    if (!closeOpenEditor()) {
      $.messager.alert('Finish this row first',
                       'The row being edited is not valid yet. Complete or cancel it ' +
                       'before regrouping the table.',
                       'warning');
      return false;
    }

    // loadData() ends with an internal acceptChanges. In jquery-easyui 1.5.2
    // (jquery.easyui.min.js):
    //
    //     loadData:function(jq,data){ return jq.each(function(){
    //       _6e0(this,data); _7ae(this); }); }
    //
    // and `_7ae` re-snapshots originalRows from the *current* rows and empties
    // updatedRows / insertedRows / deletedRows. layout() hands back the SAME
    // row objects the cell editor already mutated in place, so without the
    // save/restore below a committed inline edit would still *look* applied —
    // row[vocabIdField] set, tierOf() 'mapped', row relocated to the "Already
    // mapped" group — while getChanges() went empty and Save reported "No
    // changes to save."
    //
    // Restoring the three arrays by reference is exact rather than
    // approximate: getChanges() is a plain concat of them (min: `_795`), and
    // they hold references to the row objects themselves (`_773`, the endEdit
    // path, and `_7a8`, updateRow, both push `opts.finder.getRow(...)`). It
    // also covers a pending row that a newly collapsed group has taken out of
    // data.rows entirely, because nsAcceptPost() POSTs getChanges() and never
    // consults data.rows.
    //
    // Nothing here can invent a change: only rows easyui had already recorded
    // are put back, so a row the curator never edited stays untracked and can
    // never reach a save endpoint.
    var pending = null;
    var changes = changeCount();
    if (changes !== 0) {
      var before = gridState();
      if (changes === NS_UNREADABLE || !before || !$.isArray(before.updatedRows)) {
        // Cannot preserve what cannot be read. Refuse rather than drop the
        // curator's work on the floor. The NS_UNREADABLE arm is what makes
        // this reachable for the state-bag-is-gone case at all — see
        // changeCount().
        $.messager.alert('Unsaved changes',
                         'Save or Cancel your pending changes before regrouping the table.',
                         'warning');
        return false;
      }
      pending = {
        updated: before.updatedRows.slice(),
        inserted: before.insertedRows.slice(),
        deleted: before.deletedRows.slice(),
        originalRows: before.originalRows
      };
    }

    config.grid.datagrid('loadData', layout());

    if (pending) {
      var after = gridState();
      if (after) {
        after.updatedRows = pending.updated;
        after.insertedRows = pending.inserted;
        after.deletedRows = pending.deleted;
        // Keep the pre-edit snapshot as well, so Cancel (rejectChanges, min:
        // `_7b4`, which assigns data.rows = originalRows) still has something
        // to roll back to; easyui has just replaced it with copies of the
        // already-edited rows.
        if ($.isArray(pending.originalRows) && pending.originalRows.length) {
          after.originalRows = pending.originalRows;
        }
      }
    }
    updateCount();
    // A ticked row can have left the selectable set since it was ticked (an
    // inline edit makes it 'mapped'), and the freshly rendered checkboxes are
    // drawn from `selected` — keep the button's count in step with both.
    updateSelectedCount();
    return true;
  }

  function updateCount() {
    var unmapped = (config.rows || []).filter(function (r) {
      return !r[config.vocabIdField];
    }).length;
    var text = unmapped + ' unmapped';
    if (suggestionsFailed) { text += ' — suggestions unavailable'; }
    $('#ns-wb-count').text(text);
  }

  function titleFormatter(value, row) {
    if (row._nsHeader) {
      // One glyph in two rotations, not two glyphs: .attrs-chevron (the shared
      // component in themes/NextSeek/static/css/nextseek.css) owns the
      // transition, .ns-wb-caret.is-open owns the 90deg turn. Same motion as
      // the per-row detailview expander a few rules below it there.
      var arrow = '<span class="attrs-chevron ns-wb-caret' +
                  (collapsed[row._nsTier] ? '' : ' is-open') +
                  '" aria-hidden="true">&#9656;</span>';
      var label = NS_TIER_LABEL[row._nsTier] || 'Already mapped';
      // Tier strings reach here from the server (candidate.tier), so escape
      // them even though tierOf() only ever returns a known key — the
      // attributes below are what the inline handlers read their arguments
      // from.
      var tierAttr = nsEscapeHtml(row._nsTier);
      var wbAttr = nsEscapeHtml(instanceId);
      var countText = nsEscapeHtml(String(row._nsCount));
      var acceptableCount = row._nsAcceptableCount || 0;
      var btn = '';
      // Guard 1 of 4. Conflict, "no candidate" and already-mapped groups get
      // no bulk-accept control: there is nothing safe to accept on their
      // behalf. The acceptableCount check alongside it is not a guard in that
      // sense — layout() already computed it with isSelectable(), the same
      // predicate acceptRows() applies to every row — it only keeps the label
      // honest: a group can carry an exact/precedent/fuzzy tier and still have
      // zero rows with a writable vocabulary_id, and this is what stops that
      // group from promising a count of rows it cannot deliver, the same way
      // the conflict/none/mapped tiers already render no button. Keep the
      // tier exclusion in sync with the guards in acceptTier(),
      // acceptSelected() and acceptRows() below — four independent checks so
      // a conflict row can never reach a write. selectFormatter() applies the
      // same isSelectable() exclusion to the per-row checkbox, which is a
      // render-time sibling of this one and not counted separately.
      if (row._nsTier !== 'none' && row._nsTier !== 'conflict' && row._nsTier !== 'mapped' &&
          acceptableCount > 0) {
        btn = '<a href="javascript:void(0)" class="attrs-btn ns-wb-accept-tier" ' +
              'data-wb="' + wbAttr + '" data-tier="' + tierAttr + '" ' +
              'onclick="nsWbAcceptTier(this, event)">Accept all ' +
              nsEscapeHtml(String(acceptableCount)) + '</a>';
      }
      // Shape from the shared components (.attrs-count for the group's row
      // count, with <b> so it picks up that component's mono numerals);
      // .ns-tier-<tier> adds nothing but the semantic colour.
      return '<span class="ns-wb-group" data-wb="' + wbAttr + '" data-tier="' + tierAttr +
             '" onclick="nsWbToggleGroup(this, event)">' + arrow +
             '<span>' + nsEscapeHtml(label) + '</span><span class="attrs-count ns-tier-' +
             tierAttr + '"><b>' + countText + '</b></span>' + btn + '</span>';
    }
    return nsEscapeHtml(value == null ? '' : String(value));
  }

  // Per-row selection column. Renders a checkbox only for rows that carry one
  // unambiguous suggestion; everything else gets a plainly inert dash.
  //
  // Render-time sibling of Guard 1 (see titleFormatter). It is not by itself
  // load-bearing: toggleRow() refuses to record an unselectable row,
  // acceptSelected() re-filters, and acceptRows() re-derives the tier again.
  function selectFormatter(value, row) {
    if (!row || row._nsHeader) { return ''; }
    if (!isSelectable(row)) {
      var why = 'No single suggestion to accept — pick a value with the dropdown, then Save.';
      if (row[config.vocabIdField]) {
        why = 'Already mapped.';
      } else if (tierOf(row) === 'conflict') {
        why = 'Candidates disagree — pick a value with the dropdown, then Save.';
      }
      // A disabled checkbox still reads as "selectable, currently off"; an
      // em dash reads as "not applicable here", which is the truth.
      return '<span class="ns-wb-nocheck" title="' + nsEscapeHtml(why) + '">&#8212;</span>';
    }
    var id = idOf(row);
    return '<input type="checkbox" class="ns-wb-check" ' +
           'data-wb="' + nsEscapeHtml(instanceId) + '" ' +
           'data-id="' + nsEscapeHtml(id) + '" ' +
           (selected[id] ? 'checked="checked" ' : '') +
           'title="Select this row for Accept selected" ' +
           'onclick="nsWbToggleRow(this, event)">';
  }

  function suggestedFormatter(value, row) {
    if (row._nsHeader) { return ''; }
    if (row[config.vocabIdField]) {
      return nsEscapeHtml(row[config.vocabTitleField] || '');
    }
    if (!config.suggestionsUrl) { return ''; }
    if (suggestionsFailed) {
      // Distinct from "no candidate": the resolver was never reached, so
      // saying "no candidate" would positively assert the opposite of the
      // truth. Visually distinct class (ns-tier-unavailable, the neutral
      // grey variant) from ns-tier-none, which is crimson.
      return '<span class="attrs-chip ns-tier-unavailable">suggestions unavailable</span>';
    }
    var candidates = candidatesOf(row);
    if (!candidates.length) {
      return '<span class="attrs-chip ns-tier-none">no candidate</span>';
    }
    if (candidates.length > 1 || candidates[0].tier === 'conflict') {
      // Conflict tier: the resolver returned multiple candidates because
      // already-mapped entities with the same title disagree — or flagged a
      // single candidate as a conflict outright. Branching on the tier
      // itself (not just the count) means a conflict row with exactly one
      // candidate still renders as a conflict, not as "the" answer wearing
      // a conflict badge. Render every alternative side by side, with none
      // singled out — showing candidates[0] alone here would silently pick
      // a winner.
      return candidates.map(function (c) {
        return '<span class="attrs-chip ns-tier-' + nsEscapeHtml(c.tier) + '">' + nsEscapeHtml(c.tier) +
               '</span> ' + nsEscapeHtml(c.vocabulary_title || '');
      }).join(' <span class="attrs-muted ns-wb-vs">vs</span> ');
    }
    var candidate = candidates[0];
    if (!candidate.vocabulary_title) {
      return '<span class="attrs-chip ns-tier-none">no candidate</span>';
    }
    // Shape from .attrs-chip (shared); .ns-tier-<tier> adds only the colour.
    return '<span class="attrs-chip ns-tier-' + nsEscapeHtml(candidate.tier) + '">' +
           nsEscapeHtml(candidate.tier) + '</span> ' +
           nsEscapeHtml(candidate.vocabulary_title);
  }

  // easyui's native detailview: evidence expands in place under the row.
  function detailFormatter(index, row) {
    if (row._nsHeader) { return ''; }
    // Mirrors suggestedFormatter()'s ordering: an already-mapped row needs no
    // suggestion at all, so it is special-cased before suggestionsFailed (or
    // anything else about the resolver) is even considered. Checking
    // suggestionsFailed first — the previous order — made a mapped row's
    // evidence read "Suggestions could not be loaded" during a failed fetch,
    // which is both false (nothing was needed from that fetch for this row)
    // and contradicts suggestedFormatter's own cell, which already shows the
    // mapped title regardless of suggestionsFailed.
    if (row[config.vocabIdField]) {
      return '<div class="ns-wb-evidence">Already mapped to ' +
             nsEscapeHtml(row[config.vocabTitleField] || '—') + '.</div>';
    }
    if (suggestionsFailed) {
      // Match the "unavailable" badge suggestedFormatter() renders for this
      // case — without this check the evidence panel would say "No
      // suggestion available", which implies the resolver ran and found
      // nothing rather than that it was never reached.
      return '<div class="ns-wb-evidence">Suggestions could not be loaded.</div>';
    }
    var candidates = candidatesOf(row);
    if (!candidates.length) {
      return '<div class="ns-wb-evidence">No suggestion available.</div>';
    }
    if (candidates.length === 1 && candidates[0].tier !== 'conflict') {
      var only = candidates[0];
      return '<div class="ns-wb-evidence"><dl>' +
             '<dt>Suggested</dt><dd>' + nsEscapeHtml(only.vocabulary_title || '—') + '</dd>' +
             '<dt>Why</dt><dd>' + nsEscapeHtml(only.basis || '') +
             (only.support != null ? ' (support: ' + nsEscapeHtml(String(only.support)) + ')' : '') +
             '</dd></dl></div>';
    }
    // Conflict tier: list every candidate on equal footing, each with its
    // own basis and support count, so the disagreement is visible rather
    // than collapsed into a single suggestion.
    //
    // The candidate's title moved out of the <dt> and into the <dd>: <dt> now
    // wears the shared field-label treatment (uppercase, see the
    // `.attrs-detail-field label, .ns-wb-evidence dt` rule in
    // themes/NextSeek/static/css/nextseek.css), and a controlled-vocabulary
    // term must be shown in the case it is actually stored in. Same three
    // facts per candidate as before, same order.
    var items = candidates.map(function (c, i) {
      return '<dt>Candidate ' + (i + 1) + '</dt>' +
             '<dd><b>' + nsEscapeHtml(c.vocabulary_title || '—') + '</b> — ' +
             nsEscapeHtml(c.basis || '') +
             (c.support != null ? ' (support: ' + nsEscapeHtml(String(c.support)) + ')' : '') +
             '</dd>';
    }).join('');
    return '<div class="ns-wb-evidence"><dl>' + items + '</dl></div>';
  }

  function acceptRows(rows, emptyMessage) {
    // Close out any open editor first, exactly as render() does and for the
    // same reason: a clean run through this function ends in
    // window.location.reload(), which would silently discard whatever a still
    // -open editor on this grid holds. Through closeOpenEditor() — not a bare
    // nsEndEditingOwned() gate — because beginEdit() builds an editor for
    // *every* editable column of a row the instant it is clicked, required or
    // not: a curator who only clicked a row to look at it, and ticked others
    // for Accept, would otherwise leave that row's required combobox open and
    // empty, nsEndEditingOwned() would report the grid invalid, and this whole
    // function would return with no alert and no visible effect while the
    // toolbar button still read its stale "(N)". closeOpenEditor() tells that
    // apart from an editor that is invalid *and* holds tracked work, and only
    // the latter is refused — with the same alert render() shows for its own
    // identical case, so the curator is told rather than left staring at a
    // dead button.
    if (!closeOpenEditor()) {
      $.messager.alert('Finish this row first',
                       'The row being edited is not valid yet. Complete or cancel it ' +
                       'before accepting.',
                       'warning');
      return;
    }

    // A row committed by that (or an earlier) edit but not yet Saved is
    // tracked in getChanges() and is not necessarily one of `rows` — ticking a
    // few rows, hand-fixing one via the combobox, then hitting "Accept
    // selected" or a tier's "Accept all" is exactly the interleaving this
    // feature was built for. Reload would discard that pending edit with no
    // warning, so it is surfaced here rather than silently lost.
    var pending = changeCount();
    if (pending === NS_UNREADABLE) {
      // Cannot tell whether there is unsaved work — refuse rather than risk
      // discarding it. Same stance render() takes for the identical
      // unreadable-state-bag case (see changeCount()).
      $.messager.alert('Unsaved changes',
                       'Save or Cancel your pending changes before using Accept.',
                       'warning');
      return;
    }

    var records = [];
    rows.forEach(function (row) {
      // Guard 4 of 4. Defense in depth: mapped, conflict and none-tier rows
      // must never be written, no matter which caller assembled this row
      // list — the tier header, the checkbox selection, or anything added
      // later. tierOf() falls back to 'none' for any tier string it does not
      // recognise (see tierOf() above), so a malformed candidate can be
      // 'none'-tier while still carrying a populated vocabulary_id — the
      // !candidate check below no longer catches that case incidentally, so
      // this guard must stand on its own. The 'mapped' arm has a reachable
      // caller: acceptSelected() computes its row list via selectedRows()
      // *before* calling acceptRows(), and acceptRows() opens by calling
      // closeOpenEditor(), which can commit an open combobox edit on one of
      // those same rows — turning it 'mapped' in the gap between the two.
      // Without this arm that row would still resolve a candidate from the
      // resolver's suggestion and overwrite the mapping the curator just made.
      var tier = tierOf(row);
      if (tier === 'mapped' || tier === 'conflict' || tier === 'none') { return; }
      var candidate = candidateOf(row);
      if (!candidate || !candidate.vocabulary_id) { return; }
      var record = {};
      record[config.entityIdField] = row[config.entityIdField];
      record[config.vocabIdField] = candidate.vocabulary_id;
      records.push(record);
    });
    // How many of the rows this call considered did NOT end up in records —
    // skipped above by the guard (mapped/conflict/none, or no writable
    // vocabulary_id on the candidate). Folded into the post-save message
    // below so a tier button that promised N never reports only "wrote M"
    // with no account of the gap. The records.length === 0 case gets its own
    // dedicated "Nothing to accept" message instead (below) — this note is
    // for the partial case that message does not cover.
    var skipped = rows.length - records.length;
    if (!records.length) {
      // Wording is caller-supplied rather than fixed here: acceptTier()'s
      // rows are all one group, but acceptSelected()'s are the curator's
      // checkbox ticks, and closeOpenEditor() above can commit an edit that
      // turns the very row they ticked into 'mapped' before this filter runs
      // — "no rows in this group" would read as a bug on that path.
      $.messager.alert('Nothing to accept', emptyMessage, 'info');
      return;
    }

    function post() {
      nsPostJson(config.saveUrl, records, config.csrfToken)
        .done(function (data) {
          if (!data.status) {
            // $.messager renders msg as HTML (that's why the literal '<br>'
            // below works as a line break) — escape the data-derived values,
            // not the separator, so an echoed entity/vocabulary title can't
            // inject markup.
            $.messager.alert('Not saved',
                             nsEscapeHtml(data.msg) + '<br>' + nsEscapeHtml(JSON.stringify(data.errors)),
                             'error');
            return;
          }
          // Same skip note either way below: server-reported errors and
          // client-side skips are independent gaps between "considered" and
          // "written", and a curator reading either message deserves to see
          // both if both happened.
          var skipNote = skipped > 0 ?
            ' (' + nsEscapeHtml(String(skipped)) + ' of ' + nsEscapeHtml(String(rows.length)) +
            ' row(s) considered had no acceptable suggestion and were not sent.)' : '';
          if (data.errors && data.errors.length) {
            // Partial success. Do NOT auto-reload — that would wipe the only
            // report of which rows failed before it can be read.
            $.messager.alert('Partly saved',
                             nsEscapeHtml(data.msg) + skipNote + '<br>' +
                             nsEscapeHtml(JSON.stringify(data.errors)),
                             'warning');
            return;
          }
          $.messager.show({ title: 'Saved', msg: nsEscapeHtml(data.msg) + skipNote });
          window.location.reload();
        })
        .fail(function (xhr) {
          $.messager.alert('Not saved', 'Request failed: ' + xhr.status, 'error');
        });
    }

    if (pending === 0) {
      post();
      return;
    }
    // Pending work exists outside this accept action. Confirm before doing
    // anything that ends in a reload, rather than either refusing outright
    // (blocking the routine "tick a few, hand-fix one, accept the rest" flow)
    // or proceeding silently (discarding the hand-fix).
    $.messager.confirm('Unsaved changes',
                       'This grid has ' + pending + ' unsaved change(s) that have not been ' +
                       'Saved. Accepting now will reload the page and discard them. Continue?',
                       function (r) { if (r) { post(); } });
  }

  // Reached from the group header's inline onclick, via nsWbAcceptTier().
  //
  // Guard 2 of 4, and independent of the others: titleFormatter never renders
  // the control for these groups, and acceptRows() re-derives the tier of every
  // row it is handed — but a tier string arriving here from anywhere else must
  // still be refused before it can select rows for a write.
  function acceptTier(tier) {
    if (!tier) { return; }
    if (tier === 'conflict' || tier === 'none' || tier === 'mapped') { return; }
    acceptRows((config.rows || []).filter(function (r) {
      return !r[config.vocabIdField] && tierOf(r) === tier;
    }), 'No rows in this group carry a suggestion.');
  }

  // Reached from the checkbox's inline onclick, via nsWbToggleRow().
  //
  // Refuses to record anything that is not selectable *now*, so the selection
  // set can never hold a header, mapped, conflict or no-candidate row — not
  // even if a checkbox for one somehow reached the DOM.
  function toggleRow(id, checked) {
    if (id == null || id === '') { return; }
    var row = rowById(id);
    if (!row || !isSelectable(row)) { return; }
    if (checked) { selected[String(id)] = true; } else { delete selected[String(id)]; }
    updateSelectedCount();
  }

  // Live count in the "Accept selected (N)" button label. Only the span inside
  // the button is rewritten: easyui's linkbutton rebuilds the <a>'s children
  // into .l-btn-left/.l-btn-text (preserving the inner HTML it parsed), so
  // writing to the <a> itself would tear that structure down.
  function updateSelectedCount() {
    $(NS_WB_SELECT_COUNT).text(String(selectedRows().length));
  }

  // Re-derive both toolbar counts without a full render(). updateCount()'s
  // "N unmapped" text has the identical staleness the "Accept selected (N)"
  // count used to have and the same trigger: an inline combobox commit
  // reached only through onEndEdit, never through render(), so nothing else
  // refreshes it until the next group collapse. Same fix, same reason — see
  // the exported refreshSelectedCount below — so both live behind the one
  // hook rather than fixing the count a caller happened to notice first.
  function refreshCounts() {
    updateCount();
    updateSelectedCount();
  }

  // Reached from the toolbar's inline onclick, via nsWbAcceptSelected().
  //
  // Guard 3 of 4, and independent of the others: selectFormatter renders no
  // checkbox for an unacceptable row and toggleRow refuses to record one, but a
  // selection arriving here by any other route must still be filtered before it
  // can select rows for a write. selectedRows() applies isSelectable() to every
  // candidate row, which folds an unrecognised tier to 'none' — so a row
  // carrying {vocabulary_id: 42, tier: 'typo'} is dropped here, before
  // acceptRows() is called at all, and again inside it.
  function acceptSelected() {
    var rows = selectedRows();
    if (!rows.length) {
      $.messager.alert('Nothing selected',
                       'Tick the checkbox on one or more suggested rows first. ' +
                       'Conflicting and no-candidate rows have no checkbox — pick a ' +
                       'value with the dropdown and press Save instead.',
                       'info');
      return;
    }
    acceptRows(rows, 'None of the ticked rows still carry a suggestion to accept — an ' +
                     'edit committed just now may have already mapped the only one that did.');
  }

  // Reached from the group header's inline onclick, via nsWbToggleGroup().
  //
  // The flip only stands if render() actually regrouped. Both of render()'s
  // refusals return before loadData(), so nothing has moved on screen and the
  // restore below leaves collapsed[] exactly as the curator last saw it —
  // otherwise a refused click would still have flipped the flag, and the next
  // click would flip it back and re-render the unchanged state, so the group
  // would only appear to move on the third click.
  function toggleGroup(tier) {
    if (!tier) { return; }
    var was = collapsed[tier];
    collapsed[tier] = !was;
    if (!render()) { collapsed[tier] = was; }
  }

  function loadSuggestions() {
    if (!config.suggestionsUrl) { render(); return; }
    $.getJSON(config.suggestionsUrl)
      .done(function (data) {
        // Degrade, never block: a failed lookup leaves the page fully usable.
        if (data && data.status) {
          suggestions = data.suggestions || {};
          suggestionsFailed = false;
        } else {
          // status: 0 is a rejected lookup, not an empty one — do not let it
          // masquerade as "the resolver found nothing for every row".
          suggestions = {};
          suggestionsFailed = true;
        }
        render();
      })
      .fail(function () {
        suggestions = {};
        suggestionsFailed = true;
        render();
      });
  }

  // Is `el` this instance's grid element? Used by nsWbForGrid() to map easyui's
  // fixed onClickCell(index, field) signature — which carries no reference to
  // the workbench — back to this closure. Tolerates a config with no grid.
  function ownsGrid(el) {
    return !!(el && config.grid && config.grid.length && config.grid[0] === el);
  }

  // The one column this workbench puts an editor on, or null when the config
  // named none. The literal field name lives on the consumer page, never here
  // — see the file header. Returning null (rather than a name that matches
  // nothing) is what routes an unconfigured instance to nsWbClickCell's
  // fall-back branch instead of leaving its grid uneditable.
  function editorField() {
    return config.vocabIdField || null;
  }

  var api = {
    // Identity and editor-column probes for the module-level nsWbClickCell().
    // Public only because that handler cannot reach into this closure any
    // other way; nothing on the consumer pages calls them.
    ownsGrid: ownsGrid,
    editorField: editorField,
    titleFormatter: titleFormatter,
    selectFormatter: selectFormatter,
    suggestedFormatter: suggestedFormatter,
    detailFormatter: detailFormatter,
    acceptTier: acceptTier,
    acceptSelected: acceptSelected,
    toggleRow: toggleRow,
    toggleGroup: toggleGroup,
    // Re-derive both toolbar counts without a full render(). The consumer
    // page's onEndEdit hook calls this after committing an inline combobox
    // edit — easyui assigns row[vocabIdField] before invoking onEndEdit, so
    // tierOf() (and therefore isSelectable()) already sees the post-edit
    // state: selectedRows() drops any row the edit just moved out of the
    // selectable set, and updateCount()'s own vocabIdField filter counts it
    // as mapped too. Exported name is unchanged (callers already have it)
    // even though it now does more — see refreshCounts() above.
    refreshSelectedCount: refreshCounts,
    reload: loadSuggestions
  };
  NS_WB_REGISTRY[instanceId] = api;

  // Point the page's "Accept selected" button at this instance, the same way
  // the tier-header controls carry data-wb. Safe to do before easyui has
  // parsed the linkbutton: createButton() empties the <a>'s children and
  // rewrites its class and id, but leaves other attributes alone. A page with
  // no such button (a workbench with nothing to accept) is a no-op here and in
  // updateSelectedCount().
  $(NS_WB_SELECT_BTN).attr('data-wb', instanceId);

  // Deferred so the caller's `nsWorkbench = nsVocabWorkbench(...)` assignment
  // completes before any formatter runs. The no-suggester path (clades) renders
  // synchronously and would otherwise dereference an undefined nsWorkbench.
  setTimeout(loadSuggestions, 0);

  return api;
}
