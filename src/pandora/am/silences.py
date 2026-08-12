from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from pandora.am import client as am_client
from pandora.issues.models import ActivityKind, Issue, IssueActivity, SilenceLink

CREATED_BY = "Pandora"
ISSUE_ROUTE = "ui:issue"

log = logging.getLogger(__name__)


class SilenceError(RuntimeError):
    pass


def build_matchers(issue: Issue) -> list[dict[str, Any]]:
    labels = issue.grouping_labels or {}
    matchers = [
        {
            "name": str(key),
            "value": str(value),
            "isRegex": False,
            "isEqual": True,
        }
        for key, value in sorted(labels.items())
    ]
    if not matchers:
        raise SilenceError(
            f"issue {issue.pk} kept no grouping labels — a silence built from it"
            " would match every alert"
        )
    return matchers


def issue_url(issue: Issue) -> str:
    path = reverse(ISSUE_ROUTE, args=[issue.pk])
    base = settings.PANDORA_BASE_URL.strip().rstrip("/")
    if not base:
        return path
    return f"{base}{path}"


def build_comment(issue: Issue) -> str:
    return f"Pandora issue #{issue.pk} — {issue.title} — {issue_url(issue)}"


def silence_issue(
    issue: Issue,
    duration: timedelta,
    *,
    actor: str = "",
    client: am_client.AlertmanagerClient | None = None,
    now: datetime | None = None,
) -> SilenceLink:
    matchers = build_matchers(issue)
    if client is None:
        client = am_client.from_settings()
    if now is None:
        now = timezone.now()
    created_by = actor
    if not created_by:
        created_by = CREATED_BY

    expires_at = now + duration
    silence_id = client.create_silence(
        matchers=matchers,
        starts_at=now,
        ends_at=expires_at,
        comment=build_comment(issue),
        created_by=created_by,
    )

    with transaction.atomic():
        link = SilenceLink.objects.create(
            issue=issue,
            am_silence_id=silence_id,
            created_at=now,
            expires_at=expires_at,
        )
        IssueActivity.objects.create(
            issue=issue,
            kind=ActivityKind.SILENCED,
            actor=actor,
            at=now,
            data={
                "silence_id": silence_id,
                "expires_at": expires_at.isoformat(),
                "matchers": matchers,
            },
        )
    log.info(
        "silenced issue %s in alertmanager as %s until %s",
        issue.pk,
        silence_id,
        expires_at.isoformat(),
    )
    return link


def expire_silence(
    link: SilenceLink,
    *,
    actor: str = "",
    client: am_client.AlertmanagerClient | None = None,
    now: datetime | None = None,
) -> None:
    if client is None:
        client = am_client.from_settings()
    if now is None:
        now = timezone.now()

    client.delete_silence(link.am_silence_id)

    with transaction.atomic():
        IssueActivity.objects.create(
            issue=link.issue,
            kind=ActivityKind.UNSILENCED,
            actor=actor,
            at=now,
            data={"silence_id": link.am_silence_id},
        )
        link.delete()
    log.info("expired alertmanager silence %s", link.am_silence_id)
