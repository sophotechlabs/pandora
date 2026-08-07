import pytest

from pandora.issues import models

pytestmark = pytest.mark.django_db

# seeded rule contract


def test_a_single_default_rule_ships_with_the_schema():
    """Should seed exactly one grouping rule in the initial migration."""
    result = models.GroupingRule.objects.count()
    expected = 1

    assert result == expected


def test_the_default_rule_is_a_denylist():
    """Should default to denylist — it fails loud (too many issues), not silent."""
    rule = models.GroupingRule.objects.get()

    result = rule.mode
    expected = "denylist"

    assert result == expected


def test_the_default_rule_drops_the_per_instance_labels():
    """Should strip the labels that make one alert look like many issues."""
    rule = models.GroupingRule.objects.get()

    result = sorted(rule.labels)
    expected = sorted(
        [
            "pod",
            "instance",
            "container",
            "endpoint",
            "replicaset",
            "uid",
            "node",
            "job_name",
        ]
    )

    assert result == expected


def test_the_default_rule_drops_the_kube_job_run_name():
    """Should hide job_name — every CronJob run carries a fresh one."""
    rule = models.GroupingRule.objects.get()

    result = "job_name" in rule.labels

    assert result is True


def test_the_default_rule_matches_every_project_and_alertname():
    """Should apply everywhere until a narrower rule outranks it."""
    rule = models.GroupingRule.objects.get()

    result = {
        "project": rule.project_id,
        "alertname_regex": rule.alertname_regex,
        "active": rule.active,
    }
    expected = {"project": None, "alertname_regex": "", "active": True}

    assert result == expected


def test_the_default_rule_sorts_last():
    """Should carry the weakest priority so any custom rule wins."""
    rule = models.GroupingRule.objects.get()
    sharper = models.GroupingRule.objects.create(
        priority=10,
        mode=models.GroupingMode.ALLOWLIST,
        alertname_regex="^Kube",
    )

    result = list(models.GroupingRule.objects.values_list("pk", flat=True))
    expected = [sharper.pk, rule.pk]

    assert result == expected
