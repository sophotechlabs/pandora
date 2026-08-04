import datetime

import pytest
from django import test
from django.utils import timezone

from pandora.am import client as am_client
from pandora.am import silences
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db

ONE_HOUR = datetime.timedelta(hours=1)


@pytest.fixture
def moment():
    return timezone.now().replace(microsecond=0)


# matcher construction


def test_matchers_are_structured_and_exact(issue):
    """Should send label equality, not a regex Alertmanager has to parse."""
    result = silences.build_matchers(issue)
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


def test_matchers_come_from_the_grouping_labels_only(issue):
    """Should silence what the issue groups on, not the labels one episode carried."""
    issue.grouping_labels = {"alertname": "TargetDown"}

    result = [matcher["name"] for matcher in silences.build_matchers(issue)]
    expected = ["alertname"]

    assert result == expected


def test_an_issue_without_grouping_labels_is_refused(issue):
    """Should never build a matcher-less silence — it would mute all of Alertmanager."""
    issue.grouping_labels = {}

    with pytest.raises(silences.SilenceError) as error:
        silences.build_matchers(issue)

    assert "every alert" in str(error.value)


# comment construction


def test_the_comment_names_the_issue(issue):
    """Should let whoever finds the silence in Alertmanager get back to the record."""
    result = silences.build_comment(issue)
    expected = (
        f"pandora issue #{issue.pk} — TargetDown: scrape target unreachable"
        f" — /admin/issues/issue/{issue.pk}/change/"
    )

    assert result == expected


@test.override_settings(PANDORA_BASE_URL="https://pandora.p-mk1.test/")
def test_a_configured_base_url_makes_the_comment_link_absolute(issue):
    """Should be clickable from Alertmanager or karma once the deployment has a host."""
    result = silences.issue_url(issue)
    expected = f"https://pandora.p-mk1.test/admin/issues/issue/{issue.pk}/change/"

    assert result == expected


# silence round trip


def test_silencing_posts_the_issue_matchers(alertmanager, alertmanager_client, issue):
    """Should ask Alertmanager to mute exactly this issue's label set."""
    silences.silence_issue(issue, ONE_HOUR, actor="admin", client=alertmanager_client)

    result = alertmanager.silence_bodies()[0]["matchers"]
    expected = silences.build_matchers(issue)

    assert result == expected


def test_silencing_bounds_the_silence_by_the_duration(
    alertmanager, alertmanager_client, issue, moment
):
    """Should end the silence exactly one duration after it starts."""
    silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client, now=moment)

    body = alertmanager.silence_bodies()[0]

    result = (body["startsAt"], body["endsAt"])
    expected = (moment.isoformat(), (moment + ONE_HOUR).isoformat())

    assert result == expected


def test_silencing_records_who_asked(alertmanager, alertmanager_client, issue):
    """Should attribute the silence to the admin user in Alertmanager's own UI."""
    silences.silence_issue(issue, ONE_HOUR, actor="admin", client=alertmanager_client)

    result = alertmanager.silence_bodies()[0]["createdBy"]
    expected = "admin"

    assert result == expected


def test_an_unattributed_silence_is_created_by_pandora(
    alertmanager, alertmanager_client, issue
):
    """Should still name a creator when the caller is the reconcile loop, not a person."""
    silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    result = alertmanager.silence_bodies()[0]["createdBy"]
    expected = "pandora"

    assert result == expected


def test_silencing_stores_the_link(alertmanager, alertmanager_client, issue, moment):
    """Should keep the id and expiry so the silence can be lifted and pruned."""
    link = silences.silence_issue(
        issue, ONE_HOUR, client=alertmanager_client, now=moment
    )

    stored = issue_models.SilenceLink.objects.get(pk=link.pk)

    result = (stored.issue_id, stored.am_silence_id, stored.expires_at)
    expected = (issue.pk, next(iter(alertmanager.silences)), moment + ONE_HOUR)

    assert result == expected


def test_silencing_writes_one_activity_row(alertmanager_client, issue, moment):
    """Should show the silence in the issue's own feed, not only in Alertmanager."""
    link = silences.silence_issue(
        issue, ONE_HOUR, actor="admin", client=alertmanager_client, now=moment
    )

    activity = issue_models.IssueActivity.objects.get(issue=issue)

    result = (activity.kind, activity.actor, activity.data["silence_id"])
    expected = ("silenced", "admin", link.am_silence_id)

    assert result == expected


def test_a_second_silence_is_its_own_link(alertmanager_client, issue):
    """Should let an operator extend a silence without losing the first record."""
    silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)
    silences.silence_issue(issue, ONE_HOUR * 4, client=alertmanager_client)

    result = issue_models.SilenceLink.objects.filter(issue=issue).count()
    expected = 2

    assert result == expected


def test_lifting_a_silence_expires_it_in_alertmanager(
    alertmanager, alertmanager_client, issue
):
    """Should complete the round trip the operator started from the changelist."""
    link = silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    silences.expire_silence(link, actor="admin", client=alertmanager_client)

    result = (alertmanager.silences, alertmanager.deleted_ids())
    expected = ({}, [link.am_silence_id])

    assert result == expected


def test_lifting_a_silence_drops_the_link(alertmanager_client, issue):
    """Should leave no bookkeeping behind for a silence that no longer exists."""
    link = silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    silences.expire_silence(link, client=alertmanager_client)

    result = issue_models.SilenceLink.objects.exists()

    assert result is False


def test_lifting_a_silence_is_stamped_when_it_happened(
    alertmanager_client, issue, moment
):
    """Should date the lift from the caller's clock, like every other activity row."""
    link = silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)
    lifted_at = moment + datetime.timedelta(minutes=20)

    silences.expire_silence(link, client=alertmanager_client, now=lifted_at)

    activity = issue_models.IssueActivity.objects.get(kind="unsilenced")

    result = activity.at
    expected = lifted_at

    assert result == expected


def test_lifting_a_silence_writes_one_activity_row(alertmanager_client, issue):
    """Should record the lift as its own event in the feed."""
    link = silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    silences.expire_silence(link, actor="admin", client=alertmanager_client)

    result = list(
        issue_models.IssueActivity.objects.filter(issue=issue)
        .order_by("pk")
        .values_list("kind", flat=True)
    )
    expected = ["silenced", "unsilenced"]

    assert result == expected


def test_silencing_without_a_client_builds_one_from_settings(
    alertmanager, issue, settings
):
    """Should let a caller with no client of its own reach the configured Alertmanager."""
    settings.PANDORA_AM_URL = alertmanager.url

    silences.silence_issue(issue, ONE_HOUR)

    result = len(alertmanager.silence_bodies())
    expected = 1

    assert result == expected


def test_lifting_without_a_client_builds_one_from_settings(
    alertmanager, alertmanager_client, issue, settings
):
    """Should build the client the same way on the way back out."""
    settings.PANDORA_AM_URL = alertmanager.url
    link = silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    silences.expire_silence(link)

    result = alertmanager.deleted_ids()
    expected = [link.am_silence_id]

    assert result == expected


# failure handling


def test_a_refused_silence_writes_no_link(alertmanager, alertmanager_client, issue):
    """Should never claim an issue is silenced when Alertmanager said no."""
    alertmanager.fail_next(500, times=1)

    with pytest.raises(am_client.AlertmanagerError):
        silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    result = issue_models.SilenceLink.objects.exists()

    assert result is False


def test_a_refused_lift_keeps_the_link(alertmanager, alertmanager_client, issue):
    """Should keep the bookkeeping when the silence is still live in Alertmanager."""
    link = silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)
    alertmanager.fail_next(500, times=4)

    with pytest.raises(am_client.AlertmanagerError):
        silences.expire_silence(link, client=alertmanager_client)

    result = issue_models.SilenceLink.objects.filter(pk=link.pk).exists()

    assert result is True


def test_an_issue_without_labels_never_reaches_alertmanager(
    alertmanager, alertmanager_client, issue
):
    """Should refuse before the request, not after Alertmanager has the silence."""
    issue.grouping_labels = {}

    with pytest.raises(silences.SilenceError):
        silences.silence_issue(issue, ONE_HOUR, client=alertmanager_client)

    result = alertmanager.calls
    expected = []

    assert result == expected
