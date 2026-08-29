from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pandora.issues.models import Issue

WINDOWS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}
COUNTS = {"100": 100, "500": 500, "1000": 1000}
MAX_WINDOW = timedelta(weeks=4)


@dataclass(frozen=True)
class SnoozePlan:
    until: datetime | None = None
    past_count: int | None = None
    error: str = ""


def plan(issue: Issue, spec: str, now: datetime) -> SnoozePlan:
    if spec in WINDOWS:
        return SnoozePlan(until=now + WINDOWS[spec])
    if spec in COUNTS:
        return SnoozePlan(past_count=issue.event_count + COUNTS[spec])
    return SnoozePlan(error=f"{spec} is not a snooze window")


def snoozed(issue: Issue, now: datetime) -> bool:
    if issue.snoozed_until is not None and issue.snoozed_until > now:
        return True
    return (
        issue.snoozed_past_count is not None
        and issue.event_count < issue.snoozed_past_count
    )


def expired(issue: Issue, now: datetime) -> bool:
    if issue.snoozed_until is None and issue.snoozed_past_count is None:
        return False
    return not snoozed(issue, now)
