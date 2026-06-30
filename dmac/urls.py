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

if settings.USE_MODELTRANSLATION:
    urlpatterns += [
        re_path('^i18n/$', set_language, name='set_language'),
    ]

urlpatterns += [
    re_path("^$", views.home, name="home"),
    re_path("^", include("mezzanine.urls")),
    re_path(r'^accounts/login/', views.login_seek, name="login_seek"),
    re_path(r'^accounts/signup/', views.signup_seek, name="signup_seek"),
    
]

handler404 = "mezzanine.core.views.page_not_found"
handler500 = "mezzanine.core.views.server_error"
