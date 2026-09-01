import gzip
import http
import io
import json
import zlib

import pytest
from django import test

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import views
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


@pytest.mark.parametrize("encoding", ["br", "zstd"])
def test_a_body_with_a_broken_modern_encoding_is_refused(post, encoding):
    response = post(body=b"not compressed", headers={"Content-Encoding": encoding})

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


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


@pytest.mark.django_db
@test.override_settings(PANDORA_INGEST_MAX_BYTES=8192)
def test_a_compression_bomb_is_refused_without_inflating_it(post):
    """Should stop at the cap while decompressing, not after."""
    raw = default_body(message="x" * 5_000_000)
    body = gzip.compress(raw)
    assert len(body) < 8192 < len(raw)

    response = post(body=body, headers={"Content-Encoding": "gzip"})

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 0)
    assert result == expected


def test_a_zstd_body_is_decoded(post):
    """Should take what a busy SDK reaches for, which gzip is not."""
    import zstandard

    raw = default_body()
    body = zstandard.ZstdCompressor().compress(raw)

    response = post(body=body, headers={"Content-Encoding": "zstd"})

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


def test_a_brotli_body_is_decoded(post):
    """Should take the other encoding the protocol lists."""
    import brotli

    raw = default_body()
    body = brotli.compress(raw)

    response = post(body=body, headers={"Content-Encoding": "br"})

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


@test.override_settings(PANDORA_INGEST_MAX_BYTES=400)
def test_a_zstd_bomb_is_refused(post):
    """Should measure what the body becomes, whatever compressed it."""
    import zstandard

    raw = default_body(message="x" * 3000)
    body = zstandard.ZstdCompressor().compress(raw)

    response = post(body=body, headers={"Content-Encoding": "zstd"})

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


def test_an_event_item_over_the_per_item_limit_is_dropped(post):
    """Should refuse one oversized item without failing the whole envelope."""

    with test.override_settings(PANDORA_INGEST_MAX_BYTES=4 * 1024 * 1024):
        response = post(body=default_body(message="x" * (2 * 1024 * 1024)))

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 0)

    assert result == expected


def test_an_unknown_content_encoding_is_passed_through(post):
    """Should not guess — an encoding pandora does not know is left as bytes."""
    response = post(body=default_body(), headers={"Content-Encoding": "identity"})

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_an_oversized_session_item_is_dropped(post, dsn_key):
    """Should hold the per-item limit on the door sessions come through."""
    import json as json_module

    from pandora.releases import models as release_models

    big = {"sid": "s", "status": "exited", "attrs": {"release": "x" * 200000}}
    body = "\n".join(
        [
            json_module.dumps({}),
            json_module.dumps({"type": "session"}),
            json_module.dumps(big),
        ]
    ).encode()

    response = post(body=body)

    assert response.status_code == http.HTTPStatus.OK
    assert release_models.SessionBucket.objects.count() == 0


@test.override_settings(PANDORA_INGEST_MAX_BYTES=400)
def test_a_brotli_bomb_is_refused(post):
    """Should measure what the body becomes on every encoding, not just some."""
    import brotli

    raw = default_body(message="x" * 3000)
    body = brotli.compress(raw)

    response = post(body=body, headers={"Content-Encoding": "br"})

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


# the store endpoint


def test_the_store_endpoint_takes_a_bare_event(client, dsn_key):
    """Should remove a whole class of 'why does my client not work'."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/",
        data=json.dumps({"event_id": "a" * 32, "message": "boom"}),
        content_type="application/json",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


def test_the_store_endpoint_takes_the_key_as_a_query_parameter(client, dsn_key):
    """Should accept both auth forms, like the envelope door does."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=json.dumps({"message": "boom"}),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_the_store_endpoint_mints_an_event_id_when_none_was_sent(client, dsn_key):
    """Should let a hand-rolled curl work without inventing an id first."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=json.dumps({"message": "boom"}),
        content_type="application/json",
    )

    assert response.json()["id"]


def test_the_store_endpoint_refuses_an_unknown_key(client, dsn_key):
    """Should sit behind the same key as everything else."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={'z' * 32}",
        data=json.dumps({"message": "boom"}),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_the_store_endpoint_refuses_a_non_object(client, dsn_key):
    """Should name what is wrong rather than store a list."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=json.dumps([1, 2, 3]),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_the_store_endpoint_refuses_a_get(client, dsn_key):
    """Should be a POST like every other door."""
    response = client.get(f"/api/{dsn_key.project_id}/store/")

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_the_store_endpoint_refuses_a_body_that_is_not_json(client, dsn_key):
    """Should say so rather than store bytes nobody can read."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=b"not json",
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


@pytest.mark.parametrize("timestamp", ["1e309", "NaN", "Infinity"])
def test_the_store_endpoint_refuses_non_finite_json(client, dsn_key, timestamp):
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=f'{{"message":"boom","timestamp":{timestamp}}}',
        content_type="application/json",
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST
    assert ingest_models.RawEnvelope.objects.count() == 0


def test_a_dropped_payload_never_reaches_the_store_endpoint_inbox(client, dsn_key):
    """Should honour a drop rule on this door too — a door that skips it is a hole."""
    from pandora.scrub import models as scrub_models

    scrub_models.DropRule.objects.create(
        name="noise", field="environment", pattern="^throwaway$"
    )

    client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=json.dumps({"message": "boom", "environment": "throwaway"}),
        content_type="application/json",
    )

    result = ingest_models.RawEnvelope.objects.count()
    expected = 0

    assert result == expected


# user reports


def test_a_user_report_is_stored(client, dsn_key):
    """Should accept the form, and not build the widget that collects it."""
    from pandora.issues import models as issue_models

    body = "\n".join(
        [
            json.dumps({}),
            json.dumps({"type": "user_report"}),
            json.dumps(
                {
                    "event_id": "b" * 32,
                    "name": "Ada",
                    "email": "ada@shop.test",
                    "comments": "the checkout button did nothing",
                }
            ),
        ]
    ).encode()

    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    report = issue_models.UserReport.objects.get()
    result = (report.name, report.comments)
    expected = ("Ada", "the checkout button did nothing")

    assert result == expected


def test_a_user_report_with_no_event_id_is_dropped(client, dsn_key):
    """Should not store feedback that can never be attached to anything."""
    from pandora.issues import models as issue_models

    body = "\n".join(
        [
            json.dumps({}),
            json.dumps({"type": "user_report"}),
            json.dumps({"comments": "orphan"}),
        ]
    ).encode()

    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    result = issue_models.UserReport.objects.count()
    expected = 0

    assert result == expected


def test_a_user_report_finds_the_issue_its_event_became(client, dsn_key, post):
    """Should attach to the issue, which is where a person will read it."""
    from pandora.issues import models as issue_models

    post(body=default_body())
    issue = issue_models.Issue.objects.get()

    body = "\n".join(
        [
            json.dumps({}),
            json.dumps({"type": "user_report"}),
            json.dumps({"event_id": "a" * 32, "comments": "it broke"}),
        ]
    ).encode()
    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    result = issue_models.UserReport.objects.get().issue_id
    expected = issue.pk

    assert result == expected


def test_a_report_reads_as_who_said_it(client, dsn_key):
    """Should be legible in the admin without following the event id."""
    from pandora.issues import models as issue_models

    report = issue_models.UserReport(name="Ada", event_id="b" * 32)

    result = str(report)

    assert result.startswith("Ada on")


def test_a_user_report_that_is_not_json_is_dropped(client, dsn_key):
    """Should ack the envelope rather than fail it on one bad item."""
    from pandora.issues import models as issue_models

    body = b'{}\n{"type": "user_report"}\nnot json'

    response = client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    assert response.status_code == http.HTTPStatus.OK
    assert issue_models.UserReport.objects.count() == 0


def test_a_user_report_that_is_not_an_object_is_dropped(client, dsn_key):
    """Should refuse a list where a form was expected."""
    from pandora.issues import models as issue_models

    body = "\n".join(
        [json.dumps({}), json.dumps({"type": "user_report"}), json.dumps([1, 2])]
    ).encode()

    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    assert issue_models.UserReport.objects.count() == 0


def test_a_user_report_for_an_unknown_event_is_still_kept(client, dsn_key):
    """Should keep the feedback even when the event it names never arrived."""
    from pandora.issues import models as issue_models

    body = "\n".join(
        [
            json.dumps({}),
            json.dumps({"type": "user_report"}),
            json.dumps({"event_id": "f" * 32, "comments": "orphan"}),
        ]
    ).encode()

    client.post(
        f"/api/{dsn_key.project_id}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}"},
    )

    result = issue_models.UserReport.objects.get().issue_id

    assert result is None


@test.override_settings(PANDORA_INGEST_MAX_BYTES=400)
def test_the_store_endpoint_holds_the_size_cap(client, dsn_key):
    """Should refuse an oversized body like the envelope door does."""
    response = client.post(
        f"/api/{dsn_key.project_id}/store/?sentry_key={dsn_key.public_key}",
        data=json.dumps({"message": "x" * 3000}),
        content_type="application/json",
    )

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


# chunked bodies, which is how the JavaScript SDK sends every envelope


def chunked(dsn_key, body, key=PUBLIC_KEY):
    """Post the way `@sentry/node` does: no Content-Length, a chunked stream.

    The test client cannot express this — its payload refuses a read past the
    declared length — so the request is built by hand with a real stream.
    """
    factory = test.RequestFactory()
    request = factory.post(
        f"/api/{dsn_key.project_id}/envelope/?sentry_key={key}",
        data=body,
        content_type=ENVELOPE_TYPE,
    )
    request.environ.pop("CONTENT_LENGTH", None)
    request.META.pop("CONTENT_LENGTH", None)
    request.environ["HTTP_TRANSFER_ENCODING"] = "chunked"
    request.META["HTTP_TRANSFER_ENCODING"] = "chunked"
    request.environ["wsgi.input"] = io.BytesIO(body)
    return views.envelope(request, dsn_key.project_id)


def test_a_chunked_envelope_is_accepted(dsn_key, settings, published):
    """Should read the body Django sizes at zero.

    Django builds its request stream from `Content-Length` alone, so a chunked
    request reads as empty. `@sentry/node` sends every envelope chunked, and it
    was refused with a 400 nobody could explain until the stream was drained.
    """
    settings.PANDORA_QUEUE = RECORDING_QUEUE

    response = chunked(dsn_key, default_body())

    result = (response.status_code, len(published))
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


def test_a_chunked_envelope_keeps_its_event(dsn_key, settings):
    """Should store what the stream held, not an empty envelope."""
    settings.PANDORA_QUEUE = INLINE_QUEUE

    chunked(dsn_key, default_body(event_id="c" * 32))

    result = ingest_models.RawEnvelope.objects.get().payload["event_id"]
    expected = "c" * 32

    assert result == expected


def test_an_oversized_chunked_envelope_is_refused(dsn_key, settings):
    """Should hold the size cap on what arrives, not on what is declared."""
    settings.PANDORA_INGEST_MAX_BYTES = 256

    response = chunked(dsn_key, b"x" * 4096)

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


def test_a_chunked_request_with_no_stream_falls_back_to_the_body(dsn_key, settings):
    """Should not raise on a server that reports chunked without a WSGI stream."""
    settings.PANDORA_QUEUE = INLINE_QUEUE
    factory = test.RequestFactory()
    request = factory.post(
        f"/api/{dsn_key.project_id}/envelope/?sentry_key={PUBLIC_KEY}",
        data=default_body(event_id="e" * 32),
        content_type=ENVELOPE_TYPE,
    )
    request.META["HTTP_TRANSFER_ENCODING"] = "chunked"
    request.environ["HTTP_TRANSFER_ENCODING"] = "chunked"
    request.environ.pop("wsgi.input", None)

    response = views.envelope(request, dsn_key.project_id)

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected
