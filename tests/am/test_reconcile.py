import datetime

import pytest
from django.utils import timezone

from pandora.am import client as am_client
from pandora.am import reconcile
from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.issues import grouping
from pandora.issues import models as issue_models
from tests.am import fake_am

pytestmark = pytest.mark.django_db

TARGET_DOWN = "3c1f6a2b9d4e5087"
CPU_OVERCOMMIT = "9a8b7c6d5e4f3021"
WATCHDOG = "0f0e0d0c0b0a0908"


@pytest.fixture
def moment():
    return timezone.now().replace(microsecond=0)


@pytest.fixture
def scope(token):
    return reconcile.Scope(project=token.project, environment=token.environment)


@pytest.fixture
def reconciler(scope, alertmanager_client):
    return reconcile.Reconciler(scope, alertmanager_client)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def target_down(moment, **overrides):
    kwargs = {
        "starts_at": stamp(moment - datetime.timedelta(hours=2)),
        "ends_at": stamp(moment + datetime.timedelta(hours=1)),
    }
    kwargs.update(overrides)
    return fake_am.alert(
        TARGET_DOWN,
        {
            "alertname": "TargetDown",
            "namespace": "monitoring",
            "pod": "node-exporter-1",
        },
        **kwargs,
    )


def cpu_overcommit(moment, **overrides):
    kwargs = {
        "starts_at": stamp(moment - datetime.timedelta(hours=3)),
        "ends_at": stamp(moment + datetime.timedelta(hours=1)),
    }
    kwargs.update(overrides)
    return fake_am.alert(
        CPU_OVERCOMMIT,
        {"alertname": "KubeCPUOvercommit", "namespace": "kube-system"},
        **kwargs,
    )


def watchdog(moment):
    return fake_am.alert(
        WATCHDOG,
        {"alertname": "Watchdog", "severity": "none"},
        starts_at=stamp(moment - datetime.timedelta(days=3)),
    )


def open_episode(scope, moment, fingerprint=TARGET_DOWN, **overrides):
    labels = {
        "alertname": "TargetDown",
        "namespace": "monitoring",
        "pod": "node-exporter-1",
    }
    rule = grouping.default_rule()
    components = grouping.compute_fingerprint(rule, labels)
    issue = issue_models.Issue.objects.create(
        project=scope.project,
        fingerprint_hash=grouping.fingerprint_hash(components),
        fingerprint=components,
        grouping_labels=grouping.surviving_labels(rule, labels),
        title="TargetDown: scrape target unreachable",
        environment=scope.environment,
        first_seen=moment - datetime.timedelta(hours=2),
        last_seen=moment,
        event_count=1,
        open_episode_count=1,
        source_state=issue_models.SourceState.FIRING,
    )
    fields = {
        "project": scope.project,
        "issue": issue,
        "am_fingerprint": fingerprint,
        "labels": labels,
        "environment": scope.environment,
        "starts_at": moment - datetime.timedelta(hours=2),
        "ends_at": None,
        "delivery_count": 1,
        "last_delivery_at": moment,
    }
    fields.update(overrides)
    return issue_models.Episode.objects.create(**fields)


def run(reconciler, moment, cycles=1, minutes=1):
    reports = []
    for index in range(cycles):
        at = moment + datetime.timedelta(minutes=minutes * index)
        reports.append(reconciler.run_once(at))
    return reports


# scope resolution


def test_the_scope_comes_from_the_alertmanager_token(token):
    """Should reconcile the project and environment Alertmanager posts into."""
    scope = reconcile.resolve_scope()

    result = (scope.project.slug, scope.environment)
    expected = ("infrastructure", "p-mk1")

    assert result == expected


def test_no_alertmanager_token_is_an_error(project):
    """Should refuse to guess which project an alert set belongs to."""
    with pytest.raises(reconcile.ScopeError) as error:
        reconcile.resolve_scope()

    assert "no active Alertmanager ingest token" in str(error.value)


def test_a_read_token_is_not_an_alertmanager_scope(project):
    """Should ignore API tokens — only the ingest token binds an Alertmanager."""
    core_models.IngestToken.objects.create(
        project=project,
        name="spinoza",
        token="read-token",
        source=core_models.TokenSource.AM,
        scope=core_models.TokenScope.READ,
        environment="p-mk1",
    )

    with pytest.raises(reconcile.ScopeError):
        reconcile.resolve_scope()


def test_an_inactive_token_is_not_an_alertmanager_scope(token):
    """Should stop reconciling a cluster whose token was switched off."""
    token.active = False
    token.save(update_fields=["active"])

    with pytest.raises(reconcile.ScopeError):
        reconcile.resolve_scope()


def test_two_clusters_need_the_scope_spelled_out(token):
    """Should refuse to reconcile one Alertmanager against two clusters' episodes."""
    core_models.IngestToken.objects.create(
        project=token.project,
        name="p-mk2 alertmanager",
        token="second-ingest-token",
        source=core_models.TokenSource.AM,
        environment="p-mk2",
    )

    with pytest.raises(reconcile.ScopeError) as error:
        reconcile.resolve_scope()

    assert "infrastructure/p-mk1" in str(error.value)
    assert "infrastructure/p-mk2" in str(error.value)


def test_the_environment_flag_narrows_to_one_cluster(token):
    """Should let one reconcile Deployment per Alertmanager name its own cluster."""
    core_models.IngestToken.objects.create(
        project=token.project,
        name="p-mk2 alertmanager",
        token="second-ingest-token",
        source=core_models.TokenSource.AM,
        environment="p-mk2",
    )

    result = reconcile.resolve_scope(environment="p-mk2").environment
    expected = "p-mk2"

    assert result == expected


def test_an_unknown_project_flag_matches_nothing(token):
    """Should say so rather than reconcile the wrong project."""
    with pytest.raises(reconcile.ScopeError):
        reconcile.resolve_scope(project_slug="applications")


# missed-webhook catch-up


def test_an_alert_with_no_episode_is_caught_up(alertmanager, reconciler, moment):
    """Should recover the webhook Alertmanager never delivered."""
    alertmanager.alerts = [target_down(moment)]

    report = reconciler.run_once(moment)

    result = (report.opened, issue_models.Episode.objects.count())
    expected = (1, 1)

    assert result == expected


def test_a_caught_up_episode_carries_alertmanager_identity(
    alertmanager, reconciler, moment
):
    """Should key the episode on the same fingerprint and start the webhook would."""
    alertmanager.alerts = [target_down(moment)]

    reconciler.run_once(moment)

    episode = issue_models.Episode.objects.get()

    result = (episode.am_fingerprint, episode.starts_at, episode.ends_at)
    expected = (TARGET_DOWN, moment - datetime.timedelta(hours=2), None)

    assert result == expected


def test_a_caught_up_alert_groups_into_an_issue(alertmanager, reconciler, moment):
    """Should apply the same grouping rules the ingest path applies."""
    alertmanager.alerts = [target_down(moment)]

    reconciler.run_once(moment)

    issue = issue_models.Issue.objects.get()

    result = (issue.title, issue.grouping_labels, issue.source_state)
    expected = (
        "TargetDown: scrape target unreachable",
        {"alertname": "TargetDown", "namespace": "monitoring"},
        issue_models.SourceState.FIRING,
    )

    assert result == expected


def test_catch_up_goes_through_the_envelope_inbox(alertmanager, reconciler, moment):
    """Should replay through the one consumer, not write issues of its own."""
    alertmanager.alerts = [target_down(moment)]

    reconciler.run_once(moment)

    envelope = ingest_models.RawEnvelope.objects.get()

    result = (envelope.state, envelope.payload["groupKey"], envelope.environment)
    expected = (ingest_models.EnvelopeState.DONE, "pandora-reconcile", "p-mk1")

    assert result == expected


def test_a_second_pass_catches_nothing_up(alertmanager, reconciler, moment):
    """Should be idempotent — the episode it created covers the alert next time."""
    alertmanager.alerts = [target_down(moment)]

    reports = run(reconciler, moment, cycles=2)

    result = [report.opened for report in reports]
    expected = [1, 0]

    assert result == expected
    assert ingest_models.RawEnvelope.objects.count() == 1


def test_a_known_alert_is_left_alone(alertmanager, reconciler, scope, moment):
    """Should not touch an episode the webhook path already opened."""
    open_episode(scope, moment)
    alertmanager.alerts = [target_down(moment)]

    report = reconciler.run_once(moment)

    result = (report.opened, report.closed, report.missing)
    expected = (0, 0, 0)

    assert result == expected
    assert ingest_models.RawEnvelope.objects.exists() is False


def test_an_alert_without_a_start_is_not_caught_up(alertmanager, reconciler, moment):
    """Should skip an alert it cannot key an episode on, and keep the pass going."""
    alertmanager.alerts = [target_down(moment, starts_at=""), cpu_overcommit(moment)]

    report = reconciler.run_once(moment)

    result = (report.opened, issue_models.Episode.objects.count())
    expected = (1, 1)

    assert result == expected


def test_an_envelope_the_consumer_rejects_is_logged(
    alertmanager, reconciler, moment, caplog
):
    """Should leave the failure replayable and say so, not fail the pass silently."""
    alertmanager.alerts = [target_down(moment, starts_at="whenever")]

    reconciler.run_once(moment)

    envelope = ingest_models.RawEnvelope.objects.get()

    result = (envelope.state, issue_models.Episode.objects.exists())
    expected = (ingest_models.EnvelopeState.FAILED, False)

    assert result == expected
    assert "did not apply" in caplog.text


def test_an_alert_without_a_fingerprint_is_ignored(alertmanager, reconciler, moment):
    """Should not build episode identity out of a missing fingerprint."""
    nameless = target_down(moment)
    nameless["fingerprint"] = ""
    alertmanager.alerts = [nameless]

    report = reconciler.run_once(moment)

    result = (report.alerts, report.opened)
    expected = (1, 0)

    assert result == expected


# closing after three consecutive misses


def test_one_absence_does_not_close_an_episode(alertmanager, reconciler, scope, moment):
    """Should never manufacture a resolve from a single poll."""
    episode = open_episode(scope, moment)

    reconciler.run_once(moment)

    result = issue_models.Episode.objects.get(pk=episode.pk).ends_at
    expected = None

    assert result == expected


def test_two_absences_do_not_close_an_episode(alertmanager, reconciler, scope, moment):
    """Should hold the episode open through the second miss as well."""
    episode = open_episode(scope, moment)

    run(reconciler, moment, cycles=2)

    result = issue_models.Episode.objects.get(pk=episode.pk).ends_at
    expected = None

    assert result == expected


def test_the_third_absence_closes_the_episode(alertmanager, reconciler, scope, moment):
    """Should close the episode Alertmanager has stopped reporting."""
    episode = open_episode(scope, moment)

    run(reconciler, moment, cycles=3)

    result = issue_models.Episode.objects.get(pk=episode.pk).ends_at
    expected = moment + datetime.timedelta(minutes=2)

    assert result == expected


def test_closing_resolves_the_issue(alertmanager, reconciler, scope, moment):
    """Should carry the close through to the issue's live state."""
    episode = open_episode(scope, moment)

    run(reconciler, moment, cycles=3)

    issue = issue_models.Issue.objects.get(pk=episode.issue_id)

    result = (issue.open_episode_count, issue.source_state)
    expected = (0, issue_models.SourceState.RESOLVED)

    assert result == expected


def test_closing_goes_through_the_envelope_inbox(
    alertmanager, reconciler, scope, moment
):
    """Should apply the close through the same consumer a resolved webhook uses."""
    open_episode(scope, moment)

    run(reconciler, moment, cycles=3)

    envelope = ingest_models.RawEnvelope.objects.get()

    result = (envelope.state, envelope.payload["status"])
    expected = (ingest_models.EnvelopeState.DONE, "resolved")

    assert result == expected


def test_a_closed_episode_is_not_closed_again(alertmanager, reconciler, scope, moment):
    """Should stop counting an episode once it is closed, not resolve it every pass."""
    open_episode(scope, moment)

    reports = run(reconciler, moment, cycles=6)

    result = [report.closed for report in reports]
    expected = [0, 0, 1, 0, 0, 0]

    assert result == expected
    assert ingest_models.RawEnvelope.objects.count() == 1


def test_the_miss_count_resets_when_the_alert_comes_back(
    alertmanager, reconciler, scope, moment
):
    """Should count consecutive absences only — an Alertmanager restart is not a resolve."""
    episode = open_episode(scope, moment)

    run(reconciler, moment, cycles=2)
    alertmanager.alerts = [target_down(moment)]
    reconciler.run_once(moment + datetime.timedelta(minutes=2))
    alertmanager.alerts = []
    run(reconciler, moment + datetime.timedelta(minutes=3), cycles=2)

    result = issue_models.Episode.objects.get(pk=episode.pk).ends_at
    expected = None

    assert result == expected


def test_an_empty_then_repopulated_alertmanager_closes_nothing(
    alertmanager, reconciler, scope, moment
):
    """Should survive the restart the three-miss rule exists for."""
    open_episode(scope, moment)

    run(reconciler, moment, cycles=2)
    alertmanager.alerts = [target_down(moment)]
    report = reconciler.run_once(moment + datetime.timedelta(minutes=2))

    result = (report.closed, report.opened, report.missing)
    expected = (0, 0, 0)

    assert result == expected
    assert ingest_models.RawEnvelope.objects.exists() is False


def test_a_failed_poll_is_not_a_miss(alertmanager, reconciler, scope, moment):
    """Should never read an unreachable Alertmanager as an empty alert set."""
    episode = open_episode(scope, moment)
    alertmanager.fail_next(503, times=40)

    for index in range(4):
        reconciler.cycle(moment + datetime.timedelta(minutes=index))

    result = (
        reconciler.misses,
        issue_models.Episode.objects.get(pk=episode.pk).ends_at,
    )
    expected = ({}, None)

    assert result == expected


def test_a_failed_poll_is_reported_not_raised(alertmanager, reconciler, moment):
    """Should let the loop keep running and try again next cycle."""
    alertmanager.fail_next(503, times=40)

    report = reconciler.cycle(moment)

    assert report.error != ""
    assert report.alerts == 0


def test_run_once_raises_so_a_caller_can_decide(alertmanager, reconciler, moment):
    """Should keep the failure visible to anything that is not the loop."""
    alertmanager.fail_next(503, times=40)

    with pytest.raises(am_client.AlertmanagerError):
        reconciler.run_once(moment)


# suppressed alerts


def test_a_suppressed_alert_still_counts_as_firing(
    alertmanager, reconciler, scope, moment
):
    """Should keep a silenced alert's episode open — a silence is not a resolve."""
    episode = open_episode(scope, moment)
    alertmanager.alerts = [target_down(moment, state="suppressed")]

    reports = run(reconciler, moment, cycles=3)

    result = (
        [report.missing for report in reports],
        issue_models.Episode.objects.get(pk=episode.pk).ends_at,
    )
    expected = ([0, 0, 0], None)

    assert result == expected


def test_a_suppressed_alert_with_no_episode_is_caught_up(
    alertmanager, reconciler, moment
):
    """Should record an inhibited alert too — the record is what pandora is for."""
    alertmanager.alerts = [target_down(moment, state="suppressed")]

    report = reconciler.run_once(moment)

    result = (report.opened, issue_models.Episode.objects.count())
    expected = (1, 1)

    assert result == expected


# watchdog


def test_seeing_the_watchdog_stamps_the_metric(alertmanager, reconciler, moment):
    """Should feed the dead-man's switch that watches the Alertmanager path itself."""
    alertmanager.alerts = [watchdog(moment)]

    report = reconciler.run_once(moment)

    result = (report.watchdog, reconcile.WATCHDOG_SEEN._value.get())
    expected = (True, moment.timestamp())

    assert result == expected


def test_a_missing_watchdog_leaves_the_metric_alone(alertmanager, reconciler, moment):
    """Should let the metric go stale, which is exactly what the alert rule reads."""
    reconcile.WATCHDOG_SEEN.set(0)
    alertmanager.alerts = [target_down(moment)]

    report = reconciler.run_once(moment)

    result = (report.watchdog, reconcile.WATCHDOG_SEEN._value.get())
    expected = (False, 0)

    assert result == expected


def test_an_alert_without_labels_is_not_a_watchdog(alertmanager, reconciler, moment):
    """Should not read a label-less alert as the heartbeat."""
    broken = target_down(moment)
    broken["labels"] = "not a mapping"
    alertmanager.alerts = [broken]

    result = reconciler.run_once(moment).watchdog

    assert result is False


# scope isolation


def test_another_environment_is_left_alone(alertmanager, reconciler, scope, moment):
    """Should never close the other cluster's episodes from this Alertmanager."""
    episode = open_episode(scope, moment, environment="p-mk2")

    reports = run(reconciler, moment, cycles=3)

    result = (
        [report.open_episodes for report in reports],
        issue_models.Episode.objects.get(pk=episode.pk).ends_at,
    )
    expected = ([0, 0, 0], None)

    assert result == expected


def test_a_closed_episode_is_not_reopened_by_the_scan(
    alertmanager, reconciler, scope, moment
):
    """Should leave resolved history alone when the alert is gone from Alertmanager."""
    open_episode(scope, moment, ends_at=moment - datetime.timedelta(minutes=5))

    report = reconciler.run_once(moment)

    result = (report.open_episodes, report.missing, report.closed)
    expected = (0, 0, 0)

    assert result == expected


# reporting


def test_the_report_counts_what_the_pass_saw(alertmanager, reconciler, scope, moment):
    """Should give the loop one line worth printing."""
    open_episode(scope, moment)
    alertmanager.alerts = [cpu_overcommit(moment), watchdog(moment)]

    report = reconciler.run_once(moment)

    result = (
        report.alerts,
        report.open_episodes,
        report.opened,
        report.closed,
        report.missing,
        report.watchdog,
    )
    expected = (2, 1, 2, 0, 1, True)

    assert result == expected
