from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.people.models import (
    Assignment,
    AuditEntry,
    Membership,
    OwnershipRule,
    Team,
)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Team)
class TeamAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    filter_horizontal = ("projects",)
    inlines = (MembershipInline,)


@admin.register(Membership)
class MembershipAdmin(ModelAdmin):
    list_display = ("user", "team", "role")
    list_filter = ("role", "team")
    search_fields = ("user__username", "team__name")


@admin.register(OwnershipRule)
class OwnershipRuleAdmin(ModelAdmin):
    list_display = ("name", "field", "pattern", "team", "user", "ordering", "active")
    list_filter = ("active", "field", "project")
    search_fields = ("name", "pattern")


@admin.register(Assignment)
class AssignmentAdmin(ModelAdmin):
    list_display = ("issue", "user", "team", "rule", "assigned_at")
    list_filter = ("team",)


@admin.register(AuditEntry)
class AuditEntryAdmin(ModelAdmin):
    list_display = ("at", "actor", "action", "target")
    list_filter = ("action",)
    search_fields = ("actor", "target")
    readonly_fields = ("actor", "action", "target", "data", "at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
