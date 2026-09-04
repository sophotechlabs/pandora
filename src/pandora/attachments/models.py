from __future__ import annotations

from django.db import models
from django.utils import timezone

from pandora.core.models import Project


class EventAttachment(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="event_attachments",
    )
    event_id = models.CharField(max_length=64)
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, blank=True, default="")
    attachment_type = models.CharField(max_length=64, blank=True, default="")
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    blob = models.FileField(upload_to="attachments/%Y/%m/%d")
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "event_id", "filename", "sha256"],
                name="attachments_event_file_uq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "event_id"],
                name="attachments_event_lookup",
            ),
            models.Index(
                fields=["received_at"],
                name="attachments_received",
            ),
        ]
        ordering = ("received_at", "pk")

    def __str__(self) -> str:
        return f"{self.event_id}/{self.filename}"
