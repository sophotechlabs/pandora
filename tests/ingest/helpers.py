from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.issues import models as issue_models
from tests.ingest import fakes


def store_envelope(payload, token, received_at=None):
    envelope = ingest_models.RawEnvelope(
        project=token.project,
        source=core_models.TokenSource.AM,
        environment=token.environment,
        payload=payload,
    )
    if received_at is not None:
        envelope.received_at = received_at
    envelope.save()
    return envelope


def deliver(payload, token, store=None, received_at=None):
    if store is None:
        store = fakes.RecordingEventStore()
    envelope = store_envelope(payload, token, received_at)
    processor.process_envelope(envelope.pk, store=store)
    envelope.refresh_from_db()
    return envelope


def snapshot():
    titles = dict(issue_models.Issue.objects.values_list("pk", "fingerprint_hash"))
    return {
        "issues": sorted(
            (
                issue.fingerprint_hash,
                issue.title,
                issue.culprit,
                issue.level,
                issue.environment,
                issue.first_seen,
                issue.last_seen,
                issue.event_count,
                issue.open_episode_count,
                issue.source_state,
                issue.triage_state,
                issue.last_resolved_at,
                tuple(issue.fingerprint),
                tuple(sorted(issue.grouping_labels.items())),
            )
            for issue in issue_models.Issue.objects.all()
        ),
        "episodes": sorted(
            (
                titles[episode.issue_id],
                episode.am_fingerprint,
                episode.starts_at,
                episode.ends_at,
                episode.delivery_count,
                episode.last_delivery_at,
                episode.environment,
                tuple(sorted(episode.labels.items())),
            )
            for episode in issue_models.Episode.objects.all()
        ),
        "hourly": sorted(
            (titles[stat.issue_id], stat.hour, stat.count)
            for stat in issue_models.HourlyStat.objects.all()
        ),
        "tags": sorted(
            (titles[stat.issue_id], stat.key, stat.value, stat.count)
            for stat in issue_models.TagStat.objects.all()
        ),
        "activities": sorted(
            (
                titles[activity.issue_id],
                activity.kind,
                activity.actor,
                activity.at,
                tuple(sorted(activity.data.items())),
            )
            for activity in issue_models.IssueActivity.objects.all()
        ),
    }
