from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pandora.events.store import EventStore

BATCH = 1000

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferReport:
    projects: int
    events: int


def transfer(
    source: EventStore,
    target: EventStore,
    project_ids: Sequence[int],
    batch: int = BATCH,
) -> TransferReport:
    moved = 0
    for project_id in project_ids:
        copied = _transfer_project(source, target, project_id, batch)
        log.info("transfer: project %s copied %s events", project_id, copied)
        moved += copied
    return TransferReport(projects=len(project_ids), events=moved)


def _transfer_project(
    source: EventStore,
    target: EventStore,
    project_id: int,
    batch: int,
) -> int:
    moved = 0
    cursor = None
    while True:
        events = source.fetch(project_id, before=cursor, limit=batch)
        if not events:
            return moved
        target.insert(events)
        moved += len(events)
        cursor = events[-1].id
        if len(events) < batch:
            return moved
