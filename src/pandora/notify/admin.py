from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.notify.models import Delivery, Destination, MetricMonitor, MetricRun


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


class MetricRunInline(admin.TabularInline):
    model = MetricRun
    extra = 0
    readonly_fields = ("at", "value", "state")
    max_num = 0


@admin.register(MetricMonitor)
class MetricMonitorAdmin(ModelAdmin):
    list_display = (
        "name",
        "project",
        "dataset",
        "comparison",
        "threshold",
        "window_minutes",
        "state",
        "last_value",
        "active",
    )
    list_filter = ("active", "state", "dataset", "project")
    list_select_related = ("project",)
    search_fields = ("name", "query")
    readonly_fields = ("state", "last_value", "last_evaluated_at", "last_triggered_at")
    inlines = (MetricRunInline,)
