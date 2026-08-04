import datetime

import pytest

from pandora.issues import aggregates, models

MOMENT = datetime.datetime(2026, 8, 4, 9, 12, 41, 123000, tzinfo=datetime.UTC)
HOUR = datetime.datetime(2026, 8, 4, 9, tzinfo=datetime.UTC)

pytestmark = pytest.mark.django_db


def fill_tag_key(issue, key, count):
    models.TagStat.objects.bulk_create(
        models.TagStat(issue=issue, key=key, value=f"value-{index:04d}", count=1)
        for index in range(count)
    )


def episode_at(issue, minutes, labels, ends=True):
    starts_at = MOMENT + datetime.timedelta(minutes=minutes)
    ends_at = None
    if ends:
        ends_at = starts_at + datetime.timedelta(minutes=5)
    return models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint=f"{minutes:016d}",
        labels=labels,
        starts_at=starts_at,
        ends_at=ends_at,
        last_delivery_at=starts_at,
    )


# configuration


def test_the_key_and_value_caps_match_the_columns():
    """Should truncate tags to exactly what the schema can hold."""
    result = (aggregates.KEY_MAX, aggregates.VALUE_MAX)
    expected = (
        models.TagStat._meta.get_field("key").max_length,
        models.TagStat._meta.get_field("value").max_length,
    )

    assert result == expected


# hour buckets


def test_an_hour_bucket_drops_everything_below_the_hour():
    """Should floor a timestamp to its hour so sparkline bars line up."""
    result = aggregates.hour_of(MOMENT)
    expected = HOUR

    assert result == expected


def test_a_first_occurrence_opens_the_hour_bucket(issue):
    """Should create the bucket the first time an issue fires in that hour."""
    aggregates.count_occurrence(issue, MOMENT, {})

    result = [(stat.hour, stat.count) for stat in issue.hourly_stats.all()]
    expected = [(HOUR, 1)]

    assert result == expected


def test_a_second_occurrence_increments_the_same_bucket(issue):
    """Should add to the bucket rather than opening a second row for the hour."""
    aggregates.count_occurrence(issue, MOMENT, {})
    aggregates.count_occurrence(issue, MOMENT + datetime.timedelta(minutes=20), {})

    result = [(stat.hour, stat.count) for stat in issue.hourly_stats.all()]
    expected = [(HOUR, 2)]

    assert result == expected


def test_a_later_hour_opens_its_own_bucket(issue):
    """Should keep one row per hour so the sparkline has shape."""
    aggregates.count_occurrence(issue, MOMENT, {})
    aggregates.count_occurrence(issue, MOMENT + datetime.timedelta(hours=2), {})

    result = sorted(stat.hour for stat in issue.hourly_stats.all())
    expected = [HOUR, HOUR + datetime.timedelta(hours=2)]

    assert result == expected


def test_hour_buckets_stay_inside_one_issue(issue, project):
    """Should never leak one issue's counts into another's sparkline."""
    other = models.Issue.objects.create(
        project=project, fingerprint_hash="b" * 64, title="other"
    )
    aggregates.count_occurrence(issue, MOMENT, {})
    aggregates.count_occurrence(other, MOMENT, {})

    result = [stat.count for stat in issue.hourly_stats.all()]
    expected = [1]

    assert result == expected


# tag distribution


def test_a_first_tag_value_opens_a_row(issue):
    """Should record the label values grouping threw away."""
    aggregates.count_occurrence(issue, MOMENT, {"pod": "ledger-abc"})

    result = [(stat.key, stat.value, stat.count) for stat in issue.tag_stats.all()]
    expected = [("pod", "ledger-abc", 1)]

    assert result == expected


def test_a_repeated_tag_value_increments_its_row(issue):
    """Should count how often each value shows up, for the sidebar bars."""
    aggregates.count_occurrence(issue, MOMENT, {"pod": "ledger-abc"})
    aggregates.count_occurrence(issue, MOMENT, {"pod": "ledger-abc"})

    result = issue.tag_stats.get(key="pod").count
    expected = 2

    assert result == expected


def test_a_long_tag_value_is_cut_to_the_column(issue):
    """Should never overflow the value column on a chatty label."""
    aggregates.count_occurrence(issue, MOMENT, {"pod": "x" * 900})

    result = len(issue.tag_stats.get(key="pod").value)
    expected = aggregates.VALUE_MAX

    assert result == expected


def test_a_long_tag_key_is_cut_to_the_column(issue):
    """Should never overflow the key column on a chatty label name."""
    aggregates.count_occurrence(issue, MOMENT, {"k" * 900: "value"})

    result = len(issue.tag_stats.get().key)
    expected = aggregates.KEY_MAX

    assert result == expected


def test_a_new_value_past_the_cap_lands_in_the_overflow_bucket(issue):
    """Should stop one runaway label from filling the table with rows."""
    fill_tag_key(issue, "pod", models.TAG_VALUE_CAP)

    aggregates.count_occurrence(issue, MOMENT, {"pod": "one-pod-too-many"})

    result = issue.tag_stats.get(value=models.TAG_OVERFLOW_VALUE).count
    expected = 1

    assert result == expected


def test_the_overflow_bucket_keeps_counting(issue):
    """Should keep totalling the tail once the cap is reached."""
    fill_tag_key(issue, "pod", models.TAG_VALUE_CAP)

    aggregates.count_occurrence(issue, MOMENT, {"pod": "extra-one"})
    aggregates.count_occurrence(issue, MOMENT, {"pod": "extra-two"})

    result = issue.tag_stats.get(value=models.TAG_OVERFLOW_VALUE).count
    expected = 2

    assert result == expected


def test_a_known_value_still_counts_past_the_cap(issue):
    """Should keep updating values already tracked when the key is full."""
    fill_tag_key(issue, "pod", models.TAG_VALUE_CAP)

    aggregates.count_occurrence(issue, MOMENT, {"pod": "value-0001"})

    result = issue.tag_stats.get(key="pod", value="value-0001").count
    expected = 2

    assert result == expected


def test_the_cap_is_per_label_key(issue):
    """Should not punish a second label because the first one exploded."""
    fill_tag_key(issue, "pod", models.TAG_VALUE_CAP)

    aggregates.count_occurrence(issue, MOMENT, {"namespace": "payments"})

    result = issue.tag_stats.get(key="namespace").value
    expected = "payments"

    assert result == expected


# rebuilds


def test_a_rebuild_counts_one_bucket_per_episode(issue):
    """Should derive the sparkline from the permanent episode history."""
    episodes = [
        episode_at(issue, 0, {"pod": "a"}),
        episode_at(issue, 30, {"pod": "b"}),
        episode_at(issue, 120, {"pod": "a"}),
    ]

    aggregates.rebuild(issue, episodes)

    result = sorted((stat.hour, stat.count) for stat in issue.hourly_stats.all())
    expected = [(HOUR, 2), (HOUR + datetime.timedelta(hours=2), 1)]

    assert result == expected


def test_a_rebuild_counts_tag_values_across_episodes(issue):
    """Should rebuild the tag distribution from the labels episodes kept."""
    episodes = [
        episode_at(issue, 0, {"pod": "a", "namespace": "payments"}),
        episode_at(issue, 30, {"pod": "b", "namespace": "payments"}),
    ]

    aggregates.rebuild(issue, episodes)

    result = sorted(
        (stat.key, stat.value, stat.count) for stat in issue.tag_stats.all()
    )
    expected = [
        ("namespace", "payments", 2),
        ("pod", "a", 1),
        ("pod", "b", 1),
    ]

    assert result == expected


def test_a_rebuild_clears_what_the_old_grouping_left(issue):
    """Should replace the aggregates outright, never merge into stale rows."""
    aggregates.count_occurrence(issue, MOMENT, {"pod": "gone"})

    aggregates.rebuild(issue, [episode_at(issue, 0, {"pod": "kept"})])

    result = [stat.value for stat in issue.tag_stats.all()]
    expected = ["kept"]

    assert result == expected


def test_a_rebuild_of_nothing_leaves_nothing(issue):
    """Should empty the aggregates for an issue that lost all its episodes."""
    aggregates.count_occurrence(issue, MOMENT, {"pod": "gone"})

    aggregates.rebuild(issue, [])

    result = (issue.tag_stats.count(), issue.hourly_stats.count())
    expected = (0, 0)

    assert result == expected


def test_a_rebuild_folds_the_tail_into_the_overflow_bucket(issue):
    """Should apply the same cap on rebuild as on the ingest path."""
    episodes = [
        episode_at(issue, index, {"pod": f"pod-{index:04d}"})
        for index in range(models.TAG_VALUE_CAP + 5)
    ]

    aggregates.rebuild(issue, episodes)

    result = issue.tag_stats.filter(key="pod").count()
    expected = models.TAG_VALUE_CAP

    assert result == expected


def test_the_rebuilt_overflow_bucket_carries_the_tail_total(issue):
    """Should keep the dropped values visible as a single total."""
    episodes = [
        episode_at(issue, index, {"pod": f"pod-{index:04d}"})
        for index in range(models.TAG_VALUE_CAP + 5)
    ]

    aggregates.rebuild(issue, episodes)

    result = issue.tag_stats.get(key="pod", value=models.TAG_OVERFLOW_VALUE).count
    expected = 6

    assert result == expected


def test_a_rebuild_keeps_the_most_frequent_values(issue):
    """Should drop the long tail, not the values worth looking at."""
    episodes = [
        episode_at(issue, index, {"pod": f"pod-{index:04d}"})
        for index in range(models.TAG_VALUE_CAP + 5)
    ]
    episodes.append(episode_at(issue, 9000, {"pod": "pod-0000"}))

    aggregates.rebuild(issue, episodes)

    result = issue.tag_stats.get(key="pod", value="pod-0000").count
    expected = 2

    assert result == expected
