import datetime

from pandora.issues import sparkline

NOW = datetime.datetime(2026, 8, 4, 15, 30, tzinfo=datetime.UTC)
START = datetime.datetime(2026, 7, 28, 16, 0, tzinfo=datetime.UTC)


def hour(offset):
    return START + datetime.timedelta(hours=offset)


# grid configuration


def test_the_grid_is_twenty_eight_six_hour_buckets():
    """Should slice the window the way the changelist column is sized for."""
    result = (sparkline.BUCKET_COUNT, sparkline.BUCKET_HOURS)
    expected = (28, 6)

    assert result == expected


def test_the_window_covers_seven_days():
    """Should span exactly the retention slice the column advertises."""
    result = sparkline.WINDOW
    expected = datetime.timedelta(days=7)

    assert result == expected


def test_the_chart_is_as_wide_as_its_bars():
    """Should size the viewBox from the bar geometry, not a magic number."""
    result = sparkline.CHART_WIDTH
    expected = 28 * (sparkline.BAR_WIDTH + sparkline.BAR_GAP) - sparkline.BAR_GAP

    assert result == expected


# window edges


def test_the_window_ends_at_the_top_of_the_next_hour():
    """Should include the hour in progress so a fresh alert is visible."""
    result = sparkline.window_end(NOW)
    expected = datetime.datetime(2026, 8, 4, 16, 0, tzinfo=datetime.UTC)

    assert result == expected


def test_the_window_starts_seven_days_before_it_ends():
    """Should anchor the first bucket a full window back from the end."""
    result = sparkline.window_start(NOW)
    expected = START

    assert result == expected


# bucketing


def test_no_stats_bucket_to_a_flat_series():
    """Should return a full-length series even when nothing was recorded."""
    result = sparkline.buckets([], NOW)
    expected = [0] * 28

    assert result == expected


def test_the_first_hour_of_the_window_lands_in_the_first_bucket():
    """Should place the oldest retained hour at the left edge."""
    result = sparkline.buckets([(hour(0), 3)], NOW)

    assert result[0] == 3
    assert sum(result) == 3


def test_the_current_hour_lands_in_the_last_bucket():
    """Should place the newest hour at the right edge."""
    result = sparkline.buckets([(hour(167), 4)], NOW)

    assert result[-1] == 4
    assert sum(result) == 4


def test_six_hours_collapse_into_one_bucket():
    """Should sum every hour that shares a six-hour slot."""
    stats = [(hour(162 + offset), 1) for offset in range(6)]

    result = sparkline.buckets(stats, NOW)

    assert result[-1] == 6
    assert sum(result) == 6


def test_the_seventh_hour_opens_the_next_bucket():
    """Should not let a slot bleed past its six hours."""
    result = sparkline.buckets([(hour(5), 1), (hour(6), 1)], NOW)

    assert result[0] == 1
    assert result[1] == 1


def test_hours_older_than_the_window_are_dropped():
    """Should ignore stats the sparkline no longer covers."""
    result = sparkline.buckets([(hour(-1), 9)], NOW)

    assert sum(result) == 0


def test_hours_at_or_past_the_window_end_are_dropped():
    """Should ignore a clock-skewed future hour instead of overflowing."""
    result = sparkline.buckets([(hour(168), 9)], NOW)

    assert sum(result) == 0


# rendering


def test_the_render_draws_one_rect_per_bucket():
    """Should emit a fixed-width chart whatever the data looks like."""
    svg = sparkline.render([0] * 28)

    result = svg.count("<rect")
    expected = 28

    assert result == expected


def test_the_render_is_inline_svg_with_no_external_asset():
    """Should ship the whole column as markup — no image request per row."""
    svg = sparkline.render([1] * 28)

    assert svg.startswith('<span class="text-primary-600')
    assert "<svg" in svg
    assert "http" not in svg


def test_a_quiet_bucket_keeps_a_dimmed_baseline():
    """Should draw an empty slot as a faint one-unit tick, not a gap."""
    svg = sparkline.render([0] * 28)

    assert f'height="1" opacity="{sparkline.QUIET_OPACITY}"' in svg


def test_the_busiest_bucket_fills_the_chart():
    """Should scale the tallest bar to the full chart height."""
    counts = [0] * 27 + [10]

    svg = sparkline.render(counts)

    assert (
        f'height="{sparkline.CHART_HEIGHT}" opacity="{sparkline.BUSY_OPACITY}"' in svg
    )


def test_the_bars_are_scaled_against_the_peak():
    """Should draw half the peak count at about half the full bar height."""
    counts = [0] * 26 + [5, 10]

    svg = sparkline.render(counts)

    assert 'height="9"' in svg
    assert f'height="{sparkline.CHART_HEIGHT}"' in svg


def test_the_tooltip_reports_the_window_total():
    """Should let a reader hover for the number the shape only implies."""
    svg = sparkline.render([2] * 28)

    assert 'title="56 in 7 days"' in svg
