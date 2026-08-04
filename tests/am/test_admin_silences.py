import datetime
import hashlib

import pytest
from django.contrib import messages as django_messages
from django.utils import timezone

from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db

ISSUES = "/admin/issues/issue/"
LINKS = "/admin/issues/silencelink/"


@pytest.fixture
def configured(alertmanager, settings):
    settings.PANDORA_AM_URL = alertmanager.url
    settings.PANDORA_AM_CA_BUNDLE = ""
    return alertmanager


def make_issue(project, title, **overrides):
    now = timezone.now()
    fields = {
        "fingerprint_hash": hashlib.sha256(title.encode()).hexdigest(),
        "fingerprint": [f"alertname:{title}"],
        "grouping_labels": {"alertname": title, "namespace": "monitoring"},
        "title": title,
        "culprit": f"alertname={title}",
        "level": issue_models.Level.ERROR,
        "environment": "p-mk1",
        "triage_state": issue_models.TriageState.NEW,
        "source_state": issue_models.SourceState.FIRING,
        "first_seen": now,
        "last_seen": now,
    }
    fields.update(overrides)
    return issue_models.Issue.objects.create(project=project, **fields)


def run(client, url, action, rows):
    payload = {
        "action": action,
        "_selected_action": [str(row.pk) for row in rows],
        "index": "0",
    }
    return client.post(url, payload)


def notes(response):
    return [
        str(message) for message in django_messages.get_messages(response.wsgi_request)
    ]


# action wiring


def test_the_changelist_offers_the_three_silence_windows(admin_client, project):
    """Should give the operator 1h, 4h and 1d without leaving the issue list."""
    make_issue(project, "One")

    response = admin_client.get(ISSUES)

    choices = response.context["action_form"].fields["action"].choices

    result = [str(label) for value, label in choices if "Silence" in str(label)]
    expected = ["Silence for 1 hour", "Silence for 4 hours", "Silence for 1 day"]

    assert result == expected


def test_silencing_from_the_changelist_reaches_alertmanager(
    admin_client, project, configured
):
    """Should turn a row selection into a real Alertmanager silence."""
    issue = make_issue(project, "TargetDown")

    run(admin_client, ISSUES, "silence_hour", [issue])

    result = configured.silence_bodies()[0]["matchers"]
    expected = [
        {
            "name": "alertname",
            "value": "TargetDown",
            "isRegex": False,
            "isEqual": True,
        },
        {
            "name": "namespace",
            "value": "monitoring",
            "isRegex": False,
            "isEqual": True,
        },
    ]

    assert result == expected


def test_silencing_records_the_link(admin_client, project, configured):
    """Should leave the bookkeeping every later lift and prune depends on."""
    issue = make_issue(project, "TargetDown")

    run(admin_client, ISSUES, "silence_hour", [issue])

    result = issue_models.SilenceLink.objects.filter(issue=issue).count()
    expected = 1

    assert result == expected


def test_silencing_names_the_admin_user_in_alertmanager(
    admin_client, project, configured
):
    """Should attribute the silence to whoever clicked, not to the service."""
    issue = make_issue(project, "TargetDown")

    run(admin_client, ISSUES, "silence_hour", [issue])

    result = configured.silence_bodies()[0]["createdBy"]
    expected = "admin"

    assert result == expected


@pytest.mark.parametrize(
    ("action", "hours"),
    [("silence_hour", 1), ("silence_half_shift", 4), ("silence_day", 24)],
)
def test_each_action_sets_its_own_window(
    admin_client, project, configured, action, hours
):
    """Should mean what the label says — 1h, 4h and 1d, measured from now."""
    issue = make_issue(project, "TargetDown")

    run(admin_client, ISSUES, action, [issue])

    link = issue_models.SilenceLink.objects.get(issue=issue)

    result = round((link.expires_at - link.created_at) / datetime.timedelta(hours=1))
    expected = hours

    assert result == expected


def test_a_silence_action_reports_what_it_did(admin_client, project, configured):
    """Should say how many issues went quiet and for how long."""
    issue = make_issue(project, "TargetDown")

    response = run(admin_client, ISSUES, "silence_half_shift", [issue])

    result = notes(response)
    expected = ["Silenced 1 issue(s) in Alertmanager for 4h"]

    assert result == expected


def test_a_bulk_silence_covers_every_selected_issue(admin_client, project, configured):
    """Should apply to the whole selection, like the triage actions do."""
    issues = [make_issue(project, f"Issue {index}") for index in range(3)]

    run(admin_client, ISSUES, "silence_hour", issues)

    result = issue_models.SilenceLink.objects.count()
    expected = 3

    assert result == expected


# failure handling


def test_an_issue_without_grouping_labels_is_refused(admin_client, project, configured):
    """Should never let a click silence all of Alertmanager."""
    issue = make_issue(project, "TargetDown", grouping_labels={})

    response = run(admin_client, ISSUES, "silence_hour", [issue])

    result = notes(response)

    assert len(result) == 1
    assert "was not silenced" in result[0]
    assert "every alert" in result[0]
    assert issue_models.SilenceLink.objects.exists() is False


def test_a_refused_silence_is_reported_per_issue(admin_client, project, configured):
    """Should name the issue Alertmanager rejected and keep going."""
    issues = [make_issue(project, "First"), make_issue(project, "Second")]
    configured.fail_next(500, times=1)

    response = run(admin_client, ISSUES, "silence_hour", issues)

    result = (
        len([note for note in notes(response) if "was not silenced" in note]),
        issue_models.SilenceLink.objects.count(),
    )
    expected = (1, 1)

    assert result == expected


def test_an_unconfigured_alertmanager_stops_before_the_loop(
    admin_client, project, settings
):
    """Should report the missing URL once, not once per selected issue."""
    settings.PANDORA_AM_URL = ""
    issues = [make_issue(project, "First"), make_issue(project, "Second")]

    response = run(admin_client, ISSUES, "silence_hour", issues)

    result = notes(response)

    assert len(result) == 1
    assert "PANDORA_AM_URL" in result[0]


# lifting a silence


def test_lifting_from_the_link_list_expires_it_in_alertmanager(
    admin_client, project, configured
):
    """Should complete the round trip from the admin the silence started in."""
    issue = make_issue(project, "TargetDown")
    run(admin_client, ISSUES, "silence_hour", [issue])
    link = issue_models.SilenceLink.objects.get()

    run(admin_client, LINKS, "lift", [link])

    result = (configured.deleted_ids(), configured.silences)
    expected = ([link.am_silence_id], {})

    assert result == expected


def test_lifting_drops_the_link(admin_client, project, configured):
    """Should leave no record of a silence Alertmanager no longer holds."""
    issue = make_issue(project, "TargetDown")
    run(admin_client, ISSUES, "silence_hour", [issue])
    link = issue_models.SilenceLink.objects.get()

    response = run(admin_client, LINKS, "lift", [link])

    result = (
        issue_models.SilenceLink.objects.exists(),
        [note for note in notes(response) if "Lifted" in note],
    )
    expected = (False, ["Lifted 1 silence(s) in Alertmanager"])

    assert result == expected


def test_a_refused_lift_keeps_the_link(admin_client, project, configured):
    """Should keep the row while Alertmanager still holds the silence."""
    issue = make_issue(project, "TargetDown")
    run(admin_client, ISSUES, "silence_hour", [issue])
    link = issue_models.SilenceLink.objects.get()
    configured.fail_next(404, times=1)

    response = run(admin_client, LINKS, "lift", [link])

    result = (
        issue_models.SilenceLink.objects.filter(pk=link.pk).exists(),
        [note for note in notes(response) if "was not lifted" in note],
    )

    assert result[0] is True
    assert len(result[1]) == 1


def test_lifting_with_no_alertmanager_configured_is_reported_once(
    admin_client, project, configured, settings
):
    """Should fail the same way the silence action does, before touching a row."""
    issue = make_issue(project, "TargetDown")
    run(admin_client, ISSUES, "silence_hour", [issue])
    link = issue_models.SilenceLink.objects.get()
    settings.PANDORA_AM_URL = ""

    response = run(admin_client, LINKS, "lift", [link])

    result = [note for note in notes(response) if "lifted" in note]

    assert len(result) == 1
    assert "No silence lifted" in result[0]
    assert issue_models.SilenceLink.objects.filter(pk=link.pk).exists() is True
