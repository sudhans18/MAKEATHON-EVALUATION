import os
import sqlite3
from contextlib import contextmanager
from queue import Empty, LifoQueue
from threading import Lock
from flask import current_app, g


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class SQLiteConnectionPool:
    def __init__(self, db_path, max_size):
        self._db_path = db_path
        self._max_size = max_size
        self._queue = LifoQueue(maxsize=max_size)
        self._created = 0
        self._lock = Lock()

    def acquire(self):
        try:
            return self._queue.get_nowait()
        except Empty:
            with self._lock:
                if self._created < self._max_size:
                    self._created += 1
                    return _create_connection(self._db_path)
            return self._queue.get(timeout=5)

    def release(self, conn):
        try:
            self._queue.put_nowait(conn)
        except Exception:
            conn.close()


def _create_connection(db_path):
    conn = sqlite3.connect(
        db_path,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = FULL;")
    conn.execute("PRAGMA wal_autocheckpoint = 1000;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn


def init_connection_pool(app):
    db_path = app.config["DATABASE_PATH"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    max_pool_size = int(app.config.get("DB_POOL_SIZE", 12))
    app.extensions["sqlite_pool"] = SQLiteConnectionPool(db_path, max_pool_size)


def get_db():
    if "db" not in g:
        pool = current_app.extensions.get("sqlite_pool")
        if pool is not None:
            g.db = pool.acquire()
            g.db_from_pool = True
        else:
            db_path = current_app.config["DATABASE_PATH"]
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            g.db = _create_connection(db_path)
            g.db_from_pool = False
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        if g.pop("db_from_pool", False):
            pool = current_app.extensions.get("sqlite_pool")
            if pool is not None:
                pool.release(db)
                return
        db.close()


@contextmanager
def transaction():
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE;")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def init_db():
    db = get_db()
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as schema_file:
        db.executescript(schema_file.read())
    db.commit()
