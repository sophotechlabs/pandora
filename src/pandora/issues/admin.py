from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.issues.models import (
    Episode,
    GroupingRule,
    Issue,
    IssueActivity,
    SilenceLink,
)


@admin.register(Issue)
class IssueAdmin(ModelAdmin):
    list_display = (
        "title",
        "project",
        "level",
        "source_state",
        "triage_state",
        "event_count",
        "open_episode_count",
        "first_seen",
        "last_seen",
    )
    list_filter = ("triage_state", "source_state", "level", "project", "environment")
    search_fields = ("title", "culprit", "fingerprint_hash")
    list_select_related = ("project",)


@admin.register(Episode)
class EpisodeAdmin(ModelAdmin):
    list_display = (
        "am_fingerprint",
        "issue",
        "environment",
        "starts_at",
        "ends_at",
        "delivery_count",
        "last_delivery_at",
    )
    list_filter = ("project", "environment")
    list_select_related = ("issue",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GroupingRule)
class GroupingRuleAdmin(ModelAdmin):
    list_display = ("priority", "project", "alertname_regex", "mode", "active")
    list_filter = ("mode", "active", "project")


@admin.register(IssueActivity)
class IssueActivityAdmin(ModelAdmin):
    list_display = ("issue", "kind", "actor", "at")
    list_filter = ("kind",)
    list_select_related = ("issue",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SilenceLink)
class SilenceLinkAdmin(ModelAdmin):
    list_display = ("issue", "am_silence_id", "created_at", "expires_at")
    list_select_related = ("issue",)
