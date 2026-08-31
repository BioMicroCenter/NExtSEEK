from __future__ import unicode_literals

from django.urls import re_path
from django.conf.urls import include
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.views.i18n import set_language

from mezzanine.core.views import direct_to_template
from mezzanine.conf import settings

import seek.urls
import api_app.urls
import nextseek_api.urls

from . import views


admin.autodiscover()

urlpatterns = i18n_patterns(
    re_path(r'^login', views.login_seek, name="login_seek"),
    re_path(r'^logout$', views.logout_seek, name="logout_seek"),
    re_path(r'^signup/', views.signup_seek, name="signup_seek"),
    
    re_path("^admin/", include(admin.site.urls)),
    re_path("^seek/", include(seek.urls)),
    # re_path("^api/", include(api_app.urls)),
    re_path("^nextseek_api/", include(nextseek_api.urls)),
)

# Serve generated download files (Excel exports from retrieve / delete / publish,
# written under MEDIA_ROOT/download/). DEBUG is off in the docker deployment, so
# Django's static() media helper is a no-op — use the static serve view directly.
# Kept outside i18n_patterns so /media/... resolves without a language prefix, and
# placed before the mezzanine catch-all ("^") below so it isn't swallowed into a 404.
from django.views.static import serve as _static_serve
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", _static_serve, {"document_root": settings.MEDIA_ROOT}),
]

# SEEK OAuth (#16). Deliberately OUTSIDE i18n_patterns, for the same reason as
# the media route above and one more: SEEK_OAUTH_REDIRECT_URI is registered in
# SEEK's Doorkeeper application and compared byte for byte, so a language prefix
# appearing on the callback would break every sign-in. USE_I18N is currently
# False, which makes i18n_patterns a no-op, but that is a setting rather than a
# guarantee. Placed before the mezzanine "^" catch-all below so it resolves.
# The views 404 while SEEK_OAUTH_ENABLED is off; the routes are always present.
urlpatterns += [
    re_path(r"^oauth/seek/", include("seek.oauth.urls")),
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
    re_path(r'^accounts/signup/', views.signup_seek, name="signup_seek"),
    re_path("^", include("mezzanine.urls")),
    re_path(r'^accounts/login/', views.login_seek, name="login_seek"),

]

handler404 = "mezzanine.core.views.page_not_found"
handler500 = "mezzanine.core.views.server_error"
