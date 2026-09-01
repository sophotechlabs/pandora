from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from pandora.artifacts.models import (
    ArtifactBundle,
    BundleFile,
    FileKind,
    UploadChunk,
)
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
CAPABILITIES = ("release_files", "artifact_bundles", "artifact_bundles_v2")
GZIP_MAGIC = b"\x1f\x8b"
CHUNK_TTL = timedelta(hours=6)
STATE_OK = "ok"
STATE_MISSING = "not_found"
STATE_ERROR = "error"
IDLE_AFTER = timedelta(days=90)
MAP_SUFFIX = ".map"
MAX_BUNDLE_FILES = 4096
MAX_EXTRACTED_SIZE = 128 * 1024 * 1024
MAX_DEBUG_ID_LENGTH = 64

log = logging.getLogger(__name__)
_cache: dict[tuple[int, str], SourceMap] = {}


@dataclass
class Stored:
    bundle: ArtifactBundle
    files: int


class ChunkTooLarge(ValueError):
    pass


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


def store_chunk(project: Project, raw: bytes, at: datetime) -> str:
    """Keep one chunk under the checksum of what it decompresses to.

    The client may gzip a chunk when the server advertises it, and the checksum
    is always of the plain bytes — so the magic number decides, not a header
    that multipart does not carry per part.
    """
    data = _plain(raw)
    if len(data) > CHUNK_SIZE:
        raise ChunkTooLarge
    checksum = hashlib.sha1(data, usedforsecurity=False).hexdigest()
    existing = UploadChunk.objects.filter(project=project, checksum=checksum).first()
    if existing is not None:
        UploadChunk.objects.filter(pk=existing.pk).update(received_at=at)
        return checksum
    chunk = UploadChunk(
        project=project,
        checksum=checksum,
        size=len(data),
        received_at=at,
    )
    chunk.blob.save(f"{checksum}.chunk", ContentFile(data), save=False)
    try:
        with transaction.atomic():
            chunk.save(force_insert=True)
    except IntegrityError:
        chunk.blob.delete(save=False)
        if UploadChunk.objects.filter(project=project, checksum=checksum).exists():
            UploadChunk.objects.filter(project=project, checksum=checksum).update(
                received_at=at
            )
            return checksum
        raise
    except Exception:
        chunk.blob.delete(save=False)
        raise
    return checksum


def missing_chunks(project: Project, checksums: list[str]) -> list[str]:
    held = set(
        UploadChunk.objects.filter(project=project, checksum__in=checksums).values_list(
            "checksum", flat=True
        )
    )
    return [checksum for checksum in checksums if checksum not in held]


def assemble(
    project: Project, checksum: str, checksums: list[str], at: datetime
) -> tuple[str, list[str], str]:
    """Join the chunks the client named and store what they spell.

    Returns the state the protocol defines, so the client can be told what is
    still missing rather than being handed a failure it cannot act on.
    """
    held = {
        chunk.checksum: chunk
        for chunk in UploadChunk.objects.filter(project=project, checksum__in=checksums)
    }
    missing = [checksum for checksum in checksums if checksum not in held]
    if missing:
        return STATE_MISSING, missing, ""

    total = sum(held[checksum].size for checksum in checksums)
    if total > MAX_REQUEST_SIZE:
        return STATE_ERROR, [], "the assembled bundle is too large"

    payload = _joined(held, checksums)
    whole = hashlib.sha1(payload, usedforsecurity=False).hexdigest()
    if whole != checksum:
        return STATE_ERROR, [], "the chunks do not spell the checksum they claim"

    try:
        stored = store_bundle(project, payload, at)
    except SourceMapError as error:
        return STATE_ERROR, [], str(error)
    if not stored:
        return STATE_ERROR, [], "no file in the bundle carried a debug id"

    UploadChunk.objects.filter(pk__in=[chunk.pk for chunk in held.values()]).delete()
    return STATE_OK, [], ""


def sweep_chunks(now: datetime) -> int:
    """Drop the rubble of an upload that was never assembled."""
    stale = UploadChunk.objects.filter(received_at__lt=now - CHUNK_TTL)
    removed = 0
    for chunk in stale:
        chunk.delete()
        removed += 1
    return removed


def _joined(held: dict[str, UploadChunk], checksums: list[str]) -> bytes:
    buffer = io.BytesIO()
    for checksum in checksums:
        chunk = held[checksum]
        with chunk.blob.open("rb") as handle:
            buffer.write(handle.read())
    return buffer.getvalue()


def _plain(raw: bytes) -> bytes:
    if not raw.startswith(GZIP_MAGIC):
        return raw
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            return compressed.read(CHUNK_SIZE + 1)
    except (EOFError, OSError):
        return raw


def store_bundle(project: Project, payload: bytes, at: datetime) -> list[Stored]:
    """Take an artifact bundle — a zip of minified files and their maps."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise SourceMapError(f"artifact bundle is not a zip: {error}") from error

    files = [entry for entry in archive.infolist() if not entry.is_dir()]
    if len(files) > MAX_BUNDLE_FILES:
        raise SourceMapError("artifact bundle has too many files")
    if sum(entry.file_size for entry in files) > MAX_EXTRACTED_SIZE:
        raise SourceMapError("artifact bundle expands past the size limit")

    manifest = _manifest(archive)
    stored: dict[str, Stored] = {}
    for entry_info in files:
        name = entry_info.filename
        try:
            body = archive.read(entry_info)
        except (RuntimeError, zipfile.BadZipFile) as error:
            raise SourceMapError(f"artifact bundle cannot be read: {error}") from error
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
    finally:
        row.blob.close()
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
    sweep_chunks(now)
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
                document = json.loads(archive.read(name))
            except (RuntimeError, ValueError, zipfile.BadZipFile):
                return {}
            if isinstance(document, dict):
                return document
            return {}
    return {}


def _debug_id(name: str, body: bytes, manifest: dict) -> str:
    files = manifest.get("files") or {}
    if not isinstance(files, dict):
        files = {}
    entry = files.get(name) or {}
    if not isinstance(entry, dict):
        entry = {}
    headers = entry.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    for key in ("debug-id", "debug_id", "debugId"):
        if headers.get(key):
            return _bounded_debug_id(headers[key])
    if name.endswith(MAP_SUFFIX):
        try:
            document = json.loads(body)
        except ValueError:
            return ""
        if not isinstance(document, dict):
            return ""
        return _bounded_debug_id(debug_id_of(document))
    return ""


def _bounded_debug_id(value) -> str:
    candidate = str(value).strip()
    if len(candidate) > MAX_DEBUG_ID_LENGTH:
        return ""
    return candidate


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
