from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Max, Min
from prometheus_client import Gauge

from pandora.core.models import TokenSource
from pandora.issues.models import ActivityKind, Issue, IssueActivity

WINDOW = timedelta(days=30)
RESOLVING = (ActivityKind.RESOLVED,)

MTTR_SECONDS = Gauge(
    "pandora_mttr_seconds",
    "Median seconds from an issue first being seen to it being resolved",
    ["source"],
    multiprocess_mode="livemostrecent",
)
RESOLVED_TOTAL = Gauge(
    "pandora_resolved_issues",
    "Issues resolved inside the reporting window",
    ["source"],
    multiprocess_mode="livemostrecent",
)


@dataclass(frozen=True)
class Resolution:
    issue_id: int
    source: str
    seconds: float


def refresh(now: datetime) -> dict[str, float]:
    """Publish MTTR as a gauge and let the operator's Grafana draw it.

    Split by source, because Alertmanager issues resolve themselves when the
    alert clears and would otherwise dominate the number — the caveat Rollbar
    prints beside its own MTTR and nobody else acts on.
    """
    medians = {}
    for source, seconds in _by_source(now).items():
        median = _median(seconds)
        medians[source] = median
        MTTR_SECONDS.labels(source=source).set(median)
        RESOLVED_TOTAL.labels(source=source).set(len(seconds))
    return medians


def resolutions(now: datetime) -> list[Resolution]:
    since = now - WINDOW
    rows = (
        IssueActivity.objects.filter(kind__in=RESOLVING, at__gte=since)
        .values("issue_id")
        .annotate(resolved_at=Max("at"))
    )
    resolved = {row["issue_id"]: row["resolved_at"] for row in rows}
    if not resolved:
        return []
    opened = Issue.objects.filter(pk__in=resolved).values("pk", "first_seen")
    sources = _sources(list(resolved))
    return [
        Resolution(
            issue_id=row["pk"],
            source=sources.get(row["pk"], TokenSource.SDK),
            seconds=(resolved[row["pk"]] - row["first_seen"]).total_seconds(),
        )
        for row in opened
    ]


def _sources(issue_ids: list[int]) -> dict[int, str]:
    rows = (
        Issue.objects.filter(pk__in=issue_ids)
        .annotate(first_episode=Min("episodes__starts_at"))
        .values("pk", "first_episode")
    )
    sources: dict[int, str] = {}
    for row in rows:
        if row["first_episode"] is None:
            sources[row["pk"]] = TokenSource.SDK
        else:
            sources[row["pk"]] = TokenSource.AM
    return sources


def _by_source(now: datetime) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {TokenSource.AM: [], TokenSource.SDK: []}
    for row in resolutions(now):
        found.setdefault(row.source, []).append(row.seconds)
    return found


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
