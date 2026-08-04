from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

BUCKET_HOURS = 6
BUCKET_COUNT = 28
WINDOW = timedelta(hours=BUCKET_HOURS * BUCKET_COUNT)

BAR_WIDTH = 3
BAR_GAP = 1
CHART_HEIGHT = 18
CHART_WIDTH = BUCKET_COUNT * (BAR_WIDTH + BAR_GAP) - BAR_GAP

QUIET_OPACITY = "0.25"
BUSY_OPACITY = "0.9"


def window_end(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def window_start(now: datetime) -> datetime:
    return window_end(now) - WINDOW


def buckets(stats: Iterable[tuple[datetime, int]], now: datetime) -> list[int]:
    start = window_start(now)
    end = window_end(now)
    counts = [0] * BUCKET_COUNT
    for hour, count in stats:
        if hour < start:
            continue
        if hour >= end:
            continue
        offset = int((hour - start).total_seconds() // 3600)
        counts[offset // BUCKET_HOURS] += count
    return counts


def _bar(index: int, count: int, peak: int) -> tuple[int, int, int, int, str]:
    if peak == 0:
        height = 1
    else:
        height = 1 + round(count * (CHART_HEIGHT - 1) / peak)

    if count == 0:
        opacity = QUIET_OPACITY
    else:
        opacity = BUSY_OPACITY

    x = index * (BAR_WIDTH + BAR_GAP)
    y = CHART_HEIGHT - height
    return (x, y, BAR_WIDTH, height, opacity)


def render(counts: Sequence[int]) -> SafeString:
    peak = max(counts, default=0)
    bars = [_bar(index, count, peak) for index, count in enumerate(counts)]
    rects = format_html_join(
        "",
        '<rect x="{}" y="{}" width="{}" height="{}" opacity="{}"></rect>',
        bars,
    )
    return format_html(
        '<span class="text-primary-600 dark:text-primary-500" title="{} in 7 days">'
        '<svg width="{}" height="{}" viewBox="0 0 {} {}" fill="currentColor"'
        ' role="img" aria-label="7 day activity">{}</svg></span>',
        sum(counts),
        CHART_WIDTH,
        CHART_HEIGHT,
        CHART_WIDTH,
        CHART_HEIGHT,
        rects,
    )
