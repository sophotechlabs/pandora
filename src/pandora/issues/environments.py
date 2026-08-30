from __future__ import annotations

from datetime import datetime

from django.db.models import F

from pandora.issues.models import Issue, IssueEnvironment


def record(issue: Issue, name: str, at: datetime, count: int = 1) -> None:
    updated = IssueEnvironment.objects.filter(issue=issue, name=name).update(
        last_seen=at,
        event_count=F("event_count") + count,
    )
    if updated:
        return
    IssueEnvironment.objects.create(
        issue=issue,
        name=name,
        first_seen=at,
        last_seen=at,
        event_count=count,
    )


def names_of(issue: Issue) -> list[str]:
    return list(
        IssueEnvironment.objects.filter(issue=issue).values_list("name", flat=True)
    )
