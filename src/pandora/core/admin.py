from __future__ import annotations

import secrets

from django.contrib import admin, messages
from django.db.models import Count, Q
from unfold.admin import ModelAdmin

from pandora.core.models import DsnKey, IngestToken, Project
from pandora.issues.models import SourceState

TOKEN_BYTES = 32
DSN_KEY_BYTES = 16
SHOW_ONCE = "{name} token: {value} — copy it now, it is not shown again"


def _new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _new_public_key() -> str:
    return secrets.token_hex(DSN_KEY_BYTES)


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ("slug", "name", "issue_total", "firing_total", "created_at")
    search_fields = ("slug", "name")
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                issue_total=Count("issues", distinct=True),
                firing_total=Count(
                    "issues",
                    filter=Q(issues__source_state=SourceState.FIRING),
                    distinct=True,
                ),
            )
        )

    @admin.display(description="Issues", ordering="issue_total")
    def issue_total(self, obj):
        return obj.issue_total

    @admin.display(description="Firing", ordering="firing_total")
    def firing_total(self, obj):
        return obj.firing_total


@admin.register(DsnKey)
class DsnKeyAdmin(ModelAdmin):
    list_display = ("project", "public_key", "envelope_path", "active", "created_at")
    list_filter = ("active", "project")
    list_select_related = ("project",)
    search_fields = ("public_key",)
    readonly_fields = ("public_key", "created_at")
    fields = ("project", "active", "public_key", "created_at")

    @admin.display(description="Envelope path")
    def envelope_path(self, obj):
        return f"/api/{obj.project_id}/envelope/"

    def save_model(self, request, obj, form, change):
        if not obj.public_key:
            obj.public_key = _new_public_key()
        super().save_model(request, obj, form, change)


@admin.register(IngestToken)
class IngestTokenAdmin(ModelAdmin):
    list_display = (
        "project",
        "name",
        "source",
        "scope",
        "environment",
        "token_preview",
        "active",
    )
    list_filter = ("source", "scope", "active", "project")
    list_select_related = ("project",)
    search_fields = ("name",)
    readonly_fields = ("token_preview", "created_at")
    fields = (
        "project",
        "name",
        "source",
        "scope",
        "environment",
        "active",
        "token_preview",
        "created_at",
    )
    actions = ("regenerate",)

    @admin.display(description="Token")
    def token_preview(self, obj):
        if not obj.token:
            return "—"
        return f"{obj.token[:6]}…"

    def save_model(self, request, obj, form, change):
        issued = not obj.token
        if issued:
            obj.token = _new_token()
        super().save_model(request, obj, form, change)
        if issued:
            self._show_once(request, obj)

    @admin.action(description="Regenerate token")
    def regenerate(self, request, queryset):
        for token in queryset:
            token.token = _new_token()
            token.save(update_fields=["token"])
            self._show_once(request, token)

    def _show_once(self, request, token):
        self.message_user(
            request,
            SHOW_ONCE.format(name=token.name, value=token.token),
            messages.WARNING,
        )
