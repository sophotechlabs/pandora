import http
import json

import pytest
from django import test, urls

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import views
from pandora.issues import models as issue_models
from tests.ingest import fakes

INLINE_QUEUE = "tests.ingest.fakes.InlineQueue"
RECORDING_QUEUE = "tests.ingest.fakes.RecordingQueue"
AM_URL = "/ingest/am/"


@pytest.fixture
def post(client, am_fixture):
    def send(payload=None, token="test-ingest-token", scheme="Bearer"):
        if payload is None:
            payload = am_fixture("firing_group")
        headers = {}
        if token is not None:
            headers["Authorization"] = f"{scheme} {token}"
        return client.post(
            AM_URL,
            data=json.dumps(payload),
            content_type="application/json",
            headers=headers,
        )

    return send


@pytest.fixture
def published():
    fakes.RecordingQueue.published.clear()
    yield fakes.RecordingQueue.published
    fakes.RecordingQueue.published.clear()


@pytest.fixture
def inline_rows():
    fakes.INLINE_ROWS.clear()
    yield fakes.INLINE_ROWS
    fakes.INLINE_ROWS.clear()


# route contract


def test_alertmanager_route_is_a_bare_path():
    """Should expose the Alertmanager door at /ingest/am/ with no token in the URL."""
    result = urls.reverse("ingest-am")
    expected = AM_URL

    assert result == expected


def test_envelope_route_matches_the_sentry_scheme():
    """Should expose the SDK door at the path Sentry SDKs build from a DSN."""
    result = urls.reverse("ingest-envelope", args=[7])
    expected = "/api/7/envelope/"

    assert result == expected


def test_envelope_route_constrains_the_project_to_an_integer():
    """Should hand the view an int project id, never a string."""
    result = urls.resolve("/api/7/envelope/").kwargs
    expected = {"project_id": 7}

    assert result == expected


def test_envelope_route_rejects_a_non_numeric_project():
    """Should not resolve a DSN path with a non-numeric project id."""
    with pytest.raises(urls.Resolver404):
        urls.resolve("/api/seven/envelope/")


@pytest.mark.django_db
def test_envelope_door_answers_501_until_phase_seven(client):
    """Should answer 501 on the frozen SDK route rather than 404."""
    response = client.post("/api/7/envelope/", data=b"", content_type="text/plain")

    result = response.status_code
    expected = http.HTTPStatus.NOT_IMPLEMENTED
    assert result == expected


# authentication


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_valid_bearer_token_is_accepted(post, token, published):
    """Should take the token Alertmanager sends in the Authorization header."""
    response = post()

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


@pytest.mark.django_db
def test_a_request_without_a_token_is_rejected(post, token):
    """Should refuse an unauthenticated POST to a public ingest path."""
    response = post(token=None)

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_an_unknown_token_is_rejected(post, token):
    """Should refuse a token no IngestToken row matches."""
    response = post(token="not-the-token")

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_a_token_prefix_is_not_enough(post, token):
    """Should compare the whole token, never a prefix of it."""
    response = post(token="test-ingest")

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_a_non_bearer_scheme_is_rejected(post, token):
    """Should ignore Basic auth — Alertmanager sends Bearer."""
    response = post(scheme="Basic")

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_an_empty_bearer_value_is_rejected(post, token):
    """Should refuse an Authorization header with nothing after Bearer."""
    response = post(token="")

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_a_deactivated_token_is_rejected(post, token):
    """Should stop accepting a token an operator switched off."""
    token.active = False
    token.save(update_fields=["active"])

    response = post()

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_a_read_scoped_token_cannot_ingest(post, token):
    """Should keep an API read token out of the ingest door."""
    token.scope = core_models.TokenScope.READ
    token.save(update_fields=["scope"])

    response = post()

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_an_sdk_token_cannot_use_the_alertmanager_door(post, token):
    """Should keep the two front doors on separate credentials."""
    token.source = core_models.TokenSource.SDK
    token.save(update_fields=["source"])

    response = post()

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


@pytest.mark.django_db
def test_a_rejected_token_is_logged(post, token, caplog):
    """Should leave a trace when something posts with the wrong credentials."""
    with caplog.at_level("WARNING"):
        post(token="not-the-token")

    assert "unknown or missing token" in caplog.text


@pytest.mark.django_db
def test_a_rejected_request_writes_nothing(post, token):
    """Should never spend a durable write on an unauthenticated request."""
    post(token=None)

    result = ingest_models.RawEnvelope.objects.count()
    expected = 0

    assert result == expected


# method and body


@pytest.mark.django_db
def test_a_get_is_not_allowed(client, token):
    """Should answer 405 rather than pretending a GET is an ingest."""
    response = client.get(AM_URL)

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


@pytest.mark.django_db
def test_the_door_is_csrf_exempt(client, token):
    """Should accept a cross-origin POST without a CSRF token."""
    response = client.post(AM_URL, data="{}", content_type="application/json")

    assert response.status_code != http.HTTPStatus.FORBIDDEN


@pytest.mark.django_db
def test_a_body_that_is_not_json_is_rejected(client, token):
    """Should answer 400 on a body no translator could ever read."""
    response = client.post(
        AM_URL,
        data="not json",
        content_type="application/json",
        headers={"Authorization": "Bearer test-ingest-token"},
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


@pytest.mark.django_db
def test_a_body_that_is_not_an_object_is_rejected(post, token):
    """Should answer 400 on a JSON array where the webhook object belongs."""
    response = post(payload=[])

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


@pytest.mark.django_db
def test_a_malformed_body_writes_nothing(post, token):
    """Should keep unreadable payloads out of the inbox."""
    post(payload=[])

    result = ingest_models.RawEnvelope.objects.count()
    expected = 0

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_payload_of_the_wrong_version_is_still_stored(post, token, published):
    """Should keep a version drift replayable instead of answering 400."""
    response = post(payload={"version": "5", "alerts": []})

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


class StubRequest:
    def __init__(self, headers, body):
        self.headers = headers
        self.body = body


def test_the_size_gate_reads_the_content_length_header():
    """Should cap on the declared size, before the body is pulled into memory."""
    request = StubRequest({"Content-Length": "512"}, b"")

    result = views._content_length(request)
    expected = 512

    assert result == expected


def test_the_size_gate_falls_back_to_the_body_it_received():
    """Should still measure a chunked request that declares no length."""
    request = StubRequest({}, b"0123456789")

    result = views._content_length(request)
    expected = 10

    assert result == expected


# the size gate


@pytest.mark.django_db
@test.override_settings(PANDORA_INGEST_MAX_BYTES=10)
def test_an_oversized_body_is_rejected_with_413(post, token):
    """Should stop a large body at the gate, before the durable write."""
    response = post()

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_INGEST_MAX_BYTES=10)
def test_an_oversized_body_writes_nothing(post, token):
    """Should keep the gate in front of the disk, not behind it."""
    post()

    result = ingest_models.RawEnvelope.objects.count()
    expected = 0

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_INGEST_MAX_BYTES=10)
def test_an_oversized_body_says_why(post, token):
    """Should tell Alertmanager which limit it tripped."""
    response = post()

    result = response.json()
    expected = {"detail": "oversized"}

    assert result == expected


# the durable write


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_the_payload_lands_in_the_inbox_verbatim(post, token, published, am_fixture):
    """Should store exactly what Alertmanager sent, for replay and regroup."""
    post()

    result = ingest_models.RawEnvelope.objects.get().payload
    expected = am_fixture("firing_group")

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_the_envelope_carries_the_token_project_and_environment(post, token, published):
    """Should stamp the envelope so the consumer never needs the token again."""
    post()
    envelope = ingest_models.RawEnvelope.objects.get()

    result = (envelope.project_id, envelope.environment, envelope.source)
    expected = (token.project_id, "p-mk1", core_models.TokenSource.AM)

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_the_envelope_is_published_to_the_queue(post, token, published):
    """Should hand the consumer exactly the row it just wrote."""
    post()

    result = published
    expected = [ingest_models.RawEnvelope.objects.get().pk]

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_the_response_names_the_stored_envelope(post, token, published):
    """Should answer with the inbox id so a delivery can be traced."""
    response = post()

    result = response.json()
    expected = {"id": ingest_models.RawEnvelope.objects.get().pk}

    assert result == expected


# end to end


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=INLINE_QUEUE)
def test_a_delivered_group_becomes_a_triageable_issue(post, token, inline_rows):
    """Should turn one webhook into an issue an operator can act on."""
    post()
    issue = issue_models.Issue.objects.get()

    result = (issue.title, issue.source_state, issue.open_episode_count)
    expected = (
        "KubePodCrashLooping: Pod is crash looping.",
        "firing",
        2,
    )

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=INLINE_QUEUE)
def test_a_delivered_group_closes_its_envelope(post, token, inline_rows):
    """Should finish the inbox row inside the request that created it."""
    post()

    result = ingest_models.RawEnvelope.objects.get().state
    expected = ingest_models.EnvelopeState.DONE

    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=INLINE_QUEUE)
def test_a_second_identical_delivery_only_moves_the_counters(post, token, inline_rows):
    """Should treat a repeat_interval resend as a delivery, not a new episode."""
    post()
    post()
    issue = issue_models.Issue.objects.get()

    result = (
        issue.event_count,
        sorted(
            episode.delivery_count for episode in issue_models.Episode.objects.all()
        ),
    )
    expected = (2, [2, 2])

    assert result == expected
