from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F
from prometheus_client import Counter

from pandora.core.models import Project, TokenSource
from pandora.events.store import EventStore, get_store
from pandora.events.types import Event
from pandora.ingest.models import EnvelopeState, ProcessedEvent, RawEnvelope
from pandora.ingest.translators import am
from pandora.ingest.translators import envelope as envelope_translator
from pandora.issues import (
    aggregates,
    environments,
    hooks,
    lifecycle,
    merge,
    search,
)
from pandora.issues.models import Episode, Issue, IssueActivity, SourceState
from pandora.releases import service as releases

ENVELOPES = Counter(
    "pandora_ingest_envelopes_total",
    "Envelopes the consumer finished, by source and final state",
    ["source", "state"],
)
OCCURRENCES = Counter(
    "pandora_ingest_occurrences_total",
    "Occurrences applied to issues, by source and episode outcome",
    ["source", "outcome"],
)
ERROR_MAX = 2000

log = logging.getLogger(__name__)
EVENT_SOURCES = (TokenSource.SDK, TokenSource.LOG, TokenSource.OTLP)


def process_envelope(envelope_id: int, *, store: EventStore | None = None) -> None:
    envelope = (
        RawEnvelope.objects.select_related("project").filter(pk=envelope_id).first()
    )
    if envelope is None:
        log.warning("envelope %s is gone — nothing to process", envelope_id)
        return
    if envelope.state == EnvelopeState.DONE:
        return
    if store is None:
        store = get_store()

    try:
        _consume(envelope, store)
    except Exception as error:
        _fail(envelope, error)
        return
    ENVELOPES.labels(source=envelope.source, state=EnvelopeState.DONE).inc()


def _consume(envelope: RawEnvelope, store: EventStore) -> None:
    if envelope.source in EVENT_SOURCES:
        _consume_event(envelope, store)
        return
    _consume_webhook(envelope, store)


def _consume_webhook(envelope: RawEnvelope, store: EventStore) -> None:
    parsed = am.parse_group(
        envelope.payload,
        envelope.project,
        environment=envelope.environment,
        received_at=envelope.received_at,
    )
    with transaction.atomic():
        applied = [_apply(envelope, occurrence) for occurrence in parsed.occurrences]
        events = [event for event in applied if event is not None]
        if events:
            store.insert(events)
        _finish(envelope, rejected=parsed.rejected)


def _consume_event(envelope: RawEnvelope, store: EventStore) -> None:
    occurrence = envelope_translator.translate_event(
        envelope.payload,
        envelope.project,
        environment=envelope.environment,
        received_at=envelope.received_at,
        source=envelope.source,
    )
    sentry_id = envelope_translator.sentry_event_id(
        envelope.payload,
        fallback=f"envelope-{envelope.pk}",
    )
    with transaction.atomic():
        event = _apply_event(envelope, occurrence, sentry_id)
        if event is not None:
            store.insert([event])
        _finish(envelope)


def _finish(envelope: RawEnvelope, rejected: list[str] | None = None) -> None:
    envelope.state = EnvelopeState.DONE
    envelope.error = ""
    if rejected:
        envelope.error = "; ".join(rejected)[:ERROR_MAX]
        ENVELOPES.labels(source=envelope.source, state="rejected").inc()
    envelope.save(update_fields=["state", "error"])


def _fail(envelope: RawEnvelope, error: Exception) -> None:
    envelope.state = EnvelopeState.FAILED
    envelope.error = f"{type(error).__name__}: {error}"[:ERROR_MAX]
    envelope.save(update_fields=["state", "error"])
    ENVELOPES.labels(source=envelope.source, state=EnvelopeState.FAILED).inc()
    log.exception("envelope %s failed and stays replayable", envelope.pk)


def _apply(envelope: RawEnvelope, occurrence: lifecycle.Occurrence) -> Event | None:
    project = envelope.project
    issue, issue_created = _issue_for(project, occurrence)
    episode, episode_created = Episode.objects.get_or_create(
        project=project,
        am_fingerprint=occurrence.am_fingerprint,
        starts_at=occurrence.starts_at,
        defaults={
            "issue": issue,
            "labels": dict(occurrence.labels),
            "environment": occurrence.environment,
            "ends_at": occurrence.ends_at,
            "delivery_count": 1,
            "last_delivery_at": occurrence.timestamp,
        },
    )

    if not episode_created and episode.issue_id != issue.pk:
        _reassign_episode(episode, issue)

    issue_state = None
    if not issue_created:
        issue_state = _issue_state(issue)
    episode_state = None
    if not episode_created:
        episode_state = _episode_state(episode)

    transition = lifecycle.apply_occurrence(issue_state, episode_state, occurrence)
    _write_episode(episode, transition, occurrence)
    _write_issue(issue, transition, occurrence)
    environments.record(issue, occurrence.environment, occurrence.timestamp)
    _record(issue, transition, occurrence)
    if not _changed_episode(transition):
        return None

    return Event(
        id=am.event_id(project.pk, occurrence),
        project_id=project.pk,
        issue_id=issue.pk,
        episode_id=str(episode.pk),
        timestamp=occurrence.timestamp,
        level=occurrence.level,
        message=occurrence.message,
        fingerprint=list(occurrence.fingerprint),
        tags=dict(occurrence.tags),
        extra=dict(occurrence.extra),
        source=occurrence.source,
        environment=occurrence.environment,
    )


def _apply_event(
    envelope: RawEnvelope,
    occurrence: lifecycle.Occurrence,
    sentry_id: str,
) -> Event | None:
    project = envelope.project
    if not _claim(project, sentry_id):
        OCCURRENCES.labels(source=occurrence.source, outcome="duplicate").inc()
        return None

    issue, issue_created = _issue_for(project, occurrence)
    issue_state = None
    if not issue_created:
        issue_state = _issue_state(issue)

    ProcessedEvent.objects.filter(project=project, event_id=sentry_id).update(
        issue=issue
    )
    transition = lifecycle.apply_event(issue_state, occurrence)
    transition = _release_aware(issue, issue_state, transition, occurrence)
    releases.record(
        project,
        occurrence.release,
        occurrence.dist,
        occurrence.environment,
        occurrence.starts_at,
    )
    _write_issue(issue, transition, occurrence)
    environments.record(issue, occurrence.environment, occurrence.starts_at)
    _record(issue, transition, occurrence)
    OCCURRENCES.labels(source=occurrence.source, outcome="stored").inc()

    return Event(
        id=envelope_translator.event_id(project.pk, sentry_id, occurrence.starts_at),
        project_id=project.pk,
        issue_id=issue.pk,
        episode_id=None,
        timestamp=occurrence.starts_at,
        level=occurrence.level,
        message=occurrence.message,
        fingerprint=list(occurrence.fingerprint),
        tags=dict(occurrence.tags),
        extra={**occurrence.extra, "event_id": sentry_id},
        payload=dict(occurrence.payload),
        source=occurrence.source,
        environment=occurrence.environment,
    )


def _release_aware(
    issue: Issue,
    issue_state: lifecycle.IssueState | None,
    transition: lifecycle.Transition,
    occurrence: lifecycle.Occurrence,
) -> lifecycle.Transition:
    if issue_state is None:
        return transition
    if not lifecycle.has_regression(transition):
        return transition
    if releases.regressed(issue, occurrence.release):
        return transition
    return lifecycle.suppress_regression(transition, issue_state)


def _issue_for(
    project: Project, occurrence: lifecycle.Occurrence
) -> tuple[Issue, bool]:
    alias = merge.resolve_alias(project.pk, occurrence.fingerprint_hash)
    if alias is not None:
        return Issue.objects.select_for_update().get(pk=alias.pk), False
    return Issue.objects.select_for_update().get_or_create(
        project=project,
        fingerprint_hash=occurrence.fingerprint_hash,
        defaults=lifecycle.new_issue_fields(occurrence),
    )


def _reassign_episode(episode: Episode, issue: Issue) -> None:
    previous_id = episode.issue_id
    still_open = episode.ends_at is None
    episode.issue = issue
    episode.save(update_fields=["issue"])
    log.info(
        "episode %s moved from issue %s to %s after a grouping change",
        episode.pk,
        previous_id,
        issue.pk,
    )
    if not still_open:
        return
    Issue.objects.filter(pk=previous_id, open_episode_count__gt=0).update(
        open_episode_count=F("open_episode_count") - 1
    )
    Issue.objects.filter(pk=previous_id, open_episode_count=0).update(
        source_state=SourceState.RESOLVED
    )
    issue.open_episode_count += 1


def _claim(project: Project, sentry_id: str) -> bool:
    _, created = ProcessedEvent.objects.get_or_create(
        project=project,
        event_id=sentry_id,
    )
    return created


def _changed_episode(transition: lifecycle.Transition) -> bool:
    if transition.create_episode:
        return True
    if transition.close_episode:
        return True
    return transition.open_episode_delta > 0


def _issue_state(issue: Issue) -> lifecycle.IssueState:
    return lifecycle.IssueState(
        triage_state=issue.triage_state,
        open_episode_count=issue.open_episode_count,
        level=issue.level,
        first_seen=issue.first_seen,
        last_seen=issue.last_seen,
        last_resolved_at=issue.last_resolved_at,
    )


def _episode_state(episode: Episode) -> lifecycle.EpisodeState:
    return lifecycle.EpisodeState(
        starts_at=episode.starts_at,
        ends_at=episode.ends_at,
        delivery_count=episode.delivery_count,
    )


def _write_episode(
    episode: Episode,
    transition: lifecycle.Transition,
    occurrence: lifecycle.Occurrence,
) -> None:
    if transition.create_episode:
        outcome = "closed"
        if transition.open_episode_delta > 0:
            outcome = "opened"
        OCCURRENCES.labels(source=occurrence.source, outcome=outcome).inc()
        return

    fields = ["delivery_count", "last_delivery_at"]
    episode.delivery_count += 1
    episode.last_delivery_at = occurrence.timestamp
    outcome = "delivery"
    if transition.close_episode:
        episode.ends_at = occurrence.ends_at
        fields.append("ends_at")
        outcome = "closed"
    if transition.open_episode_delta > 0:
        episode.ends_at = None
        fields.append("ends_at")
        outcome = "reopened"
    episode.save(update_fields=fields)
    OCCURRENCES.labels(source=occurrence.source, outcome=outcome).inc()


def _write_issue(
    issue: Issue,
    transition: lifecycle.Transition,
    occurrence: lifecycle.Occurrence,
) -> None:
    before = issue.event_count
    for field, value in transition.issue_fields.items():
        setattr(issue, field, value)
    if transition.count_occurrence:
        issue.event_count += 1
    issue.open_episode_count = max(
        0, issue.open_episode_count + transition.open_episode_delta
    )
    issue.search_text = search.text_for(issue, occurrence)
    fields = sorted(
        {*transition.issue_fields, "event_count", "open_episode_count", "search_text"}
    )
    issue.save(update_fields=fields)
    if transition.count_occurrence:
        aggregates.count_occurrence(issue, occurrence.starts_at, occurrence.tags)
    hooks.fire("PANDORA_ISSUE_HOOKS", issue, transition, occurrence, before)


def _record(
    issue: Issue,
    transition: lifecycle.Transition,
    occurrence: lifecycle.Occurrence,
) -> None:
    IssueActivity.objects.bulk_create(
        [
            IssueActivity(
                issue=issue,
                kind=record.kind,
                actor=record.actor,
                at=occurrence.timestamp,
                data=record.data,
            )
            for record in transition.activities
        ]
    )
