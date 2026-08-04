import http

import pytest
from django.core import management
from django.urls import reverse

from pandora.issues import models

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded_client(admin_client):
    management.call_command("seed_demo")
    return admin_client


# registration contract


def test_the_triage_surfaces_are_registered(seeded_client):
    """Should expose the models a human triages, not the aggregate tables."""
    response = seeded_client.get(reverse("admin:app_list", args=["issues"]))

    result = sorted(
        model["object_name"] for model in response.context["app_list"][0]["models"]
    )
    expected = ["Episode", "GroupingRule", "Issue", "IssueActivity", "SilenceLink"]
    assert result == expected


def test_the_project_surfaces_are_registered(seeded_client):
    """Should expose projects, DSN keys and ingest tokens."""
    response = seeded_client.get(reverse("admin:app_list", args=["core"]))

    result = sorted(
        model["object_name"] for model in response.context["app_list"][0]["models"]
    )
    expected = ["DsnKey", "IngestToken", "Project"]
    assert result == expected


# rendering


def test_the_issue_changelist_renders_seeded_data(seeded_client):
    """Should list the seeded issues without a template or query error."""
    response = seeded_client.get(reverse("admin:issues_issue_changelist"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected
    assert b"KubePodCrashLooping" in response.content


def test_the_issue_changelist_shows_every_seeded_issue(seeded_client):
    """Should page every seeded issue onto the first changelist page."""
    response = seeded_client.get(reverse("admin:issues_issue_changelist"))

    result = response.context["cl"].result_count
    expected = models.Issue.objects.count()

    assert result == expected


def test_the_issue_detail_renders(seeded_client):
    """Should render one issue's change form off seeded data alone."""
    issue = models.Issue.objects.first()

    response = seeded_client.get(
        reverse("admin:issues_issue_change", args=[issue.pk]),
    )

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


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


def test_the_dashboard_renders(seeded_client):
    """Should render the unfold dashboard through the Phase 0 callback."""
    response = seeded_client.get(reverse("admin:index"))

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


# permissions


def test_episodes_cannot_be_hand_written(seeded_client):
    """Should keep episodes machine-owned — no add form in the admin."""
    response = seeded_client.get(reverse("admin:issues_episode_add"))

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_activities_cannot_be_hand_written(seeded_client):
    """Should keep the audit trail append-only from code, not from the UI."""
    response = seeded_client.get(reverse("admin:issues_issueactivity_add"))

    assert response.status_code == http.HTTPStatus.FORBIDDEN
