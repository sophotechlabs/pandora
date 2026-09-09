from __future__ import annotations

import datetime
import hashlib
import hmac
import json
from http import HTTPStatus
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from pandora.core.models import IngestToken, TokenScope
from pandora.issues import actions as issue_actions
from pandora.issues import triage
from pandora.issues.models import Issue
from pandora.people import audit
from pandora.releases import commits as commit_service
from pandora.releases import service
from pandora.releases.models import Deploy, Release


class RequestError(ValueError):
    pass


@csrf_exempt
def releases(request: HttpRequest, organization: str) -> JsonResponse:
    """Create a release the way `sentry-cli releases new` does."""
    token, refused = _authorize(request, ("POST",))
    if token is None:
        return refused
    try:
        document = _document(request)
        version = _required_text(document, "version", 250)
        _validate_projects(document, token)
        payload = _commit_payload(document)
    except RequestError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)

    release = service.ensure_release(token.project, version, "", timezone.now())
    try:
        report = _apply_commits(token, release, payload)
    except commit_service.CommitError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
    return JsonResponse(_serialize_release(release, report), status=HTTPStatus.CREATED)


@csrf_exempt
def release_detail(
    request: HttpRequest,
    organization: str,
    version: str,
) -> JsonResponse:
    """Attach commits to a release, the way `sentry-cli releases set-commits` does."""
    token, refused = _authorize(request, ("PUT", "POST"))
    if token is None:
        return refused
    try:
        document = _document(request)
        payload = _commit_payload(document)
    except RequestError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)

    release = service.ensure_release(token.project, version, "", timezone.now())
    try:
        report = _apply_commits(token, release, payload)
    except commit_service.CommitError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
    return JsonResponse(_serialize_release(release, report))


def _authorize(
    request: HttpRequest, methods: tuple[str, ...]
) -> tuple[IngestToken | None, JsonResponse]:
    token = _token(request)
    if token is None:
        response = JsonResponse(
            {"detail": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED
        )
        response["WWW-Authenticate"] = "Bearer"
        return None, response
    if not token.has_scope(TokenScope.DEPLOY):
        return None, JsonResponse(
            {"detail": "token lacks the deploy capability"},
            status=HTTPStatus.FORBIDDEN,
        )
    if request.method not in methods:
        response = JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )
        response["Allow"] = ", ".join(methods)
        return None, response
    return token, JsonResponse({})


def _commit_payload(document: dict[str, Any]) -> list[Any]:
    raw = document.get("commits")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RequestError("commits must be a list")
    return raw


def _apply_commits(
    token: IngestToken,
    release: Release,
    payload: list[Any],
) -> commit_service.Report:
    if not payload:
        return commit_service.Report()
    report = commit_service.set_commits(token.project, release, payload)
    report.resolved = _resolve_from_messages(token, release, payload)
    audit.record(
        "",
        audit.DEPLOY,
        str(release),
        {"commits": report.commits, "resolved": report.resolved},
        project_ids=[token.project_id],
    )
    return report


def _resolve_from_messages(
    token: IngestToken,
    release: Release,
    payload: list[Any],
) -> list[int]:
    wanted: list[int] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        wanted.extend(commit_service.issue_ids_fixed(entry.get("message", "")))
    if not wanted:
        return []
    at = timezone.now()
    resolved = []
    rows = Issue.objects.filter(
        project=token.project,
        pk__in=set(wanted),
        triage_state__in=triage.OPEN_STATES,
    )
    for issue in rows:
        service.resolve_in(issue, release=release, actor="", at=at)
        issue_actions.apply_triage(issue, triage.RESOLVED, "", at)
        resolved.append(issue.pk)
    return sorted(resolved)


def _serialize_release(
    release: Release, report: commit_service.Report
) -> dict[str, Any]:
    return {
        "version": release.version,
        "dateCreated": _isoformat(release.first_seen),
        "commitCount": report.commits,
        "repositories": report.repositories,
        "resolvedIssues": report.resolved,
    }


@csrf_exempt
def create_deploy(
    request: HttpRequest,
    organization: str,
    version: str,
) -> JsonResponse:
    token, refused = _authorize(request, ("POST",))
    if token is None:
        return refused
    try:
        document = _document(request)
        environment = _required_text(document, "environment", 100)
        name = _optional_text(document, "name", 200)
        url = _optional_text(document, "url", 500)
        _validate_projects(document, token)
        raw_started = _optional_text(document, "dateStarted", 64)
        raw_finished = _optional_text(document, "dateFinished", 64)
        started = _timestamp(raw_started, "dateStarted")
        finished = _timestamp(raw_finished, "dateFinished")
    except RequestError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
    received = timezone.now()
    if finished is None:
        finished = received
    if started is None:
        started = finished
    if finished < started:
        return JsonResponse(
            {"detail": "dateFinished is before dateStarted"},
            status=HTTPStatus.BAD_REQUEST,
        )
    identity = _identity(
        token,
        version,
        environment=environment,
        name=name,
        url=url,
        started=raw_started,
        finished=raw_finished,
    )
    try:
        release = service.ensure_release(token.project, version, "", received)
        deploy, created = service.record_completed_deploy(
            token.project,
            release,
            identifier=identity,
            environment=environment,
            started_at=started,
            finished_at=finished,
            name=name,
            url=url,
        )
    except service.DeployConflict as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)
    if created:
        audit.record(
            "",
            audit.DEPLOY,
            str(release),
            {"environment": environment, "state": deploy.state},
            project_ids=[token.project_id],
        )
        service.resolve_on_deploy(token.project, release, environment, finished)
    return JsonResponse(_serialize(deploy), status=HTTPStatus.CREATED)


def _token(request: HttpRequest) -> IngestToken | None:
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    presented = presented.strip()
    if not presented:
        return None
    for candidate in IngestToken.objects.select_related("project").filter(active=True):
        if hmac.compare_digest(candidate.token, presented):
            return candidate
    return None


def _document(request: HttpRequest) -> dict[str, Any]:
    body = request.body
    if not body:
        body = b"{}"
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, ValueError) as error:
        raise RequestError("body is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise RequestError("body is not a JSON object")
    return parsed


def _required_text(document: dict[str, Any], key: str, limit: int) -> str:
    value = _optional_text(document, key, limit)
    if not value:
        raise RequestError(f"{key} is required")
    return value


def _optional_text(document: dict[str, Any], key: str, limit: int) -> str:
    raw = document.get(key, "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise RequestError(f"{key} must be a string")
    value = raw.strip()
    if len(value) > limit:
        raise RequestError(f"{key} is too long")
    return value


def _validate_projects(document: dict[str, Any], token: IngestToken) -> None:
    projects = document.get("projects")
    if projects is None:
        return
    if not isinstance(projects, list):
        raise RequestError("projects must be a list")
    if projects != [token.project.slug]:
        raise RequestError("projects must contain only the token project")


def _timestamp(raw: str, field: str) -> datetime.datetime | None:
    if not raw:
        return None
    try:
        parsed = parse_datetime(raw)
    except ValueError as error:
        raise RequestError(f"{field} is not an ISO 8601 timestamp") from error
    if parsed is None:
        raise RequestError(f"{field} is not an ISO 8601 timestamp")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=datetime.UTC)
    return parsed


def _identity(
    token: IngestToken,
    version: str,
    *,
    environment: str,
    name: str,
    url: str,
    started: str,
    finished: str,
) -> str:
    body = json.dumps(
        {
            "project": token.project_id,
            "version": version,
            "environment": environment,
            "name": name,
            "url": url,
            "dateStarted": started,
            "dateFinished": finished,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sentry:{hashlib.sha256(body).hexdigest()}"


def _serialize(deploy: Deploy) -> dict[str, Any]:
    name: str | None = deploy.name
    if not name:
        name = None
    url: str | None = deploy.url
    if not url:
        url = None
    return {
        "id": str(deploy.pk),
        "name": name,
        "environment": deploy.environment,
        "url": url,
        "dateStarted": _isoformat(deploy.started_at),
        "dateFinished": _isoformat(deploy.finished_at),
    }


def _isoformat(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
