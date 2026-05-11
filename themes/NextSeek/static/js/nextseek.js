/**
 * NExtSEEK Theme JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    initSidebar();
    initActiveNavLink();
});

function initSidebar() {
    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function(e) {
        if (document.body.classList.contains('sidebar-open')) {
            const sidebar = document.getElementById('sidebar');
            const toggleBtn = document.querySelector('.sidebar-toggle-main');

            if (!sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
                document.body.classList.remove('sidebar-open');
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

function toggleSidebar() {
    document.body.classList.toggle('sidebar-open');
}

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

// Talk to Nessie — pass the typed query to the chat_frontend via the
// /seek/chat/ redirect; chat_frontend reads ?q= on mount.
function navNessie() {
    var el = document.getElementById("ask-nessie");
    if (!el) return;
    var v = el.value.trim();
    if (v) {
        window.location.href = "/seek/chat/?q=" + encodeURIComponent(v);
    }
}

// Wire Enter-key submission on both inputs once the DOM is ready.
document.addEventListener("DOMContentLoaded", function () {
    var uid = document.getElementById("search-uid");
    if (uid) {
        uid.addEventListener("keypress", function (e) {
            if (e.key === "Enter") { e.preventDefault(); navUID(); }
        });
    }
    var nessie = document.getElementById("ask-nessie");
    if (nessie) {
        nessie.addEventListener("keypress", function (e) {
            if (e.key === "Enter") { e.preventDefault(); navNessie(); }
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
