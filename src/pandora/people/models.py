from __future__ import annotations

from django.conf import settings
from django.db import models

from pandora.core.models import Project
from pandora.issues.models import Issue

OWNER = "owner"
MEMBER = "member"
VIEWER = "viewer"


class Role(models.TextChoices):
    OWNER = OWNER, "Owner"
    MEMBER = MEMBER, "Member"
    VIEWER = VIEWER, "Viewer"


ROLE_ORDER = {VIEWER: 0, MEMBER: 1, OWNER: 2}

ROLE_PERMISSIONS = {
    VIEWER: (),
    MEMBER: ("issues.change_issue",),
    OWNER: ("issues.change_issue", "ingest.change_rawenvelope"),
}


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    projects = models.ManyToManyField(Project, related_name="teams", blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"], name="people_membership_uq"
            ),
        ]
        ordering = ("team__name", "user__username")

    def __str__(self) -> str:
        return f"{self.user} in {self.team} ({self.role})"


class OwnershipRule(models.Model):
    name = models.CharField(max_length=100)
    pattern = models.CharField(max_length=500)
    field = models.CharField(max_length=32, default="path")
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="ownership_rules",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ownership_rules",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="ownership_rules",
        null=True,
        blank=True,
    )
    ordering = models.IntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["active", "ordering"], name="people_rule_active"),
        ]
        ordering = ("ordering", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.field}:{self.pattern})"


class Assignment(models.Model):
    issue = models.OneToOneField(
        Issue, on_delete=models.CASCADE, related_name="assignment"
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="assignments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assignments",
        null=True,
        blank=True,
    )
    rule = models.ForeignKey(
        OwnershipRule,
        on_delete=models.SET_NULL,
        related_name="assignments",
        null=True,
        blank=True,
    )
    assigned_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.issue_id} to {self.user or self.team}"


class AuditEntry(models.Model):
    actor = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=200, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["-at"], name="people_audit_at"),
            models.Index(fields=["action"], name="people_audit_action"),
        ]
        ordering = ("-at", "-pk")

    def __str__(self) -> str:
        return f"{self.actor or 'pandora'} {self.action} {self.target}"
