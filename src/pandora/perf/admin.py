from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.perf.models import SpanSummary, TransactionBucket


class SpanSummaryInline(admin.TabularInline):
    model = SpanSummary
    extra = 0
    readonly_fields = ("op", "count", "duration_sum")


@admin.register(TransactionBucket)
class TransactionBucketAdmin(ModelAdmin):
    list_display = (
        "transaction",
        "project",
        "environment",
        "release",
        "hour",
        "count",
        "failures",
        "duration_max",
    )
    list_filter = ("project", "environment")
    list_select_related = ("project",)
    search_fields = ("transaction",)
    readonly_fields = (
        "count",
        "failures",
        "duration_sum",
        "duration_max",
        "histogram",
    )
    inlines = (SpanSummaryInline,)
