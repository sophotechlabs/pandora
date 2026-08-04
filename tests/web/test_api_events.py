import datetime
import http
import inspect

import pytest

from pandora.events import store as event_store
from pandora.events import types as event_types
from pandora.issues import models as issue_models
from pandora.web import api
from tests.web import fakes

METHODS = ("insert", "fetch", "search", "prune", "ensure_partitions")


def build_event(index, project_id, issue_id, episode_id=None):
    return event_types.Event(
        id=f"01J{index:023d}",
        project_id=project_id,
        timestamp=datetime.datetime(2026, 8, 4, 12, index, tzinfo=datetime.UTC),
        level="error",
        message=f"occurrence {index}",
        issue_id=issue_id,
        episode_id=episode_id,
        fingerprint=["alertname:TargetDown"],
        tags={"namespace": "monitoring"},
        extra={"generatorURL": "https://example.test/graph"},
        source="am",
        environment="p-mk1",
    )


def signature_shape(method):
    parameters = inspect.signature(method).parameters
    return [
        (name, parameter.kind.name, parameter.default)
        for name, parameter in parameters.items()
    ]


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
def stored_events(store, project, issue):
    events = [build_event(index, project.pk, issue.pk) for index in range(1, 6)]
    store.insert(events)
    return events


def events_url(issue_id):
    return f"/api/v1/issues/{issue_id}/events"


# double contract


@pytest.mark.parametrize("method", METHODS)
def test_the_double_matches_the_frozen_store_signature(method):
    """Should keep the test double callable exactly like a real EventStore."""
    result = signature_shape(getattr(fakes.FakeEventStore, method))
    expected = signature_shape(getattr(event_store.EventStore, method))

    assert result == expected


@pytest.mark.parametrize("method", METHODS)
def test_the_unbuilt_double_matches_the_frozen_store_signature(method):
    """Should keep the not-yet-implemented double callable like a real store."""
    result = signature_shape(getattr(fakes.UnbuiltEventStore, method))
    expected = signature_shape(getattr(event_store.EventStore, method))

    assert result == expected


# store call construction


def test_the_view_asks_the_store_for_the_issue_of_the_token_project(
    client, auth, issue, store
):
    """Should scope the store query by project and issue, never by issue alone."""
    client.get(events_url(issue.pk), headers=auth)

    result = store.calls
    expected = [
        {
            "project_id": issue.project_id,
            "issue_id": issue.pk,
            "episode_id": None,
            "before": None,
            "limit": api.DEFAULT_LIMIT + 1,
        }
    ]

    assert result == expected


def test_the_view_asks_for_one_row_more_than_the_page(client, auth, issue, store):
    """Should over-fetch by one so a full page can be told from the last page."""
    client.get(events_url(issue.pk), {"limit": "10"}, headers=auth)

    result = store.calls[0]["limit"]
    expected = 11

    assert result == expected


def test_the_episode_filter_reaches_the_store(client, auth, issue, store):
    """Should pass an episode key through to the store unchanged."""
    client.get(events_url(issue.pk), {"episode": "42"}, headers=auth)

    result = store.calls[0]["episode_id"]
    expected = "42"

    assert result == expected


def test_the_cursor_reaches_the_store_as_its_before_bound(
    client, auth, issue, store, stored_events
):
    """Should hand the store the event id a consumer echoed back."""
    client.get(
        events_url(issue.pk),
        {"cursor": stored_events[2].id},
        headers=auth,
    )

    result = store.calls[0]["before"]
    expected = stored_events[2].id

    assert result == expected


# response shape


def test_an_event_serialises_to_the_documented_shape(
    client, auth, issue, stored_events
):
    """Should render every field the frozen Event carries, timestamp in UTC."""
    response = client.get(events_url(issue.pk), {"limit": "1"}, headers=auth)

    result = response.json()["results"][0]
    expected = {
        "id": stored_events[4].id,
        "project_id": issue.project_id,
        "timestamp": "2026-08-04T12:05:00Z",
        "level": "error",
        "message": "occurrence 5",
        "issue_id": issue.pk,
        "episode_id": None,
        "fingerprint": ["alertname:TargetDown"],
        "tags": {"namespace": "monitoring"},
        "extra": {"generatorURL": "https://example.test/graph"},
        "source": "am",
        "environment": "p-mk1",
    }

    assert result == expected


def test_an_issue_without_events_returns_an_empty_page(client, auth, issue, store):
    """Should answer the documented envelope even when nothing is stored."""
    response = client.get(events_url(issue.pk), headers=auth)

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.OK, {"results": [], "next_cursor": None})
    assert result == expected


# pagination


def test_a_full_event_page_hands_back_the_last_event_id(
    client, auth, issue, stored_events
):
    """Should hand back the oldest id on the page as the next cursor."""
    response = client.get(events_url(issue.pk), {"limit": "2"}, headers=auth)

    payload = response.json()
    result = ([row["id"] for row in payload["results"]], payload["next_cursor"])
    expected = (
        [stored_events[4].id, stored_events[3].id],
        stored_events[3].id,
    )

    assert result == expected


def test_the_last_event_page_hands_back_no_cursor(client, auth, issue, stored_events):
    """Should end the walk with a null cursor once the store is exhausted."""
    response = client.get(events_url(issue.pk), {"limit": "5"}, headers=auth)

    result = response.json()["next_cursor"]

    assert result is None


def test_walking_the_event_cursor_covers_every_event_once(
    client, auth, issue, stored_events
):
    """Should page through the stored events with no event repeated or skipped."""
    seen = []
    cursor = None
    for _ in range(3):
        query = {"limit": "2"}
        if cursor is not None:
            query["cursor"] = cursor
        payload = client.get(events_url(issue.pk), query, headers=auth).json()
        seen.extend(row["id"] for row in payload["results"])
        cursor = payload["next_cursor"]

    result = (seen, cursor)
    expected = ([event.id for event in reversed(stored_events)], None)

    assert result == expected


def test_events_of_another_issue_stay_out_of_the_page(
    client, auth, issue, store, stored_events
):
    """Should filter by issue so a neighbouring issue's events never leak in."""
    other = issue_models.Issue.objects.create(
        project=issue.project,
        fingerprint_hash="f" * 64,
        title="a neighbouring issue",
    )
    store.insert([build_event(9, issue.project_id, other.pk)])

    response = client.get(events_url(issue.pk), headers=auth)

    result = [row["issue_id"] for row in response.json()["results"]]
    expected = [issue.pk] * 5

    assert result == expected


# failure paths


def test_events_of_an_unknown_issue_are_not_found(client, auth, store):
    """Should answer 404 without asking the store for anything."""
    response = client.get(events_url(999999), headers=auth)

    result = (response.status_code, response.json(), store.calls)
    expected = (http.HTTPStatus.NOT_FOUND, {"detail": "issue not found"}, [])
    assert result == expected


def test_events_of_another_project_are_not_found(client, auth, other_project, store):
    """Should refuse to read another project's events through its issue id."""
    hidden = issue_models.Issue.objects.create(
        project=other_project,
        fingerprint_hash="1" * 64,
        title="an issue of another project",
    )

    response = client.get(events_url(hidden.pk), headers=auth)

    result = (response.status_code, store.calls)
    expected = (http.HTTPStatus.NOT_FOUND, [])
    assert result == expected


def test_events_need_a_token(client, issue, store, read_token):
    """Should refuse an anonymous event read."""
    response = client.get(events_url(issue.pk))

    result = (response.status_code, store.calls)
    expected = (http.HTTPStatus.UNAUTHORIZED, [])
    assert result == expected


def test_a_bad_limit_never_reaches_the_store(client, auth, issue, store):
    """Should validate the page size before spending a store query."""
    response = client.get(events_url(issue.pk), {"limit": "many"}, headers=auth)

    result = (response.status_code, response.json(), store.calls)
    expected = (
        http.HTTPStatus.BAD_REQUEST,
        {"detail": "limit must be a positive integer"},
        [],
    )
    assert result == expected


def test_a_store_without_a_body_answers_501(client, auth, issue, unbuilt_store):
    """Should answer 501, not 500, while a backend's store is unimplemented."""
    response = client.get(events_url(issue.pk), headers=auth)

    result = (response.status_code, response.json())
    expected = (
        http.HTTPStatus.NOT_IMPLEMENTED,
        {"detail": "event store is not implemented for this database yet"},
    )
    assert result == expected
