from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.scrub.models import DropRule, InboundFilter, ScrubRule


@admin.register(ScrubRule)
class ScrubRuleAdmin(ModelAdmin):
    list_display = ("name", "path", "action", "project", "active")
    list_filter = ("active", "action", "project")
    search_fields = ("name", "path")


@admin.register(DropRule)
class DropRuleAdmin(ModelAdmin):
    list_display = ("name", "field", "pattern", "project", "active", "dropped")
    list_filter = ("active", "field", "project")
    search_fields = ("name", "pattern")
    readonly_fields = ("dropped",)


@admin.register(InboundFilter)
class InboundFilterAdmin(ModelAdmin):
    list_display = ("kind", "project", "active", "dropped")
    list_filter = ("active", "kind", "project")
    readonly_fields = ("dropped",)
