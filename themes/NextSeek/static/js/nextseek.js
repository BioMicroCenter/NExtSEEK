/**
 * NExtSEEK Theme JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    initSidebar();
    initActiveNavLink();
});

function initSidebar() {
    // Belt-and-suspenders: close drawer on click outside (the .sidebar-scrim
    // element handles the canonical click-to-close path).
    document.addEventListener('click', function(e) {
        if (document.body.classList.contains('sidebar-open')) {
            const sidebar = document.getElementById('sidebar');
            const toggleBtn = document.querySelector('.mobile-toggle');

            if (!sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
                closeSidebar();
            }
        }
    });

    // Handle submenu collapse state persistence
    const submenus = document.querySelectorAll('.sidebar-nav [data-bs-toggle="collapse"]');
    submenus.forEach(function(link) {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                const bsCollapse = bootstrap.Collapse.getOrCreateInstance(target);
                bsCollapse.toggle();
            }
        });
    });
}

function initActiveNavLink() {
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.sidebar-nav .nav-link');

    navLinks.forEach(function(link) {
        const href = link.getAttribute('href');
        if (href && href !== '#' && currentPath.startsWith(href) && href !== '/') {
            link.classList.add('active');

            // Expand parent submenu if applicable
            const parentSubmenu = link.closest('.submenu');
            if (parentSubmenu) {
                parentSubmenu.classList.add('show');
                const parentLink = document.querySelector('[href="#' + parentSubmenu.id + '"]');
                if (parentLink) {
                    parentLink.setAttribute('aria-expanded', 'true');
                }
            }
        } else if (href === '/' && currentPath === '/') {
            link.classList.add('active');
        } else if (href !== '/' && !currentPath.startsWith(href)) {
            link.classList.remove('active');
        }
    });

    // Remove active from home if we're not on home
    if (currentPath !== '/') {
        const homeLink = document.querySelector('.sidebar-nav .nav-link[href="/"]');
        if (homeLink) {
            homeLink.classList.remove('active');
        }
    }
}

function openSidebar() {
    document.body.classList.add('sidebar-open');
    document.body.style.overflow = 'hidden';
    const toggle = document.querySelector('.mobile-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', 'true');
    const firstLink = document.querySelector('#sidebar .nav-link');
    if (firstLink) firstLink.focus();
}

function closeSidebar() {
    document.body.classList.remove('sidebar-open');
    document.body.style.overflow = '';
    const toggle = document.querySelector('.mobile-toggle');
    if (toggle) {
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
    }
}

// ESC closes the drawer
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
        closeSidebar();
    }
});

// Tab focus trap while drawer is open — keyboard users can't escape into
// the page underneath. Pairs with openSidebar()'s initial focus into the
// drawer and closeSidebar()'s focus-return-to-toggle.
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Tab') return;
    if (!document.body.classList.contains('sidebar-open')) return;
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    var focusables = sidebar.querySelectorAll(
        'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusables.length === 0) return;
    var first = focusables[0];
    var last  = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
    }
});

/* ================================================
   Sidebar Quick Access nav
   ================================================ */

// Search by UID — moved here from the deleted header.embed.html.
function navUID() {
    var el = document.getElementById("search-uid");
    if (!el) return;
    var v = el.value.trim();
    if (v) {
        window.location.href = "/seek/sampletree/uid=" + encodeURIComponent(v) + "/";
    }
}

// Wire Enter-key submission on the UID input once the DOM is ready.
// (Ask Nessie is now the promoted button in includes/nessie_button.html,
// a plain link to /seek/assistant/, so it needs no handler.)
document.addEventListener("DOMContentLoaded", function () {
    var uid = document.getElementById("search-uid");
    if (uid) {
        uid.addEventListener("keypress", function (e) {
            if (e.key === "Enter") { e.preventDefault(); navUID(); }
        });
    }
});

// User-menu meatball toggle
function toggleUserMenu(btn) {
    var panel = btn.closest('.user-panel');
    if (!panel) return;
    var menu = panel.querySelector('.user-menu');
    if (!menu) return;

    var nowOpen = !menu.classList.contains('open');
    menu.classList.toggle('open', nowOpen);
    btn.setAttribute('aria-expanded', nowOpen ? 'true' : 'false');

    // Drop any previous outside/escape handlers so they don't accumulate
    // across repeated open/close cycles.
    if (panel._userMenuCleanup) {
        panel._userMenuCleanup();
        panel._userMenuCleanup = null;
    }

    if (nowOpen) {
        var onDocClick = function (e) {
            if (!panel.contains(e.target)) {
                cleanup();
            }
        };
        var onKey = function (e) {
            if (e.key === 'Escape') {
                cleanup();
                btn.focus();
            }
        };
        function cleanup() {
            menu.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
            document.removeEventListener('click', onDocClick);
            document.removeEventListener('keydown', onKey);
            panel._userMenuCleanup = null;
        }
        panel._userMenuCleanup = cleanup;

        // Defer install past the current click so this same click doesn't
        // immediately close the menu it just opened.
        setTimeout(function () {
            document.addEventListener('click', onDocClick);
            document.addEventListener('keydown', onKey);
        }, 0);
    }
}

/* ================================================
   Modal-over-route: click a [data-modal-route] link -> full-screen overlay of
   its target route, with the URL pushed to history; direct navigation to the
   route renders the standalone page (server behaviour, unchanged). Add
   data-modal-iframe for full-document routes (e.g. the connections diagram).
   ================================================ */
(function () {
  function openOverlay(url, asIframe) {
    var ov = document.createElement('div');
    ov.className = 'modal-route-overlay';
    ov.innerHTML = '<div class="modal-route-panel">' +
      '<button class="modal-route-close" aria-label="Close">&times;</button>' +
      '<div class="modal-route-body"></div></div>';
    document.body.appendChild(ov);
    var bodyEl = ov.querySelector('.modal-route-body');
    if (asIframe) {
      bodyEl.innerHTML = '<iframe src="' + url + '" class="modal-route-iframe"></iframe>';
    } else {
      fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.text(); })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, 'text/html');
          var main = doc.querySelector('#content') || doc.querySelector('main') || doc.body;
          bodyEl.innerHTML = main.innerHTML;
        }).catch(function () { bodyEl.innerHTML = '<p style="padding:1.5rem">Could not load.</p>'; });
    }
    var closed = false;
    function close() {
      if (closed) { return; }
      closed = true;
      ov.remove();
      document.removeEventListener('keydown', onKey);
      if (history.state && history.state.modalRoute) { history.back(); }
    }
    function onKey(e) { if (e.key === 'Escape') { close(); } }
    ov.querySelector('.modal-route-close').addEventListener('click', close);
    ov.addEventListener('click', function (e) { if (e.target === ov) { close(); } });
    document.addEventListener('keydown', onKey);
    window.addEventListener('popstate', function h() {
      window.removeEventListener('popstate', h);
      if (!closed) { closed = true; ov.remove(); document.removeEventListener('keydown', onKey); }
    }, { once: true });
    history.pushState({ modalRoute: true }, '', url);
  }
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-modal-route]') : null;
    if (!el) { return; }
    e.preventDefault();
    openOverlay(el.getAttribute('href'), el.hasAttribute('data-modal-iframe'));
  });
})();

/* Collapsible "About this project" toggle (and any [data-about-toggle]). */
document.addEventListener('click', function (e) {
  var btn = e.target.closest ? e.target.closest('[data-about-toggle]') : null;
  if (!btn) { return; }
  var panel = document.getElementById(btn.getAttribute('aria-controls'));
  if (!panel) { return; }
  var open = panel.hidden;
  panel.hidden = !open;
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
});
