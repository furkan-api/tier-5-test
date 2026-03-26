from __future__ import annotations

from contextlib import contextmanager

import psycopg2

from app.core.config import get_settings


@contextmanager
def get_connection(db_url: str | None = None):
    """Context manager for PostgreSQL connections."""
    url = db_url or get_settings().database_url
    conn = psycopg2.connect(url)
    try:
        yield conn
    finally:
        conn.close()
