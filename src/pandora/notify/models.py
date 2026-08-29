from __future__ import annotations

from django.db import models

from pandora.core.models import Project
from pandora.issues.models import Issue, Level

NEW = "issue.new"
REGRESSION = "issue.regression"
UNSNOOZED = "issue.unsnoozed"
MILESTONE = "issue.milestone"
RESOLVED = "issue.resolved"

EVENTS = (NEW, REGRESSION, UNSNOOZED, MILESTONE, RESOLVED)
DEFAULT_EVENTS = [NEW, REGRESSION, UNSNOOZED]


class DestinationKind(models.TextChoices):
    WEBHOOK = "webhook", "Webhook"
    EMAIL = "email", "Email"
    SLACK = "slack", "Slack"
    DISCORD = "discord", "Discord"
    TEAMS = "teams", "Microsoft Teams"


class DeliveryState(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


def default_events() -> list[str]:
    return list(DEFAULT_EVENTS)


class Destination(models.Model):
    name = models.CharField(max_length=100)
    kind = models.CharField(
        max_length=16,
        choices=DestinationKind.choices,
        default=DestinationKind.WEBHOOK,
    )
    target = models.TextField(
        help_text="Webhook or chat URL, or a comma-separated list of email addresses",
    )
    secret = models.CharField(max_length=200, blank=True, default="")
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="destinations",
        null=True,
        blank=True,
    )
    events = models.JSONField(default=default_events, blank=True)
    min_level = models.CharField(
        max_length=16,
        choices=Level.choices,
        default=Level.WARNING,
    )
    digest_seconds = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["enabled"], name="notify_dest_enabled"),
        ]
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"


class Delivery(models.Model):
    destination = models.ForeignKey(
        Destination,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event = models.CharField(max_length=32)
    payload = models.JSONField(default=dict, blank=True)
    state = models.CharField(
        max_length=8,
        choices=DeliveryState.choices,
        default=DeliveryState.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    send_after = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["state", "created_at"],
                name="notify_delivery_state",
            ),
        ]
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.event} to {self.destination_id} ({self.state})"
