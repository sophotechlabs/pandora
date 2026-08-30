from __future__ import annotations

import dataclasses
import gzip
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings

from pandora.events.store import EventStore, get_store
from pandora.events.types import Event

HOUR = timedelta(hours=1)
PAGE = 500
log = logging.getLogger(__name__)


@dataclass
class Written:
    path: str
    events: int
    bytes: int


@dataclass
class Report:
    files: list[Written]

    @property
    def events(self) -> int:
        return sum(row.events for row in self.files)

    def lines(self) -> list[str]:
        return [
            f"{row.path}: {row.events} event(s), {row.bytes} bytes"
            for row in self.files
        ]


def root() -> Path | None:
    configured = settings.PANDORA_ARCHIVE_DIR
    if not configured:
        return None
    return Path(configured)


def key_for(project_id: int, hour: datetime) -> str:
    """Hive-style hourly paths, which is what every query engine already reads.

    A directory per project and hour means a restore, a re-index or a `duckdb`
    query over last March costs a path prefix rather than a full scan.
    """
    return (
        f"project={project_id}/year={hour:%Y}/month={hour:%m}"
        f"/day={hour:%d}/hour={hour:%H}/events.jsonl.gz"
    )


def export(
    project_id: int,
    since: datetime,
    until: datetime,
    store: EventStore | None = None,
    destination: Path | None = None,
) -> Report:
    if store is None:
        store = get_store()
    if destination is None:
        destination = root()
    if destination is None:
        return Report(files=[])

    files = []
    hour = _floor(since)
    while hour < until:
        rows = list(_events(store, project_id, hour, hour + HOUR))
        if rows:
            files.append(_write(destination, project_id, hour, rows))
        hour = hour + HOUR
    return Report(files=files)


def _events(
    store: EventStore, project_id: int, since: datetime, until: datetime
) -> Iterator[Event]:
    cursor = None
    while True:
        page = store.fetch(project_id, before=cursor, limit=PAGE)
        if not page:
            return
        for event in page:
            if since <= event.timestamp < until:
                yield event
        cursor = page[-1].id
        if len(page) < PAGE:
            return


def _write(
    destination: Path, project_id: int, hour: datetime, rows: list[Event]
) -> Written:
    path = destination / key_for(project_id, hour)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(_row(event), default=str) for event in rows)
    payload = gzip.compress(body.encode())
    path.write_bytes(payload)
    return Written(path=str(path), events=len(rows), bytes=len(payload))


def _row(event: Event) -> dict:
    row = dataclasses.asdict(event)
    row["timestamp"] = event.timestamp.isoformat()
    return row


def _floor(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
