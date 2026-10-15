import datetime
import http
import json

import pytest

from pandora.core import models as core_models
from pandora.releases import models

pytestmark = pytest.mark.django_db

FIRST_SEEN = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=datetime.UTC)


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


def previous_url(version):
    return f"/api/0/organizations/pandora/releases/{version}/previous-with-commits/"


def ask(client, token, version):
    return client.get(
        previous_url(version), headers={"Authorization": f"Bearer {token.token}"}
    )


def release_at(project, version, days_ago):
    return models.Release.objects.create(
        project=project,
        version=version,
        first_seen=FIRST_SEEN - datetime.timedelta(days=days_ago),
    )


def with_commits(release, *keys):
    repository, _ = models.Repository.objects.get_or_create(
        project=release.project, name="sophotechlabs/pandora"
    )
    for order, key in enumerate(keys):
        found = models.Commit.objects.create(
            repository=repository,
            key=key,
            committed_at=release.first_seen,
        )
        models.ReleaseCommit.objects.create(release=release, commit=found, order=order)
    return release


def test_a_first_release_has_no_previous_commits(client, deploy_token, project):
    """Should answer an empty object, which sentry-cli reads as start from scratch."""
    release_at(project, "1.4.0", 0)

    response = ask(client, deploy_token, "1.4.0")

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.OK, {})

    assert result == expected


def test_the_previous_release_names_its_newest_commit(client, deploy_token, project):
    """Should hand back the commit sentry-cli stops walking git history at."""
    with_commits(release_at(project, "1.3.0", 2), "b" * 40, "c" * 40)
    release_at(project, "1.4.0", 0)

    response = ask(client, deploy_token, "1.4.0")

    result = (response.json()["version"], response.json()["lastCommit"])
    expected = ("1.3.0", {"id": "b" * 40})

    assert result == expected


def test_a_release_without_commits_is_not_the_previous_one(
    client, deploy_token, project
):
    """Should skip a release CI never attached commits to."""
    with_commits(release_at(project, "1.2.0", 4), "d" * 40)
    release_at(project, "1.3.0", 2)
    release_at(project, "1.4.0", 0)

    response = ask(client, deploy_token, "1.4.0")

    assert response.json()["version"] == "1.2.0"


def test_a_later_release_is_not_the_previous_one(client, deploy_token, project):
    """Should only look backwards from the release being described."""
    release_at(project, "1.4.0", 2)
    with_commits(release_at(project, "1.5.0", 0), "e" * 40)

    response = ask(client, deploy_token, "1.4.0")

    assert response.json() == {}


def test_another_projects_release_is_not_the_previous_one(
    client, deploy_token, project
):
    """Should keep one project's history out of another's set-commits."""
    other = core_models.Project.objects.create(slug="other", name="Other")
    with_commits(release_at(other, "1.3.0", 2), "f" * 40)
    release_at(project, "1.4.0", 0)

    response = ask(client, deploy_token, "1.4.0")

    assert response.json() == {}


def test_an_unknown_release_has_no_previous_one(client, deploy_token):
    """Should answer 404 for a version this project never created."""
    response = ask(client, deploy_token, "9.9.9")

    assert response.status_code == http.HTTPStatus.NOT_FOUND


def test_asking_for_previous_commits_needs_the_deploy_capability(
    client, read_token, project
):
    """Should not let a read token see release history through this door."""
    release_at(project, "1.4.0", 0)

    response = ask(client, read_token, "1.4.0")

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_previous_commits_are_only_read(client, deploy_token, project):
    """Should refuse a write on a route sentry-cli only reads."""
    release_at(project, "1.4.0", 0)

    response = client.post(
        previous_url("1.4.0"),
        data=json.dumps({}),
        content_type="application/json",
        headers={"Authorization": f"Bearer {deploy_token.token}"},
    )

    result = (response.status_code, response["Allow"])
    expected = (http.HTTPStatus.METHOD_NOT_ALLOWED, "GET")

    assert result == expected
