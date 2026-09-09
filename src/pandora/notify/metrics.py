from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db.models import Count, QuerySet, Sum

from pandora.core.models import TokenSource
from pandora.ingest.models import RawEnvelope
from pandora.ingest.queue import get_queue
from pandora.issues.models import HourlyStat, Issue, IssueUser
from pandora.notify.models import (
    Comparison,
    MetricDataset,
    MetricMonitor,
    MetricRun,
    MonitorState,
)
from pandora.perf import service as perf
from pandora.releases.models import SessionBucket
from pandora.ui import query as query_language

RUN_HISTORY = 200
NEVER = "0001-01-01T00:00:00Z"
FINGERPRINT_BYTES = 8
PERCENT = 100.0


@dataclass
class Report:
    fired: list[int] = field(default_factory=list)
    resolved: list[int] = field(default_factory=list)
    evaluated: int = 0

    def lines(self) -> list[str]:
        return [
            f"evaluated {self.evaluated}",
            f"fired {len(self.fired)}",
            f"resolved {len(self.resolved)}",
        ]


def sweep(now: datetime) -> Report:
    report = Report()
    for monitor in MetricMonitor.objects.filter(active=True).select_related("project"):
        report.evaluated += 1
        _evaluate(monitor, now, report)
    return report


def value_of(monitor: MetricMonitor, now: datetime) -> float | None:
    """The number the threshold is compared against.

    None means there was nothing to measure — an empty window is not a breach,
    and a monitor that fires because a service is idle is a monitor nobody keeps.
    """
    window = timedelta(minutes=max(1, monitor.window_minutes))
    current = _measure(monitor, now - window, now)
    if current is None:
        return None
    if not monitor.comparison_delta_minutes:
        return current
    delta = timedelta(minutes=monitor.comparison_delta_minutes)
    previous = _measure(monitor, now - window - delta, now - delta)
    if previous is None:
        return None
    if previous <= 0:
        return None
    return (current - previous) / previous * PERCENT


def breached(monitor: MetricMonitor, value: float) -> bool:
    if monitor.comparison == Comparison.BELOW:
        return value < monitor.threshold
    return value > monitor.threshold


def _evaluate(monitor: MetricMonitor, now: datetime, report: Report) -> None:
    value = value_of(monitor, now)
    if value is None:
        _record(monitor, now, 0.0, MonitorState.NO_DATA, started=False)
        return
    state = MonitorState.OK
    if breached(monitor, value):
        state = MonitorState.FIRING
    previous = monitor.state
    started = state == MonitorState.FIRING and previous != MonitorState.FIRING
    _record(monitor, now, value, state, started=started)
    if started:
        _announce(monitor, value, now, firing=True)
        report.fired.append(monitor.pk)
        return
    if state == MonitorState.OK and previous == MonitorState.FIRING:
        _announce(monitor, value, now, firing=False)
        report.resolved.append(monitor.pk)


def _record(
    monitor: MetricMonitor,
    now: datetime,
    value: float,
    state: str,
    *,
    started: bool,
) -> None:
    """Write the evaluation, and move the start only when firing began.

    The start is what the alert's episode is keyed on, so moving it while the
    monitor is still firing would open a second episode instead of closing the
    first.
    """
    monitor.state = state
    monitor.last_value = value
    monitor.last_evaluated_at = now
    fields = ["state", "last_value", "last_evaluated_at"]
    if started:
        monitor.last_triggered_at = now
        fields.append("last_triggered_at")
    monitor.save(update_fields=fields)
    MetricRun.objects.create(monitor=monitor, at=now, value=value, state=state)
    _prune_runs(monitor)


def _prune_runs(monitor: MetricMonitor) -> None:
    keep = list(
        MetricRun.objects.filter(monitor=monitor).values_list("pk", flat=True)[
            :RUN_HISTORY
        ]
    )
    MetricRun.objects.filter(monitor=monitor).exclude(pk__in=keep).delete()


def _issues(monitor: MetricMonitor, now: datetime) -> QuerySet[Issue]:
    queryset = Issue.objects.filter(project=monitor.project)
    if monitor.environment:
        queryset = queryset.filter(environments__name=monitor.environment)
    if not monitor.query:
        return queryset
    found, _ = query_language.filter_issues(
        queryset, query_language.parse(monitor.query), now
    )
    return found


PERFORMANCE_DATASETS = (
    MetricDataset.THROUGHPUT,
    MetricDataset.LATENCY_P50,
    MetricDataset.LATENCY_P95,
    MetricDataset.FAILURE_RATE,
)


def _measure(
    monitor: MetricMonitor, starts_at: datetime, ends_at: datetime
) -> float | None:
    if monitor.dataset in PERFORMANCE_DATASETS:
        return _performance(monitor, starts_at, ends_at)
    if monitor.dataset == MetricDataset.CRASH_FREE:
        return _crash_free(monitor, starts_at, ends_at)
    issues = _issues(monitor, ends_at)
    if monitor.dataset == MetricDataset.NEW_ISSUES:
        return float(
            issues.filter(first_seen__gte=starts_at, first_seen__lt=ends_at).count()
        )
    if monitor.dataset == MetricDataset.USERS:
        return float(
            IssueUser.objects.filter(
                issue__in=issues, first_seen__gte=starts_at, first_seen__lt=ends_at
            ).count()
        )
    stats = HourlyStat.objects.filter(
        issue__in=issues,
        hour__gte=_hour(starts_at),
        hour__lt=ends_at,
    )
    if monitor.dataset == MetricDataset.ISSUES:
        return float(stats.aggregate(found=Count("issue", distinct=True))["found"] or 0)
    return float(stats.aggregate(total=Sum("count"))["total"] or 0)


def _performance(
    monitor: MetricMonitor, starts_at: datetime, ends_at: datetime
) -> float | None:
    """Read the aggregated endpoint buckets rather than a span store.

    For these datasets the monitor's query is a transaction name, optionally
    with one star — there are no spans to write a query language against, and
    an endpoint name is what an operator would type anyway.
    """
    reading = perf.combined(
        monitor.project,
        starts_at,
        ends_at,
        environment=monitor.environment,
        pattern=monitor.query.strip(),
    )
    if reading is None or reading.count <= 0:
        return None
    if monitor.dataset == MetricDataset.THROUGHPUT:
        return float(reading.count)
    if monitor.dataset == MetricDataset.FAILURE_RATE:
        return reading.failure_rate
    if monitor.dataset == MetricDataset.LATENCY_P50:
        return reading.quantile(0.5)
    return reading.quantile(0.95)


def _crash_free(
    monitor: MetricMonitor, starts_at: datetime, ends_at: datetime
) -> float | None:
    buckets = SessionBucket.objects.filter(
        project=monitor.project,
        hour__gte=_hour(starts_at),
        hour__lt=ends_at,
    )
    if monitor.environment:
        buckets = buckets.filter(environment=monitor.environment)
    totals = buckets.aggregate(sessions=Sum("sessions"), crashed=Sum("crashed"))
    sessions = totals["sessions"] or 0
    if sessions <= 0:
        return None
    crashed = totals["crashed"] or 0
    return (sessions - crashed) / sessions * PERCENT


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def summary(monitor: MetricMonitor, value: float) -> str:
    label = MetricDataset(monitor.dataset).label
    if monitor.comparison_delta_minutes:
        return (
            f"{label} changed by {value:.1f}% over {monitor.window_minutes}m,"
            f" {monitor.comparison} {monitor.threshold:g}%"
        )
    return (
        f"{label} was {value:g} over {monitor.window_minutes}m,"
        f" {monitor.comparison} {monitor.threshold:g}"
    )


def fingerprint(monitor: MetricMonitor) -> str:
    key = f"pandora-metric-monitor:{monitor.pk}".encode()
    return hashlib.sha256(key).hexdigest()[: FINGERPRINT_BYTES * 2]


def payload_for(
    monitor: MetricMonitor, value: float, now: datetime, firing: bool
) -> dict[str, Any]:
    """The Alertmanager-shaped body the monitor posts to its own inbox.

    A threshold breach is an alert, so it goes through the door alerts already
    use rather than growing a second lifecycle beside the one that works.
    """
    status = "firing"
    if not firing:
        status = "resolved"
    labels = {
        "alertname": monitor.name,
        "severity": monitor.severity,
        "project": monitor.project.slug,
        "monitor": str(monitor.pk),
        "source": "pandora-metric-monitor",
    }
    if monitor.environment:
        labels["environment"] = monitor.environment
    annotations = {
        "summary": summary(monitor, value),
        "description": summary(monitor, value),
    }
    starts_at = monitor.last_triggered_at or now
    ends_at = NEVER
    if not firing:
        ends_at = _isoformat(now)
    return {
        "version": "4",
        "status": status,
        "groupLabels": {"alertname": monitor.name},
        "commonLabels": dict(labels),
        "commonAnnotations": dict(annotations),
        "alerts": [
            {
                "status": status,
                "labels": dict(labels),
                "annotations": dict(annotations),
                "startsAt": _isoformat(starts_at),
                "endsAt": ends_at,
                "fingerprint": fingerprint(monitor),
            }
        ],
    }


def _isoformat(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _announce(
    monitor: MetricMonitor, value: float, now: datetime, firing: bool
) -> None:
    stored = RawEnvelope.objects.create(
        project=monitor.project,
        source=TokenSource.AM,
        environment=monitor.environment,
        payload=payload_for(monitor, value, now, firing),
    )
    get_queue().publish(stored.pk)
