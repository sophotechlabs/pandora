from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

from pandora.core.models import Project, TokenSource


class EnvelopeState(models.TextChoices):
    PENDING = "pending", "Pending"
    CLAIMED = "claimed", "Claimed"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class RawEnvelope(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="envelopes",
    )
    source = models.CharField(max_length=8, choices=TokenSource.choices)
    environment = models.CharField(max_length=100, blank=True, default="")
    payload = models.JSONField()
    received_at = models.DateTimeField(default=timezone.now)
    state = models.CharField(
        max_length=8,
        choices=EnvelopeState.choices,
        default=EnvelopeState.PENDING,
    )
    error = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["state", "received_at"],
                name="ingest_env_state_recv",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source} envelope {self.pk} ({self.state})"


class ProcessedEvent(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="processed_events",
    )
    event_id = models.CharField(max_length=64)
    issue = models.ForeignKey(
        "issues.Issue",
        on_delete=models.SET_NULL,
        related_name="processed_events",
        null=True,
        blank=True,
    )
    seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "event_id"],
                name="ingest_processed_event_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["seen_at"], name="ingest_processed_seen"),
        ]

    def __str__(self) -> str:
        return self.event_id


class MonitorStatus(models.TextChoices):
    OK = "ok", "Ok"
    IN_PROGRESS = "in_progress", "In progress"
    MISSED = "missed", "Missed"
    ERROR = "error", "Error"
    TIMED_OUT = "timed_out", "Timed out"


class Monitor(models.Model):
    """The thing that did not happen, which Alertmanager only partly covers.

    A row with a schedule, a margin, a max runtime and a last-seen. A sweep marks
    the ones that missed. Sentry charges per active monitor; there is no
    per-monitor cost here.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="monitors",
    )
    slug = models.SlugField(max_length=100)
    name = models.CharField(max_length=200, blank=True, default="")
    interval_minutes = models.PositiveIntegerField(default=60)
    margin_minutes = models.PositiveIntegerField(default=5)
    max_runtime_minutes = models.PositiveIntegerField(default=30)
    environment = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=MonitorStatus.choices,
        default=MonitorStatus.OK,
    )
    last_check_in = models.DateTimeField(null=True, blank=True)
    last_started = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "slug"], name="ingest_monitor_slug_uq"
            ),
        ]
        indexes = [
            models.Index(fields=["active", "status"], name="ingest_monitor_active"),
        ]
        ordering = ("slug",)

    def __str__(self) -> str:
        return f"{self.slug} every {self.interval_minutes}m ({self.status})"

    @property
    def due_after(self) -> timedelta:
        return timedelta(minutes=self.interval_minutes + self.margin_minutes)

    @property
    def runtime_limit(self) -> timedelta:
        return timedelta(minutes=self.max_runtime_minutes)


class IngestQuota(models.Model):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="quotas",
        null=True,
        blank=True,
    )
    limit = models.PositiveIntegerField()
    window_seconds = models.PositiveIntegerField(default=60)
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["active"], name="ingest_quota_active"),
        ]
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.limit}/{self.window_seconds}s)"


class IngestCounter(models.Model):
    key = models.CharField(max_length=200)
    bucket = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "bucket"],
                name="ingest_counter_key_bucket_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["bucket"], name="ingest_counter_bucket"),
        ]

    def __str__(self) -> str:
        return f"{self.key}@{self.bucket:%Y-%m-%dT%H:%M}Z x{self.count}"
