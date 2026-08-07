import hashlib
import json

import pytest

from pandora.core import models as core_models
from pandora.issues import grouping, models

LABELS = {
    "alertname": "KubePodCrashLooping",
    "namespace": "payments",
    "pod": "ledger-7d9f4c8b6d-hk2mp",
    "container": "ledger",
    "severity": "critical",
    "cluster": "p-mk1",
}
GROUPED = {
    "alertname": "KubePodCrashLooping",
    "namespace": "payments",
    "severity": "critical",
    "cluster": "p-mk1",
}
KUBE_JOB_FAILED = {
    "alertname": "KubeJobFailed",
    "cluster": "p-mk1",
    "condition": "true",
    "container": "kube-state-metrics",
    "endpoint": "http",
    "instance": "10.42.0.31:8080",
    "job": "kube-state-metrics",
    "job_name": "pandora-replay-29766737",
    "namespace": "pandora",
    "pod": "kube-state-metrics-6d8f4c9b7d-2xkzq",
    "prometheus": "monitoring/kube-prometheus-stack",
    "service": "kube-state-metrics",
    "severity": "warning",
}


def denylist(*labels):
    return models.GroupingRule(mode=models.GroupingMode.DENYLIST, labels=list(labels))


def allowlist(*labels):
    return models.GroupingRule(mode=models.GroupingMode.ALLOWLIST, labels=list(labels))


# configuration


def test_the_module_default_matches_the_rule_the_migration_seeds(db):
    """Should keep the in-code fallback identical to the seeded denylist."""
    seeded = models.GroupingRule.objects.get()

    result = sorted(grouping.DEFAULT_DENY_LABELS)
    expected = sorted(seeded.labels)

    assert result == expected


def test_the_module_default_matches_the_seeded_priority(db):
    """Should keep the fallback as weak as the seeded rule, never stronger."""
    seeded = models.GroupingRule.objects.get()

    result = grouping.DEFAULT_PRIORITY
    expected = seeded.priority

    assert result == expected


def test_the_fallback_rule_is_a_denylist():
    """Should fall back to denylist grouping — it fails loud, not silent."""
    result = grouping.default_rule().mode
    expected = models.GroupingMode.DENYLIST

    assert result == expected


def test_title_and_culprit_are_capped_to_the_column_widths():
    """Should cap derived text at the width the schema gives those columns."""
    result = (grouping.TITLE_MAX, grouping.CULPRIT_MAX)
    expected = (
        models.Issue._meta.get_field("title").max_length,
        models.Issue._meta.get_field("culprit").max_length,
    )

    assert result == expected


# rule resolution


def test_the_seeded_rule_applies_when_nothing_sharper_exists(project):
    """Should reach for the global seeded rule when no rule targets the alert."""
    result = grouping.resolve_rule(project, "KubePodCrashLooping").pk
    expected = models.GroupingRule.objects.get().pk

    assert result == expected


def test_a_lower_priority_number_outranks_the_seeded_rule(project):
    """Should let a sharper rule win — priority ascends, lowest first."""
    sharper = models.GroupingRule.objects.create(
        priority=10,
        mode=models.GroupingMode.ALLOWLIST,
        labels=["alertname"],
    )

    result = grouping.resolve_rule(project, "KubePodCrashLooping").pk
    expected = sharper.pk

    assert result == expected


def test_a_rule_only_matches_its_own_alertname_pattern(project):
    """Should skip a rule whose alertname_regex does not match the alert."""
    models.GroupingRule.objects.create(priority=10, alertname_regex="^Node")

    result = grouping.resolve_rule(project, "KubePodCrashLooping").priority
    expected = grouping.DEFAULT_PRIORITY

    assert result == expected


def test_a_matching_pattern_wins(project):
    """Should pick the rule whose alertname_regex matches."""
    matching = models.GroupingRule.objects.create(priority=10, alertname_regex="^Kube")

    result = grouping.resolve_rule(project, "KubePodCrashLooping").pk
    expected = matching.pk

    assert result == expected


def test_an_inactive_rule_never_matches(project):
    """Should ignore a rule an operator switched off in the admin."""
    models.GroupingRule.objects.create(priority=10, active=False)

    result = grouping.resolve_rule(project, "KubePodCrashLooping").priority
    expected = grouping.DEFAULT_PRIORITY

    assert result == expected


def test_a_rule_scoped_to_another_project_never_matches(project):
    """Should keep one project's grouping rules out of another's fingerprints."""
    other = core_models.Project.objects.create(slug="other", name="Other")
    models.GroupingRule.objects.create(priority=10, project=other)

    result = grouping.resolve_rule(project, "KubePodCrashLooping").priority
    expected = grouping.DEFAULT_PRIORITY

    assert result == expected


def test_a_project_rule_applies_to_its_own_project(project):
    """Should apply a project-scoped rule to that project's alerts."""
    scoped = models.GroupingRule.objects.create(priority=10, project=project)

    result = grouping.resolve_rule(project, "KubePodCrashLooping").pk
    expected = scoped.pk

    assert result == expected


def test_an_unparsable_pattern_is_skipped_and_logged(project, caplog):
    """Should survive an operator typing a broken regex into the admin."""
    models.GroupingRule.objects.create(priority=10, alertname_regex="(unclosed")

    with caplog.at_level("WARNING"):
        result = grouping.resolve_rule(project, "KubePodCrashLooping").priority
    expected = grouping.DEFAULT_PRIORITY

    assert result == expected
    assert "invalid alertname_regex" in caplog.text


def test_resolution_reuses_rules_the_caller_already_loaded(project):
    """Should accept a preloaded rule list so a group costs one query."""
    rules = [models.GroupingRule(priority=5, alertname_regex="^Kube")]

    result = grouping.resolve_rule(project, "KubePodCrashLooping", rules).priority
    expected = 5

    assert result == expected


def test_grouping_still_works_with_no_rules_at_all():
    """Should fall back to the built-in denylist if every rule is switched off."""
    result = grouping.match_rule("KubePodCrashLooping", []).labels
    expected = list(grouping.DEFAULT_DENY_LABELS)

    assert result == expected


def test_loading_rules_leaves_out_other_projects(project):
    """Should load the global rules plus this project's, and nothing else."""
    other = core_models.Project.objects.create(slug="other", name="Other")
    models.GroupingRule.objects.create(priority=20, project=other)
    mine = models.GroupingRule.objects.create(priority=10, project=project)

    result = [rule.pk for rule in grouping.load_rules(project)]
    expected = [mine.pk, models.GroupingRule.objects.get(project=None).pk]

    assert result == expected


# label filtering


def test_a_denylist_drops_only_the_labels_it_names():
    """Should keep every label the rule does not deny."""
    result = grouping.surviving_labels(denylist("pod", "container"), LABELS)
    expected = GROUPED

    assert result == expected


def test_an_allowlist_keeps_only_the_labels_it_names():
    """Should keep exactly the allowed labels, dropping everything else."""
    result = grouping.surviving_labels(allowlist("alertname", "namespace"), LABELS)
    expected = {"alertname": "KubePodCrashLooping", "namespace": "payments"}

    assert result == expected


def test_an_empty_denylist_keeps_every_label():
    """Should group on the full label set when the rule denies nothing."""
    result = grouping.surviving_labels(denylist(), LABELS)
    expected = LABELS

    assert result == expected


def test_an_empty_allowlist_keeps_nothing():
    """Should collapse to one issue when the rule allows no label at all."""
    result = grouping.surviving_labels(allowlist(), LABELS)
    expected = {}

    assert result == expected


def test_label_values_are_coerced_to_text():
    """Should carry non-string label values as text — JSON payloads vary."""
    result = grouping.surviving_labels(denylist(), {"replicas": 3})
    expected = {"replicas": "3"}

    assert result == expected


# fingerprints


def test_the_fingerprint_puts_the_alertname_first():
    """Should lead with the alertname so the identity reads alert-first."""
    result = grouping.compute_fingerprint(denylist("pod", "container"), LABELS)
    expected = [
        "alertname:KubePodCrashLooping",
        "cluster:p-mk1",
        "namespace:payments",
        "severity:critical",
    ]

    assert result == expected


def test_the_fingerprint_ignores_label_order():
    """Should hash the same identity no matter how Alertmanager ordered labels."""
    reversed_labels = dict(reversed(list(LABELS.items())))

    result = grouping.compute_fingerprint(denylist("pod"), reversed_labels)
    expected = grouping.compute_fingerprint(denylist("pod"), LABELS)

    assert result == expected


def test_the_fingerprint_survives_a_missing_alertname():
    """Should still group alerts that carry no alertname label."""
    result = grouping.compute_fingerprint(denylist(), {"job": "node-exporter"})
    expected = ["job:node-exporter"]

    assert result == expected


def test_the_hash_is_sha256_over_the_json_fingerprint():
    """Should hash the fingerprint the way seeded demo issues are hashed."""
    fingerprint = ["alertname:TargetDown", "namespace:monitoring"]

    result = grouping.fingerprint_hash(fingerprint)
    expected = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True).encode()
    ).hexdigest()

    assert result == expected


def test_two_label_sets_that_differ_only_in_denied_labels_share_an_issue():
    """Should give two crash-looping pods one fingerprint — the whole point."""
    other_pod = {**LABELS, "pod": "ledger-7d9f4c8b6d-x4rtq"}
    rule = denylist("pod", "container")

    result = grouping.fingerprint_hash(grouping.compute_fingerprint(rule, other_pod))
    expected = grouping.fingerprint_hash(grouping.compute_fingerprint(rule, LABELS))

    assert result == expected


def test_two_failed_runs_of_one_cronjob_share_an_issue():
    """Should hide job_name — a fresh run name per failure minted a new issue."""
    later_run = {**KUBE_JOB_FAILED, "job_name": "pandora-replay-29766797"}
    rule = grouping.default_rule()

    result = grouping.compute_fingerprint(rule, later_run)
    expected = grouping.compute_fingerprint(rule, KUBE_JOB_FAILED)

    assert result == expected


def test_the_cronjob_run_name_never_reaches_the_grouping_labels():
    """Should keep the run name out of the identity the silence matchers use."""
    result = grouping.surviving_labels(grouping.default_rule(), KUBE_JOB_FAILED)

    assert "job_name" not in result


def test_a_different_severity_is_a_different_issue():
    """Should split severities — severity survives the default denylist."""
    warning = {**LABELS, "severity": "warning"}
    rule = denylist("pod", "container")

    result = grouping.fingerprint_hash(grouping.compute_fingerprint(rule, warning))
    expected = grouping.fingerprint_hash(grouping.compute_fingerprint(rule, LABELS))

    assert result != expected


# derived text


def test_the_title_joins_the_alertname_and_the_summary():
    """Should read as the alert name followed by its human summary."""
    result = grouping.derive_title(GROUPED, "Pod is crash looping.")
    expected = "KubePodCrashLooping: Pod is crash looping."

    assert result == expected


def test_the_title_falls_back_to_the_alertname():
    """Should use the bare alertname when the rule ships no summary."""
    result = grouping.derive_title(GROUPED, "")
    expected = "KubePodCrashLooping"

    assert result == expected


def test_the_title_falls_back_to_the_summary():
    """Should use the summary when the labels carry no alertname."""
    result = grouping.derive_title({"job": "node-exporter"}, "target is down")
    expected = "target is down"

    assert result == expected


def test_the_title_falls_back_to_the_grouping_labels():
    """Should describe the group by its labels when nothing else names it."""
    result = grouping.derive_title({"job": "node-exporter"}, "")
    expected = "job=node-exporter"

    assert result == expected


def test_a_group_with_no_labels_still_gets_a_title():
    """Should never store an empty title, whatever the payload looked like."""
    result = grouping.derive_title({}, "")
    expected = grouping.UNLABELLED_TITLE

    assert result == expected


def test_a_long_title_is_cut_to_the_column_width():
    """Should never overflow the title column on a chatty summary."""
    result = len(grouping.derive_title(GROUPED, "x" * 900))
    expected = grouping.TITLE_MAX

    assert result == expected


def test_the_culprit_reads_as_the_grouping_labels():
    """Should render the grouping identity the way seeded issues render it."""
    result = grouping.derive_culprit(GROUPED)
    expected = (
        "alertname=KubePodCrashLooping cluster=p-mk1 "
        "namespace=payments severity=critical"
    )

    assert result == expected


def test_a_long_culprit_is_cut_to_the_column_width():
    """Should never overflow the culprit column on a wide label set."""
    labels = {f"label{index:03d}": "x" * 40 for index in range(30)}

    result = len(grouping.derive_culprit(labels))
    expected = grouping.CULPRIT_MAX

    assert result == expected


@pytest.mark.parametrize(
    "labels",
    [
        {},
        {"alertname": "TargetDown"},
        {"job": "node-exporter"},
    ],
)
def test_the_culprit_never_leads_with_a_separator(labels):
    """Should build the culprit without stray spaces on any label shape."""
    result = grouping.derive_culprit(labels)

    assert result == result.strip()
