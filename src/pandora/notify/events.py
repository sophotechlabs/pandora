from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import models

from pandora.issues.models import Issue, Level
from pandora.notify.models import Delivery, Destination

LEVEL_ORDER: dict[str, int] = {
    Level.DEBUG: 0,
    Level.INFO: 1,
    Level.WARNING: 2,
    Level.ERROR: 3,
    Level.FATAL: 4,
}
MILESTONES = (10, 100, 1000, 10000, 100000)


def milestone_reached(before: int, after: int) -> int | None:
    for milestone in MILESTONES:
        if before < milestone <= after:
            return milestone
    return None


def _loud_enough(issue: Issue, destination: Destination) -> bool:
    wanted = LEVEL_ORDER.get(destination.min_level, 0)
    return LEVEL_ORDER.get(issue.level, 0) >= wanted


def destinations_for(issue: Issue, event: str) -> list[Destination]:
    rows = Destination.objects.filter(enabled=True).filter(
        models.Q(project=None) | models.Q(project_id=issue.project_id)
    )
    return [
        destination
        for destination in rows
        if event in (destination.events or []) and _loud_enough(issue, destination)
    ]


def _owner(issue: Issue) -> dict[str, Any] | None:
    assignment = getattr(issue, "assignment", None)
    if assignment is None:
        return None
    team = None
    if assignment.team_id:
        team = assignment.team.name
    user = None
    if assignment.user_id:
        user = assignment.user.get_username()
    return {"team": team, "user": user}


def payload_for(
    issue: Issue, event: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = {
        "event": event,
        "owner": _owner(issue),
        "issue": {
            "id": issue.pk,
            "title": issue.title,
            "culprit": issue.culprit,
            "level": issue.level,
            "project": issue.project.slug,
            "environment": issue.environment,
            "event_count": issue.event_count,
            "triage_state": issue.triage_state,
            "url": _issue_url(issue),
        },
    }
    if extra:
        body.update(extra)
    return body


def _issue_url(issue: Issue) -> str:
    base = settings.PANDORA_BASE_URL.rstrip("/")
    if not base:
        return f"/issues/{issue.pk}/"
    return f"{base}/issues/{issue.pk}/"


def queue(
    issue: Issue, event: str, extra: dict[str, Any] | None = None
) -> list[Delivery]:
    targets = destinations_for(issue, event)
    if not targets:
        return []
    body = payload_for(issue, event, extra)
    return Delivery.objects.bulk_create(
        [
            Delivery(destination=destination, issue=issue, event=event, payload=body)
            for destination in targets
        ]
    )


def queue_many(issue: Issue, names: Iterable[str]) -> list[Delivery]:
    made: list[Delivery] = []
    for name in names:
        made.extend(queue(issue, name))
    return made
