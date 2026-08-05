import http
from importlib import metadata

import pytest
from django import db

from pandora.web import views

# health tests


@pytest.mark.django_db
def test_health_returns_ok(client):
    """Should answer a flat ok payload for the kubelet probes."""
    response = client.get("/health/")

    result = (response.status_code, response.json()["status"])
    expected = (http.HTTPStatus.OK, "ok")

    assert result == expected


@pytest.mark.django_db
def test_health_needs_no_authentication(client):
    """Should stay reachable without a session — probes carry no credentials."""
    response = client.get("/health/")

    assert response.status_code == http.HTTPStatus.OK


# routing tests


def test_root_redirects_to_the_admin(client):
    """Should send the bare host to the only UI pandora has."""
    response = client.get("/")

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/admin/")

    assert result == expected


@pytest.mark.django_db
def test_metrics_are_exported_for_prometheus(client):
    """Should expose the Prometheus text exposition format at /metrics."""
    response = client.get("/metrics")

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected
    assert b"python_info" in response.content


@pytest.mark.django_db
def test_metrics_include_the_ingest_gate_counters(client):
    """Should publish the pass-through gate counters from the first request."""
    response = client.get("/metrics")

    assert b"pandora_ingest_gate_checks_total" in response.content
    assert b"pandora_ingest_gate_rejections_total" in response.content


# readiness


@pytest.mark.django_db
def test_readiness_answers_ok_when_the_database_is_reachable(client):
    """Should prove the database, not just that the process is up."""
    response = client.get("/ready/")

    result = (response.status_code, response.json()["status"])
    expected = (http.HTTPStatus.OK, "ok")

    assert result == expected


@pytest.mark.django_db
def test_readiness_fails_when_the_database_does_not_answer(client, monkeypatch):
    """Should take the pod out of service rather than serve 500s."""

    def broken_cursor():
        raise db.OperationalError("no connection")

    monkeypatch.setattr(db.connection, "cursor", broken_cursor)
    response = client.get("/ready/")

    result = (response.status_code, response.json()["status"])
    expected = (http.HTTPStatus.SERVICE_UNAVAILABLE, "unavailable")

    assert result == expected


@pytest.mark.django_db
def test_health_stays_up_even_when_the_database_is_down(client, monkeypatch):
    """Should not restart a healthy process because its database blinked."""

    def broken_cursor():
        raise db.OperationalError("no connection")

    monkeypatch.setattr(db.connection, "cursor", broken_cursor)
    response = client.get("/health/")

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


@pytest.mark.django_db
def test_health_reports_the_running_version(client):
    """Should let a bug report name the build it came from."""
    response = client.get("/health/")

    result = response.json()["version"]

    assert result == views.version()
    assert result != views.UNKNOWN_VERSION


def test_an_uninstalled_build_reports_an_unknown_version(monkeypatch):
    """Should not crash the probe when the distribution metadata is missing."""

    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(views.metadata, "version", missing)

    result = views.version()
    expected = views.UNKNOWN_VERSION

    assert result == expected
