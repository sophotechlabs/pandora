import json

import pytest

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.scrub.models import DropRule, ScrubRule
from pandora.scrub.rules import REDACTED
from tests.ingest import fakes

pytestmark = pytest.mark.django_db


def event_payload(**overrides):
    payload = {
        "event_id": "b" * 32,
        "level": "error",
        "platform": "python",
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "app.views",
                                "function": "handle",
                                "in_app": True,
                                "vars": {"password": "hunter2", "count": "3"},
                            }
                        ]
                    },
                }
            ]
        },
        "user": {"id": "7", "ip_address": "203.0.113.44", "email": "a@b.test"},
        "request": {"headers": {"Authorization": "Bearer abc", "Accept": "*/*"}},
        "extra": {"note": "card 4111111111111111"},
        "tags": {"api_key": "sk-live-1", "route": "/basket"},
    }
    payload.update(overrides)
    return payload


def deliver(project, payload=None):
    store = fakes.RecordingEventStore()
    envelope = ingest_models.RawEnvelope.objects.create(
        project=project,
        source=core_models.TokenSource.SDK,
        payload=payload or event_payload(),
    )
    processor.process_envelope(envelope.pk, store=store)
    return store.rows[0]


# what reaches the store


def test_a_frame_local_named_like_a_secret_is_redacted(project):
    """Should stop the most common accidental leak — a password sitting in the locals of a stack frame."""
    stored = deliver(project)

    variables = stored.payload["exceptions"][0]["frames"][0]["vars"]

    result = (variables["password"], variables["count"])
    expected = (REDACTED, "3")

    assert result == expected


def test_an_authorization_header_is_redacted(project):
    """Should never keep a bearer token an SDK sent along with the request interface."""
    headers = deliver(project).payload["request"]["headers"]

    result = (headers["Authorization"], headers["Accept"])
    expected = (REDACTED, "*/*")

    assert result == expected


def test_a_client_ip_loses_its_last_octet(project):
    """Should keep the network for debugging and drop the household."""
    result = deliver(project).payload["user"]["ip_address"]
    expected = "203.0.113.0"

    assert result == expected


def test_a_card_number_in_extra_is_masked(project):
    """Should catch what no key name would — a card in a free-text note."""
    result = deliver(project).payload["extra"]["note"]
    expected = f"card {REDACTED}"

    assert result == expected


def test_a_secret_looking_tag_is_redacted(project):
    """Should scrub the tag breakdown too, which the UI shows to everyone who can read an issue."""
    stored = deliver(project)

    result = (stored.tags["api_key"], stored.tags["route"])
    expected = (REDACTED, "/basket")

    assert result == expected


def test_a_card_in_the_message_is_masked(project):
    """Should mask the exception value, which is rendered on the issue page verbatim."""
    payload = event_payload(
        exception={
            "values": [{"type": "ValueError", "value": "declined 4111111111111111"}]
        }
    )

    result = deliver(project, payload).message
    expected = f"ValueError: declined {REDACTED}"

    assert result == expected


def test_a_configured_rule_reaches_the_ingest_path(project):
    """Should apply the operator's own policy, not only the built-in keywords."""
    ScrubRule.objects.create(name="email", path="user.email")

    result = deliver(project).payload["user"]["email"]
    expected = REDACTED

    assert result == expected


# the fingerprint must not move


def test_scrubbing_does_not_change_the_grouping(project):
    """Should fingerprint before scrubbing — App Center shipped the other order and split every issue in two."""
    from pandora.issues.models import Issue

    deliver(project)
    with_secret = Issue.objects.get().fingerprint_hash

    Issue.objects.all().delete()
    clean = event_payload()
    clean.pop("user")
    clean["exception"]["values"][0]["stacktrace"]["frames"][0].pop("vars")
    clean["event_id"] = "c" * 32
    deliver(project, clean)

    result = Issue.objects.get().fingerprint_hash
    expected = with_secret

    assert result == expected


# drop rules at the door


def test_a_dropped_sdk_event_never_becomes_an_envelope(project, dsn_key, client):
    """Should refuse before the durable write — the saving is disk, not just noise."""
    DropRule.objects.create(name="noisy", field="type", pattern="^ValueError$")
    body = _envelope(event_payload())

    response = client.post(
        f"/api/{project.pk}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"x-sentry-auth": f"sentry sentry_key={dsn_key.public_key}"},
    )

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (200, 0)

    assert result == expected


def test_a_dropped_sdk_event_counts_against_its_rule(project, dsn_key, client):
    """Should let an operator see whether a rule is earning its place."""
    rule = DropRule.objects.create(name="noisy", field="type", pattern="^ValueError$")
    body = _envelope(event_payload())

    client.post(
        f"/api/{project.pk}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"x-sentry-auth": f"sentry sentry_key={dsn_key.public_key}"},
    )
    rule.refresh_from_db()

    result = rule.dropped
    expected = 1

    assert result == expected


def test_an_event_no_rule_matches_is_still_stored(project, dsn_key, client):
    """Should not turn one rule into a blanket refusal."""
    DropRule.objects.create(name="noisy", field="type", pattern="^KeyError$")
    body = _envelope(event_payload())

    client.post(
        f"/api/{project.pk}/envelope/",
        data=body,
        content_type="application/x-sentry-envelope",
        headers={"x-sentry-auth": f"sentry sentry_key={dsn_key.public_key}"},
    )

    result = ingest_models.RawEnvelope.objects.count()
    expected = 1

    assert result == expected


def test_a_dropped_alert_group_never_becomes_an_envelope(project, token, client):
    """Should work on the Alertmanager door, where the noise usually comes from."""
    DropRule.objects.create(name="watchdog", field="alertname", pattern="^Watchdog$")

    response = client.post(
        "/ingest/am/",
        data=json.dumps({"groupLabels": {"alertname": "Watchdog"}, "alerts": []}),
        content_type="application/json",
        headers={"authorization": f"Bearer {token.token}"},
    )

    result = (response.status_code, ingest_models.RawEnvelope.objects.count())
    expected = (200, 0)

    assert result == expected


def test_a_dropped_alert_group_reports_the_rule(project, token, client):
    """Should tell Alertmanager the delivery was accepted while saying what happened to it."""
    DropRule.objects.create(name="watchdog", field="alertname", pattern="^Watchdog$")

    response = client.post(
        "/ingest/am/",
        data=json.dumps({"groupLabels": {"alertname": "Watchdog"}, "alerts": []}),
        content_type="application/json",
        headers={"authorization": f"Bearer {token.token}"},
    )

    result = response.json()
    expected = {"dropped": "watchdog"}

    assert result == expected


def _envelope(payload):
    header = json.dumps({"event_id": payload["event_id"]})
    item = json.dumps({"type": "event"})
    return "\n".join([header, item, json.dumps(payload)]).encode()
