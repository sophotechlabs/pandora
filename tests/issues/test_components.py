import datetime

import pytest

from pandora.issues import components, models

# variant maps


def test_every_level_has_a_variant():
    """Should give the label component a colour for each stored level."""
    result = sorted(components.LEVEL_VARIANTS)
    expected = sorted(models.Level.values)

    assert result == expected


def test_every_triage_state_has_a_variant():
    """Should give the label component a colour for each stored triage state."""
    result = sorted(components.TRIAGE_VARIANTS)
    expected = sorted(models.TriageState.values)

    assert result == expected


def test_every_source_state_has_a_variant():
    """Should give the label component a colour for each stored source state."""
    result = sorted(components.SOURCE_VARIANTS)
    expected = sorted(models.SourceState.values)

    assert result == expected


def test_errors_and_worse_read_as_danger():
    """Should not paint a fatal alert the same colour as an info one."""
    result = [components.LEVEL_VARIANTS[level] for level in ("info", "error", "fatal")]
    expected = ["info", "danger", "danger"]

    assert result == expected


# table primitives


def test_a_table_reports_its_own_column_count():
    """Should let the empty-state row span the table without a template count."""
    table = components.Table(
        columns=(components.Column("a"), components.Column("b")),
        rows=(),
        empty_message="none",
    )

    result = table.column_count
    expected = 2

    assert result == expected


def test_a_column_is_left_aligned_unless_it_holds_numbers():
    """Should keep the numeric flag opt-in."""
    assert components.Column("a").numeric is False
    assert components.Column("a", numeric=True).numeric is True


def test_a_cell_defaults_to_plain_text():
    """Should render as text until a caller asks for a link or a chip."""
    cell = components.Cell(text="hello")

    result = (cell.href, cell.variant, cell.external)
    expected = (None, None, False)

    assert result == expected


# duration formatting


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0m"),
        (59, "0m"),
        (60, "1m"),
        (3600, "1h 0m"),
        (5400, "1h 30m"),
        (86400, "1d 0h"),
        (183600, "2d 3h"),
    ],
)
def test_durations_read_at_the_scale_that_matters(seconds, expected):
    """Should drop precision the longer the episode ran."""
    result = components.format_duration(datetime.timedelta(seconds=seconds))

    assert result == expected


def test_a_clock_skewed_negative_duration_reads_as_zero():
    """Should never print a negative age when a stamp arrives from the future."""
    result = components.format_duration(datetime.timedelta(seconds=-90))
    expected = "0m"

    assert result == expected


# stamp formatting


def test_a_missing_stamp_renders_as_a_dash():
    """Should leave a hole visible rather than printing None."""
    result = components.format_stamp(None)
    expected = "—"

    assert result == expected


def test_a_stamp_renders_short_and_local():
    """Should keep the changelist narrow — no year, no seconds."""
    stamp = datetime.datetime(2026, 8, 4, 9, 5, tzinfo=datetime.UTC)

    result = components.format_stamp(stamp)
    expected = "Aug 04, 09:05"

    assert result == expected


# relative stamps

NOW = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s ago"),
        (45, "45s ago"),
        (60, "1m ago"),
        (3540, "59m ago"),
        (3600, "1h ago"),
        (86340, "23h ago"),
        (86400, "1d ago"),
        (6 * 86400, "6d ago"),
        (7 * 86400, "1w ago"),
        (28 * 86400, "4w ago"),
    ],
)
def test_a_relative_stamp_drops_precision_as_it_ages(seconds, expected):
    """Should read the way an operator says it, not as a date."""
    stamp = NOW - datetime.timedelta(seconds=seconds)

    result = components.format_relative(stamp, NOW)

    assert result == expected


def test_a_stamp_older_than_five_weeks_falls_back_to_the_date():
    """Should stop counting weeks once the number stops meaning anything."""
    stamp = NOW - datetime.timedelta(days=90)

    result = components.format_relative(stamp, NOW)
    expected = "May 06, 12:00"

    assert result == expected


def test_a_missing_relative_stamp_renders_as_a_dash():
    """Should leave the hole visible here too."""
    result = components.format_relative(None, NOW)
    expected = "—"

    assert result == expected


def test_a_stamp_from_the_future_reads_as_just_now():
    """Should not print a negative age when a clock is skewed."""
    stamp = NOW + datetime.timedelta(minutes=5)

    result = components.format_relative(stamp, NOW)
    expected = "just now"

    assert result == expected


# issue duration


def test_an_open_issue_is_measured_from_when_it_opened():
    """Should keep counting while the source still says firing."""
    result = components.issue_duration(
        NOW - datetime.timedelta(hours=3),
        None,
        None,
        NOW,
    )
    expected = "3h 0m"

    assert result == expected


def test_a_settled_issue_reports_its_last_episode_length():
    """Should fall back to how long the most recent episode ran."""
    result = components.issue_duration(
        None,
        NOW - datetime.timedelta(hours=2),
        NOW - datetime.timedelta(hours=1),
        NOW,
    )
    expected = "1h 0m"

    assert result == expected


def test_an_issue_with_no_episodes_reports_no_duration():
    """Should print a dash rather than guessing a window."""
    result = components.issue_duration(None, None, None, NOW)
    expected = "—"

    assert result == expected


# share arithmetic


@pytest.mark.parametrize(
    ("count", "total", "expected"),
    [(1, 4, 25), (3, 4, 75), (4, 4, 100), (0, 4, 0), (1, 0, 0), (1, -1, 0)],
)
def test_shares_are_whole_percents_of_the_group_total(count, total, expected):
    """Should size a tag bar against its own key, guarding an empty group."""
    result = components.percent_of(count, total)

    assert result == expected
