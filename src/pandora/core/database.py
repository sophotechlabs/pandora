from __future__ import annotations

from pathlib import Path

from django.db import connection as default_connection
from django.db.backends.base.base import BaseDatabaseWrapper
from prometheus_client import Gauge

SQLITE_VENDOR = "sqlite"
POSTGRES_VENDOR = "postgresql"
MEMORY_NAMES = frozenset({":memory:", ""})

DATABASE_BYTES = Gauge(
    "pandora_database_bytes",
    "Size of the pandora database on disk",
    multiprocess_mode="livemostrecent",
)


def sqlite_path(connection: BaseDatabaseWrapper) -> Path | None:
    if connection.vendor != SQLITE_VENDOR:
        return None
    name = str(connection.settings_dict.get("NAME", ""))
    if name in MEMORY_NAMES:
        return None
    if name.startswith("file:"):
        return None
    return Path(name)


def size_bytes(connection: BaseDatabaseWrapper) -> int:
    if connection.vendor == POSTGRES_VENDOR:
        return _scalar(connection, "SELECT pg_database_size(current_database())")
    if connection.vendor == SQLITE_VENDOR:
        pages = _scalar(connection, "PRAGMA page_count")
        page_size = _scalar(connection, "PRAGMA page_size")
        return pages * page_size
    return 0


def refresh_size(connection: BaseDatabaseWrapper | None = None) -> int:
    if connection is None:
        connection = default_connection
    size = size_bytes(connection)
    DATABASE_BYTES.set(size)
    return size


def incremental_vacuum(connection: BaseDatabaseWrapper | None = None) -> bool:
    if connection is None:
        connection = default_connection
    if connection.vendor != SQLITE_VENDOR:
        return False
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA incremental_vacuum")
        cursor.fetchall()
    return True


def vacuum_into(connection: BaseDatabaseWrapper, target: Path) -> int:
    with connection.cursor() as cursor:
        cursor.execute("VACUUM INTO %s", [str(target)])
    return target.stat().st_size


def _scalar(connection: BaseDatabaseWrapper, sql: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return int(row[0])
