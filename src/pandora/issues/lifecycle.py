from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Occurrence:
    fingerprint: list[str]
    fingerprint_hash: str
    grouping_labels: dict[str, str]
    am_fingerprint: str
    labels: dict[str, str]
    status: str
    title: str
    culprit: str
    level: str
    message: str
    starts_at: datetime
    ends_at: datetime | None
    timestamp: datetime
    tags: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    environment: str = ""
    source: str = "am"


@dataclass(frozen=True)
class IssueState:
    triage_state: str
    open_episode_count: int
    level: str
    first_seen: datetime
    last_seen: datetime
    last_resolved_at: datetime | None = None


@dataclass(frozen=True)
class EpisodeState:
    starts_at: datetime
    ends_at: datetime | None
    delivery_count: int


@dataclass(frozen=True)
class ActivityRecord:
    kind: str
    actor: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Transition:
    create_issue: bool = False
    create_episode: bool = False
    close_episode: bool = False
    bump_delivery: bool = False
    count_occurrence: bool = False
    open_episode_delta: int = 0
    issue_fields: dict[str, Any] = field(default_factory=dict)
    activities: tuple[ActivityRecord, ...] = ()


def apply_occurrence(
    issue_state: IssueState | None,
    episode_state: EpisodeState | None,
    occurrence: Occurrence,
) -> Transition:
    raise NotImplementedError
