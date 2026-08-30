from __future__ import annotations

from django.db import models
from django.utils import timezone

from pandora.core.models import Project

TAG_VALUE_CAP = 100
TAG_OVERFLOW_VALUE = "<other>"


class Level(models.TextChoices):
    DEBUG = "debug", "Debug"
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"
    FATAL = "fatal", "Fatal"


class SourceState(models.TextChoices):
    FIRING = "firing", "Firing"
    RESOLVED = "resolved", "Resolved"


class TriageState(models.TextChoices):
    NEW = "new", "New"
    ACKNOWLEDGED = "ack", "Acknowledged"
    RESOLVED = "resolved", "Resolved"
    IGNORED = "ignored", "Ignored"


class ActivityKind(models.TextChoices):
    CREATED = "created", "Created"
    SNOOZED = "snoozed", "Snoozed"
    UNSNOOZED = "unsnoozed", "Woke up"
    REGRESSION = "regression", "Regression"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"
    IGNORED = "ignored", "Ignored"
    REOPENED = "reopened", "Reopened"
    MERGED = "merged", "Merged"
    UNMERGED = "unmerged", "Unmerged"
    SILENCED = "silenced", "Silenced"
    UNSILENCED = "unsilenced", "Unsilenced"
    REGROUPED = "regrouped", "Regrouped"


class GroupingMode(models.TextChoices):
    DENYLIST = "denylist", "Denylist"
    ALLOWLIST = "allowlist", "Allowlist"


class GroupingSource(models.TextChoices):
    RULE = "rule", "A grouping rule"
    DEFAULT = "default", "The built-in denylist"
    STACK = "stack", "The exception's stack signature"
    LOGENTRY = "logentry", "The log message template"
    MESSAGE = "message", "The message"
    CLIENT = "client", "A fingerprint the client declared"


class Issue(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="issues",
    )
    fingerprint_hash = models.CharField(max_length=64)
    fingerprint = models.JSONField(default=list, blank=True)
    grouping_labels = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=500)
    culprit = models.CharField(max_length=500, blank=True, default="")
    level = models.CharField(
        max_length=16,
        choices=Level.choices,
        default=Level.ERROR,
    )
    environment = models.CharField(max_length=100, blank=True, default="")
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    event_count = models.PositiveBigIntegerField(default=0)
    open_episode_count = models.PositiveIntegerField(default=0)
    source_state = models.CharField(
        max_length=16,
        choices=SourceState.choices,
        null=True,
        blank=True,
    )
    triage_state = models.CharField(
        max_length=16,
        choices=TriageState.choices,
        default=TriageState.NEW,
    )
    last_resolved_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    snoozed_past_count = models.PositiveBigIntegerField(null=True, blank=True)
    grouping_source = models.CharField(
        max_length=16,
        choices=GroupingSource.choices,
        blank=True,
        default="",
    )
    search_text = models.TextField(blank=True, default="")
    grouping_rule = models.ForeignKey(
        "issues.GroupingRule",
        on_delete=models.SET_NULL,
        related_name="issues",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "fingerprint_hash"],
                name="issues_issue_fingerprint_uq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "triage_state", "-last_seen"],
                name="issues_issue_proj_triage",
            ),
            models.Index(
                fields=["project", "source_state"],
                name="issues_issue_proj_source",
            ),
            models.Index(
                fields=["snoozed_until"],
                name="issues_issue_snoozed",
            ),
        ]
        ordering = ("-last_seen",)

    def __str__(self) -> str:
        return self.title


class IssueEnvironment(models.Model):
    issue = models.ForeignKey(
        Issue,
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
                fields=["issue", "name"],
                name="issues_issue_environment_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["name"], name="issues_issue_env_name"),
        ]
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.issue_id} in {self.name or 'no environment'}"


class Episode(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    am_fingerprint = models.CharField(max_length=64)
    labels = models.JSONField(default=dict, blank=True)
    environment = models.CharField(max_length=100, blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    delivery_count = models.PositiveIntegerField(default=1)
    last_delivery_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "am_fingerprint", "starts_at"],
                name="issues_episode_identity_uq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["issue", "-starts_at"],
                name="issues_episode_issue_start",
            ),
            models.Index(
                fields=["project", "am_fingerprint"],
                condition=models.Q(ends_at__isnull=True),
                name="issues_episode_open",
            ),
            models.Index(
                fields=["issue", "starts_at"],
                condition=models.Q(ends_at__isnull=True),
                name="issues_episode_issue_open",
            ),
        ]
        ordering = ("-starts_at",)

    def __str__(self) -> str:
        return f"{self.am_fingerprint}@{self.starts_at:%Y-%m-%dT%H:%M:%SZ}"


class IssueActivity(models.Model):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    kind = models.CharField(max_length=32, choices=ActivityKind.choices)
    actor = models.CharField(max_length=150, blank=True, default="")
    at = models.DateTimeField(default=timezone.now)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["issue", "-at"], name="issues_activity_issue_at"),
        ]
        ordering = ("-at",)

    def __str__(self) -> str:
        return f"{self.kind} on issue {self.issue_id}"


class GroupingRule(models.Model):
    priority = models.IntegerField(default=100)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="grouping_rules",
        null=True,
        blank=True,
    )
    alertname_regex = models.CharField(max_length=200, blank=True, default="")
    mode = models.CharField(
        max_length=16,
        choices=GroupingMode.choices,
        default=GroupingMode.DENYLIST,
    )
    labels = models.JSONField(default=list, blank=True)
    conditions = models.JSONField(default=dict, blank=True)
    fingerprint = models.JSONField(default=list, blank=True)
    title_template = models.CharField(max_length=500, blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["active", "priority"],
                name="issues_rule_active_prio",
            ),
        ]
        ordering = ("priority", "id")

    def __str__(self) -> str:
        return f"{self.priority} {self.mode} {self.alertname_regex or '*'}"


class UserReport(models.Model):
    """What a person typed about an error that already happened.

    Small, a form, and it attaches to an event id that already exists. Accept it
    and render it; the widget that collects it is the SDK's job.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="user_reports",
    )
    issue = models.ForeignKey(
        "issues.Issue",
        on_delete=models.CASCADE,
        related_name="user_reports",
        null=True,
        blank=True,
    )
    event_id = models.CharField(max_length=64)
    name = models.CharField(max_length=200, blank=True, default="")
    email = models.CharField(max_length=254, blank=True, default="")
    comments = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["project", "event_id"], name="issues_report_event"),
            models.Index(fields=["-received_at"], name="issues_report_received"),
        ]
        ordering = ("-received_at",)

    def __str__(self) -> str:
        return f"{self.name or 'someone'} on {self.event_id[:12]}"


class IssueAlias(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="issue_aliases",
    )
    fingerprint_hash = models.CharField(max_length=64)
    issue = models.ForeignKey(
        "issues.Issue",
        on_delete=models.CASCADE,
        related_name="aliases",
    )
    title = models.CharField(max_length=500, blank=True, default="")
    grouping_labels = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "fingerprint_hash"],
                name="issues_alias_fingerprint_uq",
            ),
        ]
        ordering = ("fingerprint_hash",)

    def __str__(self) -> str:
        return f"{self.fingerprint_hash[:12]} -> {self.issue_id}"


class SavedView(models.Model):
    name = models.CharField(max_length=100, unique=True)
    query = models.CharField(max_length=500, blank=True, default="")
    sort = models.CharField(max_length=32, blank=True, default="")
    ordering = models.IntegerField(default=100)
    created_by = models.CharField(max_length=150, blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("ordering", "name")

    def __str__(self) -> str:
        return self.name


class PathRule(models.Model):
    name = models.CharField(max_length=100)
    pattern = models.CharField(max_length=500)
    replacement = models.CharField(max_length=500, blank=True, default="")
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="path_rules",
        null=True,
        blank=True,
    )
    ordering = models.IntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["active", "ordering"], name="issues_path_active_prio"),
        ]
        ordering = ("ordering", "id")

    def __str__(self) -> str:
        return f"{self.name}: {self.pattern} -> {self.replacement}"


class HourlyStat(models.Model):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="hourly_stats",
    )
    hour = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "hour"],
                name="issues_hourly_issue_hour_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["issue", "-hour"], name="issues_hourly_issue_hour"),
        ]

    def __str__(self) -> str:
        return f"{self.hour:%Y-%m-%dT%H}Z x{self.count}"


class TagStat(models.Model):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="tag_stats",
    )
    key = models.CharField(max_length=200)
    value = models.CharField(max_length=500)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "key", "value"],
                name="issues_tag_issue_key_value_uq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["issue", "key", "-count"],
                name="issues_tag_issue_key_count",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key}={self.value} x{self.count}"


class SilenceLink(models.Model):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="silences",
    )
    am_silence_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "am_silence_id"],
                name="issues_silence_issue_am_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="issues_silence_expires"),
        ]

    def __str__(self) -> str:
        return self.am_silence_id
