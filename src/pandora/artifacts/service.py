from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from pandora.artifacts.models import ArtifactBundle, BundleFile, FileKind
from pandora.artifacts.sourcemaps import (
    SourceMap,
    SourceMapError,
    debug_id_of,
    parse,
)
from pandora.core.models import Project

MAX_CHUNKS_PER_REQUEST = 64
MAX_REQUEST_SIZE = 32 * 1024 * 1024
MAX_CONCURRENCY = 8
CHUNK_SIZE = 8 * 1024 * 1024
HASH_ALGORITHM = "sha1"
CAPABILITIES = ("artifact_bundles", "artifact_bundles_v2")
IDLE_AFTER = timedelta(days=90)
MAP_SUFFIX = ".map"

log = logging.getLogger(__name__)
_cache: dict[tuple[int, str], SourceMap] = {}


@dataclass
class Stored:
    bundle: ArtifactBundle
    files: int


def chunk_options() -> dict:
    """What `sentry-cli` negotiates against.

    Advertising only the JavaScript capabilities is what makes unmodified
    tooling negotiate down cleanly rather than fail on a shape it did not
    expect — the native ones would be a promise pandora does not keep.
    """
    return {
        "url": "/api/0/organizations/pandora/chunk-upload/",
        "chunkSize": CHUNK_SIZE,
        "chunksPerRequest": MAX_CHUNKS_PER_REQUEST,
        "maxFileSize": MAX_REQUEST_SIZE,
        "maxRequestSize": MAX_REQUEST_SIZE,
        "concurrency": MAX_CONCURRENCY,
        "hashAlgorithm": HASH_ALGORITHM,
        "compression": ["gzip"],
        "accept": list(CAPABILITIES),
    }


def store_bundle(project: Project, payload: bytes, at: datetime) -> list[Stored]:
    """Take an artifact bundle — a zip of minified files and their maps."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise SourceMapError(f"artifact bundle is not a zip: {error}") from error

    manifest = _manifest(archive)
    stored: dict[str, Stored] = {}
    for name in archive.namelist():
        if name.endswith("/"):
            continue
        body = archive.read(name)
        debug_id = _debug_id(name, body, manifest)
        if not debug_id:
            continue
        entry = stored.get(debug_id)
        if entry is None:
            entry = Stored(bundle=_bundle(project, debug_id, manifest, at), files=0)
            stored[debug_id] = entry
        _file(entry.bundle, name, body)
        entry.files += 1
    return list(stored.values())


def resolve(project_id: int, debug_id: str, line: int, column: int):
    """Look a frame up at read time, against a cache.

    A map uploaded after the error still fixes it, the stored event stays
    exactly what the SDK sent, and the write path stays short — three reasons
    the legacy ingest-time path could not offer.
    """
    source_map = load(project_id, debug_id)
    if source_map is None:
        return None
    return source_map.lookup(line, column)


def load(project_id: int, debug_id: str) -> SourceMap | None:
    key = (project_id, debug_id)
    if key in _cache:
        _touch(project_id, debug_id)
        return _cache[key]

    row = (
        BundleFile.objects.filter(
            bundle__project_id=project_id,
            bundle__debug_id=debug_id,
            kind=FileKind.SOURCE_MAP,
        )
        .select_related("bundle")
        .first()
    )
    if row is None:
        return None
    try:
        parsed = parse(row.blob.read())
    except SourceMapError:
        log.warning("bundle %s holds an unreadable source map", debug_id)
        return None
    _cache[key] = parsed
    _touch(project_id, debug_id)
    return parsed


def forget(project_id: int, debug_id: str) -> None:
    _cache.pop((project_id, debug_id), None)


def clear_cache() -> None:
    _cache.clear()


def prune(now: datetime) -> int:
    """Time to idle, not time to live.

    A bundle in use is kept; one nothing has symbolicated for ninety days is
    collectable. Sentry's model, and the right one — a map for a release still
    running must not expire on a calendar.
    """
    cutoff = now - IDLE_AFTER
    stale = ArtifactBundle.objects.filter(uploaded_at__lt=cutoff).exclude(
        last_used_at__gte=cutoff
    )
    removed = 0
    for bundle in stale:
        forget(bundle.project_id, bundle.debug_id)
        bundle.delete()
        removed += 1
    return removed


def _touch(project_id: int, debug_id: str) -> None:
    ArtifactBundle.objects.filter(project_id=project_id, debug_id=debug_id).update(
        last_used_at=timezone.now()
    )


def _manifest(archive: zipfile.ZipFile) -> dict:
    for name in ("manifest.json", "META-INF/manifest.json"):
        if name in archive.namelist():
            try:
                return json.loads(archive.read(name))
            except ValueError:
                return {}
    return {}


def _debug_id(name: str, body: bytes, manifest: dict) -> str:
    files = manifest.get("files") or {}
    entry = files.get(name) or {}
    headers = entry.get("headers") or {}
    for key in ("debug-id", "debug_id", "debugId"):
        if headers.get(key):
            return str(headers[key])
    if name.endswith(MAP_SUFFIX):
        try:
            document = json.loads(body)
        except ValueError:
            return ""
        return debug_id_of(document)
    return ""


def _bundle(
    project: Project, debug_id: str, manifest: dict, at: datetime
) -> ArtifactBundle:
    bundle, _ = ArtifactBundle.objects.get_or_create(
        project=project,
        debug_id=debug_id,
        defaults={
            "release": str(manifest.get("release", ""))[:250],
            "dist": str(manifest.get("dist", ""))[:100],
            "uploaded_at": at,
        },
    )
    return bundle


def _file(bundle: ArtifactBundle, name: str, body: bytes) -> BundleFile:
    kind = FileKind.MINIFIED
    if name.endswith(MAP_SUFFIX):
        kind = FileKind.SOURCE_MAP
    BundleFile.objects.filter(bundle=bundle, path=name[:500]).delete()
    row = BundleFile(
        bundle=bundle,
        path=name[:500],
        kind=kind,
        size=len(body),
        sha1=hashlib.sha1(body, usedforsecurity=False).hexdigest(),
    )
    row.blob.save(
        f"{bundle.debug_id}-{hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:12]}",
        ContentFile(body),
        save=False,
    )
    row.save()
    return row
