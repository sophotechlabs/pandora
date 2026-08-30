import dataclasses
import datetime

import pytest

from pandora.ingest.translators import am
from pandora.issues import grouping
from pandora.issues import models as issue_models

RECEIVED_AT = datetime.datetime(2026, 8, 4, 9, 15, tzinfo=datetime.UTC)
FIRST_STARTED_AT = datetime.datetime(2026, 8, 4, 9, 12, 41, 123000, tzinfo=datetime.UTC)
SECOND_STARTED_AT = datetime.datetime(
    2026, 8, 4, 9, 14, 11, 987000, tzinfo=datetime.UTC
)
RESOLVED_AT = datetime.datetime(2026, 8, 4, 9, 47, 41, 123000, tzinfo=datetime.UTC)
GENERATOR_URL = (
    "https://prometheus.c.p-mk1.sopho.tech/graph?g0.expr=max_over_time%28"
    "kube_pod_container_status_waiting_reason%7Breason%3D%22CrashLoopBackOff%22%7D"
    "%5B5m%5D%29+%3E%3D+1&g0.tab=1"
)
GROUP_KEY = (
    '{}/{severity=~"critical|warning"}:'
    '{alertname="KubePodCrashLooping", namespace="payments"}'
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def parse(project, token):
    def run(payload, received_at=RECEIVED_AT):
        return am.parse_group(
            payload,
            project,
            environment=token.environment,
            received_at=received_at,
        ).occurrences

    return run


@pytest.fixture
def reject(project, token):
    def run(payload, received_at=RECEIVED_AT):
        return am.parse_group(
            payload,
            project,
            environment=token.environment,
            received_at=received_at,
        ).rejected

    return run


def alert_payload(**overrides):
    alert = {
        "status": "firing",
        "labels": {"alertname": "TargetDown", "severity": "warning"},
        "annotations": {"summary": "target is down"},
        "startsAt": "2026-08-04T09:12:41.123Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "https://prometheus.example.com/graph",
        "fingerprint": "d10a4e77b6c25913",
    }
    alert.update(overrides)
    return {
        "version": "4",
        "groupKey": '{}:{alertname="TargetDown"}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "pandora",
        "groupLabels": {},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "https://alertmanager.example.com",
        "alerts": [alert],
    }


# payload contract


def test_the_translator_pins_the_payload_version():
    """Should refuse a payload version the translator was not written against."""
    result = am.PAYLOAD_VERSION
    expected = "4"

    assert result == expected


def test_a_payload_of_another_version_fails_loudly(parse):
    """Should say which version arrived so a drift is obvious in the log."""
    with pytest.raises(am.PayloadError) as error:
        parse({"version": "5", "alerts": []})

    result = str(error.value)
    expected = "unsupported Alertmanager payload version '5'"
    assert result == expected


def test_a_payload_that_is_not_an_object_fails_loudly(parse):
    """Should reject a JSON array where the webhook object belongs."""
    with pytest.raises(am.PayloadError) as error:
        parse([])

    result = str(error.value)
    expected = "payload is not a JSON object"
    assert result == expected


def test_a_payload_without_alerts_fails_loudly(parse):
    """Should reject a payload with no alerts list to walk."""
    with pytest.raises(am.PayloadError) as error:
        parse({"version": "4"})

    result = str(error.value)
    expected = "payload carries no alerts list"
    assert result == expected


def test_an_empty_alert_list_is_not_an_error(parse):
    """Should accept a group that carries no alerts rather than failing it."""
    result = parse({"version": "4", "alerts": []})
    expected = []

    assert result == expected


# per-alert validation


def test_an_alert_that_is_not_an_object_is_rejected(reject):
    """Should reject a string where an alert object belongs."""
    payload = alert_payload()
    payload["alerts"] = ["firing"]

    result = reject(payload)
    expected = ["alert 0: alert is not a JSON object"]
    assert result == expected


def test_an_unknown_alert_status_is_rejected(reject):
    """Should reject a status the episode rules have no meaning for."""
    result = reject(alert_payload(status="pending"))
    expected = ["alert 0: unsupported alert status 'pending'"]
    assert result == expected


def test_an_alert_without_a_fingerprint_is_rejected(reject):
    """Should refuse an alert with no episode identity to key on."""
    result = reject(alert_payload(fingerprint=""))
    expected = ["alert 0: alert carries no fingerprint"]
    assert result == expected


def test_an_alert_without_a_start_is_rejected(reject):
    """Should refuse an alert with no start — episode identity needs it."""
    result = reject(alert_payload(startsAt="0001-01-01T00:00:00Z"))
    expected = ["alert 0: alert carries no startsAt"]
    assert result == expected


def test_an_unparsable_start_is_rejected(reject):
    """Should refuse a start timestamp that is not a timestamp."""
    result = reject(alert_payload(startsAt="yesterday"))
    expected = ["alert 0: alert carries no startsAt"]
    assert result == expected


def test_a_bad_alert_never_discards_its_siblings(parse, reject, am_fixture):
    """Should keep the group — one unusable alert used to drop every alert with it."""
    payload = am_fixture("firing_group")
    payload["alerts"].insert(0, "not an alert")

    result = (len(parse(payload)), len(reject(payload)))
    expected = (2, 1)
    assert result == expected


def test_a_wrong_payload_version_still_fails_the_whole_group(parse):
    """Should stay loud on version drift, which the plan pins as fail-fast."""
    payload = alert_payload()
    payload["version"] = "5"

    with pytest.raises(am.PayloadError):
        parse(payload)


# golden translation


def test_a_firing_alert_translates_field_for_field(parse, am_fixture):
    """Should turn one Alertmanager alert into one fully populated occurrence."""
    occurrences = parse(am_fixture("firing_group"))

    result = dataclasses.asdict(occurrences[0])
    expected = {
        "fingerprint": [
            "alertname:KubePodCrashLooping",
            "cluster:p-mk1",
            "namespace:payments",
            "severity:critical",
        ],
        "fingerprint_hash": occurrences[0].fingerprint_hash,
        "grouping_labels": {
            "alertname": "KubePodCrashLooping",
            "cluster": "p-mk1",
            "namespace": "payments",
            "severity": "critical",
        },
        "grouping_source": "rule",
        "grouping_rule_id": occurrences[0].grouping_rule_id,
        "release": "",
        "dist": "",
        "am_fingerprint": "3c1f6a2b9d4e5087",
        "labels": {
            "alertname": "KubePodCrashLooping",
            "namespace": "payments",
            "pod": "ledger-7d9f4c8b6d-hk2mp",
            "container": "ledger",
            "severity": "critical",
            "cluster": "p-mk1",
        },
        "status": "firing",
        "title": "KubePodCrashLooping: Pod is crash looping.",
        "culprit": (
            "alertname=KubePodCrashLooping cluster=p-mk1 "
            "namespace=payments severity=critical"
        ),
        "level": "error",
        "message": (
            "Pod payments/ledger-7d9f4c8b6d-hk2mp (ledger) is in waiting state "
            "(reason: CrashLoopBackOff)."
        ),
        "starts_at": FIRST_STARTED_AT,
        "ends_at": None,
        "timestamp": RECEIVED_AT,
        "tags": {
            "alertname": "KubePodCrashLooping",
            "namespace": "payments",
            "pod": "ledger-7d9f4c8b6d-hk2mp",
            "container": "ledger",
            "severity": "critical",
            "cluster": "p-mk1",
        },
        "extra": {
            "annotations": {
                "summary": "Pod is crash looping.",
                "description": (
                    "Pod payments/ledger-7d9f4c8b6d-hk2mp (ledger) is in waiting "
                    "state (reason: CrashLoopBackOff)."
                ),
                "runbook_url": (
                    "https://runbooks.prometheus-operator.dev/runbooks/kubernetes/"
                    "kubepodcrashlooping"
                ),
            },
            "generatorURL": GENERATOR_URL,
            "externalURL": "https://alertmanager.c.p-mk1.sopho.tech",
            "groupKey": GROUP_KEY,
        },
        "environment": "p-mk1",
        "source": "am",
        "payload": {},
    }

    assert result == expected


def test_a_group_translates_every_alert_it_carries(parse, am_fixture):
    """Should hand back one occurrence per alert, in payload order."""
    result = [
        (occurrence.am_fingerprint, occurrence.starts_at)
        for occurrence in parse(am_fixture("firing_group"))
    ]
    expected = [
        ("3c1f6a2b9d4e5087", FIRST_STARTED_AT),
        ("8b70e5d41c93a2f6", SECOND_STARTED_AT),
    ]

    assert result == expected


def test_two_crash_looping_pods_land_on_one_fingerprint(parse, am_fixture):
    """Should group per-pod alerts into a single issue identity."""
    occurrences = parse(am_fixture("firing_group"))

    result = occurrences[0].fingerprint_hash
    expected = occurrences[1].fingerprint_hash

    assert result == expected


def test_a_resolved_alert_carries_its_end(parse, am_fixture):
    """Should read the resolution time from the payload, not the clock."""
    occurrences = parse(am_fixture("resolved_group"))

    result = (occurrences[0].status, occurrences[0].ends_at)
    expected = ("resolved", RESOLVED_AT)

    assert result == expected


def test_a_mixed_group_keeps_each_alert_status(parse, am_fixture):
    """Should translate a group where one alert resolves and another fires."""
    result = [
        (occurrence.status, occurrence.ends_at is None)
        for occurrence in parse(am_fixture("mixed_group"))
    ]
    expected = [("resolved", False), ("firing", True)]

    assert result == expected


def test_a_resolution_for_an_unseen_alert_translates_normally(parse, am_fixture):
    """Should translate a resolution pandora never saw fire."""
    occurrences = parse(am_fixture("resolved_unknown"))

    result = (
        occurrences[0].am_fingerprint,
        occurrences[0].status,
        occurrences[0].level,
    )
    expected = ("d10a4e77b6c25913", "resolved", "warning")

    assert result == expected


def test_the_alertmanager_ui_link_survives_translation(parse, am_fixture):
    """Should keep the enrichment links the detail page renders."""
    occurrences = parse(am_fixture("truncated"))

    result = occurrences[0].extra["externalURL"]
    expected = "https://alertmanager.c.p-mk2.sopho.tech"

    assert result == expected


def test_dropped_alerts_are_logged(parse, am_fixture, caplog):
    """Should record that Alertmanager threw alerts away before delivery."""
    with caplog.at_level("WARNING"):
        parse(am_fixture("truncated"))

    assert "dropped 7 alerts" in caplog.text


def test_a_complete_group_logs_nothing(parse, am_fixture, caplog):
    """Should stay quiet when Alertmanager dropped nothing."""
    with caplog.at_level("WARNING"):
        parse(am_fixture("firing_group"))

    assert caplog.text == ""


def test_a_non_numeric_truncation_count_is_ignored(parse, caplog):
    """Should survive a payload whose truncatedAlerts is not a number."""
    payload = alert_payload()
    payload["truncatedAlerts"] = "seven"

    with caplog.at_level("WARNING"):
        parse(payload)

    assert caplog.text == ""


# severity mapping


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        ("critical", issue_models.Level.ERROR),
        ("error", issue_models.Level.ERROR),
        ("warning", issue_models.Level.WARNING),
        ("warn", issue_models.Level.WARNING),
        ("info", issue_models.Level.INFO),
        ("none", issue_models.Level.INFO),
        ("debug", issue_models.Level.DEBUG),
        ("fatal", issue_models.Level.FATAL),
    ],
)
def test_every_prometheus_severity_maps_to_a_level(parse, severity, level):
    """Should translate the severity label into the level the schema stores."""
    payload = alert_payload(labels={"alertname": "TargetDown", "severity": severity})

    result = parse(payload)[0].level
    expected = level

    assert result == expected


def test_severity_matching_ignores_case(parse):
    """Should read CRITICAL the same as critical."""
    payload = alert_payload(labels={"alertname": "TargetDown", "severity": "CRITICAL"})

    result = parse(payload)[0].level
    expected = issue_models.Level.ERROR

    assert result == expected


def test_an_unknown_severity_falls_back_to_error(parse):
    """Should surface an unmapped severity as error rather than hiding it."""
    payload = alert_payload(labels={"alertname": "TargetDown", "severity": "spicy"})

    result = parse(payload)[0].level
    expected = issue_models.Level.ERROR

    assert result == expected


def test_a_missing_severity_falls_back_to_error(parse):
    """Should default an alert with no severity label to error."""
    payload = alert_payload(labels={"alertname": "TargetDown"})

    result = parse(payload)[0].level
    expected = issue_models.Level.ERROR

    assert result == expected


# annotations


def test_the_message_prefers_the_description(parse):
    """Should keep the instance-specific sentence as the occurrence message."""
    payload = alert_payload(
        annotations={"summary": "target is down", "description": "node-01 is down"}
    )

    result = parse(payload)[0].message
    expected = "node-01 is down"

    assert result == expected


def test_the_message_falls_back_to_the_title(parse):
    """Should still carry a readable message when only a summary exists."""
    payload = alert_payload(annotations={"summary": "target is down"})

    result = parse(payload)[0].message
    expected = "TargetDown: target is down"

    assert result == expected


def test_an_alert_without_annotations_still_translates(parse):
    """Should survive rules that ship no annotations at all."""
    payload = alert_payload(annotations={})

    result = (parse(payload)[0].title, parse(payload)[0].message)
    expected = ("TargetDown", "TargetDown")

    assert result == expected


def test_annotation_values_are_coerced_to_text(parse):
    """Should carry a numeric annotation as text — extra is JSON."""
    payload = alert_payload(annotations={"threshold": 90})

    result = parse(payload)[0].extra["annotations"]
    expected = {"threshold": "90"}

    assert result == expected


def test_an_alert_without_labels_still_translates(parse):
    """Should survive a payload whose labels key is missing or null."""
    payload = alert_payload(labels=None, annotations=None)
    occurrence = parse(payload)[0]

    result = (occurrence.tags, occurrence.fingerprint, occurrence.title)
    expected = ({}, [], grouping.UNLABELLED_TITLE)

    assert result == expected


def test_a_missing_generator_url_becomes_empty_text(parse):
    """Should never store null where the detail page expects a link."""
    payload = alert_payload()
    del payload["alerts"][0]["generatorURL"]

    result = parse(payload)[0].extra["generatorURL"]
    expected = ""

    assert result == expected


# timestamps


def test_a_firing_alert_with_a_future_end_stays_open(parse):
    """Should let the status decide the episode, not Alertmanager's expiry hint."""
    payload = alert_payload(endsAt="2026-08-04T10:12:41.123Z")

    result = parse(payload)[0].ends_at
    expected = None

    assert result == expected


def test_a_resolution_without_an_end_uses_the_delivery_time(parse):
    """Should close the episode deterministically, from the envelope not the clock."""
    payload = alert_payload(status="resolved", endsAt="0001-01-01T00:00:00Z")

    result = parse(payload)[0].ends_at
    expected = RECEIVED_AT

    assert result == expected


def test_a_resolution_with_no_end_key_uses_the_delivery_time(parse):
    """Should close the episode when Alertmanager omits endsAt entirely."""
    payload = alert_payload(status="resolved")
    del payload["alerts"][0]["endsAt"]

    result = parse(payload)[0].ends_at
    expected = RECEIVED_AT

    assert result == expected


def test_a_resolution_with_an_empty_end_uses_the_delivery_time(parse):
    """Should treat an empty endsAt as no end at all."""
    payload = alert_payload(status="resolved", endsAt="")

    result = parse(payload)[0].ends_at
    expected = RECEIVED_AT

    assert result == expected


def test_a_resolution_that_ends_before_it_starts_is_clamped(parse):
    """Should never store an episode that ends before it began."""
    payload = alert_payload(
        status="resolved",
        startsAt="2026-08-04T09:12:41.123Z",
        endsAt="2026-08-04T08:00:00.000Z",
    )

    result = parse(payload)[0].ends_at
    expected = FIRST_STARTED_AT

    assert result == expected


def test_nanosecond_timestamps_are_accepted(parse):
    """Should read the nanosecond precision Go emits without failing."""
    payload = alert_payload(startsAt="2026-08-04T09:12:41.123456789Z")

    result = parse(payload)[0].starts_at
    expected = datetime.datetime(2026, 8, 4, 9, 12, 41, 123456, tzinfo=datetime.UTC)

    assert result == expected


def test_an_offset_timestamp_is_normalised_to_utc(parse):
    """Should store every timestamp in UTC whatever offset arrived."""
    payload = alert_payload(startsAt="2026-08-04T11:12:41.123+02:00")

    result = parse(payload)[0].starts_at
    expected = FIRST_STARTED_AT

    assert result == expected


def test_a_timestamp_without_a_zone_is_read_as_utc(parse):
    """Should assume UTC rather than the server's local zone."""
    payload = alert_payload(startsAt="2026-08-04T09:12:41.123")

    result = parse(payload)[0].starts_at
    expected = FIRST_STARTED_AT

    assert result == expected


def test_the_delivery_clock_defaults_to_now(parse, project):
    """Should stamp the delivery time itself when no envelope time is given."""
    before = datetime.datetime.now(datetime.UTC)

    occurrences = am.parse_group(alert_payload(), project, received_at=None).occurrences

    result = occurrences[0].timestamp >= before
    expected = True

    assert result == expected


# event identity


def test_the_event_id_is_a_ulid(parse):
    """Should build an identifier the events table can sort by time."""
    occurrence = parse(alert_payload())[0]

    result = len(am.event_id(1, occurrence))
    expected = 26

    assert result == expected


def test_the_same_occurrence_always_gets_the_same_event_id(parse):
    """Should let a replayed envelope collide with itself instead of duplicating."""
    first = parse(alert_payload())[0]
    second = parse(alert_payload())[0]

    result = am.event_id(1, first)
    expected = am.event_id(1, second)

    assert result == expected


def test_the_resolution_of_an_episode_gets_its_own_event_id(parse):
    """Should record the close as a separate row from the open."""
    firing = parse(alert_payload())[0]
    resolved = parse(alert_payload(status="resolved"))[0]

    result = am.event_id(1, firing)
    expected = am.event_id(1, resolved)

    assert result != expected


def test_two_projects_never_share_an_event_id(parse):
    """Should keep identical alerts in different projects apart."""
    occurrence = parse(alert_payload())[0]

    result = am.event_id(1, occurrence)
    expected = am.event_id(2, occurrence)

    assert result != expected


def test_two_episodes_of_one_alert_never_share_an_event_id(parse):
    """Should key the event on the episode, not just the alert."""
    first = parse(alert_payload())[0]
    later = parse(alert_payload(startsAt="2026-08-04T11:00:00.000Z"))[0]

    result = am.event_id(1, first)
    expected = am.event_id(1, later)

    assert result != expected


@pytest.mark.django_db
def test_a_condition_on_a_label_selects_the_rule(parse, am_fixture, project):
    """Should route an alert by any label, not only by its alertname."""
    from pandora.issues.models import GroupingRule

    GroupingRule.objects.create(
        priority=10,
        conditions={"path": "labels.namespace", "value": "payments"},
        fingerprint=["payments-all"],
    )

    occurrences = parse(am_fixture("firing_group"))

    result = occurrences[0].fingerprint
    expected = ["payments-all"]

    assert result == expected


@pytest.mark.django_db
def test_a_rule_can_title_an_alert_from_its_annotations(parse, am_fixture, project):
    """Should let an operator write the title their runbook uses."""
    from pandora.issues.models import GroupingRule

    GroupingRule.objects.create(
        priority=10,
        conditions={"path": "labels.namespace", "value": "payments"},
        title_template="payments: {{ labels.alertname }}",
    )

    occurrences = parse(am_fixture("firing_group"))

    result = occurrences[0].title
    expected = "payments: KubePodCrashLooping"

    assert result == expected
