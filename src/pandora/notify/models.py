from __future__ import annotations

from django.db import models

from pandora.core.models import Project
from pandora.issues.models import Issue, Level

METRIC_FIRING = "metric.firing"
METRIC_RESOLVED = "metric.resolved"

NEW = "issue.new"
REGRESSION = "issue.regression"
UNSNOOZED = "issue.unsnoozed"
MILESTONE = "issue.milestone"
RESOLVED = "issue.resolved"
ESCALATING = "issue.escalating"
COMMENT = "issue.comment"

REPORT = "report.periodic"

EVENTS = (
    NEW,
    REGRESSION,
    UNSNOOZED,
    MILESTONE,
    RESOLVED,
    ESCALATING,
    COMMENT,
    REPORT,
)
DEFAULT_EVENTS = [NEW, REGRESSION, UNSNOOZED, ESCALATING]


class DestinationKind(models.TextChoices):
    WEBHOOK = "webhook", "Webhook"
    EMAIL = "email", "Email"
    SLACK = "slack", "Slack"
    DISCORD = "discord", "Discord"
    TEAMS = "teams", "Microsoft Teams"


class DeliveryState(models.TextChoices):
    PENDING = "pending", "Pending"
    SENDING = "sending", "Sending"
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


class MetricDataset(models.TextChoices):
    EVENTS = "events", "Events"
    ISSUES = "issues", "Issues seen"
    NEW_ISSUES = "new_issues", "Issues first seen"
    USERS = "users", "People first affected"
    CRASH_FREE = "crash_free", "Crash-free sessions (%)"
    THROUGHPUT = "throughput", "Transactions"
    LATENCY_P50 = "p50", "Median duration (ms)"
    LATENCY_P95 = "p95", "95th percentile duration (ms)"
    FAILURE_RATE = "failure_rate", "Failed transactions (%)"


class Comparison(models.TextChoices):
    ABOVE = "above", "Above"
    BELOW = "below", "Below"


class MonitorState(models.TextChoices):
    OK = "ok", "OK"
    FIRING = "firing", "Firing"
    NO_DATA = "no_data", "No data"


class MetricMonitor(models.Model):
    """A threshold over data already on disk, evaluated on a schedule.

    Sentry calls this a metric alert and gates it behind a plan. Here it is a
    row and a cron: the counters it reads are the ones the stream already keeps,
    and what it produces when it fires is an ordinary alert, so triage,
    silences, notifications and the alert-error join all work on it unchanged.
    """

    name = models.CharField(max_length=100)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="metric_monitors",
    )
    dataset = models.CharField(
        max_length=16,
        choices=MetricDataset.choices,
        default=MetricDataset.EVENTS,
    )
    query = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "An issue-stream query for the issue datasets, "
            "or a transaction name (with an optional *) for the performance ones"
        ),
    )
    environment = models.CharField(max_length=100, blank=True, default="")
    window_minutes = models.PositiveIntegerField(default=60)
    comparison = models.CharField(
        max_length=8,
        choices=Comparison.choices,
        default=Comparison.ABOVE,
    )
    threshold = models.FloatField(default=0)
    comparison_delta_minutes = models.PositiveIntegerField(default=0)
    severity = models.CharField(
        max_length=16,
        choices=Level.choices,
        default=Level.WARNING,
    )
    state = models.CharField(
        max_length=8,
        choices=MonitorState.choices,
        default=MonitorState.OK,
    )
    last_value = models.FloatField(null=True, blank=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                name="notify_metric_monitor_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["active"], name="notify_metric_active"),
        ]
        ordering = ("project__slug", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.dataset} {self.comparison} {self.threshold})"


class MetricRun(models.Model):
    monitor = models.ForeignKey(
        MetricMonitor,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    at = models.DateTimeField()
    value = models.FloatField()
    state = models.CharField(max_length=8, choices=MonitorState.choices)

    class Meta:
        indexes = [
            models.Index(fields=["monitor", "-at"], name="notify_metric_run_at"),
        ]
        ordering = ("-at", "-pk")

    def __str__(self) -> str:
        return f"{self.monitor_id} {self.value} {self.state}"
