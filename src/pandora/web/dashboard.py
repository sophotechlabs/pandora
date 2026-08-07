from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Sum
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.issues import components, triage
from pandora.issues.models import (
    ActivityKind,
    Issue,
    IssueActivity,
    SourceState,
    TriageState,
)

TOP_ISSUES = 8
NEW_WINDOW = timedelta(hours=24)
REGRESSION_WINDOW = timedelta(days=7)

TOP_ISSUE_COLUMNS = (
    components.Column("Issue"),
    components.Column("Project"),
    components.Column("Level"),
    components.Column("Events", numeric=True),
    components.Column("Last seen", numeric=True),
)


INGEST_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class Dashboard:
    kpis: tuple[components.Kpi, ...]
    tables: dict[str, components.Table] = field(default_factory=dict)


def dashboard_callback(
    request: HttpRequest,
    context: dict[str, Any],
) -> dict[str, Any]:
    now = timezone.now()
    context["dashboard"] = Dashboard(
        kpis=_kpis(now) + _ingest_kpis(now),
        tables={"issues": _top_issues()},
    )
    return context


def _kpis(now: datetime) -> tuple[components.Kpi, ...]:
    firing = Issue.objects.filter(source_state=SourceState.FIRING)
    open_episodes = firing.aggregate(total=Sum("open_episode_count"))["total"] or 0
    new_day = Issue.objects.filter(first_seen__gte=now - NEW_WINDOW).count()
    new_week = Issue.objects.filter(first_seen__gte=now - REGRESSION_WINDOW).count()
    regressions = (
        IssueActivity.objects.filter(
            kind=ActivityKind.REGRESSION,
            at__gte=now - REGRESSION_WINDOW,
        )
        .values("issue")
        .distinct()
        .count()
    )
    untriaged = Issue.objects.filter(triage_state=TriageState.NEW).count()
    acknowledged = Issue.objects.filter(
        triage_state=TriageState.ACKNOWLEDGED,
    ).count()

    return (
        components.Kpi(
            label="Firing now",
            value=firing.count(),
            hint=f"{open_episodes} open episode(s)",
        ),
        components.Kpi(
            label="New in 24 hours",
            value=new_day,
            hint=f"{new_week} in the last 7 days",
        ),
        components.Kpi(
            label="Regressions in 7 days",
            value=regressions,
            hint="issues that fired again after a resolve",
        ),
        components.Kpi(
            label="Untriaged",
            value=untriaged,
            hint=f"{acknowledged} acknowledged",
        ),
    )


def _ingest_kpis(now: datetime) -> tuple[components.Kpi, ...]:
    failed = RawEnvelope.objects.filter(state=EnvelopeState.FAILED).count()
    pending = RawEnvelope.objects.filter(state=EnvelopeState.PENDING).count()
    latest = (
        RawEnvelope.objects.filter(state=EnvelopeState.DONE)
        .order_by("-received_at")
        .values_list("received_at", flat=True)
        .first()
    )

    hint = "nothing ingested yet"
    if latest is not None:
        hint = f"last accepted {components.format_stamp(latest)}"

    recent = RawEnvelope.objects.filter(received_at__gte=now - INGEST_WINDOW).count()

    return (
        components.Kpi(
            label="Ingest backlog",
            value=failed + pending,
            hint=f"{failed} failed, {pending} pending — replay clears these",
        ),
        components.Kpi(
            label="Envelopes in the last hour",
            value=recent,
            hint=hint,
        ),
    )


def _top_issues() -> components.Table:
    issues = (
        Issue.objects.filter(triage_state__in=triage.OPEN_STATES)
        .select_related("project")
        .order_by("-event_count", "-last_seen")[:TOP_ISSUES]
    )
    rows = []
    for issue in issues:
        rows.append(
            (
                components.Cell(
                    text=issue.title,
                    href=reverse("admin:issues_issue_change", args=[issue.pk]),
                ),
                components.Cell(text=issue.project.slug),
                components.Cell(
                    text=issue.get_level_display(),
                    variant=components.LEVEL_VARIANTS.get(issue.level),
                ),
                components.Cell(text=str(issue.event_count)),
                components.Cell(text=components.format_stamp(issue.last_seen)),
            )
        )
    return components.Table(
        columns=TOP_ISSUE_COLUMNS,
        rows=tuple(rows),
        empty_message="Nothing open",
    )
