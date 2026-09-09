from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q

from pandora.core.models import Project
from pandora.issues import hooks, snooze, triage
from pandora.issues import priority as priority_module
from pandora.issues.models import (
    ActivityKind,
    HourlyStat,
    Issue,
    IssueActivity,
    TriageState,
)

BASELINE_DAYS = 7
QUIET_STATES = (TriageState.IGNORED,)


@dataclass
class Report:
    escalated: list[int] = field(default_factory=list)
    auto_resolved: list[int] = field(default_factory=list)
    reprioritised: list[int] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"escalated {len(self.escalated)}",
            f"auto-resolved {len(self.auto_resolved)}",
            f"reprioritised {len(self.reprioritised)}",
        ]


def run(now: datetime) -> Report:
    report = Report()
    _escalate(now, report)
    _auto_resolve(now, report)
    _reprioritise(report)
    return report


def rate_baseline(counts: list[int]) -> float:
    """What this issue's quiet hour looks like.

    The median rather than the mean, because one spike inside the baseline
    window would otherwise raise the bar high enough to hide the next one.
    """
    if not counts:
        return 0.0
    return float(statistics.median(counts))


def escalating(recent: int, baseline: float) -> bool:
    factor = max(1, settings.PANDORA_ESCALATION_FACTOR)
    floor = max(0, settings.PANDORA_ESCALATION_FLOOR)
    if recent < floor:
        return False
    return recent > baseline * factor


def _escalate(now: datetime, report: Report) -> None:
    hour = now.replace(minute=0, second=0, microsecond=0)
    since = hour - timedelta(days=BASELINE_DAYS)
    for issue in _quiet_issues(now):
        counts = list(
            HourlyStat.objects.filter(
                issue=issue, hour__gte=since, hour__lt=hour
            ).values_list("count", flat=True)
        )
        recent = (
            HourlyStat.objects.filter(issue=issue, hour=hour)
            .values_list("count", flat=True)
            .first()
            or 0
        )
        if not escalating(recent, rate_baseline(counts)):
            continue
        _mark_escalated(issue, recent, now)
        report.escalated.append(issue.pk)


def _quiet_issues(now: datetime):
    snoozed = Q(snoozed_until__gt=now) | Q(snoozed_past_count__gt=F("event_count"))
    return Issue.objects.filter(
        Q(triage_state__in=QUIET_STATES) | snoozed
    ).select_related("project")


def _mark_escalated(issue: Issue, recent: int, now: datetime) -> None:
    previous_state = issue.triage_state
    with transaction.atomic():
        issue.triage_state = TriageState.NEW
        issue.needs_review = True
        issue.snoozed_until = None
        issue.snoozed_past_count = None
        issue.escalated_at = now
        if not issue.priority_locked:
            issue.priority = priority_module.HIGH
        issue.save(
            update_fields=[
                "triage_state",
                "needs_review",
                "snoozed_until",
                "snoozed_past_count",
                "escalated_at",
                "priority",
            ]
        )
        IssueActivity.objects.create(
            issue=issue,
            kind=ActivityKind.ESCALATED,
            at=now,
            data={"previous_triage_state": previous_state, "count": recent},
        )
    hooks.fire("PANDORA_ESCALATION_HOOKS", issue, recent)


def _auto_resolve(now: datetime, report: Report) -> None:
    for project, days in _windows():
        cutoff = now - timedelta(days=days)
        rows = Issue.objects.filter(
            project=project,
            triage_state__in=triage.OPEN_STATES,
            last_seen__lt=cutoff,
            open_episode_count=0,
        )
        for issue in rows:
            if snooze.snoozed(issue, now):
                continue
            _mark_auto_resolved(issue, days, now)
            report.auto_resolved.append(issue.pk)


def _windows() -> list[tuple[Project, int]]:
    fallback = max(0, settings.PANDORA_AUTO_RESOLVE_DAYS)
    windows = []
    for project in Project.objects.all():
        days = project.auto_resolve_days or fallback
        if days <= 0:
            continue
        windows.append((project, days))
    return windows


def _mark_auto_resolved(issue: Issue, days: int, now: datetime) -> None:
    previous_state = issue.triage_state
    with transaction.atomic():
        issue.triage_state = TriageState.RESOLVED
        issue.last_resolved_at = now
        issue.needs_review = False
        issue.save(update_fields=["triage_state", "last_resolved_at", "needs_review"])
        IssueActivity.objects.create(
            issue=issue,
            kind=ActivityKind.AUTO_RESOLVED,
            at=now,
            data={"previous_triage_state": previous_state, "days": days},
        )
    hooks.fire("PANDORA_RESOLVE_HOOKS", issue, "")


def _reprioritise(report: Report) -> None:
    for issue in Issue.objects.filter(priority_locked=False):
        wanted = priority_module.derive(
            priority_module.Inputs(
                level=issue.level,
                open_episode_count=issue.open_episode_count,
                user_count=issue.user_count,
                escalating=issue.escalated_at is not None,
            )
        )
        if wanted == issue.priority:
            continue
        issue.priority = wanted
        issue.save(update_fields=["priority"])
        report.reprioritised.append(issue.pk)
