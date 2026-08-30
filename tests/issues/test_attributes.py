import pytest

from pandora.issues import attributes
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db


@pytest.fixture
def tagged(project):
    def build(issue, **pairs):
        for key, values in pairs.items():
            for value, count in values.items():
                issue_models.TagStat.objects.create(
                    issue=issue, key=key, value=value, count=count
                )
        return issue

    return build


@pytest.fixture
def other_issue(project):
    return issue_models.Issue.objects.create(
        project=project,
        fingerprint_hash="b" * 64,
        title="another",
    )


def names(rows):
    return [(row.key, row.value) for row in rows]


# what is distinguishing


def test_an_issue_with_no_tags_distinguishes_nothing(issue):
    """Should say nothing rather than inventing a signal."""
    result = attributes.distinguishing(issue)

    assert result == []


def test_a_value_that_dominates_this_issue_is_named(issue, other_issue, tagged):
    """Should be the answer to 'what is different about this one'."""
    tagged(issue, node={"broken-1": 9})
    tagged(other_issue, node={"fine-1": 5, "fine-2": 5})

    result = names(attributes.distinguishing(issue))
    expected = [("node", "broken-1")]

    assert result == expected


def test_a_value_common_across_the_project_is_not_named(issue, other_issue, tagged):
    """Should not report the obvious — every issue is on the same cluster."""
    tagged(issue, cluster={"p-mk1": 9})
    tagged(other_issue, cluster={"p-mk1": 9})

    result = attributes.distinguishing(issue)

    assert result == []


def test_a_rare_value_needs_enough_occurrences(issue, other_issue, tagged):
    """Should not call one event a pattern."""
    tagged(issue, node={"broken-1": 1})
    tagged(other_issue, node={"fine-1": 20})

    result = attributes.distinguishing(issue)

    assert result == []


def test_a_minority_value_is_not_named(issue, other_issue, tagged):
    """Should need the value to actually characterise the issue."""
    tagged(issue, node={"a": 1, "b": 1, "c": 1, "d": 1, "e": 6})
    tagged(other_issue, node={"z": 10})

    result = names(attributes.distinguishing(issue))
    expected = [("node", "e")]

    assert result == expected


def test_the_share_and_the_baseline_are_both_reported(issue, other_issue, tagged):
    """Should let a reader judge the number rather than trust it."""
    tagged(issue, node={"broken-1": 8, "fine-1": 2})
    tagged(other_issue, node={"fine-1": 10})

    row = attributes.distinguishing(issue)[0]
    result = (row.percent, row.baseline_percent)
    expected = (80, 0)

    assert result == expected


def test_the_overflow_bucket_is_never_reported(issue, other_issue, tagged):
    """Should not say `<other>` distinguishes anything — it is a placeholder."""
    tagged(issue, request_id={issue_models.TAG_OVERFLOW_VALUE: 40})
    tagged(other_issue, request_id={"a": 5})

    result = attributes.distinguishing(issue)

    assert result == []


def test_a_capped_key_is_marked_as_sampled(issue, other_issue, tagged):
    """Should say the number came from a sample, which is what makes it honest."""
    tagged(
        issue,
        node={"broken-1": 9, issue_models.TAG_OVERFLOW_VALUE: 1},
    )
    tagged(other_issue, node={"fine-1": 10})

    result = attributes.distinguishing(issue)[0].sampled

    assert result is True


def test_an_uncapped_key_is_not_marked_as_sampled(issue, other_issue, tagged):
    """Should not put the caveat on a breakdown that is complete."""
    tagged(issue, node={"broken-1": 9})
    tagged(other_issue, node={"fine-1": 10})

    result = attributes.distinguishing(issue)[0].sampled

    assert result is False


def test_the_strongest_signal_comes_first(issue, other_issue, tagged):
    """Should put the most distinguishing value at the top of a short list."""
    tagged(issue, node={"broken-1": 10}, region={"eu": 6, "us": 4})
    tagged(other_issue, node={"fine-1": 10}, region={"eu": 5, "us": 5})

    result = names(attributes.distinguishing(issue))[0]
    expected = ("node", "broken-1")

    assert result == expected


def test_an_issue_alone_in_its_project_still_reports(issue, tagged):
    """Should work on the first issue an install ever sees."""
    tagged(issue, node={"broken-1": 9})

    result = names(attributes.distinguishing(issue))
    expected = [("node", "broken-1")]

    assert result == expected


def test_the_list_is_bounded(issue, other_issue, tagged):
    """Should stay a panel, not a report."""
    for index in range(20):
        tagged(issue, **{f"key{index}": {"only": 5}})
    tagged(other_issue, node={"fine": 5})

    result = len(attributes.distinguishing(issue))
    expected = attributes.LIMIT

    assert result == expected


def test_a_key_whose_counts_are_all_zero_is_skipped(issue, tagged):
    """Should not divide by nothing when a breakdown row was zeroed."""
    tagged(issue, node={"broken-1": 0})

    result = attributes.distinguishing(issue)

    assert result == []
