import datetime

import pytest
from django import test
from django.utils import timezone

from pandora.am import client as am_client
from tests.am import fake_am

FIRING = fake_am.alert("3c1f6a2b9d4e5087", {"alertname": "TargetDown"})
SUPPRESSED = fake_am.alert(
    "9a8b7c6d5e4f3021",
    {"alertname": "KubeCPUOvercommit"},
    state="suppressed",
)


@pytest.fixture
def moment():
    return timezone.now().replace(microsecond=0)


def silence_kwargs(moment, **overrides):
    kwargs = {
        "matchers": [
            {
                "name": "alertname",
                "value": "TargetDown",
                "isRegex": False,
                "isEqual": True,
            }
        ],
        "starts_at": moment,
        "ends_at": moment + datetime.timedelta(hours=1),
        "comment": "pandora issue #1",
        "created_by": "admin",
    }
    kwargs.update(overrides)
    return kwargs


# session configuration


def test_the_retry_policy_covers_three_attempts_past_the_first(alertmanager_client):
    """Should retry an Alertmanager blip three times, as the phase specifies."""
    adapter = alertmanager_client._session.get_adapter("http://alertmanager.test")

    result = adapter.max_retries.total
    expected = 3

    assert result == expected


def test_the_retry_policy_covers_the_statuses_a_loaded_alertmanager_returns(
    alertmanager_client,
):
    """Should treat 429 and the 5xx family as worth another attempt."""
    adapter = alertmanager_client._session.get_adapter("http://alertmanager.test")

    result = tuple(sorted(adapter.max_retries.status_forcelist))
    expected = (429, 500, 502, 503, 504)

    assert result == expected


def test_a_post_is_never_replayed_by_the_retry_policy(alertmanager_client):
    """Should keep a retried silence from becoming two silences in Alertmanager."""
    adapter = alertmanager_client._session.get_adapter("http://alertmanager.test")

    result = "POST" in adapter.max_retries.allowed_methods

    assert result is False


def test_both_timeouts_are_set(alertmanager_client):
    """Should bound connect and read separately so a hung read cannot wedge the loop."""
    result = alertmanager_client.timeout
    expected = (5.0, 30.0)

    assert result == expected


def test_a_ca_bundle_becomes_the_verification_source(client_factory):
    """Should verify Alertmanager's TLS against PANDORA_AM_CA_BUNDLE when set."""
    client = client_factory(ca_bundle="/etc/pandora/ca/platform-ca.crt")

    result = client._session.verify
    expected = "/etc/pandora/ca/platform-ca.crt"

    assert result == expected


def test_without_a_ca_bundle_the_default_trust_store_stands(alertmanager_client):
    """Should leave requests' own verification alone rather than disable it."""
    result = alertmanager_client._session.verify

    assert result is True


def test_a_trailing_slash_on_the_url_is_dropped(alertmanager):
    """Should build one slash into every path, whatever PANDORA_AM_URL carries."""
    client = am_client.AlertmanagerClient(f"{alertmanager.url}/")

    result = client.base_url
    expected = alertmanager.url

    assert result == expected


def test_an_empty_url_is_refused_at_construction(alertmanager):
    """Should fail where it can be reported, not on the first poll."""
    with pytest.raises(am_client.AlertmanagerError) as error:
        am_client.AlertmanagerClient("   ")

    assert "PANDORA_AM_URL" in str(error.value)


@test.override_settings(
    PANDORA_AM_URL="https://alertmanager.test/",
    PANDORA_AM_CA_BUNDLE="/etc/pandora/ca/platform-ca.crt",
)
def test_the_settings_factory_carries_url_and_bundle():
    """Should be the one place deployment config turns into a client."""
    client = am_client.from_settings()

    result = (client.base_url, client._session.verify)
    expected = ("https://alertmanager.test", "/etc/pandora/ca/platform-ca.crt")

    assert result == expected


# request construction


def test_the_alert_query_asks_for_suppressed_alerts_too(
    alertmanager, alertmanager_client
):
    """Should count silenced and inhibited alerts as still firing."""
    alertmanager_client.alerts()

    result = sorted(alertmanager.calls[0].query.split("&"))
    expected = ["active=true", "inhibited=true", "silenced=true", "unprocessed=true"]

    assert result == expected


def test_a_silence_is_posted_as_alertmanager_v2_expects(
    alertmanager, alertmanager_client, moment
):
    """Should send structured matchers and RFC 3339 bounds, not a free-text matcher."""
    alertmanager_client.create_silence(**silence_kwargs(moment))

    result = alertmanager.silence_bodies()[0]
    expected = {
        "matchers": [
            {
                "name": "alertname",
                "value": "TargetDown",
                "isRegex": False,
                "isEqual": True,
            }
        ],
        "startsAt": moment.isoformat(),
        "endsAt": (moment + datetime.timedelta(hours=1)).isoformat(),
        "createdBy": "admin",
        "comment": "pandora issue #1",
    }

    assert result == expected


def test_a_naive_timestamp_is_sent_as_utc(alertmanager, alertmanager_client):
    """Should never hand Alertmanager a timestamp without a zone."""
    naive = datetime.datetime(2026, 8, 4, 12, 0, 0)

    alertmanager_client.create_silence(
        **silence_kwargs(naive, ends_at=naive + datetime.timedelta(hours=1))
    )

    result = alertmanager.silence_bodies()[0]["startsAt"]
    expected = "2026-08-04T12:00:00+00:00"

    assert result == expected


def test_a_silence_id_is_escaped_into_the_delete_path(
    alertmanager, alertmanager_client
):
    """Should never let an id from the database open a path of its own."""
    alertmanager_client.delete_silence("../alerts")

    result = alertmanager.calls[0].path
    expected = "/api/v2/silence/..%2Falerts"

    assert result == expected


# response parsing


def test_the_alert_list_comes_back_as_dicts(alertmanager, alertmanager_client):
    """Should hand the caller Alertmanager's alerts, not the response object."""
    alertmanager.alerts = [FIRING, SUPPRESSED]

    result = [entry["fingerprint"] for entry in alertmanager_client.alerts()]
    expected = ["3c1f6a2b9d4e5087", "9a8b7c6d5e4f3021"]

    assert result == expected


def test_an_empty_alert_list_is_a_valid_answer(alertmanager_client):
    """Should treat a quiet Alertmanager as data, not as a failure."""
    result = alertmanager_client.alerts()
    expected = []

    assert result == expected


def test_a_non_list_alerts_body_is_refused(alertmanager, alertmanager_client):
    """Should not let a proxy's error page pass as an empty alert set."""
    alertmanager.body_override = b'{"detail": "nope"}'

    with pytest.raises(am_client.AlertmanagerError) as error:
        alertmanager_client.alerts()

    assert "non-list" in str(error.value)


def test_a_non_object_alert_is_refused(alertmanager, alertmanager_client):
    """Should fail loudly rather than reconcile against garbage."""
    alertmanager.body_override = b"[1, 2]"

    with pytest.raises(am_client.AlertmanagerError) as error:
        alertmanager_client.alerts()

    assert "non-object" in str(error.value)


def test_a_body_that_is_not_json_is_refused(alertmanager, alertmanager_client):
    """Should name the parse failure rather than raise ValueError from requests."""
    alertmanager.body_override = b"<html>bad gateway</html>"

    with pytest.raises(am_client.AlertmanagerError) as error:
        alertmanager_client.alerts()

    assert "not JSON" in str(error.value)


def test_a_created_silence_returns_its_id(alertmanager, alertmanager_client, moment):
    """Should give the caller the id SilenceLink has to store."""
    result = alertmanager_client.create_silence(**silence_kwargs(moment))

    assert result in alertmanager.silences


def test_a_silence_response_without_an_id_is_refused(
    alertmanager, alertmanager_client, moment
):
    """Should not write a SilenceLink that cannot be lifted later."""
    alertmanager.body_override = b"{}"

    with pytest.raises(am_client.AlertmanagerError) as error:
        alertmanager_client.create_silence(**silence_kwargs(moment))

    assert "without returning an id" in str(error.value)


def test_a_non_object_silence_response_is_refused(
    alertmanager, alertmanager_client, moment
):
    """Should refuse a body it cannot read an id out of."""
    alertmanager.body_override = b"[]"

    with pytest.raises(am_client.AlertmanagerError) as error:
        alertmanager_client.create_silence(**silence_kwargs(moment))

    assert "non-object" in str(error.value)


def test_deleting_a_silence_removes_it_from_alertmanager(
    alertmanager, alertmanager_client, moment
):
    """Should expire the silence the link points at."""
    silence_id = alertmanager_client.create_silence(**silence_kwargs(moment))

    alertmanager_client.delete_silence(silence_id)

    result = alertmanager.silences
    expected = {}

    assert result == expected


# failure handling


def test_a_blip_is_retried_and_then_succeeds(alertmanager, alertmanager_client):
    """Should ride out the 503 an Alertmanager restart answers with."""
    alertmanager.alerts = [FIRING]
    alertmanager.fail_next(503, times=2)

    result = len(alertmanager_client.alerts())
    expected = 1

    assert result == expected
    assert len(alertmanager.calls_to("GET", "/api/v2/alerts")) == 3


def test_a_persistent_failure_gives_up_after_the_retries(
    alertmanager, alertmanager_client
):
    """Should stop at four attempts rather than hammer a broken Alertmanager."""
    alertmanager.fail_next(503, times=10)

    with pytest.raises(am_client.AlertmanagerError):
        alertmanager_client.alerts()

    result = len(alertmanager.calls_to("GET", "/api/v2/alerts"))
    expected = 4

    assert result == expected


def test_a_failed_silence_post_is_not_replayed(
    alertmanager, alertmanager_client, moment
):
    """Should leave one attempt behind, so no duplicate silence can appear."""
    alertmanager.fail_next(500, times=1)

    with pytest.raises(am_client.AlertmanagerError):
        alertmanager_client.create_silence(**silence_kwargs(moment))

    result = len(alertmanager.calls_to("POST", "/api/v2/silences"))
    expected = 1

    assert result == expected


def test_an_http_error_names_the_call(alertmanager, alertmanager_client):
    """Should say which request failed, not just that something did."""
    alertmanager.fail_next(500, times=4)

    with pytest.raises(am_client.AlertmanagerError) as error:
        alertmanager_client.alerts()

    assert "GET" in str(error.value)
    assert "/api/v2/alerts" in str(error.value)


def test_an_unreachable_alertmanager_becomes_one_error_type(alertmanager):
    """Should give the reconcile loop a single exception to catch."""
    unreachable = am_client.AlertmanagerClient(
        "http://127.0.0.1:1",
        backoff_factor=0.0,
        timeout=(0.2, 0.2),
    )

    with pytest.raises(am_client.AlertmanagerError):
        unreachable.alerts()


def test_a_read_that_never_finishes_becomes_one_error_type(
    alertmanager, client_factory
):
    """Should bound a hung Alertmanager rather than block the loop forever."""
    alertmanager.delay = 1.0
    client = client_factory(timeout=(0.5, 0.05), retries=0)

    with pytest.raises(am_client.AlertmanagerError):
        client.alerts()
