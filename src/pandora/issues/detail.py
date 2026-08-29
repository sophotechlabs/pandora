from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from pandora.core.models import ServiceLink
from pandora.issues import components, correlate
from pandora.issues.models import TAG_OVERFLOW_VALUE, Episode, Issue

TIMELINE_LIMIT = 20
ACTIVITY_LIMIT = 20
TAG_VALUE_LIMIT = 5
LINK_PADDING = timedelta(minutes=5)

TIMELINE_COLUMNS = (
    components.Column("Started"),
    components.Column("Ended"),
    components.Column("Duration", numeric=True),
    components.Column("Deliveries", numeric=True),
    components.Column("Distinguishing labels"),
)


@dataclass(frozen=True)
class TagGroup:
    key: str
    total: int
    bars: tuple[components.Bar, ...]


@dataclass(frozen=True)
class Link:
    label: str
    href: str


@dataclass(frozen=True)
class ActivityRow:
    kind: str
    actor: str
    at: datetime
    note: str


@dataclass(frozen=True)
class Detail:
    timeline: components.Table
    tags: tuple[TagGroup, ...]
    links: tuple[Link, ...]
    activities: tuple[ActivityRow, ...]
    annotations: tuple[tuple[str, str], ...]
    correlation: correlate.Correlation


def build(issue: Issue) -> Detail:
    now = timezone.now()
    return Detail(
        timeline=_timeline(issue, now),
        tags=_tag_groups(issue),
        links=_links(issue, now),
        activities=_activities(issue),
        annotations=_annotations(issue),
        correlation=correlate.build(issue, now),
    )


def _episode_range(
    issue: Issue, episode: Episode | None, now: datetime
) -> tuple[datetime, datetime]:
    if episode is None:
        return (issue.first_seen, issue.last_seen)
    if episode.ends_at is None:
        return (episode.starts_at, now)
    return (episode.starts_at, episode.ends_at)


def _label_diff(grouping: dict, labels: dict) -> str:
    extra = [
        f"{key}={value}"
        for key, value in sorted(labels.items())
        if grouping.get(key) != value
    ]
    return " ".join(extra)


def _timeline(issue: Issue, now: datetime) -> components.Table:
    rows = []
    for episode in issue.episodes.all()[:TIMELINE_LIMIT]:
        start, end = _episode_range(issue, episode, now)
        if episode.ends_at is None:
            ended = components.Cell(text="firing", variant="danger")
        else:
            ended = components.Cell(text=components.format_stamp(episode.ends_at))
        rows.append(
            (
                components.Cell(text=components.format_stamp(episode.starts_at)),
                ended,
                components.Cell(text=components.format_duration(end - start)),
                components.Cell(text=str(episode.delivery_count)),
                components.Cell(
                    text=_label_diff(issue.grouping_labels, episode.labels)
                ),
            )
        )
    return components.Table(
        columns=TIMELINE_COLUMNS,
        rows=tuple(rows),
        empty_message="No episodes recorded",
    )


def _tag_groups(issue: Issue) -> tuple[TagGroup, ...]:
    grouped: dict[str, list] = {}
    for stat in issue.tag_stats.all().order_by("key", "-count", "value"):
        grouped.setdefault(stat.key, []).append(stat)

    groups = []
    for key, stats in grouped.items():
        total = sum(stat.count for stat in stats)
        bars = tuple(
            components.Bar(
                label=stat.value,
                count=stat.count,
                percent=components.percent_of(stat.count, total),
            )
            for stat in stats[:TAG_VALUE_LIMIT]
        )
        groups.append(TagGroup(key=key, total=total, bars=bars))
    return tuple(groups)


def _tag_values(issue: Issue) -> dict[str, str]:
    values: dict[str, str] = {}
    for stat in issue.tag_stats.all().order_by("key", "-count", "value"):
        if stat.value == TAG_OVERFLOW_VALUE:
            continue
        values.setdefault(stat.key, stat.value)
    return values


def _link_values(issue: Issue, now: datetime) -> dict[str, object]:
    episode = issue.episodes.first()
    start, end = _episode_range(issue, episode, now)
    padded_start = start - LINK_PADDING
    padded_end = end + LINK_PADDING

    values: dict[str, object] = {}
    values.update(_tag_values(issue))
    values.update(issue.grouping_labels)
    if episode is not None:
        values.update(episode.labels)
    values["project"] = issue.project.slug
    values["environment"] = issue.environment
    values["issue"] = issue.pk
    values["fingerprint"] = issue.fingerprint_hash
    values["from_ms"] = int(start.timestamp() * 1000)
    values["to_ms"] = int(end.timestamp() * 1000)
    values["from_iso"] = start.isoformat()
    values["to_iso"] = end.isoformat()
    values["padded_from_ms"] = int(padded_start.timestamp() * 1000)
    values["padded_to_ms"] = int(padded_end.timestamp() * 1000)
    values["padded_from_iso"] = padded_start.isoformat()
    values["padded_to_iso"] = padded_end.isoformat()
    return values


def _expand(template: str, values: dict[str, object]) -> str:
    if not template:
        return ""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return ""


def _configured(issue: Issue) -> list[tuple[str, str]]:
    rows = ServiceLink.objects.filter(active=True).filter(
        models.Q(project=None) | models.Q(project_id=issue.project_id)
    )
    return [(row.name, row.url_template) for row in rows]


def _links(issue: Issue, now: datetime) -> tuple[Link, ...]:
    values = _link_values(issue, now)
    templates = [
        ("Grafana", settings.PANDORA_GRAFANA_URL),
        ("Loki", settings.PANDORA_LOKI_QUERY_URL),
    ]
    templates.extend(_configured(issue))
    links = []
    for label, template in templates:
        href = _expand(template, values)
        if href:
            links.append(Link(label=label, href=href))
    return tuple(links)


def _activity_note(data: dict) -> str:
    previous = data.get("previous_triage_state", "")
    if previous:
        return f"was {previous}"
    return ""


def _activities(issue: Issue) -> tuple[ActivityRow, ...]:
    rows = []
    for activity in issue.activities.all()[:ACTIVITY_LIMIT]:
        rows.append(
            ActivityRow(
                kind=activity.get_kind_display(),
                actor=activity.actor,
                at=activity.at,
                note=_activity_note(activity.data or {}),
            )
        )
    return tuple(rows)


def _annotations(issue: Issue) -> tuple[tuple[str, str], ...]:
    for activity in issue.activities.all():
        annotations = (activity.data or {}).get("annotations")
        if annotations:
            return tuple(sorted(annotations.items()))
    return ()
