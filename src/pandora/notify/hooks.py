from __future__ import annotations

from pandora.issues import lifecycle
from pandora.issues.models import Issue
from pandora.notify import events, models


def on_transition(
    issue: Issue,
    transition: lifecycle.Transition,
    occurrence: lifecycle.Occurrence,
    before: int,
) -> None:
    names = []
    if transition.create_issue:
        names.append(models.NEW)
    if any(record.kind == "regression" for record in transition.activities):
        names.append(models.REGRESSION)
    milestone = events.milestone_reached(before, issue.event_count)
    if milestone is not None:
        events.queue(issue, models.MILESTONE, {"milestone": milestone})
    events.queue_many(issue, names)


def on_wake(issue: Issue) -> None:
    events.queue(issue, models.UNSNOOZED)


def on_resolve(issue: Issue, actor: str) -> None:
    events.queue(issue, models.RESOLVED, {"actor": actor})
