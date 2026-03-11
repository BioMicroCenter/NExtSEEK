import dmac.settings as settings

SECRET_KEY = "YOUR SECRET KEY"
NEVERCACHE_KEY = "YOUR KEY"

# Hosts/domain names that are valid for this site; required if DEBUG is False
# See https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = [u'servername']

# List of project IDs for projects that allow
# for the assistant endpoint
ASSISTANT_PARTICIPATING_PROJECTS = set(["1"])

#############
# DATABASES #
#############

DATABASES = {
    "default": {
        # Add "postgresql_psycopg2", "mysql", "sqlite3" or "oracle".
        "ENGINE": "django.db.backends.",
        # DB name or path to database file if using sqlite3.
        "NAME": "",
        # Not used with sqlite3.
        "USER": "",
        # Not used with sqlite3.
        "PASSWORD": "",
        # Set to empty string for localhost. Not used with sqlite3.
        "HOST": "",
        # Set to empty string for default. Not used with sqlite3.
        "PORT": "",
    },

    "seek": {
        # Add "postgresql_psycopg2", "mysql", "sqlite3" or "oracle".
        "ENGINE": "django.db.backends.",
        # DB name or path to database file if using sqlite3.
        "NAME": "",
        # Not used with sqlite3.
        "USER": "",
        # Not used with sqlite3.
        "PASSWORD": "",
        # Set to empty string for localhost. Not used with sqlite3.
        "HOST": "",
        # Set to empty string for default. Not used with sqlite3.
        "PORT": "",
    }
}

NEO4J_DATABASE = {
    "NAME": "",
    "URI": "neo4j://IPADDRESS",
    "AUTH": ("USERNAME", "PASSWORD"),
}

STATICFILES_DIRS = [
    "/path/to/nextseek/themes/SmartAdmin/static",
    "/path/to/nextseek/static",
]

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/home/media/media.lawrence.com/static/"
STATIC_ROOT = "/static"

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/home/media/media.lawrence.com/media/"
MEDIA_ROOT = "/app/media"

# Can contain a comma separated string of email addresses to send notification emails to
# each time a new account is created and requires activation. 
ACCOUNTS_APPROVAL_EMAILS = 'your email address'

SERVER_IPADDRESS = 'your IP address'

ALLOWED_HOSTS = ['*']
SESSION_COOKIE_DOMAIN = 'your IP address'
CSRF_TRUSTED_ORIGINS = ['server domain']

EMAIL_USE_TLS = True
EMAIL_HOST = 'your smtp server'
EMAIL_HOST_USER = 'your host email address'
EMAIL_HOST_PASSWORD = 'your host password'
EMAIL_PORT = 587
DEFAULT_FROM_EMAIL = SERVER_EMAIL = 'your email address'

NEXTSEEK_DATABASE = "default"
SEEK_HOSTNAME = "example.com"
SEEK_URL = "https://" + SEEK_HOSTNAME
SEEK_DATABASE = "seek"
SEEK_SERVER = SEEK_URL
SEEK_JS_URL = SEEK_URL

VIRTUOSO_URL = "http://" + SEEK_HOSTNAME + ":8890/sparql/"
VIRTUOSO_JS_URL = "http://" + SEEK_HOSTNAME + ":8890/sparql"

SEEK_DATAFILE_SERVER = 'http://' + SEEK_HOSTNAME + ':portNumber'
SEEK_DATAFILE_ROOT = MEDIA_ROOT + "/uploads/"
SEEK_DATAFILE_ROOT_WEBLINK = settings.MEDIA_URL + "uploads/"

SAMPLE_TEMPLATES_FOLDER = ''
SAMPLE_TEMPLATES_FOLDER_PROJECT = ''

PUBLISH_URL = "https://fairdomhub.org"
PUBLISH_STATS_FILE = "/path/to/excel/file"

SPECTACULAR_SETTINGS = {
    'TITLE': 'Your Project API',
    'DESCRIPTION': 'Your project description',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SWAGGER_UI_DIST': 'SIDECAR',
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',
}

SMART_SEACH_URL = ""
