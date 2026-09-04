from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import OuterRef, Prefetch, QuerySet, Subquery

from pandora.attachments.models import EventAttachment
from pandora.events.types import Event
from pandora.issues import components, sparkline
from pandora.issues.models import Episode, HourlyStat, Issue
from pandora.ui import event_view

CHART_WINDOW = timedelta(days=30)
CHART_BUCKETS = 30
CHART_HEIGHT = 72
CHART_BAR_WIDTH = 12
CHART_BAR_GAP = 4
CHART_WIDTH = sparkline.chart_width(CHART_BUCKETS, CHART_BAR_WIDTH, CHART_BAR_GAP)

SPARK_HEIGHT = 22
SPARK_BAR_WIDTH = 3
SPARK_BAR_GAP = 1
SPARK_WIDTH = sparkline.chart_width(
    sparkline.BUCKET_COUNT,
    SPARK_BAR_WIDTH,
    SPARK_BAR_GAP,
)

EVENT_ID_HEAD = 8
STATE_LABELS = {
    "firing": "Firing",
    "resolved": "Resolved",
}


@dataclass(frozen=True)
class Row:
    issue: Issue
    bars: tuple[sparkline.Bar, ...]
    window_total: int
    labels: tuple[str, ...]
    last_seen: str
    first_seen: str
    duration: str
    state_label: str
    owner: str


@dataclass(frozen=True)
class ChartBar:
    bar: sparkline.Bar
    label: str


@dataclass(frozen=True)
class EventRow:
    id: str
    short_id: str
    timestamp: datetime
    level: str
    message: str
    tags: tuple[tuple[str, str], ...]
    raw: str
    body: event_view.EventBody | None
    attachments: tuple[EventAttachment, ...]


def stream_queryset(now: datetime) -> QuerySet[Issue]:
    open_episodes = Episode.objects.filter(
        issue=OuterRef("pk"), ends_at__isnull=True
    ).order_by("starts_at")
    latest_episodes = Episode.objects.filter(issue=OuterRef("pk")).order_by(
        "-starts_at"
    )
    window_stats = HourlyStat.objects.filter(
        hour__gte=sparkline.window_start(now)
    ).order_by("hour")
    return (
        Issue.objects.select_related(
            "project", "assignment", "assignment__team", "assignment__user"
        )
        .prefetch_related(
            Prefetch("hourly_stats", queryset=window_stats, to_attr="window_stats")
        )
        .annotate(
            open_since=Subquery(open_episodes.values("starts_at")[:1]),
            latest_start=Subquery(latest_episodes.values("starts_at")[:1]),
            latest_end=Subquery(latest_episodes.values("ends_at")[:1]),
        )
    )


def row(issue: Issue, now: datetime) -> Row:
    series = sparkline.counts(
        ((stat.hour, stat.count) for stat in getattr(issue, "window_stats", [])),
        now,
    )
    return Row(
        issue=issue,
        bars=tuple(
            sparkline.bars(
                series,
                height=SPARK_HEIGHT,
                width=SPARK_BAR_WIDTH,
                gap=SPARK_BAR_GAP,
            )
        ),
        window_total=sum(series),
        labels=grouping_labels(issue),
        last_seen=components.format_relative(issue.last_seen, now),
        first_seen=components.format_relative(issue.first_seen, now),
        duration=components.issue_duration(
            getattr(issue, "open_since", None),
            getattr(issue, "latest_start", None),
            getattr(issue, "latest_end", None),
            now,
        ),
        state_label=state_label(issue),
        owner=owner_of(issue),
    )


def owner_of(issue: Issue) -> str:
    assignment = getattr(issue, "assignment", None)
    if assignment is None:
        return ""
    if assignment.user is not None:
        return assignment.user.get_username()
    if assignment.team is not None:
        return assignment.team.name
    return ""


def grouping_labels(issue: Issue) -> tuple[str, ...]:
    labels = issue.grouping_labels or {}
    return tuple(f"{key}={value}" for key, value in sorted(labels.items()))


def state_label(issue: Issue) -> str:
    return STATE_LABELS.get(issue.source_state or "", "No source state")


def chart(stats: QuerySet[HourlyStat], now: datetime) -> tuple[ChartBar, ...]:
    series = sparkline.counts(
        ((stat.hour, stat.count) for stat in stats),
        now,
        window=CHART_WINDOW,
        bucket_count=CHART_BUCKETS,
    )
    start = sparkline.start_of(now, CHART_WINDOW)
    step = CHART_WINDOW / CHART_BUCKETS
    geometry = sparkline.bars(
        series,
        height=CHART_HEIGHT,
        width=CHART_BAR_WIDTH,
        gap=CHART_BAR_GAP,
    )
    return tuple(
        ChartBar(
            bar=bar,
            label=f"{components.format_stamp(start + step * index)} — {count}",
        )
        for index, (bar, count) in enumerate(zip(geometry, series, strict=True))
    )


def event_row(
    event: Event,
    attachments: tuple[EventAttachment, ...] = (),
) -> EventRow:
    return EventRow(
        id=event.id,
        short_id=event.id[:EVENT_ID_HEAD],
        timestamp=event.timestamp,
        level=event.level,
        message=event.message,
        tags=tuple(sorted((event.tags or {}).items())),
        raw=_raw(event),
        body=event_view.build(event.payload, event.project_id),
        attachments=attachments,
    )


def _raw(event: Event) -> str:
    body: dict[str, Any] = {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "level": event.level,
        "message": event.message,
        "source": event.source,
        "environment": event.environment,
        "episode_id": event.episode_id,
        "fingerprint": list(event.fingerprint or []),
        "tags": dict(event.tags or {}),
        "extra": dict(event.extra or {}),
        "payload": dict(event.payload or {}),
    }
    return json.dumps(body, indent=2, sort_keys=True, default=str)
