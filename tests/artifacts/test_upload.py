import gzip
import hashlib
import http
import io
import json
import pathlib

import pytest

from pandora.artifacts import models as artifact_models
from tests.bundles import DEBUG_ID

pytestmark = pytest.mark.django_db

URL = "/api/0/organizations/pandora/chunk-upload/"
ASSEMBLE = "/api/0/organizations/pandora/artifactbundle/assemble/"


def auth(token):
    return {"Authorization": f"Bearer {token.token}"}


def sha1(data):
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def send_chunks(client, token, payload, size=None):
    pieces = _split(payload, size or len(payload))
    files = {sha1(piece): io.BytesIO(piece) for piece in pieces}
    for name, handle in files.items():
        handle.name = name
    response = client.post(URL, files, headers=auth(token))
    return response, [sha1(piece) for piece in pieces]


def assemble(client, token, payload, checksums, checksum=None):
    body = {
        "checksum": checksum or sha1(payload),
        "chunks": checksums,
        "projects": ["live"],
    }
    return client.post(
        ASSEMBLE,
        data=json.dumps(body),
        content_type="application/json",
        headers=auth(token),
    )


def _split(payload, size):
    return [
        payload[start : start + size] for start in range(0, len(payload), size)
    ] or [b""]


# negotiation


def test_the_options_are_advertised(client, token):
    """Should be the first thing sentry-cli asks, and it must answer."""
    response = client.get(URL, headers=auth(token))

    result = (response.status_code, response.json()["hashAlgorithm"])
    expected = (http.HTTPStatus.OK, "sha1")

    assert result == expected


def test_the_options_accept_release_files(client, token):
    """Should be the capability sentry-cli gates the chunked path on.

    Without it the tool prints a deprecation notice and falls back to an upload
    that needs a release — which is what a real run found.
    """
    result = client.get(URL, headers=auth(token)).json()["accept"]

    assert "release_files" in result


def test_the_options_accept_artifact_bundles(client, token):
    """Should be what lets a source map be uploaded without naming a release."""
    result = client.get(URL, headers=auth(token)).json()["accept"]

    assert "artifact_bundles" in result


def test_the_options_need_a_token(client):
    """Should not tell an anonymous caller what this server accepts."""
    result = client.get(URL).status_code
    expected = http.HTTPStatus.UNAUTHORIZED

    assert result == expected


# uploading


def test_a_chunk_is_stored_under_its_checksum(client, token, bundle_bytes):
    """Should address a chunk by what it holds, which is how resume works."""
    payload = bundle_bytes()

    response, checksums = send_chunks(client, token, payload)

    result = (
        response.status_code,
        list(artifact_models.UploadChunk.objects.values_list("checksum", flat=True)),
    )
    expected = (http.HTTPStatus.OK, checksums)

    assert result == expected


def test_a_gzipped_chunk_is_stored_under_the_plain_checksum(
    client, token, bundle_bytes
):
    """Should checksum what the chunk means, not how it travelled."""
    payload = bundle_bytes()

    files = {sha1(payload): io.BytesIO(gzip.compress(payload))}
    for name, handle in files.items():
        handle.name = name
    client.post(URL, files, headers=auth(token))

    result = list(
        artifact_models.UploadChunk.objects.values_list("checksum", flat=True)
    )
    expected = [sha1(payload)]

    assert result == expected


def test_the_same_chunk_twice_is_stored_once(client, token, bundle_bytes):
    """Should make a retried upload cheap rather than duplicated."""
    payload = bundle_bytes()

    send_chunks(client, token, payload)
    send_chunks(client, token, payload)

    result = artifact_models.UploadChunk.objects.count()
    expected = 1

    assert result == expected


def test_a_post_with_no_chunk_is_refused(client, token):
    """Should say what is missing rather than store nothing quietly."""
    result = client.post(URL, {}, headers=auth(token)).status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_an_oversized_chunk_is_refused(client, token):
    """Should hold the contract's own 32 MiB rather than accept anything."""
    from pandora.artifacts import service

    handle = io.BytesIO(b"x" * (service.MAX_REQUEST_SIZE + 1))
    handle.name = "a" * 40

    result = client.post(URL, {"a" * 40: handle}, headers=auth(token)).status_code
    expected = http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    assert result == expected


def test_a_compressed_chunk_cannot_expand_past_the_limit(client, token, monkeypatch):
    from pandora.artifacts import service

    monkeypatch.setattr(service, "CHUNK_SIZE", 8)
    handle = io.BytesIO(gzip.compress(b"x" * 9))
    handle.name = "chunk"

    result = client.post(URL, {"chunk": handle}, headers=auth(token)).status_code

    assert result == http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_too_many_chunks_are_refused_before_any_are_stored(client, token, monkeypatch):
    from pandora.artifacts import service

    monkeypatch.setattr(service, "MAX_CHUNKS_PER_REQUEST", 2)
    files = {}
    for index in range(3):
        handle = io.BytesIO(str(index).encode())
        handle.name = str(index)
        files[str(index)] = handle

    response = client.post(URL, files, headers=auth(token))

    result = (response.status_code, artifact_models.UploadChunk.objects.count())
    expected = (http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 0)
    assert result == expected


# assembling


def test_assembling_stores_the_bundle(client, token, bundle_bytes):
    """Should be the end of the two-phase upload: chunks in, bundle out."""
    payload = bundle_bytes()
    _, checksums = send_chunks(client, token, payload)

    response = assemble(client, token, payload, checksums)

    result = (
        response.json()["state"],
        artifact_models.ArtifactBundle.objects.count(),
    )
    expected = ("ok", 1)

    assert result == expected


def test_the_assembled_bundle_carries_the_debug_id(client, token, bundle_bytes):
    """Should be addressable by the id the tooling injected."""
    payload = bundle_bytes()
    _, checksums = send_chunks(client, token, payload)

    assemble(client, token, payload, checksums)

    result = artifact_models.ArtifactBundle.objects.get().debug_id
    expected = DEBUG_ID

    assert result == expected


def test_assembling_a_multi_chunk_bundle_joins_them_in_order(
    client, token, bundle_bytes
):
    """Should reassemble a real upload, which arrives in pieces."""
    payload = bundle_bytes()
    _, checksums = send_chunks(client, token, payload, size=64)

    response = assemble(client, token, payload, checksums)

    result = (len(checksums) > 1, response.json()["state"])
    expected = (True, "ok")

    assert result == expected


def test_assembling_clears_the_chunks(client, token, bundle_bytes):
    """Should not keep the rubble once the bundle exists."""
    payload = bundle_bytes()
    _, checksums = send_chunks(client, token, payload)

    assemble(client, token, payload, checksums)

    result = artifact_models.UploadChunk.objects.count()
    expected = 0

    assert result == expected


def test_assembling_removes_the_chunk_files(client, token, bundle_bytes):
    payload = bundle_bytes()
    send_chunks(client, token, payload)
    paths = [row.blob.path for row in artifact_models.UploadChunk.objects.all()]

    assemble(client, token, payload, [sha1(payload)])

    assert all(not pathlib.Path(path).exists() for path in paths)


def test_assembling_names_the_chunks_it_has_not_got(client, token, bundle_bytes):
    """Should tell the client what to send rather than fail an upload it can finish."""
    payload = bundle_bytes()
    checksums = [sha1(payload)]

    response = assemble(client, token, payload, checksums)

    result = (response.json()["state"], response.json()["missingChunks"])
    expected = ("not_found", checksums)

    assert result == expected


def test_a_checksum_that_does_not_match_is_refused(client, token, bundle_bytes):
    """Should not store a bundle the client and the server disagree about."""
    payload = bundle_bytes()
    _, checksums = send_chunks(client, token, payload)

    response = assemble(client, token, payload, checksums, checksum="f" * 40)

    result = (response.json()["state"], artifact_models.ArtifactBundle.objects.count())
    expected = ("error", 0)

    assert result == expected


def test_a_bundle_with_no_debug_id_is_refused(client, token, bundle_bytes):
    """Should tell the operator their plugin is not injecting ids."""
    payload = bundle_bytes(document={"version": 3, "sources": [], "mappings": "A"})
    _, checksums = send_chunks(client, token, payload)

    response = assemble(client, token, payload, checksums)

    result = (response.json()["state"], "debug id" in response.json()["detail"])
    expected = ("error", True)

    assert result == expected


def test_something_that_is_not_a_zip_is_refused(client, token):
    """Should name the problem so CI fails with a readable message."""
    payload = b"not a zip"
    _, checksums = send_chunks(client, token, payload)

    response = assemble(client, token, payload, checksums)

    result = response.json()["state"]
    expected = "error"

    assert result == expected


def test_an_assemble_without_chunks_is_refused(client, token):
    """Should not accept a request that names nothing to join."""
    response = client.post(
        ASSEMBLE,
        data=json.dumps({"checksum": "a" * 40}),
        content_type="application/json",
        headers=auth(token),
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_an_assemble_that_is_not_json_is_refused(client, token):
    """Should answer the shape rather than raise."""
    response = client.post(
        ASSEMBLE, data=b"nonsense", content_type="application/json", headers=auth(token)
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


def test_an_assemble_that_is_not_an_object_is_refused(client, token):
    """Should not read a list as a request."""
    response = client.post(
        ASSEMBLE, data=b"[1]", content_type="application/json", headers=auth(token)
    )

    result = response.status_code
    expected = http.HTTPStatus.BAD_REQUEST

    assert result == expected


@pytest.mark.parametrize("chunks", ["abc", 42, ["ok", 42]])
def test_an_assemble_refuses_malformed_chunk_lists(client, token, chunks):
    response = client.post(
        ASSEMBLE,
        data=json.dumps({"checksum": "a" * 40, "chunks": chunks}),
        content_type="application/json",
        headers=auth(token),
    )

    assert response.status_code == http.HTTPStatus.BAD_REQUEST


def test_an_assemble_refuses_too_many_chunks(client, token, monkeypatch):
    from pandora.artifacts import service

    monkeypatch.setattr(service, "MAX_CHUNKS_PER_REQUEST", 2)
    response = client.post(
        ASSEMBLE,
        data=json.dumps({"checksum": "a" * 40, "chunks": ["a", "b", "c"]}),
        content_type="application/json",
        headers=auth(token),
    )

    assert response.status_code == http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_an_assemble_needs_a_token(client):
    """Should keep assembly behind the same credential as the upload."""
    result = client.post(ASSEMBLE, data=b"{}", content_type="application/json")

    assert result.status_code == http.HTTPStatus.UNAUTHORIZED


def test_an_assemble_refuses_a_get(client, token):
    """Should be a POST, and say so."""
    result = client.get(ASSEMBLE, headers=auth(token)).status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED

    assert result == expected


# housekeeping


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


def test_an_abandoned_chunk_is_swept(client, token, bundle_bytes, settings):
    """Should not keep the rubble of an upload nobody finished."""
    import datetime

    from django.utils import timezone

    from pandora.artifacts import service

    send_chunks(client, token, bundle_bytes())
    paths = [row.blob.path for row in artifact_models.UploadChunk.objects.all()]
    later = timezone.now() + service.CHUNK_TTL + datetime.timedelta(minutes=1)

    service.sweep_chunks(later)

    result = (
        artifact_models.UploadChunk.objects.count(),
        all(not pathlib.Path(path).exists() for path in paths),
    )
    expected = (0, True)

    assert result == expected
