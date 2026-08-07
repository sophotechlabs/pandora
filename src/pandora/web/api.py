from __future__ import annotations

import base64
import dataclasses
import datetime
import functools
import hmac
from collections.abc import Callable, Sequence
from http import HTTPStatus
from typing import Any

from django.db.models import Q, QuerySet
from django.http import HttpRequest, JsonResponse, QueryDict
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from pandora.core.models import IngestToken, TokenScope
from pandora.events.store import get_store
from pandora.events.types import Event
from pandora.issues.models import Episode, Issue, SourceState, TagStat, TriageState

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DETAIL_EPISODE_LIMIT = 20
DETAIL_TAG_LIMIT = 500
SEARCH_WINDOW = datetime.timedelta(days=7)
SAFE_METHODS = ("GET", "HEAD")


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def error_response(status: HTTPStatus, detail: str) -> JsonResponse:
    response = JsonResponse({"detail": detail}, status=status)
    if status == HTTPStatus.UNAUTHORIZED:
        response["WWW-Authenticate"] = "Bearer"
    return response


def authenticate(request: HttpRequest) -> IngestToken:
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        raise ApiError(HTTPStatus.UNAUTHORIZED, "bearer token required")
    presented = presented.strip()
    if not presented:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "bearer token required")
    offered = presented.encode()
    for candidate in IngestToken.objects.select_related("project").filter(active=True):
        if hmac.compare_digest(candidate.token.encode(), offered):
            return require_read_scope(candidate)
    raise ApiError(HTTPStatus.UNAUTHORIZED, "unknown token")


def require_read_scope(token: IngestToken) -> IngestToken:
    if token.scope != TokenScope.READ:
        raise ApiError(
            HTTPStatus.FORBIDDEN,
            f"token scope {token.scope!r} cannot read",
        )
    return token


def api_view(
    handler: Callable[..., dict[str, Any]],
) -> Callable[..., JsonResponse]:
    @functools.wraps(handler)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        if request.method not in SAFE_METHODS:
            response = error_response(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "read-only endpoint",
            )
            response["Allow"] = ", ".join(SAFE_METHODS)
            return response
        try:
            token = authenticate(request)
            payload = handler(request, token, *args, **kwargs)
        except ApiError as error:
            return error_response(error.status, error.detail)
        return JsonResponse(payload)

    return csrf_exempt(wrapper)


def encode_cursor(last_seen: datetime.datetime, issue_id: int) -> str:
    raw = f"{last_seen.astimezone(datetime.UTC).isoformat()}|{issue_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime.datetime, int]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, "cursor is not readable") from error
    stamp, separator, issue_id = raw.partition("|")
    if not separator:
        raise ApiError(HTTPStatus.BAD_REQUEST, "cursor is not readable")
    if not issue_id.isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, "cursor is not readable")
    parsed = parse_timestamp(stamp, "cursor is not readable")
    return parsed, int(issue_id)


def parse_timestamp(raw: str, detail: str) -> datetime.datetime:
    try:
        parsed = parse_datetime(raw)
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, detail) from error
    if parsed is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, detail)
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, datetime.UTC)
    return parsed


def parse_limit(params: QueryDict) -> int:
    raw = params.get("limit", "").strip()
    if not raw:
        return DEFAULT_LIMIT
    if not raw.isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, "limit must be a positive integer")
    limit = int(raw)
    if limit < 1:
        raise ApiError(HTTPStatus.BAD_REQUEST, "limit must be a positive integer")
    return min(limit, MAX_LIMIT)


def parse_states(params: QueryDict, name: str, valid: Sequence[str]) -> list[str]:
    values = [value.strip() for value in params.getlist(name) if value.strip()]
    unknown = sorted(set(values) - set(valid))
    if unknown:
        offered = ", ".join(repr(value) for value in unknown)
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"unknown {name} {offered}; valid: {', '.join(valid)}",
        )
    return values


def parse_cursor_param(params: QueryDict) -> str | None:
    raw = params.get("cursor", "").strip()
    if not raw:
        return None
    return raw


def parse_episode(params: QueryDict) -> str | None:
    raw = params.get("episode", "").strip()
    if not raw:
        return None
    return raw


def parse_tags(params: QueryDict) -> dict[str, str]:
    tags = {}
    for raw in params.getlist("tag"):
        key, separator, value = raw.partition(":")
        if not separator:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                f"tag {raw!r} is not in key:value form",
            )
        tags[key.strip()] = value.strip()
    return tags


def issue_queryset(token: IngestToken, params: QueryDict) -> QuerySet[Issue]:
    queryset = Issue.objects.select_related("project").filter(project=token.project)
    slug = params.get("project", "").strip()
    if slug:
        queryset = queryset.filter(project__slug=slug)
    environment = params.get("environment", "").strip()
    if environment:
        queryset = queryset.filter(environment=environment)
    triage_states = parse_states(params, "triage_state", TriageState.values)
    if triage_states:
        queryset = queryset.filter(triage_state__in=triage_states)
    source_states = parse_states(params, "source_state", SourceState.values)
    if source_states:
        queryset = queryset.filter(source_state__in=source_states)
    since = params.get("since", "").strip()
    if since:
        queryset = queryset.filter(
            last_seen__gte=parse_timestamp(since, "since must be an ISO 8601 timestamp")
        )
    return queryset.order_by("-last_seen", "-id")


def get_issue(token: IngestToken, issue_id: int) -> Issue:
    issue = (
        Issue.objects.select_related("project")
        .filter(project=token.project, pk=issue_id)
        .first()
    )
    if issue is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "issue not found")
    return issue


def isoformat(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")


def serialize_issue(issue: Issue) -> dict[str, Any]:
    return {
        "id": issue.pk,
        "project": issue.project.slug,
        "fingerprint_hash": issue.fingerprint_hash,
        "title": issue.title,
        "culprit": issue.culprit,
        "level": issue.level,
        "environment": issue.environment,
        "source_state": issue.source_state,
        "triage_state": issue.triage_state,
        "event_count": issue.event_count,
        "open_episode_count": issue.open_episode_count,
        "grouping_labels": issue.grouping_labels,
        "first_seen": isoformat(issue.first_seen),
        "last_seen": isoformat(issue.last_seen),
        "last_resolved_at": isoformat(issue.last_resolved_at),
    }


def serialize_episode(episode: Episode) -> dict[str, Any]:
    return {
        "id": episode.pk,
        "am_fingerprint": episode.am_fingerprint,
        "labels": episode.labels,
        "environment": episode.environment,
        "starts_at": isoformat(episode.starts_at),
        "ends_at": isoformat(episode.ends_at),
        "delivery_count": episode.delivery_count,
        "last_delivery_at": isoformat(episode.last_delivery_at),
    }


def serialize_tag_stat(stat: TagStat) -> dict[str, Any]:
    return {"key": stat.key, "value": stat.value, "count": stat.count}


def serialize_event(event: Event) -> dict[str, Any]:
    payload = dataclasses.asdict(event)
    payload["timestamp"] = isoformat(event.timestamp)
    return payload


@api_view
def issues(request: HttpRequest, token: IngestToken) -> dict[str, Any]:
    limit = parse_limit(request.GET)
    queryset = issue_queryset(token, request.GET)
    cursor = parse_cursor_param(request.GET)
    if cursor is not None:
        last_seen, issue_id = decode_cursor(cursor)
        queryset = queryset.filter(
            Q(last_seen__lt=last_seen) | Q(last_seen=last_seen, id__lt=issue_id)
        )
    page = list(queryset[: limit + 1])
    next_cursor = None
    if len(page) > limit:
        page = page[:limit]
        next_cursor = encode_cursor(page[-1].last_seen, page[-1].pk)
    return {
        "results": [serialize_issue(issue) for issue in page],
        "next_cursor": next_cursor,
    }


@api_view
def issue_detail(
    request: HttpRequest,
    token: IngestToken,
    issue_id: int,
) -> dict[str, Any]:
    issue = get_issue(token, issue_id)
    episodes = Episode.objects.filter(issue=issue).order_by("-starts_at", "-id")
    tag_stats = TagStat.objects.filter(issue=issue).order_by("key", "-count", "value")
    payload = serialize_issue(issue)
    payload["fingerprint"] = issue.fingerprint
    payload["episodes"] = [
        serialize_episode(episode) for episode in episodes[:DETAIL_EPISODE_LIMIT]
    ]
    payload["tag_stats"] = [
        serialize_tag_stat(stat) for stat in tag_stats[:DETAIL_TAG_LIMIT]
    ]
    return payload


@api_view
def issue_events(
    request: HttpRequest,
    token: IngestToken,
    issue_id: int,
) -> dict[str, Any]:
    issue = get_issue(token, issue_id)
    limit = parse_limit(request.GET)
    cursor = parse_cursor_param(request.GET)
    episode_id = parse_episode(request.GET)
    try:
        found = get_store().fetch(
            issue.project_id,
            issue_id=issue.pk,
            episode_id=episode_id,
            before=cursor,
            limit=limit + 1,
        )
    except NotImplementedError as error:
        raise ApiError(
            HTTPStatus.NOT_IMPLEMENTED,
            "event store is not implemented for this database yet",
        ) from error
    next_cursor = None
    if len(found) > limit:
        found = found[:limit]
        next_cursor = found[-1].id
    return {
        "results": [serialize_event(event) for event in found],
        "next_cursor": next_cursor,
    }


@api_view
def events_search(request: HttpRequest, token: IngestToken) -> dict[str, Any]:
    tags = parse_tags(request.GET)
    until = timezone.now()
    since = until - SEARCH_WINDOW
    raw_since = request.GET.get("since", "").strip()
    if raw_since:
        since = parse_timestamp(raw_since, "since is not an ISO 8601 timestamp")
    raw_until = request.GET.get("until", "").strip()
    if raw_until:
        until = parse_timestamp(raw_until, "until is not an ISO 8601 timestamp")
    if since > until:
        raise ApiError(HTTPStatus.BAD_REQUEST, "since is after until")

    limit = parse_limit(request.GET)
    try:
        found = get_store().search(token.project_id, tags, since, until, limit)
    except NotImplementedError as error:
        raise ApiError(
            HTTPStatus.NOT_IMPLEMENTED,
            "event store is not implemented for this database yet",
        ) from error
    return {"results": [serialize_event(event) for event in found]}


urlpatterns = [
    path("issues", issues, name="api-v1-issues"),
    path("issues/<int:issue_id>", issue_detail, name="api-v1-issue"),
    path("issues/<int:issue_id>/events", issue_events, name="api-v1-issue-events"),
    path("events", events_search, name="api-v1-events"),
]
