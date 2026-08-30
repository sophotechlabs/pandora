import datetime
import http

import freezegun
import pytest
from django.contrib import admin as django_admin
from django.core import management
from django.urls import reverse

from pandora.issues import admin, models, triage

pytestmark = pytest.mark.django_db

CHANGELIST = "/admin/issues/issue/"


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time("2026-08-04 12:00:00"):
        yield


@pytest.fixture
def seeded_client(admin_client):
    management.call_command("seed_demo")
    return admin_client


@pytest.fixture
def issue_view():
    return admin.IssueAdmin(models.Issue, django_admin.site)


def crashlooping():
    return models.Issue.objects.get(title__startswith="KubePodCrashLooping")


# registration contract


def test_the_triage_surfaces_are_registered(seeded_client):
    """Should expose the models a human triages, not the aggregate tables."""
    response = seeded_client.get(reverse("admin:app_list", args=["issues"]))

    result = sorted(
        model["object_name"] for model in response.context["app_list"][0]["models"]
    )
    expected = [
        "Episode",
        "GroupingRule",
        "Issue",
        "IssueActivity",
        "PathRule",
        "SilenceLink",
    ]
    assert result == expected


def test_the_project_surfaces_are_registered(seeded_client):
    """Should expose projects, DSN keys, ingest tokens and the outbound link templates."""
    response = seeded_client.get(reverse("admin:app_list", args=["core"]))

    result = sorted(
        model["object_name"] for model in response.context["app_list"][0]["models"]
    )
    expected = ["DsnKey", "IngestToken", "Project", "ServiceLink"]
    assert result == expected


# changelist configuration


def test_the_changelist_shows_the_columns_the_spec_names():
    """Should lead with state and title and end with the two seen stamps."""
    result = admin.IssueAdmin.list_display
    expected = (
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

    assert result == expected


def test_the_seen_stamps_are_rendered_short(issue, issue_view):
    """Should keep the two date columns narrow enough to fit beside the rest."""
    result = (
        issue_view.first_seen_short(issue),
        issue_view.last_seen_short(issue),
    )
    expected = ("Aug 04, 06:00", "Aug 04, 12:00")

    assert result == expected


def test_only_the_title_opens_the_issue():
    """Should keep the state dot and the sparkline from swallowing the click."""
    result = admin.IssueAdmin.list_display_links
    expected = ("issue_title",)

    assert result == expected


def test_the_project_join_is_declared_once():
    """Should fetch the project column with the row, not per row."""
    result = admin.IssueAdmin.list_select_related
    expected = ("project",)

    assert result == expected


def test_the_changelist_actions_are_offered():
    """Should expose the transitions a human owns and the three silence windows."""
    result = admin.IssueAdmin.actions
    expected = (
        "acknowledge",
        "resolve",
        "ignore",
        "silence_hour",
        "silence_half_shift",
        "silence_day",
    )

    assert result == expected


def test_issues_cannot_be_hand_written(seeded_client):
    """Should keep issues machine-created — ingest owns the grouping."""
    response = seeded_client.get(reverse("admin:issues_issue_add"))

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_only_the_triage_state_is_editable():
    """Should leave every counter and grouping field read-only in the form."""
    editable = [
        field
        for fieldset in admin.IssueAdmin.fieldsets
        for field in fieldset[1]["fields"]
        if field not in admin.IssueAdmin.readonly_fields
    ]

    result = editable
    expected = ["triage_state"]

    assert result == expected


# changelist rendering


def test_the_issue_changelist_renders_seeded_data(seeded_client):
    """Should list the seeded issues without a template or query error."""
    response = seeded_client.get(CHANGELIST)

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected
    assert b"KubePodCrashLooping" in response.content


def test_the_changelist_defaults_to_the_open_issues(seeded_client):
    """Should hide resolved and ignored issues until a reader asks for them."""
    response = seeded_client.get(CHANGELIST)

    result = response.context["cl"].result_count
    expected = models.Issue.objects.filter(triage_state__in=triage.OPEN_STATES).count()

    assert result == expected
    assert result < models.Issue.objects.count()


def test_everything_is_reachable_from_the_changelist(seeded_client):
    """Should page every seeded issue once the filter is widened."""
    response = seeded_client.get(CHANGELIST, {"triage": "all"})

    result = response.context["cl"].result_count
    expected = models.Issue.objects.count()

    assert result == expected


def test_a_firing_issue_shows_a_live_state_dot(seeded_client):
    """Should mark what the source says is firing right now."""
    body = seeded_client.get(CHANGELIST).content.decode()

    assert 'aria-label="Firing"' in body
    assert "#ef4444" in body


def test_a_settled_issue_shows_a_resolved_state_dot(seeded_client):
    """Should distinguish an issue the source has closed."""
    body = seeded_client.get(CHANGELIST, {"triage": "all"}).content.decode()

    assert 'aria-label="Resolved"' in body
    assert "#22c55e" in body


def test_the_grouping_labels_stand_in_for_the_culprit(seeded_client):
    """Should show what the fingerprint kept, not the raw culprit string."""
    body = seeded_client.get(CHANGELIST).content.decode()

    assert "alertname=KubePodCrashLooping namespace=payments" in body


def test_every_row_carries_an_inline_sparkline(seeded_client):
    """Should draw the 7-day shape in markup — no per-row image request."""
    response = seeded_client.get(CHANGELIST)

    result = response.content.decode().count('aria-label="7 day activity"')
    expected = response.context["cl"].result_count

    assert result == expected


def test_the_changelist_stays_off_the_per_row_query_path(
    seeded_client, django_assert_max_num_queries
):
    """Should keep sparklines and durations in the one changelist query."""
    with django_assert_max_num_queries(15):
        seeded_client.get(CHANGELIST)


def test_a_firing_issue_reports_how_long_it_has_been_open(
    seeded_client, rf, issue_view
):
    """Should measure from the earliest open episode, not the newest one."""
    row = issue_view.get_queryset(rf.get(CHANGELIST)).get(pk=crashlooping().pk)

    result = issue_view.duration(row)
    expected = "12h 0m"

    assert result == expected


def test_a_settled_issue_reports_its_last_episode_length(seeded_client, rf, issue_view):
    """Should fall back to how long the most recent episode ran."""
    issue = models.Issue.objects.get(title__startswith="CertManagerCertExpiringSoon")

    row = issue_view.get_queryset(rf.get(CHANGELIST)).get(pk=issue.pk)

    result = issue_view.duration(row)
    expected = "3h 0m"

    assert result == expected


def test_an_issue_with_no_episodes_reports_no_duration(issue, rf, issue_view):
    """Should print a dash rather than guessing a window."""
    row = issue_view.get_queryset(rf.get(CHANGELIST)).get(pk=issue.pk)

    result = issue_view.duration(row)
    expected = "—"

    assert result == expected


def test_an_issue_without_grouping_labels_falls_back_to_the_culprit(issue, issue_view):
    """Should still name the issue when the fingerprint kept nothing."""
    issue.grouping_labels = {}

    result = issue_view.grouping(issue)
    expected = issue.culprit

    assert result == expected


# detail rendering


def test_the_issue_detail_renders(seeded_client):
    """Should render one issue's change form off seeded data alone."""
    response = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    )

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


def test_the_detail_page_carries_its_panels(seeded_client):
    """Should build the timeline, tags, links and feed for the template."""
    response = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    )

    detail = response.context["detail"]

    assert detail.timeline.rows
    assert detail.tags
    assert detail.activities


def test_the_detail_page_renders_every_panel_heading(seeded_client):
    """Should show all four read-only panels below the form."""
    body = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    ).content.decode()

    for heading in ("Enrichment", "Annotations", "Episode timeline", "Activity"):
        assert heading in body


def test_the_detail_page_renders_the_alertmanager_annotation(seeded_client):
    """Should put the summary the alert carried in front of the reader."""
    body = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    ).content.decode()

    assert "Pod payments/ledger is in CrashLoopBackOff" in body


def test_the_detail_page_lists_the_distinguishing_pods(seeded_client):
    """Should show which pods the coarse grouping merged into one issue."""
    body = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    ).content.decode()

    assert "pod=ledger-7d9f4c8b6d-hk2mp" in body


def test_the_detail_page_reports_a_missing_enrichment_template(seeded_client):
    """Should say why the panel is empty when no URL template is configured."""
    body = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    ).content.decode()

    assert "No enrichment URL templates configured" in body


def test_the_detail_page_links_out_when_a_template_is_configured(
    seeded_client, settings
):
    """Should render the Grafana deep link the operator templated."""
    settings.PANDORA_GRAFANA_URL = "https://g.test/d?var-ns={namespace}"

    body = seeded_client.get(
        reverse("admin:issues_issue_change", args=[crashlooping().pk]),
    ).content.decode()

    assert "https://g.test/d?var-ns=payments" in body


def test_a_regression_is_visible_on_the_detail_page(seeded_client):
    """Should show that an issue came back after it had been resolved."""
    issue = models.Issue.objects.get(
        title__startswith="KubeDeploymentReplicasMismatch",
    )

    body = seeded_client.get(
        reverse("admin:issues_issue_change", args=[issue.pk]),
    ).content.decode()

    assert "Regression" in body


def test_a_missing_issue_still_reaches_the_admin_not_found_path(seeded_client):
    """Should not blow up building panels for an id that was already deleted."""
    response = seeded_client.get(
        reverse("admin:issues_issue_change", args=[999999]),
    )

    result = response.status_code
    expected = http.HTTPStatus.FOUND

    assert result == expected


# supporting admins


def test_the_episode_changelist_renders(seeded_client):
    """Should list episodes read-only."""
    response = seeded_client.get(reverse("admin:issues_episode_changelist"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


def test_the_grouping_rule_changelist_renders(seeded_client):
    """Should list the seeded default grouping rule."""
    response = seeded_client.get(reverse("admin:issues_groupingrule_changelist"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected
    assert b"denylist" in response.content


def test_a_global_grouping_rule_says_it_applies_everywhere(seeded_client):
    """Should not print an empty project cell for the default rule."""
    body = seeded_client.get(reverse("admin:issues_groupingrule_changelist"))

    assert b"all projects" in body.content


def test_the_grouping_rule_lists_the_labels_it_drops(seeded_client):
    """Should let a reader see the denylist without opening the row."""
    body = seeded_client.get(
        reverse("admin:issues_groupingrule_changelist")
    ).content.decode()

    assert "pod, instance, container, endpoint, replicaset, uid, node" in body


def test_a_project_scoped_grouping_rule_names_its_project(project):
    """Should say which project an override applies to."""
    rule = models.GroupingRule.objects.create(project=project, priority=10)
    view = admin.GroupingRuleAdmin(models.GroupingRule, django_admin.site)

    result = view.scope(rule)
    expected = project.slug

    assert result == expected


def test_a_grouping_rule_without_labels_renders_a_dash(project):
    """Should not print an empty cell for an allowlist that lists nothing yet."""
    rule = models.GroupingRule.objects.create(project=project, priority=10, labels=[])
    view = admin.GroupingRuleAdmin(models.GroupingRule, django_admin.site)

    result = view.label_list(rule)
    expected = "—"

    assert result == expected


def test_the_activity_changelist_renders(seeded_client):
    """Should list the audit trail read-only."""
    response = seeded_client.get(reverse("admin:issues_issueactivity_changelist"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


def test_the_silence_changelist_renders(seeded_client):
    """Should list silence bookkeeping even before the AM client exists."""
    response = seeded_client.get(reverse("admin:issues_silencelink_changelist"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


def test_an_expired_silence_is_flagged(issue):
    """Should show at a glance whether the silence is still holding."""
    live = models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="live",
        expires_at=issue.last_seen + datetime.timedelta(hours=1),
    )
    stale = models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="stale",
        expires_at=issue.last_seen - datetime.timedelta(hours=1),
    )
    view = admin.SilenceLinkAdmin(models.SilenceLink, django_admin.site)

    result = (view.expired(live), view.expired(stale))
    expected = (False, True)

    assert result == expected


def test_an_open_episode_reports_its_running_length(episode):
    """Should keep measuring an episode that has not ended."""
    view = admin.EpisodeAdmin(models.Episode, django_admin.site)

    result = view.length(episode)
    expected = "2h 0m"

    assert result == expected


def test_a_closed_episode_reports_the_length_it_ran(episode):
    """Should measure a finished episode between its own two stamps."""
    episode.ends_at = episode.starts_at + datetime.timedelta(minutes=45)
    view = admin.EpisodeAdmin(models.Episode, django_admin.site)

    result = view.length(episode)
    expected = "45m"

    assert result == expected


# permissions


def test_episodes_cannot_be_hand_written(seeded_client):
    """Should keep episodes machine-owned — no add form in the admin."""
    response = seeded_client.get(reverse("admin:issues_episode_add"))

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_episodes_cannot_be_deleted_by_hand(seeded_client):
    """Should keep the permanent episode history permanent."""
    view = admin.EpisodeAdmin(models.Episode, django_admin.site)

    assert view.has_delete_permission(None) is False


def test_activities_cannot_be_hand_written(seeded_client):
    """Should keep the audit trail append-only from code, not from the UI."""
    response = seeded_client.get(reverse("admin:issues_issueactivity_add"))

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_activities_cannot_be_deleted_by_hand(seeded_client):
    """Should keep the audit trail from being edited after the fact."""
    view = admin.IssueActivityAdmin(models.IssueActivity, django_admin.site)

    assert view.has_delete_permission(None) is False


def test_the_dashboard_renders(seeded_client):
    """Should render the unfold dashboard through the Phase 3 callback."""
    response = seeded_client.get(reverse("admin:index"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected
