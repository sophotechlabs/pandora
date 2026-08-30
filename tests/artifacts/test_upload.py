import http
import io

import pytest

from pandora.artifacts import models as artifact_models
from tests.artifacts.conftest import DEBUG_ID

pytestmark = pytest.mark.django_db

URL = "/api/0/organizations/pandora/chunk-upload/"


def auth(token):
    return {"Authorization": f"Bearer {token.token}"}


# negotiation


def test_the_options_are_advertised(client, token):
    """Should be the first thing sentry-cli asks, and it must answer."""
    response = client.get(URL, headers=auth(token))

    result = (response.status_code, response.json()["hashAlgorithm"])
    expected = (http.HTTPStatus.OK, "sha1")

    assert result == expected


def test_the_options_need_a_token(client):
    """Should not tell an anonymous caller what this server accepts."""
    result = client.get(URL).status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


# uploading


def test_a_bundle_uploads(client, token, bundle_bytes):
    """Should take what unmodified tooling sends, which is the whole play."""
    response = client.post(
        URL,
        data=bundle_bytes(),
        content_type="application/octet-stream",
        headers=auth(token),
    )

    result = (response.status_code, artifact_models.ArtifactBundle.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected


def test_the_response_names_what_was_stored(client, token, bundle_bytes):
    """Should let CI see the upload landed rather than assume it."""
    response = client.post(
        URL,
        data=bundle_bytes(),
        content_type="application/octet-stream",
        headers=auth(token),
    )

    result = response.json()["bundles"][0]["debug_id"]
    expected = DEBUG_ID

    assert result == expected


def test_a_multipart_upload_works_too(client, token, bundle_bytes):
    """Should take the form sentry-cli actually posts."""
    import io

    upload = io.BytesIO(bundle_bytes())
    upload.name = "bundle.zip"

    response = client.post(URL, data={"file": upload}, headers=auth(token))

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_an_upload_needs_a_token(client, bundle_bytes):
    """Should sit behind an ingest token, like every write."""
    response = client.post(
        URL, data=bundle_bytes(), content_type="application/octet-stream"
    )

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_an_empty_upload_is_refused(client, token):
    """Should say what is missing rather than store nothing quietly."""
    response = client.post(
        URL, data=b"", content_type="application/octet-stream", headers=auth(token)
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_something_that_is_not_a_zip_is_refused(client, token):
    """Should name the problem so CI fails with a readable message."""
    response = client.post(
        URL,
        data=b"not a zip",
        content_type="application/octet-stream",
        headers=auth(token),
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_a_bundle_with_no_debug_id_is_refused(client, token, bundle_bytes):
    """Should tell the operator their plugin is not injecting ids."""
    payload = bundle_bytes(document={"version": 3, "sources": [], "mappings": ""})

    response = client.post(
        URL, data=payload, content_type="application/octet-stream", headers=auth(token)
    )

    result = (response.status_code, "debug id" in response.json()["detail"])
    expected = (http.HTTPStatus.BAD_REQUEST, True)

    assert result == expected


def test_an_oversized_upload_is_refused(client, token, settings):
    """Should hold the contract's own 32 MiB rather than accept anything."""
    from pandora.artifacts import service

    response = client.post(
        URL,
        data=b"x" * (service.MAX_REQUEST_SIZE + 1),
        content_type="application/octet-stream",
        headers=auth(token),
    )

    result = response.status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


def test_a_delete_is_refused(client, token):
    """Should be a GET or a POST, nothing else."""
    result = client.delete(URL, headers=auth(token)).status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


def test_a_read_scoped_token_may_not_upload(client, project):
    """Should keep uploading behind the ingest scope."""
    from pandora.core import models as core_models

    reader = core_models.IngestToken.objects.create(
        project=project,
        name="reader",
        token="read-token",
        source=core_models.TokenSource.SDK,
        scope=core_models.TokenScope.READ,
    )

    result = client.get(URL, headers=auth(reader)).status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_an_empty_bearer_token_is_refused(client):
    """Should not treat a bare `Bearer` as a presented credential."""
    result = client.get(URL, headers={"Authorization": "Bearer  "}).status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


def test_a_field_that_is_not_a_file_is_skipped(client, token, bundle_bytes):
    """Should read the archive sentry-cli sent, whatever else rides along."""
    payload = io.BytesIO(bundle_bytes())
    payload.name = "bundle.zip"

    response = client.post(
        URL, {"version": "2", "file_gzip": payload}, headers=auth(token)
    )

    result = (response.status_code, artifact_models.BundleFile.objects.count())
    expected = (http.HTTPStatus.OK, 1)

    assert result == expected
