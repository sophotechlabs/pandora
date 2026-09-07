import datetime
import http
import json

import pytest

from pandora.core import models as core_models
from pandora.issues import models as issue_models
from pandora.releases import models

pytestmark = pytest.mark.django_db

URL = "/api/0/organizations/pandora/releases/1.2.3/deploys/"


@pytest.fixture
def deploy_token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="ci",
        token="ci-token",
        source=core_models.TokenSource.CI,
        scopes=(core_models.TokenScope.ARTIFACTS, core_models.TokenScope.DEPLOY),
    )


def post(client, token, body):
    return client.post(
        URL,
        data=json.dumps(body),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token.token}"},
    )


def test_a_sentry_deploy_is_created(client, deploy_token):
    response = post(
        client,
        deploy_token,
        {"environment": "production", "name": "pipeline 42"},
    )

    deploy = models.Deploy.objects.get()
    assert response.status_code == http.HTTPStatus.CREATED
    assert deploy.state == models.DeployState.SUCCEEDED
    assert deploy.identifier.startswith("sentry:")
    assert response.json()["environment"] == "production"


def test_an_identical_request_is_idempotent_forever(client, deploy_token):
    first = post(client, deploy_token, {"environment": "production"})
    second = post(client, deploy_token, {"environment": "production"})

    assert first.json() == second.json()
    assert models.Deploy.objects.count() == 1


def test_supplied_timestamps_are_kept(client, deploy_token):
    post(
        client,
        deploy_token,
        {
            "environment": "production",
            "dateStarted": "2026-09-02T10:00:00Z",
            "dateFinished": "2026-09-02T10:05:00Z",
        },
    )

    deploy = models.Deploy.objects.get()
    assert deploy.started_at == datetime.datetime(2026, 9, 2, 10, tzinfo=datetime.UTC)
    assert deploy.finished_at == datetime.datetime(
        2026, 9, 2, 10, 5, tzinfo=datetime.UTC
    )


def test_environment_is_required(client, deploy_token):
    response = post(client, deploy_token, {})

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_finish_cannot_precede_start(client, deploy_token):
    response = post(
        client,
        deploy_token,
        {
            "environment": "production",
            "dateStarted": "2026-09-02T10:05:00Z",
            "dateFinished": "2026-09-02T10:00:00Z",
        },
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_the_token_project_is_the_authority(client, deploy_token):
    response = post(
        client,
        deploy_token,
        {"environment": "production", "projects": ["another"]},
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST
    assert models.Deploy.objects.count() == 0


def test_a_token_without_deploy_access_is_refused(client, project):
    token = core_models.IngestToken.objects.create(
        project=project,
        name="reader",
        token="reader-token",
        scope=core_models.TokenScope.READ,
    )

    response = post(client, token, {"environment": "production"})

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_resolve_on_deploy_runs_only_once(client, deploy_token, issue):
    deploy_token.project.resolve_on_deploy = True
    deploy_token.project.save(update_fields=["resolve_on_deploy"])

    post(client, deploy_token, {"environment": "p-mk1"})
    post(client, deploy_token, {"environment": "p-mk1"})

    issue.refresh_from_db()
    assert issue.triage_state == issue_models.TriageState.RESOLVED
