from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import OuterRef, Prefetch, Subquery
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from pandora.am import client as am_client
from pandora.am import silences
from pandora.issues import actions, components, detail, regroup, sparkline, triage
from pandora.issues.models import (
    Episode,
    GroupingRule,
    HourlyStat,
    Issue,
    IssueActivity,
    SilenceLink,
    SourceState,
    TriageState,
)

SEEN_WINDOWS = (
    ("1", "Last hour"),
    ("24", "Last 24 hours"),
    ("168", "Last 7 days"),
    ("720", "Last 30 days"),
)

STATE_DOTS = {
    SourceState.FIRING: ("#ef4444", "Firing"),
    SourceState.RESOLVED: ("#22c55e", "Resolved"),
}
UNKNOWN_DOT = ("#9ca3af", "No source state")

TRIAGE_VERBS = actions.TRIAGE_VERBS

SILENCE_HOUR = actions.SILENCE_WINDOWS["1h"]
SILENCE_HALF_SHIFT = actions.SILENCE_WINDOWS["4h"]
SILENCE_DAY = actions.SILENCE_WINDOWS["1d"]


class TriageFilter(admin.SimpleListFilter):
    title = "triage"
    parameter_name = "triage"

    def lookups(self, request, model_admin):
        return (("all", "Everything"), *TriageState.choices)

    def choices(self, changelist):
        value = self.value()
        yield {
            "selected": value is None,
            "query_string": changelist.get_query_string(remove=[self.parameter_name]),
            "display": "Open",
        }
        for lookup, label in self.lookup_choices:
            yield {
                "selected": value == str(lookup),
                "query_string": changelist.get_query_string(
                    {self.parameter_name: lookup}
                ),
                "display": label,
            }

    def queryset(self, request, queryset):
        value = self.value()
        if value == "all":
            return queryset
        if value in TriageState.values:
            return queryset.filter(triage_state=value)
        return queryset.filter(triage_state__in=triage.OPEN_STATES)


class LastSeenFilter(admin.SimpleListFilter):
    title = "last seen"
    parameter_name = "seen"

    def lookups(self, request, model_admin):
        return SEEN_WINDOWS

    def queryset(self, request, queryset):
        value = self.value()
        if value is None:
            return queryset
        if not value.isdigit():
            return queryset
        cutoff = timezone.now() - timedelta(hours=int(value))
        return queryset.filter(last_seen__gte=cutoff)


@admin.register(Issue)
class IssueAdmin(ModelAdmin):
    list_display = (
        "state",
        "issue_title",
        "grouping",
        "activity",
        "event_count",
        "duration",
        "triage_state",
        "project",
        "first_seen_short",
        "last_seen_short",
    )
    list_display_links = ("issue_title",)
    list_filter = (
        TriageFilter,
        "source_state",
        "level",
        "project",
        "environment",
        LastSeenFilter,
    )
    search_fields = ("title", "culprit", "fingerprint_hash")
    list_select_related = ("project",)
    actions = (
        "acknowledge",
        "resolve",
        "ignore",
        "silence_hour",
        "silence_half_shift",
        "silence_day",
    )
    readonly_fields = (
        "project",
        "title",
        "culprit",
        "level",
        "environment",
        "source_state",
        "fingerprint_hash",
        "fingerprint",
        "grouping_labels",
        "first_seen",
        "last_seen",
        "last_resolved_at",
        "event_count",
        "open_episode_count",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "culprit",
                    "level",
                    "source_state",
                    "triage_state",
                )
            },
        ),
        (
            "Grouping",
            {
                "fields": (
                    "project",
                    "environment",
                    "fingerprint_hash",
                    "fingerprint",
                    "grouping_labels",
                )
            },
        ),
        (
            "Counters",
            {
                "fields": (
                    "event_count",
                    "open_episode_count",
                    "first_seen",
                    "last_seen",
                    "last_resolved_at",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        now = timezone.now()
        open_episodes = Episode.objects.filter(
            issue=OuterRef("pk"), ends_at__isnull=True
        ).order_by("starts_at")
        latest_episodes = Episode.objects.filter(issue=OuterRef("pk")).order_by(
            "-starts_at"
        )
        window_stats = HourlyStat.objects.filter(
            hour__gte=sparkline.window_start(now)
        ).order_by("hour")
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                Prefetch(
                    "hourly_stats",
                    queryset=window_stats,
                    to_attr="window_stats",
                )
            )
            .annotate(
                open_since=Subquery(open_episodes.values("starts_at")[:1]),
                latest_start=Subquery(latest_episodes.values("starts_at")[:1]),
                latest_end=Subquery(latest_episodes.values("ends_at")[:1]),
            )
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        issue = self.get_object(request, object_id)
        if issue is not None:
            context["detail"] = detail.build(issue)
        return super().change_view(request, object_id, form_url, context)

    @admin.display(description="", ordering="source_state")
    def state(self, obj):
        color, label = STATE_DOTS.get(obj.source_state, UNKNOWN_DOT)
        return format_html(
            '<svg width="10" height="10" viewBox="0 0 10 10" role="img"'
            ' aria-label="{}"><title>{}</title>'
            '<circle cx="5" cy="5" r="4" fill="{}"></circle></svg>',
            label,
            label,
            color,
        )

    @admin.display(description="Issue", ordering="title")
    def issue_title(self, obj):
        return obj.title

    @admin.display(description="Grouping")
    def grouping(self, obj):
        labels = obj.grouping_labels or {}
        if not labels:
            return obj.culprit or "—"
        return " ".join(f"{key}={value}" for key, value in sorted(labels.items()))

    @admin.display(description="7 days")
    def activity(self, obj):
        stats = getattr(obj, "window_stats", [])
        counts = sparkline.buckets(
            ((stat.hour, stat.count) for stat in stats),
            timezone.now(),
        )
        return sparkline.render(counts)

    @admin.display(description="Duration")
    def duration(self, obj):
        return components.issue_duration(
            getattr(obj, "open_since", None),
            getattr(obj, "latest_start", None),
            getattr(obj, "latest_end", None),
            timezone.now(),
        )

    @admin.display(description="First seen", ordering="first_seen")
    def first_seen_short(self, obj):
        return components.format_stamp(obj.first_seen)

    @admin.display(description="Last seen", ordering="last_seen")
    def last_seen_short(self, obj):
        return components.format_stamp(obj.last_seen)

    @admin.action(description="Acknowledge")
    def acknowledge(self, request, queryset):
        self._retriage(request, queryset, triage.ACKNOWLEDGED)

    @admin.action(description="Resolve")
    def resolve(self, request, queryset):
        self._retriage(request, queryset, triage.RESOLVED)

    @admin.action(description="Ignore")
    def ignore(self, request, queryset):
        self._retriage(request, queryset, triage.IGNORED)

    @admin.action(description="Silence for 1 hour")
    def silence_hour(self, request, queryset):
        self._silence(request, queryset, SILENCE_HOUR, "1h")

    @admin.action(description="Silence for 4 hours")
    def silence_half_shift(self, request, queryset):
        self._silence(request, queryset, SILENCE_HALF_SHIFT, "4h")

    @admin.action(description="Silence for 1 day")
    def silence_day(self, request, queryset):
        self._silence(request, queryset, SILENCE_DAY, "1d")

    def _silence(self, request, queryset, duration, label):
        try:
            client = am_client.from_settings()
        except am_client.AlertmanagerError as error:
            self.message_user(request, f"No silence sent — {error}", messages.ERROR)
            return

        report = actions.silence(
            queryset,
            duration,
            request.user.get_username(),
            client,
        )
        for note in report.errors:
            self.message_user(request, note, messages.ERROR)
        if report.silenced:
            self.message_user(
                request,
                f"Silenced {report.silenced} issue(s) in Alertmanager for {label}",
                messages.SUCCESS,
            )

    def _retriage(self, request, queryset, target_state):
        report = actions.retriage(
            queryset,
            target_state,
            request.user.get_username(),
            timezone.now(),
        )
        self.message_user(
            request,
            f"{TRIAGE_VERBS[target_state]} {report.changed} issue(s),"
            f" {report.unchanged} unchanged",
            messages.SUCCESS,
        )

    def save_model(self, request, obj, form, change):
        if "triage_state" not in form.changed_data:
            super().save_model(request, obj, form, change)
            return

        target_state = obj.triage_state
        obj.triage_state = form.initial.get("triage_state", "")
        self._apply(obj, target_state, request.user.get_username(), timezone.now())

    def _apply(self, issue, target_state, actor, at):
        return actions.apply_triage(issue, target_state, actor, at)


@admin.register(Episode)
class EpisodeAdmin(ModelAdmin):
    list_display = (
        "am_fingerprint",
        "issue",
        "environment",
        "starts_at",
        "ends_at",
        "length",
        "delivery_count",
        "last_delivery_at",
    )
    list_filter = ("project", "environment")
    list_select_related = ("issue", "project")
    search_fields = ("am_fingerprint",)
    date_hierarchy = "starts_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Length")
    def length(self, obj):
        if obj.ends_at is None:
            return components.format_duration(timezone.now() - obj.starts_at)
        return components.format_duration(obj.ends_at - obj.starts_at)


@admin.register(GroupingRule)
class GroupingRuleAdmin(ModelAdmin):
    list_display = (
        "priority",
        "scope",
        "alertname_regex",
        "mode",
        "label_list",
        "active",
    )
    list_filter = ("mode", "active", "project")
    list_select_related = ("project",)
    ordering = ("priority", "id")
    actions = ("regroup_now",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self.message_user(
            request,
            "Saved. Existing issues keep their old grouping until you run"
            " Regroup — new occurrences use this rule immediately.",
            messages.WARNING,
        )

    @admin.action(description="Regroup existing issues with these rules")
    def regroup_now(self, request, queryset):
        report = regroup.regroup()
        self.message_user(
            request,
            f"Regrouped {report.episodes} episode(s):"
            f" {report.issues_created} issue(s) created,"
            f" {report.episodes_moved} moved,"
            f" {report.issues_deleted} removed",
            messages.SUCCESS,
        )

    @admin.display(description="Project", ordering="project")
    def scope(self, obj):
        if obj.project is None:
            return "all projects"
        return obj.project.slug

    @admin.display(description="Labels")
    def label_list(self, obj):
        labels = obj.labels or []
        if not labels:
            return "—"
        return ", ".join(str(label) for label in labels)


@admin.register(IssueActivity)
class IssueActivityAdmin(ModelAdmin):
    list_display = ("at", "issue", "kind", "actor")
    list_filter = ("kind",)
    list_select_related = ("issue",)
    date_hierarchy = "at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SilenceLink)
class SilenceLinkAdmin(ModelAdmin):
    list_display = ("issue", "am_silence_id", "created_at", "expires_at", "expired")
    list_select_related = ("issue",)
    search_fields = ("am_silence_id",)
    actions = ("lift",)

    def has_add_permission(self, request):
        return False

    @admin.display(description="Expired", boolean=True)
    def expired(self, obj):
        return obj.expires_at <= timezone.now()

    @admin.action(description="Lift silence in Alertmanager")
    def lift(self, request, queryset):
        try:
            client = am_client.from_settings()
        except am_client.AlertmanagerError as error:
            self.message_user(request, f"No silence lifted — {error}", messages.ERROR)
            return

        actor = request.user.get_username()
        lifted = 0
        for link in queryset.select_related("issue"):
            if self._lift_one(request, link, actor, client):
                lifted += 1
        if lifted:
            self.message_user(
                request,
                f"Lifted {lifted} silence(s) in Alertmanager",
                messages.SUCCESS,
            )

    def _lift_one(self, request, link, actor, client):
        try:
            silences.expire_silence(link, actor=actor, client=client)
        except am_client.AlertmanagerError as error:
            self.message_user(
                request,
                f"{link.am_silence_id} was not lifted — {error}",
                messages.ERROR,
            )
            return False
        return True
