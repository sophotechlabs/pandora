from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.ingest.models import IngestQuota


@admin.register(IngestQuota)
class IngestQuotaAdmin(ModelAdmin):
    list_display = ("name", "project", "limit", "window_seconds", "active")
    list_filter = ("active", "project")
    search_fields = ("name",)
