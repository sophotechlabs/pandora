from __future__ import annotations

from collections.abc import Iterable
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
MERGE = "issue.merge"
UNMERGE = "issue.unmerge"
VIEW = "view.save"
DEPLOY = "release.deploy"
ARCHIVE = "events.archive"
CONFIG = "config.apply"
REDACT = "scrub.redact"


def record(
    actor: str,
    action: str,
    target: str = "",
    data: dict[str, Any] | None = None,
    *,
    project_ids: Iterable[int] = (),
) -> AuditEntry:
    entry = AuditEntry.objects.create(
        actor=actor,
        action=action,
        target=str(target)[:200],
        data=data or {},
    )
    entry.projects.add(*set(project_ids))
    return entry


def from_request(
    request: HttpRequest,
    action: str,
    target: str = "",
    data: dict[str, Any] | None = None,
    *,
    project_ids: Iterable[int] = (),
) -> AuditEntry:
    actor = ""
    if getattr(request.user, "is_authenticated", False):
        actor = request.user.get_username()
    return record(actor, action, target, data, project_ids=project_ids)


def prune(before: datetime) -> int:
    deleted, _ = AuditEntry.objects.filter(at__lt=before).delete()
    return deleted
