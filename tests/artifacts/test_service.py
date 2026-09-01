import datetime
import gzip
import hashlib
import io
import json
import pathlib
import zipfile

import pytest
from django.utils import timezone

from pandora.artifacts import models as artifact_models
from pandora.artifacts import service
from pandora.artifacts.sourcemaps import SourceMapError
from tests.artifacts.conftest import DEBUG_ID, MAP

pytestmark = pytest.mark.django_db

NOW = timezone.now()


# what sentry-cli negotiates against


def test_the_advertised_constants_match_the_upload_contract():
    """Should be verbatim, or unmodified tooling will not negotiate."""
    options = service.chunk_options()

    result = (
        options["chunksPerRequest"],
        options["maxRequestSize"],
        options["concurrency"],
        options["hashAlgorithm"],
    )
    expected = (64, 32 * 1024 * 1024, 8, "sha1")

    assert result == expected


def test_only_the_javascript_capabilities_are_advertised():
    """Should promise the JavaScript path and nothing native.

    `release_files` is what sentry-cli gates the chunked upload on and
    `artifact_bundles` is what lets it upload without naming a release. The
    native ones — debug_files, pdbs, bcsymbolmaps — stay off the list because
    they would be a promise pandora does not keep.
    """
    result = service.chunk_options()["accept"]
    expected = ["release_files", "artifact_bundles", "artifact_bundles_v2"]

    assert result == expected


# storing a bundle


def test_a_bundle_is_stored_under_its_debug_id(project, bundle_bytes):
    """Should address by the id the tooling injected, not by a filename."""
    service.store_bundle(project, bundle_bytes(), NOW)

    result = artifact_models.ArtifactBundle.objects.get().debug_id
    expected = DEBUG_ID

    assert result == expected


def test_the_map_is_kept_as_a_file(project, bundle_bytes):
    """Should store the blob beside the database, not inside the events table."""
    service.store_bundle(project, bundle_bytes(), NOW)

    row = artifact_models.BundleFile.objects.get()
    result = (row.kind, row.size > 0)
    expected = (artifact_models.FileKind.SOURCE_MAP, True)

    assert result == expected


def test_a_debug_id_from_the_manifest_is_used(project, bundle_bytes):
    """Should take the header the bundler plugin writes, which the map may lack."""
    payload = bundle_bytes(
        document={"version": 3, "sources": [], "mappings": ""},
        manifest={"files": {"app.js.map": {"headers": {"debug-id": "from-manifest"}}}},
    )

    service.store_bundle(project, payload, NOW)

    result = artifact_models.ArtifactBundle.objects.get().debug_id
    expected = "from-manifest"

    assert result == expected


def test_the_release_is_taken_from_the_manifest(project, bundle_bytes):
    """Should keep the weak association debug ids made optional."""
    payload = bundle_bytes(manifest={"release": "1.2.3", "files": {}})

    service.store_bundle(project, payload, NOW)

    result = artifact_models.ArtifactBundle.objects.get().release
    expected = "1.2.3"

    assert result == expected


def test_a_file_with_no_debug_id_is_skipped(project, bundle_bytes):
    """Should not store what it can never look up."""
    payload = bundle_bytes(document={"version": 3, "sources": [], "mappings": ""})

    result = service.store_bundle(project, payload, NOW)

    assert result == []


def test_something_that_is_not_a_zip_is_refused(project):
    """Should name the problem rather than store a blob nobody can open."""
    with pytest.raises(SourceMapError, match="not a zip"):
        service.store_bundle(project, b"not a zip", NOW)


def test_re_uploading_replaces_the_file(project, bundle_bytes):
    """Should let a rebuild overwrite its own map rather than accumulate."""
    service.store_bundle(project, bundle_bytes(), NOW)
    service.store_bundle(project, bundle_bytes(), NOW)

    result = artifact_models.BundleFile.objects.count()
    expected = 1

    assert result == expected


def test_re_uploading_removes_the_replaced_blob(project, bundle_bytes):
    service.store_bundle(project, bundle_bytes(), NOW)
    original = pathlib.Path(artifact_models.BundleFile.objects.get().blob.path)

    service.store_bundle(project, bundle_bytes(), NOW)

    assert list(original.parent.iterdir()) == [original]


def test_a_bundle_reads_as_its_id_and_release(project, bundle_bytes):
    """Should be legible in the admin without opening the row."""
    service.store_bundle(project, bundle_bytes(), NOW)

    result = str(artifact_models.ArtifactBundle.objects.get())

    assert result.startswith(DEBUG_ID[:12])


def test_a_file_reads_as_its_path(project, bundle_bytes):
    """Should say which file it is without following the bundle."""
    service.store_bundle(project, bundle_bytes(), NOW)

    result = str(artifact_models.BundleFile.objects.get())

    assert "app.js.map" in result


def test_a_manifest_that_is_not_json_is_ignored(project, bundle_bytes):
    """Should fall back to the map's own debug id rather than fail the upload."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", "not json")
        archive.writestr("app.js.map", json.dumps(MAP))

    result = service.store_bundle(project, buffer.getvalue(), NOW)

    assert len(result) == 1


@pytest.mark.parametrize("manifest", [[], {"files": []}, {"files": {"app.js.map": []}}])
def test_malformed_manifest_shapes_are_ignored(project, bundle_bytes, manifest):
    result = service.store_bundle(project, bundle_bytes(manifest=manifest), NOW)

    assert len(result) == 1


def test_an_overlong_debug_id_is_skipped(project, bundle_bytes):
    payload = bundle_bytes(
        document={"version": 3, "sources": [], "mappings": ""},
        manifest={"files": {"app.js.map": {"headers": {"debug-id": "x" * 65}}}},
    )

    result = service.store_bundle(project, payload, NOW)

    assert result == []


def test_an_archive_that_expands_past_the_limit_is_refused(
    project, bundle_bytes, monkeypatch
):
    monkeypatch.setattr(service, "MAX_EXTRACTED_SIZE", 1)

    with pytest.raises(SourceMapError, match="expands"):
        service.store_bundle(project, bundle_bytes(), NOW)


def test_a_directory_entry_is_skipped(project):
    """Should not try to read a folder as a source map."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("files/", b"")
        archive.writestr("files/app.js.map", json.dumps(MAP))

    result = service.store_bundle(project, buffer.getvalue(), NOW)

    assert len(result) == 1


def test_a_map_that_is_not_json_is_skipped(project):
    """Should skip one bad file rather than fail the whole bundle."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("broken.js.map", b"not json")
        archive.writestr("app.js.map", json.dumps(MAP))

    result = service.store_bundle(project, buffer.getvalue(), NOW)

    assert len(result) == 1


# resolving


def test_a_frame_resolves_through_the_stored_map(project, bundle_bytes):
    """Should be the whole feature: minified in, original out."""
    service.store_bundle(project, bundle_bytes(), NOW)

    position = service.resolve(project.pk, DEBUG_ID, 1, 0)

    result = (position.source, position.line)
    expected = ("src/payments.js", 1)

    assert result == expected


def test_an_unknown_debug_id_resolves_to_nothing(project):
    """Should say it cannot answer, which the UI turns into a useful sentence."""
    result = service.resolve(project.pk, "nothing", 1, 0)

    assert result is None


def test_a_stored_map_that_is_unreadable_resolves_to_nothing(project, bundle_bytes):
    """Should not take the page down over a corrupt upload."""
    service.store_bundle(project, bundle_bytes(), NOW)
    row = artifact_models.BundleFile.objects.get()
    row.blob.save(row.blob.name, io.BytesIO(b"not json"), save=True)
    service.clear_cache()

    result = service.resolve(project.pk, DEBUG_ID, 1, 0)

    assert result is None


def test_resolution_is_cached(project, bundle_bytes):
    """Should decode a map once, not once per frame."""
    service.store_bundle(project, bundle_bytes(), NOW)
    service.resolve(project.pk, DEBUG_ID, 1, 0)
    artifact_models.BundleFile.objects.all().delete()

    result = service.resolve(project.pk, DEBUG_ID, 1, 0)

    assert result is not None


def test_using_a_bundle_marks_it_as_used(project, bundle_bytes):
    """Should be what makes retention time-to-idle rather than time-to-live."""
    service.store_bundle(project, bundle_bytes(), NOW)

    service.resolve(project.pk, DEBUG_ID, 1, 0)

    result = artifact_models.ArtifactBundle.objects.get().last_used_at

    assert result is not None


# retention


def test_a_bundle_in_use_survives(project, bundle_bytes):
    """Should keep the map for a release that is still running."""
    service.store_bundle(project, bundle_bytes(), NOW)
    artifact_models.ArtifactBundle.objects.update(
        uploaded_at=NOW - datetime.timedelta(days=200), last_used_at=NOW
    )

    service.prune(NOW)

    result = artifact_models.ArtifactBundle.objects.count()
    expected = 1

    assert result == expected


def test_a_bundle_nothing_has_used_is_collected(project, bundle_bytes):
    """Should be ninety days idle, which is Sentry's model and the right one."""
    service.store_bundle(project, bundle_bytes(), NOW)
    artifact_models.ArtifactBundle.objects.update(
        uploaded_at=NOW - datetime.timedelta(days=200), last_used_at=None
    )

    result = service.prune(NOW)
    expected = 1

    assert result == expected


def test_collecting_a_bundle_removes_its_files(project, bundle_bytes):
    service.store_bundle(project, bundle_bytes(), NOW)
    path = artifact_models.BundleFile.objects.get().blob.path
    artifact_models.ArtifactBundle.objects.update(
        uploaded_at=NOW - datetime.timedelta(days=200), last_used_at=None
    )

    service.prune(NOW)

    assert not pathlib.Path(path).exists()


def test_a_fresh_bundle_is_never_collected(project, bundle_bytes):
    """Should not delete a map uploaded before the error it explains."""
    service.store_bundle(project, bundle_bytes(), NOW)

    result = service.prune(NOW)
    expected = 0

    assert result == expected


def test_collecting_a_bundle_forgets_its_cache(project, bundle_bytes):
    """Should not answer from a map it has just deleted."""
    service.store_bundle(project, bundle_bytes(), NOW)
    service.resolve(project.pk, DEBUG_ID, 1, 0)
    artifact_models.ArtifactBundle.objects.update(
        uploaded_at=NOW - datetime.timedelta(days=200), last_used_at=None
    )

    service.prune(NOW)

    result = service.resolve(project.pk, DEBUG_ID, 1, 0)

    assert result is None


# chunks


def test_a_chunk_that_only_looks_gzipped_is_kept_as_it_is(project):
    """Should not lose a chunk that happens to open with the gzip magic number."""
    raw = b"\x1f\x8bnot actually gzip"

    checksum = service.store_chunk(project, raw, NOW)

    result = service.missing_chunks(
        project, [hashlib.sha1(raw, usedforsecurity=False).hexdigest()]
    )
    expected = []

    assert (result, checksum) == (
        expected,
        hashlib.sha1(raw, usedforsecurity=False).hexdigest(),
    )


def test_a_chunk_names_itself_in_the_admin(project, bundle_bytes):
    """Should be findable by the first characters of its checksum."""
    service.store_chunk(project, bundle_bytes(), NOW)

    chunk = artifact_models.UploadChunk.objects.get()

    result = str(chunk)
    expected = chunk.checksum[:12]

    assert result == expected


def test_a_gzip_chunk_cannot_expand_past_the_chunk_limit(project, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_SIZE", 8)

    with pytest.raises(service.ChunkTooLarge):
        service.store_chunk(project, gzip.compress(b"x" * 9), NOW)


def test_re_uploading_a_chunk_refreshes_its_expiry(project, bundle_bytes):
    service.store_chunk(project, bundle_bytes(), NOW)
    later = NOW + datetime.timedelta(hours=1)

    service.store_chunk(project, bundle_bytes(), later)

    result = artifact_models.UploadChunk.objects.get().received_at
    assert result == later
