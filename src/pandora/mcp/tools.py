from __future__ import annotations

from typing import Any

from django.utils import timezone

from pandora.core.models import IngestToken, TokenScope
from pandora.events.store import get_store
from pandora.issues import detail as issue_detail
from pandora.issues.models import Issue
from pandora.ui import markdown, query
from pandora.web import api

ISSUE_LIMIT = 25
ISSUE_LIMIT_MAX = 100
EVENT_LIMIT = 10
EVENT_LIMIT_MAX = 50


class ToolError(RuntimeError):
    pass


def resolve_token(value: str) -> IngestToken:
    token = (
        IngestToken.objects.select_related("project")
        .filter(token=value, active=True, scope_grants__scope=TokenScope.READ)
        .first()
    )
    if token is None:
        raise ToolError("no active read-scoped ingest token matches PANDORA_MCP_TOKEN")
    return token


def _bounded(value: int | None, default: int, cap: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), cap))


def _issue(token: IngestToken, issue_id: int) -> Issue:
    issue = (
        Issue.objects.select_related("project")
        .filter(project=token.project, pk=issue_id)
        .first()
    )
    if issue is None:
        raise ToolError(f"issue {issue_id} is not in project {token.project.slug}")
    return issue


def search_issues(
    token: IngestToken, search: str = "", limit: int | None = None
) -> dict[str, Any]:
    now = timezone.now()
    parsed = query.parse(search or query.DEFAULT_QUERY)
    queryset = Issue.objects.select_related("project").filter(project=token.project)
    found, rejected = query.filter_issues(queryset, parsed, now)
    rows = list(
        found.order_by("-last_seen")[: _bounded(limit, ISSUE_LIMIT, ISSUE_LIMIT_MAX)]
    )
    return {
        "query": search or query.DEFAULT_QUERY,
        "ignored_terms": list(parsed.unknown) + rejected,
        "results": [api.serialize_issue(issue) for issue in rows],
    }


def get_issue(token: IngestToken, issue_id: int) -> dict[str, Any]:
    issue = _issue(token, issue_id)
    payload = api.serialize_issue(issue)
    payload["fingerprint"] = issue.fingerprint
    payload["episodes"] = [
        api.serialize_episode(episode)
        for episode in issue.episodes.all()[: api.DETAIL_EPISODE_LIMIT]
    ]
    payload["tag_stats"] = [
        api.serialize_tag_stat(stat)
        for stat in api.tag_page(
            issue.tag_stats.all().order_by("key", "-count", "value")
        )
    ]
    return payload


def get_issue_events(
    token: IngestToken, issue_id: int, limit: int | None = None
) -> dict[str, Any]:
    issue = _issue(token, issue_id)
    try:
        events = get_store().fetch(
            issue.project_id,
            issue_id=issue.pk,
            limit=_bounded(limit, EVENT_LIMIT, EVENT_LIMIT_MAX),
        )
    except NotImplementedError:
        return {"supported": False, "results": []}
    return {
        "supported": True,
        "results": api.serialize_events(token, events),
    }


def issue_as_markdown(token: IngestToken, issue_id: int) -> str:
    issue = _issue(token, issue_id)
    detail = issue_detail.build(issue)
    try:
        events = get_store().fetch(
            issue.project_id, issue_id=issue.pk, limit=markdown.EVENT_LIMIT
        )
    except NotImplementedError:
        events = []
    return markdown.render(issue, detail, events)
