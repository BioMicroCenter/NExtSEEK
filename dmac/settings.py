
from __future__ import absolute_import, unicode_literals
import os

from django import VERSION as DJANGO_VERSION
from django.utils.translation import gettext_lazy as _


########################
# MAIN DJANGO SETTINGS #
########################

DEBUG = os.getenv("DJANGO_DEBUG", False)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

SESSION_COOKIE_DOMAIN = os.getenv('DJANGO_SESSION_COOKIE_DOMAIN', None)

ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', '').split(' ')

CSRF_TRUSTED_ORIGINS = os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(' ')

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("NEXTSEEK_MYSQL_DATABASE"),
        "USER": os.getenv("MYSQL_USER"),
        "PASSWORD": os.getenv("MYSQL_PASSWORD"),
        "HOST": os.getenv("MYSQL_HOST"),
        "PORT": "3306",
    },

    "seek": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("MYSQL_DATABASE"),
        "USER": os.getenv("MYSQL_USER"),
        "PASSWORD": os.getenv("MYSQL_PASSWORD"),
        "HOST": os.getenv("MYSQL_HOST"),
        "PORT": "3306",
    }
}

TIME_ZONE = 'UTC'
USE_TZ = True
LANGUAGE_CODE = "en"
LANGUAGES = (
    ('en', _('English')),
)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SITE_ID = 1
USE_I18N = False

FILE_CHARSET = "utf-8"
AUTHENTICATION_BACKENDS = ("mezzanine.core.auth_backends.MezzanineBackend",)

FILE_UPLOAD_PERMISSIONS = 0o644

#########
# PATHS #
#########

# Full filesystem path to the project.
PROJECT_APP_PATH = os.path.dirname(os.path.abspath(__file__))
PROJECT_APP = os.path.basename(PROJECT_APP_PATH)
PROJECT_ROOT = BASE_DIR = os.path.dirname(PROJECT_APP_PATH)

# Every cache key will get prefixed with this value - here we set it to
# the name of the directory the project is in to try and use something
# project specific.
CACHE_MIDDLEWARE_KEY_PREFIX = PROJECT_APP

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/home/media/media.lawrence.com/static/"
STATIC_ROOT = "/static"

# URL prefix for static files.
# Example: "http://media.lawrence.com/static/"
STATIC_URL = "/static/"


STATICFILES_DIRS = [
    "/app/themes/NextSeek/static",
    "/app/static"
]

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/home/media/media.lawrence.com/media/"
MEDIA_ROOT = "/media"

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash.
# Examples: "http://media.lawrence.com/media/", "http://example.com/media/"
MEDIA_URL = "/media/"

# Package/module name to import the root urlpatterns from for the project.
ROOT_URLCONF = "%s.urls" % PROJECT_APP

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(PROJECT_ROOT, "themes", "NextSeek", "templates"),
        ],
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.debug",
                "django.template.context_processors.i18n",
                "django.template.context_processors.static",
                "django.template.context_processors.media",
                "django.template.context_processors.request",
                "django.template.context_processors.tz",
                "mezzanine.conf.context_processors.settings",
                "mezzanine.pages.context_processors.page",
            ],
            "loaders": [
                "mezzanine.template.loaders.host_themes.Loader",
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        },
    },
]

if DJANGO_VERSION < (1, 9):
    del TEMPLATES[0]["OPTIONS"]["builtins"]

################
# APPLICATIONS #
################

INSTALLED_APPS = (
    "seek",
    "themes.NextSeek",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.redirects",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.sitemaps",
    "django.contrib.staticfiles",
    "markdownify.apps.MarkdownifyConfig",
    "mezzanine.boot",
    "mezzanine.conf",
    "mezzanine.core",
    "mezzanine.generic",
    "mezzanine.pages",
    "mezzanine.blog",
    "mezzanine.forms",
    "mezzanine.galleries",
    #"mezzanine.twitter",
    "mezzanine.accounts",
    "widget_tweaks",

    "django_crontab",
    "rest_framework",
    "rest_framework.authtoken",
    "dj_rest_auth",
    "api_app",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "corsheaders",
    "channels",
    "nextseek_api",
)

# Django Channels (ASGI)
ASGI_APPLICATION = "dmac.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# List of middleware classes to use. Order is important; in the request phase,
# these middleware classes will be applied in the order given, and in the
# response phase the middleware will be applied in reverse order.
MIDDLEWARE = (
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "mezzanine.core.middleware.UpdateCacheMiddleware",

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    "mezzanine.core.request.CurrentRequestMiddleware",
    "mezzanine.core.middleware.RedirectFallbackMiddleware",
    "mezzanine.core.middleware.AdminLoginInterfaceSelectorMiddleware",
    "mezzanine.core.middleware.SitePermissionMiddleware",
    "mezzanine.pages.middleware.PageMiddleware",
    "mezzanine.core.middleware.FetchFromCacheMiddleware",
    "django_cprofile_middleware.middleware.ProfilerMiddleware",
)

DJANGO_CPROFILE_MIDDLEWARE_REQUIRE_STAFF = False

# Store these package names here as they may change in the future since
# at the moment we are using custom forks of them.
PACKAGE_NAME_FILEBROWSER = "filebrowser_safe"
PACKAGE_NAME_GRAPPELLI = "grappelli_safe"
FILEBROWSER_EXTENSIONS = {
        "Document": [".pdf", ".doc", ".rtf", ".txt", ".xls", ".xlsx", ".csv", ".docx"],
}

#########################
# OPTIONAL APPLICATIONS #
#########################

# These will be added to ``INSTALLED_APPS``, only if available.
OPTIONAL_APPS = (
    "debug_toolbar",
    "django_extensions",
    "compressor",
    PACKAGE_NAME_FILEBROWSER,
    PACKAGE_NAME_GRAPPELLI,
)

##################
# LOCAL SETTINGS #
##################

# Allow any settings to be defined in local_settings.py which should be
# ignored in your version control system allowing for settings to be
# defined per machine.

# Instead of doing "from .local_settings import *", we use exec so that
# local_settings has full access to everything defined in this module.
# Also force into sys.modules so it's visible to Django's autoreload.

f = os.path.join(PROJECT_APP_PATH, "local_settings.py")
if os.path.exists(f):
    if os.path.exists(f):
        with open(f) as cf:
            exec(cf.read(), globals())

####################
# DYNAMIC SETTINGS #
####################

# set_dynamic_settings() will rewrite globals based on what has been
# defined so far, in order to provide some better defaults where
# applicable. We also allow this settings module to be imported
# without Mezzanine installed, as the case may be when using the
# fabfile, where setting the dynamic settings below isn't strictly
# required.
try:
    from mezzanine.utils.conf import set_dynamic_settings
except ImportError:
    pass
else:
    set_dynamic_settings(globals())

APPEND_SLASH = True

# refer to: https://bitbucket.org/stephenmcd/mezzanine/commits/ffb536fe0d1f15f9a77a59c8c91bc5845cadc8ca
# If you set this to True, it will send new user an email with a verification link that they must click on, 
# in order to activate their account. In INSTALLED_APPS above, "mezzanine.accounts" must be active to make this work.
ACCOUNTS_VERIFICATION_REQUIRED = True
# Defaults to False and when set to True, sets newly created public user accounts to inactivate,
# requiring activation by a staff member.
ACCOUNTS_APPROVAL_REQUIRED = True

DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"

STORAGES = {
	"default": {
		"BACKEND": "django.core.files.storage.FileSystemStorage",
	},
	"staticfiles": {
		# Reverted from ManifestStaticFilesStorage on 2026-05-12 because the
		# vendored jquery-easyui-1.5.2/ tree isn't reachable by collectstatic
		# in this layout, and the manifest backend raises ValueError on any
		# {% static %} reference missing from the manifest. See v2 spec
		# section 2.1 — proper fix is to either ship a curated STATICFILES_DIRS
		# that includes easyui, or use a manifest_strict=False subclass.
		"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
	},
}

DATABASE_ROUTERS = ['seek.dbrouters.CustomRouter']

ACCOUNTS_PROFILE_MODEL = "seek.User_profile"

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': "%(asctime)s %(levelname)s %(message)s",
            'datefmt': "%a, %d %b %Y %H:%M:%S"
        },
    },
    'handlers': {
        'django_crontab': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'django_crontab.log',
            'formatter': 'verbose'
        },
        'logfile': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'django.log',
            'formatter': 'verbose' 
        },
        'seekfile': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'seek.log',
            'formatter': 'verbose'
        },
        'nextseekfile': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'nextseek.log',
            'formatter': 'verbose'
        },
    },
    'loggers': {
        'django_crontab.crontab': {
            'handlers': ['django_crontab'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'django': {
            'handlers':['logfile'],
            'propagate': True,
            'level':'DEBUG',
        },
        'django.utils.autoreload': {
          'level': 'INFO'  
        },
        'seek': {
            'handlers':['seekfile'],
            'propagate': True,
            'level':'DEBUG', 
        },
        'dmac': {
            'handlers':['nextseekfile'],
            'propagate': True,
            'level':'DEBUG',
        },
    },
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}


##############################
# NExtSEEK-specific settings #
##############################

SPECTACULAR_SETTINGS = {
    "TITLE": "NExtSEEK API",
    "VERSION": "0.1.0",
    "OAS_VERSION": "3.1.0",  # drf-spectacular supports 3.0 & 3.1
    "PREPROCESSING_HOOKS": [
        "dmac.openapi_hooks.exclude_seek_paths",
    ],
}

####################
# CORS SETTINGS    #
####################
# Allow the React/Vite frontend dev server to make cross-origin requests
# to nextseek_api endpoints. Authentication (Token/Basic/Session) is still
# enforced — CORS only governs browser same-origin policy.

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
]

CORS_ALLOW_CREDENTIALS = True

CORS_URLS_REGEX = r"^/nextseek_api/.*$"

CORS_EXPOSE_HEADERS = [
    "Content-Type",
    "X-Request-Id",
    "Cache-Control",
    "X-Accel-Buffering",
]

#######################
# SCHEMA RAG SETTINGS #
#######################

SCHEMA_RAG_DUCKDB_DIR = os.path.join(BASE_DIR, 'schema_rag', 'duckdb')
SCHEMA_RAG_DEFAULT_TTL_MINUTES = 15
SCHEMA_RAG_MAX_ENDPOINTS = 250
SCHEMA_RAG_MAX_TOP_K = 10
SCHEMA_RAG_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
SCHEMA_RAG_EMBEDDING_MODEL_PATH = os.path.join(BASE_DIR, 'schema_rag', 'embedding_models')
SCHEMA_RAG_EXCLUDED_PATH_PATTERNS = [
    "/schema_rag/",
]

# Ensure Schema RAG directories exist
os.makedirs(SCHEMA_RAG_DUCKDB_DIR, exist_ok=True)
os.makedirs(SCHEMA_RAG_EMBEDDING_MODEL_PATH, exist_ok=True)

############################
# BATCH UPLOAD SETTINGS    #
############################

# Maximum total size (in bytes) for all files in a single batch upload request.
# Default: 200 MB. Override via environment variable.
BATCH_UPLOAD_MAX_TOTAL_BYTES = int(os.getenv("BATCH_UPLOAD_MAX_TOTAL_BYTES", 200 * 1024 * 1024))

NEO4J_DATABASE = {
    "NAME": "neo4j",
    "URI": "neo4j://" + os.getenv("NEXTSEEK_NEO4J_HOST"),
    "AUTH": ("neo4j",os.getenv("NEXTSEEK_NEO4J_PASSWORD"))
}

NEXTSEEK_DATABASE = "default"
SEEK_DATABASE = "seek"

SERVER_IPADDRESS = os.getenv("NEXTSEEK_HOSTNAME")

SEEK_HOSTNAME = os.getenv("SEEK_HOSTNAME")
SEEK_SERVER = os.getenv("SEEK_HOST")
SEEK_URL = "http://" + SEEK_SERVER + ":3000"
SEEK_JS_URL = SEEK_SERVER

VIRTUOSO_URL = "http://" + SEEK_SERVER + ":8890/sparql/"
VIRTUOSO_JS_URL = "http://" + SEEK_SERVER + ":8890/sparql"

SEEK_DATAFILE_SERVER = 'https://' + SERVER_IPADDRESS
SEEK_DATAFILE_ROOT = MEDIA_ROOT + "/uploads/production/"
SEEK_DATAFILE_ROOT_WEBLINK = MEDIA_URL + "uploads/production/"

ACCOUNTS_PROFILE_MODEL = "seek.User_profile"
