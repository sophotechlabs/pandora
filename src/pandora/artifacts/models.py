from __future__ import annotations

from django.db import models
from django.utils import timezone

from pandora.core.models import Project


class FileKind(models.TextChoices):
    SOURCE_MAP = "source_map", "Source map"
    MINIFIED = "minified", "Minified source"
    SOURCE = "source", "Original source"


class ArtifactBundle(models.Model):
    """One upload, addressed by the debug id the tooling injected.

    Debug ids are the modern mechanism and they are simpler than the legacy
    path: the bundler plugin writes `//# debugId=` into the minified file and
    the same id into the map, the SDK reports it in `debug_meta`, and the server
    looks the bundle up by id. Release and dist become a weak association.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="artifact_bundles",
    )
    debug_id = models.CharField(max_length=64)
    release = models.CharField(max_length=250, blank=True, default="")
    dist = models.CharField(max_length=100, blank=True, default="")
    uploaded_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "debug_id"],
                name="artifacts_bundle_debug_id_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["last_used_at"], name="artifacts_bundle_used"),
        ]
        ordering = ("-uploaded_at",)

    def __str__(self) -> str:
        return f"{self.debug_id[:12]} ({self.release or 'no release'})"


class BundleFile(models.Model):
    bundle = models.ForeignKey(
        ArtifactBundle,
        on_delete=models.CASCADE,
        related_name="files",
    )
    path = models.CharField(max_length=500)
    kind = models.CharField(
        max_length=16,
        choices=FileKind.choices,
        default=FileKind.SOURCE_MAP,
    )
    blob = models.FileField(upload_to="artifacts/")
    size = models.PositiveBigIntegerField(default=0)
    sha1 = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["bundle", "kind"], name="artifacts_file_kind"),
        ]
        ordering = ("path",)

    def __str__(self) -> str:
        return f"{self.path} ({self.kind})"
