"""US federal holiday + weekend calendar (computed, no external dependency).

Used by the substantive 'holiday/weekend' check. Whether a given tariff actually
tolls these days is answered by the evidence sidecar (`free_time_tolls_holidays`);
this module only identifies which days are non-working. Date parsing/ranges live
in `util` and are reused here.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from .util import date_range, parse_date  # re-exported for callers' convenience

__all__ = [
    "us_federal_holidays", "is_holiday", "is_weekend", "overlap_days",
    "nonworking_days_in_range", "date_range", "parse_date",
]


def _nth_weekday(year, month, weekday, n):
    """n-th `weekday` (Mon=0..Sun=6) of a month; n=1 -> first occurrence."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d):
    """Federal observance shift: Saturday -> Friday, Sunday -> Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=128)
def us_federal_holidays(year):
    """Set of observed US federal holiday dates for `year`."""
    return {
        _observed(date(year, 1, 1)),       # New Year's Day
        _nth_weekday(year, 1, 0, 3),       # MLK Jr. Day
        _nth_weekday(year, 2, 0, 3),       # Washington's Birthday
        _last_weekday(year, 5, 0),         # Memorial Day
        _observed(date(year, 6, 19)),      # Juneteenth
        _observed(date(year, 7, 4)),       # Independence Day
        _nth_weekday(year, 9, 0, 1),       # Labor Day
        _nth_weekday(year, 10, 0, 2),      # Columbus Day
        _observed(date(year, 11, 11)),     # Veterans Day
        _nth_weekday(year, 11, 3, 4),      # Thanksgiving
        _observed(date(year, 12, 25)),     # Christmas
    }


def is_holiday(d):
    return d in us_federal_holidays(d.year)


def is_weekend(d):
    return d.weekday() >= 5


def overlap_days(a_start, a_end, b_start, b_end):
    """Inclusive count of days where [a_start,a_end] overlaps [b_start,b_end]."""
    lo, hi = max(a_start, b_start), min(a_end, b_end)
    return (hi - lo).days + 1 if hi >= lo else 0


def nonworking_days_in_range(a, b):
    """List of (date, reason) for weekend / federal-holiday days in an inclusive range."""
    out = []
    for d in date_range(a, b):
        if is_weekend(d):
            out.append((d, "weekend"))
        elif is_holiday(d):
            out.append((d, "federal holiday"))
    return out
