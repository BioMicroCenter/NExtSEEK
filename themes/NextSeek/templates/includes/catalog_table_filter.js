/* Live filter + group-collapse for the shared catalog table. Included inline. */
(function () {
  var box = document.querySelector('[data-cat-filter]');
  var count = document.querySelector('[data-cat-count]');
  var rows = Array.prototype.slice.call(document.querySelectorAll('.cat-row'));
  var groups = Array.prototype.slice.call(document.querySelectorAll('[data-group]'));

  function refreshCount() {
    if (!count) { return; }
    // A row counts only when it is neither filtered out (hidden) nor inside a
    // collapsed group, so the tally matches what is actually on screen.
    var visible = rows.filter(function (r) {
      if (r.hidden) { return false; }
      var g = r.closest('[data-group]');
      return !(g && g.classList.contains('is-collapsed'));
    }).length;
    count.textContent = visible + (visible === 1 ? ' result' : ' results');
  }

  if (box) {
    box.addEventListener('input', function () {
      var q = box.value.trim().toLowerCase();
      rows.forEach(function (r) {
        r.hidden = q !== '' && (r.getAttribute('data-filter') || '').indexOf(q) === -1;
      });
      // A group whose every row is filtered out hides its heading too.
      groups.forEach(function (g) {
        g.hidden = !g.querySelector('.cat-row:not([hidden])');
      });
      refreshCount();
    });
  }
  refreshCount();

  // Click a clade heading to collapse/expand that group.
  document.querySelectorAll('[data-group-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tb = btn.closest('[data-group]');
      if (tb) { tb.classList.toggle('is-collapsed'); refreshCount(); }
    });
  });
})();
