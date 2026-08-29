from __future__ import annotations

from pandora.issues import lifecycle
from pandora.issues.models import Issue
from pandora.people import ownership


def on_transition(
    issue: Issue,
    transition: lifecycle.Transition,
    occurrence: lifecycle.Occurrence,
    before: int,
) -> None:
    if not transition.create_issue:
        return
    ownership.assign(issue, occurrence)
