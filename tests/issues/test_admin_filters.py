import datetime
import hashlib

import freezegun
import pytest
from django.utils import timezone

from pandora.issues import admin, models

pytestmark = pytest.mark.django_db

CHANGELIST = "/admin/issues/issue/"


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time("2026-08-04 12:00:00"):
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


def titles(response):
    return sorted(issue.title for issue in response.context["cl"].result_list)


@pytest.fixture
def staffed(admin_client, project):
    make_issue(project, "New", triage_state=models.TriageState.NEW)
    make_issue(project, "Acked", triage_state=models.TriageState.ACKNOWLEDGED)
    make_issue(project, "Done", triage_state=models.TriageState.RESOLVED)
    make_issue(project, "Muted", triage_state=models.TriageState.IGNORED)
    return admin_client


# filter composition


def test_the_triage_field_filter_is_replaced_by_the_defaulting_one():
    """Should never offer a plain triage_state filter beside the default view."""
    result = admin.IssueAdmin.list_filter

    assert admin.TriageFilter in result
    assert "triage_state" not in result


def test_the_filters_cover_state_severity_scope_and_time():
    """Should offer exactly the six cuts the spec names."""
    result = admin.IssueAdmin.list_filter
    expected = (
        admin.TriageFilter,
        "source_state",
        "level",
        "project",
        "environment",
        admin.LastSeenFilter,
    )

    assert result == expected


# triage filter


def test_open_work_is_the_default_view(staffed):
    """Should show only what still needs a decision."""
    result = titles(staffed.get(CHANGELIST))
    expected = ["Acked", "New"]

    assert result == expected


def test_everything_is_one_click_away(staffed):
    """Should let a reader widen to the full set."""
    result = titles(staffed.get(CHANGELIST, {"triage": "all"}))
    expected = ["Acked", "Done", "Muted", "New"]

    assert result == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("new", ["New"]),
        ("ack", ["Acked"]),
        ("resolved", ["Done"]),
        ("ignored", ["Muted"]),
    ],
)
def test_each_state_can_be_isolated(staffed, value, expected):
    """Should narrow to one state when the reader asks for it."""
    result = titles(staffed.get(CHANGELIST, {"triage": value}))

    assert result == expected


def test_a_junk_triage_value_falls_back_to_the_default(staffed):
    """Should not hand back an empty page when the query string is wrong."""
    result = titles(staffed.get(CHANGELIST, {"triage": "nonsense"}))
    expected = ["Acked", "New"]

    assert result == expected


def test_the_default_choice_is_labelled_and_preselected(staffed):
    """Should show the reader that 'Open' is what they are looking at."""
    response = staffed.get(CHANGELIST)
    choices = list(
        admin.TriageFilter(
            response.wsgi_request, {}, models.Issue, admin.IssueAdmin
        ).choices(response.context["cl"])
    )

    result = [(choice["display"], choice["selected"]) for choice in choices]
    expected = [
        ("Open", True),
        ("Everything", False),
        ("New", False),
        ("Acknowledged", False),
        ("Resolved", False),
        ("Ignored", False),
    ]

    assert result == expected


# last seen filter


def test_the_time_windows_on_offer_span_an_hour_to_a_month():
    """Should give the operator the four windows an alert review needs."""
    result = admin.SEEN_WINDOWS
    expected = (
        ("1", "Last hour"),
        ("24", "Last 24 hours"),
        ("168", "Last 7 days"),
        ("720", "Last 30 days"),
    )

    assert result == expected


def test_the_time_window_cuts_by_last_seen(admin_client, project):
    """Should keep only what the source touched inside the window."""
    now = timezone.now()
    make_issue(project, "Fresh", last_seen=now - datetime.timedelta(minutes=30))
    make_issue(project, "Yesterday", last_seen=now - datetime.timedelta(hours=20))
    make_issue(project, "Fortnight", last_seen=now - datetime.timedelta(days=14))

    assert titles(admin_client.get(CHANGELIST, {"seen": "1"})) == ["Fresh"]
    assert titles(admin_client.get(CHANGELIST, {"seen": "24"})) == [
        "Fresh",
        "Yesterday",
    ]
    assert titles(admin_client.get(CHANGELIST, {"seen": "720"})) == [
        "Fortnight",
        "Fresh",
        "Yesterday",
    ]


def test_no_time_window_keeps_everything(admin_client, project):
    """Should not hide old issues until a window is chosen."""
    make_issue(
        project,
        "Ancient",
        last_seen=timezone.now() - datetime.timedelta(days=400),
    )

    result = titles(admin_client.get(CHANGELIST))
    expected = ["Ancient"]

    assert result == expected


def test_a_non_numeric_time_window_is_ignored(admin_client, project):
    """Should fall through to everything when the query string carries junk."""
    make_issue(project, "One")

    result = titles(admin_client.get(CHANGELIST, {"seen": "fortnight"}))
    expected = ["One"]

    assert result == expected


# combinations


def test_the_filters_combine(admin_client, project):
    """Should intersect state and time rather than picking one."""
    now = timezone.now()
    make_issue(project, "Fresh open")
    make_issue(
        project,
        "Stale open",
        last_seen=now - datetime.timedelta(days=3),
    )
    make_issue(
        project,
        "Fresh resolved",
        triage_state=models.TriageState.RESOLVED,
    )

    result = titles(admin_client.get(CHANGELIST, {"seen": "24"}))
    expected = ["Fresh open"]

    assert result == expected


def test_the_search_box_reaches_the_fingerprint(admin_client, project):
    """Should let an operator paste a hash from a log line and land on the issue."""
    issue = make_issue(project, "Hashed")

    result = titles(admin_client.get(CHANGELIST, {"q": issue.fingerprint_hash}))
    expected = ["Hashed"]

    assert result == expected
