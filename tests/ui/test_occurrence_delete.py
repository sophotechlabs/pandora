import datetime

import pytest
from django.contrib.auth import models as auth_models
from django.utils import timezone

from pandora.events import types
from pandora.issues import models
from tests.web import fakes

pytestmark = pytest.mark.django_db


@pytest.fixture
def issue(project):
    return models.Issue.objects.create(
        project=project,
        fingerprint_hash="abc123",
        fingerprint=["boom"],
        title="boom",
        culprit="boom",
        level=models.Level.ERROR,
        environment="p-mk1",
        first_seen=timezone.now() - datetime.timedelta(hours=1),
        last_seen=timezone.now(),
    )


@pytest.fixture
def stored(issue, mocker):
    events = [
        types.Event(
            id=f"01J8ZQ7X4N{index:022d}",
            project_id=issue.project_id,
            timestamp=timezone.now(),
            level="error",
            message=f"occurrence {index}",
            issue_id=issue.pk,
            source="sdk",
            environment="p-mk1",
            payload={"user": {"email": "a@b.test"}},
        )
        for index in (1, 2)
    ]
    store = fakes.FakeEventStore(events)
    mocker.patch("pandora.ui.views.get_store", return_value=store)
    return store


@pytest.fixture
def reader_client(client, db):
    watcher = auth_models.User.objects.create_user(
        username="watcher",
        password="watcher-pass",
        is_staff=True,
    )
    client.force_login(watcher)
    return client


def url(issue, event_id):
    return f"/issues/{issue.pk}/occurrences/{event_id}/delete/"


# removing one occurrence


def test_an_operator_can_delete_one_occurrence(operator_client, issue, stored):
    """Should let a leaked payload be removed without dropping the whole issue."""
    operator_client.post(url(issue, stored.events[0].id), {"next": "/"})

    result = [event.id for event in stored.events]
    expected = ["01J8ZQ7X4N" + "0" * 21 + "2"]

    assert result == expected


def test_deleting_redirects_back(operator_client, issue, stored):
    """Should land the operator back where they were, not on a blank page."""
    response = operator_client.post(url(issue, stored.events[0].id), {"next": "/"})

    result = response.status_code
    expected = 302

    assert result == expected


def test_deleting_an_absent_occurrence_is_not_an_error(operator_client, issue, stored):
    """Should tolerate a double submit or a row that prune already removed."""
    response = operator_client.post(url(issue, "01J8ZQ7X4Nabsent"), {"next": "/"})

    result = (response.status_code, len(stored.events))
    expected = (302, 2)

    assert result == expected


def test_a_store_without_fetch_is_not_an_error(operator_client, issue, mocker):
    """Should not fail on a database whose store keeps no single occurrences."""
    store = mocker.Mock()
    store.fetch.side_effect = NotImplementedError
    mocker.patch("pandora.ui.views.get_store", return_value=store)

    result = operator_client.post(url(issue, "anything"), {"next": "/"}).status_code
    expected = 302

    assert result == expected


# who may


def test_a_reader_may_not_delete(reader_client, issue, stored):
    """Should need the same permission triage needs — deleting evidence is not a read."""
    response = reader_client.post(url(issue, stored.events[0].id), {"next": "/"})

    result = (response.status_code, len(stored.events))
    expected = (403, 2)

    assert result == expected


def test_signing_in_is_required(client, issue, stored):
    """Should not expose a destructive route to anyone who can reach the port."""
    response = client.post(url(issue, stored.events[0].id), {"next": "/"})

    result = response.status_code
    expected = 302

    assert result == expected


def test_a_get_is_refused(operator_client, issue, stored):
    """Should never delete on a link click or a crawler."""
    response = operator_client.get(url(issue, stored.events[0].id))

    result = (response.status_code, len(stored.events))
    expected = (405, 2)

    assert result == expected


# the button


def test_the_occurrences_tab_offers_the_button(operator_client, issue, stored):
    """Should be reachable from where the occurrence is read."""
    page = operator_client.get(f"/issues/{issue.pk}/occurrences/").content.decode()

    result = "Delete this occurrence" in page

    assert result is True


def test_a_reader_is_not_offered_the_button(reader_client, issue, stored):
    """Should not show an action the viewer cannot take."""
    page = reader_client.get(f"/issues/{issue.pk}/occurrences/").content.decode()

    result = "Delete this occurrence" in page

    assert result is False
