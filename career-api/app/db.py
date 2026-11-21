import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row


def dsn():
    return dict(host=os.getenv('DATABASE_HOST', 'career-db'), port=int(os.getenv('DATABASE_PORT', '5432')),
                dbname=os.getenv('DATABASE_NAME', 'bwg_career'), user=os.getenv('DATABASE_USER', 'bwg_app'),
                password=os.environ['DATABASE_PASSWORD'])


@contextmanager
def connect():
    with psycopg.connect(**dsn(), row_factory=dict_row, connect_timeout=5) as cn:
        yield cn
