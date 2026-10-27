"""Bounded, purpose-specific station windows; no reads across unused gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import HourlyObservation, TimeWindow, TracingRequest


@dataclass(frozen=True)
class StationWindows:
    """Target stays separate so optional history cannot swallow target facts."""

    target: TimeWindow
    baseline: TimeWindow
    context: TimeWindow | None
    auxiliary: tuple[TimeWindow, ...]


def plan_station_windows(
    request: TracingRequest, policy: AnalysisPolicy
) -> StationWindows:
    """Plan baseline and at most 23/7 preceding hours for registered metrics."""
    target = request.time_window
    baseline = request.baseline_window or TimeWindow(
        start=target.start - timedelta(days=policy.baseline.lookback_days),
        end=target.start,
        timezone=target.timezone,
    )
    hours = {"pm2.5": 23, "o3": 7}.get(request.pollutant.lower(), 0)
    context = (
        TimeWindow(
            start=target.start - timedelta(hours=hours),
            end=target.start,
            timezone=target.timezone,
        )
        if hours
        else None
    )
    # Read only the parts not already covered by the mandatory target query.
    intervals: list[TimeWindow] = []
    for window in (baseline, context):
        if window is None:
            continue
        if window.start < target.start:
            intervals.append(
                TimeWindow(
                    start=window.start,
                    end=min(window.end, target.start),
                    timezone=target.timezone,
                )
            )
        if window.end > target.end:
            intervals.append(
                TimeWindow(
                    start=max(window.start, target.end),
                    end=window.end,
                    timezone=target.timezone,
                )
            )
    merged: list[TimeWindow] = []
    for window in sorted(intervals, key=lambda item: item.start):
        if (
            merged
            and window.start <= merged[-1].end
            and max(window.end, merged[-1].end) - merged[-1].start <= timedelta(days=31)
        ):
            previous = merged.pop()
            merged.append(
                TimeWindow(
                    start=previous.start,
                    end=max(previous.end, window.end),
                    timezone=target.timezone,
                )
            )
        else:
            merged.append(window)
    return StationWindows(target, baseline, context, tuple(merged))


def within_window(
    rows: tuple[HourlyObservation, ...], window: TimeWindow | None
) -> tuple[HourlyObservation, ...]:
    """Keep left-closed/right-open samples; None has no context purpose."""
    if window is None:
        return ()
    return tuple(row for row in rows if window.start <= row.timestamp < window.end)
