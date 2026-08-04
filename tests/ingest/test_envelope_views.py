import gzip
import http
import json
import zlib

import pytest
from django import test

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from tests.ingest import fakes

RECORDING_QUEUE = "tests.ingest.fakes.RecordingQueue"
INLINE_QUEUE = "tests.ingest.fakes.InlineQueue"
ENVELOPE_TYPE = "application/x-sentry-envelope"
PUBLIC_KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def dsn_key(project):
    return core_models.DsnKey.objects.create(project=project, public_key=PUBLIC_KEY)


@pytest.fixture
def published():
    fakes.RecordingQueue.published.clear()
    yield fakes.RecordingQueue.published
    fakes.RecordingQueue.published.clear()


def line(payload):
    return json.dumps(payload).encode()


def envelope_body(*parts):
    return b"\n".join(parts)


def default_body(event_id="a" * 32, **event):
    payload = {"event_id": event_id, "message": "boom"}
    payload.update(event)
    return envelope_body(
        line({"event_id": event_id}),
        line({"type": "event"}),
        line(payload),
    )


@pytest.fixture
def post(client, dsn_key):
    def send(body=None, key=PUBLIC_KEY, query=False, headers=None, **extra):
        if body is None:
            body = default_body()
        url = f"/api/{dsn_key.project_id}/envelope/"
        sent = dict(headers or {})
        if key is not None and not query:
            sent["X-Sentry-Auth"] = (
                f"Sentry sentry_version=7, sentry_key={key}, "
                "sentry_client=sentry.python/2.0"
            )
        if key is not None and query:
            url = f"{url}?sentry_key={key}"
        return client.post(
            url, data=body, content_type=ENVELOPE_TYPE, headers=sent, **extra
        )

    return send


# authentication


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_known_dsn_key_in_the_auth_header_is_accepted(post, published):
    """Should authenticate the way every Sentry SDK sends its key."""
    response = post()

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_dsn_key_in_the_query_string_is_accepted(post, published):
    """Should accept the query-string form older SDKs and tunnels use."""
    response = post(query=True)

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


@pytest.mark.django_db
def test_a_missing_key_is_rejected(post):
    """Should refuse an unauthenticated envelope."""
    response = post(key=None)

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


@pytest.mark.django_db
def test_an_unknown_key_is_rejected(post):
    """Should refuse a key that belongs to nothing."""
    response = post(key="f" * 32)

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


@pytest.mark.django_db
def test_an_inactive_key_is_rejected(post, dsn_key):
    """Should refuse a revoked key without deleting the row."""
    dsn_key.active = False
    dsn_key.save(update_fields=["active"])

    response = post()

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


@pytest.mark.django_db
def test_a_key_from_another_project_is_rejected(client, dsn_key):
    """Should bind the key to the project id in the path."""
    other = core_models.Project.objects.create(slug="other", name="Other")
    url = f"/api/{other.pk}/envelope/"
    headers = {"X-Sentry-Auth": f"Sentry sentry_key={PUBLIC_KEY}"}

    response = client.post(
        url, data=default_body(), content_type=ENVELOPE_TYPE, headers=headers
    )

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


@pytest.mark.django_db
def test_an_auth_header_without_the_sentry_scheme_is_rejected(post):
    """Should not read a key out of a header that is not Sentry's."""
    response = post(key=None, headers={"X-Sentry-Auth": f"Bearer {PUBLIC_KEY}"})

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


@pytest.mark.django_db
def test_an_auth_header_without_a_key_field_is_rejected(post):
    """Should refuse a Sentry header that names no key."""
    response = post(key=None, headers={"X-Sentry-Auth": "Sentry sentry_version=7"})

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_malformed_auth_pair_is_skipped_not_fatal(post, published):
    """Should keep reading the header past a field with no equals sign."""
    header = {"X-Sentry-Auth": f"Sentry nonsense, sentry_key={PUBLIC_KEY}"}

    response = post(key=None, headers=header)

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


@pytest.mark.django_db
def test_only_post_is_allowed(client, dsn_key):
    """Should refuse a GET on the ingest door."""
    response = client.get(f"/api/{dsn_key.project_id}/envelope/")

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED
    assert result == expected


# size


@pytest.mark.django_db
@test.override_settings(PANDORA_INGEST_MAX_BYTES=10)
def test_an_oversized_body_is_rejected_before_the_write(post):
    """Should stop at the declared length, before anything is stored."""
    response = post()

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 0)
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_INGEST_MAX_BYTES=400)
def test_a_body_that_inflates_past_the_cap_is_rejected(post):
    """Should measure what a compressed body becomes, not what it claims."""
    raw = default_body(message="x" * 3000)
    body = gzip.compress(raw)
    headers = {"Content-Encoding": "gzip"}
    assert len(body) < 400 < len(raw)

    response = post(body=body, headers=headers)

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 0)
    assert result == expected


# compression


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_gzipped_envelope_is_read(post, published):
    """Should accept the encoding the SDKs use by default."""
    body = gzip.compress(default_body())

    response = post(body=body, headers={"Content-Encoding": "gzip"})

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 1)
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_a_deflated_envelope_is_read(post, published):
    """Should accept deflate as well as gzip."""
    body = zlib.compress(default_body())

    response = post(body=body, headers={"Content-Encoding": "deflate"})

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 1)
    assert result == expected


@pytest.mark.django_db
def test_a_body_that_is_not_really_compressed_is_refused(post):
    """Should answer 400, not 500, when the encoding header lies."""
    response = post(body=b"plain text", headers={"Content-Encoding": "gzip"})

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST
    assert result == expected


# envelope handling


@pytest.mark.django_db
def test_a_malformed_envelope_is_refused(post):
    """Should answer 400 when the envelope cannot be split."""
    response = post(body=b"not an envelope")

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_the_response_carries_the_event_id(post, published):
    """Should answer with the id the SDK sent, which is what it logs."""
    response = post()

    result = json.loads(response.content)
    expected = {"id": "a" * 32}
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_an_event_item_is_stored_and_queued(post, published):
    """Should write the durable row and hand the id to the consumer."""
    post()

    envelope = ingest_models.RawEnvelope.objects.get()
    result = (envelope.source, envelope.state, list(published))
    expected = (
        core_models.TokenSource.SDK,
        ingest_models.EnvelopeState.PENDING,
        [envelope.pk],
    )
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_the_payload_environment_reaches_the_envelope_row(post, published):
    """Should record the environment so the consumer need not re-read it."""
    body = default_body(environment="staging")

    post(body=body)

    result = ingest_models.RawEnvelope.objects.get().environment
    expected = "staging"
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_two_event_items_become_two_rows(post, published):
    """Should store one envelope per event, as the plan pins it."""
    body = envelope_body(
        line({"event_id": "a" * 32}),
        line({"type": "event"}),
        line({"event_id": "a" * 32, "message": "one"}),
        line({"type": "event"}),
        line({"event_id": "b" * 32, "message": "two"}),
    )

    post(body=body)

    result = (ingest_models.RawEnvelope.objects.count(), len(published))
    expected = (2, 2)
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_non_event_items_are_acked_and_dropped(post, published):
    """Should answer 200 so the SDK does not retry what pandora will not keep."""
    body = envelope_body(
        line({"event_id": "a" * 32}),
        line({"type": "transaction"}),
        line({"spans": []}),
        line({"type": "session"}),
        line({"status": "ok"}),
    )

    response = post(body=body)

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 0)
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_an_event_item_that_is_not_json_is_dropped_not_fatal(post, published):
    """Should ack the envelope and skip the item rather than answer 500."""
    body = envelope_body(
        line({"event_id": "a" * 32}),
        line({"type": "event"}),
        b"not json",
    )

    response = post(body=body)

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 0)
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_an_event_item_that_is_not_an_object_is_dropped(post, published):
    """Should skip a JSON array where an event object belongs."""
    body = envelope_body(
        line({"event_id": "a" * 32}),
        line({"type": "event"}),
        line([1, 2, 3]),
    )

    response = post(body=body)

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 0)
    assert result == expected


@pytest.mark.django_db
@test.override_settings(PANDORA_QUEUE=RECORDING_QUEUE)
def test_an_item_without_its_own_id_inherits_the_envelope_one(post, published):
    """Should keep dedup working when only the header carries the id."""
    body = envelope_body(
        line({"event_id": "c" * 32}),
        line({"type": "event"}),
        line({"message": "no id of its own"}),
    )

    post(body=body)

    result = ingest_models.RawEnvelope.objects.get().payload["event_id"]
    expected = "c" * 32
    assert result == expected
