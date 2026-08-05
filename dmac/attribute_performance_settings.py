import os

from dmac.test_settings import *  # noqa: F403


def _required(name):
    value = os.environ.get(name)
    if not value or value == ":memory:":
        raise RuntimeError(f"persistent benchmark database setting required: {name}")
    return value


def _mariadb(prefix, name_key):
    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": _required(name_key),
        "HOST": _required(prefix + "_HOST"),
        "PORT": _required(prefix + "_PORT"),
        "USER": _required(prefix + "_USER"),
        "PASSWORD": _required(prefix + "_PASSWORD"),
        "OPTIONS": {"charset": "utf8mb4"},
        "CONN_MAX_AGE": 0,
        "TEST": {"MIRROR": None},
    }


DATABASES = {
    "default": _mariadb("ATTRIBUTE_DEFAULT_DB", "ATTRIBUTE_DEFAULT_DATABASE_NAME"),
    "seek": _mariadb("ATTRIBUTE_TEST_DB", "ATTRIBUTE_TEST_DATABASE_NAME"),
}
SEEK_DATABASE = "seek"
DATABASE_ROUTERS = ["seek.dbrouters.SeekRouter"]

# Registers Celery worker-process/task hooks before the app imports task modules.
if os.environ.get("ATTRIBUTE_WORKER_TELEMETRY_RESULTS"):
    from nextseek_api.attributes.tests import performance_worker_telemetry  # noqa: F401,E402
