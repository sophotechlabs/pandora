from __future__ import annotations

from django.db import models
from django.utils import timezone


class TokenSource(models.TextChoices):
    AM = "am", "Alertmanager"
    SDK = "sdk", "SDK"


class TokenScope(models.TextChoices):
    INGEST = "ingest", "Ingest"
    READ = "read", "Read"


class Project(models.Model):
    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.name


class DsnKey(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="dsn_keys",
    )
    public_key = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["project", "active"], name="core_dsnkey_proj_active"),
        ]

    def __str__(self) -> str:
        return f"{self.project.slug}/{self.public_key[:8]}"


class IngestToken(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tokens",
    )
    name = models.CharField(max_length=200)
    token = models.CharField(max_length=128, unique=True)
    source = models.CharField(
        max_length=8,
        choices=TokenSource.choices,
        default=TokenSource.AM,
    )
    scope = models.CharField(
        max_length=8,
        choices=TokenScope.choices,
        default=TokenScope.INGEST,
    )
    environment = models.CharField(max_length=100, blank=True, default="")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(
                fields=["source", "scope", "active"],
                name="core_token_src_scope",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project.slug}/{self.name}"
