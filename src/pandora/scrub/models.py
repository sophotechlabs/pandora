from __future__ import annotations

from django.db import models

from pandora.core.models import Project
from pandora.scrub import inbound


class RuleAction(models.TextChoices):
    REMOVE = "remove", "Remove"
    MASK = "mask", "Mask"


class ScrubRule(models.Model):
    name = models.CharField(max_length=100)
    path = models.CharField(max_length=500)
    action = models.CharField(
        max_length=16,
        choices=RuleAction.choices,
        default=RuleAction.REMOVE,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="scrub_rules",
        null=True,
        blank=True,
    )
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["active"], name="scrub_rule_active"),
        ]
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.path})"


class DropRule(models.Model):
    name = models.CharField(max_length=100)
    field = models.CharField(max_length=100)
    pattern = models.CharField(max_length=500)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="drop_rules",
        null=True,
        blank=True,
    )
    active = models.BooleanField(default=True)
    dropped = models.PositiveBigIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["active"], name="scrub_drop_active"),
        ]
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.field}~{self.pattern})"


class InboundFilter(models.Model):
    """One of Sentry's standard filters, switched on for a project.

    The patterns are built in and the row is the switch, because the whole
    value of these filters is that nobody has to write the pattern for
    "a Chrome extension threw this" a second time.
    """

    KIND_CHOICES = tuple(
        (kind, kind.replace("_", " ").capitalize()) for kind in inbound.KINDS
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="inbound_filters",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    options = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    dropped = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "kind"],
                name="scrub_inbound_project_kind_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["active"], name="scrub_inbound_active"),
        ]
        ordering = ("kind",)

    def __str__(self) -> str:
        return self.kind
