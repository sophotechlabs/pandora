from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.notify.models import Delivery, Destination


@admin.register(Destination)
class DestinationAdmin(ModelAdmin):
    list_display = ("name", "kind", "project", "min_level", "digest_seconds", "enabled")
    list_filter = ("enabled", "kind", "project")
    search_fields = ("name", "target")


@admin.register(Delivery)
class DeliveryAdmin(ModelAdmin):
    list_display = ("event", "destination", "issue", "state", "attempts", "created_at")
    list_filter = ("state", "event", "destination")
    readonly_fields = ("payload", "attempts", "error", "created_at", "sent_at")
