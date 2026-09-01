"""A real Alertmanager, its own webhook, pandora's episode lifecycle."""

import pytest

from pandora.issues import models as issue_models

pytestmark = pytest.mark.live


def alert_issue():
    found = issue_models.Issue.objects.filter(title__icontains="LiveTargetDown")
    assert found.exists(), "Alertmanager's webhook produced no issue"
    return found.first()


def test_a_real_alertmanager_webhook_became_an_issue():
    """Should be the half already running on p-mk1, proven in isolation here."""
    assert alert_issue() is not None


def test_each_firing_replica_opened_its_own_episode():
    """Should give every alert a lifecycle object, which no error tracker has."""
    result = issue_models.Episode.objects.filter(issue=alert_issue()).count()
    expected = 6

    assert result == expected


def test_the_grouping_rule_dropped_the_pod_label():
    """Should not mint an issue per pod — the seeded denylist covers it."""
    issue = alert_issue()

    result = issue.grouping_labels

    assert "pod" not in result


def test_the_pod_survives_as_a_tag():
    """Should keep every replica for filtering even though grouping ignores it."""
    result = set(
        issue_models.TagStat.objects.filter(issue=alert_issue(), key="pod").values_list(
            "value", flat=True
        )
    )
    expected = {f"checkout-7d9f-{index}" for index in range(6)}

    assert result == expected


def test_the_six_replicas_are_one_issue():
    """Should be the denylist doing its job: one issue, six episodes."""
    result = issue_models.Issue.objects.filter(
        title__icontains="LiveTargetDown"
    ).count()
    expected = 1

    assert result == expected


def test_the_resolved_alerts_closed_every_episode():
    """Should close on Alertmanager's own resolved delivery, not on a timer."""
    result = issue_models.Episode.objects.filter(
        issue=alert_issue(), ends_at__isnull=True
    ).count()
    expected = 0

    assert result == expected
