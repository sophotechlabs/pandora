from __future__ import annotations

from django.db import models

from pandora.core.models import Project


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
