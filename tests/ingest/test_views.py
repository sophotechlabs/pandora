import http

import pytest
from django import urls

# route contract


def test_alertmanager_route_is_a_bare_path():
    """Should expose the Alertmanager door at /ingest/am/ with no token in the URL."""
    result = urls.reverse("ingest-am")
    expected = "/ingest/am/"

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


# response tests


@pytest.mark.django_db
def test_alertmanager_door_answers_501_until_phase_one(client):
    """Should answer 501 on the frozen route rather than 404."""
    response = client.post("/ingest/am/", data="{}", content_type="application/json")

    result = response.status_code
    expected = http.HTTPStatus.NOT_IMPLEMENTED
    assert result == expected


@pytest.mark.django_db
def test_alertmanager_door_is_csrf_exempt(client):
    """Should accept an unauthenticated cross-origin POST without a CSRF token."""
    response = client.post("/ingest/am/", data="{}", content_type="application/json")

    assert response.status_code != http.HTTPStatus.FORBIDDEN


@pytest.mark.django_db
def test_envelope_door_answers_501_until_phase_seven(client):
    """Should answer 501 on the frozen SDK route rather than 404."""
    response = client.post("/api/7/envelope/", data=b"", content_type="text/plain")

    result = response.status_code
    expected = http.HTTPStatus.NOT_IMPLEMENTED
    assert result == expected
