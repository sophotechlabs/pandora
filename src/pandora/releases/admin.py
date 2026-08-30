from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.releases.models import Deploy, Release, ReleaseEnvironment, Resolution


class ReleaseEnvironmentInline(admin.TabularInline):
    model = ReleaseEnvironment
    extra = 0
    readonly_fields = ("name", "first_seen", "last_seen", "event_count")


@admin.register(Release)
class ReleaseAdmin(ModelAdmin):
    list_display = ("version", "dist", "project", "parsed", "first_seen", "last_seen")
    list_filter = ("project", "parsed")
    list_select_related = ("project",)
    search_fields = ("version",)
    inlines = (ReleaseEnvironmentInline,)


@admin.register(Deploy)
class DeployAdmin(ModelAdmin):
    list_display = ("release", "environment", "state", "started_at", "finished_at")
    list_filter = ("state", "environment")
    list_select_related = ("release",)


@admin.register(Resolution)
class ResolutionAdmin(ModelAdmin):
    list_display = ("issue", "release", "in_next", "actor", "at")
    list_filter = ("in_next",)
    list_select_related = ("issue", "release")
