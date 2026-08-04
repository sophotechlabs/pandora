from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

NEW = "new"
ACKNOWLEDGED = "ack"
RESOLVED = "resolved"
IGNORED = "ignored"

OPEN_STATES = (NEW, ACKNOWLEDGED)
TARGET_STATES = (ACKNOWLEDGED, RESOLVED, IGNORED)

REOPENED_ACTIVITY = "reopened"
ACTIVITY_FOR_TARGET = {
    ACKNOWLEDGED: "acknowledged",
    RESOLVED: "resolved",
    IGNORED: "ignored",
}


@dataclass(frozen=True)
class TriagePlan:
    changed: bool = False
    issue_fields: dict[str, Any] = field(default_factory=dict)
    activity_kind: str = ""


def plan_triage(current_state: str, target_state: str, at: datetime) -> TriagePlan:
    if current_state == target_state:
        return TriagePlan()

    issue_fields: dict[str, Any] = {"triage_state": target_state}
    if target_state == RESOLVED:
        issue_fields["last_resolved_at"] = at

    if current_state == RESOLVED:
        activity_kind = REOPENED_ACTIVITY
    else:
        activity_kind = ACTIVITY_FOR_TARGET[target_state]

    return TriagePlan(
        changed=True,
        issue_fields=issue_fields,
        activity_kind=activity_kind,
    )
