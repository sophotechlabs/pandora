from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import HttpRequest

from pandora.people.models import AuditEntry

SIGN_IN = "auth.sign-in"
SIGN_OUT = "auth.sign-out"
TRIAGE = "issue.triage"
SNOOZE = "issue.snooze"
DELETE_OCCURRENCE = "issue.occurrence-delete"
REPLAY = "ingest.replay"
SILENCE = "issue.silence"
ASSIGN = "issue.assign"
CONFIG = "config.apply"
REDACT = "scrub.redact"


def record(
    actor: str,
    action: str,
    target: str = "",
    data: dict[str, Any] | None = None,
) -> AuditEntry:
    return AuditEntry.objects.create(
        actor=actor,
        action=action,
        target=str(target)[:200],
        data=data or {},
    )


def from_request(
    request: HttpRequest,
    action: str,
    target: str = "",
    data: dict[str, Any] | None = None,
) -> AuditEntry:
    actor = ""
    if getattr(request.user, "is_authenticated", False):
        actor = request.user.get_username()
    return record(actor, action, target, data)


def prune(before: datetime) -> int:
    deleted, _ = AuditEntry.objects.filter(at__lt=before).delete()
    return deleted
