from __future__ import annotations

from datetime import datetime

from django.db.models import F, Value
from django.db.models.functions import Greatest, Least

from pandora.issues.models import Issue, IssueEnvironment


def record(issue: Issue, name: str, at: datetime, count: int = 1) -> None:
    environment, created = IssueEnvironment.objects.get_or_create(
        issue=issue,
        name=name,
        defaults={"first_seen": at, "last_seen": at, "event_count": count},
    )
    if created:
        return
    IssueEnvironment.objects.filter(pk=environment.pk).update(
        first_seen=Least(F("first_seen"), Value(at)),
        last_seen=Greatest(F("last_seen"), Value(at)),
        event_count=F("event_count") + count,
    )


def names_of(issue: Issue) -> list[str]:
    return list(
        IssueEnvironment.objects.filter(issue=issue).values_list("name", flat=True)
    )
