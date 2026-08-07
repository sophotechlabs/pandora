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

DELETE = (
    f"DELETE FROM {EVENTS_TABLE} WHERE id IN "
    f'(SELECT id FROM {EVENTS_TABLE} WHERE "timestamp" < %s LIMIT %s)'
)
PRUNE_BATCH = 5000

REASSIGN = (
    f"UPDATE {EVENTS_TABLE} SET issue_id = %s "
    "WHERE project_id = %s AND {column} IN ({placeholders})"
)

REASSIGN_CHUNK = 500


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

    def reassign(
        self, project_id: int, episode_ids: Sequence[str], issue_id: int
    ) -> int:
        return self._relink("episode_id", project_id, episode_ids, issue_id)

    def reassign_events(
        self, project_id: int, event_ids: Sequence[str], issue_id: int
    ) -> int:
        return self._relink("id", project_id, event_ids, issue_id)

    def _relink(
        self, column: str, project_id: int, keys: Sequence[str], issue_id: int
    ) -> int:
        wanted = [str(key) for key in keys]
        if not wanted:
            return 0
        changed = 0
        with self.connection.cursor() as cursor:
            for start in range(0, len(wanted), REASSIGN_CHUNK):
                chunk = wanted[start : start + REASSIGN_CHUNK]
                placeholders = ", ".join(["%s"] * len(chunk))
                cursor.execute(
                    REASSIGN.format(column=column, placeholders=placeholders),
                    [issue_id, project_id, *chunk],
                )
                changed += cursor.rowcount
        return changed

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
        cutoff = _encode_timestamp(before)
        removed = 0
        while True:
            with self.connection.cursor() as cursor:
                cursor.execute(DELETE, [cutoff, PRUNE_BATCH])
                deleted = cursor.rowcount
            removed += deleted
            if deleted < PRUNE_BATCH:
                return removed

    def ensure_partitions(self, months_ahead: int = 2) -> None:
        return None
