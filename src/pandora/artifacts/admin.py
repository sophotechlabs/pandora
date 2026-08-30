from django.contrib import admin
from unfold.admin import ModelAdmin

from pandora.artifacts.models import ArtifactBundle, BundleFile


class BundleFileInline(admin.TabularInline):
    model = BundleFile
    extra = 0
    readonly_fields = ("path", "kind", "size", "sha1")


@admin.register(ArtifactBundle)
class ArtifactBundleAdmin(ModelAdmin):
    list_display = ("debug_id", "project", "release", "uploaded_at", "last_used_at")
    list_filter = ("project",)
    list_select_related = ("project",)
    search_fields = ("debug_id", "release")
    inlines = (BundleFileInline,)
