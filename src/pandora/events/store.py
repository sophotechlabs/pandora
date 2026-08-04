from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from django.core.exceptions import ImproperlyConfigured
from django.db import connection as default_connection
from django.db.backends.base.base import BaseDatabaseWrapper

from pandora.events.postgres_store import PostgresEventStore
from pandora.events.sqlite_store import SqliteEventStore
from pandora.events.types import Event


class EventStore(Protocol):
    def insert(self, events: Sequence[Event]) -> None: ...

    def reassign(
        self, project_id: int, episode_ids: Sequence[str], issue_id: int
    ) -> int: ...

    def fetch(
        self,
        project_id: int,
        *,
        issue_id: int | None = None,
        episode_id: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[Event]: ...

    def search(
        self,
        project_id: int,
        tags: Mapping[str, str],
        since: datetime,
        until: datetime,
        limit: int = 100,
    ) -> list[Event]: ...

    def prune(self, before: datetime) -> int: ...

    def ensure_partitions(self, months_ahead: int = 2) -> None: ...


def get_store(connection: BaseDatabaseWrapper | None = None) -> EventStore:
    if connection is None:
        connection = default_connection
    if connection.vendor == "postgresql":
        return PostgresEventStore(connection)
    if connection.vendor == "sqlite":
        return SqliteEventStore(connection)
    raise ImproperlyConfigured(
        f"no EventStore implementation for database vendor {connection.vendor!r}"
    )
