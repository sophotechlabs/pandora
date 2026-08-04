from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

STATUS_FIRING = "firing"
STATUS_RESOLVED = "resolved"
SOURCE_FIRING = "firing"
SOURCE_RESOLVED = "resolved"
TRIAGE_NEW = "new"
TRIAGE_RESOLVED = "resolved"
ACTIVITY_CREATED = "created"
ACTIVITY_REGRESSION = "regression"


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


def new_issue_fields(occurrence: Occurrence) -> dict[str, Any]:
    return {
        "title": occurrence.title,
        "culprit": occurrence.culprit,
        "level": occurrence.level,
        "environment": occurrence.environment,
        "fingerprint": list(occurrence.fingerprint),
        "grouping_labels": dict(occurrence.grouping_labels),
        "first_seen": occurrence.starts_at,
        "last_seen": occurrence.timestamp,
        "event_count": 0,
        "open_episode_count": 0,
        "source_state": None,
        "triage_state": TRIAGE_NEW,
    }


def apply_occurrence(
    issue_state: IssueState | None,
    episode_state: EpisodeState | None,
    occurrence: Occurrence,
) -> Transition:
    move = _episode_move(episode_state, occurrence.status != STATUS_RESOLVED)
    open_before = 0
    if issue_state is not None:
        open_before = issue_state.open_episode_count
    open_after = max(0, open_before + move.open_episode_delta)

    issue_fields = _issue_fields(issue_state, occurrence, open_after)
    activities = _activities(issue_state, occurrence, move.open_episode_delta)
    if _has_regression(activities):
        issue_fields["triage_state"] = TRIAGE_NEW

    return Transition(
        create_issue=issue_state is None,
        create_episode=move.create,
        close_episode=move.close,
        bump_delivery=move.bump,
        count_occurrence=move.create,
        open_episode_delta=move.open_episode_delta,
        issue_fields=issue_fields,
        activities=activities,
    )


def apply_event(
    issue_state: IssueState | None,
    occurrence: Occurrence,
) -> Transition:
    fields = _event_fields(issue_state, occurrence)
    activities = _event_activities(issue_state, occurrence)
    if _has_regression(activities):
        fields["triage_state"] = TRIAGE_NEW

    return Transition(
        create_issue=issue_state is None,
        count_occurrence=True,
        issue_fields=fields,
        activities=activities,
    )


def _event_fields(
    issue_state: IssueState | None, occurrence: Occurrence
) -> dict[str, Any]:
    fields: dict[str, Any] = {"last_seen": occurrence.timestamp}
    if issue_state is None:
        fields["first_seen"] = occurrence.starts_at
        return fields
    if issue_state.last_seen > occurrence.timestamp:
        fields["last_seen"] = issue_state.last_seen
    if occurrence.starts_at < issue_state.first_seen:
        fields["first_seen"] = occurrence.starts_at
    return fields


def _event_activities(
    issue_state: IssueState | None, occurrence: Occurrence
) -> tuple[ActivityRecord, ...]:
    if issue_state is None:
        return (ActivityRecord(kind=ACTIVITY_CREATED),)
    if issue_state.triage_state != TRIAGE_RESOLVED:
        return ()

    regression = ActivityRecord(
        kind=ACTIVITY_REGRESSION,
        data={"previous_triage_state": issue_state.triage_state},
    )
    if issue_state.last_resolved_at is None:
        return (regression,)
    if occurrence.starts_at <= issue_state.last_resolved_at:
        return ()
    return (regression,)


@dataclass(frozen=True)
class _Move:
    create: bool
    close: bool
    bump: bool
    open_episode_delta: int


def _episode_move(episode_state: EpisodeState | None, firing: bool) -> _Move:
    if episode_state is None:
        if firing:
            return _Move(create=True, close=False, bump=False, open_episode_delta=1)
        return _Move(create=True, close=False, bump=False, open_episode_delta=0)
    if firing and episode_state.ends_at is not None:
        return _Move(create=False, close=False, bump=True, open_episode_delta=1)
    if not firing and episode_state.ends_at is None:
        return _Move(create=False, close=True, bump=True, open_episode_delta=-1)
    return _Move(create=False, close=False, bump=True, open_episode_delta=0)


def _issue_fields(
    issue_state: IssueState | None, occurrence: Occurrence, open_after: int
) -> dict[str, Any]:
    fields: dict[str, Any] = {"last_seen": occurrence.timestamp}
    if issue_state is not None and issue_state.last_seen > occurrence.timestamp:
        fields["last_seen"] = issue_state.last_seen
    if open_after > 0:
        fields["source_state"] = SOURCE_FIRING
    else:
        fields["source_state"] = SOURCE_RESOLVED
    if issue_state is None:
        fields["first_seen"] = occurrence.starts_at
        return fields
    if occurrence.starts_at < issue_state.first_seen:
        fields["first_seen"] = occurrence.starts_at
    return fields


def _activities(
    issue_state: IssueState | None,
    occurrence: Occurrence,
    open_episode_delta: int,
) -> tuple[ActivityRecord, ...]:
    if issue_state is None:
        return (ActivityRecord(kind=ACTIVITY_CREATED),)
    if not _regressed(issue_state, occurrence, open_episode_delta):
        return ()
    return (
        ActivityRecord(
            kind=ACTIVITY_REGRESSION,
            data={"previous_triage_state": issue_state.triage_state},
        ),
    )


def _has_regression(activities: tuple[ActivityRecord, ...]) -> bool:
    return any(record.kind == ACTIVITY_REGRESSION for record in activities)


def _regressed(
    issue_state: IssueState, occurrence: Occurrence, open_episode_delta: int
) -> bool:
    if open_episode_delta <= 0:
        return False
    if issue_state.triage_state != TRIAGE_RESOLVED:
        return False
    if issue_state.last_resolved_at is None:
        return True
    return occurrence.starts_at > issue_state.last_resolved_at
