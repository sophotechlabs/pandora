from __future__ import annotations

import dataclasses
import gzip
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
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
    skipped: list[str] = field(default_factory=list)

    @property
    def events(self) -> int:
        return sum(row.events for row in self.files)

    def lines(self) -> list[str]:
        written = [
            f"{row.path}: {row.events} event(s), {row.bytes} bytes"
            for row in self.files
        ]
        return written + [f"{path}: already archived" for path in self.skipped]


@dataclass(frozen=True)
class _Options:
    store: EventStore | None
    destination: Path | None
    resume: bool


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
    *,
    store: EventStore | None = None,
    destination: Path | None = None,
) -> Report:
    options = _Options(store=store, destination=destination, resume=False)
    return _export(project_id, since, until, options)


def resume(
    project_id: int,
    since: datetime,
    until: datetime,
    *,
    store: EventStore | None = None,
    destination: Path | None = None,
) -> Report:
    options = _Options(store=store, destination=destination, resume=True)
    return _export(project_id, since, until, options)


def _export(
    project_id: int,
    since: datetime,
    until: datetime,
    options: _Options,
) -> Report:
    store = options.store
    if store is None:
        store = get_store()
    destination = options.destination
    if destination is None:
        destination = root()
    if destination is None:
        return Report(files=[])

    files = []
    skipped = []
    hour = _floor(since)
    while hour < until:
        path = destination / key_for(project_id, hour)
        if options.resume and path.is_file():
            skipped.append(str(path))
            hour = hour + HOUR
            continue
        rows = list(_events(store, project_id, hour, hour + HOUR))
        if rows:
            files.append(_write(destination, project_id, hour, rows))
        hour = hour + HOUR
    return Report(files=files, skipped=skipped)


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
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return Written(path=str(path), events=len(rows), bytes=len(payload))


def _row(event: Event) -> dict:
    row = dataclasses.asdict(event)
    row["timestamp"] = event.timestamp.isoformat()
    return row


def _floor(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
