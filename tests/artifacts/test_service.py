import datetime
import io
import json
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
    """Should let sentry-cli negotiate down cleanly rather than promise symbolication."""
    result = service.chunk_options()["accept"]
    expected = ["artifact_bundles", "artifact_bundles_v2"]

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
