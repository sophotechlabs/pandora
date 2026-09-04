from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.attachments.models import EventAttachment


@admin.register(EventAttachment)
class EventAttachmentAdmin(ModelAdmin):
    list_display = (
        "filename",
        "project",
        "event_id",
        "content_type",
        "size",
        "received_at",
    )
    list_filter = ("project", "content_type")
    list_select_related = ("project",)
    search_fields = ("filename", "event_id", "sha256")
    readonly_fields = (
        "project",
        "event_id",
        "filename",
        "content_type",
        "attachment_type",
        "size",
        "sha256",
        "blob",
        "received_at",
    )
