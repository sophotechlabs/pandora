import http
import json

import pytest

from pandora.core import models as core_models
from pandora.issues import models as issue_models
from pandora.releases import models

pytestmark = pytest.mark.django_db

CREATE_URL = "/api/0/organizations/pandora/releases/"
DETAIL_URL = "/api/0/organizations/pandora/releases/1.4.0/"


@pytest.fixture
def deploy_token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="ci",
        token="ci-token",
        source=core_models.TokenSource.CI,
        scopes=(core_models.TokenScope.ARTIFACTS, core_models.TokenScope.DEPLOY),
    )


@pytest.fixture
def read_token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="reader",
        token="read-token",
        scopes=(core_models.TokenScope.READ,),
    )


def commit(**overrides):
    entry = {
        "id": "a" * 40,
        "repository": "sophotechlabs/pandora",
        "message": "fix the checkout total",
        "author_name": "Someone",
        "author_email": "someone@example.test",
        "timestamp": "2026-09-08T09:00:00Z",
        "patch_set": [{"path": "src/pandora/checkout.py", "type": "M"}],
    }
    entry.update(overrides)
    return entry


def send(client, token, url, body, method="post"):
    return getattr(client, method)(
        url,
        data=json.dumps(body),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token.token}"},
    )


def test_a_release_is_created_the_way_sentry_cli_does(client, deploy_token):
    """Should accept `sentry-cli releases new` without a change to the pipeline."""
    response = send(client, deploy_token, CREATE_URL, {"version": "1.4.0"})

    assert response.status_code == http.HTTPStatus.CREATED
    assert models.Release.objects.get().version == "1.4.0"


def test_creating_a_release_twice_is_idempotent(client, deploy_token):
    """Should let CI call it on every build without minting duplicates."""
    send(client, deploy_token, CREATE_URL, {"version": "1.4.0"})
    send(client, deploy_token, CREATE_URL, {"version": "1.4.0"})

    assert models.Release.objects.count() == 1


def test_a_release_needs_a_version(client, deploy_token):
    """Should refuse a release nothing can be named by."""
    response = send(client, deploy_token, CREATE_URL, {})

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_a_release_for_another_project_is_refused(client, deploy_token):
    """Should keep a token inside the project it was issued for."""
    response = send(
        client,
        deploy_token,
        CREATE_URL,
        {"version": "1.4.0", "projects": ["something-else"]},
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_commits_are_attached_by_the_detail_endpoint(client, deploy_token):
    """Should accept `sentry-cli releases set-commits --local`."""
    response = send(
        client,
        deploy_token,
        DETAIL_URL,
        {"commits": [commit()]},
        method="put",
    )

    assert response.status_code == http.HTTPStatus.OK
    assert response.json()["commitCount"] == 1
    assert models.Commit.objects.count() == 1


def test_the_response_names_the_repositories_it_saw(client, deploy_token):
    """Should let CI verify the repository name it sent was understood."""
    response = send(
        client, deploy_token, DETAIL_URL, {"commits": [commit()]}, method="put"
    )

    assert response.json()["repositories"] == ["sophotechlabs/pandora"]


def test_commits_can_ride_along_with_the_release_creation(client, deploy_token):
    """Should support the one-call form as well as the two-call one."""
    response = send(
        client,
        deploy_token,
        CREATE_URL,
        {"version": "1.4.0", "commits": [commit()]},
    )

    assert response.json()["commitCount"] == 1


def test_a_commit_message_resolves_the_issue_it_names(client, deploy_token, project):
    """Should close the issue from the commit, the way Sentry does."""
    issue = issue_models.Issue.objects.create(
        project=project,
        fingerprint_hash="a" * 64,
        title="ValueError",
        triage_state=issue_models.TriageState.NEW,
    )

    response = send(
        client,
        deploy_token,
        DETAIL_URL,
        {"commits": [commit(message=f"fixes #{issue.pk}")]},
        method="put",
    )

    issue.refresh_from_db()
    result = (issue.triage_state, response.json()["resolvedIssues"])
    expected = (issue_models.TriageState.RESOLVED, [issue.pk])

    assert result == expected


def test_a_resolved_issue_records_the_release_it_was_fixed_in(
    client, deploy_token, project
):
    """Should let a later event on an older release not reopen it."""
    issue = issue_models.Issue.objects.create(
        project=project,
        fingerprint_hash="a" * 64,
        title="ValueError",
    )

    send(
        client,
        deploy_token,
        DETAIL_URL,
        {"commits": [commit(message=f"fixes #{issue.pk}")]},
        method="put",
    )

    resolution = models.Resolution.objects.get(issue=issue)
    assert resolution.release.version == "1.4.0"


def test_an_issue_in_another_project_is_not_resolved(client, deploy_token):
    """Should not let one project's commit close another project's issue."""
    from pandora.core import models as core

    other = core.Project.objects.create(slug="other", name="Other")
    issue = issue_models.Issue.objects.create(
        project=other,
        fingerprint_hash="b" * 64,
        title="ValueError",
    )

    send(
        client,
        deploy_token,
        DETAIL_URL,
        {"commits": [commit(message=f"fixes #{issue.pk}")]},
        method="put",
    )

    issue.refresh_from_db()
    assert issue.triage_state == issue_models.TriageState.NEW


def test_commits_must_be_a_list(client, deploy_token):
    """Should name the mistake rather than store nothing quietly."""
    response = send(client, deploy_token, DETAIL_URL, {"commits": {}}, method="put")

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_a_malformed_commit_is_reported(client, deploy_token):
    """Should tell CI which requirement the payload missed."""
    response = send(
        client,
        deploy_token,
        DETAIL_URL,
        {"commits": [commit(repository="")]},
        method="put",
    )

    result = (response.status_code, "repository" in response.json()["detail"])
    expected = (http.HTTPStatus.BAD_REQUEST, True)

    assert result == expected


def test_a_read_token_cannot_attach_commits(client, read_token):
    """Should need the deploy capability, like every other write path."""
    response = send(
        client, read_token, DETAIL_URL, {"commits": [commit()]}, method="put"
    )

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_an_unknown_token_is_refused(client):
    """Should never write from an unauthenticated request."""
    response = client.put(
        DETAIL_URL,
        data=json.dumps({"commits": [commit()]}),
        content_type="application/json",
        headers={"Authorization": "Bearer nope"},
    )

    assert response.status_code == http.HTTPStatus.UNAUTHORIZED


def test_a_get_is_not_allowed(client, deploy_token):
    """Should keep the endpoint to the verbs sentry-cli uses."""
    response = client.get(
        DETAIL_URL, headers={"Authorization": f"Bearer {deploy_token.token}"}
    )

    assert response.status_code == http.HTTPStatus.METHOD_NOT_ALLOWED


def test_the_deploy_route_still_resolves(client, deploy_token):
    """Should not have been shadowed by the release detail route."""
    response = send(
        client,
        deploy_token,
        "/api/0/organizations/pandora/releases/1.4.0/deploys/",
        {"environment": "production"},
    )

    assert response.status_code == http.HTTPStatus.CREATED


def test_a_release_creation_reports_a_bad_commit(client, deploy_token):
    """Should refuse the whole call rather than create a release with no commits."""
    response = send(
        client,
        deploy_token,
        CREATE_URL,
        {"version": "1.4.0", "commits": [commit(id="")]},
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_an_unknown_token_cannot_create_a_release(client):
    """Should refuse the create endpoint the same way the detail one does."""
    response = client.post(
        CREATE_URL,
        data=json.dumps({"version": "1.4.0"}),
        content_type="application/json",
        headers={"Authorization": "Bearer nope"},
    )

    assert response.status_code == http.HTTPStatus.UNAUTHORIZED


def test_a_read_token_cannot_create_a_release(client, read_token):
    """Should need the deploy capability on both endpoints."""
    response = send(client, read_token, CREATE_URL, {"version": "1.4.0"})

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_a_get_to_the_create_endpoint_is_refused(client, deploy_token):
    """Should keep the endpoint to the verb sentry-cli uses."""
    response = client.get(
        CREATE_URL, headers={"Authorization": f"Bearer {deploy_token.token}"}
    )

    assert response.status_code == http.HTTPStatus.METHOD_NOT_ALLOWED


def test_a_commit_entry_that_is_not_an_object_resolves_nothing(client, deploy_token):
    """Should refuse the payload rather than half-apply it."""
    response = send(
        client,
        deploy_token,
        DETAIL_URL,
        {"commits": ["a" * 40]},
        method="put",
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_a_release_with_no_commits_reports_none(client, deploy_token):
    """Should let `sentry-cli releases new` run before set-commits does."""
    response = send(client, deploy_token, CREATE_URL, {"version": "1.4.0"})

    result = (response.json()["commitCount"], response.json()["resolvedIssues"])
    expected = (0, [])

    assert result == expected


def test_a_body_that_is_not_json_is_refused(client, deploy_token):
    """Should name the mistake rather than raise."""
    response = client.post(
        CREATE_URL,
        data=b"{not json",
        content_type="application/json",
        headers={"Authorization": f"Bearer {deploy_token.token}"},
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST
