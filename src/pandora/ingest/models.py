from __future__ import annotations

from django.db import models
from django.utils import timezone

from pandora.core.models import Project, TokenSource


class EnvelopeState(models.TextChoices):
    PENDING = "pending", "Pending"
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
