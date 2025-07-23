# core/database.py
import os
from mysql.connector import pooling, Error
from django.conf import settings
import logging

db = settings.DATABASES['seek']

db_config = {
    'user': db['USER'],
    'password': db['PASSWORD'],
    'host': db['HOST'],
    'database': db['NAME'],
    'pool_size': int(db.get('POOL_SIZE', 10)), # Default pool size is 5
    'pool_name': 'timeline_pool',
    'pool_reset_session': True,
}

def get_db_connection(database_name='DB_NAME'): # change to DB_NAME1 for dmac database 
    try:
        db_config['database'] = os.getenv(database_name)
        connection_pool = pooling.MySQLConnectionPool(**db_config)
        return connection_pool.get_connection()
    except Error as e:
        print(f"Error while connecting to MySQL using Connection Pool: {e}")
        raise

def execute_query(query, params, database_name='DB_NAME'):
    try:
        connection = get_db_connection(database_name)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params)
        result = cursor.fetchall()
        return result
    except Error as e:
        # print(f"Database error: {e}")
        logging.error(f"Database error: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
