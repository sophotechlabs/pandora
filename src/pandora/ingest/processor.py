from __future__ import annotations

import logging

from django.db import transaction
from prometheus_client import Counter

from pandora.events.store import EventStore, get_store
from pandora.events.types import Event
from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.ingest.translators import am
from pandora.issues import aggregates, lifecycle
from pandora.issues.models import Episode, Issue, IssueActivity

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
    occurrences = am.parse_webhook(
        envelope.payload,
        envelope.project,
        environment=envelope.environment,
        received_at=envelope.received_at,
    )
    with transaction.atomic():
        applied = [_apply(envelope, occurrence) for occurrence in occurrences]
        events = [event for event in applied if event is not None]
        if events:
            store.insert(events)
        envelope.state = EnvelopeState.DONE
        envelope.error = ""
        envelope.save(update_fields=["state", "error"])


def _fail(envelope: RawEnvelope, error: Exception) -> None:
    envelope.state = EnvelopeState.FAILED
    envelope.error = f"{type(error).__name__}: {error}"[:ERROR_MAX]
    envelope.save(update_fields=["state", "error"])
    ENVELOPES.labels(source=envelope.source, state=EnvelopeState.FAILED).inc()
    log.exception("envelope %s failed and stays replayable", envelope.pk)


def _apply(envelope: RawEnvelope, occurrence: lifecycle.Occurrence) -> Event | None:
    project = envelope.project
    issue, issue_created = Issue.objects.select_for_update().get_or_create(
        project=project,
        fingerprint_hash=occurrence.fingerprint_hash,
        defaults=lifecycle.new_issue_fields(occurrence),
    )
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

    issue_state = None
    if not issue_created:
        issue_state = _issue_state(issue)
    episode_state = None
    if not episode_created:
        episode_state = _episode_state(episode)

    transition = lifecycle.apply_occurrence(issue_state, episode_state, occurrence)
    _write_episode(episode, transition, occurrence)
    _write_issue(issue, transition, occurrence)
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
    for field, value in transition.issue_fields.items():
        setattr(issue, field, value)
    if transition.count_occurrence:
        issue.event_count += 1
    issue.open_episode_count = max(
        0, issue.open_episode_count + transition.open_episode_delta
    )
    fields = sorted({*transition.issue_fields, "event_count", "open_episode_count"})
    issue.save(update_fields=fields)
    if transition.count_occurrence:
        aggregates.count_occurrence(issue, occurrence.starts_at, occurrence.tags)


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
