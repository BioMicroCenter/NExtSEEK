"""Test settings for lane M: the MySQL concurrency lane (PLAN-00-INDEX.md "Test lanes", ``lane_mysql``).

Everything from dmac.test_settings, but the default database is a real MySQL 8.0 in a throwaway container the lane
starts on an internal network of its own; never the stack's database and never a box's. Tests marked ``mysql_lane``
run only under this module (NessieAI/tests/ns/conftest.py skips them anywhere else): they race threads, each on its
own connection, where SQLite's single writer would hide the race. The ``seek`` alias stays SQLite.
"""
import os

from dmac.test_settings import *  # noqa: F401, F403
from dmac.test_settings import DATABASES as _SQLITE_DATABASES

DATABASES = {
    **_SQLITE_DATABASES,
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "nextseek_lane",
        "USER": "root",
        "PASSWORD": os.environ.get("LANE_MYSQL_PASSWORD", "lane"),
        "HOST": os.environ.get("LANE_MYSQL_HOST", "127.0.0.1"),
        "PORT": "3306",
        "TEST": {"NAME": "test_nextseek_lane"},
    },
}
