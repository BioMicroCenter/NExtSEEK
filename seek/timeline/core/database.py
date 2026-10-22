# core/database.py
import logging
from functools import lru_cache

from django.conf import settings
from mysql.connector import pooling, Error

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _get_pool():
    """The one connection pool, built on first use (every query names its schema, so the pool's default
    database is only the app's own). Small on purpose: the pool opens every connection at once, and each
    of the workers builds its own."""
    db = settings.DATABASES[settings.SEEK_DATABASE]
    return pooling.MySQLConnectionPool(
        user=db['USER'],
        password=db['PASSWORD'],
        host=db['HOST'],
        database=settings.DATABASES['default']['NAME'],
        pool_size=int(db.get('POOL_SIZE', 3)),
        pool_name='timeline_pool',
        pool_reset_session=True,
    )


def get_db_connection():
    try:
        return _get_pool().get_connection()
    except Error as e:
        logger.error("Error while connecting to MySQL using Connection Pool: %s", e)
        raise


def execute_query(query, params):
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params)
        return cursor.fetchall()
    except Error as e:
        logger.error("Database error: %s", e)
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
