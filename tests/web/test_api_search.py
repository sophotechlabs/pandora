import datetime
import http

import pytest
from django.utils import timezone

from pandora.events import types as event_types
from tests.web import fakes

pytestmark = pytest.mark.django_db

SEARCH_URL = "/api/v1/events"


@pytest.fixture
def store(mocker):
    built = fakes.FakeEventStore()
    mocker.patch("pandora.web.api.get_store", return_value=built)
    return built


@pytest.fixture
def unbuilt_store(mocker):
    built = fakes.UnbuiltEventStore()
    mocker.patch("pandora.web.api.get_store", return_value=built)
    return built


@pytest.fixture
def tagged(store, project, issue):
    now = timezone.now()
    store.insert(
        [
            event_types.Event(
                id=f"01J000000000000000000000{index:02d}",
                project_id=project.pk,
                issue_id=issue.pk,
                timestamp=now - datetime.timedelta(minutes=index),
                level="error",
                message=f"event {index}",
                tags={"namespace": namespace, "severity": "critical"},
            )
            for index, namespace in enumerate(["payments", "payments", "billing"], 1)
        ]
    )
    return store


def test_search_needs_a_read_token(client):
    """Should refuse an unauthenticated search like every other read route."""
    response = client.get(SEARCH_URL)

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


def test_search_returns_events_matching_a_tag(client, auth, tagged):
    """Should reach the highest-cardinality dimension, which had no reader at all."""
    response = client.get(SEARCH_URL, {"tag": "namespace:payments"}, headers=auth)

    result = (response.status_code, len(response.json()["results"]))
    expected = (http.HTTPStatus.OK, 2)
    assert result == expected


def test_search_accepts_several_tags(client, auth, tagged):
    """Should narrow on every tag given, not just the first."""
    response = client.get(
        SEARCH_URL,
        {"tag": ["namespace:payments", "severity:critical"]},
        headers=auth,
    )

    result = len(response.json()["results"])
    expected = 2
    assert result == expected


def test_a_malformed_tag_is_refused(client, auth, tagged):
    """Should say what is wrong rather than silently ignoring the filter."""
    response = client.get(SEARCH_URL, {"tag": "namespace"}, headers=auth)

    result = (response.status_code, response.json()["detail"])
    expected = (http.HTTPStatus.BAD_REQUEST, "tag 'namespace' is not in key:value form")
    assert result == expected


def test_search_without_tags_returns_the_window(client, auth, tagged):
    """Should let an operator page the recent record with no filter at all."""
    response = client.get(SEARCH_URL, headers=auth)

    result = len(response.json()["results"])
    expected = 3
    assert result == expected


def test_an_inverted_window_is_refused(client, auth, tagged):
    """Should refuse a window that ends before it starts."""
    response = client.get(
        SEARCH_URL,
        {"since": "2026-08-06T10:00:00Z", "until": "2026-08-06T09:00:00Z"},
        headers=auth,
    )

    result = (response.status_code, response.json()["detail"])
    expected = (http.HTTPStatus.BAD_REQUEST, "since is after until")
    assert result == expected


def test_a_malformed_since_is_refused(client, auth, tagged):
    """Should reject a timestamp that is not ISO 8601."""
    response = client.get(SEARCH_URL, {"since": "yesterday"}, headers=auth)

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST
    assert result == expected


def test_a_malformed_until_is_refused(client, auth, tagged):
    """Should reject a bad upper bound as loudly as a bad lower one."""
    response = client.get(SEARCH_URL, {"until": "tomorrow"}, headers=auth)

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST
    assert result == expected


def test_an_explicit_window_is_honoured(client, auth, tagged):
    """Should search the window asked for, not always the default seven days."""
    long_ago = (timezone.now() - datetime.timedelta(days=400)).isoformat()
    response = client.get(
        SEARCH_URL,
        {
            "since": long_ago,
            "until": (timezone.now() - datetime.timedelta(days=399)).isoformat(),
        },
        headers=auth,
    )

    result = (response.status_code, len(response.json()["results"]))
    expected = (http.HTTPStatus.OK, 0)
    assert result == expected


def test_a_store_without_search_answers_501(client, auth, unbuilt_store):
    """Should answer 501, not 500, while a backend's store is unimplemented."""
    response = client.get(SEARCH_URL, headers=auth)

    result = response.status_code
    expected = http.HTTPStatus.NOT_IMPLEMENTED
    assert result == expected
