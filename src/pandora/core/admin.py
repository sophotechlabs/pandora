from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.core.models import DsnKey, IngestToken, Project


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ("slug", "name", "created_at")
    search_fields = ("slug", "name")


@admin.register(DsnKey)
class DsnKeyAdmin(ModelAdmin):
    list_display = ("project", "public_key", "active", "created_at")
    list_filter = ("active", "project")


@admin.register(IngestToken)
class IngestTokenAdmin(ModelAdmin):
    list_display = ("project", "name", "source", "scope", "environment", "active")
    list_filter = ("source", "scope", "active", "project")
    search_fields = ("name",)
