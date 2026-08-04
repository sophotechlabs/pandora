from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper

from pandora.events.types import EVENTS_TABLE, Event

INSERT = (
    f"INSERT OR IGNORE INTO {EVENTS_TABLE} "
    '(id, project_id, issue_id, episode_id, fingerprint, "timestamp", '
    "level, message, tags, extra, source, environment) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

SELECT = (
    'SELECT id, project_id, "timestamp", level, message, issue_id, episode_id, '
    f"fingerprint, tags, extra, source, environment FROM {EVENTS_TABLE}"
)

DELETE = f'DELETE FROM {EVENTS_TABLE} WHERE "timestamp" < %s'


def _encode_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _decode_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _to_row(event: Event) -> list[Any]:
    return [
        event.id,
        event.project_id,
        event.issue_id,
        event.episode_id,
        json.dumps(event.fingerprint),
        _encode_timestamp(event.timestamp),
        event.level,
        event.message,
        json.dumps(event.tags),
        json.dumps(event.extra),
        event.source,
        event.environment,
    ]


def _to_event(row: Mapping[str, Any]) -> Event:
    return Event(
        id=row["id"],
        project_id=row["project_id"],
        timestamp=_decode_timestamp(row["timestamp"]),
        level=row["level"],
        message=row["message"],
        issue_id=row["issue_id"],
        episode_id=row["episode_id"],
        fingerprint=json.loads(row["fingerprint"]),
        tags=json.loads(row["tags"]),
        extra=json.loads(row["extra"]),
        source=row["source"],
        environment=row["environment"],
    )


def _fetched(cursor: CursorWrapper) -> list[Event]:
    columns = [column[0] for column in cursor.description]
    return [
        _to_event(dict(zip(columns, row, strict=True))) for row in cursor.fetchall()
    ]


class SqliteEventStore:
    table = EVENTS_TABLE

    def __init__(self, connection: BaseDatabaseWrapper) -> None:
        self.connection = connection

    def insert(self, events: Sequence[Event]) -> None:
        if not events:
            return
        with self.connection.cursor() as cursor:
            cursor.executemany(INSERT, [_to_row(event) for event in events])

    def fetch(
        self,
        project_id: int,
        *,
        issue_id: int | None = None,
        episode_id: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        clauses = ["project_id = %s"]
        params: list[Any] = [project_id]
        if issue_id is not None:
            clauses.append("issue_id = %s")
            params.append(issue_id)
        if episode_id is not None:
            clauses.append("episode_id = %s")
            params.append(episode_id)
        if before is not None:
            clauses.append("id < %s")
            params.append(before)
        params.append(limit)
        query = f"{SELECT} WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT %s"
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return _fetched(cursor)

    def search(
        self,
        project_id: int,
        tags: Mapping[str, str],
        since: datetime,
        until: datetime,
        limit: int = 100,
    ) -> list[Event]:
        clauses = ["project_id = %s", '"timestamp" >= %s', '"timestamp" < %s']
        params: list[Any] = [
            project_id,
            _encode_timestamp(since),
            _encode_timestamp(until),
        ]
        for key, value in sorted(tags.items()):
            clauses.append("tags ->> %s = %s")
            params.append(key)
            params.append(value)
        params.append(limit)
        query = (
            f"{SELECT} WHERE {' AND '.join(clauses)} "
            'ORDER BY "timestamp" DESC, id DESC LIMIT %s'
        )
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return _fetched(cursor)

    def prune(self, before: datetime) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(DELETE, [_encode_timestamp(before)])
            return cursor.rowcount

    def ensure_partitions(self, months_ahead: int = 2) -> None:
        return None
