from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db.models import Q, Sum

from pandora.core.models import Project
from pandora.issues import triage
from pandora.issues.models import ActivityKind, HourlyStat, Issue, IssueActivity
from pandora.notify import events as notify_events
from pandora.notify.models import REPORT, Delivery, Destination

DEFAULT_PERIOD = timedelta(days=7)
TOP_ISSUES = 5
PERIODS = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
REPORT_EVENT = REPORT


@dataclass(frozen=True)
class TopIssue:
    issue_id: int
    title: str
    count: int
    url: str


@dataclass
class Summary:
    project: str
    period: str
    starts_at: datetime
    ends_at: datetime
    events: int = 0
    new_issues: int = 0
    resolved_issues: int = 0
    regressed_issues: int = 0
    open_issues: int = 0
    previous_events: int = 0
    top: list[TopIssue] = field(default_factory=list)

    @property
    def change(self) -> float | None:
        if self.previous_events <= 0:
            return None
        return (self.events - self.previous_events) / self.previous_events * 100

    def lines(self) -> list[str]:
        rows = [
            f"{self.project}: {self.events} events over the last {self.period}",
            f"{self.new_issues} new, {self.regressed_issues} regressed,"
            f" {self.resolved_issues} resolved, {self.open_issues} still open",
        ]
        change = self.change
        if change is not None:
            rows.append(f"{change:+.0f}% against the {self.period} before")
        rows.extend(f"  {row.count} x {row.title}" for row in self.top)
        return rows

    def as_payload(self) -> dict[str, object]:
        return {
            "event": REPORT_EVENT,
            "project": self.project,
            "period": self.period,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "events": self.events,
            "new_issues": self.new_issues,
            "resolved_issues": self.resolved_issues,
            "regressed_issues": self.regressed_issues,
            "open_issues": self.open_issues,
            "change_percent": self.change,
            "top_issues": [
                {
                    "id": row.issue_id,
                    "title": row.title,
                    "count": row.count,
                    "url": row.url,
                }
                for row in self.top
            ],
        }


def window_for(period: str) -> timedelta:
    return PERIODS.get(period, DEFAULT_PERIOD)


def build(project: Project, now: datetime, period: str = "week") -> Summary:
    """What happened in this project over the period, from data already stored.

    Sentry mails this once a week and calls it a weekly report. It is four
    aggregates over tables the stream already writes, so it costs a query rather
    than a pipeline.
    """
    window = window_for(period)
    starts_at = now - window
    issues = Issue.objects.filter(project=project)
    summary = Summary(
        project=project.slug,
        period=period,
        starts_at=starts_at,
        ends_at=now,
        events=_events(issues, starts_at, now),
        previous_events=_events(issues, starts_at - window, starts_at),
        new_issues=issues.filter(
            first_seen__gte=starts_at, first_seen__lte=now
        ).count(),
        open_issues=issues.filter(triage_state__in=triage.OPEN_STATES).count(),
        resolved_issues=_activity(project, ActivityKind.RESOLVED, starts_at, now),
        regressed_issues=_activity(project, ActivityKind.REGRESSION, starts_at, now),
        top=_top(issues, starts_at, now),
    )
    return summary


def _events(issues, starts_at: datetime, ends_at: datetime) -> int:
    total = HourlyStat.objects.filter(
        issue__in=issues, hour__gte=_hour(starts_at), hour__lt=ends_at
    ).aggregate(found=Sum("count"))
    return int(total["found"] or 0)


def _activity(
    project: Project, kind: str, starts_at: datetime, ends_at: datetime
) -> int:
    return IssueActivity.objects.filter(
        issue__project=project, kind=kind, at__gte=starts_at, at__lte=ends_at
    ).count()


def _top(issues, starts_at: datetime, ends_at: datetime) -> list[TopIssue]:
    rows = (
        issues.annotate(
            window_count=Sum(
                "hourly_stats__count",
                filter=Q(
                    hourly_stats__hour__gte=_hour(starts_at),
                    hourly_stats__hour__lt=ends_at,
                ),
            )
        )
        .filter(window_count__gt=0)
        .order_by("-window_count", "-pk")[:TOP_ISSUES]
    )
    return [
        TopIssue(
            issue_id=issue.pk,
            title=issue.title,
            count=int(issue.window_count),
            url=notify_events.issue_url(issue),
        )
        for issue in rows
    ]


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def destinations_for(project: Project) -> list[Destination]:
    rows = Destination.objects.filter(enabled=True).filter(
        Q(project=None) | Q(project=project)
    )
    return [row for row in rows if REPORT_EVENT in (row.events or [])]


def queue(summary: Summary, project: Project) -> list[Delivery]:
    targets = destinations_for(project)
    if not targets:
        return []
    body = summary.as_payload()
    anchor = _anchor(project)
    if anchor is None:
        return []
    return Delivery.objects.bulk_create(
        [
            Delivery(
                destination=destination,
                issue=anchor,
                event=REPORT_EVENT,
                payload=body,
            )
            for destination in targets
        ]
    )


def _anchor(project: Project) -> Issue | None:
    """The row a delivery is filed against.

    A delivery belongs to an issue, and a report belongs to none — so it is
    filed against the project's most recent one rather than growing a nullable
    column that every other query would then have to defend against.
    """
    return Issue.objects.filter(project=project).order_by("-last_seen").first()


def send(now: datetime, period: str = "week") -> list[Summary]:
    built = []
    for project in Project.objects.all().order_by("slug"):
        summary = build(project, now, period)
        queue(summary, project)
        built.append(summary)
    return built
