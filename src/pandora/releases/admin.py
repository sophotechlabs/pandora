from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.releases.models import (
    CodeMapping,
    Commit,
    CommitFile,
    Deploy,
    Release,
    ReleaseEnvironment,
    Repository,
    Resolution,
)


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
    list_display = (
        "identifier",
        "release",
        "environment",
        "state",
        "started_at",
        "finished_at",
    )
    list_filter = ("state", "environment", "project")
    list_select_related = ("project", "release")
    search_fields = ("identifier", "release__version", "name")


@admin.register(Resolution)
class ResolutionAdmin(ModelAdmin):
    list_display = ("issue", "release", "in_next", "actor", "at")
    list_filter = ("in_next",)
    list_select_related = ("issue", "release")


class CommitFileInline(admin.TabularInline):
    model = CommitFile
    extra = 0


@admin.register(Repository)
class RepositoryAdmin(ModelAdmin):
    list_display = ("name", "provider", "project", "url", "active")
    list_filter = ("provider", "active", "project")
    list_select_related = ("project",)
    search_fields = ("name", "url")


@admin.register(Commit)
class CommitAdmin(ModelAdmin):
    list_display = ("key", "repository", "author_name", "author_email", "committed_at")
    list_filter = ("repository",)
    list_select_related = ("repository",)
    search_fields = ("key", "message", "author_email")
    inlines = (CommitFileInline,)


@admin.register(CodeMapping)
class CodeMappingAdmin(ModelAdmin):
    list_display = (
        "project",
        "repository",
        "stack_root",
        "source_root",
        "default_branch",
        "ordering",
        "active",
    )
    list_filter = ("active", "project", "repository")
    list_select_related = ("project", "repository")
