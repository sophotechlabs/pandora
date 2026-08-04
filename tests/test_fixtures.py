import pathlib

import pytest

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "am"
NAMES = (
    "firing_group",
    "resolved_group",
    "mixed_group",
    "repeat_delivery",
    "resolved_unknown",
    "truncated",
)
NEVER_FIRED = "0001-01-01T00:00:00Z"

# fixture inventory


def test_every_scenario_the_ingest_engine_must_handle_has_a_fixture():
    """Should ship one fixture per Alertmanager delivery shape Phase 1 handles."""
    result = sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))
    expected = sorted(NAMES)

    assert result == expected


# payload contract


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_is_payload_version_four(am_fixture, name):
    """Should pin the Alertmanager webhook version the translator asserts on."""
    result = am_fixture(name)["version"]
    expected = "4"

    assert result == expected


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_carries_the_group_envelope(am_fixture, name):
    """Should carry every group-level key the v4 webhook sends."""
    result = sorted(am_fixture(name))
    expected = sorted(
        [
            "version",
            "groupKey",
            "truncatedAlerts",
            "status",
            "receiver",
            "groupLabels",
            "commonLabels",
            "commonAnnotations",
            "externalURL",
            "alerts",
        ]
    )

    assert result == expected


@pytest.mark.parametrize("name", NAMES)
def test_every_alert_carries_the_per_alert_fields(am_fixture, name):
    """Should carry labels, annotations, timestamps, generatorURL and fingerprint."""
    result = [sorted(alert) for alert in am_fixture(name)["alerts"]]
    expected = [
        sorted(
            [
                "status",
                "labels",
                "annotations",
                "startsAt",
                "endsAt",
                "generatorURL",
                "fingerprint",
            ]
        )
    ] * len(result)

    assert result == expected


@pytest.mark.parametrize("name", NAMES)
def test_every_fingerprint_is_alertmanager_shaped(am_fixture, name):
    """Should use Alertmanager's 16-hex per-label-set fingerprint."""
    result = [len(alert["fingerprint"]) for alert in am_fixture(name)["alerts"]]
    expected = [16] * len(result)

    assert result == expected


# scenario contract


def test_a_firing_group_has_no_end_timestamps(am_fixture):
    """Should leave endsAt at Go's zero time while an alert is firing."""
    result = [alert["endsAt"] for alert in am_fixture("firing_group")["alerts"]]
    expected = [NEVER_FIRED, NEVER_FIRED]

    assert result == expected


def test_a_repeat_delivery_repeats_the_identity_of_the_firing_group(am_fixture):
    """Should resend the same fingerprints and starts so only counters move."""
    identity = [
        (alert["fingerprint"], alert["startsAt"])
        for alert in am_fixture("firing_group")["alerts"]
    ]

    result = [
        (alert["fingerprint"], alert["startsAt"])
        for alert in am_fixture("repeat_delivery")["alerts"]
    ]
    expected = identity

    assert result == expected


def test_a_resolved_group_closes_the_same_episodes_the_firing_group_opened(am_fixture):
    """Should resolve the exact episodes the firing fixture created."""
    opened = [
        (alert["fingerprint"], alert["startsAt"])
        for alert in am_fixture("firing_group")["alerts"]
    ]

    result = [
        (alert["fingerprint"], alert["startsAt"])
        for alert in am_fixture("resolved_group")["alerts"]
    ]
    expected = opened

    assert result == expected
    assert all(
        alert["endsAt"] != NEVER_FIRED
        for alert in am_fixture("resolved_group")["alerts"]
    )


def test_a_mixed_group_carries_one_of_each_status(am_fixture):
    """Should exercise a group where one alert resolves while another fires."""
    result = sorted(alert["status"] for alert in am_fixture("mixed_group")["alerts"])
    expected = ["firing", "resolved"]

    assert result == expected


def test_the_unknown_resolution_matches_no_other_fixture(am_fixture):
    """Should resolve an episode pandora never saw open."""
    seen = {
        alert["fingerprint"]
        for name in ("firing_group", "mixed_group", "truncated")
        for alert in am_fixture(name)["alerts"]
    }

    result = [
        alert["fingerprint"] in seen
        for alert in am_fixture("resolved_unknown")["alerts"]
    ]
    expected = [False]

    assert result == expected


def test_the_truncated_fixture_reports_dropped_alerts(am_fixture):
    """Should carry a non-zero truncatedAlerts so the log path is exercised."""
    result = am_fixture("truncated")["truncatedAlerts"]
    expected = 7

    assert result == expected
