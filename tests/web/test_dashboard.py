import datetime
import hashlib
import http

import freezegun
import pytest
from django.core import management
from django.utils import timezone

from pandora.issues import models
from pandora.web import dashboard

pytestmark = pytest.mark.django_db

FROZEN = "2026-08-04 12:00:00"


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time(FROZEN):
        yield


def make_issue(project, title, **overrides):
    fields = {
        "fingerprint_hash": hashlib.sha256(title.encode()).hexdigest(),
        "fingerprint": [f"alertname:{title}"],
        "grouping_labels": {"alertname": title},
        "title": title,
        "culprit": f"alertname={title}",
        "level": models.Level.ERROR,
        "environment": "p-mk1",
        "triage_state": models.TriageState.NEW,
        "source_state": models.SourceState.FIRING,
        "first_seen": timezone.now(),
        "last_seen": timezone.now(),
    }
    fields.update(overrides)
    return models.Issue.objects.create(project=project, **fields)


def build(rf):
    return dashboard.dashboard_callback(rf.get("/admin/"), {})["dashboard"]


# callback contract


def test_the_callback_keeps_the_context_it_was_handed(rf):
    """Should add to unfold's context rather than replace it."""
    context = dashboard.dashboard_callback(rf.get("/admin/"), {"title": "pandora"})

    result = context["title"]
    expected = "pandora"

    assert result == expected


def test_the_callback_returns_the_same_object(rf):
    """Should not copy the context — unfold mutates the dict it passed in."""
    context = {}

    result = dashboard.dashboard_callback(rf.get("/admin/"), context)

    assert result is context


def test_the_dashboard_names_the_four_headline_numbers(rf):
    """Should answer what is on fire, what is new, what came back, what is untouched."""
    result = [kpi.label for kpi in build(rf).kpis]
    expected = [
        "Firing now",
        "New in 24 hours",
        "Regressions in 7 days",
        "Untriaged",
    ]

    assert result == expected


# empty state


def test_an_empty_database_reads_as_zeroes(rf):
    """Should render a first-boot dashboard without a division or None error."""
    result = [kpi.value for kpi in build(rf).kpis]
    expected = [0, 0, 0, 0]

    assert result == expected


def test_an_empty_database_says_nothing_is_open(rf):
    """Should explain the empty table rather than showing a blank card."""
    table = build(rf).tables["issues"]

    result = (table.rows, table.empty_message)
    expected = ((), "Nothing open")

    assert result == expected


# firing now


def test_firing_counts_issues_the_source_says_are_live(rf, project):
    """Should count issues, not episodes — one alert storm is one row."""
    make_issue(project, "Live", open_episode_count=3)
    make_issue(project, "Settled", source_state=models.SourceState.RESOLVED)

    kpi = build(rf).kpis[0]

    result = (kpi.value, kpi.hint)
    expected = (1, "3 open episode(s)")

    assert result == expected


def test_firing_sums_the_open_episodes_behind_the_issues(rf, project):
    """Should show how much noise the grouping is absorbing."""
    make_issue(project, "One", open_episode_count=2)
    make_issue(project, "Two", open_episode_count=5)

    result = build(rf).kpis[0].hint
    expected = "7 open episode(s)"

    assert result == expected


# new issues


def test_new_counts_only_the_last_day(rf, project):
    """Should keep the headline number to what appeared since yesterday."""
    make_issue(project, "Today")
    make_issue(
        project,
        "Last week",
        first_seen=timezone.now() - datetime.timedelta(days=3),
    )

    kpi = build(rf).kpis[1]

    result = (kpi.value, kpi.hint)
    expected = (1, "2 in the last 7 days")

    assert result == expected


def test_an_issue_older_than_a_week_counts_in_neither_window(rf, project):
    """Should not let a long-standing issue read as new."""
    make_issue(
        project,
        "Ancient",
        first_seen=timezone.now() - datetime.timedelta(days=30),
    )

    kpi = build(rf).kpis[1]

    result = (kpi.value, kpi.hint)
    expected = (0, "0 in the last 7 days")

    assert result == expected


# regressions


def test_regressions_count_issues_not_activity_rows(rf, project):
    """Should count an issue that flapped twice once."""
    issue = make_issue(project, "Flapper")
    for hours in (1, 5):
        models.IssueActivity.objects.create(
            issue=issue,
            kind=models.ActivityKind.REGRESSION,
            at=timezone.now() - datetime.timedelta(hours=hours),
        )

    result = build(rf).kpis[2].value
    expected = 1

    assert result == expected


def test_a_regression_older_than_a_week_drops_out(rf, project):
    """Should keep the number to the window the label promises."""
    issue = make_issue(project, "Old flapper")
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.REGRESSION,
        at=timezone.now() - datetime.timedelta(days=8),
    )

    result = build(rf).kpis[2].value
    expected = 0

    assert result == expected


def test_other_activity_kinds_are_not_regressions(rf, project):
    """Should not let an acknowledge inflate the regression count."""
    issue = make_issue(project, "Acked")
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.ACKNOWLEDGED,
        actor="admin",
        at=timezone.now(),
    )

    result = build(rf).kpis[2].value
    expected = 0

    assert result == expected


# triage backlog


def test_untriaged_counts_new_issues_and_hints_at_the_acknowledged(rf, project):
    """Should separate what nobody has looked at from what someone owns."""
    make_issue(project, "Untouched")
    make_issue(project, "Owned", triage_state=models.TriageState.ACKNOWLEDGED)
    make_issue(project, "Closed", triage_state=models.TriageState.RESOLVED)

    kpi = build(rf).kpis[3]

    result = (kpi.value, kpi.hint)
    expected = (1, "1 acknowledged")

    assert result == expected


# top issues table


def test_the_top_issue_table_names_its_columns(rf):
    """Should carry enough context to triage straight from the dashboard."""
    result = [column.label for column in build(rf).tables["issues"].columns]
    expected = ["Issue", "Project", "Level", "Events", "Last seen"]

    assert result == expected


def test_the_top_issue_table_is_ordered_by_event_count(rf, project):
    """Should put the loudest issue first."""
    make_issue(project, "Quiet", event_count=2)
    make_issue(project, "Loud", event_count=90)
    make_issue(project, "Middling", event_count=30)

    result = [row[0].text for row in build(rf).tables["issues"].rows]
    expected = ["Loud", "Middling", "Quiet"]

    assert result == expected


def test_the_top_issue_table_links_into_the_issue(rf, project):
    """Should get the reader one click from the detail page."""
    issue = make_issue(project, "Loud", event_count=9)

    result = build(rf).tables["issues"].rows[0][0].href
    expected = f"/admin/issues/issue/{issue.pk}/change/"

    assert result == expected


def test_the_top_issue_table_colours_the_level(rf, project):
    """Should let severity read at a glance instead of as a word."""
    make_issue(project, "Bad", level=models.Level.FATAL)

    cell = build(rf).tables["issues"].rows[0][2]

    result = (cell.text, cell.variant)
    expected = ("Fatal", "danger")

    assert result == expected


def test_the_top_issue_table_skips_closed_issues(rf, project):
    """Should show open work only — the dashboard is a triage queue."""
    make_issue(project, "Open", event_count=1)
    make_issue(
        project,
        "Closed",
        event_count=500,
        triage_state=models.TriageState.RESOLVED,
    )
    make_issue(
        project,
        "Muted",
        event_count=400,
        triage_state=models.TriageState.IGNORED,
    )

    result = [row[0].text for row in build(rf).tables["issues"].rows]
    expected = ["Open"]

    assert result == expected


def test_the_top_issue_table_stops_at_eight_rows(rf, project):
    """Should keep the card a summary, not a second changelist."""
    for index in range(12):
        make_issue(project, f"Issue {index}", event_count=index)

    result = len(build(rf).tables["issues"].rows)
    expected = 8

    assert result == expected


# rendering


def test_the_admin_index_renders_every_section(admin_client):
    """Should paint the KPI row and the table off seeded data alone."""
    management.call_command("seed_demo")

    response = admin_client.get("/admin/")
    body = response.content.decode()

    assert response.status_code == http.HTTPStatus.OK
    assert "Firing now" in body
    assert "Top open issues" in body
    assert "KubePodCrashLooping" in body


def test_the_admin_index_renders_its_empty_states(admin_client):
    """Should render on a fresh database with no issues at all."""
    body = admin_client.get("/admin/").content.decode()

    assert "Nothing open" in body
    assert "Untriaged" in body
