from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ulid import ULID

EVENTS_TABLE = "events_event"


def new_event_id() -> str:
    return str(ULID())


@dataclass(frozen=True)
class Event:
    id: str
    project_id: int
    timestamp: datetime
    level: str
    message: str
    issue_id: int | None = None
    episode_id: str | None = None
    fingerprint: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    source: str = "am"
    environment: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
