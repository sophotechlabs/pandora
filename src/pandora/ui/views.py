from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login
from django.core.paginator import Page, Paginator
from django.db.models import Count, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from pandora.am import client as am_client
from pandora.core.models import IngestToken
from pandora.events.store import get_store
from pandora.events.types import Event
from pandora.ingest import replay as ingest_replay
from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.issues import actions, components, sparkline, triage
from pandora.issues import detail as detail_module
from pandora.issues.models import HourlyStat, Issue, SourceState, TriageState
from pandora.people import access, audit, oidc, ownership
from pandora.people.models import AuditEntry
from pandora.ui import markdown, presenters, query
from pandora.web import dashboard

LOGIN_URL = "ui:login"
OIDC_VIA = "oidc"
PAGE_SIZE = 25
OVERVIEW_ROWS = 8
FAILURE_ROWS = 20
EVENT_ROWS = 25
REPLAY_LIMIT = 200
SILENCE_PREFIX = "silence:"
SNOOZE_PREFIX = "snooze:"
TRIAGE_PERMISSION = "issues.change_issue"
REPLAY_PERMISSION = "ingest.change_rawenvelope"

TABS = ("occurrences", "episodes", "tags", "activity")
TAB_LABELS = (
    ("occurrences", "Occurrences"),
    ("episodes", "Episodes"),
    ("tags", "Tags"),
    ("activity", "Activity"),
)

SEGMENTS = (
    ("unresolved", "Unresolved", "is:unresolved"),
    ("new", "New", "is:new"),
    ("acknowledged", "Acknowledged", "is:acknowledged"),
    ("resolved", "Resolved", "is:resolved"),
    ("ignored", "Ignored", "is:ignored"),
    ("everything", "All", ""),
)

SORTS = (
    ("last_seen", "Last seen", ("-last_seen", "-id")),
    ("first_seen", "First seen", ("-first_seen", "-id")),
    ("events", "Events", ("-event_count", "-id")),
)

TRIAGE_ACTIONS = {
    "acknowledge": triage.ACKNOWLEDGED,
    "resolve": triage.RESOLVED,
    "ignore": triage.IGNORED,
}

SILENCE_LABELS = (
    ("1h", "1 hour"),
    ("4h", "4 hours"),
    ("1d", "1 day"),
)

SNOOZE_LABELS = (
    ("1h", "1 hour"),
    ("4h", "4 hours"),
    ("1d", "1 day"),
    ("1w", "1 week"),
    ("100", "100 more"),
    ("500", "500 more"),
    ("1000", "1000 more"),
)


@dataclass(frozen=True)
class Sort:
    key: str
    label: str
    ordering: tuple[str, ...]


@dataclass(frozen=True)
class Segment:
    key: str
    label: str
    query: str
    count: int
    active: bool


@dataclass(frozen=True)
class EventPage:
    rows: tuple[presenters.EventRow, ...]
    next_cursor: str | None
    supported: bool


def _scoped(queryset: QuerySet[Issue], request: HttpRequest) -> QuerySet[Issue]:
    projects = access.projects_for(request.user)
    if projects is None:
        return queryset
    return queryset.filter(project_id__in=projects)


def sso_start(request: HttpRequest) -> HttpResponse:
    if not oidc.enabled():
        raise Http404("single sign-on is not configured")
    redirect_uri = request.build_absolute_uri(reverse("ui:sso-callback"))
    return oidc.client().authorize_redirect(request, redirect_uri)


def sso_callback(request: HttpRequest) -> HttpResponse:
    if not oidc.enabled():
        raise Http404("single sign-on is not configured")
    try:
        token = oidc.client().authorize_access_token(request)
    except Exception as error:
        messages.error(request, f"Single sign-on failed — {error}")
        return redirect("ui:login")

    claims = token.get("userinfo") or {}
    try:
        user = oidc.provision(claims)
    except oidc.OidcError as error:
        messages.error(request, f"Single sign-on failed — {error}")
        return redirect("ui:login")

    request.pandora_login_via = OIDC_VIA
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("ui:stream")


@staff_member_required(login_url=LOGIN_URL)
def stream(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    raw = request.GET.get("q")
    if raw is None:
        raw = query.DEFAULT_QUERY
    parsed = query.parse(raw)
    scoped = _scoped(presenters.stream_queryset(now), request)
    found, rejected = query.filter_issues(
        scoped, parsed, now, request.user.get_username()
    )
    sort = _sort(request.GET.get("sort", ""))
    page = _page(request, found.order_by(*sort.ordering).distinct())

    context = {
        "nav": "issues",
        "raw_query": raw,
        "ignored": parsed.unknown + tuple(rejected),
        "rows": [presenters.row(issue, now) for issue in page.object_list],
        "page": page,
        "total": page.paginator.count,
        "segments": _segments(raw),
        "sorts": [_sort(key) for key, _, _ in SORTS],
        "sort": sort,
        "page_query": urlencode({"q": raw, "sort": sort.key}),
        "windows": SILENCE_LABELS,
        "snoozes": SNOOZE_LABELS,
        "spark_width": presenters.SPARK_WIDTH,
        "spark_height": presenters.SPARK_HEIGHT,
        "can_triage": access.may(request.user, TRIAGE_PERMISSION),
    }
    if request.GET.get("partial"):
        return render(request, "ui/partials/stream_rows.html", context)
    return render(request, "ui/stream.html", context)


@staff_member_required(login_url=LOGIN_URL)
def issue_page(request: HttpRequest, issue_id: int, tab: str = TABS[0]) -> HttpResponse:
    if tab not in TABS:
        raise Http404("unknown tab")

    now = timezone.now()
    issue = get_object_or_404(
        _scoped(presenters.stream_queryset(now), request),
        pk=issue_id,
    )
    context = _issue_context(request, issue, tab, now)
    if request.GET.get("format") == "md":
        return _markdown_response(issue, context["detail"])
    if request.GET.get("partial"):
        return render(request, f"ui/partials/tab_{tab}.html", context)
    context["latest"] = _latest(issue)
    return render(request, "ui/issue.html", context)


def _markdown_response(issue: Issue, detail: detail_module.Detail) -> HttpResponse:
    body = markdown.render(issue, detail, _recent_events(issue))
    response = HttpResponse(body, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="issue-{issue.pk}.md"'
    return response


def _recent_events(issue: Issue) -> list[Event]:
    try:
        return get_store().fetch(
            issue.project_id,
            issue_id=issue.pk,
            limit=markdown.EVENT_LIMIT,
        )
    except NotImplementedError:
        return []


@staff_member_required(login_url=LOGIN_URL)
def overview(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    projects = access.projects_for(request.user)
    firing = (
        _scoped(presenters.stream_queryset(now), request)
        .filter(source_state=SourceState.FIRING)
        .order_by("-last_seen")[:OVERVIEW_ROWS]
    )
    newest = _scoped(presenters.stream_queryset(now), request).order_by("-first_seen")[
        :OVERVIEW_ROWS
    ]
    context = {
        "nav": "overview",
        "kpis": dashboard.kpis(now, projects),
        "firing": [presenters.row(issue, now) for issue in firing],
        "newest": [presenters.row(issue, now) for issue in newest],
        "spark_width": presenters.SPARK_WIDTH,
        "spark_height": presenters.SPARK_HEIGHT,
    }
    return render(request, "ui/overview.html", context)


@staff_member_required(login_url=LOGIN_URL)
def history(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    entries = AuditEntry.objects.all()
    action = request.GET.get("action", "").strip()
    if action:
        entries = entries.filter(action=action)
    actor = request.GET.get("actor", "").strip()
    if actor:
        entries = entries.filter(actor=actor)
    page = _page(request, entries)
    context = {
        "nav": "history",
        "rows": [
            (entry, components.format_relative(entry.at, now))
            for entry in page.object_list
        ],
        "page": page,
        "total": page.paginator.count,
        "actions": sorted(
            AuditEntry.objects.values_list("action", flat=True).distinct()
        ),
        "action": action,
        "actor": actor,
        "page_query": urlencode({"action": action, "actor": actor}),
    }
    return render(request, "ui/history.html", context)


@staff_member_required(login_url=LOGIN_URL)
def ingest(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    counts = RawEnvelope.objects.aggregate(
        pending=Count("pk", filter=Q(state=EnvelopeState.PENDING)),
        failed=Count("pk", filter=Q(state=EnvelopeState.FAILED)),
        done=Count("pk", filter=Q(state=EnvelopeState.DONE)),
    )
    failures = (
        RawEnvelope.objects.filter(state=EnvelopeState.FAILED)
        .select_related("project")
        .order_by("-received_at")[:FAILURE_ROWS]
    )
    context = {
        "nav": "ingest",
        "counts": counts,
        "backlog": counts["pending"] + counts["failed"],
        "last_accepted": _last_accepted(),
        "failures": [
            (envelope, components.format_relative(envelope.received_at, now))
            for envelope in failures
        ],
        "tokens": IngestToken.objects.select_related("project").order_by(
            "project__slug", "name"
        ),
    }
    return render(request, "ui/ingest.html", context)


@staff_member_required(login_url=LOGIN_URL)
@require_POST
def issue_actions(request: HttpRequest) -> HttpResponse:
    if not access.may(request.user, TRIAGE_PERMISSION):
        return HttpResponseForbidden("triage requires the issue change permission")

    issues = list(Issue.objects.filter(pk__in=request.POST.getlist("issue")))
    if not issues:
        messages.warning(request, "No issue was selected")
        return redirect(_next_url(request))

    action = request.POST.get("action", "")
    if action in TRIAGE_ACTIONS:
        _run_triage(request, issues, TRIAGE_ACTIONS[action])
    elif action.startswith(SNOOZE_PREFIX):
        _run_snooze(request, issues, action[len(SNOOZE_PREFIX) :])
    elif action.startswith(SILENCE_PREFIX):
        _run_silence(request, issues, action[len(SILENCE_PREFIX) :])
    else:
        messages.error(request, f"{action or 'that action'} is not an action")
    return redirect(_next_url(request))


@staff_member_required(login_url=LOGIN_URL)
@require_POST
def delete_occurrence(
    request: HttpRequest, issue_id: int, event_id: str
) -> HttpResponse:
    if not access.may(request.user, TRIAGE_PERMISSION):
        return HttpResponseForbidden(
            "deleting an occurrence requires the issue change permission"
        )

    issue = get_object_or_404(Issue, pk=issue_id)
    store = get_store()
    try:
        found = [
            event
            for event in store.fetch(
                issue.project_id, issue_id=issue.pk, limit=EVENT_ROWS
            )
            if event.id == event_id
        ]
    except NotImplementedError:
        found = []
    if not found:
        messages.warning(request, "That occurrence is not stored any more")
        return redirect(_next_url(request))

    removed = store.delete(issue.project_id, found)
    audit.from_request(
        request, audit.DELETE_OCCURRENCE, str(issue.pk), {"event": event_id}
    )
    messages.success(request, f"Deleted {removed} occurrence(s)")
    return redirect(_next_url(request))


@staff_member_required(login_url=LOGIN_URL)
@require_POST
def replay_envelopes(request: HttpRequest) -> HttpResponse:
    if not access.may(request.user, REPLAY_PERMISSION):
        return HttpResponseForbidden("replay requires the envelope change permission")

    result = ingest_replay.replay(
        ingest_replay.STATE_SETS["all"],
        REPLAY_LIMIT,
    )
    audit.from_request(
        request,
        audit.REPLAY,
        "",
        {"attempted": result.attempted, "done": result.done, "failed": result.failed},
    )
    messages.success(
        request,
        f"Replayed {result.attempted} envelope(s):"
        f" {result.done} applied, {result.failed} still failing",
    )
    return redirect("ui:ingest")


def _issue_context(
    request: HttpRequest, issue: Issue, tab: str, now: datetime
) -> dict[str, Any]:
    stats = HourlyStat.objects.filter(
        issue=issue,
        hour__gte=sparkline.start_of(now, presenters.CHART_WINDOW),
    ).order_by("hour")
    context: dict[str, Any] = {
        "nav": "issues",
        "issue": issue,
        "row": presenters.row(issue, now),
        "detail": detail_module.build(issue),
        "chart": presenters.chart(stats, now),
        "chart_width": presenters.CHART_WIDTH,
        "chart_height": presenters.CHART_HEIGHT,
        "tab": tab,
        "tabs": TAB_LABELS,
        "windows": SILENCE_LABELS,
        "snoozes": SNOOZE_LABELS,
        "next_url": request.get_full_path(),
        "can_triage": access.may(request.user, TRIAGE_PERMISSION),
    }
    context.update(_owner_context(issue))
    if tab == "occurrences":
        context["events"] = _events(issue, request.GET.get("cursor", ""))
    return context


def _owner_context(issue: Issue) -> dict[str, Any]:
    owner = presenters.owner_of(issue)
    if owner:
        return {"owner": owner, "owner_suggestions": []}
    if not ownership.rules_for(issue):
        return {"owner": "", "owner_suggestions": []}
    events = _recent_events(issue)
    newest = None
    if events:
        newest = events[0]
    return {
        "owner": "",
        "owner_suggestions": ownership.suggestions(issue, newest),
    }


def _latest(issue: Issue) -> presenters.EventRow | None:
    try:
        found = get_store().fetch(issue.project_id, issue_id=issue.pk, limit=1)
    except NotImplementedError:
        return None
    if not found:
        return None
    return presenters.event_row(found[0])


def _events(issue: Issue, cursor: str) -> EventPage:
    try:
        found = get_store().fetch(
            issue.project_id,
            issue_id=issue.pk,
            before=cursor or None,
            limit=EVENT_ROWS + 1,
        )
    except NotImplementedError:
        return EventPage(rows=(), next_cursor=None, supported=False)

    next_cursor = None
    if len(found) > EVENT_ROWS:
        found = found[:EVENT_ROWS]
        next_cursor = found[-1].id
    return EventPage(
        rows=tuple(presenters.event_row(event) for event in found),
        next_cursor=next_cursor,
        supported=True,
    )


def _run_triage(request: HttpRequest, issues: list[Issue], target_state: str) -> None:
    report = actions.retriage(
        issues,
        target_state,
        request.user.get_username(),
        timezone.now(),
    )
    audit.from_request(
        request,
        audit.TRIAGE,
        ",".join(str(issue.pk) for issue in issues),
        {"state": target_state, "changed": report.changed},
    )
    messages.success(
        request,
        f"{actions.TRIAGE_VERBS[target_state]} {report.changed} issue(s),"
        f" {report.unchanged} unchanged",
    )


def _run_snooze(request: HttpRequest, issues: list[Issue], spec: str) -> None:
    report = actions.apply_snooze(
        issues, spec, request.user.get_username(), timezone.now()
    )
    if report.errors:
        for error in report.errors:
            messages.error(request, error)
        return
    audit.from_request(
        request,
        audit.SNOOZE,
        ",".join(str(issue.pk) for issue in issues),
        {"spec": spec},
    )
    messages.success(request, f"Snoozed {report.snoozed} issue(s)")


def _run_silence(request: HttpRequest, issues: list[Issue], window: str) -> None:
    duration = actions.SILENCE_WINDOWS.get(window)
    if duration is None:
        messages.error(request, f"{window} is not a silence window")
        return
    try:
        client = am_client.from_settings()
    except am_client.AlertmanagerError as error:
        messages.error(request, f"No silence sent — {error}")
        return

    report = actions.silence(
        issues,
        duration,
        request.user.get_username(),
        client,
    )
    for note in report.errors:
        messages.error(request, note)
    if report.silenced:
        audit.from_request(
            request,
            audit.SILENCE,
            ",".join(str(issue.pk) for issue in issues),
            {"window": window, "silenced": report.silenced},
        )
        messages.success(
            request,
            f"Silenced {report.silenced} issue(s) in Alertmanager for {window}",
        )


def _last_accepted() -> datetime | None:
    return (
        RawEnvelope.objects.filter(state=EnvelopeState.DONE)
        .order_by("-received_at")
        .values_list("received_at", flat=True)
        .first()
    )


def _sort(key: str) -> Sort:
    for candidate, label, ordering in SORTS:
        if candidate == key:
            return Sort(key=candidate, label=label, ordering=ordering)
    first, label, ordering = SORTS[0]
    return Sort(key=first, label=label, ordering=ordering)


def _page(request: HttpRequest, queryset: QuerySet[Issue]) -> Page:
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))


def _segments(raw: str) -> list[Segment]:
    counts = Issue.objects.aggregate(
        unresolved=Count("pk", filter=Q(triage_state__in=triage.OPEN_STATES)),
        new=Count("pk", filter=Q(triage_state=TriageState.NEW)),
        acknowledged=Count("pk", filter=Q(triage_state=TriageState.ACKNOWLEDGED)),
        resolved=Count("pk", filter=Q(triage_state=TriageState.RESOLVED)),
        ignored=Count("pk", filter=Q(triage_state=TriageState.IGNORED)),
        everything=Count("pk"),
    )
    current = " ".join(raw.split())
    return [
        Segment(
            key=key,
            label=label,
            query=segment_query,
            count=counts[key],
            active=current == segment_query,
        )
        for key, label, segment_query in SEGMENTS
    ]


def _next_url(request: HttpRequest) -> str:
    target = request.POST.get("next", "")
    allowed = url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    if allowed:
        return target
    return reverse("ui:stream")
