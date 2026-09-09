from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction

from pandora.issues import hooks
from pandora.issues.models import (
    ActivityKind,
    Issue,
    IssueActivity,
    Subscription,
    SubscriptionReason,
)

BODY_MAX = 4000


class CommentError(ValueError):
    pass


def add(
    issue: Issue,
    user: User,
    body: str,
    at: datetime,
) -> IssueActivity:
    """Leave a note on an issue, and start watching it.

    The note is an activity row rather than a table of its own, so it lands in
    the same trail as the state changes it is about — which is the order a
    person reads an incident in.
    """
    text = body.strip()[:BODY_MAX]
    if not text:
        raise CommentError("a comment needs something in it")
    with transaction.atomic():
        activity = IssueActivity.objects.create(
            issue=issue,
            kind=ActivityKind.COMMENTED,
            actor=user.get_username(),
            at=at,
            data={"body": text},
        )
        subscribe(issue, user, SubscriptionReason.COMMENTED, at)
    hooks.fire("PANDORA_COMMENT_HOOKS", issue, user.get_username(), text)
    return activity


def subscribe(
    issue: Issue,
    user: User,
    reason: str,
    at: datetime,
) -> Subscription:
    subscription, _ = Subscription.objects.get_or_create(
        issue=issue,
        user=user,
        defaults={"reason": reason, "at": at},
    )
    return subscription


def watch(issue: Issue, user: User, at: datetime) -> Subscription:
    """A person saying, in words, that they want to hear about this."""
    subscription, created = Subscription.objects.get_or_create(
        issue=issue,
        user=user,
        defaults={"reason": SubscriptionReason.MANUAL, "at": at},
    )
    if not created and not subscription.active:
        subscription.active = True
        subscription.reason = SubscriptionReason.MANUAL
        subscription.save(update_fields=["active", "reason"])
    return subscription


def unwatch(issue: Issue, user: User) -> bool:
    updated = Subscription.objects.filter(issue=issue, user=user, active=True).update(
        active=False
    )
    return bool(updated)


def watching(issue: Issue, user: Any) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return Subscription.objects.filter(issue=issue, user=user, active=True).exists()


def subscribers(issue: Issue) -> list[Subscription]:
    return list(
        Subscription.objects.filter(issue=issue, active=True).select_related("user")
    )


def addresses(issue: Issue) -> list[str]:
    found = {
        subscription.user.email.strip()
        for subscription in subscribers(issue)
        if subscription.user.email.strip()
    }
    return sorted(found)


def comments(issue: Issue) -> Iterable[IssueActivity]:
    return issue.activities.filter(kind=ActivityKind.COMMENTED)
