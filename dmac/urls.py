from __future__ import unicode_literals

from django.urls import re_path
from django.conf.urls import include
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.views.i18n import set_language

from django.http import Http404
from mezzanine.accounts.views import logout as mezzanine_logout
from mezzanine.core.views import direct_to_template
from mezzanine.conf import settings

import seek.urls
import api_app.urls
import nextseek_api.urls

from . import views


admin.autodiscover()


def _not_found(request, *args, **kwargs):
    raise Http404()

urlpatterns = i18n_patterns(
    re_path(r'^login/?$', views.login_seek, name="login_seek"),
    re_path(r'^signup/', views.signup_seek, name="signup_seek"),
    
    re_path("^admin/", include(admin.site.urls)),
    re_path("^seek/", include(seek.urls)),
    # re_path("^api/", include(api_app.urls)),
    re_path("^nextseek_api/", include(nextseek_api.urls)),
)

# MEDIA_ROOT behind a login, and only the tree the application links to (dmac/media.py
# says which, and why the rest is private). nginx has no /media location and DEBUG is
# off in the docker deployment, so Django serves it with a view of its own. Kept outside
# i18n_patterns so /media/... resolves without a language prefix, and placed before the
# mezzanine catch-all ("^") below so it isn't swallowed into a 404.
from .media import serve_media
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve_media),
]

if settings.USE_MODELTRANSLATION:
    urlpatterns += [
        re_path('^i18n/$', set_language, name='set_language'),
    ]

urlpatterns += [
    re_path("^$", views.home, name="home"),
    # Must precede the mezzanine catch-all below: mezzanine.urls includes its own
    # ^accounts/signup/ view, and "^" matches everything, so a signup route placed
    # after it is unreachable and users get Mezzanine's local signup form instead
    # of being handed off to SEEK. Registered last among the signup_seek patterns
    # so {% url "signup_seek" %} reverses to this one.
    re_path(r'^accounts/signup/$', views.signup_seek, name="signup_seek"),
    # Accounts are SEEK's. Of Mezzanine's account pages only sign-out is used (the
    # user menu reverses its name, "logout"); /accounts/login/ is the SEEK login.
    re_path(r'^accounts/login/$', views.login_seek),
    re_path(r'^accounts/logout/$', mezzanine_logout, name="logout"),
    # Mezzanine's public pages are not part of NExtSEEK: its blog, site search,
    # account forms and local password reset answer 404. Shadowed rather than
    # dropped from the include, so the names Mezzanine's admin templates reverse
    # still resolve. Slashed paths only, so /accounts/login (no slash) still gets
    # APPEND_SLASH's redirect to the route above.
    re_path(r'^(?:blog|search|accounts|password_reset|reset)(?:/.*)?/$', _not_found),
    re_path("^", include("mezzanine.urls")),
]

handler404 = "mezzanine.core.views.page_not_found"
handler500 = "mezzanine.core.views.server_error"
