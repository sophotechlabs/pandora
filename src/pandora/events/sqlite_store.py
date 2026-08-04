from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from django.db.backends.base.base import BaseDatabaseWrapper

from pandora.events.types import EVENTS_TABLE, Event


class SqliteEventStore:
    table = EVENTS_TABLE

    def __init__(self, connection: BaseDatabaseWrapper) -> None:
        self.connection = connection

    def insert(self, events: Sequence[Event]) -> None:
        raise NotImplementedError

    def fetch(
        self,
        project_id: int,
        *,
        issue_id: int | None = None,
        episode_id: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        raise NotImplementedError

    def search(
        self,
        project_id: int,
        tags: Mapping[str, str],
        since: datetime,
        until: datetime,
        limit: int = 100,
    ) -> list[Event]:
        raise NotImplementedError

    def prune(self, before: datetime) -> int:
        raise NotImplementedError

    def ensure_partitions(self, months_ahead: int = 2) -> None:
        return None
