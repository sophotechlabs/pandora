from __future__ import annotations

from django.db import models
from django.utils import timezone

from pandora.core.models import Project
from pandora.issues.models import Issue


class DeployState(models.TextChoices):
    STARTED = "started", "Started"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    TIMED_OUT = "timed_out", "Timed out"


class Release(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="releases",
    )
    version = models.CharField(max_length=250)
    dist = models.CharField(max_length=100, blank=True, default="")
    sort_key = models.CharField(max_length=64, blank=True, default="")
    parsed = models.BooleanField(default=False)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    event_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version", "dist"],
                name="releases_release_version_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "sort_key"], name="releases_proj_sort"),
        ]
        ordering = ("sort_key", "first_seen")

    def __str__(self) -> str:
        if self.dist:
            return f"{self.version} ({self.dist})"
        return self.version


class ReleaseEnvironment(models.Model):
    release = models.ForeignKey(
        Release,
        on_delete=models.CASCADE,
        related_name="environments",
    )
    name = models.CharField(max_length=100, blank=True, default="")
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    event_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "name"],
                name="releases_release_environment_uq",
            ),
        ]
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.release_id} in {self.name or 'no environment'}"


class Deploy(models.Model):
    release = models.ForeignKey(
        Release,
        on_delete=models.CASCADE,
        related_name="deploys",
    )
    environment = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(
        max_length=16,
        choices=DeployState.choices,
        default=DeployState.STARTED,
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    name = models.CharField(max_length=200, blank=True, default="")
    url = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["-started_at"], name="releases_deploy_started"),
        ]
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"{self.release_id} to {self.environment or 'everywhere'} ({self.state})"


class SessionBucket(models.Model):
    """Crash-free sessions, counted rather than recorded.

    Its own aggregated table, not the event store: a session is a counter with
    a status, sampling never touches it, and nobody bills for it — so the shape
    that suits an event suits it badly.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="session_buckets",
    )
    version = models.CharField(max_length=250, blank=True, default="")
    environment = models.CharField(max_length=100, blank=True, default="")
    hour = models.DateTimeField()
    sort_key = models.CharField(max_length=64, blank=True, default="")
    parsed = models.BooleanField(default=False)
    sessions = models.PositiveBigIntegerField(default=0)
    crashed = models.PositiveBigIntegerField(default=0)
    errored = models.PositiveBigIntegerField(default=0)
    abnormal = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version", "environment", "hour"],
                name="releases_session_bucket_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "-hour"], name="releases_session_hour"),
        ]
        ordering = ("-hour",)

    def __str__(self) -> str:
        return (
            f"{self.version or 'no release'} {self.hour:%Y-%m-%dT%H}Z x{self.sessions}"
        )


class Resolution(models.Model):
    """The release boundary a resolve was made against.

    *Resolved in the next release* is a promise about the future, and the only
    honest way to keep it is to write down which release the promise was made
    at and compare every later event's release to it.
    """

    issue = models.OneToOneField(
        Issue,
        on_delete=models.CASCADE,
        related_name="resolution",
    )
    release = models.ForeignKey(
        Release,
        on_delete=models.SET_NULL,
        related_name="resolutions",
        null=True,
        blank=True,
    )
    sort_key = models.CharField(max_length=64, blank=True, default="")
    in_next = models.BooleanField(default=False)
    actor = models.CharField(max_length=150, blank=True, default="")
    at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        if self.in_next:
            return f"{self.issue_id} resolved in the next release"
        return f"{self.issue_id} resolved in {self.release_id}"
