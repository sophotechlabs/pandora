import http

import pytest

# health tests


@pytest.mark.django_db
def test_health_returns_ok(client):
    """Should answer a flat ok payload for the kubelet probes."""
    response = client.get("/health/")

    result = {"status_code": response.status_code, "body": response.json()}
    expected = {"status_code": http.HTTPStatus.OK, "body": {"status": "ok"}}

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
