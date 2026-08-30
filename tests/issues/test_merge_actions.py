import datetime

import pytest
from django.utils import timezone

from pandora.issues import merge, suggest
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def make_issue(project):
    def build(digest, **overrides):
        fields = {
            "project": project,
            "fingerprint_hash": digest,
            "fingerprint": [f"alertname:{digest}"],
            "grouping_labels": {"alertname": "TargetDown", "namespace": "payments"},
            "title": f"issue {digest}",
            "culprit": "alertname=TargetDown",
            "level": issue_models.Level.WARNING,
            "environment": "p-mk1",
            "first_seen": NOW - datetime.timedelta(hours=4),
            "last_seen": NOW,
            "event_count": 2,
        }
        fields.update(overrides)
        return issue_models.Issue.objects.create(**fields)

    return build


# merging


def test_a_merge_leaves_one_issue(make_issue):
    """Should be what a person asked for when they selected two rows."""
    keeper = make_issue("a" * 64, first_seen=NOW - datetime.timedelta(days=2))
    other = make_issue("b" * 64)

    merge.merge(keeper, [other])

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_merge_adds_the_counts(make_issue):
    """Should report the volume of the fault, not of one of its shapes."""
    keeper = make_issue("a" * 64, event_count=3)
    other = make_issue("b" * 64, event_count=4)

    merge.merge(keeper, [other])

    result = issue_models.Issue.objects.get().event_count
    expected = 7

    assert result == expected


def test_the_merged_fingerprint_becomes_an_alias(make_issue):
    """Should be the half Sentry leaves out — the next occurrence lands here."""
    keeper = make_issue("a" * 64)
    other = make_issue("b" * 64)

    merge.merge(keeper, [other])

    alias = issue_models.IssueAlias.objects.get()
    result = (alias.fingerprint_hash, alias.issue_id)
    expected = ("b" * 64, keeper.pk)

    assert result == expected


def test_the_alias_remembers_what_it_used_to_be_called(make_issue):
    """Should let a person see what they merged away before unmerging it."""
    keeper = make_issue("a" * 64)
    other = make_issue("b" * 64, title="the other one")

    merge.merge(keeper, [other])

    result = issue_models.IssueAlias.objects.get().title
    expected = "the other one"

    assert result == expected


def test_a_merge_is_recorded_on_the_issue(make_issue):
    """Should show in the activity trail like every other triage decision."""
    keeper = make_issue("a" * 64)
    other = make_issue("b" * 64)

    merge.merge(keeper, [other], actor="dev")

    row = issue_models.IssueActivity.objects.get(kind=issue_models.ActivityKind.MERGED)
    result = (row.actor, row.data["fingerprints"])
    expected = ("dev", ["b" * 64])

    assert result == expected


def test_merging_an_issue_with_itself_does_nothing(make_issue):
    """Should be a no-op rather than an error when a selection overlaps."""
    keeper = make_issue("a" * 64)

    result = merge.merge(keeper, [keeper])
    expected = 0

    assert result == expected


def test_three_issues_merge_at_once(make_issue):
    """Should take a whole selection, not a pair at a time."""
    keeper = make_issue("a" * 64)
    merge.merge(keeper, [make_issue("b" * 64), make_issue("c" * 64)])

    result = issue_models.IssueAlias.objects.count()
    expected = 2

    assert result == expected


# what arrives afterwards


def test_a_later_occurrence_of_the_merged_fingerprint_lands_on_the_keeper(
    make_issue, project
):
    """Should be why the merge holds — otherwise the issue comes straight back."""
    keeper = make_issue("a" * 64)
    merge.merge(keeper, [make_issue("b" * 64)])

    result = merge.resolve_alias(project.pk, "b" * 64)

    assert result.pk == keeper.pk


def test_an_unmerged_fingerprint_resolves_to_nothing(make_issue, project):
    """Should mint its own issue again once a person changed their mind."""
    keeper = make_issue("a" * 64)
    merge.merge(keeper, [make_issue("b" * 64)])

    merge.unmerge(keeper, "b" * 64)

    result = merge.resolve_alias(project.pk, "b" * 64)

    assert result is None


def test_unmerging_something_that_was_never_merged_says_so(make_issue):
    """Should answer rather than raise when the alias has already gone."""
    keeper = make_issue("a" * 64)

    result = merge.unmerge(keeper, "b" * 64)

    assert result is False


def test_an_unmerge_is_recorded(make_issue):
    """Should leave the trail intact in both directions."""
    keeper = make_issue("a" * 64)
    merge.merge(keeper, [make_issue("b" * 64)])

    merge.unmerge(keeper, "b" * 64, actor="dev")

    result = issue_models.IssueActivity.objects.filter(
        kind=issue_models.ActivityKind.UNMERGED
    ).count()
    expected = 1

    assert result == expected


def test_an_alias_reads_as_where_it_points(make_issue):
    """Should be legible in the admin without following the id."""
    keeper = make_issue("a" * 64)
    merge.merge(keeper, [make_issue("b" * 64)])

    result = str(issue_models.IssueAlias.objects.get())
    expected = f"bbbbbbbbbbbb -> {keeper.pk}"

    assert result == expected


# the suggestion


def test_a_merge_of_one_issue_suggests_nothing(make_issue):
    """Should need at least two examples to learn anything from."""
    result = suggest.for_issues([make_issue("a" * 64)])

    assert result is None


def test_the_shared_labels_become_the_condition(make_issue):
    """Should be the rule that would have made the merge unnecessary."""
    first = make_issue("a" * 64)
    second = make_issue(
        "b" * 64,
        grouping_labels={
            "alertname": "TargetDown",
            "namespace": "payments",
            "pod": "two",
        },
    )
    issue_models.Issue.objects.filter(pk=first.pk).update(
        grouping_labels={
            "alertname": "TargetDown",
            "namespace": "payments",
            "pod": "one",
        }
    )
    first.refresh_from_db()

    result = suggest.for_issues([first, second]).shared
    expected = {"alertname": "TargetDown", "namespace": "payments"}

    assert result == expected


def test_the_differing_label_is_named(make_issue):
    """Should say which label split the issue, which is what to deny."""
    first = make_issue(
        "a" * 64, grouping_labels={"alertname": "TargetDown", "pod": "one"}
    )
    second = make_issue(
        "b" * 64, grouping_labels={"alertname": "TargetDown", "pod": "two"}
    )

    result = suggest.for_issues([first, second]).differing
    expected = ["pod"]

    assert result == expected


def test_the_suggestion_is_a_usable_rule(make_issue):
    """Should be something an operator can accept without editing."""
    first = make_issue(
        "a" * 64, grouping_labels={"alertname": "TargetDown", "pod": "one"}
    )
    second = make_issue(
        "b" * 64, grouping_labels={"alertname": "TargetDown", "pod": "two"}
    )

    fields = suggest.for_issues([first, second]).as_rule_fields()
    rule = issue_models.GroupingRule.objects.create(priority=10, **fields)

    result = (rule.conditions, rule.fingerprint)
    expected = (
        {"path": "labels.alertname", "op": "eq", "value": "TargetDown"},
        ["alertname:TargetDown"],
    )

    assert result == expected


def test_several_shared_labels_become_an_all_branch(make_issue):
    """Should not lose the narrower half of what the two had in common."""
    first = make_issue(
        "a" * 64,
        grouping_labels={"alertname": "TargetDown", "namespace": "pay", "pod": "one"},
    )
    second = make_issue(
        "b" * 64,
        grouping_labels={"alertname": "TargetDown", "namespace": "pay", "pod": "two"},
    )

    result = suggest.for_issues([first, second]).conditions

    assert "all" in result and len(result["all"]) == 2


def test_two_issues_with_nothing_in_common_suggest_nothing(make_issue):
    """Should not invent a rule from a merge a person made for their own reasons."""
    first = make_issue("a" * 64, grouping_labels={"alertname": "One"})
    second = make_issue("b" * 64, grouping_labels={"alertname": "Two"})

    result = suggest.for_issues([first, second])

    assert result is None


def test_an_sdk_issue_falls_back_to_its_tags(make_issue):
    """Should still suggest something for a door that has no grouping labels."""
    first = make_issue("a" * 64, grouping_labels={})
    second = make_issue("b" * 64, grouping_labels={})
    for issue in (first, second):
        issue_models.TagStat.objects.create(
            issue=issue, key="service", value="gateway", count=3
        )

    result = suggest.for_issues([first, second]).shared
    expected = {"service": "gateway"}

    assert result == expected
