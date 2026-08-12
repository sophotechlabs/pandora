from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Bar:
    x: int
    y: int
    width: int
    height: int
    opacity: str


def window_end(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def window_start(now: datetime) -> datetime:
    return window_end(now) - WINDOW


def start_of(now: datetime, window: timedelta) -> datetime:
    return window_end(now) - window


def counts(
    stats: Iterable[tuple[datetime, int]],
    now: datetime,
    window: timedelta = WINDOW,
    bucket_count: int = BUCKET_COUNT,
) -> list[int]:
    start = start_of(now, window)
    end = window_end(now)
    bucket_hours = int(window.total_seconds() // 3600) // bucket_count
    series = [0] * bucket_count
    for hour, count in stats:
        if hour < start:
            continue
        if hour >= end:
            continue
        offset = int((hour - start).total_seconds() // 3600)
        series[min(offset // bucket_hours, bucket_count - 1)] += count
    return series


def buckets(stats: Iterable[tuple[datetime, int]], now: datetime) -> list[int]:
    return counts(stats, now)


def bars(
    series: Sequence[int],
    height: int = CHART_HEIGHT,
    width: int = BAR_WIDTH,
    gap: int = BAR_GAP,
) -> list[Bar]:
    peak = max(series, default=0)
    return [
        _bar(index, count, peak, height=height, width=width, gap=gap)
        for index, count in enumerate(series)
    ]


def chart_width(bucket_count: int, width: int = BAR_WIDTH, gap: int = BAR_GAP) -> int:
    return bucket_count * (width + gap) - gap


def _bar(
    index: int, count: int, peak: int, *, height: int, width: int, gap: int
) -> Bar:
    if peak == 0:
        bar_height = 1
    else:
        bar_height = 1 + round(count * (height - 1) / peak)

    if count == 0:
        opacity = QUIET_OPACITY
    else:
        opacity = BUSY_OPACITY

    return Bar(
        x=index * (width + gap),
        y=height - bar_height,
        width=width,
        height=bar_height,
        opacity=opacity,
    )


def render(series: Sequence[int]) -> SafeString:
    rects = format_html_join(
        "",
        '<rect x="{}" y="{}" width="{}" height="{}" opacity="{}"></rect>',
        ((bar.x, bar.y, bar.width, bar.height, bar.opacity) for bar in bars(series)),
    )
    return format_html(
        '<span class="text-primary-600 dark:text-primary-500" title="{} in 7 days">'
        '<svg width="{}" height="{}" viewBox="0 0 {} {}" fill="currentColor"'
        ' role="img" aria-label="7 day activity">{}</svg></span>',
        sum(series),
        CHART_WIDTH,
        CHART_HEIGHT,
        CHART_WIDTH,
        CHART_HEIGHT,
        rects,
    )
