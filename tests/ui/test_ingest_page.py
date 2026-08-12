import http

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth import models as auth_models

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from tests.ingest import helpers

pytestmark = pytest.mark.django_db

REPLAY = "/ingest/replay/"


def body(client):
    return client.get("/ingest/").content.decode()


def notes(response):
    return [
        str(message) for message in django_messages.get_messages(response.wsgi_request)
    ]


def failed_envelope(token, am_fixture):
    envelope = helpers.store_envelope(am_fixture("firing_group"), token)
    envelope.state = ingest_models.EnvelopeState.FAILED
    envelope.error = "translator refused the payload"
    envelope.save(update_fields=["state", "error"])
    return envelope


# the page


def test_an_empty_inbox_reads_as_healthy(operator_client):
    """Should render a first-boot page without a division or None error."""
    page = body(operator_client)

    assert "Nothing has failed" in page
    assert "No ingest token exists yet" in page


def test_the_backlog_counts_failed_and_pending(operator_client, token, am_fixture):
    """Should show the operator there is something for replay to pick up."""
    failed_envelope(token, am_fixture)
    helpers.store_envelope(am_fixture("firing_group"), token)

    response = operator_client.get("/ingest/")

    result = response.context["backlog"]
    expected = 2

    assert result == expected


def test_a_failed_envelope_shows_the_error_it_carried(
    operator_client, token, am_fixture
):
    """Should say why it failed without a shell on the box."""
    failed_envelope(token, am_fixture)

    assert "translator refused the payload" in body(operator_client)


def test_the_last_accepted_delivery_is_named(operator_client, token, am_fixture):
    """Should tell the operator when pandora last took anything in."""
    envelope = helpers.store_envelope(am_fixture("firing_group"), token)
    envelope.state = ingest_models.EnvelopeState.DONE
    envelope.save(update_fields=["state"])

    response = operator_client.get("/ingest/")

    result = response.context["last_accepted"]
    expected = envelope.received_at

    assert result == expected


def test_the_token_list_never_prints_the_token(operator_client, token):
    """Should say which doors are open without handing over the keys."""
    page = body(operator_client)

    assert token.name in page
    assert token.token not in page


def test_an_inactive_token_is_flagged(operator_client, project):
    """Should let an operator see a revoked door during an incident."""
    core_models.IngestToken.objects.create(
        project=project,
        name="retired reader",
        token="retired-token",
        scope=core_models.TokenScope.READ,
        active=False,
    )

    page = body(operator_client)

    assert "pill-default" in page


def test_the_page_renders(operator_client):
    """Should paint every section off the database alone."""
    response = operator_client.get("/ingest/")

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


# replay


def test_replay_clears_the_backlog(operator_client, token, am_fixture):
    """Should apply what the consumer dropped, from the page that reports it."""
    failed_envelope(token, am_fixture)

    operator_client.post(REPLAY)

    result = ingest_models.RawEnvelope.objects.filter(
        state=ingest_models.EnvelopeState.DONE
    ).count()
    expected = 1

    assert result == expected


def test_replay_reports_what_it_did(operator_client, token, am_fixture):
    """Should say how many were attempted, applied and still failing."""
    failed_envelope(token, am_fixture)

    response = operator_client.post(REPLAY, follow=True)

    result = notes(response)
    expected = ["Replayed 1 envelope(s): 1 applied, 0 still failing"]

    assert result == expected


def test_replay_returns_to_the_ingest_page(operator_client):
    """Should leave the operator looking at the number it just changed."""
    response = operator_client.post(REPLAY)

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/ingest/")

    assert result == expected


def test_replay_needs_a_post(operator_client):
    """Should keep a link or a prefetch from re-running the consumer."""
    response = operator_client.get(REPLAY)

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_replay_needs_the_envelope_change_permission(client, token, am_fixture):
    """Should keep a read-only operator from re-running ingest."""
    watcher = auth_models.User.objects.create_user(
        username="watcher",
        password="watcher-pass",
        is_staff=True,
    )
    client.force_login(watcher)
    failed_envelope(token, am_fixture)

    response = client.post(REPLAY)

    assert response.status_code == http.HTTPStatus.FORBIDDEN
    assert not ingest_models.RawEnvelope.objects.filter(
        state=ingest_models.EnvelopeState.DONE
    ).exists()
