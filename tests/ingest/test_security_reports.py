import http
import json

import pytest

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest.translators import security

pytestmark = pytest.mark.django_db


@pytest.fixture
def dsn_key(project):
    return core_models.DsnKey.objects.create(project=project, public_key="k" * 32)


def csp_report(**overrides):
    report = {
        "document-uri": "https://app.test/checkout",
        "referrer": "https://app.test/",
        "violated-directive": "script-src 'self'",
        "effective-directive": "script-src",
        "original-policy": "script-src 'self'",
        "disposition": "enforce",
        "blocked-uri": "https://evil.test/tracker.js",
        "status-code": 200,
    }
    report.update(overrides)
    return {"csp-report": report}


def post(client, key, url, body):
    return client.post(
        f"/api/{key.project_id}/{url}/?sentry_key={key.public_key}",
        data=json.dumps(body),
        content_type="application/json",
    )


# translating


def test_a_csp_report_names_what_was_blocked():
    """Should read like the browser console line a developer already saw."""
    payload = security.translate(csp_report())

    assert payload["message"] == "Blocked 'script-src' from 'https://evil.test'"


def test_a_csp_report_blocked_on_self_says_so():
    """Should not print an empty origin for an inline violation."""
    payload = security.translate(csp_report(**{"blocked-uri": "self"}))

    assert payload["message"] == "Blocked 'script-src' from 'self'"


def test_a_csp_report_is_grouped_by_directive_and_origin():
    """Should fold every hit of one blocked script into one issue."""
    first = security.translate(csp_report())
    second = security.translate(
        csp_report(**{"blocked-uri": "https://evil.test/other.js"})
    )

    assert first["fingerprint"] == second["fingerprint"]


def test_a_csp_report_keeps_the_policy_for_the_reader():
    """Should carry what a person needs to widen the policy correctly."""
    payload = security.translate(csp_report())

    assert payload["extra"]["original-policy"] == "script-src 'self'"


def test_a_csp_report_is_tagged_by_directive():
    """Should let one directive be filtered out of the stream."""
    payload = security.translate(csp_report())

    assert payload["tags"]["effective-directive"] == "script-src"


def test_a_csp_report_falls_back_to_the_violated_directive():
    """Should still name the rule when the browser sent only the older field."""
    report = csp_report()
    del report["csp-report"]["effective-directive"]

    payload = security.translate(report)

    assert "script-src 'self'" in payload["message"]


def test_an_expect_ct_report_is_translated():
    """Should accept the other report type the same endpoint receives."""
    payload = security.translate(
        {"expect-ct-report": {"hostname": "app.test", "port": 443}}
    )

    assert payload["message"] == "Expect-CT failed for 'app.test'"


def test_an_hpkp_report_is_translated():
    """Should accept a pin failure, which has no wrapper key of its own."""
    payload = security.translate(
        {"hostname": "app.test", "known-pins": ['pin-sha256="abc"']}
    )

    assert payload["message"].startswith("Public key pinning failed")


def test_an_unknown_report_is_refused():
    """Should not store a body nothing in the spec describes."""
    with pytest.raises(security.SecurityReportError):
        security.translate({"something": "else"})


def test_a_report_that_is_not_an_object_is_refused():
    """Should refuse a list where an object belongs."""
    with pytest.raises(security.SecurityReportError):
        security.translate([1, 2, 3])


def test_a_nel_report_names_the_failure_and_the_phase():
    """Should say what failed and how far the request got."""
    payload = security.translate_nel(
        {
            "url": "https://app.test/checkout",
            "user_agent": "Mozilla/5.0",
            "body": {"type": "tcp.refused", "phase": "connection"},
        }
    )

    assert payload["message"] == "NEL: tcp.refused during connection"


def test_a_nel_report_without_a_body_is_refused():
    """Should refuse a report with nothing to read."""
    with pytest.raises(security.SecurityReportError):
        security.translate_nel({"url": "https://app.test/"})


# routes


def test_the_security_route_matches_the_sentry_scheme():
    """Should sit where a browser's report-uri header would be pointed."""
    from django import urls

    result = urls.reverse("ingest-security", args=[7])
    expected = "/api/7/security/"

    assert result == expected


def test_the_nel_route_matches_the_sentry_scheme():
    """Should sit where a Report-To endpoint would be pointed."""
    from django import urls

    result = urls.reverse("ingest-nel", args=[7])
    expected = "/api/7/nel/"

    assert result == expected


def test_the_report_routes_constrain_the_project_to_an_integer():
    """Should hand the views an int project id, never a string."""
    from django import urls

    result = (
        urls.resolve("/api/7/security/").kwargs,
        urls.resolve("/api/7/nel/").kwargs,
    )
    expected = ({"project_id": 7}, {"project_id": 7})

    assert result == expected


# the endpoints


def test_a_csp_report_becomes_an_envelope(client, dsn_key):
    """Should reach the same durable inbox every other door writes to."""
    response = post(client, dsn_key, "security", csp_report())

    assert response.status_code == http.HTTPStatus.CREATED
    assert ingest_models.RawEnvelope.objects.count() == 1


def test_a_csp_report_needs_a_dsn_key(client, project):
    """Should not accept a report from a caller that named no project key."""
    response = client.post(
        f"/api/{project.pk}/security/",
        data=json.dumps(csp_report()),
        content_type="application/json",
    )

    assert response.status_code == http.HTTPStatus.UNAUTHORIZED


def test_a_malformed_security_report_is_refused(client, dsn_key):
    """Should tell the browser the body was wrong rather than store nothing."""
    response = post(client, dsn_key, "security", {"nope": 1})

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_a_get_to_the_security_endpoint_is_refused(client, dsn_key):
    """Should be POST only, the way the browser sends it."""
    response = client.get(
        f"/api/{dsn_key.project_id}/security/?sentry_key={dsn_key.public_key}"
    )

    assert response.status_code == http.HTTPStatus.METHOD_NOT_ALLOWED


def test_a_nel_array_stores_one_envelope_per_report(client, dsn_key):
    """Should accept the batch shape the browser actually posts."""
    body = [
        {"url": "https://app.test/a", "body": {"type": "tcp.refused"}},
        {"url": "https://app.test/b", "body": {"type": "dns.name_not_resolved"}},
    ]

    response = post(client, dsn_key, "nel", body)

    assert response.json()["accepted"] == 2
    assert ingest_models.RawEnvelope.objects.count() == 2


def test_a_single_nel_object_is_accepted(client, dsn_key):
    """Should not require an array for a single report."""
    response = post(
        client,
        dsn_key,
        "nel",
        {"url": "https://app.test/a", "body": {"type": "tcp.refused"}},
    )

    assert response.json()["accepted"] == 1


def test_an_unreadable_nel_report_is_skipped_not_fatal(client, dsn_key):
    """Should keep the good reports in a batch that also carried a bad one."""
    body = [
        {"url": "https://app.test/a", "body": {"type": "tcp.refused"}},
        {"url": "https://app.test/b"},
    ]

    response = post(client, dsn_key, "nel", body)

    assert response.json()["accepted"] == 1


def test_a_huge_nel_batch_is_refused(client, dsn_key):
    """Should bound one request rather than let a browser flood the inbox."""
    body = [{"url": "https://app.test/a", "body": {"type": "tcp.refused"}}] * 101

    response = post(client, dsn_key, "nel", body)

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_a_nel_body_that_is_not_a_list_is_refused(client, dsn_key):
    """Should name the mistake rather than store a string."""
    response = post(client, dsn_key, "nel", "boom")

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_a_security_report_becomes_an_issue(client, dsn_key, project):
    """Should end in the stream, which is the only reason to accept it."""
    from pandora.issues import models as issue_models

    post(client, dsn_key, "security", csp_report())

    issue = issue_models.Issue.objects.get()
    assert issue.title == "Blocked 'script-src' from 'https://evil.test'"


def test_a_blocked_inline_source_is_named_as_itself():
    """Should print the directive value rather than an empty origin."""
    payload = security.translate(csp_report(**{"blocked-uri": "inline"}))

    assert "'inline'" in payload["message"]


def test_a_blocked_uri_with_no_host_is_printed_whole():
    """Should not lose the only thing the report said was blocked."""
    payload = security.translate(csp_report(**{"blocked-uri": "data"}))

    assert "'data'" in payload["message"]


def test_a_report_with_no_blocked_uri_reads_as_self():
    """Should say what a browser means by an empty blocked-uri."""
    payload = security.translate(csp_report(**{"blocked-uri": ""}))

    assert "'self'" in payload["message"]


def test_a_document_uri_with_no_scheme_still_gives_a_culprit():
    """Should handle a bare host the way the URL parser hands it over."""
    payload = security.translate(csp_report(**{"document-uri": "//app.test/checkout"}))

    assert payload["culprit"] == "app.test"
