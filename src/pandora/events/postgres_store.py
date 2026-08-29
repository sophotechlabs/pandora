from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from django.conf import settings
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from django.utils import timezone

from pandora.events.types import EVENTS_TABLE, Event

logger = logging.getLogger(__name__)

INSERT = (
    f"INSERT INTO {EVENTS_TABLE} "
    '(id, project_id, issue_id, episode_id, fingerprint, "timestamp", '
    "level, message, tags, extra, source, environment, payload) "
    "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb) "
    "ON CONFLICT DO NOTHING"
)

SELECT = (
    'SELECT id, project_id, "timestamp", level, message, issue_id, episode_id, '
    "fingerprint::text AS fingerprint, tags::text AS tags, extra::text AS extra, "
    f"source, environment, payload::text AS payload FROM {EVENTS_TABLE}"
)

REASSIGN = (
    f"UPDATE {EVENTS_TABLE} SET issue_id = %s "
    "WHERE project_id = %s AND {column} IN ({placeholders})"
)

REASSIGN_CHUNK = 500

PARTITIONS = (
    "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) "
    "FROM pg_class c "
    "JOIN pg_inherits i ON i.inhrelid = c.oid "
    "WHERE i.inhparent = %s::regclass "
    "ORDER BY c.relname"
)

PARTITION = (
    f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE}_{{suffix}} "
    f"PARTITION OF {EVENTS_TABLE} FOR VALUES FROM ('{{start}}') TO ('{{end}}')"
)

UPPER_BOUND = re.compile(r"TO \('([^']+)'\)")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _month_start(day: date, offset: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _upper_bound(expression: str) -> datetime | None:
    match = UPPER_BOUND.search(expression)
    if match is None:
        return None
    return datetime.fromisoformat(match.group(1))


def _to_row(event: Event) -> list[Any]:
    return [
        event.id,
        event.project_id,
        event.issue_id,
        event.episode_id,
        json.dumps(event.fingerprint),
        _aware(event.timestamp),
        event.level,
        event.message,
        json.dumps(event.tags),
        json.dumps(event.extra),
        event.source,
        event.environment,
        json.dumps(event.payload),
    ]


def _to_event(row: Mapping[str, Any]) -> Event:
    return Event(
        id=row["id"],
        project_id=row["project_id"],
        timestamp=row["timestamp"],
        level=row["level"],
        message=row["message"],
        issue_id=row["issue_id"],
        episode_id=row["episode_id"],
        fingerprint=json.loads(row["fingerprint"]),
        tags=json.loads(row["tags"]),
        extra=json.loads(row["extra"]),
        source=row["source"],
        environment=row["environment"],
        payload=json.loads(row["payload"]),
    )


def _fetched(cursor: CursorWrapper) -> list[Event]:
    columns = [column[0] for column in cursor.description]
    return [
        _to_event(dict(zip(columns, row, strict=True))) for row in cursor.fetchall()
    ]


class PostgresEventStore:
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
        params: list[Any] = [project_id, _aware(since), _aware(until)]
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
        cutoff = _aware(before)
        removed = 0
        dropped = []
        with self.connection.cursor() as cursor:
            cursor.execute(PARTITIONS, [self.table])
            for name, expression in cursor.fetchall():
                upper = _upper_bound(expression)
                if upper is None:
                    continue
                if upper > cutoff:
                    continue
                quoted = self.connection.ops.quote_name(name)
                cursor.execute(f"SELECT count(*) FROM {quoted}")
                removed += cursor.fetchone()[0]
                cursor.execute(f"DROP TABLE {quoted}")
                dropped.append(name)
        if dropped:
            logger.info(
                "events prune: dropped %s (%s rows) older than %s",
                ", ".join(dropped),
                removed,
                cutoff.isoformat(),
            )
        return removed

    def ensure_partitions(self, months_ahead: int = 2) -> None:
        today = timezone.now().date()
        first = _month_start(today - timedelta(days=settings.PANDORA_RETENTION_DAYS))
        last = _month_start(today, months_ahead)
        start = first
        with self.connection.cursor() as cursor:
            while start <= last:
                end = _month_start(start, 1)
                cursor.execute(
                    PARTITION.format(
                        suffix=f"{start.year}_{start.month:02d}",
                        start=start.isoformat(),
                        end=end.isoformat(),
                    )
                )
                start = end
        logger.info(
            "events partitions ensured from %s through %s",
            first.isoformat(),
            last.isoformat(),
        )
