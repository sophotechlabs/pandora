import datetime

import pytest
from django import db
from django.utils import timezone

from pandora.issues import models

# choice contract


def test_levels_mirror_the_sentry_severity_set():
    """Should offer exactly Sentry's five levels — the internal model is theirs."""
    result = list(models.Level.values)
    expected = ["debug", "info", "warning", "error", "fatal"]

    assert result == expected


def test_source_state_covers_only_what_alertmanager_says():
    """Should keep the machine-owned state to firing and resolved."""
    result = list(models.SourceState.values)
    expected = ["firing", "resolved"]

    assert result == expected


def test_triage_state_covers_only_what_a_human_says():
    """Should keep the human-owned state separate from the source state."""
    result = list(models.TriageState.values)
    expected = ["new", "ack", "resolved", "ignored"]

    assert result == expected


def test_grouping_modes_are_denylist_and_allowlist():
    """Should offer the two grouping modes the rule engine understands."""
    result = list(models.GroupingMode.values)
    expected = ["denylist", "allowlist"]

    assert result == expected


def test_activity_kinds_cover_the_audit_trail():
    """Should enumerate every activity later phases record, so models stay frozen."""
    result = sorted(models.ActivityKind.values)
    expected = sorted(
        [
            "created",
            "regression",
            "acknowledged",
            "resolved",
            "ignored",
            "reopened",
            "silenced",
            "unsilenced",
            "regrouped",
            "snoozed",
            "unsnoozed",
            "merged",
            "unmerged",
        ]
    )

    assert result == expected


def test_the_tag_cardinality_cap_is_pinned():
    """Should cap distinct tag values per key and name the overflow bucket."""
    result = (models.TAG_VALUE_CAP, models.TAG_OVERFLOW_VALUE)
    expected = (100, "<other>")

    assert result == expected


# field contract


def test_issue_starts_untriaged(issue):
    """Should open an issue as new with no resolution recorded."""
    result = {
        "triage_state": issue.triage_state,
        "last_resolved_at": issue.last_resolved_at,
    }
    expected = {"triage_state": "new", "last_resolved_at": None}

    assert result == expected


def test_issue_retains_the_grouping_labels_for_silence_matchers(issue):
    """Should keep the label subset that grouped the issue, not just its hash."""
    issue.refresh_from_db()

    result = issue.grouping_labels
    expected = {"alertname": "TargetDown", "namespace": "monitoring"}

    assert result == expected


def test_issue_retains_the_fingerprint_components(issue):
    """Should keep the fingerprint list so regroup can compare, not only hash."""
    issue.refresh_from_db()

    result = issue.fingerprint
    expected = ["alertname:TargetDown", "namespace:monitoring"]

    assert result == expected


def test_episode_defaults_to_one_open_delivery(project, issue):
    """Should count the creating delivery and leave the episode open."""
    episode = models.Episode.objects.create(
        project=project,
        issue=issue,
        am_fingerprint="0123456789abcdef",
        starts_at=timezone.now(),
    )

    result = {"delivery_count": episode.delivery_count, "ends_at": episode.ends_at}
    expected = {"delivery_count": 1, "ends_at": None}

    assert result == expected


# uniqueness


def test_the_fingerprint_is_unique_within_a_project_and_environment(issue):
    """Should refuse a second issue with the same fingerprint in one environment."""
    with pytest.raises(db.IntegrityError):
        models.Issue.objects.create(
            project=issue.project,
            environment=issue.environment,
            fingerprint_hash=issue.fingerprint_hash,
            title="a different title over the same fingerprint",
        )


def test_one_fingerprint_is_one_issue_whatever_the_environment(issue):
    """Should refuse a second row for the same fingerprint — environment is not identity."""
    with pytest.raises(db.IntegrityError):
        models.Issue.objects.create(
            project=issue.project,
            environment="p-mk2",
            fingerprint_hash=issue.fingerprint_hash,
            title=issue.title,
        )


def test_an_episode_is_identified_by_fingerprint_and_start(episode):
    """Should refuse a duplicate of the same alert instance at the same start."""
    with pytest.raises(db.IntegrityError):
        models.Episode.objects.create(
            project=episode.project,
            issue=episode.issue,
            am_fingerprint=episode.am_fingerprint,
            starts_at=episode.starts_at,
        )


def test_the_same_alert_may_start_a_later_episode(episode):
    """Should allow a fresh episode for the same alert once it fires again."""
    later = models.Episode.objects.create(
        project=episode.project,
        issue=episode.issue,
        am_fingerprint=episode.am_fingerprint,
        starts_at=episode.starts_at + datetime.timedelta(minutes=1),
    )

    assert later.pk != episode.pk


def test_an_hourly_bucket_exists_once_per_issue(issue):
    """Should refuse a second bucket for an hour already counted."""
    hour = timezone.now().replace(minute=0, second=0, microsecond=0)
    models.HourlyStat.objects.create(issue=issue, hour=hour, count=3)

    with pytest.raises(db.IntegrityError):
        models.HourlyStat.objects.create(issue=issue, hour=hour, count=1)


def test_a_tag_value_is_counted_once_per_issue(issue):
    """Should refuse a duplicate key/value row so upserts stay increments."""
    models.TagStat.objects.create(
        issue=issue,
        key="namespace",
        value="monitoring",
        count=4,
    )

    with pytest.raises(db.IntegrityError):
        models.TagStat.objects.create(issue=issue, key="namespace", value="monitoring")


def test_a_silence_is_linked_once_per_alertmanager_id(issue):
    """Should refuse a duplicate link to the same Alertmanager silence."""
    expires_at = timezone.now() + datetime.timedelta(hours=1)
    models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="s-1",
        expires_at=expires_at,
    )

    with pytest.raises(db.IntegrityError):
        models.SilenceLink.objects.create(
            issue=issue,
            am_silence_id="s-1",
            expires_at=expires_at,
        )


# index contract


def test_the_changelist_index_matches_the_default_ordering():
    """Should index project, triage state and last seen — the changelist query."""
    result = [index.fields for index in models.Issue._meta.indexes]
    expected = [
        ["project", "triage_state", "-last_seen"],
        ["project", "source_state"],
        ["snoozed_until"],
    ]

    assert result == expected


def test_open_episodes_get_a_partial_index():
    """Should index open episodes for both lookups — reconcile's and the changelist's."""
    partial = [
        index for index in models.Episode._meta.indexes if index.condition is not None
    ]
    condition = str(db.models.Q(ends_at__isnull=True))

    result = [(index.name, index.fields, str(index.condition)) for index in partial]
    expected = [
        ("issues_episode_open", ["project", "am_fingerprint"], condition),
        ("issues_episode_issue_open", ["issue", "starts_at"], condition),
    ]
    assert result == expected


# display


def test_issue_shows_its_title(issue):
    """Should render an issue as its title."""
    result = str(issue)
    expected = "TargetDown: scrape target unreachable"

    assert result == expected


def test_episode_is_named_by_fingerprint_and_start(episode):
    """Should render an episode as the identity pair that makes it unique."""
    result = str(episode)
    expected = f"3c1f6a2b9d4e5087@{episode.starts_at:%Y-%m-%dT%H:%M:%SZ}"

    assert result == expected


def test_tag_stat_shows_the_pair_and_its_count(issue):
    """Should render a tag stat as key=value with its count."""
    stat = models.TagStat.objects.create(
        issue=issue,
        key="severity",
        value="warning",
        count=2,
    )

    result = str(stat)
    expected = "severity=warning x2"

    assert result == expected


def test_hourly_stat_shows_its_bucket(issue):
    """Should render an hourly bucket as the hour and its count."""
    hour = timezone.now().replace(minute=0, second=0, microsecond=0)
    stat = models.HourlyStat.objects.create(issue=issue, hour=hour, count=7)

    result = str(stat)
    expected = f"{hour:%Y-%m-%dT%H}Z x7"

    assert result == expected


def test_activity_names_its_kind_and_issue(issue):
    """Should render an activity as kind and issue."""
    activity = models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.REGRESSION,
    )

    result = str(activity)
    expected = f"regression on issue {issue.pk}"

    assert result == expected


@pytest.mark.django_db
def test_grouping_rule_reads_as_priority_mode_and_match():
    """Should render a rule as priority, mode and the alertname it matches."""
    rule = models.GroupingRule.objects.create(
        priority=10,
        mode=models.GroupingMode.ALLOWLIST,
    )

    result = str(rule)
    expected = "10 allowlist *"

    assert result == expected


def test_silence_link_shows_its_alertmanager_id(issue):
    """Should render a silence link as the Alertmanager silence id."""
    link = models.SilenceLink.objects.create(
        issue=issue,
        am_silence_id="s-2",
        expires_at=timezone.now(),
    )

    result = str(link)
    expected = "s-2"

    assert result == expected
