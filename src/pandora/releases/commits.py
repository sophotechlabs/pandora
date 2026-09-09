from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pandora.core.models import Project
from pandora.releases.models import (
    CodeMapping,
    Commit,
    CommitFile,
    FileChange,
    Release,
    ReleaseCommit,
    Repository,
    RepositoryProvider,
)

KEY_MAX = 64
NAME_MAX = 200
EMAIL_MAX = 254
PATH_MAX = 500
MESSAGE_MAX = 10_000
MAX_COMMITS = 1000
MAX_FILES = 500
SUSPECT_WINDOW = timedelta(days=365)
SUSPECT_LIMIT = 3
FIXES = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+(?:#|[A-Za-z][\w-]*-)(\d+)\b",
    re.IGNORECASE,
)

SOURCE_TEMPLATES: dict[str, str] = {
    RepositoryProvider.GITHUB: "{url}/blob/{ref}/{path}",
    RepositoryProvider.GITLAB: "{url}/-/blob/{ref}/{path}",
    RepositoryProvider.BITBUCKET: "{url}/src/{ref}/{path}",
}
LINE_ANCHORS: dict[str, str] = {
    RepositoryProvider.GITHUB: "#L{line}",
    RepositoryProvider.GITLAB: "#L{line}",
    RepositoryProvider.BITBUCKET: "#lines-{line}",
}


class CommitError(ValueError):
    pass


@dataclass(frozen=True)
class Suspect:
    commit: Commit
    path: str
    frame: int


@dataclass
class Report:
    commits: int = 0
    repositories: list[str] = field(default_factory=list)
    resolved: list[int] = field(default_factory=list)


def set_commits(
    project: Project,
    release: Release,
    payload: Sequence[Any],
) -> Report:
    """Attach the commits CI says went into a release.

    The payload is the one `sentry-cli releases set-commits --local` sends, so
    an existing pipeline needs no change. Nothing here reaches a forge: the
    commit, its author and the files it touched all arrive in the request.
    """
    if len(payload) > MAX_COMMITS:
        raise CommitError(f"a release carries at most {MAX_COMMITS} commits")
    report = Report()
    seen: dict[str, Repository] = {}
    with transaction.atomic():
        for order, entry in enumerate(payload):
            commit = _store(project, entry, seen)
            ReleaseCommit.objects.update_or_create(
                release=release,
                commit=commit,
                defaults={"order": order},
            )
            report.commits += 1
    report.repositories = sorted(seen)
    return report


def _store(
    project: Project,
    entry: Any,
    seen: dict[str, Repository],
) -> Commit:
    if not isinstance(entry, Mapping):
        raise CommitError("every commit must be a JSON object")
    key = _text(entry.get("id"), "id", KEY_MAX)
    if not key:
        raise CommitError("every commit needs an id")
    name = _text(entry.get("repository"), "repository", NAME_MAX)
    if not name:
        raise CommitError(f"commit {key[:12]} names no repository")
    repository = seen.get(name)
    if repository is None:
        repository = ensure_repository(project, name)
        seen[name] = repository

    commit, _ = Commit.objects.update_or_create(
        repository=repository,
        key=key,
        defaults={
            "message": _text(entry.get("message"), "message", MESSAGE_MAX),
            "author_name": _text(entry.get("author_name"), "author_name", NAME_MAX),
            "author_email": _text(entry.get("author_email"), "author_email", EMAIL_MAX),
            "committed_at": _timestamp(entry.get("timestamp")),
        },
    )
    _store_files(commit, entry.get("patch_set"))
    return commit


def _store_files(commit: Commit, patch_set: Any) -> None:
    if patch_set is None:
        return
    if not isinstance(patch_set, list):
        raise CommitError("patch_set must be a list")
    if len(patch_set) > MAX_FILES:
        raise CommitError(f"a commit carries at most {MAX_FILES} changed files")
    rows = []
    paths = set()
    for entry in patch_set:
        if not isinstance(entry, Mapping):
            raise CommitError("every patch_set entry must be a JSON object")
        path = _text(entry.get("path"), "path", PATH_MAX).lstrip("/")
        if not path or path in paths:
            continue
        paths.add(path)
        rows.append(
            CommitFile(commit=commit, path=path, change=_change(entry.get("type")))
        )
    CommitFile.objects.filter(commit=commit).delete()
    CommitFile.objects.bulk_create(rows)


def _change(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if value in FileChange.values:
        return value
    return FileChange.MODIFIED


def ensure_repository(project: Project, name: str) -> Repository:
    repository, created = Repository.objects.get_or_create(
        project=project,
        name=name,
        defaults={"provider": _guess_provider(name)},
    )
    if created and not repository.url:
        repository.url = _guess_url(repository.provider, name)
        repository.save(update_fields=["url"])
    return repository


def _guess_provider(name: str) -> str:
    lowered = name.lower()
    if "gitlab" in lowered:
        return RepositoryProvider.GITLAB
    if "bitbucket" in lowered:
        return RepositoryProvider.BITBUCKET
    if lowered.count("/") == 1:
        return RepositoryProvider.GITHUB
    return RepositoryProvider.GIT


def _guess_url(provider: str, name: str) -> str:
    if provider == RepositoryProvider.GITHUB:
        return f"https://github.com/{name}"
    return ""


def _text(raw: Any, field_name: str, limit: int) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise CommitError(f"{field_name} must be a string")
    return raw.strip()[:limit]


def _timestamp(raw: Any) -> datetime:
    if isinstance(raw, str) and raw.strip():
        parsed = parse_datetime(raw.strip())
        if parsed is None:
            raise CommitError("timestamp is not an ISO 8601 timestamp")
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed)
        return parsed
    return timezone.now()


def mappings_for(project: Project) -> list[CodeMapping]:
    return list(
        CodeMapping.objects.filter(project=project, active=True).select_related(
            "repository"
        )
    )


def map_path(
    path: str, mappings: Sequence[CodeMapping]
) -> tuple[CodeMapping, str] | None:
    """Turn a stack-trace path into a path inside a repository.

    The longest matching stack root wins, so a specific mapping can sit under a
    catch-all one without the catch-all swallowing it.
    """
    cleaned = path.strip().lstrip("/")
    if not cleaned:
        return None
    ranked = sorted(mappings, key=lambda row: len(row.stack_root), reverse=True)
    for mapping in ranked:
        root = mapping.stack_root.strip().lstrip("/")
        if root and not cleaned.startswith(root):
            continue
        tail = cleaned[len(root) :].lstrip("/")
        source_root = mapping.source_root.strip().strip("/")
        if source_root:
            return mapping, f"{source_root}/{tail}"
        return mapping, tail
    return None


def source_url(
    mapping: CodeMapping,
    path: str,
    lineno: int | None = None,
    ref: str = "",
) -> str:
    repository = mapping.repository
    template = SOURCE_TEMPLATES.get(repository.provider, "")
    if not template or not repository.url:
        return ""
    url = template.format(
        url=repository.url.rstrip("/"),
        ref=quote(ref or mapping.default_branch, safe=""),
        path=quote(path, safe="/"),
    )
    if lineno is None:
        return url
    anchor = LINE_ANCHORS.get(repository.provider, "")
    if not anchor:
        return url
    return url + anchor.format(line=lineno)


def frames_from(payload: Any) -> list[Mapping[str, Any]]:
    """The stack of the newest exception, nearest the fault first.

    In-app frames only when there are any: a commit touching a vendored library
    is never the answer to why your own service started failing.
    """
    if not isinstance(payload, Mapping):
        return []
    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list):
        return []
    for entry in reversed(exceptions):
        if not isinstance(entry, Mapping):
            continue
        raw = entry.get("frames")
        if not isinstance(raw, list):
            continue
        frames = [frame for frame in raw if isinstance(frame, Mapping)]
        if not frames:
            continue
        in_app = [frame for frame in frames if frame.get("in_app") is True]
        return list(reversed(in_app or frames))
    return []


def suspects(
    project: Project,
    frames: Sequence[Mapping[str, Any]],
    first_seen: datetime,
    limit: int = SUSPECT_LIMIT,
) -> list[Suspect]:
    """The commits that last touched the code in the stack trace.

    Walked from the top of the stack down, because the frame nearest the fault
    is the one worth blaming first. Only commits already associated with a
    release of this project are considered, and only inside a window — a file
    nobody has touched in two years is not a suspect.
    """
    mappings = mappings_for(project)
    if not mappings:
        return []
    oldest = first_seen - SUSPECT_WINDOW
    found: list[Suspect] = []
    claimed: set[int] = set()
    for index, frame in enumerate(frames):
        mapped = map_path(_frame_path(frame), mappings)
        if mapped is None:
            continue
        _, path = mapped
        for commit in _commits_touching(project, path, oldest, first_seen):
            if commit.pk in claimed:
                continue
            claimed.add(commit.pk)
            found.append(Suspect(commit=commit, path=path, frame=index))
            break
        if len(found) >= limit:
            break
    return found


def _frame_path(frame: Mapping[str, Any]) -> str:
    for key in ("abs_path", "filename", "module"):
        value = frame.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _commits_touching(
    project: Project,
    path: str,
    oldest: datetime,
    newest: datetime,
) -> list[Commit]:
    return list(
        Commit.objects.filter(
            files__path=path,
            release_commits__release__project=project,
            committed_at__gte=oldest,
            committed_at__lte=newest,
        )
        .distinct()
        .order_by("-committed_at", "-pk")[:1]
    )


def issue_ids_fixed(message: str) -> list[int]:
    """Issue numbers a commit message claims to have closed."""
    return [int(found) for found in FIXES.findall(message or "")]
